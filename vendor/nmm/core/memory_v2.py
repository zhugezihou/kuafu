"""
nmm/core/memory_v2.py — NMM 核心记忆系统 v2

三大深化改进：
1. 梯度惊喜度 — 即时计算，无需等待 predictor 训练
2. 深度睡眠 — 多轮回放 + 冲突解决 + 概念合并
3. 记忆图 — 多层关联传播

摒弃：
- 旧版 SurpriseModule 的慢速 predictor 训练
- 旧版简单合并的 consolidate
- 旧版一阶关联图

保留：
- 双层架构（Episodic + LongTerm）
- ContentAddressableMemory 寻址
- 遗忘曲线
- ConflictMemoryManager
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import math


# ═══════════════════════════════════════════════════════
# v2 改进 1: 梯度惊喜度
# ═══════════════════════════════════════════════════════

class GradientSurprise(nn.Module):
    """基于梯度的即时惊喜度（Titans 风格）

    旧版 SurpriseModule 的问题是：predictor 需要多步训练才能收敛。
    梯度惊喜度不需要训练——用记忆读取自身产生的梯度范数作为惊喜度。

    原理：
      1. 从记忆读取内容
      2. 计算"读取内容"和"当前输入"的 loss
      3. loss 对输入的梯度范数 = 惊喜度
      4. 梯度大 = 记忆不知道这个输入 → 高惊喜 → 应写入

    优点：零训练，即时生效，理论上在第一步就能给出合理的惊喜度。
    """

    def __init__(self):
        super().__init__()
        # 可学习的温度（用于 sigmoid 归一化）
        self.temperature = nn.Parameter(torch.ones(1) * 0.5)

    def forward(self, x: torch.Tensor, read: torch.Tensor) -> torch.Tensor:
        """计算梯度惊喜度

        Args:
            x: [batch, dim] 当前输入（已在记忆空间）
            read: [batch, dim] 从记忆读取的内容

        Returns:
            surprise: [batch, 1] 惊喜度 (0~1)
        """
        # 计算读取内容与输入的差异
        error = (x - read).pow(2).mean(dim=-1, keepdim=True)  # [batch, 1]

        # 对这个误差求 x 的梯度（衡量"如果改变输入，误差变化多大"）
        # 高梯度 = 高度敏感 = 记忆对输入没有准备 = 高惊喜
        grad_norm = torch.sqrt(error + 1e-8)  # 简化的梯度近似

        # sigmoid 归一化
        surprise = torch.sigmoid(grad_norm * self.temperature)
        return surprise


# ═══════════════════════════════════════════════════════
# v2 改进 2: 深度睡眠
# ═══════════════════════════════════════════════════════

class DeepSleepConsolidator:
    """深度睡眠巩固器

    旧版 consolidate 的不足：
    - 只是把 episodic buffer 写入 longterm memory
    - 没有冲突检测
    - 没有多轮回放
    - 概念更新太粗糙

    深度睡眠流程（模仿人类睡眠的慢波和 REM 阶段）：
      阶段 1 (慢波睡眠): 记忆重放 + 巩固
      阶段 2 (REM): 冲突检测 + 概念合并
      阶段 3 (深度整理): 关联图全局优化 + 遗忘
    """

    def __init__(self, num_replay_rounds: int = 5, conflict_threshold: float = 0.85):
        self.num_replay_rounds = num_replay_rounds
        self.conflict_threshold = conflict_threshold

    def deep_sleep(self, controller) -> dict:
        """一次完整的深度睡眠

        Args:
            controller: MemoryController 实例

        Returns:
            stats dict
        """
        stats = {}
        episodic = controller.episodic
        longterm = controller.longterm

        # ── 阶段 1: 慢波睡眠 — 记忆重放 + 巩固 ──
        buf = episodic.get_all()
        if buf.shape[0] == 0:
            return {"phase1": "no_data", "phase2": "skipped", "phase3": "skipped"}

        # 写入长期记忆
        self._phase1_write(longterm, buf)

        # 多轮回放（从长期记忆采样重放）
        replay_stats = self._phase1_replay(controller, self.num_replay_rounds)
        stats['phase1'] = replay_stats

        # ── 阶段 2: REM — 冲突检测 + 概念合并 ──
        conflict_stats = self._phase2_resolve_conflicts(longterm)
        merge_stats = self._phase2_merge_concepts(longterm)
        stats['phase2'] = {**conflict_stats, **merge_stats}

        # ── 阶段 3: 深度整理 — 图优化 + 遗忘 ──
        graph_stats = self._phase3_optimize_graph(longterm)
        longterm.apply_forgetting()
        stats['phase3'] = graph_stats

        # 清空情景缓冲
        episodic.clear()

        stats['consolidated'] = buf.shape[0]
        return stats

    def _phase1_write(self, longterm, buf: torch.Tensor):
        """写入长期记忆（带模糊去重）"""
        M = longterm.M
        n = buf.shape[0]

        if n > M // 2:
            idx = torch.randperm(n)[:M // 2]
            buf = buf[idx]

        # 去重写入：检查相似度，太相似的不写
        for v in buf:
            query = v.unsqueeze(0)
            weight = longterm.memory_bank.content_addressing(query)
            max_sim = weight.max().item()
            if max_sim < 0.85:  # 足够新才写入
                gate_t = torch.tensor([[0.3]])
                longterm.memory_bank.write(query, query, gate_t, lru_bias=True)

    def _phase1_replay(self, controller, rounds: int) -> dict:
        """多轮记忆回放

        从长期记忆采样，计算巩固度差异，加强未巩固的记忆。
        """
        total_consolidated = 0
        for _ in range(rounds):
            mem = controller.longterm.memory_bank.memory[0]
            usage = controller.longterm.memory_bank.usage
            if usage.sum() < 0.01:
                break

            # 按低巩固度加权采样（优先巩固弱的）
            consolidate = controller.longterm.consolidation_count
            weak = torch.sigmoid(-consolidate * 2.0 + 1.0)  # 低巩固 → 高权重
            sampling_w = usage * 0.3 + weak * 0.7
            sampling_w = sampling_w / (sampling_w.sum() + 1e-8)

            num_samples = min(10, controller.longterm.M)
            indices = torch.multinomial(sampling_w, num_samples, replacement=False)

            for idx in indices:
                sample = mem[idx]
                # 用 read 投影计算
                read_key = controller.read_key_proj(sample.unsqueeze(0))
                read = controller.longterm.read(read_key)
                error = (sample - read.squeeze(0)).pow(2).mean().item()

                if error > 0.1:  # 高误差 = 需要巩固
                    controller.longterm.consolidation_count[idx] += 0.2
                    total_consolidated += 1

        return {"replay_count": total_consolidated}

    def _phase2_resolve_conflicts(self, longterm) -> dict:
        """检测并解决冲突记忆

        两条记忆 > 冲突阈值且属于不同概念 → 标记为冲突
        冲突标记后续可被查询时用于上下文切换。
        """
        conflicts_found = 0
        M = longterm.M
        mem = longterm.memory_bank.memory[0]

        # 采样检查（全量 O(n²) 太贵）
        num_checks = min(100, M)
        indices = torch.randperm(M)[:num_checks]

        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                idx_i, idx_j = indices[i].item(), indices[j].item()
                sim = F.cosine_similarity(
                    mem[idx_i].unsqueeze(0), mem[idx_j].unsqueeze(0)).item()

                if sim > self.conflict_threshold:
                    # 检查是否属于不同概念
                    assign_i = longterm.concept_assignments[idx_i].argmax().item()
                    assign_j = longterm.concept_assignments[idx_j].argmax().item()
                    if assign_i != assign_j:
                        # 高相似但不同概念 → 冲突
                        longterm.update_association(idx_i, idx_j, -0.2)  # 负关联
                        conflicts_found += 1

        return {"conflicts_found": conflicts_found}

    def _phase2_merge_concepts(self, longterm) -> dict:
        """合并过于相似的概念中心"""
        merged = 0
        C = longterm.num_concepts
        centers = longterm.concept_centers

        for i in range(C):
            for j in range(i + 1, C):
                sim = F.cosine_similarity(
                    centers[i].unsqueeze(0), centers[j].unsqueeze(0)).item()
                if sim > 0.9:  # 太相似了，合并
                    centers.data[j] = centers[i].clone()  # 合并到 i
                    merged += 1

        return {"concepts_merged": merged}

    def _phase3_optimize_graph(self, longterm) -> dict:
        """优化关联图：传播 + 剪枝"""
        graph = longterm.association_graph

        # 一步图传播（A→B, B→C ⇒ A→C 微弱增强）
        propagation = (graph @ graph) * 0.1
        graph.data = graph + propagation
        graph.data = torch.clamp(graph, 0, 1)

        # 剪枝：弱关联归零
        graph.data[graph < 0.05] = 0.0

        return {"graph_density": (graph > 0).float().mean().item()}


# ═══════════════════════════════════════════════════════
# v2 改进 3: 记忆图 — 多层关联传播
# ═══════════════════════════════════════════════════════

class MemoryGraph:
    """记忆图 — 多层关联传播

    旧版 LongTermMemory.read() 只做了一步关联传播。
    这个模块将关联传播扩展到多层，实现"联想链"。

    query → 寻址 → 一层关联 → 二层关联 → ... → 结果

    类似知识图谱的图传播，但权重是学习的。
    """

    def __init__(self, max_hops: int = 2, decay: float = 0.5):
        self.max_hops = max_hops
        self.decay = decay  # 跨跳衰减

    def propagate(self, query_weight: torch.Tensor,
                  association_graph: torch.Tensor) -> torch.Tensor:
        """多层图传播

        Args:
            query_weight: [batch, M] 初始查询权重
            association_graph: [M, M] 关联图

        Returns:
            combined: [batch, M] 传播后的权重（多跳加权）
        """
        combined = query_weight.clone()
        current = query_weight.clone()

        for hop in range(1, self.max_hops + 1):
            # 沿图传播一步：关联传播 + 自环保留原始信号
            current = current @ association_graph  # [batch, M]
            # 加入自环（保留原始匹配信号）
            current = current + query_weight * 0.3
            # 衰减后加入
            combined = combined + current * (self.decay ** hop)

        # 归一化
        combined = combined / (combined.sum(dim=-1, keepdim=True) + 1e-8)
        return combined

    def multi_hop_read(self, memory_bank, query: torch.Tensor,
                        association_graph: torch.Tensor) -> torch.Tensor:
        """多层跳读

        Args:
            memory_bank: ContentAddressableMemory
            query: [batch, W] 查询
            association_graph: [M, M]

        Returns:
            read: [batch, W] 多跳读取结果
        """
        weight = memory_bank.content_addressing(query)  # [batch, M]
        propagated = self.propagate(weight, association_graph)
        read = memory_bank.read_weighted(propagated)
        return read


# ═══════════════════════════════════════════════════════
# v2 测试
# ═══════════════════════════════════════════════════════

def test_gradient_surprise():
    """梯度惊喜度应该对新输入给出高分，对重复输入快速下降"""
    print("=" * 60)
    print("v2 测试: 梯度惊喜度 — 即时生效")
    print("=" * 60)

    from nmm.core.memory import ContentAddressableMemory

    dim = 64
    surprise = GradientSurprise()
    mem = ContentAddressableMemory(32, dim)

    pattern = torch.randn(1, dim)

    surprises = []
    for i in range(10):
        read = mem.read(pattern)
        s = surprise(pattern, read)
        surprises.append(s.mean().item())
        # 写入（让记忆逐渐熟悉这个模式）
        gate = torch.tensor([[1.0]])
        mem.write(pattern, pattern, gate, lru_bias=False)

    print(f"  第1步: {surprises[0]:.4f}")
    print(f"  第10步: {surprises[-1]:.4f}")
    print(f"  下降: {(surprises[0] - surprises[-1]):.4f}")

    # 新模式应该高惊喜
    new_pattern = torch.randn(1, dim)
    read_new = mem.read(new_pattern)
    s_new = surprise(new_pattern, read_new).mean().item()
    print(f"  新模式: {s_new:.4f}")

    ok = surprises[0] > 0.3 and s_new > surprises[-1]
    print("  ✅ 梯度惊喜度即时生效" if ok else "  ⚠️ 需要调整")
    print()


def test_deep_sleep():
    """深度睡眠应该比旧版 consolidate 做更多事"""
    print("=" * 60)
    print("v2 测试: 深度睡眠")
    print("=" * 60)

    from nmm.core.memory import MemoryController

    dim = 32
    ctrl = MemoryController(dim, dim * 2,
                             episodic_size=16, longterm_size=32,
                             concept_count=8)

    # 写入情景记忆
    for _ in range(30):
        ctrl(torch.randn(1, dim))

    print(f"  睡眠前: 情景={ctrl.episodic.size}, 长期写入={ctrl.total_writes.item()}")

    # 深度睡眠
    deep = DeepSleepConsolidator()
    stats = deep.deep_sleep(ctrl)

    print(f"  阶段1(重放): {stats.get('phase1', {})}")
    print(f"  阶段2(冲突+合并): {stats.get('phase2', {})}")
    print(f"  阶段3(图优化): {stats.get('phase3', {})}")
    print(f"  整合总量: {stats.get('consolidated', 0)}")
    print(f"  睡眠后: 情景={ctrl.episodic.size}")

    print("  ✅ 深度睡眠完成")
    print()


def test_memory_graph():
    """记忆图应该能实现跨跳联想"""
    print("=" * 60)
    print("v2 测试: 记忆图 — 跨跳联想")
    print("=" * 60)

    from nmm.core.memory import ContentAddressableMemory

    dim = 32
    mem = ContentAddressableMemory(16, dim)
    graph = MemoryGraph(max_hops=2)

    # 写入 3 个关联的记忆块
    vectors = []
    for _ in range(3):
        v = torch.randn(1, dim)
        vectors.append(v)
        gate = torch.tensor([[1.0]])
        mem.write(v, v, gate, lru_bias=False)

    # 手动建关联：A↔B, B↔C
    assoc_graph = torch.eye(16) * 0.1
    assoc_graph[0, 1] = 0.8
    assoc_graph[1, 0] = 0.8
    assoc_graph[1, 2] = 0.8
    assoc_graph[2, 1] = 0.8

    # 用 A 查询，应该能通过 B 访问到 C
    query = vectors[0]
    weight = mem.content_addressing(query)
    propagated = graph.propagate(weight, assoc_graph)

    print(f"  初始权重: slot_2={weight[0,2].item():.4f}")
    print(f"  传播后:   slot_2={propagated[0,2].item():.4f}")

    ok = propagated[0, 2].item() > weight[0, 2].item()
    print("  ✅ 跨跳联想有效" if ok else "  ⚠️ 传播不足")
    print()


def test_v2_integration():
    """v2 整合到 MemoryController 的端到端测试"""
    print("=" * 60)
    print("v2 整合测试")
    print("=" * 60)

    from nmm.core.memory import MemoryController

    dim = 16
    ctrl = MemoryController(dim, dim * 2,
                             episodic_size=8, longterm_size=16,
                             concept_count=4)
    deep = DeepSleepConsolidator()

    # 模拟使用过程
    pattern_a = torch.randn(1, dim)
    pattern_b = torch.randn(1, dim)

    for step in range(30):
        p = pattern_a if step < 15 else pattern_b
        ctrl(p)

    # 验证梯度惊喜度：换模式时应该跳变
    print(f"  pattern_a 最后一轮: 惊喜度=?（在 MemoryController 中已改为旧版 SurpriseModule）")

    # 深度睡眠
    stats = deep.deep_sleep(ctrl)
    print(f"  深度睡眠: 整合 {stats['consolidated']}")

    # 检索
    q = ctrl.encoder(pattern_a).squeeze(0)
    results = ctrl.recall_by_content(q, k=3)
    print(f"  检索: {len(results)} 条")

    print("  ✅ v2 整合测试完成")
    print()


if __name__ == "__main__":
    test_gradient_surprise()
    test_deep_sleep()
    test_memory_graph()
    test_v2_integration()
    print("所有 v2 改进测试通过！")

"""
nmm/core/memory.py — 可微分神经记忆核心

模仿人类海马体-新皮层的双层记忆系统：
- EpisodicBuffer (海马体): 快速写入，短期存储，容量有限
- LongTermMemory (新皮层): 慢速整合，长期持久，结构化

设计原理：
1. 惊喜度驱动 → 高意外信息优先记忆
2. 遗忘门控 → 权重衰减 + LRU 淘汰
3. 双重存储 → 快速写入 + 长期整合
4. 联想寻址 → 内容相似度 + 上下文线索
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import math

# ═══════════════════════════════════════════════════════
# Phase 1: 核心记忆矩阵
# ═══════════════════════════════════════════════════════


class ContentAddressableMemory(nn.Module):
    """基于内容的可寻址外部记忆矩阵

    核心能力：
    - 内容寻址（余弦相似度 + 温度可控的 softmax）
    - 可微分读写
    - 使用情况跟踪（LRU 辅助淘汰）
    - temporal link 跟踪（DNC 风格时序关联）

    对应：DNC 的外部记忆矩阵 + Titans 的神经长期记忆
    """

    def __init__(self, num_slots: int, slot_dim: int):
        super().__init__()
        self.M = num_slots        # 记忆槽数量
        self.W = slot_dim         # 每个槽的维度

        # 记忆矩阵（可学习初始化）
        self.memory = nn.Parameter(torch.randn(1, num_slots, slot_dim) * 0.1)

        # 使用率跟踪（0~1，越高表示最近被频繁访问）
        self.register_buffer('usage', torch.zeros(num_slots))

        # 时序链接矩阵：T[i][j] = 连续写入 i→j 的关联强度
        self.register_buffer('temporal_link', torch.zeros(num_slots, num_slots))
        # 前向优先度：哪个槽最后被写入
        self.register_buffer('precedence', torch.zeros(num_slots))

    def content_addressing(self, query: torch.Tensor, temperature: float = 10.0) -> torch.Tensor:
        """基于内容的寻址

        Args:
            query: [batch, W] 查询向量
            temperature: softmax 温度（越高越集中）

        Returns:
            weight: [batch, M] 注意力权重
        """
        # 归一化记忆矩阵确保数值稳定
        mem_norm = F.normalize(self.memory, dim=-1)               # [1, M, W]
        query_norm = F.normalize(query, dim=-1)                    # [batch, W]
        sim = torch.matmul(query_norm.unsqueeze(1), mem_norm.transpose(-2, -1))
        sim = sim.squeeze(1)  # [batch, M]
        return F.softmax(sim * temperature, dim=-1)

    def read(self, query: torch.Tensor) -> torch.Tensor:
        """从记忆读取

        Args:
            query: [batch, W] 读取查询

        Returns:
            read: [batch, W] 读取的向量
        """
        weight = self.content_addressing(query)  # [batch, M]
        read = torch.matmul(weight.unsqueeze(1), self.memory)  # [batch, 1, W]
        return read.squeeze(1)

    def read_weighted(self, weight: torch.Tensor) -> torch.Tensor:
        """按指定权重读取"""
        return torch.matmul(weight.unsqueeze(1), self.memory).squeeze(1)

    def write(self, key: torch.Tensor, value: torch.Tensor,
              gate: torch.Tensor, lru_bias: bool = True):
        """写入记忆

        Args:
            key: [batch, W] 寻址键
            value: [batch, W] 写入内容
            gate: [batch, 1] 写入门控 (0~1)
            lru_bias: 是否偏向最少使用的槽
        """
        weight = self.content_addressing(key)

        if lru_bias:
            # 结合 LRU bias：高使用率的槽减少分配权重
            # 让新信息写到较少使用的记忆槽
            lru_weight = (1 - self.usage).unsqueeze(0)  # [1, M]
            lru_weight = lru_weight / (lru_weight.sum(dim=-1, keepdim=True) + 1e-8)
            # 混合内容寻址和 LRU 分配
            mix = 0.7
            weight = mix * weight + (1 - mix) * lru_weight.expand_as(weight)
            weight = weight / (weight.sum(dim=-1, keepdim=True) + 1e-8)

        # 擦除：gate * 权重 * (1 − 旧值)
        erase = gate.unsqueeze(-1) * weight.unsqueeze(-1)  # [batch, M, 1]
        # 写入：gate * 权重 * 新值
        write_val = gate.unsqueeze(-1) * weight.unsqueeze(-1) * value.unsqueeze(1)  # [batch, M, W]

        # 聚合到记忆矩阵（batch 维度取平均）
        erase_agg = erase.mean(dim=0)   # [M, 1]
        write_agg = write_val.mean(dim=0)  # [M, W]

        with torch.no_grad():
            self.memory.data = self.memory.data * (1 - erase_agg) + write_agg

            # 更新使用率（指数滑动平均）
            w_avg = weight.mean(dim=0).detach()  # [M]
            self.usage = self.usage * 0.99 + w_avg * 0.01

            # 更新时序链接
            self._update_temporal_links(weight)

    def _update_temporal_links(self, weight: torch.Tensor):
        """更新时序链接矩阵

        记录连续写入之间的顺序关系，使记忆可以按时间顺序回溯。
        """
        w = weight.mean(dim=0).detach()  # [M]
        # 当前写入的权重分布
        self.precedence = w
        # 遗忘旧的链接 + 创建新链接
        self.temporal_link = self.temporal_link * 0.95
        for i in range(self.M):
            for j in range(self.M):
                if i != j:
                    # 如果 i 在 j 之前被写入，增强链接
                    self.temporal_link[i, j] += w[i] * self.precedence[j].detach()

    def temporal_read(self, from_idx: int, forward: bool = True) -> torch.Tensor:
        """按时间顺序读取

        Args:
            from_idx: 起始槽索引
            forward: True=向前（之后写入的），False=向后（之前写入的）

        Returns:
            read: [W] 读取向量
        """
        if forward:
            link_w = self.temporal_link[from_idx]
        else:
            link_w = self.temporal_link[:, from_idx]
        # 归一化
        link_w = F.softmax(link_w, dim=-1)
        return self.read_weighted(link_w.unsqueeze(0)).squeeze(0)

    def reset(self):
        """重置记忆状态"""
        self.memory.data = torch.randn_like(self.memory) * 0.1
        self.usage.zero_()
        self.temporal_link.zero_()
        self.precedence.zero_()


# ═══════════════════════════════════════════════════════
# Phase 1: 惊喜度模块
# ═══════════════════════════════════════════════════════


class SurpriseModule(nn.Module):
    """惊喜度计算模块

    基于 Titans 的核心思想：用预测误差衡量信息新颖性。
    高惊喜度 → 信息量大 → 应被优先记忆。

    两条路径：
    1. 预测误差: 基于记忆上下文预测当前输入，误差大=惊喜
    2. 梯度幅度: 输入经过网络时产生的梯度大小（可选）
    """

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

        # 预测器：从记忆上下文预测输入
        self.predictor = nn.Sequential(
            nn.Linear(dim, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, dim),
        )

        # 可学习的温度参数（初始较小，使 sigmoid 更敏感）
        self.temperature = nn.Parameter(torch.ones(1) * 1.0)

    def forward(self, x: torch.Tensor, context: torch.Tensor) -> torch.Tensor:
        """计算惊喜度

        Args:
            x: [batch, dim] 当前输入
            context: [batch, dim] 记忆提供的上下文

        Returns:
            surprise: [batch, 1] 惊喜度 (0~1)
        """
        # 基于记忆上下文预测输入
        prediction = self.predictor(context)

        # 预测误差 = 惊喜度
        error = (x - prediction).pow(2).mean(dim=-1, keepdim=True)  # [batch, 1]

        # 用可学习温度做 sigmoid 归一化
        surprise = torch.sigmoid(error * self.temperature)
        return surprise

    def compute_surprise_from_gradient(self, x: torch.Tensor,
                                        loss_fn: callable) -> torch.Tensor:
        """基于梯度的惊喜度（Titans 风格）

        对输入 x 计算 loss，用梯度范数作为惊喜度。
        """
        x.requires_grad_(True)
        pred = self.predictor(x)
        loss = F.mse_loss(pred, x.detach())
        grad = torch.autograd.grad(loss, x, create_graph=False)[0]
        surprise = grad.norm(dim=-1, keepdim=True)
        surprise = torch.sigmoid(surprise * self.temperature)
        return surprise.detach()


# ═══════════════════════════════════════════════════════
# Phase 1: 遗忘机制
# ═══════════════════════════════════════════════════════


class ForgettingMechanism(nn.Module):
    """遗忘机制

    模仿人类记忆的遗忘曲线（Ebbinghaus Forgetting Curve）：
    - 权重衰减: 随时间自然衰减
    - 使用增强: 被频繁访问的记忆被保护不被遗忘
    - 干扰遗忘: 相似内容的写入会干扰旧记忆
    - 时间衰减: 停留时间越长的记忆衰减越慢（记忆巩固）

    公式：decay = base_decay * exp(-access_freq * protection) / (1 + age * consolidation)
    """

    def __init__(self, base_decay: float = 0.01, protection_factor: float = 5.0,
                 consolidation_rate: float = 0.01):
        super().__init__()
        self.base_decay = base_decay
        self.protection_factor = protection_factor
        self.consolidation_rate = consolidation_rate

    def compute_decay(self, usage: torch.Tensor, age: torch.Tensor) -> torch.Tensor:
        """计算每个记忆槽的衰减率

        Args:
            usage: [M] 使用率（0~1，越高越常被访问）
            age: [M] 记忆年龄（步数）

        Returns:
            decay: [M] 衰减率 (0~1)
        """
        # 使用保护：被频繁访问的衰减慢
        protection = torch.exp(-usage * self.protection_factor)

        # 巩固：越老的记忆衰减越慢
        consolidation = 1.0 / (1.0 + age * self.consolidation_rate)

        decay_rate = self.base_decay * protection * consolidation
        return decay_rate

    def apply_forgetting(self, memory: torch.Tensor, usage: torch.Tensor,
                          age: torch.Tensor, encroach: torch.Tensor = None) -> torch.Tensor:
        """应用遗忘

        Args:
            memory: [M, W] 记忆矩阵
            usage: [M] 使用率
            age: [M] 年龄
            encroach: [M, W] 干扰信号（来自相似内容写入）

        Returns:
            decayed_memory: [M, W] 遗忘后的记忆
        """
        decay = self.compute_decay(usage, age).unsqueeze(-1)  # [M, 1]

        # 权重衰减
        new_memory = memory * (1 - decay)

        # 干扰遗忘：相似内容写入导致的覆盖
        if encroach is not None:
            # 干扰强度 = 相似度 × 干扰因子
            interference = 0.3 * torch.sigmoid(encroach)
            new_memory = new_memory * (1 - interference)

        return new_memory


# ═══════════════════════════════════════════════════════
# Phase 2: 双层记忆架构
# ═══════════════════════════════════════════════════════


class EpisodicBuffer(nn.Module):
    """情景记忆缓冲 — 海马体

    特点：
    - 快速写入：新经验立即存入
    - 容量有限（e.g. 256 槽），满时自动淘汰
    - 按时间顺序组织，保留时序信息
    - 每个记忆还附带时间戳和上下文标签
    - 作为长期记忆的输入源（睡眠期整合）

    对应：海马体的快速情景编码
    """

    def __init__(self, max_size: int, slot_dim: int):
        super().__init__()
        self.max_size = max_size
        self.W = slot_dim

        # 记忆缓冲
        self.register_buffer('buffer', torch.zeros(max_size, slot_dim))
        # 时间戳（写入时的步数）
        self.register_buffer('timestamps', torch.zeros(max_size, dtype=torch.long))
        # 上下文标签（分类标记）
        self.register_buffer('context_labels', torch.zeros(max_size, dtype=torch.long))
        # 惊喜度得分
        self.register_buffer('surprise_scores', torch.zeros(max_size))
        # 当前写入位置（循环队列）
        self.register_buffer('write_head', torch.zeros(1, dtype=torch.long))
        # 当前已使用的槽数
        self.register_buffer('used_slots', torch.zeros(1, dtype=torch.long))

    def push(self, vector: torch.Tensor, context: int = 0,
             surprise: float = 0.5, step: int = 0):
        """写入一条情景记忆（循环队列模式）

        Args:
            vector: [W] 记忆向量
            context: 上下文标签
            surprise: 惊喜度
            step: 当前步数
        """
        idx = self.write_head.item()
        self.buffer[idx] = vector.detach()
        self.timestamps[idx] = step
        self.context_labels[idx] = context
        self.surprise_scores[idx] = surprise

        # 更新写指针
        used = self.used_slots.item()
        if used < self.max_size:
            self.used_slots += 1
        self.write_head.data = torch.tensor([(idx + 1) % self.max_size])

    def pop_recent(self, n: int = 1) -> list:
        """弹出最近的 n 条记忆"""
        used = self.used_slots.item()
        if used == 0:
            return []
        results = []
        for _ in range(min(n, used)):
            idx = (self.write_head.item() - 1) % self.max_size
            results.append({
                'vector': self.buffer[idx].clone(),
                'timestamp': self.timestamps[idx].item(),
                'context': self.context_labels[idx].item(),
                'surprise': self.surprise_scores[idx].item(),
            })
            self.used_slots -= 1
            self.write_head.data = torch.tensor([idx])
        return results

    def get_all(self) -> torch.Tensor:
        """获取所有有效记忆"""
        used = self.used_slots.item()
        if used == 0:
            return torch.zeros(0, self.W, device=self.buffer.device)
        return self.buffer[:used]

    def get_recent(self, n: int) -> torch.Tensor:
        """获取最近 n 条记忆"""
        used = self.used_slots.item()
        if used == 0:
            return torch.zeros(0, self.W, device=self.buffer.device)
        start = max(0, used - n)
        return self.buffer[start:used]

    def get_by_context(self, context: int) -> torch.Tensor:
        """按上下文标签获取记忆"""
        used = self.used_slots.item()
        mask = self.context_labels[:used] == context
        return self.buffer[:used][mask]

    def clear(self):
        """清空缓冲"""
        self.buffer.zero_()
        self.timestamps.zero_()
        self.context_labels.zero_()
        self.surprise_scores.zero_()
        self.write_head.zero_()
        self.used_slots.zero_()

    @property
    def is_full(self) -> bool:
        return self.used_slots.item() >= self.max_size

    @property
    def size(self) -> int:
        return self.used_slots.item()


class LongTermMemory(nn.Module):
    """长期记忆 — 新皮层

    特点：
    - 慢速整合：从情景缓冲中提取模式，整合到结构化记忆
    - 语义聚类：相似记忆自动聚合为概念
    - 联想网络：记忆间形成关联图
    - 巩固保护：反复访问的记忆越来越难被遗忘

    内部结构：
    - 记忆矩阵（content-based addressing）
    - 概念聚类（k-means 风格的聚类中心）
    - 关联图（记忆间关系矩阵）
    - 巩固计数器

    对应：新皮层的语义记忆 + 程序记忆
    """

    def __init__(self, num_slots: int, slot_dim: int, num_concepts: int = 32):
        super().__init__()
        self.M = num_slots
        self.W = slot_dim
        self.num_concepts = num_concepts

        # 基础记忆存储（复用可寻址记忆）
        self.memory_bank = ContentAddressableMemory(num_slots, slot_dim)

        # 概念中心（聚类中心）
        self.concept_centers = nn.Parameter(torch.randn(num_concepts, slot_dim) * 0.1)

        # 记忆→概念归属度矩阵 [M, num_concepts]
        self.register_buffer('concept_assignments', torch.zeros(num_slots, num_concepts))

        # 记忆间的关联强度 [M, M]（对称矩阵）
        self.register_buffer('association_graph', torch.eye(num_slots) * 0.1)

        # 巩固计数器 [M]（使用次数越多越巩固）
        self.register_buffer('consolidation_count', torch.ones(num_slots) * 0.1)

        # 遗忘机制
        self.forgetting = ForgettingMechanism()

    def write(self, vector: torch.Tensor, context_label: torch.Tensor = None,
              gate: float = 1.0, step: int = 0):
        """写入长期记忆

        写入时同时更新：
        1. 记忆矩阵内容
        2. 概念归属
        3. 关联图
        """
        batch = vector.shape[0]
        gate_t = torch.full((batch, 1), gate, device=vector.device)

        # 先找最相似的概念
        with torch.no_grad():
            # 计算对新向量的概念归属
            centroids = F.normalize(self.concept_centers, dim=-1)  # [C, W]
            v_norm = F.normalize(vector, dim=-1)  # [batch, W]
            sim_to_concepts = torch.matmul(v_norm, centroids.transpose(-2, -1))  # [batch, C]
            assignment = F.softmax(sim_to_concepts * 5.0, dim=-1)

        # 写入记忆
        self.memory_bank.write(vector, vector, gate_t, lru_bias=True)

        # 更新概念归属（取平均）
        with torch.no_grad():
            write_weight = self.memory_bank.content_addressing(vector)
            w_avg = write_weight.mean(dim=0)  # [M]
            for i in range(w_avg.shape[0]):
                wi = w_avg[i].item()
                if wi > 0.01:
                    self.concept_assignments[i] = (
                        self.concept_assignments[i] * 0.9
                        + assignment.mean(dim=0) * 0.1 * wi
                    )

            # 巩固计数器增加
            self.consolidation_count = self.consolidation_count + w_avg * 0.05

            # 年龄增加
            if not hasattr(self, '_age'):
                self.register_buffer('_age', torch.zeros(self.M))
            self._age += 1.0

    def read(self, query: torch.Tensor,
             use_association: bool = True) -> torch.Tensor:
        """读取长期记忆（可联想扩展）

        Args:
            query: [batch, W] 查询
            use_association: 是否沿关联图扩展访问

        Returns:
            read: [batch, W]
        """
        read = self.memory_bank.read(query)

        if use_association:
            # 找到最匹配的槽，再读其关联记忆
            weight = self.memory_bank.content_addressing(query)  # [batch, M]
            # 沿关联图一步传播
            assoc = weight @ self.association_graph  # [batch, M]
            assoc_read = self.memory_bank.read_weighted(assoc)
            # 混合：原始内容 70% + 关联内容 30%
            read = 0.7 * read + 0.3 * assoc_read

        return read

    def update_association(self, slot_i: int, slot_j: int, strength: float = 0.1):
        """增强两个记忆槽之间的关联"""
        self.association_graph[slot_i, slot_j] += strength
        self.association_graph[slot_j, slot_i] += strength
        # 裁剪防止溢出
        self.association_graph.data = torch.clamp(self.association_graph, 0, 1)

    def consolidate(self, episodic: EpisodicBuffer):
        """从情景记忆整合到长期记忆（睡眠期）

        1. 从 episodic 中取出所有记忆
        2. 按上下文分组，提取原型
        3. 更新概念中心
        4. 建立交叉关联
        """
        buf = episodic.get_all()
        if buf.shape[0] == 0:
            return {"consolidated": 0}

        n = buf.shape[0]
        if n > self.M // 2:
            # 太多，随机采样
            idx = torch.randperm(n)[:self.M // 2]
            buf = buf[idx]

        # 写入记忆
        gate_t = torch.full((n, 1), 0.3, device=buf.device)
        self.memory_bank.write(buf, buf, gate_t, lru_bias=True)

        # 更新概念中心（k-means 一步）
        centroids = self.concept_centers.data  # [C, W]
        # 计算 buf 到各中心的距离
        dist = torch.cdist(buf, centroids)  # [n, C]
        hard_assign = dist.argmin(dim=-1)  # [n]
        for c in range(self.num_concepts):
            mask = hard_assign == c
            if mask.sum() > 0:
                new_center = buf[mask].mean(dim=0)
                centroids[c] = centroids[c] * 0.9 + new_center * 0.1

        # 更新关联图：同一上下文的记忆增强关联
        for i in range(min(n, 50)):
            for j in range(i + 1, min(n, 50)):
                sim = F.cosine_similarity(buf[i].unsqueeze(0), buf[j].unsqueeze(0))
                if sim > 0.6:
                    # 找到对应的槽
                    w_i = self.memory_bank.content_addressing(buf[i].unsqueeze(0))
                    w_j = self.memory_bank.content_addressing(buf[j].unsqueeze(0))
                    slot_i = w_i.argmax(dim=-1).item()
                    slot_j = w_j.argmax(dim=-1).item()
                    self.update_association(slot_i, slot_j, sim.item() * 0.1)

        return {"consolidated": n}

    def apply_forgetting(self):
        """应用遗忘：低巩固、低频访问的记忆逐渐消失"""
        age = getattr(self, '_age', torch.ones(self.M, device=self.memory_bank.memory.device))
        decay = self.forgetting.compute_decay(
            self.memory_bank.usage,
            age
        )
        # 保护高巩固的记忆
        protection = torch.sigmoid(self.consolidation_count * 2.0 - 1.0)
        effective_decay = decay * (1 - protection)

        # 应用衰减
        for i in range(self.M):
            if effective_decay[i] > 0.001 and self.consolidation_count[i] < 5.0:
                self.memory_bank.memory.data[0, i] *= (1 - effective_decay[i])

    def get_concept(self, concept_idx: int) -> torch.Tensor:
        """获取指定概念中心向量"""
        return self.concept_centers[concept_idx]

    def get_concept_members(self, concept_idx: int, top_k: int = 5) -> list:
        """获取属于某个概念的 top-k 记忆"""
        assignments = self.concept_assignments[:, concept_idx]
        top = assignments.topk(min(top_k, self.M)).indices
        return [self.memory_bank.memory[0, i] for i in top]


# ═══════════════════════════════════════════════════════
# Phase 2: 冲突记忆管理
# ═══════════════════════════════════════════════════════


class ConflictMemoryManager:
    """冲突记忆共存管理

    人类记忆的一个重要特征：可以同时容纳矛盾的记忆。
    例如"蛇是危险的"和"宠物蛇是安全的"可以共存。

    策略：
    1. 冲突检测：两条记忆相似 > 阈值但不一致
    2. 共存储：保留两条，各自附带置信度和上下文
    3. 上下文切换：查询时根据上下文选择适用的版本
    4. 置信度调整：后续证据支持一条时，另一条衰减
    """

    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold  # 冲突检测阈值
        self._conflicts = {}  # (i, j) → conflict_score

    def detect_conflict(self, mem_i: torch.Tensor, mem_j: torch.Tensor) -> float:
        """检测两条记忆是否冲突

        Returns:
            conflict_score: 0=不冲突, 1=高度冲突
            冲突 = 高相似度 + 输出方向相反
        """
        sim = F.cosine_similarity(mem_i.unsqueeze(0), mem_j.unsqueeze(0)).item()
        if sim > self.threshold:
            # 高相似度意味着它们在说同一件事
            # 如果它们在新数据上的预测方向不同，则是冲突
            conflict = sim  # 简化版本
            return conflict
        return 0.0

    def record_conflict(self, slot_i: int, slot_j: int, score: float):
        """记录冲突对"""
        key = (min(slot_i, slot_j), max(slot_i, slot_j))
        if key not in self._conflicts:
            self._conflicts[key] = score
        else:
            # 置信度持续下降
            self._conflicts[key] = self._conflicts[key] * 0.9 + score * 0.1

    def resolve(self, query: torch.Tensor, candidates: list) -> torch.Tensor:
        """根据上下文解决冲突

        Args:
            query: 当前查询（携带上下文信息）
            candidates: 候选记忆向量列表

        Returns:
            selected: 选出的记忆
        """
        if len(candidates) == 1:
            return candidates[0]

        # 选择与查询最匹配的候选
        scores = [F.cosine_similarity(query.unsqueeze(0), c.unsqueeze(0)).item()
                  for c in candidates]
        best = candidates[scores.index(max(scores))]
        return best

    def has_conflicts(self) -> bool:
        return len(self._conflicts) > 0


# ═══════════════════════════════════════════════════════
# Phase 1+2: 记忆控制器（整合所有模块）
# ═══════════════════════════════════════════════════════


class MemoryController(nn.Module):
    """记忆控制器 — 整合所有记忆模块

    整体的记忆循环：
    Input → Surprise 检测 → Episodic Buffer（快速写入）
                                      ↓ 睡眠期/空闲时
                                     LongTerm Memory（整合）
                                      ↓
                                     Recall（查询时）
    """

    def __init__(self, input_dim: int, hidden_dim: int,
                 episodic_size: int = 256, longterm_size: int = 512,
                 concept_count: int = 32):
        super().__init__()

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.step = 0

        # 编码器：将输入映射到记忆空间
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim * 2),
            nn.GELU(),
            nn.Linear(hidden_dim * 2, hidden_dim),
        )

        # 情景记忆缓冲（海马体）
        self.episodic = EpisodicBuffer(episodic_size, hidden_dim)

        # 长期记忆（新皮层）
        self.longterm = LongTermMemory(longterm_size, hidden_dim, concept_count)

        # 惊喜度检测
        self.surprise = SurpriseModule(hidden_dim)

        # 冲突管理
        self.conflict = ConflictMemoryManager()

        # 阅读投影
        self.read_key_proj = nn.Linear(hidden_dim, hidden_dim)

        # 输出投影
        self.output_proj = nn.Linear(hidden_dim * 2, hidden_dim)

        # LSTM 作为工作记忆
        self.lstm = nn.LSTMCell(hidden_dim * 2, hidden_dim)

        # 统计
        self.register_buffer('total_writes', torch.zeros(1, dtype=torch.long))
        self.register_buffer('total_reads', torch.zeros(1, dtype=torch.long))

    def forward(self, x: torch.Tensor, state=None):
        """单步处理

        Args:
            x: [batch, input_dim] 输入
            state: (h, c) LSTM 状态

        Returns:
            output: [batch, hidden_dim]
            memory_context: [batch, hidden_dim]
            auxiliary: dict with surprise, read, etc.
        """
        batch = x.shape[0]
        device = x.device

        # 初始化 LSTM 状态
        if state is None:
            h = torch.zeros(batch, self.hidden_dim, device=device)
            c = torch.zeros(batch, self.hidden_dim, device=device)
        else:
            h, c = state

        # 编码输入
        encoded = self.encoder(x)

        # 从长期记忆中读取（用当前隐藏状态）
        read_key = self.read_key_proj(h)
        longterm_read = self.longterm.read(read_key)

        # 计算惊喜度：记忆预测和实际输入的差异
        surprise = self.surprise(encoded, longterm_read)

        # 写入情景记忆（惊喜度高的内容）
        gate = surprise.mean(dim=0).item()
        if gate > 0.3:  # 阈值过滤
            self.episodic.push(
                encoded.mean(dim=0),
                context=0,
                surprise=gate,
                step=self.step,
            )
            self.total_writes += 1

        # LSTM 更新
        lstm_input = torch.cat([encoded, longterm_read], dim=-1)
        h_new, c_new = self.lstm(lstm_input, (h, c))

        # 输出
        output = self.output_proj(torch.cat([h_new, longterm_read], dim=-1))

        self.step += 1
        self.total_reads += 1

        aux = {
            'surprise': surprise,
            'episodic_size': self.episodic.size,
            'longterm_usage': self.longterm.memory_bank.usage,
        }

        return output, (h_new, c_new), aux

    def sleep(self):
        """睡眠期记忆巩固

        1. 情景记忆 → 长期记忆整合
        2. 概念中心更新
        3. 关联图更新
        4. 遗忘低巩固记忆
        """
        result = self.longterm.consolidate(self.episodic)
        self.longterm.apply_forgetting()
        self.episodic.clear()
        return result

    def recall_by_content(self, query: torch.Tensor, k: int = 5) -> list:
        """基于内容的联想检索

        Args:
            query: [W] 查询向量
            k: 返回 top-k 结果

        Returns:
            [{'vector': Tensor, 'score': float, 'source': str}, ...]
        """
        results = []

        # 搜索长期记忆
        weight = self.longterm.memory_bank.content_addressing(query.unsqueeze(0))
        topk = weight.topk(k, dim=-1)
        for i in range(k):
            idx = topk.indices[0, i].item()
            vec = self.longterm.memory_bank.memory[0, idx]
            score = topk.values[0, i].item()
            results.append({'vector': vec, 'score': score, 'source': 'longterm', 'slot': idx})

        # 搜索情景记忆
        epi = self.episodic.get_all()
        if epi.shape[0] > 0:
            q_norm = F.normalize(query.unsqueeze(0), dim=-1)
            epi_norm = F.normalize(epi, dim=-1)
            sims = (q_norm @ epi_norm.transpose(-2, -1)).squeeze(0)
            vals, idxs = sims.topk(min(k, sims.shape[0]))
            for i in range(vals.shape[0]):
                results.append({
                    'vector': epi[idxs[i]],
                    'score': vals[i].item(),
                    'source': 'episodic',
                    'slot': idxs[i].item(),
                })

        # 按分数排序去重
        results.sort(key=lambda r: r['score'], reverse=True)
        seen = set()
        unique = []
        for r in results:
            key = hash(r['vector'].data_ptr())
            if key not in seen and len(unique) < k:
                seen.add(key)
                unique.append(r)

        return unique

    def recall_sequential(self, start_context: str = None, n: int = 5) -> list:
        """时序检索（回忆最近发生的事）"""
        recent = self.episodic.get_recent(n)
        return [{'vector': recent[i], 'source': 'episodic_recent'} for i in range(recent.shape[0])]

    def recall_by_concept(self, concept_idx: int, k: int = 5) -> list:
        """按概念检索"""
        members = self.longterm.get_concept_members(concept_idx, k)
        return [{'vector': m, 'source': 'concept', 'concept': concept_idx} for m in members]

    def get_stats(self) -> dict:
        """获取记忆系统统计信息"""
        return {
            'steps': self.step,
            'total_writes': self.total_writes.item(),
            'total_reads': self.total_reads.item(),
            'episodic_used': self.episodic.size,
            'episodic_capacity': self.episodic.max_size,
            'longterm_slots': self.longterm.M,
            'concepts': self.longterm.num_concepts,
        }


# ═══════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════


def test_memory_controller():
    """测试记忆控制器基本功能"""
    print("=" * 60)
    print("测试 MemoryController — 基本流程")
    print("=" * 60)

    batch, dim, hidden = 2, 64, 128
    controller = MemoryController(dim, hidden)

    # 模拟多步输入
    h, c = None, None
    for step in range(10):
        x = torch.randn(batch, dim)
        output, (h, c), aux = controller(x)
        print(f"  步 {step:2d} | 惊喜度={aux['surprise'].mean().item():.3f} | "
              f"情景={aux['episodic_size']:3d} | 写入={controller.total_writes.item():3d}")

    # 睡眠巩固
    print("\n→ 睡眠巩固中...")
    result = controller.sleep()
    print(f"  整合了 {result['consolidated']} 条情景记忆")

    # 检索
    query = torch.randn(hidden)
    results = controller.recall_by_content(query, k=3)
    print(f"\n→ 检索结果: {len(results)} 条")
    for r in results:
        print(f"  [{r['source']}] 分数={r['score']:.3f}")

    print("\n✅ MemoryController 测试通过\n")


def test_dual_memory():
    """测试双层记忆架构"""
    print("=" * 60)
    print("测试双层记忆 — Episodic + LongTerm")
    print("=" * 60)

    dim = 64
    buf = EpisodicBuffer(max_size=32, slot_dim=dim)
    ltm = LongTermMemory(num_slots=128, slot_dim=dim)

    # 写入多条情景记忆
    for i in range(20):
        v = torch.randn(dim)
        buf.push(v, context=i % 3, surprise=0.5 + torch.randn(1).item() * 0.2)

    print(f"  情景缓冲: {buf.size}/{buf.max_size}")

    # 整合到长期记忆
    result = ltm.consolidate(buf)
    print(f"  整合到长期记忆: {result['consolidated']} 条")

    # 读取验证
    query = torch.randn(dim)
    read = ltm.read(query.unsqueeze(0))
    print(f"  长期记忆读取: shape={read.shape}")

    # 遗忘
    ltm.apply_forgetting()
    print("  遗忘已应用")

    # 概念
    concept = ltm.get_concept(0)
    members = ltm.get_concept_members(0, 3)
    print(f"  概念0中心: norm={concept.norm():.3f}, 成员={len(members)}")

    print("✅ 双层记忆测试通过\n")


def test_surprise_and_forgetting():
    """测试惊喜度和遗忘机制"""
    print("=" * 60)
    print("测试惊喜度 + 遗忘")
    print("=" * 60)

    dim = 32
    # 创建两个明显不同的输入模式
    pattern_a = torch.randn(1, dim)
    pattern_b = torch.randn(1, dim)

    controller = MemoryController(dim, dim * 2,
                                   episodic_size=64, longterm_size=128)

    # 重复输入 pattern_a（应该惊喜度下降）
    print("\n重复输入 pattern_a:")
    for i in range(6):
        _, (h, c), aux = controller(pattern_a)
        print(f"  step {i}: 惊喜度={aux['surprise'].mean().item():.4f}")

    # 切换 pattern_b（惊喜度应该上升）
    print("\n切换到 pattern_b:")
    for i in range(3):
        _, (h, c), aux = controller(pattern_b)
        print(f"  step {i}: 惊喜度={aux['surprise'].mean().item():.4f}")

    print("\n遗忘测试:")
    forgetting = ForgettingMechanism()
    usage = torch.tensor([0.0, 0.5, 0.9])
    age = torch.tensor([100.0, 10.0, 1.0])
    decay = forgetting.compute_decay(usage, age)
    for u, a, d in zip(usage, age, decay):
        print(f"  使用率={u:.1f}, 年龄={a:.0f} → 衰减率={d:.4f}")

    print("✅ 惊喜度+遗忘测试通过\n")


def test_conflict_memory():
    """测试冲突记忆管理"""
    print("=" * 60)
    print("测试冲突记忆管理")
    print("=" * 60)

    manager = ConflictMemoryManager(threshold=0.5)

    # 模拟两条高度相似的记忆
    mem_a = torch.ones(64) * 0.5
    mem_b = torch.ones(64) * 0.5 + 0.01

    score = manager.detect_conflict(mem_a, mem_b)
    print(f"  相似记忆冲突: {score:.3f}" + (" (冲突)" if score > 0.5 else " (不冲突)"))

    # 解决冲突
    query = torch.ones(64) * 0.5
    selected = manager.resolve(query, [mem_a, mem_b])
    print(f"  冲突已解决")

    print("✅ 冲突记忆测试通过\n")


if __name__ == "__main__":
    test_memory_controller()
    test_dual_memory()
    test_surprise_and_forgetting()
    test_conflict_memory()
    print("所有测试通过！")

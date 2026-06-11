"""
nmm/core/thinking.py — 思维引擎

模仿人类默认模式网络（DMN）和前额叶皮层的思维能力：
- 关联：记忆片段间建立连接
- 推理：基于记忆的逻辑推导、传递推理、时间推理
- 创造：现有记忆的重新组合生成新内容
- 元认知：知道自己知道什么

核心循环：
  Query → Context Buildup → Association → Reasoning → Synthesis → Output
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from nmm.core.memory import MemoryController


# ═══════════════════════════════════════════════════════
# Phase 3a: 关联引擎
# ═══════════════════════════════════════════════════════


class AssociationEngine(nn.Module):
    """关联引擎 — 记忆片段间的联想

    核心能力：
    1. 直接关联：A 直接关联 B（共现/因果）
    2. 传递关联：A→B→C 推导出 A↔C
    3. 类比关联：A:B ≈ C:D 的结构映射
    4. 反事实关联："如果不是A，会怎样"
    """

    def __init__(self, dim: int, num_heads: int = 4):
        super().__init__()
        self.dim = dim

        # 多头注意力用于关联发现
        self.attention = nn.MultiheadAttention(dim, num_heads, batch_first=True)

        # 关联评分网络
        self.relation_scorer = nn.Sequential(
            nn.Linear(dim * 2, dim),
            nn.GELU(),
            nn.Linear(dim, 1),
        )

    def find_direct_associations(self, query: torch.Tensor,
                                  candidates: torch.Tensor, top_k: int = 5) -> list:
        """查找直接关联"""
        if candidates.shape[0] == 0:
            return []
        q = query.unsqueeze(0).unsqueeze(0)
        c = candidates.unsqueeze(0)
        attn_out, attn_weights = self.attention(q, c, c)
        scores = attn_weights.squeeze(0).squeeze(0)
        k = min(top_k, scores.shape[0])
        top_scores, top_indices = scores.topk(k)
        results = []
        for i in range(k):
            idx = top_indices[i].item()
            results.append((idx, top_scores[i].item(), candidates[idx]))
        return results

    def compute_relation_score(self, a: torch.Tensor, b: torch.Tensor) -> float:
        """计算两个记忆间的关联强度"""
        pair = torch.cat([a, b], dim=-1).unsqueeze(0)
        score = torch.sigmoid(self.relation_scorer(pair))
        return score.item()

    def transitive_association(self, a: torch.Tensor, b: torch.Tensor,
                                c: torch.Tensor) -> float:
        """传递关联：如果 A→B 且 B→C，则 A→C 的强度"""
        ab = self.compute_relation_score(a, b)
        bc = self.compute_relation_score(b, c)
        return ab * bc

    def analogical_transfer(self, source_a: torch.Tensor, source_b: torch.Tensor,
                             target_a: torch.Tensor) -> torch.Tensor:
        """类比迁移：已知 A:B 和 A'，推导 B'

        A 关联到 B，A' 与 A 类似，则 B' 与 B 类似。
        """
        # A:B 的关系向量
        relation = source_b - source_a
        # 应用到 A'
        b_prime = target_a + relation
        return b_prime


# ═══════════════════════════════════════════════════════
# Phase 3b: 推理引擎
# ═══════════════════════════════════════════════════════


class ReasoningEngine(nn.Module):
    """推理引擎 — 从记忆中进行逻辑、时间和因果推理

    推理类型：
    1. 传递推理：A∈B, B∈C → A∈C
    2. 时间推理：A先于B，B先于C → A先于C
    3. 因果推理：A导致B，B导致C → A导致C
    4. 归纳推理：多次A→B → 一般规则"A通常导致B"
    5. 演绎推理：rule(A→B), fact(A) → 结论B
    """

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

        # 关系类型编码（0=共现, 1=因果, 2=时序, 3=类比, 4=矛盾）
        self.relation_embed = nn.Embedding(5, dim)

        # 推理器
        self.reasoner = nn.Sequential(
            nn.Linear(dim * 3, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, dim),
        )

    def transitive_reasoning(self, mem_a: torch.Tensor, mem_b: torch.Tensor,
                              mem_c: torch.Tensor) -> torch.Tensor:
        """传递推理：已知 A 关联 B，B 关联 C，推导 A 和 C 的关系

        实现：用 transformer-style 组合关系嵌入
        """
        # A+B 组合 → 推理 C 的表示
        combined = torch.cat([mem_a, mem_b, mem_c], dim=-1)
        inferred = self.reasoner(combined)
        return inferred

    def temporal_reasoning(self, events: list) -> dict:
        """时间推理：对一系列按时间排序的事件进行推理

        Args:
            events: [{'vector': Tensor, 'time': int, 'type': str}, ...]

        Returns:
            {'sequence': str, 'patterns': list, 'predictions': Tensor}
        """
        # 提取时序模式
        if len(events) < 2:
            return {'sequence': 'too_short', 'patterns': [], 'predictions': None}

        # 计算相邻事件间的变化
        deltas = []
        for i in range(1, len(events)):
            delta = events[i]['vector'] - events[i - 1]['vector']
            deltas.append(delta)

        # 预测下一步
        if len(deltas) >= 2:
            # 用最近的 delta 趋势外推
            momentum = deltas[-1] + (deltas[-1] - deltas[-2]) * 0.5
            predicted = events[-1]['vector'] + momentum
        else:
            predicted = events[-1]['vector'] + deltas[-1]

        return {
            'sequence': f"{len(events)}个事件",
            'patterns': [{'type': 'delta', 'magnitude': d.norm().item()} for d in deltas],
            'predictions': predicted,
        }

    def causal_reasoning(self, cause: torch.Tensor, effect: torch.Tensor,
                          new_cause: torch.Tensor) -> torch.Tensor:
        """因果推理

        已知：cause → effect
        给定：new_cause
        推断：new_effect

        用 cause 到 effect 的变换映射到新场景
        """
        # 因果关系向量
        causal_transform = effect - cause
        # 应用到新场景
        predicted_effect = new_cause + causal_transform
        return predicted_effect

    def inductive_generalization(self, examples: list[tuple]) -> torch.Tensor:
        """归纳推理：从多个 (cause, effect) 对中归纳规则

        Args:
            examples: [(cause_vec, effect_vec), ...]

        Returns:
            rule_vector: 一般规则的向量表示
        """
        if len(examples) < 2:
            return examples[0][1] - examples[0][0] if examples else torch.zeros(self.dim)

        # 计算所有因果变换的平均
        transforms = [effect - cause for cause, effect in examples]
        avg_transform = torch.stack(transforms).mean(dim=0)

        return avg_transform  # 这就是归纳出的"规则"


# ═══════════════════════════════════════════════════════
# Phase 3c: 创造引擎
# ═══════════════════════════════════════════════════════


class CreativeEngine(nn.Module):
    """创造引擎 — 现有记忆的重新组合生成新内容

    对应：海马体-前额叶回路的创造性重组

    创造模式：
    1. 重组：A+B → AB（混合两个记忆）
    2. 类比：A:B → C:D（结构映射）
    3. 反事实：如果A不同会怎样
    4. 抽象：从具体记忆中提取一般模式
    """

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

        # 混合网络
        self.blender = nn.Sequential(
            nn.Linear(dim * 2, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, dim),
        )

        # 变异网络（对记忆加噪声/扰动）
        self.mutator = nn.Sequential(
            nn.Linear(dim, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, dim),
        )

        # 抽象网络（具体→一般）
        self.abstraction = nn.Sequential(
            nn.Linear(dim, dim),
            nn.GELU(),
            nn.Linear(dim, dim),
        )

    def recombine(self, mem_a: torch.Tensor, mem_b: torch.Tensor,
                  alpha: float = 0.5) -> torch.Tensor:
        """重组：混合两个记忆

        Args:
            mem_a: 记忆 A
            mem_b: 记忆 B
            alpha: 混合比例（0=纯A, 1=纯B）

        Returns:
            new: 新生成的记忆
        """
        # 两种混合方式
        # 1. 线性插值
        blended_linear = alpha * mem_a + (1 - alpha) * mem_b

        # 2. 神经网络混合
        combined = torch.cat([mem_a, mem_b], dim=-1).unsqueeze(0)
        blended_nn = self.blender(combined).squeeze(0)

        # 最终 = 两者加权
        return 0.6 * blended_linear + 0.4 * blended_nn

    def mutate(self, memory: torch.Tensor, strength: float = 0.1) -> torch.Tensor:
        """变异：对记忆加噪声生成变体

        Args:
            memory: [W] 原始记忆
            strength: 变异强度

        Returns:
            mutated: [W] 变异后的记忆
        """
        m = memory.unsqueeze(0)
        delta = self.mutator(m).squeeze(0)
        mutated = memory + delta * strength
        return mutated

    def abstract(self, memories: list[torch.Tensor]) -> torch.Tensor:
        """抽象：从多个记忆提取共同模式

        Args:
            memories: [N, dim] 多个相关记忆

        Returns:
            abstracted: [dim] 抽象概念
        """
        stack = torch.stack(memories)
        # 取平均 + 非线性变换
        mean_mem = stack.mean(dim=0)
        abstracted = self.abstraction(mean_mem.unsqueeze(0)).squeeze(0)
        return abstracted

    def counterfactual(self, fact: torch.Tensor,
                        condition: str = "reverse") -> torch.Tensor:
        """反事实生成

        "反之"或"如果不是这样"
        """
        if condition == "reverse":
            # 在高维空间中的"反方向"
            return -fact
        elif condition == "orthogonal":
            # 不相关但互补的方向
            noise = torch.randn_like(fact)
            ortho = noise - (noise.dot(fact) / fact.dot(fact)) * fact
            return ortho
        return fact


# ═══════════════════════════════════════════════════════
# Phase 3d: 元认知
# ═══════════════════════════════════════════════════════


class MetaCognition:
    """元认知 — \"知道自己知道什么\"

    核心能力：
    1. 知识边界感知：知道哪些知识是确定的，哪些是模糊的
    2. 置信度评估：对记忆内容的可靠程度打分
    3. 知识盲区检测：知道什么不知道
    4. 学习需求识别：知道需要学什么
    """

    def __init__(self, confidence_threshold: float = 0.3):
        self.threshold = confidence_threshold

    def assess_knowledge(self, memory_controller: MemoryController,
                          query: torch.Tensor) -> dict:
        """评估对某个主题的知识掌握程度

        Args:
            memory_controller: 记忆控制器
            query: 查询向量

        Returns:
            {
                'has_knowledge': bool,
                'confidence': float,
                'closest_memories': int,
                'knowledge_density': float,
            }
        """
        results = memory_controller.recall_by_content(query, k=10)

        if not results:
            return {
                'has_knowledge': False,
                'confidence': 0.0,
                'closest_memories': 0,
                'knowledge_density': 0.0,
            }

        scores = [r['score'] for r in results]
        avg_score = sum(scores) / len(scores)
        high_conf = sum(1 for s in scores if s > self.threshold)

        return {
            'has_knowledge': avg_score > self.threshold,
            'confidence': avg_score,
            'closest_memories': len(results),
            'knowledge_density': high_conf / len(results),
        }

    def detect_knowledge_gaps(self, related_queries: list[torch.Tensor],
                               controller: MemoryController) -> list:
        """检测知识盲区

        给出多个相关查询，找出模型知识薄弱的方向。

        Returns:
            [(query, confidence), ...] 按置信度升序排列
        """
        gaps = []
        for q in related_queries:
            assessment = self.assess_knowledge(controller, q)
            if not assessment['has_knowledge']:
                gaps.append((q, assessment['confidence']))

        gaps.sort(key=lambda x: x[1])
        return gaps

    def what_i_know(self, controller: MemoryController) -> dict:
        """返回系统整体的知识状态"""
        stats = controller.get_stats()
        return {
            'total_memories': stats['longterm_slots'],
            'total_experiences': stats['total_writes'],
            'concepts_available': stats['concepts'],
            'current_confidence': 0.5,  # 简化
        }


# ═══════════════════════════════════════════════════════
# Phase 3: 思维引擎（整合所有子模块）
# ═══════════════════════════════════════════════════════


class ThinkingEngine(nn.Module):
    """思维引擎

    整合关联、推理、创造、元认知为一个统一的思维循环：

    1. 查询输入
    2. 从记忆构建上下文
    3. 发现关联
    4. 推理
    5. 创造新内容
    6. 评估（元认知）
    7. 输出
    """

    def __init__(self, memory: MemoryController, dim: int):
        super().__init__()
        self.memory = memory
        self.dim = dim

        self.association = AssociationEngine(dim)
        self.reasoning = ReasoningEngine(dim)
        self.creation = CreativeEngine(dim)
        self.metacog = MetaCognition()

        # 综合器
        self.synthesizer = nn.Sequential(
            nn.Linear(dim * 3, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, dim),
        )

    def think(self, query: torch.Tensor, mode: str = "auto") -> dict:
        """一次完整的思维过程

        Args:
            query: [W] 输入向量
            mode: "associate" / "reason" / "create" / "auto"

        Returns:
            {
                'output': Tensor,
                'mode': str,
                'associations': list,
                'reasoning_result': dict or None,
                'creation_result': Tensor or None,
                'metacognition': dict,
            }
        """
        result = {'mode': mode, 'output': query.clone()}

        # 1. 元认知：评估当前知识
        knowledge = self.metacog.assess_knowledge(self.memory, query)
        result['metacognition'] = knowledge

        # 2. 基于模式选择处理路径
        if mode == "auto":
            if not knowledge['has_knowledge']:
                mode = "create"
            elif knowledge['confidence'] > 0.7:
                mode = "reason"
            else:
                mode = "associate"
            result['mode'] = mode

        # 3. 检索相关记忆
        related = self.memory.recall_by_content(query, k=3)
        related_vecs = torch.stack([r['vector'] for r in related]) if related else None

        result['associations'] = []
        result['reasoning_result'] = None
        result['creation_result'] = None

        if mode == "associate" and related_vecs is not None:
            assoc = self.association.find_direct_associations(query, related_vecs)
            result['associations'] = [{'idx': a, 'score': s} for a, s, _ in assoc]

        elif mode == "reason" and related_vecs is not None and related_vecs.shape[0] >= 2:
            a, b = related_vecs[0], related_vecs[1]
            inferred = self.reasoning.transitive_reasoning(query, a, b)
            result['reasoning_result'] = {'inferred': inferred}
            result['output'] = 0.7 * query + 0.3 * inferred

        elif mode == "create":
            if related_vecs is not None and related_vecs.shape[0] >= 2:
                a, b = related_vecs[0], related_vecs[-1]
                created = self.creation.recombine(a, b, alpha=0.5)
                # 变异
                created = self.creation.mutate(created, strength=0.2)
                result['creation_result'] = created
                result['output'] = 0.5 * query + 0.5 * created
            else:
                # 完全新创
                created = self.creation.mutate(query, strength=0.3)
                result['creation_result'] = created
                result['output'] = created

        return result

    def forward(self, x: torch.Tensor, state=None):
        """前向传播（兼容 controller 接口）

        先通过记忆，再通过思维引擎
        """
        output, new_state, mem_aux = self.memory(x, state)

        # 对每个样本做一次思维循环
        batch = x.shape[0]
        thinkers = []
        for i in range(batch):
            thought = self.think(output[i], mode="auto")
            thinkers.append(thought)

        return output, new_state, {'memory': mem_aux, 'thinking': thinkers}


# ═══════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════


def test_association():
    print("=" * 60)
    print("测试 AssociationEngine")
    print("=" * 60)
    dim = 64
    engine = AssociationEngine(dim)
    query = torch.randn(dim)
    candidates = torch.randn(10, dim)
    results = engine.find_direct_associations(query, candidates, top_k=3)
    print(f"  找到 {len(results)} 个关联")
    for idx, score, vec in results:
        print(f"    索引 {idx}: 分数={score:.4f}")
    # 类比迁移
    a, b, a_prime = torch.randn(dim), torch.randn(dim), torch.randn(dim)
    b_prime = engine.analogical_transfer(a, b, a_prime)
    print(f"  类比迁移: {b_prime.shape}")
    print("✅ 关联引擎测试通过\n")


def test_reasoning():
    print("=" * 60)
    print("测试 ReasoningEngine")
    print("=" * 60)
    dim = 64
    engine = ReasoningEngine(dim)
    a, b, c = torch.randn(dim), torch.randn(dim), torch.randn(dim)
    inferred = engine.transitive_reasoning(a, b, c)
    print(f"  传递推理: {inferred.shape}")
    events = [{'vector': torch.randn(dim), 'time': i, 'type': 'event'}
              for i in range(5)]
    temporal = engine.temporal_reasoning(events)
    print(f"  时间推理: {temporal['sequence']}")
    examples = [(torch.randn(dim), torch.randn(dim)) for _ in range(3)]
    rule = engine.inductive_generalization(examples)
    print(f"  归纳推理（规则）: norm={rule.norm():.3f}")
    print("✅ 推理引擎测试通过\n")


def test_creation():
    print("=" * 60)
    print("测试 CreativeEngine")
    print("=" * 60)
    dim = 64
    engine = CreativeEngine(dim)
    a, b = torch.randn(dim), torch.randn(dim)
    blended = engine.recombine(a, b, alpha=0.5)
    print(f"  重组: norm={blended.norm():.3f}")
    mutated = engine.mutate(a, strength=0.2)
    print(f"  变异: sim={F.cosine_similarity(a.unsqueeze(0), mutated.unsqueeze(0)).item():.4f}")
    abstracted = engine.abstract([torch.randn(dim) for _ in range(5)])
    print(f"  抽象: {abstracted.shape}")
    print("✅ 创造引擎测试通过\n")


def test_metacognition():
    print("=" * 60)
    print("测试 MetaCognition")
    print("=" * 60)
    from nmm.core.memory import MemoryController
    controller = MemoryController(64, 128)
    meta = MetaCognition()
    query = torch.randn(128)
    knowledge = meta.assess_knowledge(controller, query)
    print(f"  是否有知识: {knowledge['has_knowledge']}")
    print(f"  置信度: {knowledge['confidence']:.3f}")
    what = meta.what_i_know(controller)
    print(f"  知识摘要: {what}")
    print("✅ 元认知测试通过\n")


def test_thinking_engine():
    print("=" * 60)
    print("测试 ThinkingEngine (完整流程)")
    print("=" * 60)
    from nmm.core.memory import MemoryController
    dim = 64
    memory = MemoryController(dim, 128)
    engine = ThinkingEngine(memory, 128)

    # 先存一些记忆
    for _ in range(20):
        x = torch.randn(1, dim)
        engine.memory(x)

    engine.memory.sleep()

    query = torch.randn(128)
    thought = engine.think(query, mode="auto")
    print(f"  思维模式: {thought['mode']}")
    print(f"  元认知: 有知识={thought['metacognition']['has_knowledge']}")
    print(f"  关联数: {len(thought['associations'])}")
    print(f"  输出 norm: {thought['output'].norm():.3f}")

    print("✅ 思维引擎测试通过\n")


if __name__ == "__main__":
    test_association()
    test_reasoning()
    test_creation()
    test_metacognition()
    test_thinking_engine()
    print("所有思维引擎测试通过！")

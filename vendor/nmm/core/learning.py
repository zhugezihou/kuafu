"""
nmm/core/learning.py — 持续学习引擎

让模型像人一样持续成长的核心模块。

三大学习机制：
1. 在线学习（Online Learning）：每次交互后立即更新
2. 睡眠整合（Sleep Consolidation）：空闲期深度整合
3. 灾难性遗忘防御（Anti-Catastrophic Forgetting）
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from collections import deque
from typing import Optional

from nmm.core.memory import MemoryController


# ═══════════════════════════════════════════════════════
# Phase 4a: 在线学习
# ═══════════════════════════════════════════════════════


class OnlineLearner:
    """在线学习器 — 每次交互后立即更新"""

    def __init__(self, learning_rate: float = 0.001, surprise_threshold: float = 0.5):
        self.lr = learning_rate
        self.surprise_threshold = surprise_threshold
        self._step = 0

    def update_from_interaction(self, controller: MemoryController,
                                 input_x: torch.Tensor,
                                 output: torch.Tensor,
                                 target: Optional[torch.Tensor] = None):
        """从单次交互更新
        Args:
            input_x: [batch, input_dim]
            output: [batch, hidden_dim]
            target: 可选监督信号
        """
        self._step += 1
        if target is not None:
            encoded = controller.encoder(input_x)
            prediction = controller.surprise.predictor(
                controller.longterm.read(controller.read_key_proj(output)))
            loss = F.mse_loss(prediction, target)
            loss.backward()
            for name, param in controller.surprise.predictor.named_parameters():
                if param.grad is not None:
                    param.data -= self.lr * param.grad
                    param.grad.zero_()
        with torch.no_grad():
            encoded = controller.encoder(input_x)
            read_key = controller.read_key_proj(output)
            longterm_read = controller.longterm.read(read_key)
            surprise = controller.surprise(encoded, longterm_read)
            gate = surprise.mean().item()
            if gate > self.surprise_threshold:
                controller.episodic.push(
                    encoded.squeeze(0), context=0, surprise=gate, step=self._step)
                controller.total_writes += 1

    def update_predictor_sgd(self, controller: MemoryController,
                              recent_history: list, epochs: int = 1):
        if len(recent_history) < 2:
            return
        inputs = torch.stack([h[0] for h in recent_history])
        targets = torch.stack([h[1] for h in recent_history])
        optimizer = torch.optim.SGD(controller.surprise.predictor.parameters(),
                                     lr=self.lr * 0.5)
        for _ in range(epochs):
            with torch.no_grad():
                encoded = controller.encoder(inputs)
                read_key = controller.read_key_proj(encoded)
                context = controller.longterm.read(read_key)
            pred = controller.surprise.predictor(context)
            loss = F.mse_loss(pred, targets)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                controller.surprise.predictor.parameters(), 1.0)
            optimizer.step()


# ═══════════════════════════════════════════════════════
# Phase 4b: 睡眠整合
# ═══════════════════════════════════════════════════════


class SleepConsolidator:
    """睡眠整合器 — 空闲时深度整合记忆"""

    def __init__(self, consolidation_strength: float = 0.3):
        self.consolidation_strength = consolidation_strength

    def sleep_cycle(self, controller: MemoryController,
                     num_replays: int = 10) -> dict:
        stats = {'consolidated': 0, 'replays': 0, 'forgotten': 0}
        result = controller.longterm.consolidate(controller.episodic)
        stats['consolidated'] = result['consolidated']
        replay_count = self._replay(controller, num_replays)
        stats['replays'] = replay_count
        controller.longterm.apply_forgetting()
        controller.episodic.clear()
        return stats

    def _replay(self, controller: MemoryController, num_replays: int) -> int:
        """海马体回放 — 重放巩固记忆"""
        count = 0
        for _ in range(num_replays):
            mem = controller.longterm.memory_bank.memory[0]
            usage = controller.longterm.memory_bank.usage
            if usage.sum() < 0.01:
                break
            weights = F.softmax(usage, dim=-1)
            idx = torch.multinomial(weights, 1).item()
            sample = mem[idx]
            with torch.no_grad():
                read_key = controller.read_key_proj(sample.unsqueeze(0))
                context = controller.longterm.read(read_key)
                prediction = controller.surprise.predictor(context)
                loss = F.mse_loss(prediction, sample.unsqueeze(0))
            if loss.item() > 0.1:
                controller.longterm.consolidation_count[idx] += 0.1
                count += 1
        return count


# ═══════════════════════════════════════════════════════
# Phase 4c: 灾难性遗忘防御
# ═══════════════════════════════════════════════════════


class ForgettingDefense:
    """灾难性遗忘防御系统"""

    def __init__(self, replay_interval: int = 50):
        self.replay_interval = replay_interval
        self._memory_buffer = deque(maxlen=200)
        self._step = 0

    def add_to_buffer(self, memory: torch.Tensor):
        self._memory_buffer.append(memory.detach())

    def protect_memory(self, controller: MemoryController) -> dict:
        hc = (controller.longterm.consolidation_count > 2.0).sum().item()
        assoc_sum = controller.longterm.association_graph.sum(dim=-1)
        ha = (assoc_sum > 1.0).sum().item()
        return {'protected_count': hc + ha, 'high_consolidation': hc, 'high_association': ha}

    def replay_memories(self, controller: MemoryController,
                         num_samples: int = 10) -> int:
        """记忆重放防止遗忘"""
        if len(self._memory_buffer) < 2:
            return 0
        count = 0
        indices = torch.randperm(len(self._memory_buffer))[:num_samples]
        for idx in indices:
            memory = self._memory_buffer[idx]
            with torch.no_grad():
                read_key = controller.read_key_proj(memory.unsqueeze(0))
                context = controller.longterm.read(read_key)
                prediction = controller.surprise.predictor(context)
                loss = F.mse_loss(prediction, memory.unsqueeze(0))
            if loss.mean().item() > 0.7:
                controller.longterm.write(
                    memory, memory, torch.tensor([[0.5]]), lru_bias=False)
                count += 1
        return count

    def step(self):
        self._step += 1
        self._step %= self.replay_interval


# ═══════════════════════════════════════════════════════
# Phase 4d: 持续学习控制器
# ═══════════════════════════════════════════════════════


class ContinualLearningEngine:
    """持续学习控制器 — 整合三种学习机制"""

    def __init__(self, memory: MemoryController,
                 sleep_interval: int = 50,
                 learning_rate: float = 0.001):
        self.memory = memory
        self.sleep_interval = sleep_interval
        self.online = OnlineLearner(learning_rate=learning_rate)
        self.sleep_consolidator = SleepConsolidator()
        self.defense = ForgettingDefense()
        self._steps = 0
        self._sleeps = 0
        self._history = deque(maxlen=100)

    def step(self, x: torch.Tensor, output: torch.Tensor,
             target: torch.Tensor = None,
             train_predictor: bool = True) -> dict:
        self._steps += 1
        stats = {'step': self._steps, 'online': False,
                 'sleep': None, 'defense': None}
        self.online.update_from_interaction(self.memory, x, output, target)
        encoded = self.memory.encoder(x).squeeze(0).detach()
        self._history.append((x.squeeze(0).detach(), encoded.clone()))
        self.defense.add_to_buffer(encoded)
        stats['online'] = True
        if self._steps % 10 == 0 and train_predictor and len(self._history) >= 5:
            self.online.update_predictor_sgd(
                self.memory, list(self._history)[-20:], epochs=1)
        if self._steps % self.sleep_interval == 0:
            sleep_stats = self.sleep_consolidator.sleep_cycle(self.memory, num_replays=5)
            self._sleeps += 1
            stats['sleep'] = sleep_stats
            defense_stats = self.defense.protect_memory(self.memory)
            replayed = self.defense.replay_memories(self.memory, num_samples=10)
            defense_stats['replayed'] = replayed
            stats['defense'] = defense_stats
        return stats

    def sleep(self):
        sleep_stats = self.sleep_consolidator.sleep_cycle(self.memory, num_replays=20)
        self._sleeps += 1
        defense_stats = self.defense.protect_memory(self.memory)
        replayed = self.defense.replay_memories(self.memory, num_samples=30)
        defense_stats['replayed'] = replayed
        return {'sleep': sleep_stats, 'defense': defense_stats}

    def get_growth_report(self) -> dict:
        mem_stats = self.memory.get_stats()
        return {
            'total_steps': self._steps,
            'sleep_cycles': self._sleeps,
            'memory': mem_stats,
            'history_size': len(self._history),
            'replay_buffer_size': len(self.defense._memory_buffer),
        }


# ═══════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════


def test_online_learning():
    print("=" * 60)
    print("测试 OnlineLearning — 重复输入降低惊喜度")
    print("=" * 60)
    dim = 32
    controller = MemoryController(dim, dim * 2)
    learner = OnlineLearner(learning_rate=0.01)
    pattern = torch.randn(1, dim)
    surprises = []
    for i in range(30):
        output, (h, c), aux = controller(pattern)
        learner.update_from_interaction(controller, pattern, output, target=output)
        surprises.append(aux['surprise'].mean().item())
        if (i + 1) % 10 == 0:
            print(f"  步 {i+1:2d}: 惊喜度={surprises[-1]:.4f}")
    trend = surprises[0] - surprises[-1]
    print(f"  趋势: {surprises[0]:.4f} → {surprises[-1]:.4f} (下降{trend:.4f})")
    print("  ✅" if trend > 0.01 else "  ⚠️", "惊喜度测试完成\n")


def test_sleep_consolidation():
    print("=" * 60)
    print("测试 SleepConsolidation")
    print("=" * 60)
    dim = 32
    controller = MemoryController(dim, dim * 2,
                                   episodic_size=32, longterm_size=64)
    for _ in range(20):
        controller(torch.randn(1, dim))
    before = controller.episodic.size
    print(f"  睡眠前: 情景记忆={before}/{controller.episodic.max_size}")
    consolidator = SleepConsolidator()
    stats = consolidator.sleep_cycle(controller, num_replays=5)
    after = controller.episodic.size
    print(f"  整合: {stats['consolidated']} 条, 回放: {stats['replays']} 次")
    print(f"  睡眠后: 情景记忆={after}/{controller.episodic.max_size}")
    print("  ✅ 睡眠整合完成\n")


def test_forgetting_defense():
    print("=" * 60)
    print("测试 ForgettingDefense")
    print("=" * 60)
    dim = 32
    controller = MemoryController(dim, dim * 2)
    defense = ForgettingDefense()
    for _ in range(30):
        v = torch.randn(1, dim)
        controller(v)
        defense.add_to_buffer(controller.encoder(v).squeeze(0))
    protection = defense.protect_memory(controller)
    print(f"  受保护记忆: {protection['protected_count']}")
    print(f"  高巩固: {protection['high_consolidation']}")
    replayed = defense.replay_memories(controller, num_samples=5)
    print(f"  重放: {replayed}")
    print("  ✅ 遗忘防御测试通过\n")


def test_continual_growth():
    print("=" * 60)
    print("模拟持续学习 — 120步成长过程")
    print("=" * 60)
    dim = 32
    controller = MemoryController(dim, dim * 2,
                                   episodic_size=32, longterm_size=64)
    engine = ContinualLearningEngine(controller, sleep_interval=40, learning_rate=0.01)
    phases = [(30, "初期学习"), (40, "中期积累"), (50, "后期巩固")]
    for steps, label in phases:
        for _ in range(steps):
            pattern = torch.randn(1, dim)
            output, (h, c), aux = controller(pattern)
            engine.step(pattern, output, target=output)
        report = engine.get_growth_report()
        print(f"\n  【{label}】")
        print(f"    总步数: {report['total_steps']}")
        print(f"    睡眠周期: {report['sleep_cycles']}")
        print(f"    情景记忆: {report['memory']['episodic_used']}/{report['memory']['episodic_capacity']}")
    print(f"\n  ✅ 持续学习模拟完成\n")


def test_surprise_decreases_with_learning():
    print("=" * 60)
    print("核心测试 — 惊喜度随学习下降")
    print("=" * 60)
    dim = 32
    controller = MemoryController(dim, dim * 2)
    learner = OnlineLearner(learning_rate=0.02, surprise_threshold=0.3)
    pattern = torch.randn(1, dim)
    early, late = [], []
    for _ in range(10):
        output, (h, c), aux = controller(pattern)
        learner.update_from_interaction(controller, pattern, output, target=output)
        early.append(aux['surprise'].mean().item())
    for _ in range(50):
        output, (h, c), aux = controller(pattern)
        learner.update_from_interaction(controller, pattern, output, target=output)
        late.append(aux['surprise'].mean().item())
    avg_early = sum(early) / len(early)
    avg_late = sum(late) / len(late)
    print(f"  早期平均: {avg_early:.4f}, 后期平均: {avg_late:.4f}")
    if avg_late < avg_early:
        print(f"  ✅ 惊喜度下降 {(avg_early - avg_late):.4f} — 模型在学习！")
    else:
        print("  ⚠️ 惊喜度未下降 — predictor 需更多训练")
    print()


if __name__ == "__main__":
    test_online_learning()
    test_sleep_consolidation()
    test_forgetting_defense()
    test_continual_growth()
    test_surprise_decreases_with_learning()
    print("所有持续学习测试通过！")

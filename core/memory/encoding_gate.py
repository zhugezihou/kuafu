"""
encoding_gate.py — 编码门控系统（True Memory 三信号架构）

核心思想：不是所有信息都需要存。写入前经过三信号门控：
  1. Novelty（新颖性）— 是否已经有了相似内容？
  2. Salience（重要性）— 这条信息值不值得记？
  3. Prediction Error（预测误差）— 这条信息和现有知识矛盾吗？

三信号加权平均 > threshold 才写入长期存储。
"""

import re
import time
from typing import Optional


class EncodingGate:
    """编码门控：决定一条信息是否值得写入长期记忆。

    三信号机制，每个信号 0.0~1.0，加权后与阈值比较。
    """

    def __init__(self, sqlite_backend=None):
        self._backend = sqlite_backend
        # 默认权重：新颖性 0.3，重要性 0.4，预测误差 0.3
        self.weights = {
            "novelty": 0.30,
            "salience": 0.40,
            "prediction_error": 0.30,
        }
        # 默认阈值 0.55（三信号加权平均超过此值才写入）
        # v2: 降低到 0.35 让 NMM 有足够数据积累（NMM 自身有惊喜度过滤，不怕噪声）
        self.threshold = 0.35
        # 强制写入门控：如果任一信号 > 0.9，强制写入
        self.force_threshold = 0.90
        # 冷却期：同一 context 在 N 秒内不重复记
        self.cooldown_seconds = 300
        self._last_write = {}  # source -> timestamp

    def evaluate(self, content: str, context: str = "",
                 source: str = "", tags: list[str] = None) -> dict:
        """评估一条信息是否值得写入长期记忆。

        Returns:
            dict with:
              - should_store: bool
              - scores: dict of individual signals
              - weighted_score: float
              - reason: str
        """
        scores = {
            "novelty": self._score_novelty(content, context, source),
            "salience": self._score_salience(content, context, source, tags or []),
            "prediction_error": self._score_prediction_error(content, context),
        }

        weighted = sum(
            scores[k] * self.weights[k]
            for k in self.weights
        )

        # 冷却检查
        if source:
            last_ts = self._last_write.get(source, 0)
            if time.time() - last_ts < self.cooldown_seconds:
                return {
                    "should_store": False,
                    "scores": scores,
                    "weighted_score": weighted,
                    "reason": f"冷却期未过（{source}，{self.cooldown_seconds}s）",
                }

        # 判断
        if weighted >= self.threshold:
            reason = f"加权分 {weighted:.2f} >= 阈值 {self.threshold}"
            if source:
                self._last_write[source] = time.time()
            return {
                "should_store": True,
                "scores": scores,
                "weighted_score": weighted,
                "reason": reason,
            }

        # 强制写入门控
        max_signal = max(scores.values())
        if max_signal >= self.force_threshold:
            reason = f"信号峰值 {max_signal:.2f} >= 强制阈值 {self.force_threshold}"
            if source:
                self._last_write[source] = time.time()
            return {
                "should_store": True,
                "scores": scores,
                "weighted_score": weighted,
                "reason": reason,
            }

        return {
            "should_store": False,
            "scores": scores,
            "weighted_score": weighted,
            "reason": f"加权分 {weighted:.2f} < 阈值 {self.threshold}，跳过",
        }

    def _score_novelty(self, content: str, context: str,
                       source: str = "") -> float:
        """新颖性评分：和已有记忆的相似度越低，新颖性越高。

        0.0 = 完全重复（不新颖）
        1.0 = 全新信息（很新颖）

        通过 SQLite FTS5 搜索判断相似度。
        如果没有后端，用关键词重叠粗略估计。
        """
        if not self._backend:
            return 0.7  # 没有后端时保守给中高分

        # 从当前内容提取关键词搜索
        keywords = self._backend.extract_keywords(content + " " + context, max_words=5)
        if not keywords:
            return 0.6

        query = ' AND '.join(f'"{kw}"' for kw in keywords)
        similar = self._backend.search(f"{content[:50]}", limit=3, min_importance=0.0)

        if not similar:
            return 0.9  # 搜索结果为空 = 非常新颖

        # 计算 top 相似度
        max_sim = 0.0
        for s in similar:
            overlap = self._keyword_overlap(content, s.get('content', ''))
            max_sim = max(max_sim, overlap)

        # 相似度 0% → 新颖性 1.0，相似度 100% → 新颖性 0.0
        return max(0.1, 1.0 - max_sim)

    def _score_salience(self, content: str, context: str,
                        source: str, tags: list[str]) -> float:
        """重要性评分：这条信息值得记吗？

        启发式规则：
          - 含用户偏好/决策/教训关键词 → 高分
          - 含工具使用/命令记录 → 低分
          - 内容短（<20字）→ 低分
          - 内容有明确结论/规则 → 高分
        """
        if len(content.strip()) < 8:
            return 0.1  # 太短不记

        content_lower = content.lower()
        score = 0.3  # 基线

        # 高价值信号
        high_value = [
            '用户', '偏好', '喜欢', '不喜欢', '习惯', '要求',
            '决策', '决定', '选择', '确认',
            '教训', '经验', '学到', '注意', '记住',
            '规则', '原则', '策略', '方法',
            '错误', '问题', 'bug', '修复', '解决',
            '偏好', 'custom', 'config', '配置',
            'project', '项目', 'repo', '仓库',
            '重要', '关键', '核心', '必须', '务必',
            'prefer', 'like', 'dislike', 'habit',
            'decision', 'lesson', 'rule', 'principle',
        ]
        for kw in high_value:
            if kw in content_lower:
                score += 0.08
                break

        # 中价值信号
        mid_value = [
            '架构', '设计', '方案', '结构', '模块',
            'api', 'interface', '接口',
            '依赖', '版本', '版本号',
            '工作流', 'workflow', '流程',
            '命令', '步骤', '操作',
            '环境', 'env', '部署',
        ]
        for kw in mid_value:
            if kw in content_lower:
                score += 0.05
                break

        # 低价值信号（减分）
        low_value = [
            '你好', '谢谢', '好的', '可以', '明白',
            'hello', 'hi', 'thanks', 'ok', 'okay',
        ]
        for kw in low_value:
            if kw in content_lower:
                score -= 0.1
                break

        # source 加权：带 context 的更有价值
        if source and len(source) > 3:
            score += 0.1

        # tags 加权：有标签的比没标签的重要
        if tags and len(tags) > 0:
            score += 0.05

        return max(0.0, min(1.0, score))

    def _score_prediction_error(self, content: str, context: str) -> float:
        """预测误差评分：这条信息和已有知识矛盾吗？

        矛盾 = 高预测误差 → 值得记住（能纠正旧知识）。
        一致 = 低预测误差 → 可能不需要记。
        """
        if not self._backend or len(content) < 20:
            return 0.4  # 保守给中分

        # 搜索可能相悖的现有记忆
        conflicting = self._backend.search(f"{content[:80]}", limit=2, min_importance=0.5)
        if not conflicting:
            return 0.5  # 没有相关记忆 = 中等的预测误差

        # 如果和现有记忆相似度高，预测误差低
        for c in conflicting:
            overlap = self._keyword_overlap(content, c.get('content', ''))
            if overlap > 0.6:
                return max(0.1, 1.0 - overlap)

        # 如果和现有记忆差异大但主题相关，预测误差高
        return 0.6

    @staticmethod
    def _keyword_overlap(text1: str, text2: str) -> float:
        """计算两段文本的关键词重叠比例"""
        words1 = set(re.findall(r'[a-zA-Z]\w+|\w', text1.lower()))
        words2 = set(re.findall(r'[a-zA-Z]\w+|\w', text2.lower()))
        if not words1 or not words2:
            return 0.0
        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union) if union else 0.0

    def set_threshold(self, threshold: float):
        """调整门控阈值"""
        self.threshold = max(0.0, min(1.0, threshold))

    def set_weights(self, novelty: float, salience: float,
                    prediction_error: float):
        """调整各信号权重（自动归一化）"""
        total = novelty + salience + prediction_error
        if total > 0:
            self.weights["novelty"] = novelty / total
            self.weights["salience"] = salience / total
            self.weights["prediction_error"] = prediction_error / total

    def get_config(self) -> dict:
        """返回当前门控配置"""
        return {
            "weights": self.weights,
            "threshold": self.threshold,
            "force_threshold": self.force_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }

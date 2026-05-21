"""autonomous — 夸父主动进化模块包。"""

from autonomous.reviewer import Reviewer, ReviewerThread
from autonomous.learner import Learner
from autonomous.skill_extractor import SkillExtractor

try:
    from autonomous.prioritizer import (
        IdlePrioritizer,
        TaskPrioritizer,
        EvolutionScheduler,
        ActionItem,
        DecisionRecord,
    )
except ImportError:
    # 降级：允许依赖缺失时继续运行
    IdlePrioritizer = None  # type: ignore
    TaskPrioritizer = None  # type: ignore
    EvolutionScheduler = None  # type: ignore
    ActionItem = None  # type: ignore
    DecisionRecord = None  # type: ignore

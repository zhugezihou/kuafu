"""
core/turn_context.py — 结构化不可变 Turn 上下文（TurnContext）

设计源自 Codex CLI 的 TurnContext 模式：
  - 每次 turn 开始时构建，turn 结束时销毁
  - 不可变快照：所有字段只读
  - 函数式更新：with_*() 返回新的 Self
  - 自描述：to_dict() 可序列化用于 rollout / resume

关系：
  - TurnContext 是"本次交互的完整上下文容器"
  - PromptManager 是"如何组装 system prompt 的工具"
  - TurnContext 注入到 PromptManager 用于组装
"""

import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional, Any


@dataclass(frozen=True)
class TurnContext:
    """不可变的 Turn 上下文快照。
    
    每次模型交互（turn）开始时构建，包含本次推理请求的所有配置和信息。
    所有字段只读（frozen=True）。要修改，用 with_*() 方法返回新实例。
    """

    # ── 身份与标识 ──
    turn_id: str = ""                         # Turn 唯一 ID
    session_id: str = ""                      # 所属 Session ID
    created_at: float = 0.0                   # 创建时间戳

    # ── 任务信息 ──
    task: str = ""                            # 当前任务描述
    task_type: str = "generic"                # 任务类型（coding/research/...）
    turn_count: int = 0                       # 第几轮 turn

    # ── 模型与配置 ──
    model: str = ""                           # 模型名称
    temperature: float = 0.7                  # 模型温度
    max_tokens: int = 4096                    # 最大输出 token

    # ── 审批策略 ──
    approval_enabled: bool = True             # 是否启用审批
    approval_mode: str = "gateway"            # gateway / interactive

    # ── 用户指令 ──
    user_instructions: Optional[str] = None   # AGENTS.md 注入的指令
    reminders: list = field(default_factory=list)  # System reminders

    # ── 工具与技能 ──
    tools_enabled: bool = True                # 是否暴露工具
    skills_available: list = field(default_factory=list)  # 当前可用技能
    active_skills: list = field(default_factory=list)     # 已激活的技能

    # ── 上下文压缩 ──
    compression_ratio: float = 0.0            # 当前压缩比
    is_compressed: bool = False               # 上下文是否已被压缩

    # ── 扩展数据 ──
    metadata: dict = field(default_factory=dict)  # 扩展元数据

    def __post_init__(self):
        """冻结后的初始化——自动填补默认值。"""
        # 使用 object.__setattr__ 绕过 frozen=True
        if not self.turn_id:
            object.__setattr__(self, 'turn_id', self._generate_id())
        if not self.created_at:
            object.__setattr__(self, 'created_at', time.time())

    # ── 工厂方法 ──

    @classmethod
    def create(cls, *, task: str = "", session_id: str = "",
               turn_count: int = 0, **kwargs) -> "TurnContext":
        """创建新的 TurnContext。推荐入口。"""
        return cls(
            turn_id=cls._generate_id(),
            created_at=time.time(),
            task=task,
            session_id=session_id,
            turn_count=turn_count,
            **kwargs,
        )

    # ── 函数式更新 ──

    def with_task(self, task: str) -> "TurnContext":
        """返回新实例，仅更新 task。"""
        return self._replace(task=task)

    def with_turn_count(self, turn_count: int) -> "TurnContext":
        """返回新实例，仅更新 turn_count。"""
        return self._replace(turn_count=turn_count)

    def with_model(self, model: str, **overrides) -> "TurnContext":
        """返回新实例，更新模型及可选参数。"""
        return self._replace(model=model, **overrides)

    def with_approval(self, enabled: bool = True,
                       mode: str = "gateway") -> "TurnContext":
        """返回新实例，更新审批策略。"""
        return self._replace(approval_enabled=enabled, approval_mode=mode)

    def with_reminders(self, reminders: list) -> "TurnContext":
        """返回新实例，更新 reminders。"""
        return self._replace(reminders=reminders)

    def with_compression(self, ratio: float, is_compressed: bool = True) -> "TurnContext":
        """返回新实例，更新压缩状态。"""
        return self._replace(compression_ratio=ratio, is_compressed=is_compressed)

    # ── 序列化 / 自描述 ──

    def to_dict(self) -> dict:
        """序列化为 dict（用于 rollout 持久化 / resume）。"""
        d = asdict(self)
        d["_type"] = "TurnContext"
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TurnContext":
        """从 dict 反序列化。"""
        if "_type" in data:
            data = {k: v for k, v in data.items() if k != "_type"}
        return cls(**data)

    def to_json(self) -> str:
        """序列化为 JSON（用于日志/调试）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # ── 辅助 ──

    def _replace(self, **kwargs) -> "TurnContext":
        """返回新实例，仅更新指定字段。"""
        current = asdict(self)
        current.update(kwargs)
        return TurnContext(**current)

    @staticmethod
    def _generate_id() -> str:
        """生成唯一 ID。"""
        ts = int(time.time() * 1000000)
        return f"turn_{ts}_{hashlib.md5(str(ts).encode()[:4]).hexdigest()[:6]}"

    def __repr__(self) -> str:
        return (f"TurnContext(turn_id={self.turn_id}, "
                f"session={self.session_id[:8] if self.session_id else '?'}, "
                f"turn={self.turn_count}, task='{self.task[:30]}')")

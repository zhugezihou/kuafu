"""
core/approval.py — 统一审批管理器。

职责：
夸父在做出 **有副作用的决策前** 通过此模块向用户发起审批请求，
待用户确认后再执行。

适用范围：
- P4 自检清理（删除孤立记忆/去重 rules/清理缓存）
- L2+ 进化（修改 strategy/ quality.yaml / prompts.yaml）
- 自我修复（修改核心代码 core/*.py / capabilities/*.py）
- 策略重写（大幅度改写 strategy/ 内容）
- 记忆重写（批量修改/删除 memory/ 内容）

不适用（无需审批）：
- L0/L1 进化（写一条记忆、记录日志，无副作用）
- 自检 dry-run（只读不修改）
- 普通任务执行

三种审批模式：

1. **非阻塞提交**（后台线程用）
   ApprovalManager.submit(title, detail, ...)
   → 写入 memory/approvals/{id}.json，立即返回
   → 后台线程继续运行，不等待

2. **终端交互**（夸父对话中用）
   ApprovalManager.terminal_prompt(title, detail, ...)
   → 打印报告 + 显示 [y/N] 选项
   → 返回 True/False

3. **Hermes 飞书审批**（Hermes 侧调用）
   ApprovalManager.list_pending() → 展示待审批项
   ApprovalManager.approve(req_id) → 批准
   ApprovalManager.reject(req_id) → 拒绝

存储路径：memory/approvals/{req_id}.json
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger("kuafu.approval")

ROOT_DIR = Path(__file__).resolve().parent.parent
APPROVALS_DIR = ROOT_DIR / "memory" / "approvals"


@dataclass
class ApprovalRequest:
    """一次审批请求的数据记录。"""
    id: str                       # 唯一 ID（时间戳 + hash）
    title: str                    # 审批标题，简短描述
    detail: str                   # 详细说明，含具体数据
    risk: str                     # low / medium / high
    status: str                   # pending / approved / rejected / expired
    created_at: float
    decided_at: Optional[float] = None
    timeout: int = 86400          # 默认 24 小时过期


def _req_id(title: str) -> str:
    return f"appr_{int(time.time())}_{abs(hash(title)) % 10000:04d}"


# ── 文件持久化 ──────────────────────────────────────────────────────────


def _save(req: ApprovalRequest):
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    path = APPROVALS_DIR / f"{req.id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(req), f, ensure_ascii=False, indent=2)


def _load(req_id: str) -> Optional[ApprovalRequest]:
    path = APPROVALS_DIR / f"{req_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ApprovalRequest(**data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


# ── 统一审批接口 ────────────────────────────────────────────────────────


class ApprovalManager:
    """审批管理器，纯静态方法。"""

    # ── 提交审批 ──────────────────────────────────────────────────────

    @staticmethod
    def submit(
        title: str,
        detail: str = "",
        risk: str = "medium",
        timeout: int = 86400,
    ) -> str:
        """非阻塞提交审批请求。

        写入文件后立即返回，不等待用户决策。
        适用于后台线程（HealthChecker、Evolution 等不能阻塞的场景）。
        返回请求 ID，供后续查询。
        """
        req_id = _req_id(title)
        req = ApprovalRequest(
            id=req_id,
            title=title,
            detail=detail,
            risk=risk,
            status="pending",
            created_at=time.time(),
            timeout=timeout,
        )
        _save(req)
        logger.info(f"📋 提交审批 [{risk.upper()}] {title}  (ID: {req_id})")
        return req_id

    @staticmethod
    def terminal_prompt(
        title: str,
        detail: str = "",
        risk: str = "medium",
        timeout: int = 300,
    ) -> bool:
        """终端交互式审批。

        打印报告 + 显示 [y/N] 选项，等待用户输入。
        适用于夸父对话中需要用户当场确认的场景。
        返回 True（批准）/ False（拒绝/超时）。
        """
        req_id = _req_id(title)
        req = ApprovalRequest(
            id=req_id,
            title=title,
            detail=detail,
            risk=risk,
            status="pending",
            created_at=time.time(),
            timeout=timeout,
        )

        # 打印审批信息
        risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk, "🟡")
        print(f"\n{'='*60}")
        print(f"  {risk_icon}  审批请求 [{risk.upper()}]")
        print(f"  标题: {title}")
        print(f"  详情: ")
        for line in detail.strip().split("\n"):
            print(f"    {line}")
        print(f"{'='*60}")
        print()

        # 等待用户输入（带超时）
        import sys
        from select import select

        print(f"是否执行？ [y/N] （{timeout}s 后自动拒绝）: ", end="", flush=True)

        if sys.stdin.isatty():
            # 真终端 → 用 input() 但有超时
            try:
                import signal

                def handler(signum, frame):
                    raise TimeoutError

                signal.signal(signal.SIGALRM, handler)
                signal.alarm(timeout)
                answer = input().strip().lower()
                signal.alarm(0)
            except TimeoutError:
                answer = ""
                print()  # newline after timeout
            except (EOFError, KeyboardInterrupt):
                answer = ""
        else:
            # 非 TTY（管道/重定向）→ 只读不阻塞
            ready, _, _ = select([sys.stdin], [], [], timeout)
            if ready:
                answer = sys.stdin.readline().strip().lower()
            else:
                answer = ""

        approved = answer in ("y", "yes", "是", "批准", "确认", "ok")
        req.status = "approved" if approved else "rejected"
        req.decided_at = time.time()
        _save(req)

        if approved:
            print(f"  ✅ 已批准: {title}")
        else:
            print(f"  ⏭️  已跳过: {title}")

        logger.info(f"{'✅' if approved else '❌'} 审批 {'通过' if approved else '拒绝'}: {title}")
        return approved

    # ── 决策接口（供 Hermes/外部调用） ──────────────────────────────────

    @staticmethod
    def approve(req_id: str = "") -> bool:
        """批准一个待审批请求。req_id 为空时找最新 pending 的请求。"""
        req = ApprovalManager._resolve(req_id)
        if req is None:
            logger.error("没有可批准的待审批请求")
            return False
        if req.status != "pending":
            logger.warning(f"审批请求 {req.id} 状态为 {req.status}，不能重复审批")
            return False
        req.status = "approved"
        req.decided_at = time.time()
        _save(req)
        logger.info(f"✅ 审批通过: {req.title}")
        return True

    @staticmethod
    def reject(req_id: str = "") -> bool:
        """拒绝一个待审批请求。req_id 为空时找最新 pending 的请求。"""
        req = ApprovalManager._resolve(req_id)
        if req is None:
            logger.error("没有可拒绝的待审批请求")
            return False
        if req.status != "pending":
            logger.warning(f"审批请求 {req.id} 状态为 {req.status}，不能重复审批")
            return False
        req.status = "rejected"
        req.decided_at = time.time()
        _save(req)
        logger.info(f"❌ 审批拒绝: {req.title}")
        return True

    @staticmethod
    def _resolve(req_id: str = "") -> Optional[ApprovalRequest]:
        """解析请求 ID。空串时找最新 pending 的请求。"""
        if req_id:
            return _load(req_id)
        # 没有指定 ID → 找最新的一个 pending 请求
        pending = ApprovalManager.list_pending()
        if not pending:
            return None
        # 按创建时间倒序，取最新的
        pending.sort(key=lambda r: r.created_at, reverse=True)
        return pending[0]

    # ── 查询 ──────────────────────────────────────────────────────────

    @staticmethod
    def list_pending() -> list[ApprovalRequest]:
        """获取所有待审批的请求。"""
        if not APPROVALS_DIR.exists():
            return []
        # 清理过期的
        now = time.time()
        pending = []
        for f in sorted(APPROVALS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                req = ApprovalRequest(**data)
                if req.status == "pending":
                    if now - req.created_at > req.timeout:
                        # 过期了
                        req.status = "expired"
                        req.decided_at = now
                        _save(req)
                        continue
                    pending.append(req)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return pending

    @staticmethod
    def list_recent(limit: int = 10) -> list[ApprovalRequest]:
        """列出最近的审批历史。"""
        if not APPROVALS_DIR.exists():
            return []
        items = []
        for f in sorted(APPROVALS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                items.append(ApprovalRequest(**data))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if len(items) >= limit:
                break
        return items


# ── 格式化输出（供 Hermes/夸父展示） ─────────────────────────────────────


def format_approval(req: ApprovalRequest) -> str:
    """格式化一条审批请求为可读文本。"""
    risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(req.risk, "🟡")
    return (
        f"{risk_icon} **{req.title}**  `{req.id}`\n"
        f"  {req.detail[:300]}"
    )


def format_pending_summary() -> str:
    """返回待审批请求摘要。无待审批时返回空字符串。"""
    pending = ApprovalManager.list_pending()
    if not pending:
        return ""
    lines = ["**📋 夸父待审批事项**\n"]
    for i, req in enumerate(pending, 1):
        risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(req.risk, "🟡")
        detail_short = req.detail[:120].replace("\n", " ")
        lines.append(
            f"{i}. {risk_icon} **{req.title}**\n"
            f"   `{req.id}`\n"
            f"   {detail_short}..."
        )
    lines.append("\n输入「批准 `{id}`」或「拒绝 `{id}`」决策")
    return "\n".join(lines)

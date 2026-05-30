"""
夸父会话管理系统 (Session Store)

职责：
1. SQLite 存储会话历史（轻量级，无需第三方依赖）
2. 会话 CRUD：创建、追加、列出、浏览、导出、删除、恢复
3. 自动截断到 max_tokens 以防止 context 溢出
4. token 估算（基于字符数近似，零依赖）

设计原则：
- 只用 Python sqlite3 标准库
- 会话存储在工作目录的 memory/ 下
- 每条消息存 role + content 完整文本
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
SESSION_DB = MEMORY_DIR / "sessions.db"
SESSION_JSONL_DIR = MEMORY_DIR / "sessions_jsonl"  # 非破坏性压缩的原始消息存储

logger = logging.getLogger("kuafu.session_store")

# token 估算：中文约 1.5 字符/token，英文约 4 字符/token
# Qwen3.5-9B 实测中文约 1.69 chars/token，取安全值 1.6
CHARS_PER_TOKEN = 1.6


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（零依赖近似）"""
    return int(len(text) / CHARS_PER_TOKEN)


@dataclass
class Session:
    """一个会话。"""
    id: str                # 形如 "sess_20260519_003"
    title: str             # 会话标题（自动生成或用户设定）
    created_at: float      # 创建时间戳
    updated_at: float      # 最后活动时间
    message_count: int = 0 # 消息条数
    total_tokens: int = 0  # 总 token 数估算
    status: str = "active" # active / archived / deleted


class SessionStore:
    """会话存储。SQLite 后端。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or SESSION_DB
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @staticmethod
    def _clean_surrogates(text: str) -> str:
        """清理 surrogate 字符，避免 SQLite 写入失败。"""
        if not text:
            return text
        return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")

    # ── 数据库初始化 ──────────────────────────────────────────────

    def _init_db(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                message_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                token_count INTEGER DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, id)
        """)
        self._conn.commit()

    def _get_cursor(self):
        if not self._conn:
            self._init_db()
        return self._conn.cursor()

    # ── 会话管理 ──────────────────────────────────────────────────

    def create_session(self, title: str = "") -> str:
        """创建一个新会话。返回 session_id。"""
        now = time.time()
        import uuid
        session_id = f"sess_{time.strftime('%Y%m%d', time.localtime(now))}_{uuid.uuid4().hex[:8]}"

        cursor = self._get_cursor()
        cursor.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title or f"会话 {time.strftime('%Y%m%d', time.localtime(now))}", now, now),
        )
        self._conn.commit()
        return session_id

    def append_message(self, session_id: str, role: str, content: str):
        """追加一条消息到会话。"""
        cursor = self._get_cursor()
        # 清理 surrogate 字符，避免 SQLite 写入失败
        clean_content = self._clean_surrogates(content)
        token_count = estimate_tokens(clean_content)
        now = time.time()

        cursor.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, token_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, clean_content, now, token_count),
        )
        cursor.execute(
            "UPDATE sessions SET message_count = message_count + 1, "
            "total_tokens = total_tokens + ?, updated_at = ? WHERE id = ?",
            (token_count, now, session_id),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话信息。"""
        cursor = self._get_cursor()
        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return Session(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"],
            total_tokens=row["total_tokens"],
            status=row["status"],
        )

    def get_messages(self, session_id: str, max_tokens: int = 0) -> list[dict]:
        """获取会话消息列表。

        Args:
            session_id: 会话 ID
            max_tokens: 最大 token 数（0=不限）。超限时从最旧的消息开始丢弃，保留最新的。

        Returns:
            [{"role": "...", "content": "..."}, ...]
        """
        cursor = self._get_cursor()
        cursor.execute(
            "SELECT role, content, token_count FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        rows = cursor.fetchall()
        if not rows:
            return []

        messages = []
        for row in rows:
            messages.append({
                "role": row["role"],
                "content": row["content"],
            })

        # 截断：从开头丢弃直到 token 数不超限
        if max_tokens > 0:
            total = sum(row[2] for row in rows)
            if total > max_tokens:
                cut_start = 0
                while cut_start < len(rows) and total > max_tokens:
                    total -= rows[cut_start][2]
                    cut_start += 1
                messages = messages[cut_start:]
                if messages and messages[0]["role"] != "system":
                    messages.insert(0, {
                        "role": "system",
                        "content": f"[注意：会话已截断，移除了之前的 {cut_start} 条消息以节省上下文空间]",
                    })
        return messages

    def get_history_messages(self, session_id: str, max_tokens: int = 8000) -> list[dict]:
        """获取会话历史（不含 system 消息），用于注入新任务的上下文。

        Args:
            session_id: 会话 ID
            max_tokens: 最大 token 数

        Returns:
            消息列表（只含 user/assistant，适合作为 few-shot 上下文）
        """
        cursor = self._get_cursor()
        cursor.execute(
            "SELECT role, content, token_count FROM messages "
            "WHERE session_id = ? AND role != 'system' "
            "ORDER BY id ASC",
            (session_id,),
        )
        rows = cursor.fetchall()
        if not rows:
            return []

        # 从最新消息截断（保留最新的）
        total = 0
        selected = []
        for row in reversed(rows):
            t = row[2]
            if total + t > max_tokens:
                break
            total += t
            selected.insert(0, {"role": row["role"], "content": row["content"]})
        return selected

    def get_context_messages(self, session_id: str, system_prompt: str, max_tokens: int = 12000) -> list[dict]:
        """构建完整的 LLM 上下文消息列表（system + history）。

        自动保证总 token 数不超过 max_tokens。
        """
        # 先估算 system_prompt 的 token
        sys_tokens = estimate_tokens(system_prompt)
        remaining = max_tokens - sys_tokens - 500  # 留 buffer

        # 获取截断后的消息
        messages = self.get_messages(session_id, max_tokens=remaining)

        # 把 system prompt 放在最前面
        if messages and messages[0]["role"] == "system":
            messages[0] = {"role": "system", "content": system_prompt}
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})

        return messages

    # ── 会话浏览 ──────────────────────────────────────────────────

    def list_sessions(self, limit: int = 20, status: str = "") -> list[Session]:
        """列出会话。按更新时间倒序。"""
        cursor = self._get_cursor()
        if status:
            cursor.execute(
                "SELECT * FROM sessions WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        return [
            Session(
                id=r["id"], title=r["title"],
                created_at=r["created_at"], updated_at=r["updated_at"],
                message_count=r["message_count"], total_tokens=r["total_tokens"],
                status=r["status"],
            )
            for r in cursor.fetchall()
        ]

    def search_sessions(self, query: str, limit: int = 10) -> list[Session]:
        """搜索会话标题和内容。简单的 LIKE 搜索。"""
        cursor = self._get_cursor()
        cursor.execute(
            "SELECT DISTINCT s.* FROM sessions s "
            "LEFT JOIN messages m ON s.id = m.session_id "
            "WHERE s.title LIKE ? OR m.content LIKE ? "
            "ORDER BY s.updated_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )
        return [
            Session(
                id=r["id"], title=r["title"],
                created_at=r["created_at"], updated_at=r["updated_at"],
                message_count=r["message_count"], total_tokens=r["total_tokens"],
                status=r["status"],
            )
            for r in cursor.fetchall()
        ]

    def archive_session(self, session_id: str):
        """归档会话。"""
        cursor = self._get_cursor()
        cursor.execute(
            "UPDATE sessions SET status = 'archived', updated_at = ? WHERE id = ?",
            (time.time(), session_id),
        )
        self._conn.commit()

    def delete_session(self, session_id: str):
        """删除会话及其所有消息。"""
        cursor = self._get_cursor()
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()

    def prune_sessions(self, keep_days: int = 30):
        """清理超过 keep_days 天未活动的归档会话。"""
        cutoff = time.time() - keep_days * 86400
        cursor = self._get_cursor()
        cursor.execute(
            "SELECT id FROM sessions WHERE status = 'archived' AND updated_at < ?",
            (cutoff,),
        )
        old_ids = [r[0] for r in cursor.fetchall()]
        for sid in old_ids:
            cursor.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        cursor.execute(
            "DELETE FROM sessions WHERE status = 'archived' AND updated_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return len(old_ids)

    def export_session(self, session_id: str) -> Optional[str]:
        """导出会话为 JSON 格式。"""
        session = self.get_session(session_id)
        if not session:
            return None
        cursor = self._get_cursor()
        cursor.execute(
            "SELECT role, content, timestamp FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        messages = [
            {
                "role": r["role"],
                "content": r["content"][:500] + ("..." if len(r["content"]) > 500 else ""),
                "timestamp": r["timestamp"],
            }
            for r in cursor.fetchall()
        ]
        return json.dumps({
            "session": asdict(session),
            "messages": messages,
        }, ensure_ascii=False, indent=2)

    def get_stats(self) -> dict:
        """获取会话统计。"""
        cursor = self._get_cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE status = 'active'")
        active = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(message_count) FROM sessions")
        total_msgs = cursor.fetchone()[0] or 0
        cursor.execute("SELECT SUM(total_tokens) FROM sessions")
        total_tokens = cursor.fetchone()[0] or 0
        return {
            "total_sessions": total,
            "active_sessions": active,
            "total_messages": total_msgs,
            "total_tokens_estimated": total_tokens,
        }

    # ── JSONL 持久化（ContextCollapse 支持）────────────────────────

    def _get_jsonl_path(self, session_id: str) -> Path:
        """获取 session 对应的 JSONL 文件路径。"""
        SESSION_JSONL_DIR.mkdir(parents=True, exist_ok=True)
        safe_id = session_id.replace("/", "_").replace("..", "")
        return SESSION_JSONL_DIR / f"{safe_id}.jsonl"

    def save_raw_messages(self, session_id: str, messages: list[dict]):
        """将完整消息列表以 JSONL 格式持久化（覆盖写入），保留原始内容。

        ContextCollapse 写入原始消息的完整副本，之后即使上下文被压缩，
        也可通过 get_raw_messages() 按需读取原始细节。
        """
        path = self._get_jsonl_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for msg in messages:
                # 只保存 role + content，不保存 tool_call_id 等 transient 字段
                record = {
                    "role": msg.get("role", ""),
                    "content": msg.get("content", ""),
                }
                if msg.get("tool_calls"):
                    # 保留 tool_calls 摘要（工具名+参数缩略）
                    tc_infos = []
                    for tc in msg.get("tool_calls", []):
                        fn = tc.get("function", {})
                        tc_infos.append({
                            "name": fn.get("name", "?"),
                            "args_preview": str(fn.get("arguments", {}))[:200],
                        })
                    record["tool_calls"] = tc_infos
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_raw_messages(self, session_id: str) -> Optional[list[dict]]:
        """读取 JSONL 中的完整原始消息列表。

        返回与原始 messages 格式兼容的列表：[{role, content, ...}, ...]
        若文件不存在，返回 None。
        """
        path = self._get_jsonl_path(session_id)
        if not path.exists():
            return None
        messages = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    messages.append(msg)
                except json.JSONDecodeError:
                    continue
        return messages

    def get_raw_messages_since(self, session_id: str, start_index: int = 0,
                                 max_tokens: int = 3000) -> list[dict]:
        """从 JSONL 中读取指定范围的原始消息。

        Args:
            session_id: 会话 ID
            start_index: 从第几条消息开始（0-indexed）
            max_tokens: 最多返回多少 token 的数据

        Returns:
            原始消息列表
        """
        all_msgs = self.get_raw_messages(session_id)
        if not all_msgs:
            return []

        # 从 start_index 开始
        selected = all_msgs[start_index:]

        # 按 token 数裁剪
        total_tokens = 0
        result = []
        for msg in selected:
            tokens = estimate_tokens(str(msg.get("content", "")))
            if total_tokens + tokens > max_tokens and result:
                break
            total_tokens += tokens
            result.append(msg)

        return result

    def close(self):
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        try:
            self.close()
        except sqlite3.ProgrammingError:
            # SQLite 对象跨线程导致的异常，忽略（GC 线程与创建线程不同）
            pass
        except Exception:
            pass

    # ── P1-3: 会话 Fork ────────────────────────────────────────────

    def fork_session(self, src_session_id: str, title: str = "",
                     include_history: bool = True, max_tokens: int = 6000) -> Optional[str]:
        """从现有会话 fork 出一个新子会话。

        将源会话的上下文注入新会话作为「历史背景」，
        新会话可以在此基础上继续对话而不影响源会话。

        Args:
            src_session_id: 源会话 ID
            title: 新会话标题
            include_history: 是否注入源会话历史
            max_tokens: 注入历史的最大 token 数

        Returns:
            新会话 ID，或 None（源不存在时）
        """
        src = self.get_session(src_session_id)
        if not src:
            logger.warning(f"fork_session: 源会话 {src_session_id} 不存在")
            return None

        # 创建新会话
        if not title:
            title = f"[fork] {src.title}"
        new_id = self.create_session(title=title)

        if include_history:
            # 从源会话获取历史消息（不含 system，含截断）
            history = self.get_history_messages(src_session_id, max_tokens=max_tokens)
            if history:
                # 注入历史背景作为第一条系统消息
                history_text = "\n".join(
                    f"## {m['role']}: {m['content'][:500]}"
                    for m in history[-20:]  # 最多 20 条
                )
                fork_context = (
                    f"[会话历史] 以下内容来自父会话「{src.title}」({src_session_id})：\n"
                    f"{history_text}\n\n"
                    f"[提示] 你正在父会话的基础上继续工作。请优先参考以上历史上下文。"
                )
                self.append_message(new_id, "system", fork_context)

        return new_id

    def resume_context(self, src_session_id: str, max_tokens: int = 4000, use_llm: bool = True) -> Optional[str]:
        """从历史会话恢复上下文（压缩版）。

        生成一段「上下文简报」注入到当前会话，比 fork 更轻量：
        - 不创建新会话
        - 只返回一段可被注入 system prompt 的文本
        - 保留源会话的关键决策、Pin 标记、白板信息
        - P3-3: use_llm=True 时使用本地 LLM 生成智能摘要，比关键词提取更准确

        Args:
            src_session_id: 源会话 ID
            max_tokens: 简报最大 token 数
            use_llm: 是否使用 LLM 生成智能摘要（默认 True）

        Returns:
            上下文简报文本，或 None
        """
        src = self.get_session(src_session_id)
        if not src:
            return None

        # 获取所有消息
        messages = self.get_messages(src_session_id, max_tokens=max_tokens)
        if not messages:
            return None

        # P3-3: 尝试 LLM 智能摘要（更结构化、更准确）
        if use_llm:
            try:
                llm_summary = self._llm_summarize_session(messages, src, max_tokens)
                if llm_summary:
                    return llm_summary
            except Exception as e:
                logger.warning(f"LLM 会话摘要失败，回退到关键词提取: {e}")

        # 回退：关键词提取（原有逻辑）
        parts = []
        parts.append(f"📋 会话简报：{src.title} ({src_session_id})")
        parts.append(f"   {src.message_count} 条消息 · {src.total_tokens} tokens")

        # 提取 system prompt 中的白板/决策
        for m in messages:
            if m["role"] == "system" and any(kw in (m.get("content") or "")
                                            for kw in ["白板", "决策", "决定", "方案", "已确定"]):
                content = str(m.get("content", ""))[:400]
                parts.append(f"\n📝 决策记录：\n{content}")
                break

        # 提取包含 [PIN] / [KEEP] 标记的消息
        pinned = []
        for m in messages:
            content = str(m.get("content", ""))
            if "[PIN]" in content or "[KEEP]" in content or "[保留]" in content:
                pinned.append(content[:200])
        if pinned:
            parts.append(f"\n📌 关键信息（{len(pinned)} 条 Pin 记录）：")
            for p in pinned[-5:]:  # 最多 5 条
                parts.append(f"   • {p.replace('[PIN]', '').replace('[KEEP]', '').strip()[:100]}")

        # 提取最近 user-assistant 交互作为上下文
        user_msgs = [(i, m) for i, m in enumerate(messages) if m["role"] == "user"]
        if user_msgs:
            parts.append(f"\n💬 最后 {min(3, len(user_msgs))} 轮对话：")
            for idx, m in user_msgs[-3:]:
                content = str(m.get("content", ""))[:200]
                parts.append(f"   🧑 {content}")

        return "\n".join(parts)

    # ── P3-3: LLM 智能会话摘要 ─────────────────────────────────────

    def _llm_summarize_session(self, messages: list[dict], session: Session,
                                max_tokens: int = 4000) -> Optional[str]:
        """使用本地 LLM 对会话进行结构化智能摘要。

        自动提取：对话主题、关键决策、待办事项、技术结论。
        """
        try:
            from core.llm import LLMClient

            # 准备会话内容样本（取首+尾以减少 token 消耗）
            content_parts = []
            # 系统消息始终保留
            for m in messages:
                if m["role"] == "system":
                    c = str(m.get("content", ""))[:400]
                    if c:
                        content_parts.append(f"[系统] {c}")

            # 取前 3 轮 + 后 5 轮对话
            non_system = [(i, m) for i, m in enumerate(messages) if m["role"] != "system"]
            front = non_system[:3]
            back = non_system[-5:] if len(non_system) > 8 else non_system[3:]

            seen = set()
            for idx, m in front + back:
                role = m["role"]
                c = str(m.get("content", ""))[:300]
                if c and c not in seen:
                    seen.add(c)
                    label = "🧑 用户" if role == "user" else "🤖 助手"
                    content_parts.append(f"{label}: {c}")

            conversation_sample = "\n\n".join(content_parts)
            if len(conversation_sample) > 8000:
                conversation_sample = conversation_sample[:8000] + "..."

            prompt = f"""你是一个专业的对话摘要助手。请分析以下会话，输出结构化简报。

会话标题：{session.title}
消息数：{session.message_count} 条

对话内容：
{conversation_sample}

请严格按以下格式输出（不要加额外解释）：

## 主题
（1-2句话概括会话目的）

## 关键决策
（列出已确定的方案、决定，每条一行用 - 开头）

## 待办事项
（列出尚未完成的行动项，每条一行用 - 开头；无则写"无"）

## 技术结论
（列出代码/架构/配置方面的结论性信息，每条一行用 - 开头；无则写"无"）"""

            client = LLMClient(
                max_tokens=min(max_tokens, 2000),
                temperature=0.2,
            )
            result = client.chat([{"role": "user", "content": prompt}])
            llm_content = (result.get("content") or "").strip()

            if llm_content and len(llm_content) > 50:
                return (
                    f"📋 会话简报（LLM）：{session.title} ({session.id})\n"
                    f"   {session.message_count} 条消息\n\n"
                    f"{llm_content}"
                )

            return None
        except ImportError:
            logger.warning("LLM 模块不可用，跳过智能摘要")
            return None
        except Exception as e:
            logger.warning(f"LLM 摘要异常: {e}")
            return None

    def find_related_sessions(self, query: str, limit: int = 5) -> list[Session]:
        """找到与查询相关的会话（用于推荐 resume 目标）。

        先用内容搜索找匹配，再用关键词匹配标题。
        """
        results = self.search_sessions(query, limit=limit)
        # 补充：按更新时间排序
        all_sessions = self.list_sessions(limit=limit * 2)
        existing_ids = {s.id for s in results}
        for s in all_sessions:
            if s.id not in existing_ids:
                results.append(s)
            if len(results) >= limit:
                break
        return results[:limit]

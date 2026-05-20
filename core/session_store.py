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
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
SESSION_DB = MEMORY_DIR / "sessions.db"

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
        self._conn = sqlite3.connect(str(self.db_path))
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
        # 生成唯一 ID
        date_str = time.strftime("%Y%m%d", time.localtime(now))
        cursor = self._get_cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM sessions WHERE id LIKE ?",
            (f"sess_{date_str}_%",),
        )
        count = cursor.fetchone()[0] + 1
        session_id = f"sess_{date_str}_{count:03d}"

        cursor.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title or f"会话 {date_str}-{count:03d}", now, now),
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

    def close(self):
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()

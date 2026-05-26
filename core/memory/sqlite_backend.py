"""
sqlite_backend.py — SQLite FTS5 全文索引记忆后端

核心能力：
  - FTS5 BM25 全文检索
  - 关键词提取 + 自动索引
  - 时间衰减排序
  - 跨 session 持久化

零外部依赖：仅用 Python 标准库 sqlite3 (Python 自带 FTS5)
"""

import json
import os
import sqlite3
import time
import re
import hashlib
from pathlib import Path
from typing import Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "memory" / "memory.db"


class SQLiteFTSBackend:
    """SQLite FTS5 全文索引记忆后端

    使用 SQLite 内建的 FTS5 扩展做 BM25 全文检索，不需要任何外部依赖。
    核心表：
      - memories: 原始记忆数据
      - memories_fts: FTS5 虚拟表（content 同步）
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self):
        """连接数据库，建表（如果不存在）"""
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-8000")  # 8MB cache

        # 记忆主表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                context TEXT DEFAULT '',
                source TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                importance REAL DEFAULT 0.5,
                timestamp REAL NOT NULL,
                session_id TEXT DEFAULT '',
                created TEXT DEFAULT (datetime('now')),
                accessed_count INTEGER DEFAULT 0,
                last_accessed REAL DEFAULT 0,
                ttl_days REAL DEFAULT 30,
                is_compressed INTEGER DEFAULT 0,
                parent_id TEXT DEFAULT ''
            )
        """)

        # FTS5 全文索引（内容同步到 memories 表）
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                context,
                tags,
                content=memories,
                content_rowid='rowid',
                tokenize='unicode61 tokenchars ''-/_#@.'''
            )
        """)

        # 全局数据同步触发器：确保 FTS5 与 memories 表保持同步
        self._conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, context, tags)
                VALUES (new.rowid, new.content, new.context, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, context, tags)
                VALUES ('delete', old.rowid, old.content, old.context, old.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, context, tags)
                VALUES ('delete', old.rowid, old.content, old.context, old.tags);
                INSERT INTO memories_fts(rowid, content, context, tags)
                VALUES (new.rowid, new.content, new.context, new.tags);
            END;
        """)

        # 关键词哈希索引（去重用）
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_hashes (
                hash_key TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_timestamp
            ON memories(timestamp DESC)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_source
            ON memories(source)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_session
            ON memories(session_id)
        """)
        self._conn.commit()

    # ── 关键词提取 ───────────────────────────────────────────────────

    @staticmethod
    def extract_keywords(text: str, max_words: int = 10) -> list[str]:
        """提取文本的关键词。

        策略：
        1. 中文：2-gram 字符片段
        2. 英文：分词后过滤停用词
        """
        if not text:
            return []

        keywords = set()
        text_lower = text.lower()

        # 中文 2-gram
        for i in range(len(text) - 1):
            ch = text[i:i+2]
            if all('\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' for c in ch):
                keywords.add(ch)

        # 英文分词
        eng_words = re.findall(r'[a-z]\w+(?:[-_]\w+)*', text_lower)
        stopwords = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
            'on', 'with', 'at', 'by', 'from', 'and', 'or', 'not', 'but', 'if',
            'so', 'as', 'than', 'that', 'this', 'these', 'those', 'it', 'its',
            'i', 'you', 'we', 'they', 'he', 'she', 'my', 'your', 'our', 'their',
            'his', 'her', 'me', 'us', 'them', 'about', 'into', 'over', 'after',
            'all', 'also', 'just', 'more', 'most', 'some', 'any', 'each', 'every',
            'no', 'nor', 'only', 'own', 'same', 'very', 'too', 'again', 'further',
            'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how',
        }
        for w in eng_words:
            if w not in stopwords and len(w) > 1:
                keywords.add(w)

        # 截取最重要的前 N 个
        sorted_kws = sorted(keywords, key=lambda x: text_lower.count(x), reverse=True)
        return sorted_kws[:max_words]

    @staticmethod
    def compute_hash(content: str, context: str = "") -> str:
        """计算内容的哈希值（用于去重）"""
        text = (content + context).strip().lower()
        # 归一化：去空格 + 排序中文字符
        normalized = ''.join(sorted(re.sub(r'\s+', '', text)))
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]

    # ── 核心 CRUD ────────────────────────────────────────────────────

    def store(self, content: str, context: str = "", source: str = "",
              tags: list[str] = None, importance: float = 0.5,
              session_id: str = "", ttl_days: float = 30,
              is_compressed: bool = False, parent_id: str = "") -> str:
        """存储一条记忆，返回记忆 ID。

        自动去重（相同内容更新 timestamp 和 accessed_count）。
        """
        h = self.compute_hash(content, context)

        # 去重检查
        existing = self._conn.execute(
            "SELECT memory_id FROM memory_hashes WHERE hash_key = ?",
            (h,)
        ).fetchone()
        if existing:
            # 更新访问次数和时间
            self._conn.execute(
                "UPDATE memories SET timestamp = ?, accessed_count = accessed_count + 1, last_accessed = ? WHERE id = ?",
                (time.time(), time.time(), existing[0])
            )
            self._conn.commit()
            return existing[0] + "_dedup"

        mem_id = f"mem_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
        tags_json = json.dumps(tags or [], ensure_ascii=False)

        self._conn.execute(
            """INSERT INTO memories
               (id, content, context, source, tags, importance, timestamp,
                session_id, ttl_days, is_compressed, parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mem_id, content, context, source, tags_json, importance,
             time.time(), session_id, ttl_days, 1 if is_compressed else 0, parent_id)
        )

        # 写入关键词哈希索引
        self._conn.execute(
            "INSERT INTO memory_hashes (hash_key, memory_id, timestamp) VALUES (?, ?, ?)",
            (h, mem_id, time.time())
        )

        self._conn.commit()
        return mem_id

    def search(self, query: str, limit: int = 5, min_importance: float = 0.0,
               source: str = "", recency_boost: bool = True) -> list[dict]:
        """三层检索：精确匹配 → FTS5 BM25 → 关键词 fallback

        返回按相关性 + 时间衰减排序的结果。
        """
        now = time.time()
        results = []

        # 第 1 层：FTS5 全文检索（精准 BM25）
        if query.strip():
            # FTS5 查询语法：双引号精确短语，* 前缀匹配
            fts_query = self._build_fts_query(query)
            if fts_query:
                rows = self._conn.execute(
                    """SELECT m.id, m.content, m.context, m.source, m.tags,
                              m.importance, m.timestamp, m.session_id,
                              m.accessed_count, m.last_accessed, m.ttl_days,
                              m.is_compressed, m.parent_id,
                              rank AS bm25_score
                       FROM memories_fts f
                       JOIN memories m ON m.rowid = f.rowid
                       WHERE memories_fts MATCH ?
                         AND m.importance >= ?
                       ORDER BY rank""",
                    (fts_query, min_importance)
                ).fetchall()

                for r in rows:
                    d = dict(r)
                    # 时间衰减因子：24h 内无衰减，7 天衰减 50%，30 天衰减 90%
                    age_hours = (now - d['timestamp']) / 3600
                    if age_hours < 24:
                        time_decay = 1.0
                    elif age_hours < 24 * 7:
                        time_decay = 1.0 - (age_hours - 24) / (24 * 7 - 24) * 0.5
                    else:
                        time_decay = max(0.1, 0.5 - (age_hours - 24 * 7) / (24 * 30 - 24 * 7) * 0.4)

                    # 访问频率增益
                    freq_boost = min(1.5, 1.0 + d['accessed_count'] * 0.05)

                    # 最终评分：BM25 排名倒转 + 时间衰减 + 频率增益
                    bm25 = d.pop('bm25_score', 100)
                    d['bm25_raw'] = bm25
                    d['time_decay'] = time_decay
                    d['final_score'] = (100 / (1 + abs(bm25) if bm25 != 0 else 100)) * time_decay * freq_boost

                    # TTL 过期过滤
                    max_age = d['ttl_days'] * 86400
                    if now - d['timestamp'] > max_age:
                        continue

                    # source 过滤
                    if source and d.get('source', '') != source:
                        continue

                    d['tags'] = json.loads(d.get('tags', '[]'))
                    d['is_compressed'] = bool(d['is_compressed'])
                    results.append(d)

        # 第 2 层：关键词 fallback（FTS5 无结果时）
        if not results:
            keywords = self.extract_keywords(query, max_words=5)
            for kw in keywords:
                pattern = f"%{kw}%"
                rows = self._conn.execute(
                    """SELECT * FROM memories
                       WHERE (content LIKE ? OR context LIKE ?)
                         AND importance >= ?
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (pattern, pattern, min_importance, limit)
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    d['tags'] = json.loads(d.get('tags', '[]'))
                    d['is_compressed'] = bool(d['is_compressed'])
                    d['time_decay'] = 1.0
                    d['final_score'] = 0.5
                    results.append(d)

        # 去重 + 截取
        seen = set()
        unique = []
        for r in results:
            if r['id'] not in seen:
                seen.add(r['id'])
                unique.append(r)
        unique.sort(key=lambda x: x.get('final_score', 0), reverse=True)

        return unique[:limit]

    def reflect(self, query: str) -> str:
        """综合推理：搜索 + 组织成可读格式"""
        results = self.search(query, limit=5)
        if not results:
            return f"关于「{query}」没有找到相关记忆。"
        lines = [f"关于「{query}」找到 {len(results)} 条相关记忆："]
        for i, r in enumerate(results, 1):
            content = r['content'][:300]
            lines.append(f"\n{i}. {content}")
            if r.get('context'):
                lines.append(f"   (上下文: {r['context'][:100]})")
            kw = ', '.join(r.get('tags', [])[:3])
            if kw:
                lines.append(f"   (标签: {kw})")
            if r.get('time_decay', 1.0) < 0.5:
                lines.append(f"   (较旧记忆)")
        return '\n'.join(lines)

    # ── 辅助方法 ─────────────────────────────────────────────────────

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """将自然语言查询转换为 FTS5 查询语法。"""
        if not query.strip():
            return ""

        # 提取英文词 + 中文 2-gram
        parts = []
        for token in re.findall(r'[a-zA-Z]\w*(?:[-_]\w+)*|[\u4e00-\u9fff]+', query):
            token_lower = token.lower()
            if re.match(r'^[a-z]', token_lower):
                # 英文词加 * 前缀匹配
                if len(token_lower) > 2:
                    parts.append(f'"{token_lower}"*')
                else:
                    parts.append(token_lower)
            else:
                # 中文拆成 2-gram
                for i in range(len(token) - 1):
                    parts.append(token[i:i+2])

        if not parts:
            return ""

        return ' AND '.join(parts)

    def get_by_id(self, mem_id: str) -> Optional[dict]:
        """按 ID 获取单条记忆"""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (mem_id,)
        ).fetchone()
        if row:
            d = dict(row)
            d['tags'] = json.loads(d.get('tags', '[]'))
            d['is_compressed'] = bool(d['is_compressed'])
            return d
        return None

    def update(self, mem_id: str, **kwargs) -> bool:
        """更新记忆字段"""
        allowed = {'content', 'context', 'source', 'tags', 'importance',
                   'ttl_days', 'accessed_count', 'last_accessed'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        set_parts = []
        params = []
        for k, v in updates.items():
            if k == 'tags':
                v = json.dumps(v, ensure_ascii=False)
            set_parts.append(f"{k} = ?")
            params.append(v)

        params.append(mem_id)
        self._conn.execute(
            f"UPDATE memories SET {', '.join(set_parts)} WHERE id = ?",
            params
        )
        self._conn.commit()
        return True

    def delete_expired(self) -> int:
        """删除过期的记忆，返回删除数量"""
        now = time.time()
        deleted = self._conn.execute(
            "DELETE FROM memories WHERE ? - timestamp > ttl_days * 86400",
            (now,)
        ).rowcount
        if deleted > 0:
            self._conn.commit()
            # 清理孤立的 hash 索引
            self._conn.execute(
                """DELETE FROM memory_hashes WHERE memory_id NOT IN
                   (SELECT id FROM memories)"""
            )
            self._conn.commit()
        return deleted

    def count(self) -> int:
        """返回有效记忆数量"""
        now = time.time()
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE ? - timestamp <= ttl_days * 86400",
            (now,)
        ).fetchone()
        return row['cnt'] if row else 0

    def get_stats(self) -> dict:
        """返回统计信息"""
        total = self._conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()['c']
        now = time.time()
        expired = self._conn.execute(
            "SELECT COUNT(*) as c FROM memories WHERE ? - timestamp > ttl_days * 86400",
            (now,)
        ).fetchone()['c']
        compressed = self._conn.execute(
            "SELECT COUNT(*) as c FROM memories WHERE is_compressed = 1"
        ).fetchone()['c']
        return {
            "total": total,
            "valid": total - expired,
            "expired": expired,
            "compressed": compressed,
            "db_path": str(self.db_path),
            "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
        }

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

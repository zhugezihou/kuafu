"""
memory_api.py — 夸父记忆系统

支持两种后端模式（通过 KUAFU_MEMORY_MODE 环境变量选择）:
  - 'file' (默认): 本地 JSON 文件存储，零依赖
  - 'hindsight': 对接 Hindsight Cloud API，语义搜索 + 知识图谱

Hindsight 模式需要配置:
  - HINDSIGHT_API_KEY: API key
  - HINDSIGHT_BANK_ID: 记忆库 ID (默认: kuafu)
  - HINDSIGHT_API_URL: API 地址 (默认: https://api.hindsight.vectorize.io)

核心原则：零新增 Python 依赖，仅用 urllib 和 json 标准库。

v0.4 新增:
  - 写入去重（关键词 overlap > 60% 视为重复，跳过或覆盖旧值）
  - TTL 过期（默认 30 天，搜索时自动过滤）
  - 自动清理（清理过期记忆文件）
  - 合并管理（同一主题多条记忆自动标记）
"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


# ── 配置 ──────────────────────────────────────────────────────────────

DEFAULT_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
DEFAULT_TASKS_DIR = DEFAULT_MEMORY_DIR / "tasks"

# Hindsight Cloud API 默认地址
DEFAULT_HINDSIGHT_API_URL = "https://api.hindsight.vectorize.io"

# v0.4 记忆管理默认值
DEFAULT_TTL_DAYS = 30                     # 默认过期天数
DEFAULT_DEDUP_OVERLAP_RATIO = 0.6        # 去重关键词重叠阈值
DEFAULT_MERGE_THRESHOLD = 5              # 同一 source/context 超此条数触发合并
MAX_MEMORY_CHARS = 2000                  # 单条记忆最大字符数


# ── 工具函数 ──────────────────────────────────────────────────────────

def _get_env_or_dotenv(key: str, default: str = "") -> str:
    """从环境变量或 .env 文件取值，支持多搜索路径"""
    val = os.environ.get(key)
    if val:
        return val
    # 尝试从 .env 读取
    search_paths = [
        Path.cwd() / ".env",
        Path.home() / ".hermes" / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in search_paths:
        if env_path.exists():
            try:
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        return v.strip().strip("\"'")
            except OSError:
                continue
    return default


def _hindsight_request(
    method: str,
    path: str,
    body: Optional[dict] = None,
    api_key: Optional[str] = None,
    base_url: str = DEFAULT_HINDSIGHT_API_URL,
    timeout: int = 60,
) -> dict:
    """向 Hindsight Cloud API 发送 HTTP 请求 (urllib, 零依赖)

    返回解析后的 JSON dict。
    请求失败时抛出 RuntimeError，附带服务器返回的错误信息。
    """
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8") if body else None

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"Hindsight API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Hindsight API unreachable: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Hindsight request failed: {e}")


def _keyword_overlap_ratio(text1: str, text2: str) -> float:
    """计算两段文本的关键词重叠比例。
    
    将文本拆分为 2-gram 词元（中文/英文），计算交集与并集之比。
    """
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    # 中文字符拆成 2-gram
    chars1 = {text1[i:i+2] for i in range(len(text1)-1)}
    chars2 = {text2[i:i+2] for i in range(len(text2)-1)}
    all_words1 = words1 | chars1
    all_words2 = words2 | chars2
    intersection = all_words1 & all_words2
    union = all_words1 | all_words2
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _clean_surrogates(obj):
    """递归清理字符串中的 surrogate 字符"""
    if isinstance(obj, str):
        return obj.encode('utf-8', errors='replace').decode('utf-8')
    if isinstance(obj, dict):
        return {k: _clean_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_surrogates(i) for i in obj]
    return obj


# ── 文件后端 ──────────────────────────────────────────────────────────

class FileMemoryBackend:
    """本地 JSON 文件记忆存储（v0.4 新增去重 + TTL + 自动清理）"""

    def __init__(self, memory_dir: Optional[Path] = None):
        self.memory_dir = memory_dir or DEFAULT_MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = self.memory_dir / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.memory_dir / "index.json"
        self._index = self._load_index()
        # v0.4 配置
        self._ttl_days = float(os.environ.get("KUAFU_MEMORY_TTL_DAYS", str(DEFAULT_TTL_DAYS)))
        self._dedup_ratio = float(os.environ.get("KUAFU_MEMORY_DEDUP_RATIO", str(DEFAULT_DEDUP_OVERLAP_RATIO)))

    def _load_index(self) -> dict:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"memories": [], "last_id": 0}

    def _save_index(self):
        self._index_path.write_text(
            json.dumps(_clean_surrogates(self._index), ensure_ascii=False, indent=2)
        )

    def _find_duplicate(self, content: str, context: str = "") -> Optional[dict]:
        """查找与 content 相似度超阈值的已有记忆。
        
        v0.4 新增：写入前去重检查。
        返回匹配的第一条记忆 dict（含 id, file_path），无重复返回 None。
        """
        for m in reversed(self._index["memories"]):
            mem_id = m["id"]
            file_path = self.memory_dir / f"{mem_id}.json"
            if not file_path.exists():
                continue
            try:
                entry = json.loads(file_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            existing = entry.get("content", "")
            ratio = _keyword_overlap_ratio(content, existing)
            if ratio >= self._dedup_ratio:
                return {"id": mem_id, "file_path": file_path, "entry": entry, "ratio": ratio}
        return None

    def _delete_expired(self) -> int:
        """删除过期的记忆文件，返回删除数量。
        
        v0.4 新增：TTL 过期清理。
        """
        now = time.time()
        max_age = self._ttl_days * 86400
        expired_ids = []
        kept = []
        for m in self._index["memories"]:
            ts = m.get("timestamp", 0)
            if now - ts > max_age:
                expired_ids.append(m["id"])
            else:
                kept.append(m)
        if not expired_ids:
            return 0
        for mem_id in expired_ids:
            fp = self.memory_dir / f"{mem_id}.json"
            if fp.exists():
                fp.unlink()
        self._index["memories"] = kept
        self._save_index()
        return len(expired_ids)

    def _merge_similar(self) -> int:
        """合并同一 source/context 下超过阈值的多条记忆。
        
        v0.4 新增：将同一主题（相同 source）的旧记忆合并到一个文件。
        返回合并（归档）的数量。
        """
        # 按 source 分组
        groups = {}
        for m in self._index["memories"]:
            mem_id = m["id"]
            fp = self.memory_dir / f"{mem_id}.json"
            if not fp.exists():
                continue
            try:
                entry = json.loads(fp.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            key = entry.get("source", "") or entry.get("context", "")
            if not key:
                continue
            groups.setdefault(key, []).append({"id": mem_id, "entry": entry, "path": fp})

        merged_count = 0
        for key, items in groups.items():
            if len(items) < DEFAULT_MERGE_THRESHOLD:
                continue
            # 保留最新的，合并内容
            items.sort(key=lambda x: x["entry"].get("timestamp", 0), reverse=True)
            keep = items[0]
            to_archive = items[1:]
            # 把旧记忆内容合并到最新条目的 content 中
            merged_lines = [keep["entry"].get("content", "")]
            for old in to_archive:
                old_content = old["entry"].get("content", "")
                if old_content and old_content not in merged_lines:
                    merged_lines.append(old_content)
            keep["entry"]["content"] = " | ".join(merged_lines)
            keep["entry"]["_merged_count"] = len(items)
            keep["entry"]["_merged_at"] = time.time()
            keep["path"].write_text(json.dumps(_clean_surrogates(keep["entry"]), ensure_ascii=False, indent=2))
            # 删除旧文件
            for old in to_archive:
                old["path"].unlink()
                self._index["memories"] = [x for x in self._index["memories"] if x["id"] != old["id"]]
            merged_count += len(to_archive)

        if merged_count > 0:
            self._save_index()
        return merged_count

    def store(self, content: str, context: str = "", source: str = "") -> str:
        """存储一条记忆，返回记忆 ID。
        
        v0.4 增强：
          - 写入前去重检查（关键词重叠 > dedup_ratio 则跳过）
          - 超长内容自动截断
          - 自动触发过期清理（每存储 10 条触发一次 _delete_expired）
        """
        # 去重检查
        dup = self._find_duplicate(content, context)
        if dup:
            # 覆盖旧条目的更新时间
            dup["entry"]["timestamp"] = time.time()
            dup["entry"]["content"] = content
            if context:
                dup["entry"]["context"] = context
            if source:
                dup["entry"]["source"] = source
            dup["file_path"].write_text(
                json.dumps(_clean_surrogates(dup["entry"]), ensure_ascii=False, indent=2)
            )
            # 更新索引中的时间戳
            for m in self._index["memories"]:
                if m["id"] == dup["id"]:
                    m["timestamp"] = time.time()
                    break
            self._save_index()
            return dup["id"] + "_dedup"

        # 截断过长的内容
        if len(content) > MAX_MEMORY_CHARS:
            content = content[:MAX_MEMORY_CHARS] + "..."

        self._index["last_id"] += 1
        mem_id = f"mem_{self._index['last_id']}"
        entry = {
            "id": mem_id,
            "content": content,
            "context": context,
            "source": source,
            "timestamp": time.time(),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ttl_days": self._ttl_days,
        }
        file_path = self.memory_dir / f"{mem_id}.json"
        file_path.write_text(
            json.dumps(_clean_surrogates(entry), ensure_ascii=False, indent=2)
        )
        # 更新索引
        self._index["memories"].append({
            "id": mem_id,
            "timestamp": entry["timestamp"],
            "summary": content[:80],
        })
        self._save_index()

        # 每 10 条新记忆触发一次过期清理
        if self._index["last_id"] % 10 == 0:
            expired = self._delete_expired()
            merged = self._merge_similar()
            if expired or merged:
                print(f"[Memory] 自动清理: {expired} 过期 + {merged} 合并")

        return mem_id

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """简单关键词搜索（FTS5 不可用时的 fallback）
        
        v0.4 增强：自动过滤过期记忆。
        """
        q = query.lower()
        results = []
        now = time.time()
        max_age = self._ttl_days * 86400
        # 反向遍历，最新的在前
        for m in reversed(self._index["memories"]):
            # 过滤过期
            ts = m.get("timestamp", 0)
            if now - ts > max_age:
                continue

            mem_id = m["id"]
            file_path = self.memory_dir / f"{mem_id}.json"
            if not file_path.exists():
                continue
            try:
                entry = json.loads(file_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            text = (entry.get("content", "") + " " + entry.get("context", "")).lower()
            if q in text:
                results.append({
                    "id": mem_id,
                    "key": entry.get("source", ""),
                    "content": entry.get("content", ""),
                    "context": entry.get("context", ""),
                    "source": entry.get("source", ""),
                    "created": entry.get("created", ""),
                    "score": 1.0,
                })
                if len(results) >= limit:
                    break
        return results

    def reflect(self, query: str) -> str:
        """基于存储的记忆做综合推理（file 模式下只是返回相关记忆）"""
        results = self.search(query, limit=3)
        if not results:
            return "没有找到相关记忆。"
        lines = [f"关于「{query}」找到 {len(results)} 条相关记忆："]
        for r in results:
            lines.append(f"\n- {r['content'][:200]}")
            if r.get("context"):
                lines.append(f"  (上下文: {r['context'][:100]})")
        return "\n".join(lines)

    def save_task(self, task_id: str, task_data: dict):
        """保存任务跟踪 JSON"""
        path = self.tasks_dir / f"{task_id}.json"
        path.write_text(json.dumps(task_data, ensure_ascii=False, indent=2))

    def load_task(self, task_id: str) -> Optional[dict]:
        path = self.tasks_dir / f"{task_id}.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def list_recent(self, limit: int = 10) -> list[dict]:
        """列出最近记忆（v0.4 自动过滤过期）"""
        recent = []
        now = time.time()
        max_age = self._ttl_days * 86400
        for m in reversed(self._index["memories"]):
            ts = m.get("timestamp", 0)
            if now - ts > max_age:
                continue
            file_path = self.memory_dir / f"{m['id']}.json"
            if file_path.exists():
                try:
                    recent.append(json.loads(file_path.read_text()))
                except (json.JSONDecodeError, OSError):
                    recent.append(m)
            else:
                recent.append(m)
            if len(recent) >= limit:
                break
        return recent

    def clear(self):
        """清除所有记忆（仅供测试用）"""
        for m in self._index["memories"]:
            fp = self.memory_dir / f"{m['id']}.json"
            if fp.exists():
                fp.unlink()
        self._index = {"memories": [], "last_id": 0}
        self._save_index()

    def maintenance(self) -> dict:
        """触发主动维护：过期清理 + 合并，返回操作统计。
        
        v0.4 新增：供 agent_loop 定时调用。
        """
        expired = self._delete_expired()
        merged = self._merge_similar()
        return {
            "expired": expired,
            "merged": merged,
            "total_remaining": len(self._index["memories"]),
        }

    # ── v0.4 新查询接口 ────────────────────────────────────────────

    def count(self) -> int:
        """返回当前有效记忆条数（过滤过期后）"""
        now = time.time()
        max_age = self._ttl_days * 86400
        return sum(1 for m in self._index["memories"] if now - m.get("timestamp", 0) <= max_age)

    def get_stats(self) -> dict:
        """返回记忆系统统计"""
        now = time.time()
        max_age = self._ttl_days * 86400
        total = len(self._index["memories"])
        expired = sum(1 for m in self._index["memories"] if now - m.get("timestamp", 0) > max_age)
        return {
            "total": total,
            "valid": total - expired,
            "expired": expired,
            "ttl_days": self._ttl_days,
            "dedup_ratio": self._dedup_ratio,
            "disk_path": str(self.memory_dir),
        }


# ── Hindsight 后端 ────────────────────────────────────────────────────

class HindsightMemoryBackend:
    """Hindsight Cloud API 记忆后端 — 语义搜索 + 知识图谱"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        bank_id: str = "kuafu",
        api_url: str = DEFAULT_HINDSIGHT_API_URL,
        timeout: int = 60,
    ):
        self.api_key = api_key or _get_env_or_dotenv("HINDSIGHT_API_KEY", "")
        self.bank_id = _get_env_or_dotenv("HINDSIGHT_BANK_ID", bank_id)
        self.api_url = _get_env_or_dotenv("HINDSIGHT_API_URL", api_url)
        self.timeout = timeout
        self._ready = bool(self.api_key)

    def _check_ready(self):
        if not self._ready:
            raise RuntimeError(
                "Hindsight API key 未配置。请设置 HINDSIGHT_API_KEY 环境变量，"
                "或使用 file 模式（KUAFU_MEMORY_MODE=file）。"
            )

    def store(self, content: str, context: str = "", source: str = "") -> str:
        """存记忆到 Hindsight"""
        self._check_ready()
        body = {
            "items": [{"content": content, "context": context}],
        }
        if source:
            body["items"][0]["metadata"] = {"source": source}
        try:
            resp = _hindsight_request(
                "POST",
                f"/v1/default/banks/{self.bank_id}/memories",
                body,
                api_key=self.api_key,
                base_url=self.api_url,
                timeout=self.timeout,
            )
            return resp.get("status", "ok")
        except RuntimeError as e:
            return f"hindsight_store_error: {e}"

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """语义搜索记忆"""
        self._check_ready()
        body = {
            "query": query,
            "budget": "mid",
            "max_tokens": 4096,
        }
        try:
            resp = _hindsight_request(
                "POST",
                f"/v1/default/banks/{self.bank_id}/memories/recall",
                body,
                api_key=self.api_key,
                base_url=self.api_url,
                timeout=self.timeout,
            )
            results = resp.get("results", [])
            return [
                {
                    "id": r.get("id", ""),
                    "content": r.get("text", ""),
                    "score": r.get("score", 0),
                }
                for r in results[:limit]
            ]
        except RuntimeError as e:
            return [{"content": f"hindsight_search_error: {e}", "score": 0}]

    def reflect(self, query: str) -> str:
        """综合推理"""
        self._check_ready()
        body = {
            "query": query,
            "budget": "low",
        }
        try:
            resp = _hindsight_request(
                "POST",
                f"/v1/default/banks/{self.bank_id}/reflect",
                body,
                api_key=self.api_key,
                base_url=self.api_url,
                timeout=self.timeout,
            )
            return resp.get("text", "没有相关记忆。")
        except RuntimeError as e:
            return f"hindsight_reflect_error: {e}"

    def store_batch(self, items: list[dict]) -> str:
        """批量存储"""
        self._check_ready()
        body = {"items": items}
        try:
            resp = _hindsight_request(
                "POST",
                f"/v1/default/banks/{self.bank_id}/memories",
                body,
                api_key=self.api_key,
                base_url=self.api_url,
                timeout=self.timeout,
            )
            return resp.get("status", "ok")
        except RuntimeError as e:
            return f"hindsight_batch_error: {e}"


# ── MemoryAPI 接口（统一入口） ────────────────────────────────────────

class MemoryAPI:
    """夸父记忆 API — 统一接口，自动选择后端

    用法：
        api = MemoryAPI()  # 自动检测模式
        api.store("用户喜欢简洁的回复")
        results = api.search("用户偏好")
        answer = api.reflect("我了解用户什么？")
    """

    def __init__(self, mode: Optional[str] = None, memory_dir: Optional[Path] = None):
        self._file_backend = FileMemoryBackend(memory_dir)
        self._hindsight_backend: Optional[HindsightMemoryBackend] = None

        mode = mode or _get_env_or_dotenv("KUAFU_MEMORY_MODE", "file")
        self._mode = mode.lower()

        if self._mode == "hindsight":
            try:
                self._hindsight_backend = HindsightMemoryBackend()
                if not self._hindsight_backend._ready:
                    print("[MemoryAPI] Hindsight API key 未配置，回退到 file 模式")
                    self._mode = "file"
            except Exception as e:
                print(f"[MemoryAPI] Hindsight 初始化失败: {e}，回退到 file 模式")
                self._mode = "file"

        # ── 持久化钩子（Hindsight 专用） ─────────────────────────────
        self._hindsight_remember = self._null_hook
        self._hindsight_recall = self._null_hook
        if self._mode == "hindsight":
            self._hindsight_remember = self._hindsight_remember_impl
            self._hindsight_recall = self._hindsight_recall_impl

    @property
    def mode(self) -> str:
        return self._mode

    @staticmethod
    def _null_hook(*args, **kwargs):
        return None

    def _hindsight_remember_impl(self, content: str, context: str = ""):
        """Hindsight 存储钩子，被 agent_loop 调用"""
        self._hindsight_backend.store(content, context=context)

    def _hindsight_recall_impl(self, query: str) -> list[dict]:
        """Hindsight 检索钩子"""
        return self._hindsight_backend.search(query)

    # ── 公开接口（统一存储/搜索/推理） ──────────────────────────────

    def store(self, content: str, context: str = "", source: str = "") -> str:
        """存储一条记忆"""
        return self._file_backend.store(content, context, source)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """搜索记忆（语义搜索或关键词搜索）"""
        return self._file_backend.search(query, limit)

    def reflect(self, query: str) -> str:
        """综合推理"""
        return self._file_backend.reflect(query)

    # ── 兼容别名（旧接口 remember/recall，供测试和 agent_loop 使用） ─

    def remember(self, key: str, content: str, tags: list = None) -> str:
        """兼容旧接口：remember(key, content, tags) -> store
        
        key 参数通过 source 传递给底层存储，以便后续检索能匹配到。
        """
        context = f"tags:{','.join(tags)}" if tags else ""
        return self.store(content, context=context, source=key)

    def recall(self, query: str, limit: int = 10) -> list[dict]:
        """兼容旧接口：recall(query) -> search"""
        return self.search(query, limit)

    def get_status(self) -> dict:
        """返回记忆系统状态"""
        stats = self._file_backend.get_stats()
        return {
            "mode": self._mode,
            "total": stats["valid"],
            "stats": stats,
        }

    def hindsight_store(self, content: str, context: str = "") -> None:
        """显式调用 Hindsight 存储（仅在 hindsight 模式下有效）"""
        self._hindsight_remember(content, context)

    def hindsight_recall(self, query: str) -> list[dict]:
        """显式调用 Hindsight 检索"""
        return self._hindsight_recall(query)

    def store_batch(self, items: list[dict]) -> str:
        if self._hindsight_backend and self._mode == "hindsight":
            return self._hindsight_backend.store_batch(items)
        results = []
        for item in items:
            mem_id = self._file_backend.store(
                item.get("content", ""),
                context=item.get("context", ""),
                source=item.get("source", ""),
            )
            results.append(mem_id)
        return json.dumps({"stored": len(results), "ids": results})

    # ── 文件后端子接口 ────────────────────────────────────────────────

    def save_task(self, task_id: str, task_data: dict):
        self._file_backend.save_task(task_id, task_data)

    def load_task(self, task_id: str) -> Optional[dict]:
        return self._file_backend.load_task(task_id)

    def list_recent(self, limit: int = 10) -> list[dict]:
        return self._file_backend.list_recent(limit)

    def clear(self):
        self._file_backend.clear()

    # ── v0.4 新增：记忆管理接口 ──────────────────────────────────────

    def maintenance(self) -> dict:
        """主动触发记忆维护（过期清理 + 合并）"""
        return self._file_backend.maintenance()

    def count(self) -> int:
        """当前有效记忆条数"""
        return self._file_backend.count()

    def get_stats(self) -> dict:
        """记忆系统完整统计"""
        stats = self._file_backend.get_stats()
        stats["mode"] = self._mode
        return stats

    # ── 工具模式（供 AgentLoop 识别 memory 相关工具） ────────────────

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "memory_store",
                "description": "存储一条记忆，供未来检索。适合记录用户偏好、项目决策、经验教训。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "记忆内容"},
                        "context": {"type": "string", "description": "上下文标签（可选）"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_search",
                "description": "搜索历史记忆。支持语义搜索（hindsight 模式）或关键词搜索（file 模式）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索查询"},
                        "limit": {"type": "integer", "description": "返回结果数上限", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_reflect",
                "description": "基于所有记忆做综合推理，回答需要跨记忆整合的问题。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "要推理的问题"},
                    },
                    "required": ["query"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict) -> str:
        """处理 AgentLoop 发起的记忆工具调用"""
        if tool_name == "memory_store":
            content = args.get("content", "")
            context = args.get("context", "")
            if not content:
                return json.dumps({"error": "content 不能为空"})
            self.store(content, context=context)
            return json.dumps({"result": "记忆已存储"})

        elif tool_name == "memory_search":
            query = args.get("query", "")
            limit = args.get("limit", 5)
            if not query:
                return json.dumps({"error": "query 不能为空"})
            results = self.search(query, limit=limit)
            if not results:
                return json.dumps({"result": "没有找到相关记忆。"})
            lines = [f"{i+1}. {r['content'][:200]}" for i, r in enumerate(results)]
            return json.dumps({"result": "\n".join(lines)})

        elif tool_name == "memory_reflect":
            query = args.get("query", "")
            if not query:
                return json.dumps({"error": "query 不能为空"})
            answer = self.reflect(query)
            return json.dumps({"result": answer})

        return json.dumps({"error": f"未知记忆工具: {tool_name}"})

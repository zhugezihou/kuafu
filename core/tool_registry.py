"""
夸父工具注册系统 (Tool Registry)

职责：
1. 工具定义注册（OpenAI Function Call 格式）
2. 工具执行分派
3. 提供可扩展的工具接口（新工具只需实现函数 + 注册 schema）

设计原则：
- 零新增依赖（仅标准库）
- 与现有 agent_loop.py 兼容（原有工具名不变）
- 支持动态注册/注销
"""

import json
import os
import re
import shlex
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Any, Callable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent


# ── 工具处理函数签名 ───────────────────────────────────────────────
# 每个工具是一个 (schema_dict, handler_function) 对
# handler_function(args: dict) -> dict 必须有 "success" 和 "output" 字段

ToolHandler = Callable[[dict], dict]


class ToolRegistry:
    """工具注册中心。管理所有可用工具的定义和执行。"""

    def __init__(self):
        self._schemas: list[dict] = []     # OpenAI Function Call 格式 schema
        self._handlers: dict[str, ToolHandler] = {}  # name -> handler
        self._register_core_tools()

    # ── 注册 API ───────────────────────────────────────────────────

    def register(self, name: str, schema: dict, handler: ToolHandler):
        """注册一个工具。

        Args:
            name: 工具名（用于 function_call）
            schema: OpenAI Function Call 格式的 schema dict（包含 description, parameters 等）
            handler: 处理函数，接受 args dict，返回 {"success": bool, "output": str, ...}
        """
        # 移除已存在的同名工具
        self._schemas = [s for s in self._schemas if s["function"]["name"] != name]
        full_schema = {
            "type": "function",
            "function": {
                "name": name,
                **schema,
            },
        }
        self._schemas.append(full_schema)
        self._handlers[name] = handler

    def unregister(self, name: str) -> bool:
        """注销一个工具。"""
        old_count = len(self._schemas)
        self._schemas = [s for s in self._schemas if s["function"]["name"] != name]
        self._handlers.pop(name, None)
        return len(self._schemas) < old_count

    def get_schemas(self) -> list[dict]:
        """获取全部工具定义（OpenAI Function Call 格式）。"""
        return list(self._schemas)

    def execute(self, tool_call: dict) -> dict:
        """执行一个工具调用。

        Args:
            tool_call: {
                "id": "...",
                "function": {"name": "...", "arguments": {...} | "{...}"}
            }

        Returns:
            {"success": bool, "output": str, ...}
        """
        fn_name = tool_call.get("function", {}).get("name", "")
        raw_args = tool_call.get("function", {}).get("arguments", {})

        # 如果 arguments 是字符串（JSON），解析它
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                args = {}
        else:
            args = raw_args if isinstance(raw_args, dict) else {}

        if fn_name not in self._handlers:
            return {"success": False, "output": f"未知工具: {fn_name}"}

        try:
            result = self._handlers[fn_name](args)
            # 保证返回格式
            if not isinstance(result, dict):
                return {"success": True, "output": str(result)}
            if "output" not in result:
                result["output"] = str(result.get("result", ""))
            return result
        except Exception as e:
            return {"success": False, "output": f"工具 {fn_name} 异常: {e}"}

    def get_handler(self, name: str) -> Optional[ToolHandler]:
        """获取指定工具的处理函数。"""
        return self._handlers.get(name)

    def list_tools(self) -> list[str]:
        """列出所有已注册的工具名。"""
        return [s["function"]["name"] for s in self._schemas]

    # ── 核心工具注册 ──────────────────────────────────────────────

    def _register_core_tools(self):
        """注册夸父的核心工具集。"""
        self.register("terminal", self._term_schema(), self._handle_terminal)
        self.register("read_file", self._read_schema(), self._handle_read_file)
        self.register("write_file", self._write_schema(), self._handle_write_file)
        self.register("patch", self._patch_schema(), self._handle_patch)
        self.register("search_files", self._search_schema(), self._handle_search_files)
        self.register("web_search", self._web_search_schema(), self._handle_web_search)
        self.register("web_fetch", self._web_fetch_schema(), self._handle_web_fetch)
        self.register("finish", self._finish_schema(), self._handle_finish)

    # ── Schema 定义 ────────────────────────────────────────────────

    @staticmethod
    def _term_schema() -> dict:
        return {
            "description": "在 Linux 终端中执行命令。可以运行 shell 命令、脚本、编译代码等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                    "workdir": {"type": "string", "description": "工作目录（绝对路径），默认为项目根目录"},
                    "timeout": {"type": "integer", "description": "超时时间（秒），默认 30"},
                },
                "required": ["command"],
            },
        }

    @staticmethod
    def _read_schema() -> dict:
        return {
            "description": "读取文件内容。返回带行号的文本。不支持二进制文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（绝对或相对）"},
                    "offset": {"type": "integer", "description": "起始行号（1-indexed），默认 1"},
                    "limit": {"type": "integer", "description": "最多返回行数，默认 500"},
                },
                "required": ["path"],
            },
        }

    @staticmethod
    def _write_schema() -> dict:
        return {
            "description": "写入文件。会完全覆盖已有内容。会检查 core/ 目录写保护。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（绝对或相对）"},
                    "content": {"type": "string", "description": "完整的文件内容"},
                },
                "required": ["path", "content"],
            },
        }

    @staticmethod
    def _patch_schema() -> dict:
        return {
            "description": "对文件进行精确的文本替换编辑。比 write_file 更适合修改已有文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "old_string": {"type": "string", "description": "要被替换的旧文本（必须是唯一的）"},
                    "new_string": {"type": "string", "description": "新的文本"},
                    "replace_all": {"type": "boolean", "description": "是否替换所有匹配（默认 false）"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        }

    @staticmethod
    def _search_schema() -> dict:
        return {
            "description": "搜索文件内容或查找文件名。用于代码搜索、文档检索等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "搜索模式。内容搜索用正则，文件搜索用 glob 模式"},
                    "target": {
                        "type": "string",
                        "enum": ["content", "files"],
                        "description": "'content' 搜索文件内容，'files' 查找文件名",
                    },
                    "path": {"type": "string", "description": "搜索路径"},
                    "file_glob": {"type": "string", "description": "文件筛选模式（如 *.py）"},
                },
                "required": ["pattern", "target"],
            },
        }

    @staticmethod
    def _web_search_schema() -> dict:
        return {
            "description": "搜索互联网。用于获取最新信息、调研项目、查找文档等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数，默认 5"},
                },
                "required": ["query"],
            },
        }

    @staticmethod
    def _web_fetch_schema() -> dict:
        return {
            "description": "抓取网页内容。用于阅读在线文档、文章、README 等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页 URL"},
                },
                "required": ["url"],
            },
        }

    @staticmethod
    def _finish_schema() -> dict:
        return {
            "description": "完成任务并返回最终结果。调用此工具表示任务已完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "最终结果摘要"},
                    "summary": {"type": "string", "description": "详细的任务完成报告"},
                },
                "required": ["result", "summary"],
            },
        }

    # ── 工具实现 ──────────────────────────────────────────────────

    # ---- terminal ----

    def _handle_terminal(self, args: dict) -> dict:
        command = args.get("command", "")
        workdir = args.get("workdir", str(ROOT_DIR))
        timeout = args.get("timeout", 30)

        if not command:
            return {"success": False, "output": "命令不能为空"}

        # 安全检查
        from core.sandbox import validate_command
        safe, risk, reason = validate_command(command)
        if not safe:
            return {"success": False, "output": f"安全拦截 [{risk}]: {reason}"}

        try:
            r = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=timeout,
            )
            output = r.stdout
            if r.stderr:
                output += "\n--- stderr ---\n" + r.stderr
            if output.strip():
                output = output[:3000]
                if len(output) >= 3000:
                    output += "\n... (输出已截断)"
            return {
                "success": r.returncode == 0,
                "output": output or "(无输出)",
                "exit_code": r.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "output": f"命令执行超时（{timeout}s）"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ---- read_file ----

    def _handle_read_file(self, args: dict) -> dict:
        path = Path(args.get("path", ""))
        if not path.is_absolute():
            path = ROOT_DIR / path
        if not path.exists():
            return {"success": False, "output": f"文件不存在: {path}"}
        try:
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")
            offset = args.get("offset", 1)
            limit = args.get("limit", 500)
            selected = lines[offset - 1 : offset - 1 + limit]
            output = "\n".join(
                f"{offset + i}|{line}" for i, line in enumerate(selected)
            )
            return {"success": True, "output": output, "total_lines": len(lines)}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ---- write_file ----

    def _handle_write_file(self, args: dict) -> dict:
        path = Path(args.get("path", ""))
        content = args.get("content", "")
        if not path.is_absolute():
            path = ROOT_DIR / path

        from core.sandbox import is_path_allowed_for_write
        allowed, reason = is_path_allowed_for_write(str(path))
        if not allowed:
            return {"success": False, "output": f"安全拦截: {reason}"}

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"success": True, "output": f"已写入 {path} ({len(content)} bytes)"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ---- patch ----

    def _handle_patch(self, args: dict) -> dict:
        path = Path(args.get("path", ""))
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        replace_all = args.get("replace_all", False)
        if not path.is_absolute():
            path = ROOT_DIR / path

        if not path.exists():
            return {"success": False, "output": f"文件不存在: {path}"}

        try:
            content = path.read_text(encoding="utf-8")
            if old not in content:
                return {"success": False, "output": f"未找到匹配文本: {old[:50]}..."}
            count = content.count(old)
            if count > 1 and not replace_all:
                return {"success": False, "output": f"匹配到 {count} 处，请使用更精确的文本"}
            new_content = content.replace(old, new, 1 if not replace_all else -1)
            path.write_text(new_content, encoding="utf-8")
            return {"success": True, "output": f"已替换 {'全部' if replace_all else '1'}处"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ---- search_files ----

    def _handle_search_files(self, args: dict) -> dict:
        pattern = args.get("pattern", "")
        target = args.get("target", "content")
        search_path = args.get("path", str(ROOT_DIR))
        file_glob = args.get("file_glob")

        if target == "content":
            cmd = ["rg", "-n", "--max-count", "5", pattern]
            if file_glob:
                cmd.extend(["-g", file_glob])
            cmd.append(search_path)
        else:
            cmd = ["find", search_path, "-name", pattern, "-type", "f"]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            output = r.stdout[:2000] or "(无匹配结果)"
            return {"success": True, "output": output}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ---- web_search ----

    def _handle_web_search(self, args: dict) -> dict:
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        if not query:
            return {"success": False, "output": "搜索词不能为空"}
        return self._search_duckduckgo(query, max_results)

    def _search_duckduckgo(self, query: str, max_results: int = 5) -> dict:
        """通过 DuckDuckGo Lite 搜索（免费，无需 API key）"""
        url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; KuafuSearch/1.0)",
                "Accept": "text/html",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return self._search_bing(query, max_results)

        # 解析 DDG 结果
        results = []
        link_pattern = re.compile(
            r'<a[^>]*class="result-link"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>',
            re.IGNORECASE,
        )
        snippet_pattern = re.compile(
            r'<td[^>]*class="result-snippet"[^>]*>([^<]*)</td>',
            re.IGNORECASE,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (href, title) in enumerate(links):
            if i >= max_results:
                break
            title = title.strip() or "(无标题)"
            snippet = snippets[i].strip() if i < len(snippets) else ""
            snippet = re.sub(r'<[^>]+>', ' ', snippet).strip()
            results.append({"title": title, "url": href, "snippet": snippet[:200]})

        # fallback: 通用链接提取
        if not results:
            all_links = re.findall(
                r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]*)</a>', html
            )
            seen = set()
            for href, title in all_links:
                if href not in seen and len(results) < max_results:
                    seen.add(href)
                    results.append({
                        "title": title.strip()[:100] or href[:60],
                        "url": href,
                        "snippet": "",
                    })

        if not results:
            return {"success": True, "output": f"搜索「{query}」未找到结果。"}

        lines = [f"搜索结果: 「{query}」", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r['snippet']:
                lines.append(f"   {r['snippet']}")
            lines.append("")
        return {"success": True, "output": "\n".join(lines).strip()}

    def _search_bing(self, query: str, max_results: int = 5) -> dict:
        """Bing 搜索作为 fallback"""
        url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return {"success": False, "output": f"搜索失败: {e}"}

        results = []
        for m in re.finditer(
            r'<h2><a[^>]*href="(https?://[^"]+)"[^>]*>([^<]*)</a>', html
        ):
            if len(results) >= max_results:
                break
            results.append({
                "title": m.group(2).strip(),
                "url": m.group(1),
                "snippet": "",
            })

        if not results:
            return {"success": True, "output": f"Bing 搜索「{query}」未找到结果。"}
        lines = [f"搜索结果 (Bing): 「{query}」", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}")
        return {"success": True, "output": "\n".join(lines).strip()}

    # ---- web_fetch ----

    def _handle_web_fetch(self, args: dict) -> dict:
        url = args.get("url", "")
        if not url:
            return {"success": False, "output": "URL 不能为空"}
        if not url.startswith(("http://", "https://")):
            return {"success": False, "output": "URL 必须以 http:// 或 https:// 开头"}

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                html = raw.decode(charset, errors="replace")
        except Exception as e:
            return {"success": False, "output": f"抓取失败: {e}"}

        text = self._clean_html(html)
        return {"success": True, "output": text}

    @staticmethod
    def _clean_html(html: str, max_length: int = 3000) -> str:
        """从 HTML 中提取纯文本（零依赖）"""
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else ""

        text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')

        if title:
            text = f"标题: {title}\n\n{text}"
        if len(text) > max_length:
            text = text[:max_length] + "\n\n...(内容已截断)"
        return text

    # ---- finish ----

    @staticmethod
    def _handle_finish(args: dict) -> dict:
        return {
            "success": True,
            "output": json.dumps(args, ensure_ascii=False),
            "result": args.get("result", ""),
            "summary": args.get("summary", ""),
        }

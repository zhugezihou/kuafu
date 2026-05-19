"""
夸父 Agent 执行循环 — LLM + 工具调用的核心引擎。

职责：
1. 组装完整的 system prompt（身份 + 规则 + 记忆 + 进化状态）
2. 调用 LLM 获取响应
3. 解析 tool_calls 并执行
4. 收集观察结果返回给 LLM
5. 任务完成后触发反思和进化

流程：
    [用户任务] → 组装 prompt → LLM 推理
        ├── 有 tool_calls → 执行工具 → 返回观察 → LLM 继续
        └── 无 tool_calls → 输出结果 → 反思 → 进化 → [完成]
"""

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core.identity import load_identity_statement
from core.llm import LLMClient
from core.memory_api import MemoryAPI
from core.evolution import EvolutionEngine

ROOT_DIR = Path(__file__).resolve().parent.parent

# ── Bing 搜索 fallback（当 DuckDuckGo 不可用时） ─────────────────


class _BingSearch:
    """Bing 搜索 fallback（urllib 实现，零依赖）"""

    @staticmethod
    def _search(query: str, max_results: int = 5) -> dict:
        import re
        import urllib.request
        import urllib.parse

        url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return {"success": False, "output": f"所有搜索后端均不可用: {e}"}

        results = []
        # Bing 搜索结果: <li class="b_algo"> ... <h2><a href="..." target="_blank">title</a></h2> ... <p>snippet</p>
        # 按 <li class="b_algo"> 分割
        blocks = re.split(r'<li[^>]*class="b_algo"[^>]*>', html)
        for block in blocks[1:]:  # 跳过第一个（匹配前的内容）
            if len(results) >= max_results:
                break
            # 提取链接
            link_m = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>', block)
            # 提取标题
            title_m = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.DOTALL)
            # 提取摘要
            snippet_m = re.search(
                r'<p[^>]*class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL
            )

            href = link_m.group(1) if link_m else ""
            title_raw = title_m.group(1) if title_m else ""
            title = re.sub(r'<[^>]+>', ' ', title_raw).strip()[:100] if title_raw else "(无标题)"
            snippet_raw = snippet_m.group(1) if snippet_m else ""
            snippet = re.sub(r'<[^>]+>', ' ', snippet_raw).strip()[:200] if snippet_raw else ""

            if href:
                results.append({
                    "title": title,
                    "url": href,
                    "snippet": snippet,
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


# ---- 工具定义（OpenAI Function Call 格式） ----

TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "在 Linux 终端中执行命令。可以运行 shell 命令、脚本、编译代码等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录（绝对路径），默认为项目根目录",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时时间（秒），默认 30",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容。返回带行号的文本。不支持二进制文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对或相对）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（1-indexed），默认 1",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回行数，默认 500",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件。会完全覆盖已有内容。会检查 core/ 目录写保护。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对或相对）",
                    },
                    "content": {
                        "type": "string",
                        "description": "完整的文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "对文件进行精确的文本替换编辑。比 write_file 更适合修改已有文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "要被替换的旧文本（必须是唯一的）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "新的文本",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "搜索文件内容或查找文件名。用于代码搜索、文档检索等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式。内容搜索用正则，文件搜索用 glob 模式",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["content", "files"],
                        "description": "'content' 搜索文件内容，'files' 查找文件名",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索路径",
                    },
                    "file_glob": {
                        "type": "string",
                        "description": "文件筛选模式（如 *.py）",
                    },
                },
                "required": ["pattern", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网。用于获取最新信息、调研项目、查找文档等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果数，默认 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页内容。用于阅读在线文档、文章、README 等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页 URL",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "完成任务并返回最终结果。调用此工具表示任务已完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "最终结果摘要",
                    },
                    "summary": {
                        "type": "string",
                        "description": "详细的任务完成报告",
                    },
                },
                "required": ["result", "summary"],
            },
        },
    },
]


class AgentLoop:
    """Agent 执行循环引擎。

    负责：
    - 组装 system prompt
    - 调用 LLM
    - 执行工具
    - 循环直到 finish
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        memory: Optional[MemoryAPI] = None,
        evolution: Optional[EvolutionEngine] = None,
        max_turns: int = 20,
        on_step: Optional[Callable[[str], None]] = None,
    ):
        self.llm = llm or LLMClient()
        self.memory = memory or MemoryAPI()
        self.evolution = evolution or EvolutionEngine()
        self.max_turns = max_turns
        self.on_step = on_step  # 实时回调：on_step("状态描述")

    def build_system_prompt(self) -> str:
        """组装完整的系统 prompt。"""
        parts = []

        # 1. 身份声明
        parts.append(load_identity_statement())
        parts.append("")

        # 2. 核心规则
        parts.append("## 核心规则")
        parts.append("- 你是夸父，一个自我进化的 AI agent")
        parts.append(f"- 用户是你的主人（在 IDENTITY.md 中定义）")
        parts.append("- 每次任务完成后，必须反思学到了什么")
        parts.append("- 如果用户纠正了你，记住这个教训")
        parts.append("- 绝对不可以修改 core/ 目录下的任何文件")
        parts.append("- 用中文思考和回复")
        parts.append("")

        # 3. 工具说明
        parts.append("## 可用工具")
        parts.append("你有以下工具可用，通过 function_call 调用：")
        for tool_def in TOOLS_DEFINITIONS:
            fn = tool_def["function"]
            desc = fn["description"].split("。")[0]
            parts.append(f"- {fn['name']}: {desc}")
        parts.append("")
        parts.append("完成任务后，调用 finish() 工具结束。")
        parts.append("")
        parts.append("## 输出格式")
        parts.append("- 你的回复是直接对用户说的话，不是系统日志或任务报告")
        parts.append("- 如果用户问问题，直接回答内容本身，不要说'已回答'、'已介绍'、'已完成'这类")
        parts.append("- 例如用户问'你能做什么'，你直接列出能力，而不是说'已介绍能力'")
        parts.append("")

        # 4. 进化状态
        stats = self.evolution.get_evolution_stats()
        parts.append("## 进化状态")
        parts.append(f"- 总进化次数: {stats['total_evolutions']}")
        parts.append(f"- 各级进化: {stats.get('by_level', {})}")
        task_stats = self.evolution.get_task_stats()
        parts.append(f"- 已完成任务: {task_stats['total']}")
        if task_stats["total"] > 0:
            parts.append(f"- 成功率: {task_stats['success_rate']}%")
        parts.append("")

        # 5. 历史记忆
        recent = self.memory.recall("", limit=10)
        if recent:
            parts.append("## 相关记忆")
            for m in recent[-5:]:
                parts.append(f"- {m.get('key', '?')}: {m.get('content', '')[:100]}")
            parts.append("")

        # 6. 用户偏好（从 memory/user_prefs.json 加载）
        prefs_path = Path(__file__).resolve().parent.parent / "memory" / "user_prefs.json"
        if prefs_path.exists():
            try:
                prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
                if prefs:
                    parts.append("## 用户偏好")
                    for k, v in prefs.items():
                        parts.append(f"- {k}: {v}")
                    parts.append("")
            except (json.JSONDecodeError, OSError):
                pass

        return "\n".join(parts)

    def _execute_tool(self, tool_call: dict) -> dict:
        """执行一个工具调用。"""
        fn_name = tool_call.get("function", {}).get("name", "")
        args = tool_call.get("function", {}).get("arguments", {})

        try:
            if fn_name == "terminal":
                return self._tool_terminal(args)
            elif fn_name == "read_file":
                return self._tool_read_file(args)
            elif fn_name == "write_file":
                return self._tool_write_file(args)
            elif fn_name == "patch":
                return self._tool_patch(args)
            elif fn_name == "search_files":
                return self._tool_search_files(args)
            elif fn_name == "web_search":
                return self._tool_web_search(args)
            elif fn_name == "web_fetch":
                return self._tool_web_fetch(args)
            elif fn_name == "finish":
                return {"success": True, "output": json.dumps(args, ensure_ascii=False)}
            else:
                return {"success": False, "output": f"未知工具: {fn_name}"}
        except Exception as e:
            return {"success": False, "output": f"工具执行异常: {e}"}

    def _tool_terminal(self, args: dict) -> dict:
        """执行终端命令。"""
        import subprocess
        command = args.get("command", "")
        workdir = args.get("workdir", str(ROOT_DIR))
        timeout = args.get("timeout", 30)

        if not command:
            return {"success": False, "output": "命令不能为空"}

        # 安全检查
        from core.sandbox import validate_command
        safe, risk, reason = validate_command(command)
        if not safe:
            return {"success": False, "output": f"安全拦截: {reason}"}

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
                output = output[:3000]  # 截断防止 context 溢出
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

    def _tool_read_file(self, args: dict) -> dict:
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

    def _tool_write_file(self, args: dict) -> dict:
        path = Path(args.get("path", ""))
        content = args.get("content", "")
        if not path.is_absolute():
            path = ROOT_DIR / path

        # 安全检查：禁止写入 core/
        from core.sandbox import is_path_allowed_for_write
        if not is_path_allowed_for_write(str(path)):
            return {"success": False, "output": f"安全拦截: {path} 在保护区内，禁止写入"}

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"success": True, "output": f"已写入 {path} ({len(content)} bytes)"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    def _tool_patch(self, args: dict) -> dict:
        """精确文本替换。"""
        path = Path(args.get("path", ""))
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        if not path.is_absolute():
            path = ROOT_DIR / path

        if not path.exists():
            return {"success": False, "output": f"文件不存在: {path}"}

        try:
            content = path.read_text(encoding="utf-8")
            if old not in content:
                return {"success": False, "output": f"未找到匹配文本: {old[:50]}..."}
            count = content.count(old)
            if count > 1 and not args.get("replace_all"):
                return {"success": False, "output": f"匹配到 {count} 处，请使用更精确的文本"}
            new_content = content.replace(old, new, 1 if not args.get("replace_all") else -1)
            path.write_text(new_content, encoding="utf-8")
            return {"success": True, "output": f"已替换 1 处"}
        except Exception as e:
            return {"success": False, "output": str(e)}

    def _tool_search_files(self, args: dict) -> dict:
        """搜索文件内容或文件名。"""
        pattern = args.get("pattern", "")
        target = args.get("target", "content")
        search_path = args.get("path", str(ROOT_DIR))
        file_glob = args.get("file_glob")

        import subprocess
        cmd_parts = []

        if target == "content":
            cmd_parts = ["rg", "-n", "--max-count", "5", pattern]
            if file_glob:
                cmd_parts.extend(["-g", file_glob])
            cmd_parts.append(search_path)
        else:
            cmd_parts = ["find", search_path, "-name", pattern, "-type", "f"]

        try:
            r = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=10)
            output = r.stdout[:2000] or "(无匹配结果)"
            return {"success": True, "output": output}
        except Exception as e:
            return {"success": False, "output": str(e)}

    # ── 网络工具 ──────────────────────────────────────────────────────

    @staticmethod
    def _clean_html(html: str, max_length: int = 3000) -> str:
        """从 HTML 中提取纯文本（简单实现，零依赖）"""
        import re
        # 提取 title
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else ""

        # 去除 style/script
        text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # 去除 HTML 标签
        text = re.sub(r'<[^>]+>', ' ', text)
        # 压缩空白
        text = re.sub(r'\s+', ' ', text).strip()
        # 解码 HTML 实体
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')

        if title:
            text = f"标题: {title}\n\n{text}"

        if len(text) > max_length:
            text = text[:max_length] + "\n\n...(内容已截断)"

        return text

    @staticmethod
    def _ddg_search(query: str, max_results: int = 5) -> dict:
        """通过 DuckDuckGo Lite 接口搜索（免费，无需 API key）"""
        import re
        import urllib.request
        import urllib.parse

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
        except Exception as e:
            # DDG 不可用时 fallback 到 Bing
            return _BingSearch._search(query, max_results)

        # 解析结果表格...
        results = []
        # 匹配所有链接行（结果在 class="result-link" 的表格行中）
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
            # 清理 snippet 中的 HTML
            snippet = re.sub(r'<[^>]+>', ' ', snippet).strip()
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet[:200],
            })

        if not results:
            # fallback: 尝试通用链接提取
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

    def _tool_web_search(self, args: dict) -> dict:
        """搜索互联网（DuckDuckGo Lite，免费无需 API key）"""
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        if not query:
            return {"success": False, "output": "搜索词不能为空"}
        return self._ddg_search(query, max_results)

    def _tool_web_fetch(self, args: dict) -> dict:
        """抓取网页内容并提取文本"""
        url = args.get("url", "")
        if not url:
            return {"success": False, "output": "URL 不能为空"}
        if not url.startswith(("http://", "https://")):
            return {"success": False, "output": "URL 必须以 http:// 或 https:// 开头"}

        import urllib.request
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
                # 尝试检测编码
                content_type = resp.headers.get("Content-Type", "")
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                html = raw.decode(charset, errors="replace")
        except Exception as e:
            return {"success": False, "output": f"抓取失败: {e}"}

        text = self._clean_html(html)
        return {"success": True, "output": text}

    def run(self, task: str) -> dict:
        """执行一次完整任务。

        Returns:
            {
                "success": bool,
                "result": str,
                "summary": str or None,
                "turns": int,
                "evolution": EvolutionEvent or None,
                "errors": list[str],
                "duration": float,
            }
        """
        start = time.time()
        errors = []
        messages = []
        turn_count = 0
        final_result = ""
        final_summary = ""

        # System prompt
        system_prompt = self.build_system_prompt()
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task})

        # 执行循环
        for turn in range(self.max_turns):
            turn_count = turn + 1

            # 通知：LLM 思考中
            if self.on_step:
                self.on_step(f"🤔 第 {turn_count}/{self.max_turns} 轮 — LLM 思考中...")

            # 调用 LLM
            response = self.llm.chat(messages, tools=TOOLS_DEFINITIONS)

            if not response["success"]:
                error_msg = response.get("error", "LLM 调用失败")
                errors.append(error_msg)
                break

            # 添加 assistant 消息
            assistant_msg = {"role": "assistant", "content": response["content"]}
            if response.get("tool_calls"):
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": tc["type"],
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": json.dumps(tc["function"]["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in response["tool_calls"]
                ]
            messages.append(assistant_msg)

            # 检查是否调用了 finish
            finish_called = False
            if response.get("tool_calls"):
                # 如果 LLM 在回复文本的同时还调用了 finish，取文本内容作为最终结果
                llm_content = response.get("content", "").strip()
                for tc in response["tool_calls"]:
                    if tc["function"]["name"] == "finish":
                        args = tc["function"]["arguments"]
                        # 优先用 LLM 的回复文本（如果写了具体内容）
                        if llm_content and llm_content != "":
                            final_result = llm_content
                            final_summary = args.get("summary", llm_content[:200])
                        else:
                            final_result = args.get("result", "")
                            final_summary = args.get("summary", "")
                        finish_called = True
                        break
                if finish_called:
                    break

            # 执行工具调用
            if response.get("tool_calls"):
                for tc in response["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    args = tc["function"]["arguments"]
                    # 跳过 finish（核心系统工具）
                    if fn_name != "finish":
                        # 通知：正在执行工具
                        arg_preview = json.dumps(args, ensure_ascii=False)[:60]
                        if self.on_step:
                            self.on_step(f"🔧 执行 {fn_name}({arg_preview}...)")

                    tool_result = self._execute_tool(tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(tool_result.get("output", "(无输出)")),
                    })
                    if not tool_result["success"]:
                        errors.append(
                            f"工具 {tc['function']['name']} 失败: {tool_result.get('output', '')}"
                        )
            else:
                # 没有 tool_calls 也没 finish，LLM 直接回复了文本
                # LLM 可能在做纯聊天/解释类回答，直接收下结果
                final_result = response["content"]
                final_summary = response["content"][:200]
                messages.append({
                    "role": "tool",
                    "tool_call_id": "auto-finish",
                    "content": json.dumps({"result": final_result, "summary": final_summary}, ensure_ascii=False),
                })
                break

        # 准备任务结果
        task_result = {
            "success": len(errors) == 0,
            "result": final_result or response.get("content", ""),
            "summary": final_summary,
            "errors": errors,
            "tool_calls": turn_count,
            "task_type": "generic",
            "duration": round(time.time() - start, 3),
        }

        # 反思：记录任务到记忆
        self.memory.remember(
            key=f"task:{time.strftime('%Y%m%d_%H%M%S')}",
            content=task_result["result"][:200],
            tags=["task", task_result["task_type"]],
        )

        # 自检：让 LLM 审视自己的输出，判断是否有问题
        self._self_check(task_result, messages, start)

        # 进化评估
        evolution_event = self.evolution.evaluate_and_evolve(task_result)
        if evolution_event:
            task_result["evolution"] = evolution_event

        task_result["turns"] = turn_count
        task_result["messages_count"] = len(messages)
        return task_result

    def _self_check(self, task_result: dict, messages: list, start: float) -> None:
        """任务完成后自检：让 LLM 审视自己的输出是否犯错了。

        如果发现明显错误，追加 self_correction 到 result 中。
        不额外消耗太多 token，只做快速检查。
        """
        result_text = task_result.get("result", "")
        if not result_text:
            return

        # 只对写了代码/文件的任务做自检（纯回答不浪费 token）
        tool_names = [m.get("tool_calls", [{}])[0].get("function", {}).get("name", "")
                      if m.get("tool_calls") else "" for m in messages]
        has_code_work = any("write_file" in str(t) or "patch" in str(t) or "terminal" in str(t) for t in tool_names)
        if not has_code_work:
            return

        if self.on_step:
            self.on_step("🔍 自检中 — 审视输出是否有问题...")

        check_prompt = (
            "你刚才完成了一个任务。请快速检查你的最终输出，指出是否有以下问题：\n\n"
            "1. 代码有语法错误或明显逻辑错误？\n"
            "2. 生成的文件路径/位置有问题？\n"
            "3. 输出中的代码无法直接运行？\n"
            "4. 运行产生了错误——你修复了还是只报告了？如果只报告没修复，算有问题。\n\n"
            f"你的最终输出:\n```\n{result_text[:1500]}\n```\n\n"
            "如果存在明显问题，先描述问题，再给出修正方案。\n"
            "如果完全没有问题（代码正确、错误已修复），只回复「无问题」三个字。"
        )
        check_msg = [
            {"role": "system", "content": "你是夸父自检器。只检查输出的正确性，不要做无关分析。"},
            {"role": "user", "content": check_prompt},
        ]
        try:
            check_resp = self.llm.chat(check_msg, tools=None)
            if check_resp["success"]:
                feedback = check_resp["content"].strip()
                if feedback != "无问题" and len(feedback) > 10:
                    task_result["self_check"] = feedback
                    task_result["result"] += f"\n\n---\n🔍 自检反馈:\n{feedback}"
                    if self.on_step:
                        self.on_step(f"⚠️ 自检发现问题: {feedback[:120]}...")
                else:
                    if self.on_step:
                        self.on_step("✅ 自检无问题")
        except Exception as e:
            if self.on_step:
                self.on_step(f"⚠️ 自检异常: {e}")

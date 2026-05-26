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

from core.safety import CommandLevel, SafetyLayer
from core.approval import ApprovalManager

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
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
        self.register("github_search", self._github_search_schema(), self._handle_github_search)
        self.register("github_get_repo", self._github_get_repo_schema(), self._handle_github_get_repo)
        self.register("tavily_search", self._tavily_search_schema(), self._handle_tavily_search)
        self.register("finish", self._finish_schema(), self._handle_finish)

        # Microcompact: 读取已存储的工具完整结果
        self.register("read_tool_result", self._read_tool_result_schema(), self._handle_read_tool_result)

        # 白板模式工具
        self.register("finish_step", self._finish_step_schema(), self._handle_finish_step)
        self.register("whiteboard_read", self._whiteboard_read_schema(), self._handle_whiteboard_read)
        self.register("whiteboard_write", self._whiteboard_write_schema(), self._handle_whiteboard_write)

    # ── Schema 定义 ────────────────────────────────────────────────

    @staticmethod
    def _term_schema() -> dict:
        return {
            "description": "执行 shell 命令（终端），返回命令的输出和状态码",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录（可选，默认项目根目录）",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数（可选，默认 30）",
                    },
                },
                "required": ["command"],
            },
        }

    @staticmethod
    def _read_schema() -> dict:
        return {
            "description": "读取文件内容（支持分页）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（可选，默认 1）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多读取行数（可选，默认 200）",
                    },
                },
                "required": ["path"],
            },
        }

    @staticmethod
    def _write_schema() -> dict:
        return {
            "description": "写入文件内容（覆盖模式）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容",
                    },
                },
                "required": ["path", "content"],
            },
        }

    @staticmethod
    def _patch_schema() -> dict:
        return {
            "description": "对文件执行精确的查找替换编辑",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "要查找的原文（必须有唯一匹配）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "替换后的内容",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        }

    @staticmethod
    def _search_schema() -> dict:
        return {
            "description": "在项目中搜索文件内容或文件名",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式（正则表达式）",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["content", "files"],
                        "description": "搜索目标：'content' 搜索内容，'files' 搜索文件名",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索路径（可选，默认项目根目录）",
                    },
                },
                "required": ["pattern"],
            },
        }

    @staticmethod
    def _web_search_schema() -> dict:
        return {
            "description": "在互联网上搜索信息，返回搜索结果列表（标题 + URL + 摘要）",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 5，最大 10）",
                    },
                },
                "required": ["query"],
            },
        }

    @staticmethod
    def _web_fetch_schema() -> dict:
        return {
            "description": "抓取并提取网页的纯文本内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网页 URL",
                    },
                },
                "required": ["url"],
            },
        }

    @staticmethod
    def _finish_schema() -> dict:
        return {
            "description": "完成任务并返回最终结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "任务结果总结",
                    },
                    "summary": {
                        "type": "string",
                        "description": "详细摘要",
                    },
                },
                "required": ["result"],
            },
        }

    @staticmethod
    def _read_tool_result_schema() -> dict:
        return {
            "description": "读取 Microcompact 已存储到磁盘的工具完整结果。当你在上下文中看到 '[工具结果已存储]' 占位时，调用此工具获取完整内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "完整路径（来自占位中的 '完整路径:' 字段）",
                    },
                },
                "required": ["file_path"],
            },
        }

    # ── 工具实现 ──────────────────────────────────────────────────

    # ---- terminal ----

    @staticmethod
    def _build_env() -> dict:
        """构建干净的运行环境变量（脱敏 API key）"""
        env = dict(os.environ)
        # 脱敏敏感变量
        for key in list(env.keys()):
            if any(k in key.lower() for k in ["api_key", "api_secret", "token", "password", "secret"]):
                env[key] = "***"
        return env

    def _handle_terminal(self, args: dict) -> dict:
        command = args.get("command", "")
        workdir = args.get("workdir", str(ROOT_DIR))
        timeout = args.get("timeout", 30)

        if not command.strip():
            return {"success": False, "output": "命令不能为空"}

        # ── 集成 SafetyLayer 安全分级 + 拒绝跟踪 ──
        level, risk_name, reason = SafetyLayer.classify_command(command)

        # 检查拒绝跟踪的自动决策
        need_ask, decision = SafetyLayer.needs_approval_with_denial(level, command)
        if need_ask:
            # 需要询问用户 → 发起审批
            approved = ApprovalManager.terminal_prompt(
                title=f"执行命令",
                detail=(
                    f"命令: `{SafetyLayer.sanitize_command(command)}`\n"
                    f"风险等级: {level}\n"
                    f"风险: {risk_name}\n"
                    f"原因: {reason}\n"
                ),
                risk="high" if level == CommandLevel.DANGEROUS else "medium",
            )
            if approved:
                SafetyLayer.report_approval(command)
            else:
                # 用户拒绝了 → 记录到 DenialTracker
                SafetyLayer.report_denial(command)
                return {"success": False, "output": f"操作已被用户拒绝: {reason}"}
        elif decision == "block":
            # 拒绝跟踪自动阻止
            return {"success": False, "output": f"操作被安全策略自动阻止（已学习用户频繁拒绝此类命令）: {reason}"}
        elif decision == "allow" and level in (CommandLevel.ATTENTION, CommandLevel.DANGEROUS):
            # 拒绝跟踪自动放行（已学习信任）— 打印日志但执行
            print(f"🔓 [DenialTracker] 自动放行 {level} 级命令（已学习用户信任）: {SafetyLayer.sanitize_command(command)}")

        # ── 执行命令 ──
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
                env=self._build_env(),
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr[:2000]}"
            if result.returncode != 0:
                output += f"\n[退出码: {result.returncode}]"
            # 限制输出大小
            if len(output) > 5000:
                output = output[:5000] + "\n\n...(输出已截断)"
            return {
                "success": result.returncode == 0,
                "output": output.strip() or "(无输出)",
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "output": f"命令执行超时 ({timeout}s)"}
        except Exception as e:
            return {"success": False, "output": f"命令执行失败: {e}"}

    # ---- read_file ----

    def _handle_read_file(self, args: dict) -> dict:
        path = args.get("path", "")
        offset = args.get("offset", 1)
        limit = args.get("limit", 200)

        if not path:
            return {"success": False, "output": "路径不能为空"}

        # 路径解析
        p = Path(path)
        if not p.is_absolute():
            p = ROOT_DIR / p

        if not p.exists():
            return {"success": False, "output": f"文件不存在: {p}"}
        if not p.is_file():
            return {"success": False, "output": f"不是文件: {p}"}

        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            start = max(0, offset - 1)
            end = min(total, start + limit)
            content_lines = lines[start:end]
            output = "\n".join(
                f"{i + 1:6d}|{l}"
                for i, l in enumerate(content_lines, start=start + 1)
            )
            if total > end:
                output += f"\n...(共 {total} 行，显示 {start + 1}-{end})"
            return {"success": True, "output": output, "total_lines": total}
        except Exception as e:
            return {"success": False, "output": f"读取失败: {e}"}

    # ---- write_file ----

    def _handle_write_file(self, args: dict) -> dict:
        path = args.get("path", "")
        content = args.get("content", "")

        if not path:
            return {"success": False, "output": "路径不能为空"}

        p = Path(path)
        if not p.is_absolute():
            p = ROOT_DIR / p

        # 安全检查：不能写入 core/ 目录（只读保护区）
        core_path = (ROOT_DIR / "core").resolve()
        try:
            p_resolved = p.resolve()
            if str(p_resolved).startswith(str(core_path)):
                return {"success": False, "output": f"禁止写入 core/ 只读保护区: {p}"}
        except (OSError, ValueError):
            pass

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"success": True, "output": f"已写入 {len(content)} 字符到 {p}"}
        except Exception as e:
            return {"success": False, "output": f"写入失败: {e}"}

    # ---- patch ----

    def _handle_patch(self, args: dict) -> dict:
        path = args.get("path", "")
        old_str = args.get("old_string", "")
        new_str = args.get("new_string", "")

        if not path or not old_str:
            return {"success": False, "output": "path 和 old_string 不能为空"}

        p = Path(path)
        if not p.is_absolute():
            p = ROOT_DIR / p

        if not p.exists():
            return {"success": False, "output": f"文件不存在: {p}"}

        try:
            text = p.read_text(encoding="utf-8")
            if old_str not in text:
                return {"success": False, "output": f"在文件中未找到匹配内容: {old_str[:50]}"}
            new_text = text.replace(old_str, new_str, 1)
            p.write_text(new_text, encoding="utf-8")
            return {"success": True, "output": f"补丁已应用: {p}"}
        except Exception as e:
            return {"success": False, "output": f"补丁失败: {e}"}

    # ---- search_files ----

    def _handle_search_files(self, args: dict) -> dict:
        pattern = args.get("pattern", "")
        target = args.get("target", "content")
        path = args.get("path", str(ROOT_DIR))

        if not pattern:
            return {"success": False, "output": "搜索模式不能为空"}

        try:
            search_path = Path(path)
            if not search_path.is_absolute():
                search_path = ROOT_DIR / path

            results = []

            if target == "files":
                # 文件名搜索（glob 模式）
                for f in search_path.rglob(pattern):
                    try:
                        rel = f.relative_to(ROOT_DIR)
                        results.append(str(rel))
                    except ValueError:
                        results.append(str(f))
                    if len(results) >= 50:
                        break
            else:
                # 内容搜索（递归文件 + 正则）
                py_files = [f for f in search_path.rglob("*") if f.is_file()]
                for f in py_files[:200]:  # 限制文件数
                    try:
                        text = f.read_text(encoding="utf-8", errors="replace")
                        for i, line in enumerate(text.splitlines(), 1):
                            if re.search(pattern, line, re.IGNORECASE):
                                try:
                                    rel = f.relative_to(ROOT_DIR)
                                except ValueError:
                                    rel = f
                                results.append(f"{rel}:{i}: {line.strip()[:120]}")
                                if len(results) >= 30:
                                    break
                    except Exception:
                        continue
                    if len(results) >= 30:
                        break

            if not results:
                return {"success": True, "output": "未找到匹配结果。"}
            return {
                "success": True,
                "output": "\n".join(results),
                "count": len(results),
            }
        except Exception as e:
            return {"success": False, "output": f"搜索失败: {e}"}

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
        """Bing 搜索作为 fallback（多模式解析）"""
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

        # 多模式解析 Bing HTML（现代版：<li class="b_algo"><h2><a> + caption）
        results = []

        # 主模式：Bing 现代搜索结果（b_algo 容器）
        b_algo_blocks = re.findall(
            r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>(.*?)</li>',
            html, re.DOTALL | re.IGNORECASE
        )
        for block in b_algo_blocks:
            if len(results) >= max_results:
                break
            link_m = re.search(
                r'<h2[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                block, re.DOTALL
            )
            if not link_m:
                continue
            url = link_m.group(1).strip()
            title = re.sub(r'<[^>]+>', '', link_m.group(2)).strip()
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            snippet = re.sub(r'<[^>]+>', ' ', snippet_m.group(1)).strip() if snippet_m else ""
            snippet = snippet[:200]
            if "bing.com" not in url and url.startswith("http"):
                results.append({"title": title[:100] or url[:60], "url": url, "snippet": snippet})

        # 备用模式：旧版/特殊情况
        if not results:
            backup_patterns = [
                r'<h2[^>]*>.*?<a[^>]*href="(https?://(?!.*bing\.com)[^"]+)"[^>]*>(.*?)</a>',
                r'<a[^>]*href="(https?://(?!www\.bing\.com|r\.bing\.com)[^"]+)"[^>]*>(.*?)</a>',
            ]
            for pattern in backup_patterns:
                for m in re.finditer(pattern, html, re.DOTALL):
                    if len(results) >= max_results:
                        break
                    url = m.group(1).strip()
                    title = re.sub(r'<[^>]+>', '', m.group(2)).strip()[:100]
                    if "bing.com" not in url and url.startswith("http"):
                        results.append({"title": title or url[:60], "url": url, "snippet": ""})
                if results:
                    break

        if not results:
            return {"success": True, "output": f"Bing 搜索「{query}」未找到结果。"}
        lines = [f"搜索结果 (Bing): 「{query}」", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['url']}")
            if r.get('snippet'):
                lines.append(f"   {r['snippet']}")
            lines.append("")
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

    # ---- finish_step ----

    @staticmethod
    def _finish_step_schema() -> dict:
        return {
            "description": "【白板模式】标记当前步骤完成，输出结果和摘要。在 WhiteboardExecutor 的分步执行中使用，表示当前步骤执行完毕",
            "parameters": {
                "type": "object",
                "properties": {
                    "output": {
                        "type": "string",
                        "description": "当前步骤的输出结果",
                    },
                    "summary": {
                        "type": "string",
                        "description": "当前步骤的简要摘要（用于日志和检查点）",
                    },
                },
                "required": ["output", "summary"],
            },
        }

    def _handle_finish_step(self, args: dict) -> dict:
        return {
            "success": True,
            "output": args.get("output", ""),
            "summary": args.get("summary", ""),
        }

    # ---- whiteboard_read ----

    @staticmethod
    def _whiteboard_read_schema() -> dict:
        return {
            "description": "【白板模式】读取外部白板中指定分区的内容。分区包括：current_state（当前执行状态）、completed（已完成步骤）、next_plan（待执行计划）、intermediate（中间结果库）。每次只读取一个分区，内容不消耗 LLM 上下文窗口",
            "parameters": {
                "type": "object",
                "properties": {
                    "partition": {
                        "type": "string",
                        "enum": ["current_state", "completed", "next_plan", "intermediate"],
                        "description": "要读取的白板分区名",
                    },
                },
                "required": ["partition"],
            },
        }

    def _handle_whiteboard_read(self, args: dict) -> dict:
        partition = args.get("partition", "")
        try:
            from core.whiteboard import Whiteboard
            wb = Whiteboard()
            data = wb.read(partition)
            if not data:
                return {"success": True, "output": f"白板分区「{partition}」为空"}
            output = json.dumps(data, ensure_ascii=False, indent=2)
            return {"success": True, "output": output}
        except Exception as e:
            return {"success": False, "output": f"读取白板失败: {e}"}

    # ---- whiteboard_write ----

    @staticmethod
    def _whiteboard_write_schema() -> dict:
        return {
            "description": "【白板模式】向外部白板的指定分区写入一条记录。分区包括：current_state（当前状态）、completed（已完成步骤）、next_plan（计划）、intermediate（中间结果）、excluded_paths（已排除路径）、hypotheses（待验证假设）。内容不消耗 LLM 上下文窗口",
            "parameters": {
                "type": "object",
                "properties": {
                    "partition": {
                        "type": "string",
                        "enum": ["current_state", "completed", "next_plan", "intermediate", "excluded_paths", "hypotheses"],
                        "description": "目标分区名",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容（建议简洁，关键信息在前200字符）",
                    },
                    "key": {
                        "type": "string",
                        "description": "可选的内容标识键（用于后续检索）",
                    },
                },
                "required": ["partition", "content"],
            },
        }

    def _handle_whiteboard_write(self, args: dict) -> dict:
        partition = args.get("partition", "")
        content = args.get("content", "")
        key = args.get("key", "")
        try:
            from core.whiteboard import Whiteboard
            wb = Whiteboard()
            entry = {"content": content}
            if key:
                entry["key"] = key
            wb.append(partition, entry)
            return {"success": True, "output": f"已写入白板分区「{partition}」"}
        except Exception as e:
            return {"success": False, "output": f"写入白板失败: {e}"}

    # ---- github_search ----

    @staticmethod
    def _github_search_schema() -> dict:
        return {
            "description": "搜索 GitHub 上的仓库、代码或 issue。返回仓库名、描述、星数、URL。注意：GitHub API 有 60 次/小时的速率限制",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "GitHub 搜索关键词，如 'AI agent framework' 或 'langchain python'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 5，最大 10）",
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["repositories", "code"],
                        "description": "搜索类型：repositories（仓库）或 code（代码）。默认 repositories",
                    },
                },
                "required": ["query"],
            },
        }

    def _handle_github_search(self, args: dict) -> dict:
        query = args.get("query", "")
        max_results = min(args.get("max_results", 5), 10)
        search_type = args.get("search_type", "repositories")
        if not query:
            return {"success": False, "output": "搜索词不能为空"}

        import urllib.parse
        encoded_q = urllib.parse.quote(query)
        url = f"https://api.github.com/search/{search_type}?q={encoded_q}&per_page={max_results}&sort=stars"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Kuafu/1.0",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"success": False, "output": f"GitHub 搜索失败: {e}"}

        items = data.get("items", [])
        if not items:
            return {"success": True, "output": f"GitHub 搜索「{query}」未找到结果。"}

        lines = [f"GitHub 搜索结果 ({search_type}): 「{query}」", ""]
        for i, item in enumerate(items[:max_results], 1):
            if search_type == "repositories":
                name = item.get("full_name", "?")
                desc = item.get("description", "") or "(无描述)"
                stars = item.get("stargazers_count", 0)
                lang = item.get("language", "") or "?"
                url_repo = item.get("html_url", "")
                lines.append(f"{i}. {name}")
                lines.append(f"   ⭐{stars}  |  {lang}")
                lines.append(f"   {desc}")
                lines.append(f"   {url_repo}")
            else:
                # code search
                repo = item.get("repository", {}).get("full_name", "?")
                path = item.get("path", "?")
                html_url = item.get("html_url", "")
                lines.append(f"{i}. {repo}: {path}")
                lines.append(f"   {html_url}")
            lines.append("")

        lines.append("---")
        total = data.get("total_count", 0)
        lines.append(f"共 {total} 条结果，显示前 {len(items)} 条")
        lines.append("提示：用 github_get_repo(tool) 查看某个仓库的详细信息和 README")
        return {"success": True, "output": "\n".join(lines).strip()}

    # ---- github_get_repo ----

    @staticmethod
    def _github_get_repo_schema() -> dict:
        return {
            "description": "获取 GitHub 仓库的详细信息，包括描述、星数、语言、许可协议和 README 内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "仓库名，格式为 'owner/repo'，如 'nousresearch/hermes-agent'",
                    },
                    "get_readme": {
                        "type": "boolean",
                        "description": "是否获取 README 内容（默认 true）",
                    },
                },
                "required": ["repo"],
            },
        }

    def _handle_github_get_repo(self, args: dict) -> dict:
        repo = args.get("repo", "")
        get_readme = args.get("get_readme", True)
        if not repo or "/" not in repo:
            return {"success": False, "output": "仓库名格式错误，应为 'owner/repo'，如 'nousresearch/hermes-agent'"}

        try:
            # 获取仓库信息
            url = f"https://api.github.com/repos/{repo}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Kuafu/1.0",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            lines = [f"📦 {data.get('full_name', repo)}", ""]
            desc = data.get("description") or "(无描述)"
            lines.append(f"描述: {desc}")
            lines.append(f"⭐ Stars: {data.get('stargazers_count', 0)}")
            lines.append(f"🍴 Forks: {data.get('forks_count', 0)}")
            lines.append(f"🐛 Issues: {data.get('open_issues_count', 0)}")
            lines.append(f"📋 语言: {data.get('language', '?') or '?'}")
            lines.append(f"📜 许可: {data.get('license', {}).get('spdx_id', '无') if data.get('license') else '无'}")
            lines.append(f"🔗 {data.get('html_url', '')}")
            if data.get("homepage"):
                lines.append(f"🌐 主页: {data['homepage']}")
            lines.append(f"📅 创建: {data.get('created_at', '?')[:10]}")
            lines.append(f"🔄 更新: {data.get('updated_at', '?')[:10]}")
            lines.append("")

            # 获取 README
            if get_readme:
                readme_url = f"https://api.github.com/repos/{repo}/readme"
                req2 = urllib.request.Request(
                    readme_url,
                    headers={
                        "User-Agent": "Kuafu/1.0",
                        "Accept": "application/vnd.github.v3.raw",
                    },
                )
                try:
                    with urllib.request.urlopen(req2, timeout=15) as resp2:
                        readme_text = resp2.read().decode("utf-8", errors="replace")
                    # 截取 README 关键部分
                    if len(readme_text) > 2000:
                        readme_text = readme_text[:2000] + "\n\n...(README 已截断)"
                    lines.append("📖 README:")
                    lines.append(readme_text)
                except Exception:
                    lines.append("(README 不可用)")

            return {"success": True, "output": "\n".join(lines).strip()}

        except urllib.error.HTTPError as e:
            if e.code == 403:
                return {"success": False, "output": "GitHub API 速率限制已满（60次/小时），请稍后再试。"}
            elif e.code == 404:
                return {"success": False, "output": f"仓库 '{repo}' 不存在。"}
            return {"success": False, "output": f"GitHub API 错误 ({e.code}): {e.reason}"}
        except Exception as e:
            return {"success": False, "output": f"获取仓库信息失败: {e}"}

    # ---- tavily_search ----

    @staticmethod
    def _tavily_search_schema() -> dict:
        return {
            "description": "使用 Tavily AI 搜索引擎搜索互联网。返回高质量的结果，包含标题、URL 和内容摘要。比 web_search 更稳定可靠，支持深度搜索",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 5，最大 10）",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                        "description": "搜索深度：basic（快速）或 advanced（深度，更耗时但更全面）",
                    },
                },
                "required": ["query"],
            },
        }

    def _handle_tavily_search(self, args: dict) -> dict:
        query = args.get("query", "")
        max_results = min(args.get("max_results", 5), 10)
        depth = args.get("depth", "basic")
        if not query:
            return {"success": False, "output": "搜索词不能为空"}
        if not TAVILY_API_KEY:
            return {"success": False, "output": "Tavily API key 未配置。请在 .env 中添加 TAVILY_API_KEY，或使用 web_search 代替"}

        payload = json.dumps({
            "query": query,
            "search_depth": depth,
            "max_results": max_results,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {TAVILY_API_KEY}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            return {"success": False, "output": f"Tavily 搜索失败 (HTTP {e.code}): {body}"}
        except Exception as e:
            return {"success": False, "output": f"Tavily 搜索失败: {e}"}

        results = data.get("results", [])
        if not results:
            return {"success": True, "output": f"Tavily 搜索「{query}」未找到结果。"}

        lines = [f"搜索结果 (Tavily): 「{query}」", ""]
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "(无标题)")
            url = r.get("url", "")
            content = r.get("content", "")[:300]
            lines.append(f"{i}. {title}")
            lines.append(f"   {url}")
            if content:
                lines.append(f"   {content}")
            lines.append("")

        if data.get("answer"):
            lines.append(f"📋 总结: {data['answer']}")
            lines.append("")

        total = data.get("total_results", len(results))
        lines.append(f"--- 共 {total} 条结果，显示前 {len(results)} 条 ---")
        return {"success": True, "output": "\n".join(lines).strip()}

    # ---- read_tool_result (Microcompact) ----

    def _handle_read_tool_result(self, args: dict) -> dict:
        """读取 Microcompact 存储的完整工具结果。"""
        file_path = args.get("file_path", "")
        if not file_path:
            return {"success": False, "output": "file_path 不能为空"}
        try:
            from core.context_compress import ToolResultStore
            result = ToolResultStore.load(file_path)
            if result is None:
                return {"success": False, "output": f"工具结果文件不存在或已过期: {file_path}"}
            return {"success": True, "output": result}
        except Exception as e:
            return {"success": False, "output": f"读取工具结果失败: {e}"}

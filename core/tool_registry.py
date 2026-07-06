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
#
# Copyright (c) 2026 zhugezihou
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


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
    """工具注册中心。管理所有可用工具的定义和执行。

    三级工具架构：
    - 核心工具（core）：如 terminal、finish — 始终以全量 schema 暴露给 LLM
    - 紧凑工具（compact）：如 read_file、write_file、patch 等 — 仅名称+描述在提示词中，
      不占用 tools 参数；当 LLM 首次调用时自动提升为全量 schema 并注入
    - 延迟工具（deferred）：如 web_search、github — 通过 ToolSearch 元工具发现后注入
    """

    def __init__(self):
        self._schemas: list[dict] = []     # 核心工具 schema（始终对 LLM 可见）
        self._handlers: dict[str, ToolHandler] = {}  # 所有工具的 handler（core + compact + deferred）
        self._compact: list[dict] = []     # 紧凑工具完整 schema（不暴露给 LLM，调用时自动提升）
        self._deferred: list[dict] = []     # 延迟加载工具定义（含 keywords 用于搜索）
        self._injected_tools: list[dict] = []  # 当前 session 已注入的完整 schema（compact 提升 + 延迟发现）
        self._deferred_call_count: dict[str, int] = {}  # 延迟工具调用计数，用于热提升
        self._register_core_tools()

    # ── 注册 API ───────────────────────────────────────────────────

    def register(self, name: str, schema: dict, handler: ToolHandler):
        """注册核心工具（始终以全量 schema 对 LLM 可见）。

        这三个注册方式的区别：
        - register() 全量 schema 始终出现在 tools 参数中，LLM 每轮都看到
        - register_compact() 仅名称+描述出现在提示词中，首次调用后自动提升
        - register_deferred() 完全隐藏，通过 ToolSearch 发现后注入
        """
        # 移除已存在的同名工具（所有池）
        self._schemas = [s for s in self._schemas if s["function"]["name"] != name]
        self._deferred = [s for s in self._deferred if s.get("schema", {}).get("function", {}).get("name") != name]
        self._injected_tools = [s for s in self._injected_tools
                                if s["function"]["name"] != name]
        full_schema = {
            "type": "function",
            "function": {
                "name": name,
                **schema,
            },
        }
        self._schemas.append(full_schema)
        self._handlers[name] = handler

    def register_deferred(self, name: str, schema: dict, handler: ToolHandler,
                          keywords: list[str] = None):
        """注册一个延迟加载工具。

        该工具不会出现在 LLM 的默认工具列表中。
        LLM 需要通过 ToolSearch 元工具搜索关键词来激活它。

        Args:
            name: 工具名
            schema: OpenAI Function Call 格式 schema
            handler: 处理函数
            keywords: 搜索关键词列表（如 ["web", "search", "google", "duckduckgo"]）
                      用于 ToolSearch 的模糊匹配
        """
        # 从核心工具移除（如果已存在）
        self._schemas = [s for s in self._schemas if s["function"]["name"] != name]
        self._injected_tools = [s for s in self._injected_tools
                                if s["function"]["name"] != name]

        full_schema = {
            "type": "function",
            "function": {
                "name": name,
                **schema,
            },
        }
        self._deferred.append({
            "schema": full_schema,
            "keywords": [kw.lower() for kw in (keywords or [])],
            "description": schema.get("description", ""),
        })
        self._handlers[name] = handler

    def register_compact(self, name: str, schema: dict, handler: ToolHandler):
        """注册一个紧凑工具。

        紧凑工具不会占用 LLM 调用的 tools 参数（节省 token）。
        其名称和一行描述会出现在 system prompt 中。
        当 LLM 首次调用该工具时，系统自动注入其完整 schema（self-promote）。

        Args:
            name: 工具名
            schema: OpenAI Function Call 格式完整 schema
            handler: 处理函数
        """
        # 从所有池中移除
        self._schemas = [s for s in self._schemas if s["function"]["name"] != name]
        self._injected_tools = [s for s in self._injected_tools
                                if s["function"]["name"] != name]
        self._deferred = [s for s in self._deferred
                          if s["schema"]["function"]["name"] != name]

        full_schema = {
            "type": "function",
            "function": {
                "name": name,
                **schema,
            },
        }
        self._compact.append(full_schema)
        self._handlers[name] = handler

    def unregister(self, name: str) -> bool:
        """注销一个工具。"""
        old_count = len(self._schemas) + len(self._compact) + len(self._injected_tools)
        self._schemas = [s for s in self._schemas if s["function"]["name"] != name]
        self._compact = [s for s in self._compact if s["function"]["name"] != name]
        self._injected_tools = [s for s in self._injected_tools
                                if s["function"]["name"] != name]
        self._handlers.pop(name, None)
        return (len(self._schemas) + len(self._compact) + len(self._injected_tools)) < old_count

    def get_schemas(self) -> list[dict]:
        """获取所有对 LLM 可见的工具 schema。

        只包含：核心工具（始终全量）+ 已注入工具（compact 提升 + ToolSearch 延迟发现）。
        紧凑工具不在此列——它们在 system prompt 中只有一行描述。
        """
        core = list(self._schemas)
        injected = list(self._injected_tools)
        return core + injected

    def get_active_tools_names(self) -> list[str]:
        """获取所有对 LLM 可见的工具名列表（用于日志/诊断）。"""
        names = [s["function"]["name"] for s in self._schemas]
        names += [s["function"]["name"] for s in self._injected_tools]
        return names

    def get_compact_tools_description(self) -> list[tuple[str, str]]:
        """获取紧凑工具的名称和描述列表（用于 system prompt 文本描述）。

        Returns:
            [(name, description), ...]
        """
        return [(s["function"]["name"], s["function"].get("description", ""))
                for s in self._compact]

    def _promote_compact_tool(self, name: str) -> bool:
        """将紧凑工具提升为注入工具（首次调用后自动触发）。

        返回 True 如果是首次提升（compact → injected）。
        """
        for s in self._compact:
            if s["function"]["name"] == name:
                # 检查是否已注入
                if any(t["function"]["name"] == name for t in self._injected_tools):
                    return False
                self._injected_tools.append(s)
                return True
        return False

    def inject_tool(self, name: str) -> bool:
        """将一个延迟加载工具注入到当前 session 的可见工具列表中。

        LLM 下次调用时就能看到并调用这个工具。
        如果工具不在延迟池中，返回 False。
        """
        for entry in self._deferred:
            if entry["schema"]["function"]["name"] == name:
                # 已注入则跳过
                if any(s["function"]["name"] == name for s in self._injected_tools):
                    return True
                self._injected_tools.append(entry["schema"])
                return True
        return False

    def _search_deferred_tools(self, query: str, max_results: int = 5) -> list[dict]:
        """在延迟工具池中搜索匹配 query 的工具。

        匹配策略（按优先级）：
        1. 工具名包含 query 中的词 → 最高分
        2. keywords 包含 query 中的词 → 中等分
        3. description 包含 query 中的词 → 低分

        Returns:
            [{"name": str, "description": str, "keywords": list[str], "score": int}, ...]
        """
        # 分词：空格/逗号分隔 + 中文连续文本按2-4字滑动窗口分词 + 英文子串提取
        raw_words = [w.lower() for w in re.split(r"[,\s]+", query) if len(w) > 1]
        query_words = list(raw_words)
        for rw in raw_words:
            if all('\u4e00' <= c <= '\u9fff' for c in rw):
                for length in [2, 3, 4]:
                    for i in range(len(rw) - length + 1):
                        seg = rw[i:i+length]
                        if seg not in query_words:
                            query_words.append(seg)
            else:
                # 混合词（如 "github仓库"）：提取其中连续英文字母子串
                eng_segs = set(re.findall(r'[a-z]{3,}', rw))
                for seg in eng_segs:
                    if seg not in query_words:
                        query_words.append(seg)

        if not query_words:
            return []

        scored = []
        for entry in self._deferred:
            name = entry["schema"]["function"]["name"]
            desc = entry["description"].lower()
            kws = entry["keywords"]
            score = 0
            for qw in query_words:
                if qw in name.lower():
                    score += 10
                if qw in kws:
                    score += 5
                if qw in desc:
                    score += 1
            if score > 0:
                scored.append({
                    "name": name,
                    "description": entry["description"],
                    "keywords": kws,
                    "score": score,
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:max_results]

    @staticmethod
    def _tool_search_schema() -> dict:
        return {
            "description": "搜索额外工具。如果你觉得当前工具不够用，用这个搜索更多可用的隐藏工具。"
                           "输入自然语言描述你想要的功能，系统会匹配并激活最相关的工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "描述你想要的功能，如 '搜索互联网'、'搜索GitHub仓库'、'抓取网页内容'",
                    },
                },
                "required": ["query"],
            },
        }

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

        # 紧凑工具自动提升：首次调用时注入完整 schema（下轮 LLM 调用就能看到参数）
        promoted = self._promote_compact_tool(fn_name)

        # ── 延迟工具热提升：同一 Session 内调用 ≥3 次 → 自动升为 compact ──
        if fn_name in self._handlers and fn_name not in self._schemas:
            # 检查是否是一个延迟工具（不在 _schemas 也不在 _compact）
            is_deferred = any(
                e["schema"]["function"]["name"] == fn_name
                for e in self._deferred
            )
            if is_deferred:
                count = self._deferred_call_count.get(fn_name, 0) + 1
                self._deferred_call_count[fn_name] = count
                if count >= 3:
                    # 热提升：向 compact 池加入 schema，保留 deferred 池中记录
                    # 注意：不从 _deferred 移除，这样 ToolSearch 元工具仍能发现它
                    schema_entry = None
                    for e in self._deferred:
                        if e["schema"]["function"]["name"] == fn_name:
                            schema_entry = e["schema"]
                            break
                    if schema_entry:
                        # 避免重复加入 compact 池
                        if not any(s["function"]["name"] == fn_name for s in self._compact):
                            self._compact.append(schema_entry)
                        # 同时将其注入到当前会话
                        if not any(s["function"]["name"] == fn_name for s in self._injected_tools):
                            self._injected_tools.append(schema_entry)
                        print(f"[ToolRegistry] 🔥 热提升延迟工具: {fn_name}（调用 {count} 次，升为 compact）")

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
        """注册夸父的核心工具集 + 紧凑工具 + 延迟加载工具。"""
        # ── L0 核心工具（始终全量 schema 对 LLM 可见）──
        self.register("terminal", self._term_schema(), self._handle_terminal)
        self.register("finish", self._finish_schema(), self._handle_finish)
        self.register("send_file_to_user", self._send_file_schema(), self._handle_send_file)

        # ── L1 紧凑工具（仅名称+描述在提示词中，首次调用后自动提升）──
        self.register_compact("read_file", self._read_schema(), self._handle_read_file)
        self.register_compact("write_file", self._write_schema(), self._handle_write_file)
        self.register_compact("patch", self._patch_schema(), self._handle_patch)
        self.register_compact("search_files", self._search_schema(), self._handle_search_files)

        # 白板模式工具
        self.register_compact("finish_step", self._finish_step_schema(), self._handle_finish_step)
        self.register_compact("whiteboard_read", self._whiteboard_read_schema(), self._handle_whiteboard_read)
        self.register_compact("whiteboard_write", self._whiteboard_write_schema(), self._handle_whiteboard_write)

        # Microcompact: 读取已存储的工具完整结果
        self.register_compact("read_tool_result", self._read_tool_result_schema(), self._handle_read_tool_result)

        # ── 元工具 ──
        # ToolSearch: LLM 通过它发现延迟加载工具
        self._register_tool_search()

        # ── 延迟加载工具（对 LLM 隐藏，通过 ToolSearch 发现） ──
        self.register_deferred("web_search", self._web_search_schema(), self._handle_web_search,
                               keywords=["web", "search", "internet", "google", "bing", "baidu",
                                         "duckduckgo", "网页搜索", "互联网"])
        self.register_deferred("web_fetch", self._web_fetch_schema(), self._handle_web_fetch,
                               keywords=["web", "fetch", "crawl", "scrape", "extract", "url",
                                         "网页抓取", "爬取", "提取网页", "http"])
        self.register_deferred("tavily_search", self._tavily_search_schema(), self._handle_tavily_search,
                               keywords=["tavily", "deep search", "research", "ai search",
                                         "深度搜索", "研究"])
        self.register_deferred("github_search", self._github_search_schema(), self._handle_github_search,
                               keywords=["github", "git", "code search", "repository",
                                         "开源仓库", "代码搜索"])
        self.register_deferred("github_get_repo", self._github_get_repo_schema(), self._handle_github_get_repo,
                               keywords=["github", "git", "repository", "repo info",
                                         "仓库信息", "repo详情"])

        # ── 多媒体工具（deferred） ──
        # 注意：DeepSeek 本身不支持图像生成/语音识别，但这些工具调用外部 API 完成
        self.register_deferred("image_gen", self._image_gen_schema(), self._handle_image_gen,
                               keywords=["image", "generate", "draw", "picture", "photo", "illustration",
                                         "图像", "图片", "生成图片", "画图", "插图", "创作", "设计"])
        self.register_deferred("vision_analyze", self._vision_schema(), self._handle_vision_analyze,
                               keywords=["vision", "image", "picture", "photo", "recognize", "ocr",
                                         "视觉", "图像识别", "图片分析", "识别图片", "看图", "理解图像"])
        self.register_deferred("text_to_speech", self._tts_schema(), self._handle_tts,
                               keywords=["tts", "speech", "voice", "audio", "朗读", "语音", "播报",
                                         "听", "发音", "语音合成"])
        self.register_deferred("speech_to_text", self._stt_schema(), self._handle_stt,
                               keywords=["stt", "speech", "voice", "transcribe", "whisper",
                                         "语音识别", "听写", "转录", "音频转文字"])

        # ── 高级搜索工具（deferred） ──
        self.register_deferred("aggregate_search", self._aggregate_search_schema(), self._handle_aggregate_search,
                               keywords=["search", "aggregate", "deep search", "multi engine", "research",
                                         "聚合搜索", "深度搜索", "综合搜索", "多引擎搜索", "研究"])

        # ── 下载工具（deferred） ──
        self.register_deferred("download_file", self._download_schema(), self._handle_download,
                               keywords=["download", "file", "url", "wget", "curl", "aria2",
                                         "下载文件", "下载", "下载链接"])

        # ── 浏览器工具（deferred） ──
        self.register_deferred("browser_navigate", self._browser_nav_schema(), self._handle_browser_navigate,
                               keywords=["browser", "web", "page", "url", "navigate", "open page",
                                         "浏览器", "打开网页", "浏览", "网页"])
        self.register_deferred("browser_snapshot", self._browser_snap_schema(), self._handle_browser_snapshot,
                               keywords=["browser", "snapshot", "refresh", "page", "reload",
                                         "浏览器快照", "刷新页面", "获取页面"])
        self.register_deferred("browser_click", self._browser_click_schema(), self._handle_browser_click,
                               keywords=["browser", "click", "tap", "select", "press",
                                         "点击", "选择", "单击"])
        self.register_deferred("browser_type", self._browser_type_schema(), self._handle_browser_type,
                               keywords=["browser", "type", "input", "fill", "search",
                                         "输入", "填写", "搜索框"])
        self.register_deferred("browser_screenshot", self._browser_screenshot_schema(), self._handle_browser_screenshot,
                               keywords=["browser", "screenshot", "capture", "screen",
                                         "截图", "浏览器截图", "页面截图"])
        self.register_deferred("browser_js", self._browser_js_schema(), self._handle_browser_js,
                               keywords=["browser", "javascript", "js", "eval", "execute",
                                         "执行JS", "浏览器脚本", "eval"])

    def _register_tool_search(self):
        """注册 ToolSearch 元工具（始终对 LLM 可见的隐藏工具发现入口）。"""
        schema = self._tool_search_schema()
        full_schema = {
            "type": "function",
            "function": {
                "name": "tool_search",
                **schema,
            },
        }
        self._schemas.append(full_schema)
        self._handlers["tool_search"] = self._handle_tool_search

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
            "description": "读取文件内容（支持分页）。参数: path(必需), offset(可选,默认1), limit(可选,默认200)",
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
            "description": "写入文件内容（覆盖模式）。参数: path(必需), content(必需)",
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
            "description": "对文件执行精确的查找替换编辑。参数: path(必需), old_string(必需), new_string(必需)",
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
            "description": "在项目中搜索文件内容或文件名。参数: pattern(必需), target(可选,'content'或'files'), path(可选)",
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
    def _send_file_schema() -> dict:
        return {
            "description": "发送一个已存在的文件给用户（自动选择用户当前使用的通道发送文件本体）。用户要求发给我、传文件时用此工具。文件必须已存在于磁盘上。系统会自动识别当前触发通道，也可通过 platform 参数显式指定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件的绝对路径（如 /home/asus/kuafu/xxx.docx）",
                    },
                    "description": {
                        "type": "string",
                        "description": "对文件的文字说明（可选）",
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["wechat", "feishu"],
                        "description": "指定发送通道（可选。不填则自动识别用户当前使用的通道）",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "飞书目标群聊 ID（可选。仅飞书通道需要时指定，不填则用当前聊天）",
                    },
                },
                "required": ["file_path"],
            },
        }

    @staticmethod
    def _finish_schema() -> dict:
        return {
            "description": "完成任务并返回最终结果。如果生成了需要发给用户的文件，用 send_files 参数列出文件路径，系统会自动发送。",
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
                    "send_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要发送给用户的文件路径列表（可选）。系统会自动通过微信/飞书发送文件本体。",
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

    def _handle_terminal(self, args: dict) -> dict:  # pragma: no cover
        command = args.get("command", "")
        # Desktop 模式下默认 workdir 为用户目录
        if os.environ.get("KUAFU_DESKTOP") == "1":
            default_dir = os.environ.get("USERPROFILE", "C:\\Users\\Default")
        else:
            default_dir = str(ROOT_DIR)
        workdir = args.get("workdir", default_dir)
        timeout = args.get("timeout", 30)

        if not command.strip():
            return {"success": False, "output": "命令不能为空"}

        # ── 跨平台命令翻译（Desktop/Windows 模式） ──
        from core.cross_platform import Platform
        if Platform.is_windows() or Platform.is_desktop():
            translated = Platform.translate_command(command)
            if translated != command:
                print(f"[Terminal] 命令已翻译: {command[:80]} → {translated[:80]}")
                command = translated

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

    def _handle_read_file(self, args: dict) -> dict:  # pragma: no cover
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

    def _handle_write_file(self, args: dict) -> dict:  # pragma: no cover
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

    def _handle_patch(self, args: dict) -> dict:  # pragma: no cover
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

    def _handle_search_files(self, args: dict) -> dict:  # pragma: no cover
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

    def _handle_web_search(self, args: dict) -> dict:  # pragma: no cover
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        if not query:
            return {"success": False, "output": "搜索词不能为空"}
        return self._search_duckduckgo(query, max_results)

    def _search_duckduckgo(self, query: str, max_results: int = 5) -> dict:  # pragma: no cover
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

    def _search_bing(self, query: str, max_results: int = 5) -> dict:  # pragma: no cover
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

    def _handle_web_fetch(self, args: dict) -> dict:  # pragma: no cover
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
        result = args.get("result", "")
        summary = args.get("summary", "")
        send_files = args.get("send_files", [])
        output_parts = [json.dumps({"result": result, "summary": summary}, ensure_ascii=False)]
        
        if send_files:
            sent = []
            for fp in send_files:
                path = Path(fp).expanduser().resolve()
                if path.exists() and path.is_file():
                    sent.append(str(path))
                else:
                    output_parts.append(f"文件不存在: {fp}")
            if sent:
                output_parts.append(f"待发送文件: {'、'.join(sent)}（gateway 层发送）")
        
        return {
            "success": True,
            "output": "\n".join(output_parts),
            "result": result,
            "summary": summary,
            "_send_files": send_files,  # 供 AgentLoop 检测后发送
        }

    # ---- send_file_to_user ----

    @staticmethod
    def _send_via_wechat(file_path: str) -> dict:
        """通过微信 iLink 发送文件。"""
        try:
            from core.channel.wechat_ilink import WeChatILinkChannel
            wc = WeChatILinkChannel()
            if not wc._bot_token:
                return {"success": False, "output": "微信未登录"}
            state_file = wc._state_file
            last_chat_id = ""
            last_ctx_token = ""
            if state_file and state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                last_chat_id = state.get("last_chat_id", "")
                last_ctx_token = state.get("last_context_token", "")
            if not last_chat_id:
                last_chat_id = wc._bot_open_id
            if not last_chat_id:
                return {"success": False, "output": "微信无可用会话"}

            p = Path(file_path).expanduser().resolve()
            if not p.exists() or not p.is_file():
                return {"success": False, "output": f"文件不存在: {file_path}"}
            result = wc.send_file(str(p), last_chat_id, last_ctx_token)
            if result.success:
                return {"success": True, "output": f"文件已通过微信发送: {p.name}"}
            return {"success": False, "output": result.error or "微信发送失败"}
        except Exception as e:
            return {"success": False, "output": f"微信发送异常: {e}"}

    @staticmethod
    def _send_via_feishu(file_path: str, chat_id: str = "", description: str = "") -> dict:
        """通过飞书 API 上传并发送文件到指定会话。"""
        app_id = os.environ.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        target_chat_id = chat_id or os.environ.get("FEISHU_CHAT_ID", "oc_d860f9f653e3421db6ea419a81414cf6")
        if not app_id or not app_secret:
            return {"success": False, "output": "飞书未配置"}

        import urllib.request
        token_body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
        token_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=token_body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
        )
        try:
            with urllib.request.urlopen(token_req, timeout=10) as r:
                td = json.loads(r.read().decode())
                token = td.get("tenant_access_token", "")
                if not token:
                    return {"success": False, "output": f"飞书token获取失败: {td.get('msg','')}"}
        except Exception as e:
            return {"success": False, "output": f"飞书token请求失败: {e}"}

        p = Path(file_path).expanduser().resolve()
        if not p.exists() or not p.is_file():
            return {"success": False, "output": f"文件不存在: {file_path}"}
        file_bytes = p.read_bytes()
        import uuid
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
        
        # 构造 multipart body（飞书要求：file_type + file_name 文本 + file 二进制）
        body_parts = []
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file_type\"\r\n\r\nstream\r\n".encode("utf-8"))
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file_name\"\r\n\r\n{p.name}\r\n".encode("utf-8"))
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{p.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
        body_parts.append(file_bytes)
        body_parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(body_parts)
        upload_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/files", data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
        )
        try:
            with urllib.request.urlopen(upload_req, timeout=30) as r:
                ur_data = json.loads(r.read().decode())
                if ur_data.get("code") != 0:
                    return {"success": False, "output": f"飞书上传失败: {ur_data.get('msg','')}"}
                file_key = ur_data.get("data", {}).get("file_key", "")
        except Exception as e:
            return {"success": False, "output": f"飞书上传请求失败: {e}"}

        msg_body = json.dumps({"receive_id": target_chat_id, "msg_type": "file",
            "content": json.dumps({"file_key": file_key, "file_name": p.name}, ensure_ascii=False)}, ensure_ascii=False).encode("utf-8")
        send_req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id", data=msg_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}, method="POST",
        )
        try:
            with urllib.request.urlopen(send_req, timeout=15) as r:
                sd = json.loads(r.read().decode())
                if sd.get("code") != 0:
                    return {"success": False, "output": f"飞书发送失败: {sd.get('msg','')}"}
                msg_id = sd.get("data", {}).get("message_id", "")
        except Exception as e:
            return {"success": False, "output": f"飞书发送请求失败: {e}"}

        return {"success": True, "output": f"文件已通过飞书发送: {p.name} (msg_id={msg_id})"}

    def _handle_send_file(self, args: dict) -> dict:
        """上传并发送文件到用户的当前触发通道。

        根据 KUAFU_CURRENT_PLATFORM 环境变量或 args.platform 确定发送通道。
        不再微信优先飞书 fallback —— 只发送到触发通道。
        """
        file_path = args.get("file_path", "").strip()
        if not file_path:
            return {"success": False, "output": "请指定文件路径"}
        p = Path(file_path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "output": f"文件不存在: {file_path}"}
        if not p.is_file():
            return {"success": False, "output": f"路径不是文件: {file_path}"}

        # 确定目标通道：优先 args 中显式指定的 platform，其次环境变量
        platform = args.get("platform", "").strip() or os.environ.get("KUAFU_CURRENT_PLATFORM", "")
        if not platform:
            return {"success": False, "output": "无法确定发送通道：未设置 KUAFU_CURRENT_PLATFORM"}
        description = args.get("description", "")

        if platform == "wechat":
            result = ToolRegistry._send_via_wechat(file_path)
            if result["success"]:
                return result
            # 微信不支持 bot 发送文件本体，直接返回明确提示
            return {"success": False, "output": "微信通道不支持 bot 发送文件，请通过飞书访问或直接在服务器上处理"}
        elif platform == "feishu":
            chat_id = args.get("chat_id", "").strip() or os.environ.get("KUAFU_CURRENT_CHAT_ID", "")
            result = ToolRegistry._send_via_feishu(file_path, chat_id=chat_id, description=description)
            if result["success"]:
                return result
            return {"success": False, "output": f"飞书发送失败: {result.get('output','')}"}
        else:
            # 未知通道，依次尝试
            wechat_result = ToolRegistry._send_via_wechat(file_path)
            if wechat_result["success"]:
                return wechat_result
            feishu_result = ToolRegistry._send_via_feishu(file_path, description=description)
            if feishu_result["success"]:
                return feishu_result
            return {"success": False,
                    "output": f"无法发送文件: 微信({wechat_result.get('output','')}) 飞书({feishu_result.get('output','')})"}

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

    def _handle_whiteboard_read(self, args: dict) -> dict:  # pragma: no cover
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

    def _handle_whiteboard_write(self, args: dict) -> dict:  # pragma: no cover
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

    def _handle_github_search(self, args: dict) -> dict:  # pragma: no cover
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
                        "description": "仓库名，格式为 'owner/repo'，如 'nousresearch/夸父-agent'",
                    },
                    "get_readme": {
                        "type": "boolean",
                        "description": "是否获取 README 内容（默认 true）",
                    },
                },
                "required": ["repo"],
            },
        }

    def _handle_github_get_repo(self, args: dict) -> dict:  # pragma: no cover
        repo = args.get("repo", "")
        get_readme = args.get("get_readme", True)
        if not repo or "/" not in repo:
            return {"success": False, "output": "仓库名格式错误，应为 'owner/repo'，如 'nousresearch/夸父-agent'"}

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

    def _handle_tavily_search(self, args: dict) -> dict:  # pragma: no cover
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

    def _handle_read_tool_result(self, args: dict) -> dict:  # pragma: no cover
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

    # ---- tool_search (Deferred Tool Loading) ----

    def _handle_tool_search(self, args: dict) -> dict:
        """ToolSearch 元工具 handler：搜索延迟加载工具并注入到当前 session。"""
        query = args.get("query", "")
        if not query:
            return {"success": False, "output": "query 不能为空"}

        results = self._search_deferred_tools(query)
        if not results:
            return {
                "success": True,
                "output": f"未找到与 '{query}' 匹配的隐藏工具。"
                          f"请尝试其他关键词。当前可用核心工具：{', '.join(self.get_active_tools_names())}",
            }

        # 自动将找到的工具注入到当前 session
        injected_names = []
        for tool in results:
            if self.inject_tool(tool["name"]):
                injected_names.append(tool["name"])

        output_lines = [
            f"🔍 已找到并激活以下隐藏工具（输入 '{query}'）：",
        ]
        for tool in results:
            output_lines.append(f"  • {tool['name']}: {tool['description']}")
        if injected_names:
            output_lines.append("")
            output_lines.append(f"已注入当前 session: {', '.join(injected_names)}")
            output_lines.append("你现在可以直接调用这些工具了。")

        return {"success": True, "output": "\n".join(output_lines)}

    # ═══════════════════════════════════════════════════════════════
    # 多媒体工具
    # ═══════════════════════════════════════════════════════════════

    # ── image_gen ──────────────────────────────────────────────

    @staticmethod
    def _image_gen_schema() -> dict:
        return {
            "description": "根据文字描述生成图像。支持多种模型和风格。需要 IMAGE_GEN_API_URL 环境变量配置 API 端点（如 SiliconFlow 或本地 ComfyUI）",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "图像描述文字，越详细效果越好",
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "负面提示词（可选），描述不希望出现的内容",
                    },
                    "size": {
                        "type": "string",
                        "enum": ["1024x1024", "1024x768", "768x1024", "512x512"],
                        "description": "图像尺寸（默认 1024x1024）",
                    },
                    "model": {
                        "type": "string",
                        "description": "模型名称（可选，默认使用 API 默认模型）",
                    },
                },
                "required": ["prompt"],
            },
        }

    def _handle_image_gen(self, args: dict) -> dict:  # pragma: no cover
        """图像生成 handler — 调用 SiliconFlow / 兼容 API 生成图像。"""
        prompt = args.get("prompt", "")
        if not prompt:
            return {"success": False, "output": "prompt 不能为空"}

        negative = args.get("negative_prompt", "")
        size = args.get("size", "1024x1024")
        model = args.get("model", "")

        api_url = os.environ.get("IMAGE_GEN_API_URL", "")
        api_key = os.environ.get("IMAGE_GEN_API_KEY", "")

        if not api_url:
            return {"success": False, "output": "未配置图像生成 API。请设置 IMAGE_GEN_API_URL 环境变量。"}

        try:
            payload = {
                "prompt": prompt,
                "size": size,
            }
            if negative:
                payload["negative_prompt"] = negative
            if model:
                payload["model"] = model

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # 尝试提取图像 URL（兼容多种 API 格式）
            image_url = ""
            if isinstance(data, dict):
                image_url = (
                    data.get("data", [{}])[0].get("url", "")
                    or data.get("images", [{}])[0].get("url", "")
                    or data.get("output", [{}])[0] if isinstance(data.get("output"), list) else ""
                    or data.get("result", "")
                    or data.get("url", "")
                )

            if image_url:
                return {
                    "success": True,
                    "output": f"✅ 图像已生成！\n{prompt}\n\n图片 URL: {image_url}\n\n（你可以通过浏览器打开该 URL 查看或下载图片。如果需要保存到本地，请告知。）",
                }
            else:
                preview = json.dumps(data, ensure_ascii=False)[:500]
                return {"success": True, "output": f"API 已响应，但未识别到图片 URL。原始响应:\n{preview}"}

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            return {"success": False, "output": f"图像生成失败 (HTTP {e.code}): {body}"}
        except Exception as e:
            return {"success": False, "output": f"图像生成失败: {e}"}

    # ── vision_analyze ─────────────────────────────────────────

    @staticmethod
    def _vision_schema() -> dict:
        return {
            "description": "分析图像内容。支持图像识别、OCR 文字提取、物体检测、场景理解等。接受图片 URL 或本地文件路径",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path_or_url": {
                        "type": "string",
                        "description": "图片路径（本地文件绝对路径）或 HTTP URL",
                    },
                    "question": {
                        "type": "string",
                        "description": "关于图像的具体问题（可选，如不指定则返回通用描述）",
                    },
                },
                "required": ["image_path_or_url"],
            },
        }

    def _handle_vision_analyze(self, args: dict) -> dict:  # pragma: no cover
        """图像理解 handler — 通过夸父自身的 LLM 多模态能力分析图像。

        注意：当前使用的 DeepSeek-Chat 模型本身不支持图像输入。
        此函数尝试调用多模态 API（如 OpenAI GPT-4V / Qwen-VL）完成分析，
        或使用本地 CLIP/LLaVA 模型（通过 VISION_API_URL 配置）。
        """
        image_path = args.get("image_path_or_url", "")
        question = args.get("question", "请详细描述这张图片的内容")

        if not image_path:
            return {"success": False, "output": "图片路径或 URL 不能为空"}

        # 尝试调用多模态 API
        vision_api_url = os.environ.get("VISION_API_URL", "")
        vision_api_key = os.environ.get("VISION_API_KEY", "")

        # 检查图片是否存在（本地路径）
        local_path = Path(image_path)
        if local_path.exists():
            # 本地文件 → base64 编码
            import base64 as _b64
            try:
                img_data = _b64.b64encode(local_path.read_bytes()).decode("utf-8")
                ext = local_path.suffix.lower()
                mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                        "png": "image/png", "webp": "image/webp",
                        "gif": "image/gif"}.get(ext.lstrip("."), "image/png")
                image_data_url = f"data:{mime};base64,{img_data}"
            except Exception as e:
                return {"success": False, "output": f"读取图片失败: {e}"}
        else:
            # URL
            image_data_url = image_path

        # 策略 1：如果配置了 VISION_API_URL，调那个
        if vision_api_url:
            try:
                payload = {
                    "model": os.environ.get("VISION_MODEL", "gpt-4o"),
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": question},
                                {"type": "image_url", "image_url": {"url": image_data_url}},
                            ],
                        }
                    ],
                    "max_tokens": 1024,
                }
                headers = {"Content-Type": "application/json"}
                if vision_api_key:
                    headers["Authorization"] = f"Bearer {vision_api_key}"

                req = urllib.request.Request(
                    vision_api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                content = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    or data.get("response", "")
                    or json.dumps(data, ensure_ascii=False)[:1000]
                )
                return {"success": True, "output": f"📷 图像分析结果:\n{content}"}

            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:300]
                return {"success": False, "output": f"视觉 API 调用失败 (HTTP {e.code}): {body}"}
            except Exception as e:
                return {"success": False, "output": f"视觉分析失败: {e}"}

        # 策略 2：无 API 配置，尝试本地方案
        # 尝试用 jp2a/ascii 工具做简单转换（纯 fallback）
        try:
            import subprocess as _sp
            if local_path.exists():
                # ffmpeg 提取基本信息
                result = _sp.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json",
                     "-show_format", "-show_streams", str(local_path)],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    info = json.loads(result.stdout)
                    streams = info.get("streams", [])
                    width = streams[0].get("width", "?") if streams else "?"
                    height = streams[0].get("height", "?") if streams else "?"
                    fmt = info.get("format", {}).get("format_name", "?")
                    return {
                        "success": True,
                        "output": f"📷 图像信息:\n尺寸: {width}x{height}\n格式: {fmt}\n路径: {image_path}\n\n（未配置 VISION_API_URL，无法进行 AI 分析。请设置环境变量使用多模态模型）",
                    }
        except Exception:
            pass

        return {"success": False, "output": f"未配置视觉分析 API。请设置 VISION_API_URL 和 VISION_API_KEY 环境变量。"}

    # ── text_to_speech ─────────────────────────────────────────

    @staticmethod
    def _tts_schema() -> dict:
        return {
            "description": "将文字转为语音音频。支持多种语音和语速。返回音频文件的本地路径",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要转为语音的文字内容",
                    },
                    "voice": {
                        "type": "string",
                        "enum": ["default", "female", "male"],
                        "description": "音色选择（默认 default）",
                    },
                    "speed": {
                        "type": "number",
                        "description": "语速倍率，0.5-2.0（默认 1.0）",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "输出文件路径（可选，默认自动生成在 memory/audio/ 下）",
                    },
                },
                "required": ["text"],
            },
        }

    def _handle_tts(self, args: dict) -> dict:  # pragma: no cover
        """文字转语音 handler。

        优先调用 TTS_API_URL 配置的 API（如 OpenAI TTS 或 Edge-TTS），
        回退到本地 espeak/ffmpeg。
        """
        text = args.get("text", "")
        if not text:
            return {"success": False, "output": "text 不能为空"}

        voice = args.get("voice", "default")
        speed = float(args.get("speed", 1.0))
        output_path = args.get("output_path", "")

        # 输出目录
        audio_dir = ROOT_DIR / "memory" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        if not output_path:
            import hashlib as _hlib
            safe_name = _hlib.md5(text.encode("utf-8")).hexdigest()[:12]
            voice_suffix = voice if voice != "default" else ""
            output_path = str(audio_dir / f"tts_{safe_name}{voice_suffix}.wav")

        api_url = os.environ.get("TTS_API_URL", "")
        api_key = os.environ.get("TTS_API_KEY", "")

        # 策略 1：TTS API
        if api_url:
            try:
                # 兼容 OpenAI TTS API 格式
                payload = {
                    "model": os.environ.get("TTS_MODEL", "tts-1"),
                    "input": text,
                    "voice": voice if voice != "default" else "alloy",
                    "speed": speed,
                }
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                req = urllib.request.Request(
                    api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    audio_data = resp.read()

                Path(output_path).write_bytes(audio_data)
                return {
                    "success": True,
                    "output": f"✅ 语音已生成!\n文件: {output_path}\n时长: {len(audio_data)} 字节\n文本: {text[:60]}...",
                }
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:200]
                # fallthrough to local fallback
                logger = __import__("logging").getLogger("kuafo.tool")
                logger.warning(f"TTS API 失败，回退到本地: {e.code} {body}")
            except Exception as e:
                return {"success": False, "output": f"TTS 生成失败: {e}"}

        # 策略 2：本地 espeak + ffmpeg
        try:
            import subprocess as _sp
            # 先用 espeak 生成基础音频
            espeak_cmd = ["espeak", text, "-w", output_path, "-s", str(int(speed * 175))]
            result = _sp.run(espeak_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and Path(output_path).exists():
                return {
                    "success": True,
                    "output": f"✅ 语音已生成 (本地 eSpeak)\n文件: {output_path}\n文本: {text[:60]}...\n播放: ffplay {output_path}",
                }
            # espeak 不可用，尝试纯 ffmpeg
            result2 = _sp.run(
                ["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                 "-t", str(max(1, len(text) // 10)), output_path],
                capture_output=True, text=True, timeout=10,
            )
            if result2.returncode == 0:
                return {
                    "success": True,
                    "output": f"⚠️ 生成了静音占位音频（无 TTS 引擎可用）\n文件: {output_path}\n请安装 espeak: apt install espeak",
                }
            return {"success": False, "output": f"本地 TTS 失败: espeak 和 ffmpeg 均不可用"}
        except FileNotFoundError:
            return {"success": False, "output": "本地 TTS 引擎不可用。请安装 espeak 或配置 TTS_API_URL。"}
        except Exception as e:
            return {"success": False, "output": f"TTS 失败: {e}"}

    # ── speech_to_text ─────────────────────────────────────────

    @staticmethod
    def _stt_schema() -> dict:
        return {
            "description": "将音频文件转为文字（语音识别）。支持多种音频格式。需要配置 STT_API_URL（如 Whisper API）",
            "parameters": {
                "type": "object",
                "properties": {
                    "audio_path": {
                        "type": "string",
                        "description": "音频文件路径（本地绝对路径，支持 mp3/wav/ogg/m4a）",
                    },
                    "language": {
                        "type": "string",
                        "description": "音频语言代码（可选，如 zh/en/ja。默认自动检测）",
                    },
                },
                "required": ["audio_path"],
            },
        }

    def _handle_stt(self, args: dict) -> dict:  # pragma: no cover
        """语音转文字 handler。

        调用 STT_API_URL 配置的 API（如 OpenAI Whisper API）。
        DeepSeek 本身不支持语音输入，因此依赖外部 API。
        """
        audio_path = args.get("audio_path", "")
        language = args.get("language", "")

        if not audio_path:
            return {"success": False, "output": "audio_path 不能为空"}

        audio_file = Path(audio_path)
        if not audio_file.exists():
            return {"success": False, "output": f"音频文件不存在: {audio_path}"}

        api_url = os.environ.get("STT_API_URL", "")
        api_key = os.environ.get("STT_API_KEY", "")

        if not api_url:
            return {"success": False, "output": "未配置语音识别 API。请设置 STT_API_URL 环境变量。"}

        try:
            # 兼容 OpenAI Whisper API 格式（multipart/form-data）
            # 使用 urllib 构建 multipart 请求
            import uuid as _uuid
            boundary = "----" + _uuid.uuid4().hex

            body_parts = []
            body_parts.append(f"--{boundary}")
            body_parts.append('Content-Disposition: form-data; name="file"; filename="{}"'.format(audio_file.name))
            body_parts.append("Content-Type: audio/wav")
            body_parts.append("")
            body_parts.append(audio_file.read_bytes().decode("latin-1"))

            if language:
                body_parts.append(f"--{boundary}")
                body_parts.append('Content-Disposition: form-data; name="language"')
                body_parts.append("")
                body_parts.append(language)

            body_parts.append(f"--{boundary}--")
            body_parts.append("")

            body_str = "\r\n".join(body_parts)
            body_bytes = body_str.encode("latin-1")

            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body_bytes)),
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            req = urllib.request.Request(
                api_url,
                data=body_bytes,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            text = (
                data.get("text", "")
                or data.get("result", "")
                or data.get("transcription", "")
                or data.get("response", "")
            )

            if text:
                return {"success": True, "output": f"📝 语音识别结果:\n{text}"}
            else:
                return {"success": True, "output": f"API 响应未包含文本。原始数据: {json.dumps(data, ensure_ascii=False)[:300]}"}

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            return {"success": False, "output": f"语音识别失败 (HTTP {e.code}): {body}"}
        except Exception as e:
            return {"success": False, "output": f"语音识别失败: {e}"}

    # ── aggregate_search ─────────────────────────────────────────

    @staticmethod
    def _aggregate_search_schema() -> dict:
        return {
            "description": "高级聚合搜索：同时搜索 DuckDuckGo + Bing + Tavily（如有 API Key），自动去重合并，可选 LLM 汇总生成结构化答案。适合需要全面信息的深度研究场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，越具体越好",
                    },
                    "summary": {
                        "type": "boolean",
                        "description": "是否使用 LLM 汇总生成综合答案（默认 true）",
                    },
                },
                "required": ["query"],
            },
        }

    def _handle_aggregate_search(self, args: dict) -> dict:  # pragma: no cover
        """高级聚合搜索 handler。"""
        from core.aggregate_search import aggregate_search

        query = args.get("query", "")
        if not query:
            return {"success": False, "output": "搜索词不能为空"}

        result = aggregate_search(
            query=query,
            max_per_engine=5,
            tavily_api_key=TAVILY_API_KEY,
        )

        return {
            "success": result["success"],
            "output": result["output"],
        }

    # ── download_file ─────────────────────────────────────────

    @staticmethod
    def _download_schema() -> dict:
        return {
            "description": "下载文件到本地。支持 HTTP/HTTPS/FTP，自动选择最佳引擎（Python requests → aria2c → wget → curl），自动处理文件名和去重。下载完成后返回文件路径和统计信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要下载的文件 URL（支持 http/https/ftp）",
                    },
                    "filename": {
                        "type": "string",
                        "description": "自定义文件名（可选，默认从 URL/响应头自动推断）",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "下载目录（可选，默认 downloads/ 目录）",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数（可选，默认 60）",
                    },
                },
                "required": ["url"],
            },
        }

    def _handle_download(self, args: dict) -> dict:  # pragma: no cover
        """下载文件 handler。"""
        from core.downloader import DownloadEngine

        url = args.get("url", "")
        if not url:
            return {"success": False, "output": "URL 不能为空"}
        if not url.startswith(("http://", "https://", "ftp://")):
            return {"success": False, "output": "URL 必须以 http:// https:// 或 ftp:// 开头"}

        filename = args.get("filename")
        output_dir = args.get("output_dir")
        timeout = args.get("timeout", 60)

        result = DownloadEngine.download(
            url=url,
            output_dir=output_dir,
            filename=filename,
            timeout=timeout,
        )

        if result.success:
            return {
                "success": True,
                "output": result.summarize(),
                "path": result.path,
                "size": result.size,
                "engine": result.engine,
                "elapsed": result.elapsed,
            }
        else:
            return {
                "success": False,
                "output": f"下载失败: {result.error}",
            }

    # ── browser_navigate ───────────────────────────────────────

    @staticmethod
    def _browser_nav_schema() -> dict:
        return {
            "description": "在无头浏览器中打开一个网页，返回页面交互元素快照（按钮、链接、输入框等）。适合需要与网页交互的场景（如登录、搜索、填表单）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要打开的页面 URL（如 https://example.com）",
                    },
                },
                "required": ["url"],
            },
        }

    def _handle_browser_navigate(self, args: dict) -> dict:  # pragma: no cover
        from core.browser import navigate
        url = args.get("url", "")
        if not url:
            return {"success": False, "output": "URL 不能为空"}
        return navigate(url)

    # ── browser_snapshot ───────────────────────────────────────

    @staticmethod
    def _browser_snap_schema() -> dict:
        return {
            "description": "获取当前浏览器页面的快照（交互元素列表），用于查看页面最新状态。当页面因交互发生变化或需要刷新视图时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "full": {
                        "type": "boolean",
                        "description": "是否获取完整文本内容（默认 false，仅返回可交互元素）",
                    },
                },
                "required": [],
            },
        }

    def _handle_browser_snapshot(self, args: dict) -> dict:  # pragma: no cover
        from core.browser import snapshot
        full = args.get("full", False)
        return snapshot(full=full)

    # ── browser_click ──────────────────────────────────────────

    @staticmethod
    def _browser_click_schema() -> dict:
        return {
            "description": "点击页面上的某个元素。用 @e 格式引用（如 @e5），该 ref 来自 browser_navigate 或 browser_snapshot 返回的快照",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "元素引用 ID（如 @e5），来自页面快照中的 [@e5] 标记",
                    },
                },
                "required": ["ref"],
            },
        }

    def _handle_browser_click(self, args: dict) -> dict:  # pragma: no cover
        from core.browser import click
        ref = args.get("ref", "")
        if not ref:
            return {"success": False, "output": "ref 不能为空"}
        return click(ref)

    # ── browser_type ───────────────────────────────────────────

    @staticmethod
    def _browser_type_schema() -> dict:
        return {
            "description": "向页面上的输入框输入文本（如搜索框、登录表单）。用 @e 格式引用元素，输入前会自动清空原有内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "输入框元素引用 ID（如 @e3），来自页面快照中的 [@e3] 标记",
                    },
                    "text": {
                        "type": "string",
                        "description": "要输入的文本内容",
                    },
                },
                "required": ["ref", "text"],
            },
        }

    def _handle_browser_type(self, args: dict) -> dict:  # pragma: no cover
        from core.browser import type_text
        ref = args.get("ref", "")
        text = args.get("text", "")
        if not ref:
            return {"success": False, "output": "ref 不能为空"}
        if not text:
            return {"success": False, "output": "text 不能为空"}
        return type_text(ref, text)

    # ── browser_screenshot ─────────────────────────────────────

    @staticmethod
    def _browser_screenshot_schema() -> dict:
        return {
            "description": "截取当前浏览器页面的截图，保存到 screenshots/ 目录。对于验证页面渲染、查看图片/图表/验证码等场景非常有用",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "自定义文件名（可选，默认自动生成时间戳文件名）",
                    },
                },
                "required": [],
            },
        }

    def _handle_browser_screenshot(self, args: dict) -> dict:  # pragma: no cover
        from core.browser import screenshot
        filename = args.get("filename")
        return screenshot(filename=filename)

    # ── browser_js ─────────────────────────────────────────────

    @staticmethod
    def _browser_js_schema() -> dict:
        return {
            "description": "在浏览器页面中执行 JavaScript 代码，返回执行结果。适合提取页面数据、操作DOM、调用API等高级场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要执行的 JavaScript 表达式或代码块",
                    },
                },
                "required": ["expression"],
            },
        }

    def _handle_browser_js(self, args: dict) -> dict:  # pragma: no cover
        from core.browser import execute_js
        expression = args.get("expression", "")
        if not expression:
            return {"success": False, "output": "expression 不能为空"}
        return execute_js(expression)

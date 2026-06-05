"""
夸父技能包 (kfskill) 格式定义与操作

kfskill 是夸父（Kuafu）的技能包格式，用于技能的分发、安装和共享。
每个 kfskill 包含：
- 技能元数据（名称、描述、关键词、作者、版本）
- 执行步骤（有序的任务列表）
- 依赖声明（可选的外部工具或环境需求）
- 使用统计（自动追踪）

格式基础：YAML（兼容 SKILL.md 的 YAML frontmatter）
扩展名：.yaml（标准）、.kfskill（显式标识）
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT_DIR / "skills"

# ── kfskill 格式规范 ───────────────────────────────────────────

# 一个有效的 kfskill 文件结构示例：
"""
name: 网页抓取与内容提取           # 技能名称（必填，唯一标识）
description: 自动抓取网页并提取核心内容  # 描述（必填，一行摘要）
version: 1.0.0                    # 版本号（推荐，默认 1.0.0）
author: kuafu                     # 作者（可选）
category: web                     # 分类（可选：coding/web/research/devops/media）
keywords:                         # 搜索关键词（推荐）
  - web scraping
  - 网页抓取
  - content extraction
dependencies:                     # 依赖声明（可选）
  tools:                          # 需要哪些工具
    - python
    - curl
  packages:                       # 需要哪些 Python 包
    - beautifulsoup4
    - lxml
steps:                            # 执行步骤（必填，至少 1 步）
  - 分析目标网页结构
  - 使用 python + requests 抓取 HTML
  - 提取标题、正文、元数据
  - 格式化输出结果
pitfalls:                         # 常见陷阱（可选）
  - 某些网站有反爬机制，需要调整 User-Agent
  - 动态渲染页面需配合 Playwright
created_at: 1779715166            # 创建时间戳（自动填充）
usage_count: 0                    # 使用次数（自动追踪）
source: manual                    # 来源：manual / kuafu_evolution / market_install
"""


# ── kfskill Schema 定义 ────────────────────────────────────────

KF_SKILL_SCHEMA = {
    "required": ["name", "description", "steps"],
    "optional": {
        "version": {"type": str, "default": "1.0.0"},
        "author": {"type": str, "default": ""},
        "category": {"type": str, "default": ""},
        "keywords": {"type": list, "default": []},
        "dependencies": {"type": dict, "default": {}},
        "steps": {"type": list, "default": []},
        "pitfalls": {"type": (list, type(None)), "default": []},
        "created_at": {"type": (int, float), "default": 0},
        "usage_count": {"type": int, "default": 0},
        "source": {"type": str, "default": "manual"},
    },
}

VALID_CATEGORIES = {
    "coding", "web", "research", "devops", "media",
    "writing", "data-science", "productivity", "communication",
    "general",
}


# ── 验证 ───────────────────────────────────────────────────────

def validate_kfskill(data: dict) -> tuple[bool, list[str]]:
    """验证 kfskill 格式是否合法。

    Returns:
        (is_valid, [error_messages])
    """
    errors = []

    # 必填字段
    for field in ["name", "description", "steps"]:
        if field not in data or not data[field]:
            errors.append(f"缺少必填字段: {field}")

    # name 长度
    name = data.get("name", "")
    if name and len(name) > 100:
        errors.append(f"name 过长（{len(name)} > 100）")
    if name and re.search(r'[<>:"/\\|?*]', name):
        errors.append(f"name 包含非法字符: {name}")

    # description 长度
    desc = data.get("description", "")
    if desc and len(desc) > 500:
        errors.append(f"description 过长（{len(desc)} > 500）")

    # steps
    steps = data.get("steps", [])
    if steps and not isinstance(steps, list):
        errors.append("steps 必须是列表")
    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if not isinstance(step, str) or not step.strip():
                errors.append(f"steps[{i}] 必须是非空字符串")

    # category
    cat = data.get("category", "")
    if cat and cat not in VALID_CATEGORIES:
        errors.append(f"无效 category: {cat}，可选: {sorted(VALID_CATEGORIES)}")

    # version 格式
    version = data.get("version", "")
    if version and not re.match(r"^\d+\.\d+\.\d+$", str(version)):
        errors.append(f"version 格式无效: {version}，应为 x.y.z")

    # keywords
    keywords = data.get("keywords", [])
    if keywords and not isinstance(keywords, list):
        errors.append("keywords 必须是列表")

    # usage_count
    usage = data.get("usage_count", 0)
    if not isinstance(usage, int) or usage < 0:
        errors.append("usage_count 必须是非负整数")

    return len(errors) == 0, errors


# ── 生命周期操作 ──────────────────────────────────────────────

def create_skill(name: str, description: str, steps: list[str],
                 category: str = "", keywords: list[str] = None,
                 pitfalls: list[str] = None,
                 version: str = "1.0.0",
                 author: str = "",
                 dependencies: dict = None,
                 source: str = "manual") -> dict:
    """创建一个新的 kfskill 格式技能数据。

    Returns:
        {"success": True, "data": {...}} 或 {"success": False, "error": "..."}
    """
    data = {
        "name": name,
        "description": description,
        "version": version,
        "author": author or os.environ.get("USER", ""),
        "category": category,
        "keywords": keywords or [],
        "dependencies": dependencies or {},
        "steps": steps,
        "pitfalls": pitfalls or [],
        "created_at": int(time.time()),
        "usage_count": 0,
        "source": source,
    }

    valid, errors = validate_kfskill(data)
    if not valid:
        return {"success": False, "error": "; ".join(errors)}

    return {"success": True, "data": data}


def save_skill(data: dict, output_dir: Optional[str] = None) -> dict:
    """将 kfskill 数据写入 YAML 文件。

    不使用 pyyaml 依赖，手动序列化为兼容格式。
    """
    out_dir = Path(output_dir) if output_dir else SKILLS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", data["name"])
    file_path = out_dir / f"{safe_name}.yaml"

    content = _serialize_to_yaml(data)
    file_path.write_text(content, encoding="utf-8")

    return {"success": True, "path": str(file_path), "name": data["name"]}


def load_skill(file_path: str) -> dict:
    """从 YAML 文件加载 kfskill。

    使用简单的解析器（无需 pyyaml）。
    """
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    content = path.read_text(encoding="utf-8")
    data = _parse_yaml(content)
    if not data:
        return {"success": False, "error": "无法解析 YAML"}

    valid, errors = validate_kfskill(data)
    if not valid:
        return {"success": False, "error": "; ".join(errors), "data": data}

    return {"success": True, "data": data, "path": str(file_path)}


def increment_usage(file_path: str) -> dict:
    """增加技能的 usage_count。"""
    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    content = path.read_text(encoding="utf-8")
    data = _parse_yaml(content)
    if not data:
        return {"success": False, "error": "无法解析 YAML"}

    count = data.get("usage_count", 0) + 1
    data["usage_count"] = count

    # 替换 usage_count 行
    new_content = re.sub(
        r"^usage_count:\s*\d+",
        f"usage_count: {count}",
        content,
        flags=re.MULTILINE,
    )
    path.write_text(new_content, encoding="utf-8")
    return {"success": True, "usage_count": count}


def export_to_json(data: dict) -> dict:
    """将 kfskill 导出为 JSON（市场索引格式）。"""
    return {
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "version": data.get("version", "1.0.0"),
        "author": data.get("author", ""),
        "category": data.get("category", ""),
        "keywords": data.get("keywords", []),
        "steps": len(data.get("steps", [])),
        "pitfalls": len(data.get("pitfalls", [])),
        "usage_count": data.get("usage_count", 0),
    }


# ── YAML 序列化/解析（零依赖） ────────────────────────────────

def _serialize_to_yaml(data: dict) -> str:
    """将 kfskill 数据序列化为 YAML 格式。"""
    lines = []
    for key, value in data.items():
        if key == "steps":
            lines.append("steps:")
            for step in value:
                lines.append(f"- {step}")
        elif key == "keywords":
            lines.append("keywords:")
            for kw in value:
                lines.append(f"- {kw}")
        elif key == "pitfalls":
            if value:
                lines.append("pitfalls:")
                for p in value:
                    lines.append(f"- {p}")
            else:
                lines.append("pitfalls: null")
        elif key == "dependencies" and isinstance(value, dict):
            lines.append("dependencies:")
            for dk, dv in value.items():
                if isinstance(dv, list):
                    lines.append(f"  {dk}:")
                    for item in dv:
                        lines.append(f"    - {item}")
                else:
                    lines.append(f"  {dk}: {dv}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def _parse_yaml(content: str) -> Optional[dict]:
    """简化 YAML 解析器（仅支持 kfskill 所用到的格式）。"""
    data = {}
    current_key = None
    current_list = None
    in_dependencies = False
    dep_key = None

    for line in content.split("\n"):
        stripped = line.strip()

        # 跳过空行和注释
        if not stripped or stripped.startswith("#"):
            continue

        # 检测嵌套的 dependencies
        if stripped == "dependencies:":
            in_dependencies = True
            data["dependencies"] = {}
            continue

        if in_dependencies:
            indent_match = re.match(r"^(\s+)(\w[\w_]*):$", line)
            if indent_match:
                dep_key = indent_match.group(2)
                data["dependencies"][dep_key] = []
                continue
            if dep_key:
                list_match = re.match(r"^\s+-\s+(.+)$", line)
                if list_match:
                    data["dependencies"][dep_key].append(list_match.group(1).strip())
                    continue
            # 退出 dependencies
            if not stripped.startswith(" ") and ":" in stripped:
                in_dependencies = False
                dep_key = None

        # 列表项（steps, keywords, pitfalls）
        list_match = re.match(r"^-\s+(.+)$", stripped)
        if list_match and current_key:
            data.setdefault(current_key, []).append(list_match.group(1).strip())
            continue

        # 键值对
        kv_match = re.match(r"^(\w[\w_]*):\s*(.*)$", stripped)
        if kv_match:
            key = kv_match.group(1)
            value = kv_match.group(2).strip()

            # 跳过已有的复杂结构键
            if key in ("steps", "keywords", "pitfalls"):
                current_key = key
                data[key] = []
                continue

            # 处理特殊值
            if value == "null":
                data[key] = None
            elif value == "true":
                data[key] = True
            elif value == "false":
                data[key] = False
            elif value.isdigit():
                data[key] = int(value)
            elif value.replace(".", "", 1).isdigit() and value.count(".") == 1:
                try:
                    data[key] = float(value)
                except ValueError:
                    data[key] = value
            else:
                # 移除引号
                data[key] = value.strip("\"'")

            current_key = None
            current_list = None

    return data if data else None


# ── 导出为规范文档字符串 ──────────────────────────────────────

KFSKILL_SPECIFICATION = """
# 夸父技能包格式 (kfskill) v1.0

kfskill 是夸父（Kuafu）的技能包分享格式，用于技能的分发、安装和共享。
每个技能包就是一个 .yaml 文件。

---

## 文件结构

```yaml
name:                # 必填。技能名称（唯一标识，最长 100 字符）
description:         # 必填。一行摘要（最长 500 字符）
version:             # 推荐。语义化版本号 x.y.z（默认 1.0.0）
author:              # 可选。作者名
category:            # 可选。分类（coding/web/research/devops/media/writing/data-science/productivity/communication/general）
keywords:            # 推荐。搜索关键词列表
  - keyword1
  - keyword2
dependencies:        # 可选。依赖声明
  tools:             #   需要的系统工具
    - python
    - curl
  packages:          #   需要的 Python 包
    - requests
steps:               # 必填。执行步骤列表（至少 1 步）
  - 第一步做什么
  - 第二步做什么
pitfalls:            # 可选。常见陷阱和注意事项
  - 注意点1
  - 注意点2
created_at:          # 自动填充。创建时间戳
usage_count:         # 自动追踪。使用次数
source:              # 自动填充。来源（manual/kuafu_evolution/market_install）
```

## 示例

```yaml
name: API 文档查询工具
description: 根据关键词搜索并返回 API 文档链接和摘要
version: 1.0.0
author: kuafu
category: coding
keywords:
  - API
  - 文档
  - 搜索
steps:
  - 分析用户查询的 API 名称和版本
  - 搜索对应的官方文档网站
  - 提取相关接口说明和参数
  - 返回结构化的文档摘要
pitfalls:
  - 不同版本的 API 可能有差异
  - 部分文档需要翻墙访问
created_at: 1779715166
usage_count: 0
source: manual
```

## 字段规范

| 字段 | 必填 | 类型 | 最大长度 | 说明 |
|------|------|------|---------|------|
| name | ✅ | string | 100 | 技能唯一标识 |
| description | ✅ | string | 500 | 一行摘要 |
| version | 推荐 | string | 20 | 语义化版本 |
| author | ❌ | string | 100 | 作者名称 |
| category | ❌ | string | 30 | 分类标签 |
| keywords | 推荐 | [string] | 10项 | 搜索关键词 |
| dependencies | ❌ | dict | — | 依赖声明 |
| steps | ✅ | [string] | 50项 | 执行步骤 |
| pitfalls | ❌ | [string] | 20项 | 注意事项 |
| created_at | 自动 | int | — | Unix 时间戳 |
| usage_count | 自动 | int | — | 非负整数 |
| source | 自动 | string | 30 | 来源标记 |

## 命名约束

- name 不能包含 `<>:"/\\|?*` 等特殊字符
- 文件名由 name 自动生成，特殊字符替换为下划线
- 不支持同名技能（后安装会覆盖）
"""

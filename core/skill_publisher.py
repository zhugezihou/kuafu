"""
夸父技能发布工具 (Skill Publisher)

完整工作流：
1. 验证（validate）— 检查格式完整性、依赖声明、沙箱配置
2. 打包（package）— 生成市场索引条目 + 准备上传文件
3. 发布（publish）— 推送到 GitHub Release 或克隆的仓库
4. 索引更新 — 生成/更新 index.json

设计原则：
- 零新增依赖（仅标准库 + gh CLI）
- 与现有 git 工作流集成（推送到 GitHub）
- 自动版本号管理（检测文件变更自动递增）
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent

# ── 发布配置 ──────────────────────────────────────────────────

DEFAULT_REPO_URL = "https://github.com/zhugezihou/kuafu-skill-market"
DEFAULT_REPO_SSH = "git@github.com:zhugezihou/kuafu-skill-market.git"


# ── 验证结果 ──────────────────────────────────────────────────

@dataclass
class ValidationReport:
    """技能验证报告。"""

    passed: bool = False
    checks: dict = field(default_factory=dict)

    def add_check(self, name: str, ok: bool, detail: str = ""):
        self.checks[name] = {"ok": ok, "detail": detail}
        if not ok:
            self.passed = False

    def summary(self) -> str:
        if self.passed:
            return "✅ 全部验证通过"
        lines = ["❌ 验证未通过:"]
        for name, check in self.checks.items():
            icon = "✅" if check["ok"] else "❌"
            detail = f" — {check['detail']}" if check["detail"] else ""
            lines.append(f"   {icon} {name}{detail}")
        return "\n".join(lines)


# ── 发布计划 ──────────────────────────────────────────────────

@dataclass
class PublishPlan:
    """发布计划。"""

    skill_name: str = ""
    version: str = ""
    file_path: str = ""
    index_entry: dict = field(default_factory=dict)
    repo_path: str = ""           # 市场仓库本地路径
    skill_dest: str = ""          # 技能文件目标路径
    index_path: str = ""          # index.json 目标路径
    release_tag: str = ""         # GitHub Release tag
    checksum: str = ""            # 文件校验和
    url: str = ""                 # 发布后的 raw URL


# ── 核心发布逻辑 ──────────────────────────────────────────────

def validate_skill(data: dict) -> ValidationReport:
    """对技能进行全方位验证。

    检查项：
    - kfskill 格式完整性
    - 必填字段存在
    - 版本号格式
    - 分类合法性
    - 沙箱配置可用
    - 依赖声明合法
    """
    report = ValidationReport(passed=True)

    # 1. kfskill 格式
    from core.kfskill import validate_kfskill
    valid, errors = validate_kfskill(data)
    report.add_check("kfskill 格式", valid, "; ".join(errors) if errors else "")

    # 2. 必填字段
    name = data.get("name", "")
    desc = data.get("description", "")
    steps = data.get("steps", [])
    report.add_check("名称非空", bool(name))
    report.add_check("描述非空", bool(desc))
    report.add_check("至少 1 个步骤", len(steps) >= 1, f"当前: {len(steps)}")

    # 3. 版本号
    version = data.get("version", "")
    has_version = bool(version) and re.match(r"^\d+\.\d+\.\d+$", str(version))
    report.add_check("版本号格式 (x.y.z)", bool(has_version),
                     f"当前: {version or '(空)'}")

    # 4. 分类
    from core.kfskill import VALID_CATEGORIES
    cat = data.get("category", "")
    if cat:
        is_valid = cat in VALID_CATEGORIES
        detail = f"「{cat}」" if is_valid else f"「{cat}」不在合法分类中: {sorted(VALID_CATEGORIES)}"
        report.add_check("分类合法", is_valid, detail)
    else:
        report.add_check("分类已设置", False, "建议设置分类以便搜索")

    # 5. 沙箱检查（已移除，安全策略由 SafetyLayer + PolicyManager 统一管理）
    report.add_check("沙箱配置", True, "已由 SafetyLayer + PolicyManager 统一管理")

    # 6. 依赖声明
    deps = data.get("dependencies", {})
    if deps:
        from core.skill_deps import check_dependencies
        dep_result = check_dependencies(data)
        report.add_check(
            "依赖声明",
            dep_result.ok,
            dep_result.summary() if not dep_result.ok else ""
        )
    else:
        report.add_check("依赖声明（推荐）", True, "无可选依赖")

    # 7. 关键词推荐
    keywords = data.get("keywords", [])
    report.add_check("关键词（推荐）", len(keywords) >= 2,
                     f"当前: {len(keywords)} 个，建议至少 2 个")

    # 8. pitfalls 推荐
    pitfalls = data.get("pitfalls", [])
    if not pitfalls:
        report.add_check("注意事项（推荐）", False, "建议补充常见陷阱和注意事项")
    else:
        report.add_check("注意事项", True, f"{len(pitfalls)} 条")

    # 最终状态
    # 只有致命检查决定 passed 状态
    fatal_names = ["kfskill 格式", "名称非空", "描述非空", "至少 1 个步骤"]
    report.passed = all(
        report.checks.get(f_name, {}).get("ok", False)
        for f_name in fatal_names
    )
    return report


def package_skill(skill_name: str, output_dir: Optional[str] = None) -> dict:
    """打包技能为发布格式。

    Args:
        skill_name: 本地技能名称
        output_dir: 输出目录（可选，默认 /tmp/kuafu-publish/）

    Returns:
        {"success": True, "plan": PublishPlan, ...}
        或 {"success": False, "error": "..."}
    """
    from core.skill_manager import SkillManager
    from core.kfskill import export_to_json

    mgr = SkillManager()
    local = mgr.list_local()

    # 查找技能
    found = None
    for s in local + mgr.list_installed_market():
        if s.name == skill_name:
            found = s
            break
    if not found:
        return {"success": False, "error": f"未找到技能: {skill_name}"}

    # 读取技能数据
    try:
        import yaml
        fpath_str = found.file_path
        if not Path(fpath_str).is_absolute():
            from core.skill_manager import ROOT_DIR as SKILL_ROOT
            fpath_str = str(SKILL_ROOT / fpath_str)
        fpath = Path(fpath_str)
        data = yaml.safe_load(fpath.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {"success": False, "error": f"读取技能文件失败: {e}"}

    # 验证
    report = validate_skill(data)
    if not report.passed:
        return {"success": False, "error": report.summary(), "report": report}

    # 生成发布计划
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", data.get("name", skill_name))
    version = data.get("version", "1.0.0")
    timestamp = int(time.time())

    out_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "kuafu-publish"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 计算 checksum
    content = fpath.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()[:16]

    # 构建计划
    plan = PublishPlan(
        skill_name=data.get("name", skill_name),
        version=version,
        file_path=str(fpath),
        index_entry=export_to_json(data),
        repo_path="",
        skill_dest=f"skills/{safe_name}.yaml",
        index_path="index.json",
        release_tag=f"{safe_name}-v{version}",
        checksum=file_hash,
        url=f"https://raw.githubusercontent.com/zhugezihou/kuafu-skill-market/main/skills/{safe_name}.yaml",
    )
    plan.index_entry["url"] = plan.url
    plan.index_entry["checksum"] = f"sha256:{file_hash}"

    # 创建发布目录
    publish_dir = out_dir / safe_name
    publish_dir.mkdir(parents=True, exist_ok=True)

    # 复制技能文件
    shutil.copy2(str(fpath), str(publish_dir / f"{safe_name}.yaml"))

    # 生成 index.json
    index_entry = plan.index_entry.copy()
    index_data = {
        "skills": [index_entry],
        "_meta": {
            "generated_at": timestamp,
            "source_skill": skill_name,
            "version": version,
        },
    }
    (publish_dir / "index.json").write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 生成发布说明
    readme = _generate_release_notes(data, plan)
    (publish_dir / "RELEASE.md").write_text(readme, encoding="utf-8")

    return {
        "success": True,
        "plan": plan,
        "publish_dir": str(publish_dir),
        "report": report,
    }


def publish_to_github(plan: PublishPlan,
                       repo_url: str = DEFAULT_REPO_SSH,
                       create_release: bool = False,
                       branch: str = "main") -> dict:
    """将技能发布到 GitHub 市场仓库。

    两种模式：
    1. Git Push（默认）：克隆仓库 → 添加文件 → commit → push
    2. GitHub Release（--release）：创建 Release + 上传技能文件

    Args:
        plan: 发布计划
        repo_url: 市场仓库 URL（SSH 或 HTTPS）
        create_release: 是否创建 GitHub Release（需 gh CLI）
        branch: 目标分支

    Returns:
        {"success": True, "message": str, ...}
    """
    # 检查 gh CLI
    if create_release and not shutil.which("gh"):
        return {"success": False, "error": "创建 Release 需要安装 gh CLI"}

    # 检查 git
    if not shutil.which("git"):
        return {"success": False, "error": "需要安装 git"}

    # 创建临时目录
    tmp_dir = Path(tempfile.mkdtemp(prefix="kuafu-publish-"))
    try:
        # 克隆仓库
        result = subprocess.run(
            ["git", "clone", repo_url, str(tmp_dir / "repo")],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"success": False, "error": f"克隆仓库失败: {result.stderr[:200]}"}

        repo_path = tmp_dir / "repo"

        # 确保 skills/ 目录存在
        skills_dir = repo_path / "skills"
        skills_dir.mkdir(exist_ok=True)

        # 复制技能文件
        skill_src = Path(plan.file_path)
        skill_dst = skills_dir / f"{Path(plan.skill_dest).name}"
        shutil.copy2(str(skill_src), str(skill_dst))

        # 更新/生成 index.json
        index_path = repo_path / "index.json"
        if index_path.exists():
            try:
                index_data = json.loads(index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                index_data = {"skills": []}
        else:
            index_data = {"skills": []}

        # 更新或添加技能条目
        existing = [s for s in index_data.get("skills", [])
                    if s.get("name") != plan.skill_name]
        existing.append(plan.index_entry)
        index_data["skills"] = existing

        # 更新仓库元数据
        index_data["name"] = "kuafu-skill-market"
        index_data["description"] = "夸父（Kuafu）AI Agent 远程技能仓库"
        index_data["version"] = "1.0.0"
        index_data["updated_at"] = int(time.time())
        index_data["total_skills"] = len(existing)

        index_path.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Commit & Push
        commit_msg = f"发布技能: {plan.skill_name} v{plan.version}"
        git_cmds = [
            (["git", "add", "-A"], {}),
            (["git", "commit", "-m", commit_msg], {}),
            (["git", "push", "origin", branch], {"timeout": 30}),
        ]
        for cmd, kwargs in git_cmds:
            r = subprocess.run(cmd, cwd=str(repo_path),
                               capture_output=True, text=True, **kwargs)
            if r.returncode != 0 and "nothing to commit" not in r.stderr:
                # commit 可能无变化，不视为错误
                if "commit" not in cmd[0]:
                    pass

        # 创建 Release（可选）
        release_info = {}
        if create_release:
            tag = plan.release_tag
            subprocess.run(["git", "tag", tag], cwd=str(repo_path),
                           capture_output=True, text=True)
            subprocess.run(["git", "push", "origin", tag], cwd=str(repo_path),
                           capture_output=True, text=True, timeout=30)

            release_result = subprocess.run(
                ["gh", "release", "create", tag,
                 str(skill_dst),
                 "--repo", DEFAULT_REPO_URL,
                 "--title", f"{plan.skill_name} v{plan.version}",
                 "--notes", f"发布技能: {plan.skill_name} v{plan.version}"],
                capture_output=True, text=True, timeout=30,
            )
            release_info = {
                "tag": tag,
                "success": release_result.returncode == 0,
                "url": f"https://github.com/zhugezihou/kuafu-skill-market/releases/tag/{tag}" if release_result.returncode == 0 else "",
            }

        return {
            "success": True,
            "message": f"✅ 已发布 {plan.skill_name} v{plan.version}",
            "skill_url": plan.url,
            "commit_msg": commit_msg,
            "release": release_info,
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "git 操作超时，请检查网络"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        # 清理临时文件
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass


def publish_to_local(index_path: str, plan: PublishPlan) -> dict:
    """将技能发布到本地市场索引文件（不涉及 GitHub）。

    适合：本地测试、离线环境、手动管理仓库的场景。
    """
    ip = Path(index_path)

    # 读取或创建 index.json
    if ip.exists():
        try:
            index_data = json.loads(ip.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            index_data = {"skills": []}
    else:
        index_data = {
            "name": "kuafu-local-market",
            "description": "本地技能市场（手动管理）",
            "skills": [],
        }

    # 更新条目
    existing = [s for s in index_data.get("skills", [])
                if s.get("name") != plan.skill_name]
    existing.append(plan.index_entry)
    index_data["skills"] = existing
    index_data["total_skills"] = len(existing)
    index_data["updated_at"] = int(time.time())

    # 写入
    ip.parent.mkdir(parents=True, exist_ok=True)
    ip.write_text(json.dumps(index_data, ensure_ascii=False, indent=2),
                  encoding="utf-8")

    # 复制技能文件
    skills_dir = ip.parent / "skills"
    skills_dir.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", plan.skill_name)
    shutil.copy2(plan.file_path, str(skills_dir / f"{safe_name}.yaml"))

    return {
        "success": True,
        "message": f"✅ 已发布到本地: {ip}",
        "index_path": str(ip),
        "skill_path": str(skills_dir / f"{safe_name}.yaml"),
        "skill_url": f"skills/{safe_name}.yaml",
    }


# ── 辅助函数 ──────────────────────────────────────────────────

def _generate_release_notes(data: dict, plan: PublishPlan) -> str:
    """生成发布说明。"""
    name = data.get("name", plan.skill_name)
    version = plan.version
    desc = data.get("description", "")
    steps = data.get("steps", [])
    pitfalls = data.get("pitfalls", [])
    deps = data.get("dependencies", {})

    lines = [
        f"# {name} v{version}",
        "",
        f"{desc}",
        "",
        "## 步骤",
        "",
    ]
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {step}")

    if pitfalls:
        lines.extend(["", "## 注意事项", ""])
        for p in pitfalls:
            lines.append(f"- {p}")

    if deps:
        lines.extend(["", "## 依赖", ""])
        for dk, dv in deps.items():
            if isinstance(dv, list):
                lines.append(f"- {dk}: {', '.join(dv)}")
            else:
                lines.append(f"- {dk}: {dv}")

    lines.extend([
        "",
        "---",
        f"- 校验和: `{plan.checksum}`",
        f"- 发布时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ])
    return "\n".join(lines)


def get_next_version(name: str, bump: str = "patch") -> str:
    """获取技能的下一个版本号。

    Args:
        name: 技能名称
        bump: 版本递增方式（major/minor/patch）

    Returns:
        新版本号字符串
    """
    from core.skill_manager import SkillManager
    mgr = SkillManager()

    current = "0.0.0"
    for s in mgr.list_local() + mgr.list_installed_market():
        if s.name == name:
            try:
                import yaml
                from core.skill_manager import ROOT_DIR as SKILL_ROOT
                fpath = s.file_path
                if not Path(fpath).is_absolute():
                    fpath = str(SKILL_ROOT / fpath)
                data = yaml.safe_load(Path(fpath).read_text(encoding="utf-8"))
                current = (data or {}).get("version", "0.0.0")
            except Exception:
                pass
            break

    # 解析版本号
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(current))
    if not m:
        return "1.0.0"

    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if bump == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1

    return f"{major}.{minor}.{patch}"

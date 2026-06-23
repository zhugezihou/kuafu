"""
夸父 CLI 子命令系统 — kuafu cron/sessions/skill/status 子命令。

用法：
    kuafu                          # 交互模式
    kuafu "写个脚本"               # 直接执行任务
    kuafu cron list                # 查看定时任务
    kuafu cron create "30m" "搜索新闻"  # 创建定时任务
    kuafu sessions list            # 查看会话列表
    kuafu sessions prune           # 清理旧会话
    kuafu status                   # 查看状态
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


import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent


def run_cron(args: argparse.Namespace, agent: Any) -> int:
    """kuafu cron 子命令。"""
    from core.cron_scheduler import CronScheduler, CronTask

    # 创建调度器（用 agent.run 作为任务回调）
    scheduler = CronScheduler(on_task_run=lambda task: agent.run(task.task_text)["result"])

    if args.cmd == "list":
        tasks = scheduler.get_tasks()
        if not tasks:
            print("没有定时任务。使用 'kuafu cron create' 添加。")
            return 0

        print(f"{'名称':<25} {'调度':<15} {'运行':>5} {'启用':>5}")
        print("-" * 60)
        for t in tasks:
            status = "✅" if t.enabled else "⏸"
            print(f"{t.name:<25} {t.schedule_raw:<15} {t.run_count:>5} {status:>5}")
        return 0

    elif args.cmd == "create":
        if not args.task_text:
            print("用法: kuafu cron create <schedule> <task_text>")
            print("  schedule: '30m' | '2h' | '0 8 * * *' | ISO时间")
            print("  task_text: 要让夸父执行的指令")
            print()
            print("示例: kuafu cron create '30m' '搜索今日科技新闻'")
            return 1

        schedule = args.schedule
        text = args.task_text
        name = args.name or f"cron_{time.strftime('%m%d_%H%M%S')}"

        task = CronTask(
            name=name,
            schedule=schedule,
            task_text=text,
            enabled=True,
            output_mode=args.output_mode or "file",
        )
        scheduler.add_task(task)
        print(f"✅ 已创建定时任务: {name}")
        print(f"   调度: {schedule}")
        print(f"   任务: {text[:80]}...")
        if not scheduler._running:
            scheduler.start()
            print("🟢 调度器已自动启动")
        return 0

    elif args.cmd == "remove":
        if not args.name:
            print("用法: kuafu cron remove <task_name>")
            tasks = scheduler.get_tasks()
            if tasks:
                print("现有任务:")
                for t in tasks:
                    print(f"  {t.name}")
            return 1

        if scheduler.remove_task(args.name):
            print(f" 已删除任务: {args.name}")
        else:
            print(f" 未找到任务: {args.name}")
        return 0

    elif args.cmd == "start":
        if not scheduler._running:
            scheduler.start()
            print("调度器已启动")
        else:
            print("调度器已在运行")
        return 0

    elif args.cmd == "stop":
        scheduler.stop()
        print("调度器已停止")
        return 0

    elif args.cmd == "status":
        tasks = scheduler.get_tasks()
        running = scheduler._running
        print(f"调度器状态: {'运行中' if running else '已停止'}")
        print(f"任务数: {len(tasks)}")
        print()
        if tasks:
            print(f"{'名称':<25} {'调度':<15} {'运行':>5} {'启用':>5} {'最后运行':<20}")
            print("-" * 80)
            for t in tasks:
                last_run = t.last_run[-16:] if t.last_run else "-"
                status = "✅" if t.enabled else "⏸"
                print(f"{t.name:<25} {t.schedule_raw:<15} {t.run_count:>5} {status:>5} {last_run:<20}")
        return 0

    else:
        print(f"未知的 cron 子命令: {args.cmd}")
        print("可用命令: list, create, remove, start, stop, status")
        return 1


def run_sessions(args: argparse.Namespace, agent: Any) -> int:
    """kuafu sessions 子命令。"""
    store = agent.sessions if hasattr(agent, 'sessions') else None
    if store is None:
        from core.session_store import SessionStore
        store = SessionStore()

    if args.cmd == "list":
        limit = args.limit or 20
        status_filter = args.status or ""
        sessions = store.list_sessions(limit=limit, status=status_filter)
        if not sessions:
            print("没有会话记录。")
            return 0

        print(f"{'ID':<22} {'标题':<35} {'消息':>5} {'Tokens':>8} {'状态':<10} {'最后活跃':<20}")
        print("-" * 100)
        for s in sessions:
            updated = datetime.fromtimestamp(s.updated_at).strftime("%m-%d %H:%M")
            print(f"{s.id:<22} {s.title[:34]:<35} {s.message_count:>5} {s.total_tokens:>8} {s.status:<10} {updated:<20}")
        return 0

    elif args.cmd == "browse":
        query = args.query or ""
        if query:
            results = store.search_sessions(query, limit=20)
        else:
            results = store.list_sessions(limit=20)

        if not results:
            print("没有匹配的会话。")
            return 0

        print(f"{'ID':<22} {'标题':<35} {'消息':>5} {'状态':<10}")
        print("-" * 75)
        for s in results:
            print(f"{s.id:<22} {s.title[:34]:<35} {s.message_count:>5} {s.status:<10}")
        return 0

    elif args.cmd == "export":
        if not args.session_id:
            print("用法: kuafu sessions export <session_id>")
            return 1
        exported = store.export_session(args.session_id)
        if exported:
            print(exported)
        else:
            print(f" 未找到会话: {args.session_id}")
        return 0

    elif args.cmd == "delete":
        if not args.session_id:
            print("用法: kuafu sessions delete <session_id>")
            return 1
        store.delete_session(args.session_id)
        print(f" 已删除会话: {args.session_id}")
        return 0

    elif args.cmd == "prune":
        days = args.days or 30
        count = store.prune_sessions(keep_days=days)
        print(f" 已清理 {count} 个 {days} 天前的归档会话")
        return 0

    elif args.cmd == "stats":
        stats = store.get_stats()
        print("会话统计")
        print(f"   总会话数: {stats['total_sessions']}")
        print(f"   活跃会话: {stats['active_sessions']}")
        print(f"   总消息数: {stats['total_messages']}")
        print(f"   总 Tokens: {stats['total_tokens_estimated']}")
        return 0

    else:
        print(f"未知的 sessions 子命令: {args.cmd}")
        print("可用命令: list, browse, export, delete, prune, stats")
        return 1


def run_status(args: argparse.Namespace, agent: Any) -> int:
    """kuafu status — 查看夸父状态。"""
    status = agent.get_status() if hasattr(agent, 'get_status') else {}
    print(f"夸父 Kuafu v{getattr(agent, 'version', '?')}")
    print(f"   LLM: {agent.llm.model}")
    print(f"   后端: {getattr(agent.llm, 'backend', '?')}")
    print(f"   任务计数: {getattr(agent, '_task_count', 0)}")
    print()
    if status.get("evolution"):
        evo = status["evolution"]
        print(f"进化统计")
        print(f"   总进化次数: {evo.get('total_evolutions', 0)}")
        print(f"   最近事件: {evo.get('last_event', {}).get('action', '无')}")
    print()
    try:
        print(f"技能文件: {len(list((ROOT_DIR / 'skills').glob('*.yaml')))}")
        prefs_path = ROOT_DIR / "memory" / "user_prefs.json"
        if prefs_path.exists():
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            print(f"用户偏好: {len(prefs)} 条")
    except Exception:
        pass
    return 0


def run_model(args: argparse.Namespace, agent: Any) -> int:
    """kuafu model — 模型切换/查看。"""
    if args.cmd == "switch":
        backend = args.backend or "cloud"
        if hasattr(agent.llm, 'switch') and callable(agent.llm.switch):
            agent.llm.switch(backend)
            print(f"已切换到后端: {backend}")
        else:
            print(f"LLM 不支持运行时切换")
        return 0

    elif args.cmd == "list":
        print(f"当前: {agent.llm.model} ({getattr(agent.llm, 'backend', '?')})")
        return 0

    else:
        print(f"当前: {agent.llm.model} ({getattr(agent.llm, 'backend', '?')})")
        print("可用: kuafu model list / kuafu model switch cloud|local")
        return 0


def run_kfskill(args: argparse.Namespace, agent: Any = None) -> int:
    """kuafu kfskill — 技能包操作。"""
    from core.kfskill import (
        create_skill, save_skill, load_skill, validate_kfskill,
        export_to_json, KFSKILL_SPECIFICATION
    )
    from pathlib import Path

    cmd = args.cmd

    if cmd == "create":
        name = args.name
        description = args.description or f"通过 CLI 创建的技能: {name}"
        category = args.category
        author = args.author or os.environ.get("USER", "")
        version = args.version or "1.0.0"
        keywords = list(args.keywords) if args.keywords else []

        # 交互式输入步骤
        print(f"创建技能包: {name}")
        print(f"描述: {description}")
        print(f"分类: {category or '(未设置)'}")
        print()
        print("请输入执行步骤（每行一步，空行结束）:")
        steps = []
        i = 1
        while True:
            try:
                line = input(f"  step {i}: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            steps.append(line)
            i += 1

        if not steps:
            print("❌ 至少需要 1 个执行步骤")
            return 1

        print("请输入注意事项/陷阱（每行一项，空行结束，直接回车跳过）:")
        pitfalls = []
        i = 1
        while True:
            try:
                line = input(f"  pitfall {i}: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            pitfalls.append(line)
            i += 1

        result = create_skill(
            name=name, description=description, steps=steps,
            category=category, keywords=keywords,
            pitfalls=pitfalls if pitfalls else None,
            version=version, author=author,
            source="manual",
        )
        if not result["success"]:
            print(f"❌ 创建失败: {result.get('error', '未知错误')}")
            return 1

        save_result = save_skill(result["data"])
        if save_result["success"]:
            print(f"✅ 技能包已创建: {save_result['path']}")
        else:
            print(f"❌ 保存失败: {save_result.get('error', '未知错误')}")
        return 0

    elif cmd == "list":
        from core.skill_manager import SkillManager
        mgr = SkillManager()
        local = mgr.list_local()
        category_filter = args.category or ""
        if category_filter:
            filtered = [s for s in local if s.category == category_filter]
        else:
            filtered = local

        if not filtered:
            print(f"无技能包{'（分类: ' + category_filter + '）' if category_filter else ''}")
            return 0

        print(f"技能包列表 ({len(filtered)}):")
        print(f"  {'名称':<30} {'分类':<12} {'版本':<10} {'步骤':<5} {'使用':<5}")
        print(f"  {'-'*62}")
        for s in filtered:
            ver = getattr(s, 'version', '?') or '?'
            print(f"  {s.name:<30} {s.category or 'general':<12} {ver:<10} {s.steps:<5} {s.usage_count:<5}")
        return 0

    elif cmd == "validate":
        file_path = args.file
        result = load_skill(file_path)
        if result["success"]:
            data = result["data"]
            print(f"✅ {data['name']} (v{data.get('version', '?')})")
            print(f"   描述: {data['description']}")
            print(f"   分类: {data.get('category', '—')}")
            print(f"   步骤: {len(data.get('steps', []))} 个")
            print(f"   格式: 合法")
        else:
            print(f"❌ 验证失败: {result.get('error', '未知错误')}")
            if "data" in result:
                data = result["data"]
                print(f"   名称: {data.get('name', '?')}")
                print(f"   步骤: {len(data.get('steps', []))} 个")
        return 0

    elif cmd == "export":
        name = args.name
        from core.skill_manager import SkillManager
        mgr = SkillManager()
        local = mgr.list_local()

        skill = None
        for s in local:
            if s.name == name:
                skill = s
                break

        if not skill:
            print(f"❌ 未找到技能: {name}")
            return 1

        # 读取原文件
        file_path = Path(skill.file_path) if Path(skill.file_path).is_absolute() else Path(skill.file_path)
        result = load_skill(str(file_path))
        if not result["success"]:
            print(f"❌ 读取失败: {result.get('error', '未知错误')}")
            return 1

        data = result["data"]
        json_out = export_to_json(data)
        print(f"📦 {data['name']} — kfskill v{data.get('version', '1.0.0')}")
        print(f"   描述: {data['description']}")
        print(f"   作者: {data.get('author', '—')}")
        print(f"   分类: {data.get('category', '—')}")
        print(f"   关键词: {', '.join(data.get('keywords', []))}")
        print(f"   步骤数: {len(data.get('steps', []))}")
        print(f"   使用次数: {data.get('usage_count', 0)}")
        print(f"   来源: {data.get('source', '?')}")
        print(f"\n   文件: {file_path}")
        return 0

    elif cmd == "info":
        name = args.name

        # 先尝试作为文件路径
        path = Path(name)
        if path.exists() and path.suffix in (".yaml", ".yml", ".kfskill"):
            result = load_skill(str(path))
        else:
            # 按名称搜索本地技能
            from core.skill_manager import SkillManager
            mgr = SkillManager()
            local = mgr.list_local()
            found = None
            for s in local:
                if s.name == name:
                    file_path = Path(s.file_path) if Path(s.file_path).is_absolute() else Path(s.file_path)
                    result = load_skill(str(file_path))
                    found = True
                    break
            if not found:
                print(f"❌ 未找到技能: {name}")
                return 1

        if not result["success"]:
            print(f"❌ 加载失败: {result.get('error', '未知错误')}")
            return 1

        data = result["data"]
        print(f"📦 {data['name']}")
        print(f"   版本: {data.get('version', '?')}")
        print(f"   描述: {data['description']}")
        print(f"   作者: {data.get('author', '—')}")
        print(f"   分类: {data.get('category', '—')}")
        print(f"   关键词: {', '.join(data.get('keywords', [])) or '—'}")
        print(f"   创建于: {time.strftime('%Y-%m-%d %H:%M', time.localtime(data.get('created_at', 0)))}")
        print(f"   使用次数: {data.get('usage_count', 0)}")
        print(f"   来源: {data.get('source', '?')}")
        print(f"\n   执行步骤 ({len(data.get('steps', []))} 步):")
        for i, step in enumerate(data.get("steps", []), 1):
            print(f"     {i}. {step}")
        pitfalls = data.get("pitfalls", [])
        if pitfalls:
            print(f"\n   注意事项 ({len(pitfalls)}:{' '})")
            for p in pitfalls:
                print(f"     ⚠  {p}")
        deps = data.get("dependencies", {})
        if deps:
            print(f"\n   依赖:")
            for dk, dv in deps.items():
                print(f"     {dk}: {', '.join(dv) if isinstance(dv, list) else dv}")
        return 0

    return 0

def run_skill(args: argparse.Namespace, agent: Any = None) -> int:
    """kuafu skill — 技能管理（本地/市场/安装/卸载/版本/退化/回滚）。"""
    from core.skill_manager import SkillManager
    from pathlib import Path
    import time as _time
    ROOT_DIR = Path(__file__).resolve().parent.parent
    SKILLS_DIR = ROOT_DIR / "skills"

    mgr = SkillManager()

    cmd = args.cmd

    # ── list ──
    if cmd == "list":
        local = mgr.list_local()
        installed = mgr.list_installed_market()

        # 按分类筛选（需从 kfskill 读取 category）
        category_filter = getattr(args, "category", "")

        if category_filter:
            filtered = []
            for s in local:
                try:
                    import yaml
                    fpath = Path(s.file_path) if Path(s.file_path).is_absolute() else ROOT_DIR / s.file_path
                    data = yaml.safe_load(fpath.read_text(encoding="utf-8"))
                    if data and data.get("category", "") == category_filter:
                        filtered.append(s)
                except Exception:
                    filtered.append(s)
            local = filtered

        print(f"📦 技能列表 (共 {len(local)} 个)")
        print(f"   {'名称':<30} {'分类':<12} {'步骤':<5} {'使用':<6} {'描述'}")
        print(f"   {'-'*80}")
        for s in local:
            cat = getattr(s, 'category', '') or 'general'
            desc = s.description[:40] if s.description else ''
            print(f"   {s.name[:28]:<30} {cat:<12} {s.steps:<5} {s.usage_count:<6} {desc}")
        if installed:
            print(f"\n📦 市场安装 ({len(installed)}):")
            for s in installed:
                print(f"   {s.name[:28]:<30} {s.description[:50]}")
        return 0

    # ── search ──
    elif cmd == "search":
        query = getattr(args, "query", "") or ""
        if not query:
            market = mgr.fetch_market_index()
            print(f"🌐 技能市场 ({len(market)} 个可用):")
            print(f"   {'名称':<30} {'分类':<12} {'作者':<16} {'描述'}")
            print(f"   {'-'*80}")
            for s in market[:25]:
                print(f"   {s.name[:28]:<30} {(s.category or '?'):<12} {(s.author or '?'):<16} {s.description[:40]}")
            return 0

        local = mgr.search_local(query)
        market = mgr.search_market(query)
        if not local and not market:
            print(f"🔍 未找到匹配「{query}」的技能")
            return 0

        if local:
            print(f"📦 本地匹配 ({len(local)}):")
            for s in local:
                print(f"   {s.name:30s} {s.description[:50]}")
        if market:
            print(f"\n🌐 市场匹配 ({len(market)}):")
            for s in market:
                print(f"   {s.name:30s} [{s.author or '?'}] {s.description[:50]}")
        return 0

    # ── install ──
    elif cmd == "install":
        name = getattr(args, "name", "") or ""
        if not name:
            print("用法: kuafu skill install <skill_name|url>")
            return 1

        # 如果是本地 .yaml/.kfskill 文件路径，直接复制
        p = Path(name)
        if p.exists() and p.suffix in (".yaml", ".yml", ".kfskill"):
            dest = SKILLS_DIR / p.name
            import shutil
            shutil.copy2(str(p), str(dest))
            # 读取名称
            try:
                import yaml
                data = yaml.safe_load(dest.read_text(encoding="utf-8"))
                skill_name = data.get("name", dest.stem) if data else dest.stem
                print(f"✅ 已安装本地技能包: {skill_name}")
                print(f"   文件: {dest}")
            except Exception:
                print(f"✅ 已复制: {dest}")
            return 0

        result = mgr.install(name)
        if result["success"]:
            print(f"✅ 已安装: {result['name']}")
            fp = result.get("file", "")
            if fp:
                print(f"   路径: {fp}")
        else:
            print(f"❌ 安装失败: {result.get('error', '未知错误')}")
        return 0

    # ── remove ──
    elif cmd == "remove":
        name = getattr(args, "name", "") or ""
        if not name:
            print("用法: kuafu skill remove <skill_name>")
            return 1
        if mgr.remove_local(name) or mgr.uninstall(name):
            print(f"🗑️  已删除: {name}")
        else:
            print(f"未找到: {name}")
        return 0

    # ── stats ──
    elif cmd == "stats":
        stats = mgr.get_stats()
        print("📊 技能统计:")
        print(f"   本地: {stats['local']} 个")
        print(f"   市场安装: {stats['installed_market']} 个")
        print(f"   市场可用: {stats['available_market']} 个")
        print(f"   远程仓库: {stats.get('repos', 0)} 个 ({stats.get('repo_skills', 0)} 技能)")

        # 分类统计
        from collections import Counter
        cats = Counter()
        local = mgr.list_local()
        for s in local:
            try:
                fpath = Path(s.file_path) if Path(s.file_path).is_absolute() else ROOT_DIR / s.file_path
                import yaml
                data = yaml.safe_load(fpath.read_text(encoding="utf-8"))
                cat = (data or {}).get("category", "general") or "general"
            except Exception:
                cat = "general"
            cats[cat] += 1
        if cats:
            print(f"\n   分类分布:")
            for cat, cnt in cats.most_common():
                print(f"     {cat:<15} {cnt} 个")
        return 0

    # ── version ──
    elif cmd == "version":
        from core.evolution_tracker import EvolutionTracker
        tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))

        show_names = []
        if getattr(args, "name", None):
            show_names = [args.name]
        else:
            show_names = sorted(tracker.get_all_skills().keys())

        if not show_names:
            print("没有技能版本记录。")
            print("  扫描: kuafu skill scan")
            return 0

        for name in show_names:
            history = tracker.get_evolution_history(name)
            if history:
                print(f"📋 {name}")
                print(f"   {'版本':<6} {'模式':<12} {'摘要':<30} {'时间':<20}")
                print(f"   {'-'*68}")
                for h in history:
                    ts = _time.strftime("%m-%d %H:%M", _time.localtime(h["created_at"]))
                    summary = (h["summary"] or "")[:28]
                    parent = f"←{h['parent']}" if h.get("parent") else ""
                    print(f"   v{h['version']:<4} {h['mode']:<12} {summary:<30} {ts:<20}")
                print()
            else:
                print(f"📋 {name} (无版本记录)")
        return 0

    # ── scan ──
    elif cmd == "scan":
        from core.evolution_tracker import EvolutionTracker
        tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))
        result = tracker.scan_skills_directory()
        print(f"📂 扫描完成: {result['scanned']} 个文件")
        print(f"   新增: {result['new']} | 更新: {result['updated']} | 未变更: {result['unchanged']}")
        for d in result.get("details", []):
            status = d.get("status", "")
            if status == "error":
                print(f"   ❌ {d.get('file', '?')}: {d.get('error', '?')}")
            elif status == "new":
                print(f"   🆕 {d.get('name', '?')} v{d.get('version', '?')}")
            elif status == "updated":
                print(f"   🔄 {d.get('name', '?')} v{d.get('version', '?')}")
        return 0

    # ── diff ──
    elif cmd == "diff":
        if not getattr(args, "name", None):
            print("用法: kuafu skill diff <skill_name> [v1] [v2]")
            return 1
        from core.evolution_tracker import EvolutionTracker
        tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))
        v1 = getattr(args, "v1", None) or 1
        v2 = getattr(args, "v2", None) or 2
        diff = tracker.diff_skill_versions(args.name, v1, v2)
        if diff is None:
            print(f"版本 {v1} 或 {v2} 不存在")
        else:
            print(f"差异 {args.name} v{v1} ↔ v{v2}:")
            print(diff)
        return 0

    # ── restore ──
    elif cmd == "restore":
        if not getattr(args, "name", None) or not hasattr(args, "version") or not args.version:
            print("用法: kuafu skill restore <skill_name> <version>")
            return 1
        from core.evolution_tracker import EvolutionTracker
        tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))
        ok = tracker.restore_skill_file(args.name, args.version)
        if ok:
            print(f"✅ 已恢复 {args.name} 到 v{args.version}")
        else:
            print(f"❌ 恢复失败: 版本 {args.version} 不存在")
        return 0

    # ── log ──
    elif cmd == "log":
        from core.evolution_tracker import EvolutionTracker
        tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))
        stats = tracker.get_stats()
        print(f"📊 进化追踪统计:")
        print(f"   技能版本链: {stats['total_skills']} 个技能")
        print(f"   任务类型: {stats['total_task_types']} 种")
        print(f"   已知错误: {stats['known_errors']} 个")
        print(f"   进化事件: {stats['total_events']} 条")
        print()
        events = tracker.get_recent_events(limit=8)
        if events:
            print("最近进化事件:")
            print(f"   {'等级':<10} {'动作':<40} {'时间':<15}")
            print(f"   {'-'*65}")
            for e in events:
                ts = _time.strftime("%m-%d %H:%M", _time.localtime(e["created_at"]))
                action = (e["action"] or "")[:38]
                print(f"   {e['level']:<10} {action:<40} {ts:<15}")
        return 0

    # ── degrade ──
    elif cmd == "degrade":
        from core.evolution_tracker import EvolutionTracker
        tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))

        if getattr(args, "name", None):
            failures = getattr(args, "failures", None)
            result = tracker.detect_degradation(args.name, recent_task_failures=failures)
            if result:
                print(f"⚠️  {args.name} 退化检测: {result['severity']}")
                for s in result["signals"]:
                    print(f"   📉 {s}")
                if result.get("best_version"):
                    print(f"   当前: v{result['current_version']} → 最佳: v{result['best_version']}")
                if result.get("suggested_action"):
                    print(f"   💡 {result['suggested_action']}")
            else:
                print(f"✅ {args.name} 未检测到退化")
        else:
            results = tracker.detect_all_degradations()
            if results:
                print(f"退化检测: {len(results)} 个技能检测到退化信号")
                for r in results:
                    print(f"   ⚠️  {r['skill_name']}: {r['severity']} — {'; '.join(r['signals'])}")
                    if r.get("suggested_action"):
                        print(f"       💡 {r['suggested_action']}")
            else:
                print("✅ 未检测到任何技能退化")
        return 0

    # ── rollback ──
    elif cmd == "rollback":
        from core.evolution_tracker import EvolutionTracker
        tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))

        if getattr(args, "name", None):
            result = tracker.auto_rollback(args.name)
            if result:
                print(f"✅ 已回滚 {args.name} v{result['from_version']} → v{result['to_version']}")
                print(f"   严重度: {result['severity']}")
                for s in result.get("signals", []):
                    print(f"   📉 {s}")
                if result.get("backup_file"):
                    print(f"   💾 备份: {result['backup_file']}")
            else:
                print(f"ℹ️  {args.name} 无需回滚")
        else:
            results = tracker.auto_rollback_all()
            if results:
                print(f"自动回滚完成: {len(results)} 个技能已回滚")
                for r in results:
                    print(f"   ✅ {r['skill_name']} v{r['from_version']} → v{r['to_version']} ({r['severity']})")
            else:
                print("✅ 无需回滚")

        # 回滚后重新扫描同步版本链
        tracker.scan_skills_directory()
        return 0

    # ── deps ──
    elif cmd == "deps":
        name = getattr(args, "name", "") or ""
        if not name:
            print("用法: kuafu skill deps <skill_name> [--install]")
            return 1

        from core.skill_deps import (
            get_deps_from_skill, check_dependencies,
            install_dependencies, suggest_command, verify_installation,
        )
        import shutil

        info = get_deps_from_skill(name)
        if not info["exists"]:
            print(f"❌ 未找到技能: {name}")
            return 1

        deps = info["dependencies"]
        if not deps:
            print(f"📦 {name} 没有声明的依赖")
            return 0

        print(f"📦 {name} 依赖检查:")
        print()

        # 显示依赖声明
        tools = deps.get("tools", [])
        packages = deps.get("packages", [])
        env_vars = deps.get("env", [])
        notes = deps.get("notes", [])

        if tools:
            print(f"   系统工具 ({len(tools)}):")
            for t in tools:
                installed = "✅" if shutil.which(t) else "❌"
                print(f"     {installed} {t}")
            print()
        if packages:
            print(f"   Python 包 ({len(packages)}):")
            for p in packages:
                from core.skill_deps import _parse_package_spec, _check_package
                pkg_name, _ = _parse_package_spec(p)
                ok = _check_package(pkg_name)
                icon = "✅" if ok else "❌"
                print(f"     {icon} {p}")
            print()
        if env_vars:
            print(f"   环境变量 ({len(env_vars)}):")
            for e in env_vars:
                ok = bool(os.environ.get(e))
                icon = "✅" if ok else "❌"
                print(f"     {icon} {e}")
            print()
        if notes:
            print(f"   说明:")
            for n in notes:
                print(f"     📝 {n}")
            print()

        # 检查结果
        check = check_dependencies(info.get("data", {"dependencies": deps}))
        if check.ok:
            print("   ✅ 所有依赖已满足")
        else:
            print(f"   {check.summary()}")

        # 安装建议
        cmd_suggest = suggest_command({"dependencies": deps})
        if cmd_suggest:
            print()
            print("   💡 安装命令:")
            print(f"      {cmd_suggest}")

        # --install 参数
        if getattr(args, "install", False):
            print()
            print("   🔄 正在安装缺失依赖...")
            install_result = install_dependencies(
                {"dependencies": deps}, auto_confirm=True
            )
            if install_result["installed"]:
                print(f"   ✅ 已安装: {', '.join(install_result['installed'])}")
            if install_result["skipped"]:
                print(f"   ⏭️  跳过: {', '.join(install_result['skipped'])}")
            if install_result["failed"]:
                for pkg, err in install_result["failed"]:
                    print(f"   ❌ {pkg} 安装失败: {err}")
            if install_result["warnings"]:
                for w in install_result["warnings"]:
                    print(f"   ⚠️  {w}")
        return 0

    # ── sandbox ──
    elif cmd == "sandbox":
        name = getattr(args, 'name', '') or ''
        if not name:
            print('用法: kuafu skill sandbox <skill_name>  (沙箱功能已移除)')
            return 1

        print('\U0001f6e1\ufe0f  技能沙箱功能已移除，安全策略由 SafetyLayer + PolicyManager 统一管理。')
        return 0

    # ---- edit ----
    elif cmd == "edit":
        name = getattr(args, "name", "") or ""
        if not name:
            print("用法: kuafu skill edit <skill_name>")
            return 1

        # 查找技能文件（本地 + 市场安装）
        local = mgr.list_local()
        installed = mgr.list_installed_market()
        found = None
        for s in local + installed:
            if s.name == name:
                found = s
                break
        if not found:
            print(f"❌ 未找到技能: {name}")
            return 1

        fpath = Path(found.file_path) if Path(found.file_path).is_absolute() else ROOT_DIR / found.file_path
        if not fpath.exists():
            print(f"❌ 文件不存在: {fpath}")
            return 1

        # 用 $EDITOR 或默认 vi
        editor = os.environ.get("EDITOR", "vi")
        import subprocess
        try:
            subprocess.call([editor, str(fpath)])
        except FileNotFoundError:
            print(f"❌ 找不到编辑器: {editor}，请设置 $EDITOR 环境变量")
            return 1
        print(f"✅ 已保存: {fpath}")
        return 0

    # ── info ──
    elif cmd == "info":
        name = getattr(args, "name", "") or ""
        if not name:
            print("用法: kuafu skill info <skill_name>")
            return 1

        # 查找技能文件（本地 + 市场安装）
        local = mgr.list_local()
        installed = mgr.list_installed_market()
        found = None
        for s in local + installed:
            if s.name == name:
                found = s
                break
        if not found:
            print(f"❌ 未找到技能: {name}")
            return 1

        try:
            import yaml
            fpath = Path(found.file_path) if Path(found.file_path).is_absolute() else ROOT_DIR / found.file_path
            data = yaml.safe_load(fpath.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"❌ 读取失败: {e}")
            return 1

        print(f"📦 {data.get('name', name)}")
        if data.get("version"):
            print(f"   版本: {data['version']}")
        print(f"   描述: {data.get('description', '—')}")
        if data.get("category"):
            print(f"   分类: {data['category']}")
        if data.get("author"):
            print(f"   作者: {data['author']}")
        if data.get("keywords"):
            print(f"   关键词: {', '.join(data['keywords'][:8])}")
        if data.get("created_at"):
            print(f"   创建于: {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(data['created_at']))}")
        if data.get("usage_count") is not None:
            print(f"   使用次数: {data['usage_count']}")
        print(f"\n   执行步骤 ({len(data.get('steps', []))} 步):")
        for i, step in enumerate(data.get("steps", []), 1):
            print(f"     {i}. {step}")
        pitfalls = data.get("pitfalls", [])
        if pitfalls:
            print(f"\n   注意事项:")
            for p in pitfalls:
                print(f"     ⚠  {p}")
        deps = data.get("dependencies", {})
        if deps:
            print(f"\n   依赖:")
            for dk, dv in deps.items():
                if isinstance(dv, list):
                    print(f"     {dk}: {', '.join(dv)}")
                else:
                    print(f"     {dk}: {dv}")
        print(f"\n   文件: {fpath}")
        return 0

    # ── publish ──
    elif cmd == "publish":
        name = getattr(args, "name", "") or ""
        if not name:
            print("用法: kuafu skill publish <skill_name> [选项]")
            return 1

        mode = getattr(args, "mode", "") or "local"
        bump = getattr(args, "bump", "patch")

        from core.skill_publisher import (
            validate_skill, package_skill,
            publish_to_github, publish_to_local,
            get_next_version,
        )
        from core.kfskill import load_skill

        # 检查 gh CLI（如果是 release 模式）
        import shutil
        if mode == "release" and not shutil.which("gh"):
            print("❌ Release 模式需要安装 gh CLI")
            print("  brew install gh  # macOS")
            print("  apt install gh   # Linux")
            return 1

        # 阶段 1: 打包
        print(f"📦 打包技能: {name}")
        result = package_skill(name)
        if not result["success"]:
            print(f"❌ 打包失败:")
            print(result.get("error", "未知错误"))
            # 如果有验证报告，打印详情
            report = result.get("report")
            if report:
                print()
                print(report.summary())
            return 1

        plan = result["plan"]
        report = result["report"]

        # 打印验证报告
        print()
        print("📋 验证报告:")
        for check_name, check_data in report.checks.items():
            icon = "✅" if check_data["ok"] else "⚠️"
            detail = f" — {check_data['detail']}" if check_data["detail"] else ""
            print(f"   {icon} {check_name}{detail}")

        print()
        print(f"📤 发布计划:")
        print(f"   技能: {plan.skill_name}")
        print(f"   版本: {plan.version}")
        print(f"   文件: {plan.file_path}")
        print(f"   校验: {plan.checksum}")
        print(f"   URL:  {plan.url}")
        print()

        # 阶段 2: 发布
        if mode == "github":
            # 推送到 GitHub 仓库
            print("🔄 推送到 GitHub 市场仓库...")
            create_release = getattr(args, "release", False)
            result = publish_to_github(plan, create_release=create_release)
            if result["success"]:
                print(result["message"])
                print(f"   URL: {result['skill_url']}")
                if result.get("release") and result["release"].get("url"):
                    print(f"   Release: {result['release']['url']}")
            else:
                print(f"❌ GitHub 发布失败: {result.get('error', '未知错误')}")
                return 1

        elif mode == "release":
            # 推送到 GitHub 并创建 Release
            print("🔄 创建 GitHub Release...")
            result = publish_to_github(plan, create_release=True)
            if result["success"]:
                print(result["message"])
                print(f"   URL: {result['skill_url']}")
                if result.get("release") and result["release"].get("url"):
                    print(f"   Release: {result['release']['url']}")
            else:
                print(f"❌ Release 发布失败: {result.get('error', '未知错误')}")
                return 1

        else:  # local（默认）
            # 发布到本地文件
            output = getattr(args, "output", "") or "/tmp/kuafu-skill-market"
            result = publish_to_local(
                str(Path(output) / "index.json"), plan
            )
            if result["success"]:
                print(result["message"])
                print(f"   索引: {result['index_path']}")
                print(f"   技能: {result['skill_path']}")
            else:
                print(f"❌ 本地发布失败: {result.get('error', '未知错误')}")
                return 1

        # 版本提示
        next_version = get_next_version(name, bump=bump)
        print()
        print(f"💡 下次发布: kuafu skill publish \"{name}\" --bump {bump}")
        print(f"   新版本: {next_version}")
        return 0

    else:
        print("用法: kuafu skill <子命令> [选项]")
        print()
        print("管理命令:")
        print("  list [--category]    列出本地技能（支持分类筛选）")
        print("  search <关键词>      搜索本地和远程市场")
        print("  install <名称|URL>   安装技能（本地 .yaml/.kfskill 或远程市场）")
        print("  remove <名称>        删除技能")
        print("  stats                技能统计（含分类分布）")
        print()
        print("发布共享:")
        print("  publish <名称> [--mode local|github|release] 完整发布技能（验证→打包→发布）")
        print("           [--bump patch|minor|major] 版本递增")
        print()
        print("版本管理:")
        print("  version [名称]       查看版本链")
        print("  scan                 扫描 skills/ 同步版本链")
        print("  diff <名称> [v1] [v2] 比较版本差异")
        print("  restore <名称> <版本> 恢复指定版本")
        print("  log                  查看进化追踪日志")
        print()
        print("质量管理:")
        print("  degrade [名称]       检测技能退化")
        print("  rollback [名称]      自动回滚退化技能")
        print()
        print("编辑查看:")
        print("  info <名称>          查看技能详情")
        print("  edit <名称>          用 $EDITOR 编辑技能文件")
        print("  deps <名称> [--install] 检查/安装技能依赖")
        print("  sandbox <名称>       查看技能沙箱配置")
        return 0


def run_repo(args: argparse.Namespace, agent: Any = None) -> int:
    """kuafu repo — 远程技能仓库管理。"""
    from core.skill_repo import RepoManager

    mgr = RepoManager()
    cmd = args.cmd

    # ── list ──
    if cmd == "list":
        repos = mgr.list_repos()
        if not repos:
            print("❌ 未配置任何远程仓库")
            print("  添加: kuafu repo add <名称> <URL>")
            return 0

        print(f"📡 远程技能仓库 ({len(repos)}):")
        print(f"   {'名称':<20} {'状态':<8} {'缓存':<8} {'描述'}")
        print(f"   {'-'*65}")
        for r in repos:
            repo_obj = mgr.get_repo(r["name"])
            cached = repo_obj and repo_obj.cache_path.exists()
            status = "✅" if r["enabled"] else "⬜"
            cache_str = "已缓存" if cached else "—"
            desc = r["description"][:35]
            print(f"   {r['name']:<20} {status:<8} {cache_str:<8} {desc}")
        print()
        stats = mgr.get_stats()
        print(f"   总计: {stats['total_repos']} 个仓库, {stats['total_skills']} 个技能")
        return 0

    # ── add ──
    elif cmd == "add":
        name = getattr(args, "name", "") or ""
        url = getattr(args, "url", "") or ""
        if not name or not url:
            print("用法: kuafu repo add <名称> <仓库URL>")
            return 1

        description = getattr(args, "description", "") or ""
        result = mgr.add_repo(name, url, description)
        if result["success"]:
            print(f"✅ 已添加仓库: {name}")
            print(f"   URL: {url}")
            print(f"   可达: {'是' if result.get('reachable') else '否，但已添加'}")
            if result.get("skills_count", 0) > 0:
                print(f"   技能数: {result['skills_count']}")
        else:
            print(f"❌ 添加失败: {result.get('error', '未知错误')}")
        return 0

    # ── remove ──
    elif cmd == "remove":
        name = getattr(args, "name", "") or ""
        if not name:
            print("用法: kuafu repo remove <名称>")
            return 1
        if mgr.remove_repo(name):
            print(f"🗑️  已移除仓库: {name}")
        else:
            print(f"未找到仓库: {name}")
        return 0

    # ── search ──
    elif cmd == "search":
        query = getattr(args, "query", "") or ""
        if not query:
            # 列出所有仓库的可用技能
            all_skills = mgr.list_all_skills()
            if not all_skills:
                print("❌ 无可用技能（仓库可能不可达）")
                return 0
            print(f"📦 远程技能 (共 {len(all_skills)} 个):")
            print(f"   {'技能名称':<30} {'版本':<10} {'仓库':<16} {'分类':<12} {'描述'}")
            print(f"   {'-'*85}")
            for s in all_skills[:30]:
                print(f"   {s['name'][:28]:<30} {s['version']:<10} {s['repo'][:14]:<16} "
                      f"{(s['category'] or '?'):<12} {s['description'][:30]}")
            if len(all_skills) > 30:
                print(f"   ... 还有 {len(all_skills) - 30} 个")
            return 0

        results = mgr.search(query)
        if not results:
            print(f"🔍 远程仓库未找到匹配「{query}」的技能")
            return 0

        print(f"🔍 远程搜索「{query}」(找到 {len(results)} 个):")
        print(f"   {'技能名称':<30} {'仓库':<16} {'版本':<8} {'分类':<10} {'描述'}")
        print(f"   {'-'*85}")
        for s in results[:20]:
            print(f"   {s['name'][:28]:<30} {s['repo'][:14]:<16} {s['version']:<8} "
                  f"{(s['category'] or '?'):<10} {s['description'][:32]}")
        return 0

    # ── refresh ──
    elif cmd == "refresh":
        results = mgr.refresh_all()
        if not results:
            print("❌ 无仓库可刷新")
            return 0
        print("🔄 刷新仓库缓存:")
        for r in results:
            icon = "✅" if r["success"] else "❌"
            print(f"   {icon} {r['name']}: {r.get('source', '?')} ({r['skills_count']} 技能)")
        return 0

    # ── stats ──
    elif cmd == "stats":
        stats = mgr.get_stats()
        print(f"📊 远程仓库统计:")
        print(f"   仓库数: {stats['total_repos']}")
        print(f"   远程技能数: {stats['total_skills']}")
        print()
        for r in stats["repos"]:
            cache_info = ""
            if r["cache_age_sec"] >= 0:
                mins = r["cache_age_sec"] // 60
                cache_info = f"(缓存 {mins} 分钟前)"
            status_icon = "✅" if r["status"] == "ok" else "⏳"
            print(f"   {status_icon} {r['name']}")
            print(f"      技能: {r['skills']} 个 {cache_info}")
            print(f"      URL: {r['url']}")
        return 0

    # ── clear-cache ──
    elif cmd == "clear-cache":
        name = getattr(args, "name", "") or ""
        cleared = mgr.clear_cache(name=name if name else None)
        if cleared > 0:
            print(f"🧹 已清理 {cleared} 个缓存文件")
        else:
            print("无缓存可清理")
        return 0

    else:
        print("用法: kuafu repo <子命令> [选项]")
        print()
        print("仓库管理:")
        print("  list                  列出所有已配置的远程仓库")
        print("  add <名称> <URL>      添加远程仓库")
        print("  remove <名称>         移除仓库")
        print()
        print("技能搜索:")
        print("  search [关键词]       搜索所有远程仓库的技能")
        print()
        print("维护:")
        print("  refresh               强制刷新所有仓库缓存")
        print("  stats                 查看仓库状态统计")
        print("  clear-cache [名称]    清理缓存文件")
        return 0


def run_tools(args: argparse.Namespace, agent: Any) -> int:
    """kuafu tools — 工具集管理。"""
    from core.tool_registry import ToolRegistry

    registry = getattr(agent, '_tools', None) or getattr(agent, 'tool_registry', None)
    if registry is None:
        loop = getattr(agent, '_loop', None)
        if loop and hasattr(loop, 'tools'):
            registry = loop.tools
    if registry is None:
        registry = ToolRegistry()

    if args.cmd == "list":
        core_names = sorted(registry.list_tools())
        compact_names = sorted(s["function"]["name"] for s in getattr(registry, '_compact', []))
        deferred_names = sorted(
            s["schema"]["function"]["name"] for s in getattr(registry, '_deferred', [])
        )
        print(f"核心工具 ({len(core_names)}):")
        for n in core_names:
            print(f"  {n}")
        print(f"\n紧凑工具 ({len(compact_names)}):")
        for n in compact_names:
            print(f"  {n}")
        print(f"\n延迟工具 ({len(deferred_names)}):")
        for n in deferred_names:
            print(f"  {n}")
        return 0

    elif args.cmd == "enable":
        name = args.name or ""
        if not name:
            print("用法: kuafu tools enable <tool_name>")
            return 1
        for entry in getattr(registry, '_deferred', []):
            if entry["schema"]["function"]["name"] == name:
                registry.inject_tool(name)
                print(f"已启用: {name}")
                return 0
        for s in getattr(registry, '_compact', []):
            if s["function"]["name"] == name:
                registry._promote_compact_tool(name)
                print(f"已提升: {name}")
                return 0
        for s in getattr(registry, '_schemas', []):
            if s["function"]["name"] == name:
                print(f"{name} 是核心工具，始终可用")
                return 0
        print(f"未找到工具: {name}")
        return 1

    elif args.cmd == "disable":
        name = args.name or ""
        if not name:
            print("用法: kuafu tools disable <tool_name>")
            return 1
        for s in getattr(registry, '_schemas', []):
            if s["function"]["name"] == name:
                print(f"不能禁用核心工具: {name}")
                return 1
        injected = getattr(registry, '_injected_tools', [])
        before = len(injected)
        registry._injected_tools = [s for s in injected if s["function"]["name"] != name]
        if len(registry._injected_tools) < before:
            print(f"已禁用: {name}")
            return 0
        compact = getattr(registry, '_compact', [])
        before = len(compact)
        registry._compact = [s for s in compact if s["function"]["name"] != name]
        if len(registry._compact) < before:
            print(f"已禁用紧凑工具: {name}")
            return 0
        print(f"未找到工具: {name}")
        return 1

    elif args.cmd == "stats":
        core = len(getattr(registry, '_schemas', []))
        compact = len(getattr(registry, '_compact', []))
        deferred = len(getattr(registry, '_deferred', []))
        injected = len(getattr(registry, '_injected_tools', []))
        print("工具统计:")
        print(f"  核心: {core} 个")
        print(f"  紧凑: {compact} 个（首次调用后自动提升）")
        print(f"  延迟: {deferred} 个（通过 tool_search 发现）")
        print(f"  已注入: {injected} 个")
        return 0

    else:
        print("可用: kuafu tools list / enable / disable / stats")
        return 0



def run_gateway(args: argparse.Namespace, agent: Any) -> int:
    """kuafu gateway — Gateway 守护进程管理。"""
    from core.gateway import GatewayServer, install_service, uninstall_service

    if args.cmd == "start":
        os.environ["KUAFU_GATEWAY_RUNNING"] = "1"  # 标记 gateway 模式，审批走通道推送
        gw = GatewayServer(agent, host=args.host, port=args.port, api_key=args.key)
        if gw.start():
            print(f"Gateway 运行中: http://{args.host}:{args.port}")
            print("按 Ctrl+C 停止")
            try:
                while gw.is_running():
                    import time
                    time.sleep(1)
            except KeyboardInterrupt:
                print()
            gw.stop()
            return 0
        else:
            print(f"Gateway 启动失败")
            return 1

    elif args.cmd == "install":
        install_service()
        return 0

    elif args.cmd == "uninstall":
        uninstall_service()
        return 0

    elif args.cmd == "stop":
        """向运行中的 Gateway 发送 shutdown 信号。"""
        port = getattr(args, "port", 8765)
        import urllib.request
        import json
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/shutdown",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=5)
            result = json.loads(resp.read().decode("utf-8"))
            print(f"✅ Gateway 已停止: {result}")
        except Exception as e:
            print(f"❌ Gateway 停止失败: {e}")
            return 1
        return 0

    elif args.cmd == "status":
        import subprocess
        result = subprocess.run(
            ["systemctl", "--user", "status", "kuafu-gateway"],
            capture_output=True, text=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print("Gateway 未运行或未安装")
        return result.returncode

    return 1


def run_channel(args: argparse.Namespace, agent: Any) -> int:
    """kuafu channel — 通道热加载管理（需 gateway 运行中）。"""
    # 连接本地 gateway API
    port = args.port or 8765
    base = f"http://127.0.0.1:{port}"
    import urllib.request
    import json

    def _api_get(path: str) -> dict:
        try:
            resp = urllib.request.urlopen(f"{base}{path}", timeout=5)
            return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"❌ Gateway 连接失败 ({base}): {e}")
            print("   请先确认 kuafu gateway start 已在运行")
            return {}

    def _api_post(path: str, body: dict) -> dict:
        try:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                f"{base}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"❌ Gateway 请求失败 ({path}): {e}")
            return {}

    if args.cmd == "list":
        result = _api_get("/api/channel/list")
        channels = result.get("channels", [])
        if not channels:
            print("没有已注册的通道。")
            print("  发现可用: kuafu channel discover")
            print("  加载通道: kuafu channel load <name>")
            return 0

        print(f"{'名称':<20} {'状态':<10}")
        print("-" * 30)
        for ch in channels:
            status = "✅ 运行中" if ch.get("running") else "⏹ 已停止"
            print(f"{ch['name']:<20} {status}")
        return 0

    elif args.cmd == "discover":
        # 先展示已注册的
        list_result = _api_get("/api/channel/list")
        registered = {ch["name"] for ch in list_result.get("channels", [])}

        result = _api_get("/api/channel/discover")
        discovered = result.get("discovered", {})
        if not discovered:
            print("未发现任何通道类。")
            return 0

        print(f"发现 {len(discovered)} 个通道类:")
        for name, cls_name in discovered.items():
            tag = " (已加载)" if name in registered else ""
            print(f"  • {name:<20} {cls_name:<30}{tag}")
        print()
        print("加载: kuafu channel load <name>")
        return 0

    elif args.cmd == "load":
        if not args.name:
            print("用法: kuafu channel load <channel_name>")
            print("  可用: kuafu channel discover")
            return 1

        result = _api_post("/api/channel/load", {"name": args.name})
        if result.get("status") == "loaded":
            print(f"✅ 通道已加载: {args.name}")
        else:
            error = result.get("error", "未知错误")
            print(f"❌ 加载失败: {error}")
        return 0

    elif args.cmd == "remove":
        if not args.name:
            print("用法: kuafu channel remove <channel_name>")
            return 1

        result = _api_post("/api/channel/remove", {"name": args.name})
        if result.get("status") == "removed":
            print(f"✅ 通道已移除: {args.name}")
        else:
            error = result.get("error", "未知错误")
            print(f"❌ 移除失败: {error}")
        return 0

    elif args.cmd == "reload":
        if not args.name:
            print("用法: kuafu channel reload <channel_name>")
            return 1

        result = _api_post("/api/channel/reload", {"name": args.name})
        if result.get("status") == "reloaded":
            print(f"✅ 通道已热重载: {args.name}")
        else:
            error = result.get("error", "未知错误")
            print(f"❌ 重载失败: {error}")
        return 0

    else:
        print("未知的 channel 子命令")
        print("可用: list, discover, load, remove, reload")
        return 1


def run_setup(args: argparse.Namespace, agent: Any = None) -> int:
    """kuafu setup — 交互式配置向导。"""
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parent.parent
    setup_path = ROOT_DIR / "setup_wizard.py"
    if not setup_path.exists():
        print("❌ setup_wizard.py 未找到")
        return 1

    # 代理到 setup_wizard.py
    _sys.argv = [_sys.argv[0], str(setup_path)]
    exec(open(str(setup_path), encoding="utf-8").read())
    return 0


def run_batch(args: argparse.Namespace, agent: Any) -> int:
    """kuafu batch — 批量任务管理。"""
    from core.batch_engine import BatchEngine

    engine = BatchEngine(agent=agent, max_concurrent=3)

    if args.cmd == "submit":
        """提交批量任务。"""
        # 从文件读取或直接参数
        tasks = []

        if args.file:
            try:
                filepath = Path(args.file)
                text = filepath.read_text(encoding="utf-8")
                # 按行分割（空行跳过，# 注释跳过）
                tasks = [
                    line.strip() for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
            except Exception as e:
                print(f"❌ 读取文件失败: {e}")
                return 1
        elif args.tasks:
            tasks = args.tasks
        else:
            print("❌ 请指定 --file 或直接输入任务")
            print("  用法: kuafu batch submit --file tasks.txt")
            print("  用法: kuafu batch submit --task '任务1' --task '任务2'")
            return 1

        if not tasks:
            print("❌ 没有有效的任务")
            return 1

        batch_id = engine.submit(tasks)
        print(f"📦 批次已提交: {batch_id}")
        print(f"   任务数: {len(tasks)}")
        print(f"   并发: {engine.max_concurrent}")
        print(f"   查看状态: kuafu batch status {batch_id}")
        return 0

    elif args.cmd == "status":
        """查看批次状态。"""
        import time as _time

        if args.batch_id:
            batch_ids = [args.batch_id]
        else:
            # 显示所有批次
            batches = engine.get_all_batches(limit=10)
            if not batches:
                print("没有批次记录。")
                return 0
            print(f"最近批次 ({len(batches)}):")
            print(f"  {'批次ID':<30} {'总数':>5} {'完成':>5} {'运行':>5} {'失败':>5} {'待处理':>5}")
            print(f"  {'-' * 60}")
            for b in batches:
                print(f"  {b['batch_id']:<30} {b['total']:>5} {b['completed']:>5} "
                      f"{b['running']:>5} {b['failed']:>5} {b['pending']:>5}")
            return 0

        for bid in batch_ids:
            status = engine.get_status(bid)
            if status.total == 0:
                print(f"批次不存在: {bid}")
                continue

            print(f"批次: {bid}")
            print(f"  进度: {status.completed}/{status.total} "
                  f"(完成={status.completed} 运行={status.running} "
                  f"失败={status.failed} 待处理={status.pending})")
            print()

            if status.results:
                print(f"  明细:")
                print(f"    {'#':<3} {'状态':<12} {'任务':<50} {'耗时':<8}")
                print(f"    {'-' * 75}")
                for r in status.results:
                    task_preview = (r["task_text"] or "")[:48]
                    dur = f"{r['duration']:.1f}s" if r["duration"] else "-"
                    print(f"    {r['task_index']:<3} {r['status']:<12} {task_preview:<50} {dur:<8}")
            print()
        return 0

    elif args.cmd == "cancel":
        """取消批次。"""
        if not args.batch_id:
            print("用法: kuafu batch cancel <batch_id>")
            return 1
        count = engine.cancel_batch(args.batch_id)
        print(f"已取消 {count} 个待处理任务")
        return 0

    elif args.cmd == "retry":
        """重试失败任务。"""
        if not args.batch_id:
            print("用法: kuafu batch retry <batch_id>")
            return 1
        count = engine.retry_failed(args.batch_id)
        print(f"已重试 {count} 个失败任务")
        return 0

    elif args.cmd == "clear":
        """清理批次记录。"""
        if not args.batch_id:
            print("用法: kuafu batch clear <batch_id>")
            return 1
        count = engine.clear_batch(args.batch_id)
        print(f"已清理 {count} 条记录")
        return 0

    else:
        print("可用: kuafu batch submit / status / cancel / retry / clear")
        return 0


def run_evolution(args: argparse.Namespace, agent: Any = None) -> int:
    """kuafu evolution — 进化系统管理。"""
    from core.evolution_tracker import EvolutionTracker
    from pathlib import Path
    import time as _time

    tracker = EvolutionTracker(db_path=Path("memory/evolution.db"))

    if args.cmd == "stats":
        """进化综合统计。"""
        stats = tracker.get_stats(include_recent_events=True)

        print("夸父进化系统 — 综合统计")
        print("=" * 50)
        print(f"技能版本链:  {stats.get('total_skills', 0)} 个技能")
        print(f"任务类型:     {stats.get('total_task_types', 0)} 种")
        print(f"已知错误:     {stats.get('known_errors', 0)} 个")
        print(f"进化事件:     {stats.get('total_events', 0)} 条 (skill: {stats.get('skill_events', 0)})")
        print()

        recent = stats.get("recent_24h", {})
        print(f"近24小时活动:")
        print(f"  适应度评估: {recent.get('fitness_evals', 0)} 次")
        print(f"  进化事件:   {recent.get('events', 0)} 条")
        print()

        # 任务类型 Top N
        rows = tracker._execute(
            "SELECT task_type, count, consecutive_fail, last_seen "
            "FROM evolution_task_types ORDER BY count DESC LIMIT 5"
        ).fetchall()
        if rows:
            print(f"高频任务类型 (Top 5):")
            print(f"  {'类型':<20} {'次数':>6} {'连续失败':>8} {'最后活跃':<15}")
            print(f"  {'-' * 50}")
            for r in rows:
                ts = _time.strftime("%m-%d %H:%M", _time.localtime(r["last_seen"]))
                print(f"  {r['task_type']:<20} {r['count']:>6} {r['consecutive_fail']:>8} {ts:<15}")
            print()

        # 退化检测摘要
        degradations = tracker.detect_all_degradations()
        if degradations:
            print(f"退化检测: {len(degradations)} 个技能有退化信号")
            for d in degradations:
                print(f"  ⚠️  {d['skill_name']}: {d['severity']} — {'; '.join(d['signals'])}")
            print()

        # 最近事件
        events = stats.get("recent_events", [])
        if events:
            print(f"最近进化事件:")
            print(f"  {'等级':<10} {'动作':<45} {'时间':<15}")
            print(f"  {'-' * 70}")
            for e in events[:8]:
                ts = _time.strftime("%m-%d %H:%M", _time.localtime(e["created_at"]))
                action = (e.get("action") or "")[:43]
                print(f"  {e.get('level', '?'):<10} {action:<45} {ts:<15}")
        return 0

    elif args.cmd == "tasks":
        """查看任务类型统计详情。"""
        rows = tracker._execute(
            "SELECT task_type, count, consecutive_fail, last_seen "
            "FROM evolution_task_types ORDER BY count DESC"
        ).fetchall()
        if not rows:
            print("没有任务类型记录。")
            return 0

        print(f"任务类型统计 ({len(rows)} 种):")
        print(f"  {'类型':<25} {'次数':>6} {'连续失败':>8} {'失败率':>8} {'最后活跃':<15}")
        print(f"  {'-' * 65}")
        for r in rows:
            ts = _time.strftime("%m-%d %H:%M", _time.localtime(r["last_seen"]))
            fail_rate = tracker.get_recent_failure_rate(r["task_type"], n=10)
            fail_str = f"{fail_rate:.0%}" if fail_rate > 0 else "-"
            print(f"  {r['task_type']:<25} {r['count']:>6} {r['consecutive_fail']:>8} {fail_str:>8} {ts:<15}")
        return 0

    elif args.cmd == "errors":
        """查看已知错误库。"""
        rows = tracker._execute(
            "SELECT error_text, count, skill_name FROM evolution_errors ORDER BY count DESC LIMIT 20"
        ).fetchall()
        if not rows:
            print("没有已知错误记录。")
            return 0

        total = tracker.get_error_count()
        print(f"已知错误库 ({total} 个):")
        print(f"  {'错误(前60字)':<62} {'次数':>4} {'关联技能':<20}")
        print(f"  {'-' * 90}")
        for r in rows:
            error_preview = (r["error_text"] or "")[:60]
            skill = r["skill_name"] or "-"
            print(f"  {error_preview:<62} {r['count']:>4} {skill:<20}")
        return 0

    elif args.cmd == "fitness":
        """查看适应度评估日志。"""
        # 所有有 fitness 记录的技能
        rows = tracker._execute(
            """SELECT skill_name, COUNT(*) as c,
                      ROUND(AVG(score), 3) as avg_score,
                      ROUND(MIN(score), 3) as min_score,
                      ROUND(MAX(score), 3) as max_score
               FROM evolution_fitness_log
               GROUP BY skill_name
               ORDER BY c DESC"""
        ).fetchall()

        if not rows:
            print("没有适应度评估记录。")
            return 0

        total = tracker._execute("SELECT COUNT(*) as c FROM evolution_fitness_log").fetchone()["c"]
        print(f"适应度评估日志 ({total} 条, {len(rows)} 个技能):")
        print(f"  {'技能':<25} {'评估次数':>8} {'平均分':>8} {'最低':>8} {'最高':>8}")
        print(f"  {'-' * 60}")
        for r in rows:
            print(f"  {r['skill_name']:<25} {r['c']:>8} {r['avg_score']:>8} {r['min_score']:>8} {r['max_score']:>8}")
        return 0

    elif args.cmd == "chart":
        """进化终端可视化。"""
        from core.evolution_viz import EvolutionVisualizer

        viz = EvolutionVisualizer(tracker)

        chart_type = getattr(args, "type", None) or "dashboard"

        if chart_type == "dashboard":
            print(viz.dashboard())
        elif chart_type == "trend":
            skill = getattr(args, "skill", None)
            print(viz.fitness_trend(skill_name=skill))
        elif chart_type == "tasks":
            print(viz.task_type_chart())
        elif chart_type == "timeline":
            skill = getattr(args, "skill", None)
            print(viz.skill_timeline(skill_name=skill))
        elif chart_type == "degrade":
            print(viz.degradation_summary())
        elif chart_type == "all":
            print(viz.full_report())
        else:
            print(f"未知图表类型: {chart_type}")
            print("可用: dashboard / trend / tasks / timeline / degrade / all")
        return 0

    else:
        print("可用: kuafu evolution stats / tasks / errors / fitness")
        return 0


# ── 子命令配置 ──────────────────────────────────────────────

SUBCOMMANDS = {
    "cron": {
        "help": "定时任务管理",
        "handler": run_cron,
        "subparsers": {
            "list": {"help": "列出所有定时任务"},
            "create": {
                "help": "创建定时任务",
                "args": [
                    ("schedule", {"help": "调度表达式（30m/2h/0 8 * * */ISO时间）"}),
                    ("task_text", {"help": "任务文本", "nargs": "?"}),
                ],
                "optional": [
                    ("--name", {"help": "任务名称"}),
                    ("--output", {"dest": "output_mode", "help": "输出模式（file/feishu）"}),
                ],
            },
            "remove": {
                "help": "删除定时任务",
                "args": [("name", {"help": "任务名称"})],
            },
            "start": {"help": "启动调度器"},
            "stop": {"help": "停止调度器"},
            "status": {"help": "查看调度器状态"},
        },
    },
    "sessions": {
        "help": "会话管理",
        "handler": run_sessions,
        "subparsers": {
            "list": {
                "help": "列出会话",
                "optional": [
                    ("--limit", {"type": int, "default": 20}),
                    ("--status", {"choices": ["active", "archived"]}),
                ],
            },
            "browse": {
                "help": "搜索浏览会话",
                "optional": [("-q", "--query", {"help": "搜索关键词"})],
            },
            "export": {
                "help": "导出会话",
                "args": [("session_id", {"help": "会话ID"})],
            },
            "delete": {
                "help": "删除会话",
                "args": [("session_id", {"help": "会话ID"})],
            },
            "prune": {
                "help": "清理旧会话",
                "optional": [("--days", {"type": int, "default": 30, "help": "保留天数"})],
            },
            "stats": {"help": "会话统计"},
        },
    },
    "status": {
        "help": "查看夸父状态",
        "handler": run_status,
    },
    "model": {
        "help": "模型管理",
        "handler": run_model,
        "subparsers": {
            "list": {"help": "列出可用模型"},
            "switch": {
                "help": "切换后端",
                "args": [("backend", {"help": "provider ID (deepseek/openai/claude/qwen等)"})],
            },
        },
    },
    "gateway": {
        "help": "Gateway 守护进程管理",
        "handler": run_gateway,
        "subparsers": {
            "start": {
                "help": "启动 Gateway（前台）",
                "optional": [
                    ("--port", {"type": int, "default": 8765}),
                    ("--host", {"default": "127.0.0.1"}),
                    ("--key", {"default": "", "help": "API Key"}),
                ],
            },
            "stop": {
                "help": "停止 Gateway（发送 shutdown 信号）",
                "optional": [
                    ("--port", {"type": int, "default": 8765}),
                ],
            },
            "install": {"help": "安装 systemd user service"},
            "uninstall": {"help": "卸载 systemd user service"},
            "status": {"help": "查看 Gateway 状态（systemctl）"},
        },
    },
    "channel": {
        "help": "消息通道热加载管理（list/discover/load/remove/reload）",
        "handler": run_channel,
        "subparsers": {
            "list": {
                "help": "列出已注册通道及运行状态",
                "optional": [
                    ("--port", {"type": int, "default": 8765, "help": "Gateway 端口"}),
                ],
            },
            "discover": {
                "help": "扫描所有可用通道类",
                "optional": [
                    ("--port", {"type": int, "default": 8765, "help": "Gateway 端口"}),
                ],
            },
            "load": {
                "help": "热加载通道",
                "args": [("name", {"help": "通道名称"})],
                "optional": [
                    ("--port", {"type": int, "default": 8765, "help": "Gateway 端口"}),
                ],
            },
            "remove": {
                "help": "移除并停止通道",
                "args": [("name", {"help": "通道名称"})],
                "optional": [
                    ("--port", {"type": int, "default": 8765, "help": "Gateway 端口"}),
                ],
            },
            "reload": {
                "help": "热重载通道（stop → start）",
                "args": [("name", {"help": "通道名称"})],
                "optional": [
                    ("--port", {"type": int, "default": 8765, "help": "Gateway 端口"}),
                ],
            },
        },
    },
    "setup": {
        "help": "交互式配置向导（飞书/微信/LLM）",
        "handler": run_setup,
    },
    "batch": {
        "help": "批量任务管理（submit/status/cancel/retry/clear）",
        "handler": run_batch,
        "subparsers": {
            "submit": {
                "help": "提交批量任务",
                "optional": [
                    ("-f", "--file", {"help": "任务文件（每行一个任务）"}),
                    ("-t", "--task", {"action": "append", "dest": "tasks", "help": "单条任务（可多次）"}),
                ],
            },
            "status": {
                "help": "查看批次状态",
                "optional": [
                    ("batch_id", {"nargs": "?", "help": "批次ID（可选，不指定则列出所有）"}),
                ],
            },
            "cancel": {
                "help": "取消待处理任务",
                "args": [("batch_id", {"help": "批次ID"})],
            },
            "retry": {
                "help": "重试失败任务",
                "args": [("batch_id", {"help": "批次ID"})],
            },
            "clear": {
                "help": "清理批次记录",
                "args": [("batch_id", {"help": "批次ID"})],
            },
        },
    },
    "evolution": {
        "help": "进化系统管理（stats/tasks/errors/fitness）",
        "handler": run_evolution,
        "subparsers": {
            "stats": {
                "help": "进化综合统计（技能/任务/事件/退化）",
            },
            "tasks": {
                "help": "任务类型统计详情",
            },
            "errors": {
                "help": "查看已知错误库",
                "optional": [
                    ("--limit", {"type": int, "default": 20, "help": "最大显示数"}),
                ],
            },
            "fitness": {
                "help": "查看适应度评估日志",
            },
            "chart": {
                "help": "进化终端可视化（dashboard/trend/tasks/timeline/degrade/all）",
                "optional": [
                    ("--type", {"default": "dashboard", "help": "图表类型: dashboard/trend/tasks/timeline/degrade/all"}),
                    ("--skill", {"default": "", "help": "技能名（trend/timeline 用）"}),
                ],
            },
        },
    },
    "skill": {
        "help": "技能管理（本地/市场/安装/卸载/版本/退化/回滚）",
        "handler": run_skill,
        "subparsers": {
            "list": {
                "help": "列出所有技能",
                "optional": [
                    ("--category", {"default": "", "help": "按分类筛选（coding/web/research/...）"}),
                ],
            },
            "search": {
                "help": "搜索技能市场",
                "optional": [("query", {"nargs": "?", "help": "搜索关键词"})],
            },
            "install": {
                "help": "安装远程技能",
                "args": [("name", {"help": "技能名称或文件路径/URL"})],
            },
            "remove": {
                "help": "删除技能",
                "args": [("name", {"help": "技能名称"})],
            },
            "stats": {"help": "技能统计（含分类分布）"},
            "info": {
                "help": "查看技能详情",
                "args": [("name", {"help": "技能名称"})],
            },
            "edit": {
                "help": "用 $EDITOR 编辑技能文件",
                "args": [("name", {"help": "技能名称"})],
            },
            "publish": {
                "help": "完整发布技能（验证 → 打包 → 发布到本地/GitHub/Release）",
                "args": [("name", {"help": "技能名称"})],
                "optional": [
                    ("--mode", {"default": "local", "choices": ["local", "github", "release"],
                                "help": "发布模式: local=本地文件, github=推送到Git仓库, release=GitHub Release"}),
                    ("--output", {"default": "", "help": "local 模式下输出目录（默认 /tmp/kuafu-skill-market）"}),
                    ("--bump", {"default": "patch", "choices": ["major", "minor", "patch"],
                                "help": "版本递增方式"}),
                    ("--release", {"action": "store_true", "help": "创建 GitHub Release（仅 github 模式）"}),
                ],
            },
            "deps": {
                "help": "检查/安装技能依赖",
                "args": [("name", {"help": "技能名称"})],
                "optional": [
                    ("--install", {"action": "store_true", "help": "自动安装缺失的 Python 包"}),
                ],
            },
            "sandbox": {
                "help": "查看技能沙箱配置",
                "args": [("name", {"help": "技能名称"})],
            },
            "version": {
                "help": "查看技能版本链",
                "optional": [
                    ("name", {"nargs": "?", "help": "技能名称（可选，不指定则列出所有）"}),
                ],
            },
            "scan": {"help": "扫描 skills/ 目录，同步版本链"},
            "diff": {
                "help": "比较技能两个版本的内容差异",
                "args": [("name", {"help": "技能名称"})],
                "optional": [
                    ("--v1", {"type": int, "default": 1, "help": "版本1（默认1）"}),
                    ("--v2", {"type": int, "default": 2, "help": "版本2（默认2）"}),
                ],
            },
            "restore": {
                "help": "从版本链恢复技能文件到指定版本",
                "args": [
                    ("name", {"help": "技能名称"}),
                    ("version", {"type": int, "help": "目标版本号"}),
                ],
            },
            "log": {"help": "查看进化追踪日志"},
            "degrade": {
                "help": "检测技能退化",
                "optional": [
                    ("name", {"nargs": "?", "help": "技能名称（可选，不指定则扫描所有）"}),
                    ("--failures", {"nargs": "*", "type": bool, "help": "最近任务成功/失败列表"}),
                ],
            },
            "rollback": {
                "help": "自动回滚退化的技能到历史最佳版本",
                "optional": [
                    ("name", {"nargs": "?", "help": "技能名称（可选，不指定则回滚所有）"}),
                ],
            },
        },
    },
    "kfskill": {
        "help": "技能包操作（create/list/validate/export/info）",
        "handler": run_kfskill,
        "subparsers": {
            "create": {
                "help": "创建 kfskill 技能包",
                "args": [
                    ("name", {"help": "技能名称"}),
                ],
                "optional": [
                    ("--description", {"default": "", "help": "技能描述"}),
                    ("--category", {"default": "", "help": "分类（coding/web/research/devops/media/writing）"}),
                    ("--author", {"default": "", "help": "作者"}),
                    ("--version", {"default": "1.0.0", "help": "版本号"}),
                    ("--keywords", {"nargs": "*", "default": [], "help": "搜索关键词"}),
                ],
            },
            "list": {
                "help": "列出所有 kfskill 技能包",
                "optional": [
                    ("--category", {"default": "", "help": "按分类筛选"}),
                ],
            },
            "validate": {
                "help": "验证 kfskill 格式合法性",
                "args": [("file", {"help": "技能包文件路径"})],
            },
            "export": {
                "help": "将本地技能导出为 kfskill 格式",
                "args": [("name", {"help": "技能名称"})],
            },
            "info": {
                "help": "查看技能包详情",
                "args": [("name", {"help": "技能名称或文件路径"})],
            },
        },
    },
    "repo": {
        "help": "远程技能仓库管理（add/remove/list/search/refresh）",
        "handler": run_repo,
        "subparsers": {
            "list": {"help": "列出所有已配置的远程仓库"},
            "add": {
                "help": "添加远程仓库",
                "args": [
                    ("name", {"help": "仓库名称"}),
                    ("url", {"help": "仓库索引 JSON URL"}),
                ],
                "optional": [
                    ("--description", {"default": "", "help": "仓库描述"}),
                ],
            },
            "remove": {
                "help": "移除远程仓库",
                "args": [("name", {"help": "仓库名称"})],
            },
            "search": {
                "help": "搜索远程仓库的技能",
                "optional": [("query", {"nargs": "?", "help": "搜索关键词"})],
            },
            "refresh": {"help": "强制刷新所有仓库缓存"},
            "stats": {"help": "查看仓库状态统计"},
            "clear-cache": {
                "help": "清理缓存文件",
                "optional": [("name", {"nargs": "?", "help": "仓库名称（可选）"})],
            },
        },
    },
    "tools": {
        "help": "工具集管理（列出/启用/禁用）",
        "handler": run_tools,
        "subparsers": {
            "list": {"help": "列出所有工具"},
            "enable": {
                "help": "启用工具",
                "args": [("name", {"help": "工具名称"})],
            },
            "disable": {
                "help": "禁用工具",
                "args": [("name", {"help": "工具名称"})],
            },
            "stats": {"help": "工具统计"},
        },
    },
}

# ── Subcommand Parser ──────────────────────────────────────

def _build_subcommand_parser() -> argparse.ArgumentParser:
    """构建仅含子命令的 parser（不含 task 参数）。"""
    parser = argparse.ArgumentParser(description="夸父 — 自我进化的 AI Agent")
    parser.add_argument("--whiteboard", action="store_true", help="使用白板模式")
    subparsers = parser.add_subparsers(dest="subcommand", title="子命令", required=True)

    for name, config in SUBCOMMANDS.items():
        sub = subparsers.add_parser(name, help=config["help"])
        sub.set_defaults(sub_handler=config["handler"])

        subparsers_inner = config.get("subparsers", {})
        if subparsers_inner:
            inner = sub.add_subparsers(dest="cmd")
            for cmd_name, cmd_config in subparsers_inner.items():
                p = inner.add_parser(cmd_name, help=cmd_config.get("help", ""))
                p.set_defaults(cmd=cmd_name)
                for arg in cmd_config.get("args", []):
                    if len(arg) == 2:
                        name_or_flags, kwargs = arg
                        p.add_argument(name_or_flags, **kwargs)
                    else:
                        name_or_flags, alias, kwargs = arg
                        p.add_argument(name_or_flags, alias, **kwargs)
                for arg in cmd_config.get("optional", []):
                    if len(arg) == 2:
                        name_or_flags, kwargs = arg
                        p.add_argument(name_or_flags, **kwargs)
                    else:
                        name_or_flags, alias, kwargs = arg
                        p.add_argument(name_or_flags, alias, **kwargs)
    return parser


# ── CLI 入口 ────────────────────────────────────────────────


def main() -> int:
    """夸父 CLI 入口。返回 exit code。

    路由规则：
    1. sys.argv[1] 在 SUBCOMMANDS 中 → 子命令模式（cron/sessions/status/model）
    2. 否则 → 直接执行任务或交互模式（走原有的 main.py）
    3. 无参数 → 交互模式
    """
    try:
        # 子命令模式
        if len(sys.argv) >= 2 and sys.argv[1] in SUBCOMMANDS:
            parser = _build_subcommand_parser()
            args = parser.parse_args()
            from core.main import KuafuAgent
            agent = KuafuAgent()
            return args.sub_handler(args, agent)

        # 直接执行/交互模式 — 走原有 main.py
        from core.main import main as agent_main
        return agent_main()
    except Exception as e:
        import traceback
        print(f"[FATAL] CLI 入口异常: {e}", flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        print(f"[FATAL] __main__ 异常: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)

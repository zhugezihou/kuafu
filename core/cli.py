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

import argparse
import json
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


def run_skill(args: argparse.Namespace, agent: Any = None) -> int:
    def run_skill(args: argparse.Namespace, agent: Any = None) -> int:
        """kuafu skill — 技能管理。"""
        from core.skill_manager import SkillManager
        mgr = SkillManager()

        if args.cmd == "list":
            local = mgr.list_local()
            installed = mgr.list_installed_market()
            print(f"本地技能 ({len(local)}):")
            for s in local:
                print(f"  {s.name:30s} {s.description[:50]}")
            if installed:
                print(f"\n市场安装 ({len(installed)}):")
                for s in installed:
                    print(f"  {s.name:30s} {s.description[:50]}")
            return 0

        elif args.cmd == "search":
            query = args.query or ""
            if not query:
                market = mgr.fetch_market_index()
                print(f"技能市场 ({len(market)} 个可用):")
                for s in market[:20]:
                    print(f"  {s.name:30s} [{s.category or '?'}] {s.description[:40]}")
                return 0

            local = mgr.search_local(query)
            market = mgr.search_market(query)
            print(f"本地匹配 ({len(local)}):")
            for s in local:
                print(f"  {s.name}")
            print(f"\n市场匹配 ({len(market)}):")
            for s in market:
                print(f"  {s.name:30s} [{s.author or '?'}] {s.description[:50]}")
            return 0

        elif args.cmd == "install":
            name = args.name or ""
            if not name:
                print("用法: kuafu skill install <skill_name|url>")
                return 1
            result = mgr.install(name)
            if result["success"]:
                print(f"✅ 已安装: {result['name']}")
                fp = result.get("file", "")
                if fp:
                    print(f"   路径: {fp}")
            else:
                print(f"❌ 安装失败: {result.get('error', '未知错误')}")
            return 0

        elif args.cmd == "remove":
            name = args.name or ""
            if not name:
                print("用法: kuafu skill remove <skill_name>")
                return 1
            if mgr.remove_local(name) or mgr.uninstall(name):
                print(f"已删除: {name}")
            else:
                print(f"未找到: {name}")
            return 0

        elif args.cmd == "stats":
            stats = mgr.get_stats()
            print("技能统计:")
            print(f"  本地: {stats['local']} 个")
            print(f"  市场安装: {stats['installed_market']} 个")
            print(f"  市场可用: {stats['available_market']} 个")
            return 0

        else:
            print("可用: kuafu skill list / search / install / remove / stats")
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
                "args": [("backend", {"choices": ["cloud", "local"], "help": "目标后端"})],
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
            "install": {"help": "安装 systemd user service"},
            "uninstall": {"help": "卸载 systemd user service"},
            "status": {"help": "查看 Gateway 状态（systemctl）"},
        },
    },
    "setup": {
        "help": "交互式配置向导（飞书/微信/LLM）",
        "handler": run_setup,
    },
    "skill": {
        "help": "技能管理（本地/市场/安装/卸载）",
        "handler": run_skill,
        "subparsers": {
            "list": {"help": "列出所有技能"},
            "search": {
                "help": "搜索技能市场",
                "optional": [("query", {"nargs": "?", "help": "搜索关键词"})],
            },
            "install": {
                "help": "安装远程技能",
                "args": [("name", {"help": "技能名称或 SKILL.md URL"})],
            },
            "remove": {
                "help": "删除技能",
                "args": [("name", {"help": "技能名称"})],
            },
            "stats": {"help": "技能统计"},
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


if __name__ == "__main__":
    sys.exit(main())

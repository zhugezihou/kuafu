#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夸父启动器 (Kuafu Launcher)"""
import os,sys,json,time,argparse
from pathlib import Path
from datetime import datetime
ROOT_DIR=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT_DIR))

class Color:
    R="\033[0m";B="\033[1m";D="\033[2m"
    RED="\033[91m";GRN="\033[92m";YLW="\033[93m"
    BLU="\033[94m";MAG="\033[95m";CYN="\033[96m";BGB="\033[44m"
    @staticmethod
    def ok(t): return f"{Color.GRN}{t}{Color.R}"
    @staticmethod
    def er(t): return f"{Color.RED}{t}{Color.R}"
    @staticmethod
    def wa(t): return f"{Color.YLW}{t}{Color.R}"
    @staticmethod
    def inf(t): return f"{Color.CYN}{t}{Color.R}"
    @staticmethod
    def ti(t): return f"{Color.B}{Color.BGB} {t} {Color.R}"
    @staticmethod
    def hl(t): return f"{Color.B}{Color.MAG}{t}{Color.R}"

BANNER = r"""    _                     __
   | |                   / _|
   | | __ ___   ____ _  | |_ _   _ _ __
   | |/ _ | |  _| | | | '_ \\
   | | (_| |\\ V / (_| | | | | |_| | | | |
   |_|\\__,_| \\_/ \\__,_| |_|  \\__,_|_| |_|
   逐日不息 . 自我超越
"""

class KuafuLauncher:
    def __init__(self):
        self.agent=None
        self.feishu_bot=None
        self.cron_scheduler=None
        try:
            from core.main import KuafuAgent
            self.agent=KuafuAgent()
        except Exception as e:
            print(f"  {Color.er('加载失败:')} {e}")

        # 启动飞书 Bot（异步轮询）
        self._init_feishu()

        # 启动 Cron 调度器
        self._init_cron()

    def _init_feishu(self):
        """初始化飞书 Bot（如有配置）。"""
        from core.feishu_bot import FeishuBot, FEISHU_ENABLED
        if not FEISHU_ENABLED:
            print(f"  {Color.D}飞书渠道: 未配置{Color.R}")
            return

        if not self.agent:
            print(f"  {Color.wa('飞书渠道: Agent 未加载，跳过')}")
            return

        def on_feishu_message(text: str, msg_id: str) -> str:
            if not self.agent:
                return " Agent 未加载"
            try:
                r = self.agent.run(text)
                return r.get("result", " 处理完成")
            except Exception as e:
                return f" 处理出错: {str(e)[:150]}"

        self.feishu_bot = FeishuBot(
            app_id=FEISHU_APP_ID,
            app_secret=FEISHU_APP_SECRET,
            chat_id=FEISHU_CHAT_ID,
            on_message=on_feishu_message,
        )
        self.feishu_bot.start()

    def _init_cron(self):
        """初始化 Cron 定时任务。"""
        from core.cron_scheduler import CronScheduler
        cron_cfg = ROOT_DIR / "cron" / "schedule.yaml"

        if not cron_cfg.exists():
            print(f"  {Color.D}Cron定时: 无配置{Color.R}")
            return

        def on_cron_task(task) -> str:
            if not self.agent:
                return " Agent 未加载"
            try:
                r = self.agent.run(task.task_text)
                return r.get("result", " 完成")
            except Exception as e:
                return f" {e}"

        self.cron_scheduler = CronScheduler(
            config_path=str(cron_cfg),
            on_task_run=on_cron_task,
        )
        self.cron_scheduler.start()

    def _clear(self):
        os.system('clear' if os.name=='posix' else 'cls')

    def show_menu(self):
        while True:
            self._clear()
            print(BANNER)
            print(f"  {Color.D}{'-'*50}{Color.R}")
            print(f"  {Color.B}主菜单{Color.R}")
            print(f"  {Color.D}{'-'*50}{Color.R}")
            print(f"  {Color.CYN}1{Color.R}) 交互模式")
            print(f"  {Color.CYN}2{Color.R}) 状态看板")
            print(f"  {Color.CYN}3{Color.R}) 快速任务")
            print(f"  {Color.CYN}4{Color.R}) 任务日志")
            print(f"  {Color.CYN}5{Color.R}) 系统监控")
            print(f"  {Color.CYN}6{Color.R}) 进化历史")
            print(f"  {Color.CYN}7{Color.R}) 身份声明")
            print(f"  {Color.CYN}8{Color.R}) 环境检查")
            print(f"  {Color.CYN}9{Color.R}) 飞书状态")
            print(f"  {Color.CYN}0{Color.R}) 退出")
            print(f"  {Color.D}{'-'*50}{Color.R}")
            c=input(f"  {Color.B}请选择 [0-9]: {Color.R}").strip()
            if c=='1': self._interactive()
            elif c=='2': self._show_status()
            elif c=='3': self._quick_tasks()
            elif c=='4': self._show_logs()
            elif c=='5': self._sys_monitor()
            elif c=='6': self._evo_history()
            elif c=='7': self._show_id()
            elif c=='8': self._env_check()
            elif c=='9': self._feishu_status()
            elif c=='0': print(f"\n  {Color.inf('再见！')}");break
            else: input(f"\n  {Color.wa('无效')}")

    def _interactive(self):
        if not self.agent: input(f"\n  {Color.er('未加载')}");return
        self._clear()
        print(f"\n  {Color.ti(' 交互模式 ')}")
        print(f"  {Color.D}{'-'*50}{Color.R}")
        print('  输入exit返回')
        print(f"  {Color.D}{'-'*50}{Color.R}\n")
        while True:
            try:
                t=input(f"  {Color.B}夸父> {Color.R}").strip()
                if t.lower() in ('exit','quit','q','back'): break
                if not t: continue
                print(f"\n  {Color.inf('思考中...')}\n")
                r=self.agent.run(t)
                ic=Color.ok('OK') if r['success'] else Color.er('FAIL')
                print(f"  [{ic}] {r.get('result','')}")
                if r.get('evolution'):
                    e=r['evolution'];print(f"\n  {Color.hl('进化:')} L{e.level} - {e.action}")
                print(f"\n  {Color.D}{r.get('duration',0)}s | {r.get('turns',0)} turns{Color.R}\n")
            except KeyboardInterrupt: break
            except Exception as e: print(f"\n  {Color.er(str(e))}\n")

    def _show_status(self):
        self._clear()
        print(f"\n  {Color.ti(' 状态看板 ')}\n")
        if not self.agent: print('  未加载');input('\n  回车返回...');return
        try:
            s=self.agent.get_status()
            print(f"  名称: {s.get('name','?')}  版本: {s.get('version','?')}")
            print(f"  LLM: {s.get('llm_model','?')}  任务数: {s.get('task_count',0)}")
            m=s.get('memory',{})
            print(f"\n  记忆: {m.get('total',0)}条 ({m.get('mode','?')})")
            e=s.get('evolution',{})
            print(f"  进化: {e.get('total_evolutions',0)}次")
            for lv,c in sorted(e.get('by_level',{}).items()): print(f"    L{lv}: {c}次")
            t=s.get('task_stats',{})
            if t: print(f"  任务: {t.get('total',0)}完成, {t.get('success_rate',0)}%成功率")
            # 补充飞书/Cron状态
            print(f"\n  {Color.D}{'-'*20}{Color.R}")
            print(f"  飞书: {Color.ok('运行中') if self.feishu_bot and self.feishu_bot._running else Color.wa('未启动')}")
            print(f"  Cron: {Color.ok('运行中') if self.cron_scheduler else Color.wa('未启动')}")
        except Exception as e: print(f"  {Color.er(str(e))}")
        input(f"\n  {Color.D}回车返回{Color.R}")

    def _feishu_status(self):
        self._clear()
        print(f"\n  {Color.ti(' 飞书状态 ')}\n")
        if not self.feishu_bot:
            print(f"  {Color.wa('飞书Bot未启动')}")
            input(f"\n  {Color.D}回车返回{Color.R}")
            return
        print(f"  运行中: {Color.ok('是') if self.feishu_bot._running else Color.er('否')}")
        print(f"  消息处理: {getattr(self.feishu_bot, '_processed_count', 0)} 条")
        print(f"  错误: {getattr(self.feishu_bot, '_error_count', 0)} 次")
        if self.feishu_bot._last_error:
            print(f"  最后错误: {self.feishu_bot._last_error}")
        input(f"\n  {Color.D}回车返回{Color.R}")

    def _quick_tasks(self):
        if not self.agent: input(f"\n  {Color.er('未加载')}");return
        self._clear()
        print(f"\n  {Color.ti(' 快速任务 ')}\n")
        ts=[('搜索新闻','搜索今天最新科技新闻'),('系统信息','查看CPU内存磁盘'),
            ('文件结构','列出项目目录'),('运行测试','运行测试用例'),
            ('Git状态','查看Git状态'),('搜索记忆','搜索记忆系统')]
        for i,(n,p) in enumerate(ts,1): print(f"  {Color.CYN}{i}{Color.R}) {n} - {Color.D}{p}{Color.R}")
        print(f"  {Color.CYN}0{Color.R}) 返回")
        c=input(f"\n  {Color.B}选择: {Color.R}").strip()
        if c=='0' or not c.isdigit(): return
        i=int(c)-1
        if 0<=i<len(ts):
            print(f"\n  {Color.inf('执行中...')}\n")
            try:
                r=self.agent.run(ts[i][1])
                ic=Color.ok('OK') if r['success'] else Color.er('FAIL')
                print(f"  [{ic}] {r.get('result','')}")
            except Exception as e: print(f"  {Color.er(str(e))}")
            input(f"\n  {Color.D}回车继续{Color.R}")

    def _show_logs(self):
        self._clear()
        print(f"\n  {Color.ti(' 日志 ')}\n")
        ld=ROOT_DIR/'logs'
        logs=list(ld.glob('*.log')) if ld.exists() else []
        if not logs: print('  无日志');input('  回车返回...');return
        logs.sort(key=lambda p:p.stat().st_mtime,reverse=True)
        for i,f in enumerate(logs[:10],1):
            print(f"  {Color.CYN}{i}{Color.R}) {f.name} {Color.D}({f.stat().st_size}B){Color.R}")
        print(f"  {Color.CYN}0{Color.R}) 返回")
        c=input(f"\n  {Color.B}查看哪个? {Color.R}").strip()
        if c=='0' or not c.isdigit(): return
        i=int(c)-1
        if 0<=i<len(logs):
            content=logs[i].read_text(errors='replace')
            print(f"\n{Color.D}{'-'*60}{Color.R}")
            print('\n'.join(content.split('\n')[-50:]))
            print(f"\n{Color.D}{'-'*60}{Color.R}")
            input('  回车返回...')

    def _sys_monitor(self):
        self._clear()
        print(f"\n  {Color.ti(' 系统监控 ')}\n")
        try:
            with open('/proc/meminfo') as f: mem=f.read()
            t=a=0
            for l in mem.split('\n'):
                if l.startswith('MemTotal:'): t=int(l.split()[1])//1024
                elif l.startswith('MemAvailable:'): a=int(l.split()[1])//1024
            u=t-a;p=u*100//t if t else 0
            bar='#'*(p//5)+'.'*(20-p//5)
            print(f"  内存: [{bar}] {u}MB/{t}MB ({p}%)")
        except: print(f"  {Color.wa('无法读取内存')}")
        try:
            with open('/proc/loadavg') as f: l=f.read().split()[:3]
            print(f"  CPU负载: {'  '.join(l)}")
        except: pass
        try:
            st=os.statvfs('/')
            t=st.f_frsize*st.f_blocks//(1024**3)
            f=st.f_frsize*st.f_bfree//(1024**3)
            u=t-f;p=u*100//t if t else 0
            bar='#'*(p//5)+'.'*(20-p//5)
            print(f"  磁盘: [{bar}] {u}G/{t}G ({p}%)")
        except: pass
        input(f"\n  {Color.D}回车返回{Color.R}")

    def _evo_history(self):
        self._clear()
        print(f"\n  {Color.ti(' 进化历史 ')}\n")
        try:
            if self.agent:
                mems=self.agent.memory.recall('evolution',limit=20)
                if mems:
                    for m in mems[-10:]:
                        print(f"  {Color.D}{m.get('key','?')}{Color.R} {m.get('content','')[:80]}")
                else: print('  暂无进化记录')
        except Exception as e: print(f"  {Color.er(str(e))}")
        input(f"\n  {Color.D}回车返回{Color.R}")

    def _show_id(self):
        self._clear()
        print(f"\n  {Color.ti(' 身份声明 ')}\n")
        try:
            from core.identity import load_identity_statement
            print(load_identity_statement())
        except:
            idp=ROOT_DIR/'IDENTITY.md'
            if idp.exists(): print(idp.read_text())
        input(f"\n  {Color.D}回车返回{Color.R}")

    def _env_check(self):
        self._clear()
        print(f"\n  {Color.ti(' 环境检查 ')}\n")
        print(f"  Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        for mod in ['core.main','core.identity','core.sandbox','core.memory_api','core.evolution','core.llm','core.agent_loop','core.feishu_bot','core.cron_scheduler','core.skill_resolver','core.tool_registry','core.session_store','core.context_compress','core.safety']:
            try: __import__(mod);print(f"  {mod}: {Color.ok('OK')}")
            except Exception as e: print(f"  {mod}: {Color.er(str(e))}")
        env_path=ROOT_DIR/'.env'
        if env_path.exists():
            with open(env_path) as f:
                for l in f:
                    if 'DEEPSEEK' in l: print(f"  {l.split('=')[0]}: {Color.ok('已配置')}")
        else: print(f"  .env: {Color.wa('未找到')}")
        input(f"\n  {Color.D}回车返回{Color.R}")

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--status',action='store_true')
    p.add_argument('--task',nargs='?')
    args=p.parse_args()
    launcher=KuafuLauncher()
    if args.status: launcher._show_status()
    elif args.task:
        if launcher.agent:
            r=launcher.agent.run(args.task)
            print(r.get('result',''))
        else: print('Agent未加载')
    else: launcher.show_menu()

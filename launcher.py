#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夸父启动器 (Kuafu Launcher)"""
import os,sys,json,time,argparse
from pathlib import Path
from datetime import datetime
ROOT_DIR=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT_DIR))

class Color:
    R="[0m";B="[1m";D="[2m"
    RED="[91m";GRN="[92m";YLW="[93m"
    BLU="[94m";MAG="[95m";CYN="[96m";BGB="[44m"
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

BANNER = """    _                     __
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
        try:
            from core.main import KuafuAgent
            self.agent=KuafuAgent()
        except Exception as e:
            print(f"  {Color.er('加载失败:')} {e}")

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
            print(f"  {Color.CYN}0{Color.R}) 退出")
            print(f"  {Color.D}{'-'*50}{Color.R}")
            c=input(f"
  {Color.B}请选择 [0-8]: {Color.R}").strip()
            if c=='1': self._interactive()
            elif c=='2': self._show_status()
            elif c=='3': self._quick_tasks()
            elif c=='4': self._show_logs()
            elif c=='5': self._sys_monitor()
            elif c=='6': self._evo_history()
            elif c=='7': self._show_id()
            elif c=='8': self._env_check()
            elif c=='0': print(f"
  {Color.inf('再见！')}");break
            else: input(f"
  {Color.wa('无效')}")

    def _interactive(self):
        if not self.agent: input(f"
  {Color.er('未加载')}");return
        self._clear()
        print(f"
  {Color.ti(' 交互模式 ')}")
        print(f"  {Color.D}{'-'*50}{Color.R}")
        print('  输入exit返回')
        print(f"  {Color.D}{'-'*50}{Color.R}
")
        while True:
            try:
                t=input(f"  {Color.B}夸父> {Color.R}").strip()
                if t.lower() in ('exit','quit','q','back'): break
                if not t: continue
                print(f"
  {Color.inf('思考中...')}
")
                r=self.agent.run(t)
                ic=Color.ok('OK') if r['success'] else Color.er('FAIL')
                print(f"  [{ic}] {r.get('result','')}")
                if r.get('evolution'):
                    e=r['evolution'];print(f"
  {Color.hl('进化:')} L{e.level} - {e.action}")
                print(f"
  {Color.D}{r.get('duration',0)}s | {r.get('turns',0)} turns{Color.R}
")
            except KeyboardInterrupt: break
            except Exception as e: print(f"
  {Color.er(str(e))}
")

    def _show_status(self):
        self._clear()
        print(f"
  {Color.ti(' 状态看板 ')}
")
        if not self.agent: print('  未加载');input('
  回车返回...');return
        try:
            s=self.agent.get_status()
            print(f"  名称: {s.get('name','?')}  版本: {s.get('version','?')}")
            print(f"  LLM: {s.get('llm_model','?')}  任务数: {s.get('task_count',0)}")
            m=s.get('memory',{})
            print(f"
  记忆: {m.get('total',0)}条 ({m.get('mode','?')})")
            e=s.get('evolution',{})
            print(f"  进化: {e.get('total_evolutions',0)}次")
            for lv,c in sorted(e.get('by_level',{}).items()): print(f"    L{lv}: {c}次")
            t=s.get('task_stats',{})
            if t: print(f"  任务: {t.get('total',0)}完成, {t.get('success_rate',0)}%成功率")
        except Exception as e: print(f"  {Color.er(str(e))}")
        input(f"
  {Color.D}回车返回{Color.R}")

    def _quick_tasks(self):
        if not self.agent: input(f"
  {Color.er('未加载')}");return
        self._clear()
        print(f"
  {Color.ti(' 快速任务 ')}
")
        ts=[('搜索新闻','搜索今天最新科技新闻'),('系统信息','查看CPU内存磁盘'),
            ('文件结构','列出项目目录'),('运行测试','运行测试用例'),
            ('Git状态','查看Git状态'),('搜索记忆','搜索记忆系统')]
        for i,(n,p) in enumerate(ts,1): print(f"  {Color.CYN}{i}{Color.R}) {n} - {Color.D}{p}{Color.R}")
        print(f"  {Color.CYN}0{Color.R}) 返回")
        c=input(f"
  {Color.B}选择: {Color.R}").strip()
        if c=='0' or not c.isdigit(): return
        i=int(c)-1
        if 0<=i<len(ts):
            print(f"
  {Color.inf('执行中...')}
")
            try:
                r=self.agent.run(ts[i][1])
                ic=Color.ok('OK') if r['success'] else Color.er('FAIL')
                print(f"  [{ic}] {r.get('result','')}")
            except Exception as e: print(f"  {Color.er(str(e))}")
            input(f"
  {Color.D}回车继续{Color.R}")

    def _show_logs(self):
        self._clear()
        print(f"
  {Color.ti(' 日志 ')}
")
        ld=ROOT_DIR/'logs'
        logs=list(ld.glob('*.log')) if ld.exists() else []
        if not logs: print('  无日志');input('  回车返回...');return
        logs.sort(key=lambda p:p.stat().st_mtime,reverse=True)
        for i,f in enumerate(logs[:10],1):
            print(f"  {Color.CYN}{i}{Color.R}) {f.name} {Color.D}({f.stat().st_size}B){Color.R}")
        print(f"  {Color.CYN}0{Color.R}) 返回")
        c=input(f"
  {Color.B}查看哪个? {Color.R}").strip()
        if c=='0' or not c.isdigit(): return
        i=int(c)-1
        if 0<=i<len(logs):
            content=logs[i].read_text(errors='replace')
            print(f"
  {Color.D}{'-'*60}{Color.R}")
            print('
'.join(content.split('
')[-50:]))
            print(f"  {Color.D}{'-'*60}{Color.R}")
            input('  回车返回...')

    def _sys_monitor(self):
        self._clear()
        print(f"
  {Color.ti(' 系统监控 ')}
")
        try:
            with open('/proc/meminfo') as f: mem=f.read()
            t=a=0
            for l in mem.split('
'):
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
        input(f"
  {Color.D}回车返回{Color.R}")

    def _evo_history(self):
        self._clear()
        print(f"
  {Color.ti(' 进化历史 ')}
")
        try:
            if self.agent:
                mems=self.agent.memory.recall('evolution',limit=20)
                if mems:
                    for m in mems[-10:]:
                        print(f"  {Color.D}{m.get('key','?')}{Color.R} {m.get('content','')[:80]}")
                else: print('  暂无进化记录')
        except Exception as e: print(f"  {Color.er(str(e))}")
        input(f"
  {Color.D}回车返回{Color.R}")

    def _show_id(self):
        self._clear()
        print(f"
  {Color.ti(' 身份声明 ')}
")
        try:
            from core.identity import load_identity_statement
            print(load_identity_statement())
        except:
            idp=ROOT_DIR/'IDENTITY.md'
            if idp.exists(): print(idp.read_text())
        input(f"
  {Color.D}回车返回{Color.R}")

    def _env_check(self):
        self._clear()
        print(f"
  {Color.ti(' 环境检查 ')}
")
        print(f"  Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        for mod in ['core.main','core.identity','core.sandbox','core.memory_api','core.evolution','core.llm','core.agent_loop']:
            try: __import__(mod);print(f"  {mod}: {Color.ok('OK')}")
            except Exception as e: print(f"  {mod}: {Color.er(str(e))}")
        env_path=ROOT_DIR/'.env'
        if env_path.exists():
            with open(env_path) as f:
                for l in f:
                    if 'DEEPSEEK' in l: print(f"  {l.split('=')[0]}: {Color.ok('已配置')}")
        else: print(f"  .env: {Color.wa('未找到')}")
        input(f"
  {Color.D}回车返回{Color.R}")

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

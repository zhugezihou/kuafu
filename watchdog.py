#!/usr/bin/env python3
"""
夸父守护程序 (watchdog.py)
监控夸父进程，被关闭时自动重启。

用法:
  python watchdog.py          # 前台运行守护
  python watchdog.py start    # 后台运行守护
  python watchdog.py stop     # 停止守护
  python watchdog.py status   # 查看状态
"""

import os
import sys
import time
import signal
import subprocess
import atexit

KUAFFU_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(KUAFFU_DIR, ".watchdog.pid")
KUAFU_PID_FILE = os.path.join(KUAFFU_DIR, ".kuafu.pid")
LOG_FILE = os.path.join(KUAFFU_DIR, "logs", "watchdog.log")
KUAFU_LOG = os.path.join(KUAFFU_DIR, "logs", "kuafu.log")


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    print(line)


def read_pid(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_pid(path, pid):
    with open(path, "w") as f:
        f.write(str(pid))


def pid_alive(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def find_kuafu_pid():
    """从 /proc 中找到实际运行的夸父进程 PID（命令行包含 core.cli 的 python 进程）。"""
    import glob
    for proc_dir in sorted(glob.glob("/proc/[0-9]*"), key=lambda x: int(os.path.basename(x))):
        try:
            pid = int(os.path.basename(proc_dir))
            comm = open(os.path.join(proc_dir, "comm")).read().strip()
            if comm not in ("python", "python3"):
                continue
            cmdline = open(os.path.join(proc_dir, "cmdline"), "rb").read().decode("utf-8", errors="replace")
            if "core.cli" in cmdline and "watchdog" not in cmdline:
                return pid
        except (IOError, ValueError, OSError):
            continue
    return None


def start_kuafu():
    log("🚀 启动夸父...")
    proc = subprocess.Popen(
        ["bash", "kuafu.sh", "gateway", "start"],
        cwd=KUAFFU_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    time.sleep(1.5)  # 等待 exec 替换完成
    real_pid = find_kuafu_pid()
    if real_pid:
        write_pid(KUAFU_PID_FILE, real_pid)
        log(f"✅ 夸父已启动 (PID: {real_pid})")
        return real_pid
    else:
        log(f"⚠️ 启动后未找到夸父进程 (bash PID: {proc.pid})")
        return None


def stop_kuafu():
    pid = read_pid(KUAFU_PID_FILE)
    if pid and pid_alive(pid):
        log(f"⏹ 停止夸父 (PID: {pid})...")
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
        except OSError:
            pass
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        log("⏹ 夸父已停止")
    # 清理 gateway 进程
    try:
        result = subprocess.run(
            ["pgrep", "-f", "kuafu.*gateway"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split():
            if line:
                os.kill(int(line), signal.SIGKILL)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    for f in [KUAFU_PID_FILE]:
        if os.path.exists(f):
            os.remove(f)


def watchdog_loop():
    pid = os.getpid()
    write_pid(PID_FILE, pid)
    log(f"🛡 守护程序启动 (PID: {pid})")

    # 启动夸父
    kuafu_pid = read_pid(KUAFU_PID_FILE)
    if not kuafu_pid or not pid_alive(kuafu_pid):
        kuafu_pid = start_kuafu()

    # 监控循环
    while True:
        time.sleep(5)
        if not kuafu_pid or not pid_alive(kuafu_pid):
            log(f"⚠️ 夸父进程 (PID: {kuafu_pid}) 已关闭，正在重启...")
            kuafu_pid = start_kuafu()
            # 如果启动失败，等久一点再重试
            if not kuafu_pid:
                log("⏳ 启动失败，10秒后重试...")
                time.sleep(10)


def daemonize():
    """后台运行"""
    pid = os.fork()
    if pid > 0:
        # 父进程退出
        print(f"🛡 夸父守护程序已启动 (PID: {pid})")
        sys.exit(0)
    # 子进程
    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        sys.exit(0)
    # 重定向标准流
    with open(os.devnull, "w") as f:
        os.dup2(f.fileno(), sys.stdin.fileno())
        os.dup2(f.fileno(), sys.stdout.fileno())
        os.dup2(f.fileno(), sys.stderr.fileno())
    watchdog_loop()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "foreground"

    if cmd == "start":
        existing = read_pid(PID_FILE)
        if existing and pid_alive(existing):
            print(f"⚠️ 守护程序已在运行 (PID: {existing})")
            return
        daemonize()
    elif cmd == "stop":
        pid = read_pid(PID_FILE)
        if pid and pid_alive(pid):
            log(f"⏹ 停止守护程序 (PID: {pid})...")
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        stop_kuafu()
        print("⏹ 夸父守护程序已停止")
    elif cmd == "status":
        wd_pid = read_pid(PID_FILE)
        kf_pid = read_pid(KUAFU_PID_FILE)
        print("═══ 夸父守护状态 ═══")
        if wd_pid and pid_alive(wd_pid):
            print(f"🛡 守护程序: 运行中 (PID: {wd_pid})")
        else:
            print("🛡 守护程序: 未运行")
        if kf_pid and pid_alive(kf_pid):
            print(f"🚀 夸父进程: 运行中 (PID: {kf_pid})")
        else:
            print("🚀 夸父进程: 未运行")
        print("📋 最近日志:")
        try:
            with open(LOG_FILE) as f:
                lines = f.readlines()
                for line in lines[-5:]:
                    print(f"  {line.strip()}")
        except FileNotFoundError:
            print("  (无日志)")
    else:
        print("用法: python watchdog.py {start|stop|status}")


if __name__ == "__main__":
    main()

#!/bin/bash
echo "杀掉旧 watchdog 进程 (PID 22592)..."
kill 22592 2>/dev/null
sleep 1
echo "启动新版 watchdog..."
cd /home/asus/kuafu && python3 watchdog.py &
echo "重启完成！"

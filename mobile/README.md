# 夸父 (Kuafu) · 手机版

> 把自己的 AI Agent 揣进兜里 🚀

## 快速开始

```bash
# 1. 在 Termux 中运行一键安装
curl -fsSL https://raw.githubusercontent.com/zhugezihou/kuafu/main/mobile/install-mobile.sh | bash

# 2. 启动夸父
bash mobile/start-mobile.sh

# 3. 手机浏览器打开
#    http://127.0.0.1:8080/
```

## 系统要求

| 项目 | 要求 |
|------|------|
| **手机** | Android 11+ / iOS (通过 iSH) |
| **SoC** | 骁龙 8 Gen 2+ / 天玑 9000+ / A17+ |
| **内存** | 8GB+ (推荐 12GB+) |
| **存储** | 8GB+ 空闲空间 (模型 4.7GB) |
| **环境** | Termux (Android) / iSH (iOS) |

**推荐设备**: 小米 17 Pro Max (Snapdragon 8 Elite Gen 5, 16GB)

## 手机端目录说明

```
mobile/
├── web_server.py       # Web UI 服务器 (纯 Python http.server)
├── static/
│   └── chat.html       # 聊天界面 (移动端优化 SPA)
├── install-mobile.sh   # 一键安装脚本 (Termux)
├── termux-daemon.sh    # 后台守护进程 (心跳保活)
└── start-mobile.sh     # 快速启动脚本
```

## 启动方式

### 1. 直接启动（推荐）
```bash
bash mobile/start-mobile.sh
```

### 2. 守护进程模式（后台保活）
```bash
bash mobile/termux-daemon.sh start     # 启动
bash mobile/termux-daemon.sh stop      # 停止
bash mobile/termux-daemon.sh status    # 查看状态
bash mobile/termux-daemon.sh logs      # 查看日志
```

### 3. 开机自启
安装 Termux:Boot 后，夸父会自动随 Termux 启动。

### 4. 手动启动
```bash
python mobile/web_server.py --port 8080 --host 0.0.0.0
```

## 使用方式

**手机浏览器**: `http://127.0.0.1:8080/`
**电脑浏览器**: `http://<手机IP>:8080/` (同一 WiFi)

## 模型选择

| 模型 | 大小 | 内存需求 | 建议 |
|------|------|---------|------|
| Qwen3-4B-Q4_K_M | 2.5GB | 4GB+ | 轻量，适合旧设备 |
| Qwen3-8B-Q4_K_M | 4.7GB | 8GB+ | **推荐**，性能/体积平衡 |
| Qwen3-14B-Q4_K_M | 8.5GB | 12GB+ | 高性能，适合旗舰机 |

## 架构

```
┌─────────────────────────────────┐
│         手机浏览器               │
│    (Chrome / Safari)            │
└──────────────┬──────────────────┘
               │ HTTP
┌──────────────▼──────────────────┐
│     Web UI (Flask 纯 Python)     │
│     mobile/web_server.py         │
│     零外部依赖，http.server      │
└──────┬──────────────────┬───────┘
       │                  │
┌──────▼──────┐   ┌──────▼──────────┐
│ llama-server│   │  云端 DeepSeek   │
│ (本地推理)   │   │  (fallback)     │
│ ~20 tok/s   │   │  ~60 tok/s      │
└─────────────┘   └─────────────────┘
```

## 已知限制

- **审批系统**: 手机端无飞书，终端命令自动放行（需修改 `core/approval.py`）
- **联网能力**: 云端模式需要 WiFi/数据网络
- **后台保活**: 部分 Android 系统会杀后台，建议启用开发者选项中的「不保留活动」关掉或给 Termux 加锁

## PC 端开发

手机版是夸父的 Web 前端，PC 端开发请用:
```bash
bash kuafu.sh                 # WSL 环境
python -m core.main           # 命令行
```

## 许可证

MIT

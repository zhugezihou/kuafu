# 夸父消息通道系统

> 多平台消息通道的统一抽象、热加载、网关集成。

## 架构概览

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Feishu WS   │     │  WeChat      │     │  其他通道    │
│  Channel     │     │  iLink       │     │  (可扩展)    │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └──────────┬─────────┴──────────┬─────────┘
                  ▼                    ▼
         ┌─────────────────────────────────┐
         │        ChannelManager           │
         │  register / remove / poll_all   │
         │  load_channel / reload_channel  │
         │  discover_channels (热加载)      │
         └──────────────┬──────────────────┘
                        │
         ┌──────────────▼──────────────────┐
         │        GatewayLoop              │
         │  poll → agent.run → send        │
         │  审批决策检测 / 审批卡片推送     │
         └──────────────┬──────────────────┘
                        │
         ┌──────────────▼──────────────────┐
         │        Gateway HTTP API         │
         │  /api/channel/{discover,list,   │
         │    load,remove,reload}          │
         └─────────────────────────────────┘
```

## 核心模块

### `channel/base.py` — 抽象基类

```python
@dataclass
class Message:
    text: str          # 消息文本
    msg_id: str        # 消息 ID
    platform: str      # 平台标识（"feishu", "wechat" 等）
    chat_id: str       # 来源群聊/频道 ID
    sender: str        # 发送者 ID
    sender_name: str   # 发送者显示名
    raw: dict          # 原始消息数据

@dataclass
class SendResult:
    success: bool
    msg_id: str = ""
    platform: str = ""
    error: str = ""

class MessageChannel(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...    # 通道唯一标识

    @abstractmethod
    def send(self, text: str, **kwargs) -> SendResult: ...
    @abstractmethod
    def poll(self) -> list[Message]: ...

    def start(self) -> None: ...  # 可选
    def stop(self) -> None: ...   # 可选
```

任何新的通道实现只需继承 `MessageChannel` 并实现 `name` / `send` / `poll` 三个方法。

### `channel/manager.py` — 通道管理器

| 方法 | 说明 |
|------|------|
| `register(channel)` | 注册通道，若名称已存在则自动 stop 旧通道再替换 |
| `get(name)` | 按名称获取通道实例 |
| `list()` | 返回所有已注册通道名称列表 |
| `remove(name)` | 移除并 stop 指定通道 |
| `restart(name)` | 重启通道（stop → start） |
| `start_all()` | 启动所有已注册通道 |
| `stop_all()` | 停止所有通道 |
| `poll_all()` | 轮询所有通道的新消息 |
| `broadcast(text)` | 向所有通道广播消息 |

**热加载能力：**

| 类方法 | 说明 |
|--------|------|
| `discover_channels()` | 扫描 `core.channel` 包，自动发现所有 `MessageChannel` 子类 |
| `load_channel(name)` | 从发现注册表中加载并启动一个通道 |
| `reload_channel(name)` | 热重载一个通道（stop → unregister → load → start） |
| `refresh_all()` | 从注册表重新加载所有可发现的通道 |

`discover_channels()` 会自动实例化每个通道类一次来探测 `name` 属性，因此新通道类只要放在 `core/channel/` 目录下就会被自动发现，**零注册配置**。

### `channel/gateway_loop.py` — 消息消费循环

`GatewayLoop` 是消息处理的引擎：

```
循环: poll_all() → 每条消息 → 审批决策检测 → agent.run() → send()
```

- **审批决策检测** — 用户回复 `1 abc12345` 批准 / `0 abc12345` 拒绝
- **审批卡片推送** — 飞书卡片按钮审批（带回调注册）
- **消息源关联** — 记录最近消息的来源通道和 chat_id，确保审批回复推送到正确位置

### 内置通道

#### 飞书 WebSocket 直连（`feishu_ws.py`）

- 使用 `lark-oapi` SDK 建立 WebSocket 长连接
- **@bot 过滤** — 群聊消息必须 @夸父 才处理，私聊（p2p）无需 @
- **消息去重** — 按 msg_id 缓存，防止 WS 重连后的消息重放
- **时间过滤** — 绕过 WS 连接前的历史消息
- **审批卡片** — 发送 interactive card 按钮，接收卡片回调执行审批决策

环境变量：
- `FEISHU_APP_ID` — 飞书应用 App ID
- `FEISHU_APP_SECRET` — 飞书应用 App Secret
- `FEISHU_CHAT_ID` — 默认飞书群聊天 ID

#### 微信 iLink 通道（`wechat_ilink.py`）

- 腾讯官方 iLink Bot 协议，扫码登录
- **扫码登录** — 首次启动输出二维码，扫码后自动持久化 token
- **长轮询收消息** — `getupdates` API
- **自动重登录** — token 过期时重新输出二维码

环境变量：
- `WECHAT_ILINK_DATA_DIR` — iLink 持久化数据存储目录（可选，默认 `memory/`）

#### 如何新增一个通道

1. 在 `core/channel/` 下新建文件，继承 `MessageChannel`
2. 实现 `name` / `send` / `poll`（和可选的 `start` / `stop`）
3. 无需修改任何注册代码，`discover_channels()` 会自动发现

## HTTP API（通过 Gateway）

所有通道管理通过 Gateway HTTP Server 暴露为 REST 接口。

### 端点列表

```
GET  /api/channel/list       → 列出已注册通道及运行状态
GET  /api/channel/discover   → 扫描所有可用的通道类

POST /api/channel/load       → {"name": "feishu"}    热加载通道
POST /api/channel/remove     → {"name": "wechat"}    热移除通道
POST /api/channel/reload     → {"name": "feishu"}    热重载通道
```

### 请求示例

```bash
# 发现可用通道
curl http://127.0.0.1:8765/api/channel/discover
# → {"discovered": {"feishu": "FeishuWebSocketChannel", "wechat": "WeChatILinkChannel"}}

# 列出已注册通道
curl http://127.0.0.1:8765/api/channel/list
# → {"channels": [{"name": "feishu", "running": true}, {"name": "wechat", "running": true}]}

# 热加载通道
curl -X POST http://127.0.0.1:8765/api/channel/load \
  -H "Content-Type: application/json" \
  -d '{"name": "feishu"}'
# → {"status": "loaded", "name": "feishu"}

# 热移除通道
curl -X POST http://127.0.0.1:8765/api/channel/remove \
  -H "Content-Type: application/json" \
  -d '{"name": "wechat"}'
# → {"status": "removed", "name": "wechat"}

# 热重载通道（stop旧→start新）
curl -X POST http://127.0.0.1:8765/api/channel/reload \
  -H "Content-Type: application/json" \
  -d '{"name": "feishu"}'
# → {"status": "reloaded", "name": "feishu"}
```

## CLI 命令

所有通道管理操作也可以通过 `kuafu channel` CLI 命令执行，底层通过 HTTP 通信。

| 命令 | 说明 |
|------|------|
| `kuafu channel list` | 列出已注册通道及运行状态 |
| `kuafu channel discover` | 扫描所有可用通道类 |
| `kuafu channel load <name>` | 热加载指定通道 |
| `kuafu channel remove <name>` | 移除并停止指定通道 |
| `kuafu channel reload <name>` | 热重载指定通道 |

所有命令支持 `--port` 参数指定 Gateway 端口（默认 8765）。

### 使用示例

```bash
# 需要先启动 Gateway
kuafu gateway start

# 另一个终端：发现可用通道
kuafu channel discover
# → 发现 2 个通道类:
#     • feishu               FeishuWebSocketChannel  (已加载)
#     • wechat               WeChatILinkChannel

# 热加载飞书通道
kuafu channel load feishu
# → ✅ 通道已加载: feishu

# 查看通道列表
kuafu channel list
# → 名称                  状态
#    ------------------------------
#    feishu              ✅ 运行中
#    wechat              ⏹ 已停止

# 热移除微信通道
kuafu channel remove wechat
# → ✅ 通道已移除: wechat

# 热重载飞书通道
kuafu channel reload feishu
# → ✅ 通道已热重载: feishu
```

## 热加载机制

热加载允许在 Gateway 运行时动态添加/移除/重载消息通道，**无需重启 Gateway 进程**。

### 工作流程

```
1. discover_channels()
   ├── 扫描 core/channel/ 包下所有模块
   ├── 查找 MessageChannel 的子类
   ├── 探测实例化获取 name 属性
   └── 填充 _CHANNEL_REGISTRY 注册表

2. load_channel("feishu")
   ├── 从注册表获取 FeishuWebSocketChannel 类
   ├── 实例化 → register() → 若已存在则 stop 旧实例
   ├── channel.start()
   └── GatewayLoop.poll_all() 自动覆盖新通道

3. remove("wechat")
   ├── 从 _channels 字典 pop
   ├── channel.stop()
   └── GatewayLoop.poll_all() 自动跳过已移除的通道

4. reload_channel("feishu")
   └── remove("feishu") → load_channel("feishu")
```

### 设计要点

- **`register()` 天然支持替换** — 同名通道自动 stop 旧实例再注册新实例
- **`poll_all()` 无锁遍历** — poll/remove 之间无竞态，因为 `_channels` 是 Python dict（CPython GIL 保护的赋值是原子的）
- **`GatewayLoop` 无感知** — 只调用 `channel_manager.poll_all()`，通道增减对循环透明

## 消息流

一条用户消息从接收到回复的完整链路：

```
用户消息
   │
   ▼
Channel.poll()          ← 通道层收取消息
   │
   ▼
GatewayLoop.poll_all()  ← 轮询所有通道
   │
   ▼
GatewayLoop._handle_message(msg)
   │
   ├── 审批决策检测？→ 是 → ApprovalManager 处理 → 返回
   │
   └── 否 → agent.run(text)
                │
                ▼
            LLM → 工具调用循环 (ReAct)
                │
                ▼
            生成回复 text
                │
                ▼
            channel.send(reply, chat_id=msg.chat_id)
                │
                ▼
          用户收到回复
```

## 审批集成

GatewayLoop 在初始化时自动注册审批回调：

| 回调 | 触发时机 | 目标通道 |
|------|----------|----------|
| `on_approval_request`（agent 实例） | 工具需要审批时 | 触发审批的消息来源通道 |
| `ON_APPROVAL_REQUEST_CB`（全局模块） | 同上 | 同上 |
| `ON_CARD_APPROVAL_CB`（飞书） | 飞书卡片按钮点击时 | 回传审批决策 |

**审批消息格式：**

| 通道 | 格式 |
|------|------|
| 飞书 | interactive card（批准/拒绝按钮） |
| 微信 | `🔐 审批请求\n工具: terminal\nID: a1b2\n回复「1 a1b2」批准` |

**用户回复格式：**

| 格式 | 示例 | 说明 |
|------|------|------|
| 短指令 | `1 a1b2` | 批准，req_id 后 8 位 |
| 短指令 | `0 a1b2` | 拒绝 |
| 中文 | `批准 a1b2` / `拒绝 a1b2` | 文字指令 |
| 英文 | `approve a1b2` / `reject a1b2` | 英文指令 |

## 测试

通道系统有 12+ 个专项测试，覆盖：

```
✅ Channel: 数据类型（Message / SendResult）
✅ Channel: Manager 注册/启动/停止
✅ Channel: 热加载 发现/加载/移除/重载
✅ Feishu WebSocket: 初始化和发送
✅ Feishu WebSocket: 审批卡片构建
✅ E2E: MockChannel 基本能力
✅ E2E: Gateway 通道管理 API
✅ E2E: GatewayLoop 消息轮询与处理（Mock通道）
✅ E2E: 热加载时 GatewayLoop 不中断
✅ E2E: GatewayLoop 审批决策检测
✅ E2E: CLI channel 子命令解析
✅ E2E: Gateway HTTP 路由完整性
✅ E2E: Bypass 审批回调注入
```

```bash
# 运行全部通道测试
python test_all.py

# 只运行通道相关
python -c "
from test_all import *
test_channel_types()
test_channel_manager()
test_channel_hotload()
test_feishu_ws()
test_feishu_approval_card()
test_mock_channel()
test_gateway_channel_api()
test_gateway_loop_mock()
test_hotload_loop_continuity()
test_loop_approval_decision()
test_cli_channel_parsing()
test_gateway_routes()
test_approval_callback_injection()
"
```

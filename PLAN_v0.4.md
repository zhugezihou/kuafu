# 夸父 v0.4 — 开发计划

> 目标：补齐 P0 全部 4 项核心缺失，成为现代 Agent 框架
> 截止：2026-05-30
> 原则：零依赖哲学保留（核心仅 pyyaml），MCP 集成用 stdio 子进程

---

## 一、版本号更新

**文件修改：**
- `core/main.py`：`VERSION = "0.3.0"` → `VERSION = "0.4.0"`
- `pyproject.toml`：`version = "0.2.0"` → `version = "0.4.0"`

---

## 二、P0-1: MCP 协议支持（2-3 天）

### 目标
Agent 能发现、连接、调用 MCP Server 暴露的工具（stdio 模式）。

### 实现方式
**零依赖策略**：用 `subprocess` + `json-rpc` over stdio，不引入 `mcp` Python SDK。

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `core/mcp_bridge.py` | **新建** | MCP 桥接核心：`MCPServer` 类（stdio 子进程管理）、`MCPBridge` 类（多 Server 管理，动态注册工具到 ToolRegistry） |
| `core/mcp_config.yaml` | **新建** | MCP Server 配置文件（接近 MCP 标准格式） |
| `core/tool_registry.py` | **修改** | 加 `register_mcp_tools(mcp_bridge)` 方法，接收 MCP 工具列表注册到 Registry |
| `core/agent_loop.py` | **修改** | 初始化时加载 MCP 配置并注册工具 |

### 配置示例 (`mcp_config.yaml`)
```yaml
mcp_servers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/asus"]
  brave-search:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-brave-search"]
  sqlite:
    command: uvx
    args: ["mcp-server-sqlite", "--db-path", "/home/asus/test.db"]
```

### MCPBridge 核心接口
```python
class MCPServer:
    """管理单个 MCP Server 进程"""
    async def connect(self) -> bool       # 启动子进程 + 初始化
    async def list_tools(self) -> list     # 获取工具列表
    async def call_tool(self, name, args)  # 调用工具
    async def disconnect(self)             # 关闭子进程

class MCPBridge:
    """管理多个 MCP Server"""
    def load_config(self, path)           # 加载 YAML 配置
    def connect_all(self)                 # 连接所有 Server
    def get_all_tools(self) -> list       # 聚合所有工具 schema
    def register_to(self, registry)       # 注册到 ToolRegistry
```

### 关键细节
1. **JSON-RPC**：MCP 协议基于 JSON-RPC 2.0
   - 初始化：`{"jsonrpc":"2.0", "id":1, "method":"initialize", "params":{...}}`
   - 工具列表：`{"jsonrpc":"2.0", "id":2, "method":"tools/list", "params":{}}`
   - 工具调用：`{"jsonrpc":"2.0", "id":3, "method":"tools/call", "params":{"name":"...", "arguments":{...}}}`
2. **生命周期**：Agent 启动时连接，退出时断开
3. **错误处理**：Server 进程崩溃自动重启（最多 3 次）
4. **超时控制**：每个工具调用默认 30s 超时

---

## 三、P0-2: WebHook 事件驱动（1-2 天）

### 目标
外部系统可以通过 HTTP POST 触发夸父执行任务。

### 实现方式
用 Python `http.server`（标准库）轻量实现，零新增依赖。

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `core/webhook_server.py` | **新建** | 轻量 HTTP Server：`WebhookServer` 类，`/webhook/<token>` 端点，解析 payload 后投递到 AgentLoop |
| `core/main.py` | **修改** | 启动时可选启用 Webhook Server（`--webhook-port 8765`） |
| `core/webhook_config.yaml` | **新建** | WebHook 配置：端口、Token、认证方式 |

### WebhookServer 核心接口
```python
class WebhookServer:
    def __init__(self, port=8765, token="")
    def start(self)                       # 后台线程启动 HTTP server
    def stop(self)
    def register_hook(self, path, handler)  # 注册自定义路径处理器
```

### 端点设计
- `POST /webhook/<token>` — 通用 WebHook 入口
  - Body: `{"task": "...", "context": {...}}`
  - 响应: `{"status": "accepted", "task_id": "..."}`
- `GET /health` — 健康检查
- `POST /webhook/<token>/approve` — 审批（复用已有审批逻辑）

### 安全
- Token 认证（配置在 webhook_config.yaml）
- 可选 IP 白名单
- 请求体大小限制（默认 1MB）

---

## 四、P0-3: 多平台消息通道（3-5 天）

### 目标
夸父能通过 Telegram、Discord、邮件收发消息，不局限于飞书。

### 实现方式
抽象 `MessageChannel` 接口，各平台独立实现。保持零核心依赖原则，各通道为可选安装。

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `core/channel/__init__.py` | **新建** | 通道管理器，消息分发 |
| `core/channel/base.py` | **新建** | `MessageChannel` 抽象基类 |
| `core/channel/telegram.py` | **新建** | Telegram Bot（可选依赖 `python-telegram-bot`） |
| `core/channel/discord.py` | **新建** | Discord Bot（可选依赖 `discord.py`） |
| `core/channel/email.py` | **新建** | 邮件（标准库 `imaplib` + `smtplib`，零新增依赖） |
| `core/channel/feishu.py` | **新建** | 将现有 feishu_bot.py 重构为通道接口 |

### MessageChannel 接口
```python
class MessageChannel(ABC):
    name: str                              # 通道名
    async def send(self, msg, **kwargs)     # 发送消息
    async def poll(self) -> list[Message]   # 轮询新消息
    async def listen(self, callback)        # 长连接监听（可选）
    async def start(self)                   # 启动通道
    async def stop(self)                    # 停止通道
```

### 复用现有飞书实现
现有 `core/feishu_bot.py` 重构为新接口，保持向后兼容。

### pyproject.toml 扩展
```toml
[project.optional-dependencies]
telegram = ["python-telegram-bot>=20.0"]
discord = ["discord.py>=2.0"]
web = ["jinja2>=3.0"]
all = ["jinja2>=3.0", "rich>=13.0", "python-telegram-bot>=20.0", "discord.py>=2.0"]
```

---

## 五、P0-4: 子 Agent 系统（3-5 天）

### 目标
复杂任务能 spawn 隔离的子 Agent 并行执行，完成后汇总结果。

### 实现方式
新建 `subagent.py`：子 Agent 拥有独立的 AgentLoop 实例，上下文隔离、工具集受限、权限继承。

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `core/subagent.py` | **新建** | 子 Agent 核心：`SubAgent` 类、`SubAgentPool` 池管理 |
| `core/agent_loop.py` | **修改** | 加 `delegate_task` 工具（内部调用 SubAgent） |

### SubAgent 核心接口
```python
@dataclass
class SubAgentTask:
    goal: str
    context: str
    tools: list[str]          # 允许的工具列表（继承父权限）

class SubAgent:
    def __init__(self, task, parent_context, tool_whitelist):
        self.session = SessionStore()  # 隔离会话
        self.llm = LLMClient()         # 共享 LLM 但隔离对话
        self.tools = self._filter_tools(tool_whitelist)
    
    async def run(self) -> dict         # 执行子任务，返回结果
    def get_trace(self) -> list         # 返回工具调用记录

class SubAgentPool:
    def __init__(self, max_concurrent=3)
    async def spawn(self, task) -> SubAgent
    async def run_all(self, tasks) -> list[dict]  # 并行执行
```

### 新增工具：`delegate_task`
```json
{
  "name": "delegate_task",
  "description": "将有明确边界的子任务委托给隔离的子 Agent 执行",
  "parameters": {
    "goal": "任务目标",
    "context": "上下文信息",
    "tools": ["terminal", "read_file"]  // 可选，限制子 Agent 工具集
  }
}
```

### 安全设计
- 子 Agent 继承父 Agent 的权限（沙盒、路径白名单）
- 子 Agent 不能 spawn 子 Agent（防止递归）
- 子 Agent 超时上限 5 分钟
- 最大并发 3 个

---

## 六、P1-7: 记忆自动管理/清理（2-3 天）

### 目标
记忆系统自动去重、合并、过期清理，防止记忆膨胀污染检索质量。

### 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `core/memory_api.py` | **修改** | 添加自动清理逻辑 |
| `core/memory_config.yaml` | **新建** | 记忆管理配置 |

### 实现策略
1. **去重**：写入前检查相似度（关键词 overlap > 60% 视为重复，跳过）
2. **过期**：每条记忆加 `ttl` 字段，超时自动删除（默认 30 天）
3. **合并**：同一主题 3 条以上 → LLM 摘要合并为 1 条
4. **阈值过滤**：search 结果相关性低于 0.3 自动丢弃

---

## 七、开发顺序

| 阶段 | 内容 | 预估 |
|------|------|------|
| **Phase 1** | MCP 协议支持 | 2-3 天 |
| **Phase 2** | 子 Agent 系统 | 3-5 天 |
| **Phase 3** | 多平台消息通道 | 3-5 天 |
| **Phase 4** | WebHook 事件驱动 | 1-2 天 |
| **Phase 5** | 记忆自动管理 | 2-3 天 |
| **Phase 6** | 版本号更新 + 集成测试 | 1 天 |

**总预估：12-18 天**

---

## 八、测试计划

1. **MCP**：启动 mock MCP Server → 夸父调用工具 → 验证结果
2. **子 Agent**：spawn 2 个子 Agent 并行执行 → 验证隔离性 + 结果汇总
3. **多通道**：各通道发/收消息 → 验证消息路由正确
4. **WebHook**：curl POST 触发任务 → 验证 Agent 执行
5. **记忆管理**：写入 20 条相似记忆 → 验证去重+合并+过期

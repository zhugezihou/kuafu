# 夸父 Desktop v1 — 独立模式开发计划

## 目标
Desktop 脱离外部 Gateway，内嵌夸父引擎，用户开箱即用。

## 架构
```
┌─────────────────────────────────────┐
│  Tauri Desktop App                   │
│  ┌─────────────┐  ┌──────────────┐  │
│  │ Svelte 前端  │  │ Rust 后端    │  │
│  │ - Chat UI   │◄─┤ - Agent 进程 │  │
│  │ - Settings  │  │ - LLM 进程   │  │
│  │ - Markdown  │  │ - IPC Bridge │  │
│  └─────────────┘  └──────────────┘  │
└─────────────────────────────────────┘
```

## 阶段

### Phase 1: Rust 后端 — Agent 子进程管理
- Tauri 启动时 spawn `kuafu` 子进程（`python -m core.main --gateway-port 8081`）
- 监听进程退出，自动重启
- 前端通过 Tauri commands 调用 API，不再直连 localhost:8081（通过 Rust 转发）
- `src-tauri/src/agent.rs` — AgentManager 结构体

### Phase 2: 前端 — Settings 页面
- 模型选择（本地 GGUF / 云端 DeepSeek / OpenAI）
- API Key 管理
- 主题切换（暗/亮）
- 配置持久化（Tauri store plugin）

### Phase 3: 前端 — Markdown 渲染
- 代码块语法高亮
- 行号
- 复制按钮

### Phase 4: 前端 — 流式输出
- SSE 流接入打字机效果
- 逐 token 渲染

## 验收标准
1. Desktop 启动后自动拉起夸父引擎
2. 聊天收发正常，不需手动启动任何服务
3. 设置页面可切换模型
4. CI 自动构建 Windows .exe

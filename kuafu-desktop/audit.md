# Desktop Audit — 所有已发现的问题

## 1. 前端层 (Svelte 5 + TypeScript)

### 1.1 App.svelte — onMount clean-up 动态 import
- **问题**: `return () => { import("@tauri-apps/api/core").then(...) }` 
  在组件销毁时动态 import。Svelte 5 的 `onMount` return 函数在组件卸载时同步执行，
  但 `import()` 是异步的 → 页面卸载时可能来不及执行 `stop_agent`
- **修复**: onMount 开头预存 invoke 引用

### 1.2 App.svelte — `startAgentAsync()` 中的动态 import 每次调用都跑
- **问题**: 第 44 行 `const { invoke } = await import("@tauri-apps/api/core")` 
  每次启动都动态 import，且 `checkHealth()` 失败时没有错误提示给用户

### 1.3 App.svelte — `checkHealth()` 失败时无感知
- **问题**: 引擎启动失败后，状态栏显示离线，但没有明确的"点我重试"按钮
- 用户只能重新打开 app。需要加一个"重启引擎"按钮在错误横幅上

### 1.4 MessageList.svelte — 嵌套 `$effect`
- **问题**: 
```svelte
$effect(() => {
  if (msgContainer) {
    $effect(() => {
      $messages;
      requestAnimationFrame(...)
    });
  }
});
```
嵌套 `$effect` 在 Svelte 5 中有潜在性能问题。但这里只在 msgContainer 变化时创建，
不算严重。不过可以简化。

### 1.5 MessageList.svelte — 使用 `$messages` store 订阅语法
- Svelte 5 中 `$messages` 是 rune 语法还是 store？store.ts 用的是 `writable()` 
  (Svelte store)，要确认 MessageList.svelte 中能正确访问 `$messages`

### 1.6 store.ts — `saveSession()` 中 subscribe+立即调用导致内存泄漏
```typescript
export function saveSession() {
  let msgs: Message[] = [];
  messages.subscribe((m) => (msgs = m))();  // 立即调用 unsubscribe
}
```
- **问题**: 这个写法在 Svelte 5 中可能不工作。`subscribe` 返回的 unsubscribe 函数
  在 `subscribe` 回调第一次执行后调用，但 Svelte 5 的 store 行为可能有变化。
- 更安全的写法: `const msgs = get(messages)`

### 1.7 store.ts — `archiveCurrentSession()` 中同样的问题
- 同上，`messages.subscribe` 和 `currentSessionId.subscribe` 

### 1.8 MarkdownRenderer.svelte — `$effect` 中解析 markdown
- **问题**: `$effect` 中调 `marked.parse(content)`，如果 content 长可能卡顿。
- 应该用 `onMount` 或 debounce

### 1.9 StatusBar.svelte — 硬编码版本号
- 版本号每次都得手动改，应该从 `window.__TAURI_INTERNALS__` 或 Rust invoke 获取

## 2. Rust 层 (Tauri 2)

### 2.1 agent.rs — `start()` 中 800ms sleep 阻塞主线程
- **问题**: `std::thread::sleep(800ms)` 在 Tauri command handler 中执行，
  会阻塞 Tauri 的异步线程池
- **修复**: 用 tokio::time::sleep 或 spawn_blocking

### 2.2 agent.rs — Mutex 嵌套可能死锁
- **问题**: `start()` 中先 lock `self.process`，然后内部调用 `auto_setup()`，
  `auto_setup()` 不 lock，但 `status()` 方法 lock `self.process` — 如果
  `start()` 和 `status()` 同时调用可能出问题
- 但 Tauri 2 command 是顺序处理的，不会并发调用同一个 State → 实际风险低

### 2.3 agent.rs — `find_system_python()` 在 Windows 上找 `python3`
- **问题**: 第 84 行 `for name in &["python3", "python"]` — Windows 上没有 `python3`，
  但 `python` 可能指向 Microsoft Store 的 Python，启动很慢或失败
- 应该优先检查 `py.exe` (Python launcher)

### 2.4 agent.rs — 无健康检查重试
- **问题**: `start()` 启动子进程后 sleep 800ms 检查一次，但如果夸父启动慢于 800ms
  （首次启动需要 import 大量模块），会误判为启动失败
- **修复**: 轮询 5 秒，每 500ms 检查一次

### 2.5 agent.rs — stop() 直接 kill
- **问题**: `child.kill()` 直接杀掉进程，夸父的 session store 等没有 graceful shutdown
- 应该先发 SIGTERM，等一会再 SIGKILL

### 2.6 lib.rs — Mutex lock 结果用 unwrap
- `agent_status()` 中 `state.agent.lock().map(|mut a| a.status())` — 
  Mutex 被 poison 时返回的 `map_err` 后的 unwrap_or，丢失 poison 信息
- 但不算严重问题

## 3. 夸父 Gateway 层

### 3.1 Gateway 默认端口 8765，没问题 — Desktop 传了 --port 8081

### 3.2 Gateway /api/task 结果截断 2000 字符
- **问题**: `result.get("result", "")[:2000]` — 长回复被截断
- **修复**: 提到 50000 或不要截断

### 3.3 Gateway /api/status 返回字段 — 需要确认前端兼容
- 前端的 `checkHealth()` 只检查 `resp.ok`，不关心 body 内容 — OK

### 3.4 Gateway 的 CORS
- **问题**: Gateway 是 Python http.server，虽然加了 `Access-Control-Allow-Origin: *`，
  但 Desktop 前端直接通过 `localhost:8081` 没有跨域问题 — OK

### 3.5 Gateway agent.run() 可能没有初始化完成就接收请求
- `start()` 启动 HTTP 后立即可以接收请求，但 agent 可能还没初始化完
- 在 `start()` 中 health check 轮询会避免这个问题

## 4. 构建/打包层

### 4.1 build.bat — 应该加上 `--bundles nsis`
- 已经加了

### 4.2 build.bat — 需要确保 git submodule / core 路径正确
- 从 kuafu/kuafu-desktop/ 运行，夸父源码在 kuafu/core/ = ../core — 正确

### 4.3 build.bat — npm ci 会安装所有依赖，package.json 去掉了无用插件
- 已经去掉

## 5. CI 层

### 5.1 CI 没有 setup-python step
- **问题**: desktop-build job 没有 setup-python，运行 pip 会失败
- 但 CI 只负责打包不运行 pyyaml 安装 — 已经移除了

## 总结：必须修复的关键问题

| # | 严重度 | 层 | 问题 | 
|---|--------|-----|------|
| P0 | 🔴 严重 | Rust | start() 启动检测超时 800ms 太短，首次启动会误判失败 |
| P0 | 🔴 严重 | Rust | stop() 直接 kill 进程，导致 session 数据丢失 |
| P1 | 🟡 中等 | 前端 | onMount clean-up 中动态 import 可能不执行 stop_agent |
| P1 | 🟡 中等 | 前端 | saveSession() 用 subscribe 而非 get()，Svelte 5 兼容风险 |
| P1 | 🟡 中等 | Rust | find_system_python 不查 py.exe (Windows Python Launcher) |
| P2 | 🟢 低 | 前端 | 错误提示没有重试按钮 |
| P2 | 🟢 低 | Rust | sleep 800ms 阻塞异步线程池 |
| P2 | 🟢 低 | Gateway | /api/task 结果截断 2000 字符 |

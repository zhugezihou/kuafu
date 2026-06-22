# FIX.md — 修复清单

## 状态：第三批（架构级）全部完成 ✅

## T1: HTTP Server 单线程→ThreadedHTTPServer 🔴
- `HTTPServer` → `ThreadedHTTPServer(ThreadingMixIn, HTTPServer)`
- `allow_reuse_address=True`, `daemon_threads=True`
- 改前: 一个 `POST /api/task` 阻塞30s, 所有API排队
- 改后: 每个请求独立线程, 长任务不影响审批/health/cron管理

## T2: ApprovalManager._decision_events 加锁 🔴
- 加 `_decision_lock: ClassVar[threading.Lock]`
- `_get_event()` 中 `if req_id not in dict` + `dict[req_id]=ev` 整个包在锁内
- `approve()` 和 `reject()` 中 `pop` 操作也加锁
- 改前: 经典 check-then-act 竞态, 并发审批可能丢失 event → 超时300s
- 改后: 所有 `_decision_events` 操作串行化, 无竞态

## T3: 移除 ON_APPROVAL_REQUEST_CB 死代码 🟢
- 移除 `_lazy_init` 中的 import + if 检查 (7行)
- 顺便修复了注释缩进不对齐的排版bug
- 改前: 永不命中的死代码, 但优先级高于GatewayLoop注入
- 改后: 无死代码, 不污染回调链

## 验证结果（第三批）
- 语法检查: ✅ 3 文件通过
- 全量 import: ✅ 通过
- 功能验证: ✅ T1/2/3 各 2-3 项验证通过
- 并发安全验证: ✅ 4线程同时get_event 400次无竞态
- 测试回归: ✅ 4通过, 1跳过(网络下载,原有)


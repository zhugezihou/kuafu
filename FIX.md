# FIX.md — 修复清单

## 状态：第二批 全部完成 ✅

## P1（中等严重度）

### □ P1-1: 审批超时 300s 硬编码（tool_orchestrator.py）✅
- `ev.wait(timeout=300)` → `timeout=_get_approval_timeout()`
- 从 `approval.py:69` 的配置函数读取，默认不变仍为 300s
- 用户可在 config 中 `APPROVAL_TIMEOUT=600` 调整

### □ P1-2: Gateway 重启未完成审批丢失（gateway.py）✅
- `_handle_restart()` 重启前检查 `ApprovalManager.list_pending()`
- 有待审批时先等待最多 30s 让用户完成审批
- 仍超时则强制重启，不影响已有审批

### □ P1-3: _handle_invoke_experts 全失败仍返回 success=True（agent_loop.py）✅
- 拆分 output_parts 列表，用 `bool(output_parts)` 判断
- 全失败时 `success=False, output="所有专家均失败"`

## P2（低严重度）

### □ P2-1: session_map.json 并发写覆盖（gateway_loop.py）✅
- 加 `self._session_map_lock = threading.Lock()`
- 所有读（get/update）和写（赋值+文件 dump）都加锁保护

## P3（运维增强）

### □ P3-1: 添加 /api/health 端点（gateway.py）✅
- 新增 `GET /api/health` → `{"status": "ok", "mode": "gateway"}`
- 用于外部监控/健康检查

## 验证结果（第二批）
- 语法检查：✅ 4 文件通过
- 全量 import：✅ 通过
- 功能验证：✅ 全部 8 项验证通过
- 测试回归：✅ 112 失败（全部原有），1865 通过，无新增


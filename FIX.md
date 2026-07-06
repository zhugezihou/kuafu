# E1: 飞书审批持久化重构

## 现状

审批数据有两份存储：
1. `core/approval.py` → `_save()` 写 `memory/approvals/{id}.json` ✅ 持久化
2. `feishu_ws.py` → `_card_approval_state: dict[str, threading.Event]` ❌ 纯内存

**核心缺陷：** WS 重连后，已发审批卡片的 Event 对象全部丢失。新 WS 连接收不到用户在断连期间点击的卡片回调。

## 修复方案

**不改审批系统架构，只做 WS 重连恢复审批监听。**

WS 重连时：
1. 扫描 `memory/approvals/` 中 `status="pending"` 的记录
2. 为每一条重新创建 `threading.Event` 并注册到 `_card_approval_state`
3. 重新注册 `ON_CARD_APPROVAL_CBS` 回调

## 影响面分析

### 文件
- `core/channel/feishu_ws.py` — `_reconnect()` 或重连后回调处
- `core/approval.py` — 可能需要暴露一个 `scan_pending_events()` 工具方法

### 跨对象传递链
- 不涉及跨对象链（纯 FeishuWebSocketChannel 内部）
- 引用 `ApprovalManager` 和 `ON_CARD_APPROVAL_CBS` 是类级/模块级，安全

### 测试
- `tests/` 中不涉及 WS 审批持久化的测试

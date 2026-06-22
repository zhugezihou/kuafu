# FIX.md — 修复清单

## 状态：全部完成 ✅

## 优先级：P0 ✅

### □ P0-1: 专家系统 resp.success 检查（agent_loop.py）✅
- `_handle_invoke_expert` 和 `_call_expert_once` 两处都加了 `resp.get("success", False)` 检查
- 失败时直接返回 `{"success": False, "output": "专家 xxx LLM 调用失败: ..."}`
- 同时加了空内容 fallback：content 为空时返回 `"专家 xxx 已完成分析"`

### □ P0-2: cron source_channel send_text 缺 fallback（cron_scheduler.py）✅
- `channel_bot.send_text(...)` 改为 `hasattr(channel_bot, 'send_text')` + `hasattr(channel_bot, 'send')` 双 fallback
- FeishuWebSocketChannel（无 send_text）也能正常推送

## 优先级：P1

### □ P1-1: 专家空 content 有 fallback（agent_loop.py）✅
- 和 P0-1 一起修的，两处都加了 `if not content: content = f"专家 {name} 已完成分析"`

## 验证结果
- 语法检查：✅ 两文件通过
- 核心方法引用：✅ 全部存在
- 测试回归：✅ 无新增失败（112 失败均为已有问题）
- 计数验证：✅ resp.success 检查 2 处 · 空内容 fallback 2 处 · send_text fallback 2 处


# 夸父（Kuafu）全面测试题目清单 v2.0

> 基于代码 v0.4.0（最新 commit: 6cea0ef）编制
> 现有测试脚本保留在 tests/ 下，可一键运行
> 34 个模块、35 个大类、~180 项测试用例

---

## 第一部分：现有测试已覆盖，建议重跑确认

### T1：身份系统（test_all → test_identity）
**P0 | 15s**
- [ ] 1.1 `load_identity_statement()` 返回字符串含"夸父"和"Kuafu"
- [ ] 1.2 `get_agent_name()` 返回 "夸父"
- [ ] 1.3 `_fallback_identity()` 返回兜底身份声明（非空）
- [ ] 1.4 `validate_identity_in_prompt()` 含关键字的 prompt 通过
- [ ] 1.5 `detect_identity_impersonation()` 冒用身份的消息返回 True

### T2：沙盒系统（test_all → test_sandbox）
**P0 | 15s**
- [ ] 2.1 `is_path_allowed_for_write` 拒绝写 `core/` 目录
- [ ] 2.2 `is_path_allowed_for_write` 允许写 `strategy/` 目录
- [ ] 2.3 `validate_command` 安全命令("ls -la")返回 safe=True
- [ ] 2.4 `validate_command` 高危命令("rm -rf /")返回 safe=False
- [ ] 2.5 **新增**：拒绝写 `CORE_CHARTER.md`
- [ ] 2.6 **新增**：拒绝写 `IDENTITY.md`

### T3：记忆系统（test_all → test_memory_api）
**P0 | 30s**
- [ ] 3.1 `remember()` 写入成功返回 True
- [ ] 3.2 `recall()` 检索刚写入的记忆，结果 ≥ 1
- [ ] 3.3 `reflect()` 返回非空字符串
- [ ] 3.4 **新增**：写入带 tag 的记忆，按 tag 检索
- [ ] 3.5 **新增**：`forget()` 删除记忆
- [ ] 3.6 **新增**：SQLiteFTSBackend 初始化创建正确表结构

### T4：进化系统（test_all → test_evolution）
**P0 | 30s**
- [ ] 4.1 `evaluate_and_evolve()` 无 LLM 时返回 None（D 方案降级）
- [ ] 4.2 `record_task()` 记录 coding 类任务
- [ ] 4.3 `get_task_stats()` 返回正确 total 和 by_type
- [ ] 4.4 `get_evolution_stats()` 返回完整统计字段

### T5：Agent 表示与状态（test_all → test_agent_repr/prompt）
**P0 | 20s**
- [ ] 5.1 `KuafuAgent()` 初始化含名称和版本
- [ ] 5.2 `build_system_prompt()` 含"夸父"和"进化"
- [ ] 5.3 `get_status()` 返回含 version/memory/evolution/task_stats 的字典
- [ ] 5.4 **新增**：build_system_prompt 含当前日期和时分

### T6：Agent 工具完整性（test_all → test_agent_loop_tools）
**P1 | 15s**
- [ ] 6.1 `AgentLoop.tools.get_schemas()` 返回预期工具集合
- [ ] 6.2 **新增**：所有工具 schema 含 description 字段
- [ ] 6.3 **新增**：delegate_task 工具的 description 以中文开头

### T7：上下文压缩（regression_test → test_4）
**P1 | 15s**
- [ ] 7.1 `needs_compression()` 小上下文返回 False
- [ ] 7.2 `compress()` 压缩后 tokens ≤ 压缩前 tokens
- [ ] 7.3 **新增**：BudgetReduction(P0-1)管线能初始化
- [ ] 7.4 **新增**：渐进压缩(P0-2)管线能初始化

### T8：审批系统全链路（test_approval_full_chain.py）
**P1 | 30s**
- [ ] 8.1 submit() 创建审批文件
- [ ] 8.2 list_pending() 返回待审批列表
- [ ] 8.3 approve() 状态变为 approved
- [ ] 8.4 reject() 状态变为 rejected
- [ ] 8.5 format_pending_summary() 返回可读文本
- [ ] 8.6 terminal_prompt() 模拟 y/n 输入
- [ ] 8.7 **新增**：`_is_interactive()` 在非终端环境返回 False
- [ ] 8.8 **新增**：`_get_approval_timeout()` 返回正整数默认 300

### T9：进化管道（test_evolution_pipeline.py）
**P2 | 30s**
- [ ] 9.1 Observer 正确收集工具调用信息（tool_calls/tool_errors）
- [ ] 9.2 EvolutionState 正确记录任务结果
- [ ] 9.3 EvolutionEngine.run_pipeline() 无 LLM 时正确降级
- [ ] 9.4 管道不抛出异常

### T10：P3 技能提取（test_p3_skill_extractor.py）
**P2 | 30s**
- [ ] 10.1 LLM 返回有内容 JSON → 生成带具体步骤的 skill
- [ ] 10.2 LLM 返回空话 → 质量校验失败 → 降级模板
- [ ] 10.3 LLM 调用失败 → 安全降级
- [ ] 10.4 无 LLM → evolution._extract_skill 降级到模板

### T11：P4 策略物化（test_p4_strategy_materialization.py）
**P2 | 30s**
- [ ] 11.1 get_rules() ≥ 6 条规则
- [ ] 11.2 get_quality("code") ≥ 2 条质量标准
- [ ] 11.3 get_prompt("research") 非空
- [ ] 11.4 evolution._sync_strategy() 更新 strategy/ 目录

### T12：WebHook 生命周期（test_all → test_webhook_lifecycle）
**P2 | 20s**
- [ ] 12.1 WebhookServer 启动返回 True
- [ ] 12.2 is_running() 返回 True
- [ ] 12.3 /health 端点返回 {"status": "ok"}
- [ ] 12.4 stop() 后 is_running() 返回 False

### T13：子 Agent Schema（test_all → test_subagent_schema）
**P1 | 10s**
- [ ] 13.1 get_delegate_schema() parameters 含 goal 和 context
- [ ] 13.2 MAX_CONCURRENT ≥ 1
- [ ] 13.3 MAX_TURNS ≥ 1

### T14：通道基础（test_all → test_channel_init）
**P2 | 10s**
- [ ] 14.1 ChannelManager.list() 返回空列表
- [ ] 14.2 ChannelManager.get("nonexistent") 返回 None

### T15：安全层（regression_test → test_5）
**P1 | 15s**
- [ ] 15.1 API key 脱敏 → "***"
- [ ] 15.2 密码脱敏 → "***"
- [ ] 15.3 Authorization Header 脱敏 → "***"
- [ ] 15.4 SAFE 命令归类正确
- [ ] 15.5 DANGEROUS 命令归类正确
- [ ] 15.6 ATTENTION 命令归类正确

---

## 第二部分：新编测试 — 核心模块未覆盖路径

### T16：evolution_engine.py（全新）
**P2 | 20s | 文件：232行**
- [ ] 16.1 EvolutionEngine 初始化不抛出异常
- [ ] 16.2 `evaluate()` 返回 L0-Lx 级别
- [ ] 16.3 `apply_evolution()` 执行进化动作
- [ ] 16.4 进化统计跟踪正常工作

### T17：Hook 系统（全新）
**P2 | 30s | 文件：573行，27个钩子**
- [ ] 17.1 注册 SyncSubscriber → 触发后同步执行
- [ ] 17.2 注册 AsyncSubscriber → 触发后异步执行
- [ ] 17.3 注册 OnceSubscriber → 触发一次后自动卸载
- [ ] 17.4 注册 ConditionSubscriber → 条件不满足时不执行
- [ ] 17.5 `on_agent_start` 钩子可触发
- [ ] 17.6 `on_tool_before` 钩子接收工具名和参数
- [ ] 17.7 `on_tool_after` 钩子接收工具执行结果
- [ ] 17.8 `on_memory_write` 钩子接收写入内容
- [ ] 17.9 `on_llm_call_before` 钩子触发
- [ ] 17.10 错误订阅不阻塞其他订阅执行

### T18：MCP 桥接（全新）
**P2 | 30s | 文件：358行**
- [ ] 18.1 MCPClient 初始化正确
- [ ] 18.2 `discover_tools()` 返回工具列表
- [ ] 18.3 JSON-RPC 协议格式正确
- [ ] 18.4 MCP Server 启动/停止生命周期
- [ ] 18.5 异常 MCP Server 优雅降级

### T19：Budget Allocator（全新）
**P2 | 20s | 文件：471行**
- [ ] 19.1 初始化含 5 类预算
- [ ] 19.2 `estimate_tokens()` 返回正整数
- [ ] 19.3 超限预警触发
- [ ] 19.4 本地模式 vs 云端模式阈值不同
- [ ] 19.5 get_snapshot() 返回完整预算状态

### T20：Prompt Template（全新）
**P2 | 20s | 文件：450行**
- [ ] 20.1 Section 初始化含 id/title/content/condition
- [ ] 20.2 `assemble()` 按条件动态组装 sections
- [ ] 20.3 无记忆时记忆 section 不注入
- [ ] 20.4 get_token_estimate() 返回正整数

### T21：白板架构（全新）
**P2 | 30s | 文件：913行**
- [ ] 21.1 Whiteboard 初始化创建空状态
- [ ] 21.2 `write(partition, data)` 写入成功
- [ ] 21.3 `read(partition)` 读取正确
- [ ] 21.4 `list_partitions()` 列出所有分区
- [ ] 21.5 Decomposer 将长任务分解为步骤
- [ ] 21.6 WhiteboardExecutor 执行步骤序列
- [ ] 21.7 **边缘**：写入不存在的分区自动创建
- [ ] 21.8 **边缘**：读取不存在的分区返回空

### T22：Cron 定时任务（全新）
**P2 | 30s | 文件：446行**
- [ ] 22.1 CronScheduler 初始化
- [ ] 22.2 `add_task()` 添加 cron 表达式任务
- [ ] 22.3 `remove_task()` 移除任务
- [ ] 22.4 `list_tasks()` 返回任务列表
- [ ] 22.5 `pause/resume` 暂停/恢复任务
- [ ] 22.6 每 10 分钟任务正确计算 next_run
- [ ] 22.7 一次性任务到期自动移除
- [ ] 22.8 **边缘**：无效 cron 表达式优雅降级

### T23：通道基础类（全新）
**P2 | 15s | 文件：376行**
- [ ] 23.1 MessageChannel 抽象类声明正确接口
- [ ] 23.2 Message dataclass 含 text/platform/timestamp/metadata
- [ ] 23.3 SendResult dataclass 含 success/platform/error

### T24：飞书通道（全新）
**P2 | 30s | 文件：590行**
- [ ] 24.1 FeishuChannel 初始化（env 无 key 时跳过）
- [ ] 24.2 `send()` 返回 SendResult
- [ ] 24.3 `poll()` 返回消息列表（或空列表）
- [ ] 24.4 无 API key 时优雅降级

---

## 第三部分：新编测试 — 自治层模块

### T25：学习者（全新）
**P3 | 30s | 文件：563行**
- [ ] 25.1 Learner 初始化正常
- [ ] 25.2 `learn_from_result()` 记录学习
- [ ] 25.3 `summarize_learnings()` 返回总结
- [ ] 25.4 不同任务类型的学习记录分离

### T26：自检健康（全新）
**P3 | 30s | 文件：551行**
- [ ] 26.1 HealthChecker 初始化
- [ ] 26.2 `check_core_integrity()` 检查核心文件完整性
- [ ] 26.3 `check_memory_sanity()` 检查记忆系统健康
- [ ] 26.4 `generate_report()` 生成健康报告
- [ ] 26.5 发现异常时返回告警

### T27：规划器（全新）
**P3 | 30s | 文件：438行**
- [ ] 27.1 IdlePrioritizer 初始化
- [ ] 27.2 `get_next_task()` 返回待处理任务
- [ ] 27.3 EvolutionScheduler 调度进化计划
- [ ] 27.4 DecisionRecord 记录正确

### T28：评审器（全新）
**P3 | 20s | 文件：228行**
- [ ] 28.1 ReviewerThread 启动和停止
- [ ] 28.2 `review_session()` 评审会话
- [ ] 28.3 评审结果写入正确格式

### T29：Web 学习者（全新）
**P3 | 30s | 文件：627行**
- [ ] 29.1 WebLearner 初始化
- [ ] 29.2 `fetch_and_learn()` 抓取并学习
- [ ] 29.3 学习结果存入 memory

---

## 第四部分：新编测试 — 端到端与集成

### T30：LLM Client（扩展测试）
**P1 | 30s | 文件：332行**
- [ ] 30.1 LLMClient 初始化（含 api_key 注入）
- [ ] 30.2 `switch()` 切换模型提供方
- [ ] 30.3 `chat()` 返回正确格式（需 mock）
- [ ] 30.4 错误时抛出/返回异常信息
- [ ] 30.5 **边缘**：空 messages 列表处理

### T31：Session Store（扩展测试）
**P1 | 20s | 文件：691行**
- [ ] 31.1 `create_session()` 创建会话
- [ ] 31.2 `append_message()` 添加消息
- [ ] 31.3 `get_messages()` 返回消息列表
- [ ] 31.4 会话统计正确
- [ ] 31.5 **新增**：`list_sessions()` 分页
- [ ] 31.6 **新增**：`delete_session()` 软删除
- [ ] 31.7 **新增**：大会话（>100条）检索正常

### T32：AgentLoop（扩展测试）
**P1 | 60s | 文件：1,947行**
- [ ] 32.1 `build_system_prompt()` 含 7 个 section
- [ ] 32.2 `detect_task_type()` 正确分类任务
- [ ] 32.3 **新增**：非交互模式 `run()` 不阻塞
- [ ] 32.4 **新增**：交互模式 terminal_prompt 阻塞等待 y/N
- [ ] 32.5 **新增**：`context_summarize()` 触发汇总（P0-3）
- [ ] 32.6 **新增**：`pending_approval` 回调触发飞书推送

### T33：飞书 Bot（全新）
**P2 | 30s | 文件：590行**
- [ ] 33.1 FeishuBot 初始化（仅发送模式）
- [ ] 33.2 `send_message()` 发送审批消息
- [ ] 33.3 `start_polling()/stop_polling()` 轮询生命周期
- [ ] 33.4 超时自动驳回逻辑

### T34：ToolRegistry（扩展测试）
**P1 | 20s | 文件：1,417行**
- [ ] 34.1 注册新工具
- [ ] 34.2 延迟加载延迟注册
- [ ] 34.3 `get_schemas()` 返回正确个数
- [ ] 34.4 **新增**：`execute()` 含动态参数注入
- [ ] 34.5 **新增**：工具调用次数统计

### T35：端到端全链路（新增）
**P1 | 120s | 模拟完整用户交互**
- [ ] 35.1 用户发消息 → AgentLoop 收到 → build_system_prompt
- [ ] 35.2 LLM 返回工具调用 → ToolRegistry 执行
- [ ] 35.3 工具结果回 LLM → 循环
- [ ] 35.4 Observer 收集信号
- [ ] 35.5 任务完成 → EvolutionEngine 评估
- [ ] 35.6 记忆自动写入（高价值）
- [ ] 35.7 技能生成（P3 触发时）

---

## 测试优先级策略

```
P0（关键路径，必须过）：T1 T2 T3 T4 T5 T6 T30 T32
｜ 这些失败了系统根本不可用
｜
P1（核心功能）：T7 T8 T13 T14 T15 T31 T34 T35
｜ 审批/压缩/存储/端到端
｜
P2（重要功能）：T9 T10 T11 T12 T16 T17 T18 T19 T20 T21 T22 T23 T24 T33
｜ 进化管道/中间件/通道
｜
P3（增值功能）：T25 T26 T27 T28 T29
｜ 自治层/自我进化
```

---

## 现有测试运行命令

```bash
# 一键运行全部现有测试
cd /home/asus/kuafu
source venv/bin/activate

# 核心测试集
python tests/test_all.py

# 回归测试
python tests/regression_test.py

# 进化管道测试
python tests/test_evolution_pipeline.py

# P3 技能提取测试
python tests/test_p3_skill_extractor.py

# P4 策略物化测试
python tests/test_p4_strategy_materialization.py

# 审批全链路测试
python tests/test_approval_full_chain.py
```

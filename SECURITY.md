# Security Policy

## Reporting a Vulnerability

夸父项目认真对待安全漏洞。如果发现安全问题，**请不要公开披露**，请通过以下方式私密报告：

- Email: 在 GitHub 仓库的 Issues 中搜索「security」联系维护者
- GitHub Private Vulnerability Reporting: 仓库的 Security 页面

我们会：

1. 在 48 小时内确认收到报告
2. 评估影响范围
3. 在修复完成后告知报告者
4. 发布安全公告

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x | ✅ |

## Security Considerations

夸父是一个 AI Agent 框架，可以执行代码和终端命令。以下安全机制已内置：

- **审批系统**：危险命令需要人工确认
- **SafetyLayer 三态决策**：Allow / Block / Escalate
- **DenyRules 硬拒绝**：不可覆盖的安全规则
- **core/ 目录保护**：任何 agent 不可修改核心代码

如发现任何绕过上述机制的漏洞，请立即报告。

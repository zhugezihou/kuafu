"""core/workflow_v2/ — 夸父工作流平台 v2。

轻量级工作流引擎，YAML 定义、顺序+并行执行、cron 集成。

设计原则：
- 零外部依赖
- YAML 文件即工作流定义
- 节点类型：terminal / http / llm / condition / subflow
- 模板变量 {{nodes.x.output}} / {{input.x}}
- 节点间通过 output 传递数据
- 支持并行执行（parallel: true）
"""

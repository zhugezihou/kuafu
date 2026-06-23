# Contributing to Kuafu

感谢您关注夸父！夸父是一个自我进化的 AI Agent 框架，我们欢迎各种形式的贡献。

## 贡献方式

### 🐛 提交 Bug

1. 在 [Issues](https://github.com/zhugezihou/kuafu/issues) 搜索是否已有相同问题
2. 如果不存在，创建新 Issue，使用 Bug Report 模板
3. 包括：
   - 夸父版本（`python -c "from core.main import KuafuAgent; print(KuafuAgent().version)"`）
   - Python 版本
   - 操作系统 / Docker 环境
   - 完整的错误输出
   - 最小复现步骤

### 💡 功能建议

1. 创建 Feature Request Issue
2. 描述你想要的场景和具体能力
3. 如果可能，给出实现思路

### 🔧 提交 PR

1. Fork 仓库
2. 从 `main` 创建功能分支
3. 写代码 — 确保：
   - 所有测试通过（`pytest tests/ -x --tb=short -q`）
   - 新功能有对应测试
   - 代码符合现有风格
4. 更新 CHANGELOG.md（追加到 Unreleased 段）
5. 提交 PR，关联对应的 Issue

## 开发环境

```bash
git clone https://github.com/zhugezihou/kuafu.git
cd kuafu
python3 -m venv venv
source venv/bin/activate
pip install -e .
pip install pytest pytest-cov pytest-timeout
```

## 测试要求

- 核心模块新增代码必须有测试覆盖
- PR 的 CI 必须全部绿色
- 覆盖率不能下降

## 代码规范

- Python 3.10+ 类型注解
- 遵循现有代码风格（4空格缩进，~100字符行宽）
- 中英文混合注释（中文解释逻辑，英文保留关键词）

## CLA

提交 PR 即视为同意将代码以 Apache-2.0 许可贡献给项目。

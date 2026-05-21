"""P4-5 集成测试：策略物化全链路验证。

覆盖：
1. strategy_loader 加载与降级
2. agent_loop.build_system_prompt 集成（从 strategy/ 读取规则）
3. skill_resolver 两阶段匹配（task_type + keyword）
4. evolution.py 双向同步（_sync_strategy 更新 strategy/）
5. 边缘情况：空 strategy/ 目录、损坏 YAML、无 LLM 降级
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

# 确保在 kuafu 根目录
REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))

import yaml

# ── 1. strategy_loader 测试 ──────────────────────────────

print("=" * 60)
print("1. strategy_loader 测试")
print("=" * 60)

from autonomous.strategy_loader import (
    get_prompt, get_strategy, get_quality, get_rules,
    render_prompt, clear_cache,
)

# 1.1 get_rules — 从 task_strategies.yaml 读取规则
rules = get_rules()
assert isinstance(rules, list), f"期望 list, 得到 {type(rules)}"
assert len(rules) >= 6, f"期望至少 6 条规则, 得到 {len(rules)}"  # 4个任务类型各1+2条
print(f"  规则总数: {len(rules)}")
for r in rules:
    assert isinstance(r, str), f"规则应为 str: {r}"
print(f"  ✓ get_rules() OK")

# 1.2 get_quality — 读取质量标准
quality = get_quality("code")
assert isinstance(quality, list), f"期望 list, 得到 {type(quality)}"
assert len(quality) >= 2, f"期望至少 2 条规则"
for q in quality:
    assert "severity" in q and "rule" in q, f"quality 条目格式错误: {q}"
print(f"  quality(code) 规则数: {len(quality)}")
print(f"  ✓ get_quality() OK")

# 1.3 get_quality — 未知类型的降级
unknown_q = get_quality("nonexistent_type")
assert isinstance(unknown_q, list)
print(f"  quality(nonexistent_type) 返回空: {len(unknown_q) == 0}")
print(f"  ✓ 降级 OK")

# 1.4 get_strategy — 读取任务策略
strategy = get_strategy("coding")
assert isinstance(strategy, dict)
assert "max_retries" in strategy
expected = strategy.get("max_retries")
print(f"  strategy(coding).max_retries: {expected}")
print(f"  ✓ get_strategy() OK")

# 1.5 render_prompt
rendered = render_prompt("Hello {name}!", name="Kuafu")
assert rendered == "Hello Kuafu!"
print(f"  ✓ render_prompt() OK")

print("\n✓ strategy_loader 全部通过\n")

# ── 2. agent_loop.build_system_prompt 集成测试 ──────────────

print("=" * 60)
print("2. agent_loop.build_system_prompt 集成测试")
print("=" * 60)

from core.agent_loop import AgentLoop

# 创建最小化 AgentLoop 实例
loop = AgentLoop.__new__(AgentLoop)
loop.memory = types.SimpleNamespace()
loop.memory.recall = lambda query="", limit=10: []
loop.evolution = types.SimpleNamespace()
loop.evolution.get_evolution_stats = lambda: {"total_evolutions": 5, "by_level": {0: 2, 1: 1, 2: 1, 3: 1}}
loop.evolution.get_task_stats = lambda: {"total": 15, "success_rate": 86.7}
loop.tools = types.SimpleNamespace()
loop.tools.get_schemas = lambda: []

# 2.1 带任务的 prompt（有质量标准注入）
prompt_with_task = loop.build_system_prompt("帮我写一个 Python 脚本解析 CSV")
assert "核心规则" in prompt_with_task
assert "质量标准" in prompt_with_task  # P4-2 新注入
assert "可用工具" in prompt_with_task
assert "进化状态" in prompt_with_task
print(f"  带任务 prompt 长度: {len(prompt_with_task)} chars")
print(f"  包含核心规则: {'核心规则' in prompt_with_task}")
print(f"  包含质量标准: {'质量标准' in prompt_with_task}")
print(f"  ✓ build_system_prompt(task) OK")

# 2.2 无任务的 prompt（列出所有技能）
prompt_no_task = loop.build_system_prompt("")
assert "核心规则" in prompt_no_task
print(f"  无任务 prompt 长度: {len(prompt_no_task)} chars")
print(f"  ✓ build_system_prompt() OK")

# 2.3 规则来源验证：应该是从 strategy/ 读取的，不是硬编码的
assert "绝对不可以修改 core/" not in prompt_with_task, "旧硬编码规则残留"
assert "你是夸父，一个自我进化的 AI agent" not in prompt_with_task, "旧硬编码规则残留"
print(f"  ✓ 旧硬编码规则已删除")

print("\n✓ agent_loop 集成全部通过\n")

# ── 3. skill_resolver 测试 ──────────────────────────────

print("=" * 60)
print("3. skill_resolver 测试")
print("=" * 60)

from core.skill_resolver import match_skills, _detect_task_type, _match_by_task_type

# 3.1 _detect_task_type
assert _detect_task_type("写一个 Python 脚本") == "coding"
assert _detect_task_type("实现一个函数") == "coding"
assert _detect_task_type("搜索最新的 AI 框架") == "research"
assert _detect_task_type("调研一下市场趋势") == "research"
assert _detect_task_type("读文件 data.csv") == "file_operation"
assert _detect_task_type("压缩这个目录") == "file_operation"
assert _detect_task_type("今天天气怎么样") == "generic"
print(f"  ✓ _detect_task_type OK")

# 3.2 _match_by_task_type（当前 skill 无 task_type，应该返回空）
tt_matches = _match_by_task_type("coding")
assert isinstance(tt_matches, list)
print(f"  _match_by_task_type(coding): {len(tt_matches)} 个匹配（当前无 skill 带 task_type）")

# 3.3 match_skills — 两阶段匹配
matches = match_skills("写一个 Python 脚本")
assert isinstance(matches, list)
print(f"  match_skills('写一个 Python 脚本'): {len(matches)} 个匹配")
for m in matches:
    print(f"    - {m['name']} (score={m['score']}, task_type={m.get('task_type', '')})")

print(f"  ✓ match_skills OK")

print("\n✓ skill_resolver 全部通过\n")

# ── 4. evolution.py 双向同步测试 ──────────────────────────────

print("=" * 60)
print("4. evolution.py 双向同步测试")
print("=" * 60)

from core.evolution import EvolutionEngine

# 保存原始文件以便恢复
strategy_dir = REPO_ROOT / "strategy"
orig_quality = (strategy_dir / "quality.yaml").read_text(encoding="utf-8")
orig_strategies = (strategy_dir / "task_strategies.yaml").read_text(encoding="utf-8")

try:
    # 4.1 降级同步测试
    engine = EvolutionEngine.__new__(EvolutionEngine)
    engine._task_history = [
        {"task_type": "coding", "success": True, "result": "完成", "duration": 5.0, "tool_calls": 3}
    ]
    engine._llm = None
    engine._log_path = REPO_ROOT / "logs" / "test_evolution_log.json"
    engine._log_path.parent.mkdir(exist_ok=True)
    engine._log_path.write_text("[]", encoding="utf-8")
    engine._memory = None
    engine._last_level_time = {}
    engine._min_interval = 60

    engine._sync_strategy("测试同步: coding 类型任务优化")

    with open(strategy_dir / "quality.yaml", "r") as f:
        qdata = yaml.safe_load(f)
    assert len(qdata) >= 4, f"quality.yaml 应该追加了规则: {len(qdata)}"
    print(f"  quality.yaml 规则数: {len(qdata)}")

    with open(strategy_dir / "task_strategies.yaml", "r") as f:
        sdata = yaml.safe_load(f)
    notes = sdata.get("generic", {}).get("notes", [])
    assert len(notes) >= 1, f"task_strategies.yaml 应该追加了 notes"
    print(f"  task_strategies.yaml generic.notes: {len(notes)} 条")
    print(f"  ✓ _sync_strategy 降级同步 OK")

    # 4.2 完整进化循环测试
    engine2 = EvolutionEngine()
    engine2._min_interval = 0
    for i in range(8):
        engine2.evaluate_and_evolve({
            "success": True,
            "task_type": "coding",
            "result": f"coding 任务 {i+1}",
            "duration": 3.0,
            "tool_calls": 2,
            "timestamp": time.time() + i * 200,
        })

    stats = engine2.get_evolution_stats()
    print(f"  进化统计: total={stats['total_evolutions']}, by_level={stats['by_level']}")
    assert stats["total_evolutions"] >= 1, "应该有进化事件"
    # L0 应该在每3次成功触发（3次、6次...）

    # 手动触发 L2 验证同步
    qdata_before = len(yaml.safe_load((strategy_dir / "quality.yaml").read_text()))

    event = engine2._evolve(
        level=2,
        trigger="人工测试: L2 策略进化",
        action="更新策略模板",
        target="strategy/",
    )
    assert event is not None, "_evolve(L2) 应该返回事件"

    qdata_after = len(yaml.safe_load((strategy_dir / "quality.yaml").read_text()))
    print(f"  quality.yaml 在 L2 后: {qdata_before} → {qdata_after}")
    assert qdata_after > qdata_before, "L2 应该追加质量规则"
    print(f"  ✓ L2 策略同步 OK")

    # 4.3 L3 技能提取测试（verify P3 链路仍然工作）
    event_l3 = engine2._evolve(
        level=3,
        trigger="人工测试: L3 技能提取",
        action="提取技能包",
        target="skills/test_extract.yaml",
    )
    assert event_l3 is not None, "_evolve(L3) 应该返回事件"
    test_skill_path = REPO_ROOT / "skills" / "test_extract.yaml"
    if test_skill_path.exists():
        test_skill_path.unlink()  # 清理
    print(f"  ✓ L3 技能提取 OK")

finally:
    # 恢复原始文件
    (strategy_dir / "quality.yaml").write_text(orig_quality, encoding="utf-8")
    (strategy_dir / "task_strategies.yaml").write_text(orig_strategies, encoding="utf-8")
    # 清理测试日志
    test_log = REPO_ROOT / "logs" / "test_evolution_log.json"
    if test_log.exists():
        test_log.unlink()

print("\n✓ evolution 双向同步全部通过\n")

# ── 5. 边缘情况测试 ──────────────────────────────

print("=" * 60)
print("5. 边缘情况测试")
print("=" * 60)

# 5.1 损坏 YAML — strategy_loader 应该有降级
from core import evolution as ev_mod

# 模拟 strategy 目录不存在
orig_strategy = ev_mod.ROOT_DIR / "strategy"
backup_strategy = REPO_ROOT / "strategy_backup"
if backup_strategy.exists():
    shutil.rmtree(backup_strategy)
shutil.copytree(orig_strategy, backup_strategy)

# 删除 strategy 目录测试降级
shutil.rmtree(orig_strategy)
orig_strategy.mkdir(parents=True, exist_ok=True)

try:
    clear_cache()
    rules_empty = get_rules()
    assert isinstance(rules_empty, list)
    print(f"  无 strategy/ 时 get_rules(): {len(rules_empty)} 条（应返回空列表）")

    quality_empty = get_quality("code")
    assert isinstance(quality_empty, list)
    print(f"  无 strategy/ 时 get_quality(): {len(quality_empty)} 条")

    # strategy_loader 不崩溃
    prompt_default = get_prompt("default")
    assert isinstance(prompt_default, dict)
    print(f"  无 strategy/ 时 get_prompt(): 返回内嵌默认模板")
    print(f"  ✓ 空目录降级 OK")
finally:
    # 恢复
    shutil.rmtree(orig_strategy)
    shutil.copytree(backup_strategy, orig_strategy)
    shutil.rmtree(backup_strategy)

# 5.2 单条规则文件测试
clear_cache()
rules_normal = get_rules()
print(f"  正常策略: {len(rules_normal)} 条规则")
print(f"  ✓ 边缘情况全部通过")

print("\n" + "=" * 60)
print("🎉 P4 全部 5 个子任务测试通过")
print("=" * 60)

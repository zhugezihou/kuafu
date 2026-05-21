"""P3 SkillExtractor 测试验证。

覆盖场景：
1. LLM 返回有实质内容的 JSON → 生成带具体步骤的 skill
2. LLM 返回空话 → 质量校验没通过，降级到模板
3. LLM 调用失败 → 降级安全
4. 无 LLM → evolution.py 的 _extract_skill 降级到模板
5. 完整集成测试：from evolution.evaluate_and_evolve → L3 触发 → skill 写入
"""

import sys
import os
import json
import tempfile
import time
import shutil

# ─── 环境准备 ───────────────────────────────

# 切换到项目根目录
PROJECT_ROOT = "/home/asus/kuafu"
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

# ─── Mock LLM ──────────────────────────────

class MockLLM:
    """可注入的 mock LLM，返回可控的 JSON 响应。"""

    def __init__(self, response: str):
        self._response = response
        self.calls = []

    def chat(self, messages: list) -> dict:
        self.calls.append(messages)
        return {
            "success": True,
            "content": self._response,
            "tool_calls": None,
            "usage": None,
            "error": None,
        }


class FailingMockLLM:
    """始终失败的 mock LLM。"""

    def chat(self, messages: list) -> dict:
        return {
            "success": False,
            "content": "",
            "tool_calls": None,
            "usage": None,
            "error": "mock error",
        }


class ExceptionMockLLM:
    """总是抛异常的 mock LLM。"""

    def chat(self, messages: list) -> dict:
        raise RuntimeError("mock: LLM unavailable")


# ─── Mock Memory ───────────────────────────

class MockMemory:
    def __init__(self):
        self.stored = []

    def remember(self, key: str, content: str, tags: list = None):
        self.stored.append({"key": key, "content": content, "tags": tags})

    def recall(self, query: str, limit: int = 10):
        return []


# ─── 测试用例 ───────────────────────────────

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}: {detail}")


# ──────────────────────────────────────────
# Test 1: LLM 返回有实质内容的 JSON
# ──────────────────────────────────────────

def test_valid_llm_extraction():
    print("\n═══ Test 1: LLM 返回高质量 skill 内容 ═══")
    from autonomous.skill_extractor import SkillExtractor

    llm_response = json.dumps({
        "name": "web_search",
        "description": "用 Tavily API 搜索网络并提取相关结果的技能",
        "steps": [
            "1. 调用 tavily_search(query, search_depth='advanced', max_results=5) 获取搜索结果",
            "2. 用 tavily_extract(urls, format='markdown') 提取最相关页面的完整内容",
            "3. 将提取的内容整理为摘要，标注来源链接",
        ],
        "pitfalls": [
            "搜索结果可能包含广告或时效性差的内容，需要人工判断",
            "tavily_extract 对某些网站可能返回空内容，准备降级方案",
        ],
        "example_scenario": "用户问'当前中美贸易战最新进展'",
        "example_steps": [
            "搜索'trade war US China 2026 latest'",
            "提取前3个结果的完整内容",
            "用时间线方式整理关键事件",
        ],
    })

    llm = MockLLM(llm_response)
    memory = MockMemory()
    extractor = SkillExtractor(llm.chat, memory.remember)

    task_history = [
        {
            "success": True,
            "task_type": "web_search",
            "result": "user asked about AI Agent frameworks",
            "duration": 12.5,
            "tool_calls": 5,
            "timestamp": time.time() - 3600,
        },
        {
            "success": True,
            "task_type": "web_search",
            "result": "user asked about llama.cpp benchmarks",
            "duration": 8.3,
            "tool_calls": 4,
            "timestamp": time.time() - 1800,
        },
        {
            "success": True,
            "task_type": "web_search",
            "result": "user asked about MCP protocol",
            "user_correction": "需要更关注MCP架构而非具体实现",
            "duration": 15.1,
            "tool_calls": 7,
            "timestamp": time.time() - 900,
        },
    ]

    result = extractor.extract(task_history, "web_search", "3次成功+Tavily模式识别")

    check("提取返回结果", result is not None)
    check("质量为 pass", result and result.get("quality") == "pass")
    check("有文件路径", result and "path" in result)
    check("LLM 被调用", len(llm.calls) == 1)
    check("记忆已存储", len(memory.stored) >= 1)

    # 检查生成的 YAML 文件
    if result:
        yaml_path = result["path"]
        check("YAML 文件存在", os.path.exists(yaml_path))
        if os.path.exists(yaml_path):
            content = open(yaml_path).read()
            check("包含具体 steps", "tavily_search" in content or "搜索" in content)
            check("包含 pitfalls", "pitfall" in content.lower() or "降级" in content)
            check("名称正确", "web_search" in content)

    # 清理
    if result and os.path.exists(result["path"]):
        os.remove(result["path"])


# ──────────────────────────────────────────
# Test 2: LLM 返回空话 → 质量校验不过
# ──────────────────────────────────────────

def test_low_quality_rejected():
    print("\n═══ Test 2: LLM 返回空话 → 质量校验不通过 ═══")
    from autonomous.skill_extractor import SkillExtractor

    llm_response = json.dumps({
        "name": "research",
        "description": "做研究",
        "steps": [
            "根据具体需求灵活应用合适的方法完成任务",
            "完成主要工作后报告最终结果",
        ],
        "pitfalls": [],
        "example_scenario": "",
        "example_steps": [],
    })

    llm = MockLLM(llm_response)
    extractor = SkillExtractor(llm.chat)

    task_history = [
        {
            "success": True,
            "task_type": "research",
            "result": "some research task",
            "duration": 10.0,
            "tool_calls": 3,
            "timestamp": time.time(),
        },
    ]

    result = extractor.extract(task_history, "research", "test")

    check("返回了结果", result is not None)
    check("质量为 fail", result and result.get("quality") == "fail")


# ──────────────────────────────────────────
# Test 3: LLM 调用失败 → 降级安全
# ──────────────────────────────────────────

def test_llm_failure_safe():
    print("\n═══ Test 3: LLM 调用失败 → 降级安全 ═══")
    from autonomous.skill_extractor import SkillExtractor

    # 3a: LLM 返回 success=False
    llm = FailingMockLLM()
    extractor = SkillExtractor(llm.chat)
    result = extractor.extract([{"success": True, "task_type": "test", "result": "x"}], "test", "fail")
    check("success=False 时返回 None（触发降级）", result is None)

    # 3b: LLM 抛出异常
    llm2 = ExceptionMockLLM()
    extractor2 = SkillExtractor(llm2.chat)
    result2 = extractor2.extract([{"success": True, "task_type": "test", "result": "x"}], "test", "exception")
    check("异常时返回 None（触发降级）", result2 is None)


# ──────────────────────────────────────────
# Test 4: 无 LLM → evolution 降级到模板
# ──────────────────────────────────────────

def test_evolution_fallback():
    print("\n═══ Test 4: 无 LLM → evolution 降级到模板 ═══")
    from core.evolution import EvolutionEngine

    evo = EvolutionEngine(
        task_history=[
            {
                "success": True,
                "task_type": "research",
                "result": "完成了一次关于LLM Agent的调研",
                "user_correction": None,
                "duration": 15.0,
                "tool_calls": 6,
                "timestamp": time.time() - 100,
            },
            {
                "success": True,
                "task_type": "research",
                "result": "完成了第二次调研",
                "user_correction": "方案二更好",
                "duration": 20.0,
                "tool_calls": 8,
                "timestamp": time.time() - 50,
            },
            {
                "success": True,
                "task_type": "research",
                "result": "完成了第三次调研",
                "user_correction": None,
                "duration": 10.0,
                "tool_calls": 5,
                "timestamp": time.time(),
            },
        ],
        llm=None,  # 无 LLM
    )

    # 模拟 L3 触发后的 _extract_skill 调用
    result = evo._extract_skill("skills/research.yaml", "测试降级")

    check("降级返回了路径", result is not None)
    if result and os.path.exists(result):
        content = open(result).read()
        check("包含 fallback 标记", "fallback" in content.lower() or "保底" in content)
        os.remove(result)


# ──────────────────────────────────────────
# Test 5: 集成测试 — LLM 可用时的完整流程
# ──────────────────────────────────────────

def test_integration_with_llm():
    print("\n═══ Test 5: 集成测试（LLM 可用） ═══")
    from core.evolution import EvolutionEngine
    from autonomous.skill_extractor import SkillExtractor

    llm_response = json.dumps({
        "name": "research",
        "description": "用 Tavily 进行网络调研并提取结构化信息的技能",
        "steps": [
            "用 tavily_search 搜索英文关键词获取初始结果",
            "用 tavily_extract 提取最相关3个页面的全文",
            "用 LLM 将提取内容整理为结构化摘要",
        ],
        "pitfalls": [
            "中文搜索用中文关键词，英文搜索用英文关键词",
            "tavily_extract 对 paywall 页面返回空内容",
        ],
        "example_scenario": "用户问'帮我查一下最新论文'",
        "example_steps": [
            "搜索 'latest ML papers 2026'",
            "提取 arxiv 页面内容",
            "整理为摘要列表",
        ],
    })

    mock_llm = MockLLM(llm_response)
    mock_memory = MockMemory()

    # 构造 evolution 引擎（有 LLM 和 memory）
    evo = EvolutionEngine(
        task_history=[
            {"success": True, "task_type": "research", "result": "调研了AI Agent", "duration": 12.0, "tool_calls": 5, "user_correction": None, "timestamp": time.time() - 3000},
            {"success": True, "task_type": "research", "result": "调研了MCP", "duration": 8.0, "tool_calls": 4, "user_correction": "关注架构", "timestamp": time.time() - 2000},
            {"success": True, "task_type": "research", "result": "调研了Tavily", "duration": 15.0, "tool_calls": 7, "user_correction": None, "timestamp": time.time() - 1000},
        ],
        llm=mock_llm,
        memory=mock_memory,
    )

    # _extract_skill 会检测到有 LLM，自动用 SkillExtractor
    result = evo._extract_skill("skills/research.yaml", "3次research成功")


    check("返回了路径", result is not None)
    if result and os.path.exists(result):
        content = open(result).read()
        # 检查是 LLM 生成的（不是降级模板）
        check("不是 fallback 模板", "fallback" not in content.lower())
        check("包含具体工具名", "tavily" in content.lower())
        check("包含步骤", "steps:" in content)
        check("包含陷阱", "pitfall" in content.lower())
        os.remove(result)

    # 检查 LLM 被调用了
    check("LLM 被调用", len(mock_llm.calls) == 1)
    check("LLM prompt 包含任务类型", mock_llm.calls[0][1]["content"].find("research") >= 0)


# ──────────────────────────────────────────
# Test 6: 获取日志
# ──────────────────────────────────────────

def test_extraction_log():
    print("\n═══ Test 6: 提取日志 ═══")
    from autonomous.skill_extractor import SkillExtractor

    llm = MockLLM(json.dumps({
        "name": "log_test",
        "description": "test skill",
        "steps": ["step 1", "step 2"],
        "pitfalls": ["pitfall 1"],
        "example_scenario": "test",
        "example_steps": ["example 1"],
    }))
    extractor = SkillExtractor(llm.chat)

    task_history = [
        {"success": True, "task_type": "log_test", "result": "test", "duration": 5.0, "tool_calls": 3, "timestamp": time.time()},
    ]

    result = extractor.extract(task_history, "log_test", "test提取")
    if result and result.get("path") and os.path.exists(result["path"]):
        os.remove(result["path"])

    log = extractor.get_log()
    check("日志非空", len(log) >= 1)
    check("日志包含 id", log[0].get("id") is not None)
    check("日志包含时间戳", log[0].get("timestamp", 0) > 0)


# ──────────────────────────────────────────
# 运行
# ──────────────────────────────────────────

def main():
    global passed, failed
    print("=" * 50)
    print("P3 SkillExtractor 测试套件")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    test_valid_llm_extraction()
    test_low_quality_rejected()
    test_llm_failure_safe()
    test_evolution_fallback()
    test_integration_with_llm()
    test_extraction_log()

    print(f"\n{'=' * 50}")
    print(f"结果: {passed} 通过 / {failed} 失败 / {passed + failed} 总计")
    print(f"{'=' * 50}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

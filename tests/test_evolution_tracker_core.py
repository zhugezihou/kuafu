"""测试 core/evolution_tracker.py 的 EvolutionTracker 核心方法。

覆盖：__init__, _init_db, close, _execute, _execute_many,
      record_skill_evolution, get_evolution_history, get_all_skills,
      record_skill_quality, get_skill_quality, get_skill_degradation,
      record_result, is_novel, is_repeated_failure, get_task_type_count,
      get_recent_failure_rate, get_task_type_stats,
      log_fitness, get_fitness_history, get_fitness_trend,
      record_event, get_recent_events, get_event_stats,
      record_error, is_known_error, get_error_count, get_top_errors,
      set_meta, get_meta, get_stats, undo_last_skill_evolution
"""

import json
import sys
import time
from pathlib import Path

# 确保项目根路径在 sys.path 中
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from core.evolution_tracker import EvolutionTracker


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def tracker(tmp_path):
    """创建一个使用临时数据库的 EvolutionTracker 实例。"""
    db_path = tmp_path / "test_evolution.db"
    t = EvolutionTracker(db_path=db_path, reuse_conn=False)
    yield t
    t.close()


@pytest.fixture
def seeded_tracker(tmp_path):
    """预制数据的 tracker：一个技能两个版本 + 两条质量记录。"""
    db_path = tmp_path / "seeded_evolution.db"
    t = EvolutionTracker(db_path=db_path, reuse_conn=False)
    t.record_skill_evolution("test-skill", "skills/test.yaml", "CAPTURED", "v1", parent=None)
    t.record_skill_evolution("test-skill", "skills/test.yaml", "FIX", "v2", parent="1")
    for _ in range(6):
        t.record_skill_quality("test-skill", 0.9, {"acc": 0.9})
        t.record_skill_quality("test-skill", 0.8, {"acc": 0.8})
    t.record_result("coding", True)
    yield t
    t.close()


# ═══════════════════════════════════════════════════════════
# __init__ & _init_db & close
# ═══════════════════════════════════════════════════════════


class TestInit:
    def test_default_path_creates_db(self, tmp_path):
        """默认路径会自动创建 db 文件。"""
        db_path = tmp_path / "evolution.db"
        t = EvolutionTracker(db_path=db_path, reuse_conn=False)
        assert db_path.exists()
        t.close()

    def test_reuse_conn_same_path(self, tmp_path):
        """reuse_conn=True 且路径相同 → 复用共享连接。"""
        db_path = tmp_path / "shared.db"
        t1 = EvolutionTracker(db_path=db_path, reuse_conn=True)
        t2 = EvolutionTracker(db_path=db_path, reuse_conn=True)
        assert t1.conn is t2.conn
        assert t1._owns_conn is True
        assert t2._owns_conn is False
        t1.close()

    def test_reuse_conn_different_path(self, tmp_path):
        """reuse_conn=True 但路径不同 → 新建连接。"""
        db1 = tmp_path / "a.db"
        db2 = tmp_path / "b.db"
        t1 = EvolutionTracker(db_path=db1, reuse_conn=True)
        t2 = EvolutionTracker(db_path=db2, reuse_conn=True)
        assert t1.conn is not t2.conn
        assert t1._owns_conn is True
        assert t2._owns_conn is True
        t1.close()
        t2.close()

    def test_reuse_conn_false(self, tmp_path):
        """reuse_conn=False → 总是新建连接。"""
        db_path = tmp_path / "no_reuse.db"
        t1 = EvolutionTracker(db_path=db_path, reuse_conn=False)
        t2 = EvolutionTracker(db_path=db_path, reuse_conn=False)
        assert t1.conn is not t2.conn
        assert t1._owns_conn is True
        assert t2._owns_conn is True
        t1.close()
        t2.close()

    def test_init_db_creates_tables(self, tmp_path):
        """_init_db 应创建所有表和索引。"""
        db_path = tmp_path / "schema.db"
        t = EvolutionTracker(db_path=db_path, reuse_conn=False)
        cursor = t.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r["name"] for r in cursor.fetchall()]
        expected = {
            "evolution_skills", "evolution_skill_quality", "evolution_task_types",
            "evolution_fitness_log", "evolution_events", "evolution_errors",
            "evolution_meta", "evolution_skill_content",
        }
        for et in expected:
            assert et in tables, f"表 {et} 未创建"
        t.close()

    def test_close_owns_conn(self, tmp_path):
        """close 在 _owns_conn=True 时关闭连接。"""
        db_path = tmp_path / "close_owns.db"
        t = EvolutionTracker(db_path=db_path, reuse_conn=False)
        t.close()
        with pytest.raises(Exception):
            t.conn.execute("SELECT 1")

    def test_close_not_owns_conn(self, tmp_path):
        """close 在 _owns_conn=False 时不关闭连接。"""
        db_path = tmp_path / "close_shared.db"
        t1 = EvolutionTracker(db_path=db_path, reuse_conn=True)
        t2 = EvolutionTracker(db_path=db_path, reuse_conn=True)
        t2.close()  # _owns_conn=False，不应关闭
        # t1 的连接应仍然可用
        row = t1.conn.execute("SELECT 1 AS x").fetchone()
        assert row["x"] == 1
        t1.close()


# ═══════════════════════════════════════════════════════════
# _execute & _execute_many
# ═══════════════════════════════════════════════════════════


class TestExecute:
    def test_execute_returns_cursor(self, tracker):
        """_execute 返回 sqlite3.Cursor。"""
        cursor = tracker._execute("SELECT 1 AS x")
        assert cursor.fetchone()["x"] == 1

    def test_execute_with_params(self, tracker):
        """_execute 支持参数绑定。"""
        tracker._execute(
            "INSERT INTO evolution_meta (key, value) VALUES (?, ?)",
            ("k", "v"),
        )
        tracker.conn.commit()
        row = tracker._execute(
            "SELECT value FROM evolution_meta WHERE key = ?", ("k",)
        ).fetchone()
        assert row["value"] == "v"

    def test_execute_many(self, tracker):
        """_execute_many 批量插入。"""
        tracker._execute_many(
            "INSERT INTO evolution_meta (key, value) VALUES (?, ?)",
            [("a", "1"), ("b", "2"), ("c", "3")],
        )
        tracker.conn.commit()
        rows = tracker._execute(
            "SELECT value FROM evolution_meta ORDER BY key"
        ).fetchall()
        assert [r["value"] for r in rows] == ["1", "2", "3"]


# ═══════════════════════════════════════════════════════════
# 技能版本链
# ═══════════════════════════════════════════════════════════


class TestSkillEvolution:
    def test_record_skill_evolution_first_version(self, tracker):
        """首次记录技能版本号应为 1。"""
        v = tracker.record_skill_evolution("pip-install", "skills/pip.yaml", "CAPTURED", "初始版本")
        assert v == 1

    def test_record_skill_evolution_increments(self, tracker):
        """多次记录同一技能应递增版本号。"""
        v1 = tracker.record_skill_evolution("my-skill", "a.yaml", "CAPTURED", "v1")
        v2 = tracker.record_skill_evolution("my-skill", "a.yaml", "FIX", "v2", parent="1")
        v3 = tracker.record_skill_evolution("my-skill", "b.yaml", "DERIVED", "v3", parent="2")
        assert v1 == 1
        assert v2 == 2
        assert v3 == 3

    def test_get_evolution_history(self, tracker):
        """获取指定技能的版本链历史。"""
        tracker.record_skill_evolution("s1", "f1.yaml", "CAPTURED", "v1")
        tracker.record_skill_evolution("s1", "f2.yaml", "FIX", "v2", parent="1")
        history = tracker.get_evolution_history("s1")
        assert len(history) == 2
        assert history[0]["version"] == 1
        assert history[1]["version"] == 2
        assert history[1]["parent"] == "1"

    def test_get_evolution_history_empty(self, tracker):
        """不存在的技能返回空列表。"""
        assert tracker.get_evolution_history("nonexistent") == []

    def test_get_all_skills(self, tracker):
        """返回所有技能的最新版本号。"""
        tracker.record_skill_evolution("a", "a.yaml")
        tracker.record_skill_evolution("a", "a.yaml")
        tracker.record_skill_evolution("b", "b.yaml")
        all_skills = tracker.get_all_skills()
        assert all_skills == {"a": 2, "b": 1}

    def test_get_all_skills_empty(self, tracker):
        """没有技能记录时返回空字典。"""
        assert tracker.get_all_skills() == {}

    def test_undo_last_skill_evolution(self, tracker):
        """回滚 skill 的最新版本。"""
        tracker.record_skill_evolution("s", "f.yaml", "CAPTURED", "v1")
        tracker.record_skill_evolution("s", "f.yaml", "FIX", "v2", parent="1")
        result = tracker.undo_last_skill_evolution("s")
        assert result == {"rolled_back_v": 2, "restored_to_v": 1}
        history = tracker.get_evolution_history("s")
        assert len(history) == 1
        assert history[0]["version"] == 1

    def test_undo_last_skill_evolution_only_one(self, tracker):
        """只有一个版本时无法回滚，返回 None。"""
        tracker.record_skill_evolution("s", "f.yaml")
        assert tracker.undo_last_skill_evolution("s") is None

    def test_undo_last_skill_evolution_nonexistent(self, tracker):
        """不存在的技能返回 None。"""
        assert tracker.undo_last_skill_evolution("nonexistent") is None

    def test_undo_last_deletes_fitness_log(self, tracker):
        """回滚时删除对应的 fitness 日志。"""
        tracker.record_skill_evolution("s", "f.yaml")
        tracker.record_skill_evolution("s", "f.yaml")
        tracker.log_fitness("s", 0.9, usage_count=2)
        tracker.log_fitness("s", 0.8, usage_count=1)
        # v2 的记录对应的 version=2
        result = tracker.undo_last_skill_evolution("s")
        assert result is not None
        history = tracker.get_fitness_history("s")
        # 只有 version=1 的记录应保留（但 log_fitness 用的是 MAX(version)，
        # 如果先 insert 再 record_skill，v2 的记录会绑定 v2）
        # 实际上 log_fitness 查询的是当前最新版本，所以两条记录都可能绑定 v2
        # 但回滚只删除 version=latest["version"] 的记录
        row = tracker._execute(
            "SELECT COUNT(*) as c FROM evolution_fitness_log WHERE skill_name='s' AND version=2"
        ).fetchone()
        assert row["c"] == 0


# ═══════════════════════════════════════════════════════════
# 技能适应度质量
# ═══════════════════════════════════════════════════════════


class TestSkillQuality:
    def test_record_skill_quality(self, tracker):
        """记录质量评分，自动取当前版本。"""
        tracker.record_skill_evolution("sq", "sq.yaml")
        tracker.record_skill_quality("sq", 0.85, {"acc": 0.85})
        scores = tracker.get_skill_quality("sq")
        assert scores == [0.85]

    def test_get_skill_quality_none(self, tracker):
        """没有质量记录返回 None。"""
        assert tracker.get_skill_quality("no-skill") is None

    def test_get_skill_quality_multiple(self, tracker):
        """同一个技能当前版本的多次质量评分。"""
        tracker.record_skill_evolution("sq2", "sq2.yaml")
        for s in [0.7, 0.8, 0.9]:
            tracker.record_skill_quality("sq2", s)
        scores = tracker.get_skill_quality("sq2")
        assert scores == [0.7, 0.8, 0.9]

    def test_get_skill_degradation_insufficient(self, tracker):
        """质量记录不足 n*2 条时返回 None。"""
        tracker.record_skill_evolution("d", "d.yaml")
        for s in [0.9, 0.8]:
            tracker.record_skill_quality("d", s)
        assert tracker.get_skill_degradation("d", n=5) is None

    def test_get_skill_degradation_sufficient(self, tracker):
        """足够质量记录时计算退化差异。"""
        tracker.record_skill_evolution("d2", "d2.yaml")
        # 前 5 次高分，后 5 次低分 → 退化
        for s in [0.9, 0.9, 0.9, 0.9, 0.9, 0.5, 0.5, 0.5, 0.5, 0.5]:
            tracker.record_skill_quality("d2", s)
        diff = tracker.get_skill_degradation("d2", n=5)
        assert diff is not None
        assert diff < 0  # 退化

    def test_get_skill_degradation_improvement(self, tracker):
        """质量上升应为正数。"""
        tracker.record_skill_evolution("d3", "d3.yaml")
        for s in [0.5, 0.5, 0.5, 0.5, 0.5, 0.9, 0.9, 0.9, 0.9, 0.9]:
            tracker.record_skill_quality("d3", s)
        diff = tracker.get_skill_degradation("d3", n=5)
        assert diff is not None
        assert diff > 0  # 改善

    def test_get_skill_degradation_no_historical(self, tracker):
        """只有 recent 没有 historical 时返回 None。"""
        tracker.record_skill_evolution("d4", "d4.yaml")
        for s in [0.9, 0.9, 0.9, 0.9]:
            tracker.record_skill_quality("d4", s)
        # 4 条记录，n=5 → recent=4, historical=0 → 返回 None
        assert tracker.get_skill_degradation("d4", n=5) is None


# ═══════════════════════════════════════════════════════════
# 任务类型统计
# ═══════════════════════════════════════════════════════════


class TestTaskType:
    def test_record_result_new(self, tracker):
        """首次记录结果应插入新行。"""
        tracker.record_result("code_gen", True)
        assert tracker.get_task_type_count("code_gen") == 1

    def test_record_result_existing(self, tracker):
        """已存在 task_type 时更新计数和 last_n。"""
        tracker.record_result("test", True)
        tracker.record_result("test", False)
        tracker.record_result("test", True)
        assert tracker.get_task_type_count("test") == 3

    def test_record_result_upsert_count(self, tracker):
        """验证 upsert 后的 count 累积。"""
        for _ in range(5):
            tracker.record_result("build", True)
        assert tracker.get_task_type_count("build") == 5

    def test_record_result_last_n_limited(self, tracker):
        """last_n 应最多保留 20 条。"""
        for i in range(25):
            tracker.record_result("many", i % 2 == 0)
        row = tracker._execute(
            "SELECT last_n FROM evolution_task_types WHERE task_type='many'"
        ).fetchone()
        last_n = json.loads(row["last_n"])
        assert len(last_n) <= 20

    def test_is_novel_new(self, tracker):
        """新 task_type 应返回 True。"""
        assert tracker.is_novel("brand_new") is True

    def test_is_novel_existing(self, tracker):
        """已存在的 task_type 应返回 False。"""
        tracker.record_result("existing", True)
        assert tracker.is_novel("existing") is False

    def test_is_repeated_failure_new(self, tracker):
        """不存在的 task_type 应返回 False。"""
        assert tracker.is_repeated_failure("no_type") is False

    def test_is_repeated_failure_below_threshold(self, tracker):
        """连续失败次数不足 threshold 应返回 False。"""
        tracker.record_result("fragile", False)
        assert tracker.is_repeated_failure("fragile", threshold=2) is False

    def test_is_repeated_failure_above_threshold(self, tracker):
        """连续失败次数 >= threshold 应返回 True。
        注意：首次 INSERT 时 consecutive_fail 固定为 0，
        所以 N 次失败后实际 consecutive_fail = N-1。
        """
        tracker.record_result("fragile", False)  # INSERT → cf=0
        tracker.record_result("fragile", False)  # UPDATE → cf=1
        tracker.record_result("fragile", False)  # UPDATE → cf=2
        assert tracker.is_repeated_failure("fragile", threshold=2) is True

    def test_is_repeated_failure_reset_on_success(self, tracker):
        """成功后应重置连续失败计数。"""
        tracker.record_result("fragile", False)   # INSERT → cf=0
        tracker.record_result("fragile", False)   # UPDATE → cf=1
        tracker.record_result("fragile", True)    # UPDATE → cf=0
        assert tracker.is_repeated_failure("fragile", threshold=2) is False

    def test_get_task_type_count_zero(self, tracker):
        """不存在的 task_type 返回 0。"""
        assert tracker.get_task_type_count("ghost") == 0

    def test_get_recent_failure_rate_none(self, tracker):
        """不存在的 task_type 返回 0.0。"""
        assert tracker.get_recent_failure_rate("ghost") == 0.0

    def test_get_recent_failure_rate_all_success(self, tracker):
        """全部成功返回 0.0。"""
        for _ in range(3):
            tracker.record_result("ok", True)
        assert tracker.get_recent_failure_rate("ok") == 0.0

    def test_get_recent_failure_rate_all_fail(self, tracker):
        """全部失败返回 1.0。"""
        for _ in range(3):
            tracker.record_result("bad", False)
        assert tracker.get_recent_failure_rate("bad") == 1.0

    def test_get_recent_failure_rate_mixed(self, tracker):
        """混合结果。"""
        tracker.record_result("mixed", True)
        tracker.record_result("mixed", False)
        tracker.record_result("mixed", False)
        assert tracker.get_recent_failure_rate("mixed", n=5) == pytest.approx(2 / 3)

    def test_get_recent_failure_rate_empty_last_n(self, tracker):
        """last_n 为空返回 0.0。"""
        tracker.record_result("empty_n", True)
        # 直接修改 last_n 为空
        tracker._execute(
            "UPDATE evolution_task_types SET last_n='[]' WHERE task_type='empty_n'"
        )
        tracker.conn.commit()
        assert tracker.get_recent_failure_rate("empty_n") == 0.0

    def test_get_task_type_stats(self, tracker):
        """获取所有 task_type 的统计列表。"""
        tracker.record_result("a", True)
        tracker.record_result("b", False)
        tracker.record_result("b", False)
        stats = tracker.get_task_type_stats()
        assert len(stats) == 2
        # 按 count DESC 排序，b=2, a=1
        assert stats[0]["task_type"] == "b"
        assert stats[0]["count"] == 2
        assert stats[1]["task_type"] == "a"
        assert stats[1]["count"] == 1

    def test_get_task_type_stats_empty(self, tracker):
        """无记录时返回空列表。"""
        assert tracker.get_task_type_stats() == []


# ═══════════════════════════════════════════════════════════
# Fitness 日志
# ═══════════════════════════════════════════════════════════


class TestFitnessLog:
    def test_log_fitness(self, tracker):
        """记录适应度评估。"""
        tracker.record_skill_evolution("fs", "fs.yaml")
        tracker.log_fitness("fs", 0.85, {"acc": 0.85}, success_rate=0.9,
                            usage_count=5, step_count=3, last_used_days=1.0,
                            quality_score=0.8)
        history = tracker.get_fitness_history("fs")
        assert len(history) == 1
        assert history[0]["score"] == 0.85
        assert history[0]["usage_count"] == 5

    def test_log_fitness_minimal(self, tracker):
        """最简参数记录适应度。"""
        tracker.record_skill_evolution("fs2", "fs2.yaml")
        tracker.log_fitness("fs2", 0.5)
        history = tracker.get_fitness_history("fs2")
        assert len(history) == 1

    def test_get_fitness_history_limit(self, tracker):
        """get_fitness_history 应遵守 limit。"""
        tracker.record_skill_evolution("fs3", "fs3.yaml")
        for i in range(10):
            tracker.log_fitness("fs3", 0.5 + i * 0.05)
        history = tracker.get_fitness_history("fs3", limit=3)
        assert len(history) == 3

    def test_get_fitness_history_empty(self, tracker):
        """无 Fitness 记录的技能返回空列表。"""
        assert tracker.get_fitness_history("ghost") == []

    def test_get_fitness_trend_too_few(self, tracker):
        """数据不足 n 条时返回 None。"""
        tracker.record_skill_evolution("ft1", "ft1.yaml")
        for i in range(3):
            tracker.log_fitness("ft1", 0.5 + i * 0.1)
        assert tracker.get_fitness_trend("ft1", n=10) is None

    def test_get_fitness_trend_sufficient(self, tracker):
        """足够数据时返回趋势统计。"""
        tracker.record_skill_evolution("ft2", "ft2.yaml")
        # 10 条上升数据，recent 5 > overall avg
        for s in [0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0]:
            tracker.log_fitness("ft2", s)
        trend = tracker.get_fitness_trend("ft2", n=5)
        assert trend is not None
        assert "recent_avg" in trend
        assert "overall_avg" in trend
        assert "trend" in trend
        assert trend["samples"] == 10

    def test_get_fitness_trend_direction(self, tracker):
        """上升时 trend=up，下降时 trend=down。"""
        tracker.record_skill_evolution("ft3", "ft3.yaml")
        # 下降趋势
        for s in [0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5, 0.45]:
            tracker.log_fitness("ft3", s)
        trend = tracker.get_fitness_trend("ft3", n=5)
        assert trend["trend"] == "down"

    def test_get_fitness_trend_no_records(self, tracker):
        """无记录时返回 None。"""
        assert tracker.get_fitness_trend("missing") is None


# ═══════════════════════════════════════════════════════════
# 进化事件
# ═══════════════════════════════════════════════════════════


class TestEvents:
    def test_record_event_defaults(self, tracker):
        """记录事件，使用默认参数。"""
        tracker.record_event(action="test_action")
        events = tracker.get_recent_events()
        assert len(events) == 1
        assert events[0]["action"] == "test_action"
        assert events[0]["level"] == "info"
        assert events[0]["success"] is True

    def test_record_event_all_fields(self, tracker):
        """记录事件，全部字段。"""
        tracker.record_event(level="error", action="crash", target="sys",
                             payload="out of memory", success=False)
        events = tracker.get_recent_events()
        e = events[0]
        assert e["level"] == "error"
        assert e["action"] == "crash"
        assert e["target"] == "sys"
        assert e["success"] is False

    def test_get_recent_events_empty(self, tracker):
        """无事件时返回空列表。"""
        assert tracker.get_recent_events() == []

    def test_get_recent_events_limit(self, tracker):
        """get_recent_events 应遵守 limit。"""
        for i in range(10):
            tracker.record_event(action=f"event_{i}")
        events = tracker.get_recent_events(limit=3)
        assert len(events) == 3

    def test_get_event_stats(self, tracker):
        """事件统计摘要。"""
        tracker.record_event(action="one")
        tracker.record_event(level="skill", action="evolve")
        tracker.record_event(level="skill", action="refine")
        stats = tracker.get_event_stats()
        assert stats["total_events"] == 3
        assert stats["skill_events"] == 2

    def test_get_event_stats_empty(self, tracker):
        """无事件时统计为 0。"""
        stats = tracker.get_event_stats()
        assert stats["total_events"] == 0
        assert stats["skill_events"] == 0


# ═══════════════════════════════════════════════════════════
# 错误记录
# ═══════════════════════════════════════════════════════════


class TestErrors:
    def test_record_error_new(self, tracker):
        """记录新错误。"""
        tracker.record_error("ImportError: no module flask")
        assert tracker.get_error_count() == 1

    def test_record_error_duplicate(self, tracker):
        """重复记录同一错误增加 count。"""
        tracker.record_error("TypeError: expected int")
        tracker.record_error("TypeError: expected int")
        rows = tracker.get_top_errors()
        assert len(rows) == 1
        assert rows[0]["count"] == 2

    def test_record_error_with_skill(self, tracker):
        """记录错误并关联技能名。"""
        tracker.record_error("KeyError: 'name'", skill_name="parse_skill")
        rows = tracker.get_top_errors()
        assert rows[0]["skill_name"] == "parse_skill"

    def test_get_error_count_zero(self, tracker):
        """无错误时返回 0。"""
        assert tracker.get_error_count() == 0

    def test_get_top_errors_order(self, tracker):
        """get_top_errors 按 count DESC 排序。"""
        tracker.record_error("err_a")      # count=1
        tracker.record_error("err_b")
        tracker.record_error("err_b")       # count=2
        tracker.record_error("err_c")
        tracker.record_error("err_c")
        tracker.record_error("err_c")       # count=3
        top = tracker.get_top_errors()
        assert top[0]["error_text"] == "err_c"
        assert top[0]["count"] == 3
        assert top[1]["error_text"] == "err_b"
        assert top[1]["count"] == 2
        assert top[2]["error_text"] == "err_a"

    def test_get_top_errors_limit(self, tracker):
        """get_top_errors 遵守 limit。"""
        for i in range(10):
            tracker.record_error(f"err_{i}")
        top = tracker.get_top_errors(limit=3)
        assert len(top) == 3

    def test_get_top_errors_empty(self, tracker):
        """无错误时返回空列表。"""
        assert tracker.get_top_errors() == []

    def test_is_known_error_empty(self, tracker):
        """已知错误库为空返回 False。"""
        assert tracker.is_known_error("anything") is False

    def test_is_known_error_exact_match(self, tracker):
        """精确匹配应返回 True（通过分词重叠）。"""
        tracker.record_error("module flask not found")
        assert tracker.is_known_error("module flask not found") is True

    def test_is_known_error_fuzzy_match(self, tracker):
        """模糊匹配：重叠词 >= min(3, len(words_known)//2)。"""
        tracker.record_error("error cannot connect to database server")
        assert tracker.is_known_error("failed to connect to database") is True

    def test_is_known_error_no_match(self, tracker):
        """不相似的错误返回 False。"""
        tracker.record_error("http timeout")
        assert tracker.is_known_error("cpu overheating detected") is False

    def test_is_known_error_short_phrase(self, tracker):
        """已知错误词数 <= 3 时无法通过模糊匹配（代码要求 len(words_known) > 3）。"""
        tracker.record_error("too short")
        # "too short" -> words_known = {"too", "short"} -> len=2, 不满足 >3
        # 所以不会进入模糊匹配逻辑，直接返回 False
        assert tracker.is_known_error("something too short here") is False

    def test_is_known_error_empty_input(self, tracker):
        """空字符串输入返回 False。"""
        assert tracker.is_known_error("") is False

    def test_is_known_error_nonexistent_in_db(self, tracker):
        """数据库中有错误，但输入完全不相关。"""
        tracker.record_error("TypeError: int object is not callable")
        assert tracker.is_known_error("OSError: file not found") is False

    def test_record_error_integrity_error(self, tmp_path, monkeypatch):
        """IntegrityError 异常被安全忽略（通过 monkeypatch 模拟 SQLite 异常）。"""
        import sqlite3
        db_path = tmp_path / "integrity.db"
        t = EvolutionTracker(db_path=db_path, reuse_conn=False)

        # 先插入一条记录
        t.record_error("dup_error")
        assert t.get_error_count() == 1

        original_execute = t._execute

        def broken_execute(sql, params=()):
            if "ON CONFLICT" in sql:
                raise sqlite3.IntegrityError("模拟的 IntegrityError")
            return original_execute(sql, params)

        # 使用 monkeypatch 替换方法
        monkeypatch.setattr(t, '_execute', broken_execute)

        # 即使 _execute 抛出 IntegrityError，record_error 也应安全忽略
        t.record_error("dup_error")

        # 确认连接仍然可用
        rows = t._execute("SELECT count FROM evolution_errors WHERE error_text='dup_error'").fetchall()
        assert len(rows) > 0

        monkeypatch.undo()
        t.close()

    def test_get_skill_degradation_no_data(self, tracker):
        """没有技能记录时返回 None。"""
        assert tracker.get_skill_degradation("nonexistent") is None

    def test_get_skill_degradation_n_zero(self, tracker):
        """n=0 时跳过退化计算返回 None（边界情况）。"""
        tracker.record_skill_evolution("n0", "n0.yaml")
        for s in [0.5, 0.6, 0.7, 0.8]:
            tracker.record_skill_quality("n0", s)
        # n=0: len(scores)=4, 4<0? False → 通过检查
        # recent = scores[:-0] = [], historical = scores[:0] = []
        # not historical → True → return None
        assert tracker.get_skill_degradation("n0", n=0) is None


# ═══════════════════════════════════════════════════════════
# 元数据管理
# ═══════════════════════════════════════════════════════════


class TestMeta:
    def test_set_get_meta(self, tracker):
        """设置和读取元数据。"""
        tracker.set_meta("schema_version", "2.0")
        assert tracker.get_meta("schema_version") == "2.0"

    def test_get_meta_default(self, tracker):
        """不存在的 key 返回默认值。"""
        assert tracker.get_meta("nonexistent", "default_val") == "default_val"

    def test_get_meta_default_empty(self, tracker):
        """不设置默认值应返回空字符串。"""
        assert tracker.get_meta("no_key") == ""

    def test_set_meta_overwrite(self, tracker):
        """重复设置同一 key 应覆盖。"""
        tracker.set_meta("key1", "old")
        tracker.set_meta("key1", "new")
        assert tracker.get_meta("key1") == "new"

    def test_set_meta_migration_flag(self, tracker):
        """设置迁移标记。"""
        tracker.set_meta("migrated_from_json", "true")
        assert tracker.get_meta("migrated_from_json") == "true"


# ═══════════════════════════════════════════════════════════
# 综合统计 (get_stats)
# ═══════════════════════════════════════════════════════════


class TestGetStats:
    def test_get_stats_empty(self, tracker):
        """空数据库的统计。"""
        stats = tracker.get_stats()
        assert stats["total_skills"] == 0
        assert stats["total_task_types"] == 0
        assert stats["known_errors"] == 0
        assert stats["total_events"] == 0
        assert stats["recent_24h"]["fitness_evals"] == 0
        assert stats["recent_24h"]["events"] == 0

    def test_get_stats_with_data(self, tracker):
        """有数据时的统计。"""
        tracker.record_skill_evolution("s1", "s1.yaml")
        tracker.record_skill_evolution("s1", "s1.yaml")
        tracker.record_skill_evolution("s2", "s2.yaml")
        for _ in range(3):
            tracker.record_result("task", True)
        tracker.log_fitness("s1", 0.9)
        tracker.log_fitness("s1", 0.8)
        tracker.record_event(action="e1")
        tracker.record_event(action="e2")
        tracker.record_error("some error")
        tracker.set_meta("k", "v")

        stats = tracker.get_stats(include_recent_events=True)
        assert stats["total_skills"] == 2
        assert stats["total_task_types"] == 1
        assert stats["known_errors"] == 1
        assert stats["total_events"] == 2
        assert "recent_events" in stats
        assert len(stats["recent_events"]) == 2

    def test_get_stats_no_recent_events(self, tracker):
        """include_recent_events=False 时不包含 recent_events。"""
        tracker.record_event(action="e1")
        stats = tracker.get_stats(include_recent_events=False)
        assert "recent_events" not in stats


# ═══════════════════════════════════════════════════════════
# 边界条件与集成场景
# ═══════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_concurrent_same_path_reuse(self, tmp_path):
        """多实例相同路径的共享连接行为。"""
        db_path = tmp_path / "concurrent.db"
        t1 = EvolutionTracker(db_path=db_path, reuse_conn=True)
        t2 = EvolutionTracker(db_path=db_path, reuse_conn=True)

        # 通过 t1 写入，t2 读取
        t1.record_skill_evolution("shared", "shared.yaml")
        history = t2.get_evolution_history("shared")
        assert len(history) == 1

        t1.close()
        # t2 不拥有连接，关闭无影响

    def test_record_skill_evolution_all_modes(self, tracker):
        """验证所有 mode 值。"""
        for mode in ["CAPTURED", "FIX", "DERIVED"]:
            v = tracker.record_skill_evolution(f"sk_{mode}", f"{mode}.yaml", mode=mode)
            assert v >= 1
            history = tracker.get_evolution_history(f"sk_{mode}")
            assert history[0]["mode"] == mode

    def test_record_event_levels(self, tracker):
        """验证所有 event level。"""
        for level in ["info", "skill", "warning", "error"]:
            tracker.record_event(level=level, action=f"action_{level}")
        events = tracker.get_recent_events()
        levels = {e["level"] for e in events}
        assert levels == {"info", "skill", "warning", "error"}

    def test_get_skill_quality_different_versions(self, tracker):
        """不同版本的技能质量评分隔离。"""
        tracker.record_skill_evolution("mv", "mv.yaml", "CAPTURED", "v1")
        tracker.record_skill_quality("mv", 0.8)  # 绑定 v1
        tracker.record_skill_evolution("mv", "mv.yaml", "FIX", "v2", parent="1")
        tracker.record_skill_quality("mv", 0.9)  # 绑定 v2
        # get_skill_quality 获取当前版本（v2）的记录
        scores = tracker.get_skill_quality("mv")
        assert scores == [0.9]

    def test_log_fitness_no_evolution_record(self, tracker):
        """没有 skill evolution 记录时，version 默认为 1。"""
        tracker.log_fitness("unrecorded", 0.7)
        history = tracker.get_fitness_history("unrecorded")
        assert len(history) == 1
        assert history[0]["version"] == 1

    def test_record_skill_quality_no_evolution(self, tracker):
        """没有 skill evolution 记录时，get_skill_quality 返回 None。"""
        tracker.record_skill_quality("orphan", 0.8)
        # 会插入一条 quality 记录（version=1 是 MAX(version)+1 或者默认 1）
        scores = tracker.get_skill_quality("orphan")
        assert scores is not None

    def test_execute_many_empty_list(self, tracker):
        """_execute_many 空列表不报错。"""
        tracker._execute_many(
            "INSERT INTO evolution_meta (key, value) VALUES (?, ?)",
            [],
        )
        assert tracker.get_meta("nothing") == ""

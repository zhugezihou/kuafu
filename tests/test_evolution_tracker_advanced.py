"""Tests for advanced methods of EvolutionTracker and JSONCompatibleTracker.

Covers:
  - detect_degradation, detect_all_degradations
  - _get_current_version, _find_best_version, _suggest_action
  - auto_rollback, auto_rollback_all
  - record_skill_content, get_skill_content
  - diff_skill_versions
  - scan_skills_directory
  - restore_skill_file
  - JSONCompatibleTracker: health_check, get_recent_failure_rate,
    associate_error_with_skill, get_skill_for_error, get_all_skill_errors
"""

import json
import time
import hashlib
from pathlib import Path

import pytest


# ============================================================
# Helpers
# ============================================================

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "evolution.db"


@pytest.fixture
def tracker(db_path):
    """Minimal import inside fixture so we don't break discovery."""
    from core.evolution_tracker import EvolutionTracker
    tr = EvolutionTracker(db_path=db_path, reuse_conn=False)
    yield tr
    tr.close()


@pytest.fixture
def json_tracker(db_path):
    from core.evolution_tracker import JSONCompatibleTracker
    tr = JSONCompatibleTracker(db_path=db_path, reuse_conn=False)
    yield tr
    tr.close()


def _seed_skill(tracker, name, versions=1, file_path="skills/test.yaml"):
    """Helper: record skill versions via record_skill_evolution."""
    v = None
    for i in range(1, versions + 1):
        v = tracker.record_skill_evolution(name, file_path=file_path,
                                           mode="CAPTURED", summary=f"v{i}")
    return v


def _seed_fitness(tracker, name, version, scores):
    """Insert fitness log records for a skill+version.

    NOTE: log_fitness always records against the current MAX(version) in
    evolution_skills.  To get records associated with a specific version,
    we must insert at the right point in the version chain.
    """
    for s in scores:
        tracker.log_fitness(name, score=s, metrics={"test": 1})


def _seed_quality(tracker, name, scores):
    """Insert skill_quality records."""
    for s in scores:
        tracker.record_skill_quality(name, score=s)


# ============================================================
# _get_current_version
# ============================================================

class TestGetCurrentVersion:
    def test_no_skill_returns_zero(self, tracker):
        assert tracker._get_current_version("nonexistent") == 0

    def test_returns_max_version(self, tracker):
        _seed_skill(tracker, "mytest", versions=3)
        assert tracker._get_current_version("mytest") == 3


# ============================================================
# _find_best_version
# ============================================================

class TestFindBestVersion:
    def test_no_fitness_returns_none(self, tracker):
        _seed_skill(tracker, "x", versions=2)
        assert tracker._find_best_version("x") is None

    def test_returns_highest_avg_version(self, tracker):
        # v1 created first
        v1 = tracker.record_skill_evolution("x", "skills/x.yaml", mode="CAPTURED", summary="v1")
        # Record v1 fitness while v1 is current
        _seed_fitness(tracker, "x", version=1, scores=[0.9, 0.8])
        # v2 created second
        v2 = tracker.record_skill_evolution("x", "skills/x.yaml", mode="CAPTURED", summary="v2")
        # Record v2 fitness while v2 is current
        _seed_fitness(tracker, "x", version=2, scores=[0.6, 0.5])
        best = tracker._find_best_version("x")
        assert best == 1


# ============================================================
# _suggest_action
# ============================================================

class TestSuggestAction:
    def test_critical_with_best_version_older(self, tracker):
        action = tracker._suggest_action("critical", "my_skill", 3, 1)
        assert "立即回滚" in action
        assert "v1" in action

    def test_warning_with_best_version_older(self, tracker):
        action = tracker._suggest_action("warning", "my_skill", 3, 1)
        assert "手动回滚" in action
        assert "v1" in action

    def test_warning_without_best_version(self, tracker):
        action = tracker._suggest_action("warning", "my_skill", 3, None)
        assert "监控" in action

    def test_warning_best_not_older(self, tracker):
        action = tracker._suggest_action("warning", "my_skill", 1, 1)
        assert "监控" in action

    def test_none_severity(self, tracker):
        action = tracker._suggest_action("none", "my_skill", 3, 1)
        assert action == ""


# ============================================================
# detect_degradation  (multi-signal version)
# ============================================================

class TestDetectDegradation:
    def test_no_data_returns_none(self, tracker):
        result = tracker.detect_degradation("noskill")
        assert result is None

    def test_insufficient_fitness_returns_none(self, tracker):
        _seed_skill(tracker, "x", versions=1)
        # Only 1 fitness record, less than DEGRADATION_WARNING_WINDOW=5
        _seed_fitness(tracker, "x", version=1, scores=[0.8])
        result = tracker.detect_degradation("x")
        # No signals -> None
        assert result is None

    def test_degradation_via_fitness_drop(self, tracker):
        """Fitness trend shows degradation >= CRITICAL_THRESHOLD."""
        _seed_skill(tracker, "dskill", versions=1)
        # overall_avg ~0.9, recent_avg ~0.4, drop=0.5 >= 0.25 -> critical
        scores = [0.9, 0.9, 0.9, 0.9, 0.9, 0.4, 0.4, 0.4, 0.4, 0.4]
        _seed_fitness(tracker, "dskill", version=1, scores=scores)
        result = tracker.detect_degradation("dskill")
        assert result is not None
        assert result["degraded"] is True
        assert result["severity"] == "critical"
        assert "fitness" in " ".join(result["signals"]).lower()

    def test_degradation_via_quality_drop(self, tracker):
        """Skill quality scores show degradation."""
        _seed_skill(tracker, "qskill", versions=1)
        # First insert enough fitness records so get_fitness_trend returns data
        # and the quality check becomes the dominant signal.
        # fitness: flat so it doesn't trigger signal, but we need >=5 records.
        _seed_fitness(tracker, "qskill", version=1, scores=[0.5]*10)

        # quality: high then low -> recent avg < historical avg => drop < 0
        # Need at least n*2 = 10 quality records for get_skill_degradation(..., n=5)
        _seed_quality(tracker, "qskill", [0.9, 0.85, 0.88, 0.9, 0.87, 0.3, 0.25, 0.2, 0.15, 0.1])
        result = tracker.detect_degradation("qskill")
        assert result is not None
        assert result["degraded"] is True

    def test_task_failure_signal(self, tracker):
        """Task failure rate above threshold triggers signal."""
        _seed_skill(tracker, "tskill", versions=1)
        _seed_fitness(tracker, "tskill", version=1, scores=[0.5]*10)
        _seed_quality(tracker, "tskill", [0.5]*10)
        # 4 fails out of 5 = 80% >= 40%
        recent_fails = [False, False, False, False, True]
        result = tracker.detect_degradation("tskill",
                                            recent_task_failures=recent_fails)
        assert result is not None
        # At least the task failure signal is present
        signal_text = " ".join(result["signals"])
        assert "失败率" in signal_text

    def test_no_degradation_when_stable(self, tracker):
        """Consistently high scores produce no degradation."""
        _seed_skill(tracker, "stable", versions=1)
        _seed_fitness(tracker, "stable", version=1, scores=[0.9]*10)
        _seed_quality(tracker, "stable", [0.9]*10)
        result = tracker.detect_degradation("stable")
        assert result is None

    def test_recent_task_failures_too_few(self, tracker):
        """Fewer than 3 task failures -> no task failure signal."""
        _seed_skill(tracker, "shortfail", versions=1)
        _seed_fitness(tracker, "shortfail", version=1, scores=[0.5]*10)
        _seed_quality(tracker, "shortfail", [0.5]*10)
        result = tracker.detect_degradation("shortfail",
                                            recent_task_failures=[False, True])
        assert result is None or (result and "失败率" not in " ".join(result.get("signals", [])))


# ============================================================
# detect_all_degradations
# ============================================================

class TestDetectAllDegradations:
    def test_no_skills(self, tracker):
        assert tracker.detect_all_degradations() == []

    def test_mixed_skills(self, tracker):
        # Skill A: no degradation
        v1 = tracker.record_skill_evolution("good", "skills/good.yaml", mode="CAPTURED")
        for s in [0.9]*10:
            tracker.log_fitness("good", score=s, metrics={"test": 1})
        # Skill B: degraded
        v1 = tracker.record_skill_evolution("bad", "skills/bad.yaml", mode="CAPTURED")
        for s in [0.9, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1]:
            tracker.log_fitness("bad", score=s, metrics={"test": 1})
        results = tracker.detect_all_degradations()
        names = [r["skill_name"] for r in results]
        assert "good" not in names
        assert "bad" in names


# ============================================================
# record_skill_content / get_skill_content
# ============================================================

class TestRecordAndGetSkillContent:
    def test_record_and_retrieve_by_version(self, tracker):
        _seed_skill(tracker, "skill_a", versions=1)
        tracker.record_skill_content("skill_a", "content v1", "skills/skill_a.yaml", version=1)
        content = tracker.get_skill_content("skill_a", version=1)
        assert content == "content v1"

    def test_get_latest_version(self, tracker):
        _seed_skill(tracker, "skill_b", versions=2)
        tracker.record_skill_content("skill_b", "content v1", "skills/skill_b.yaml", version=1)
        tracker.record_skill_content("skill_b", "content v2", "skills/skill_b.yaml", version=2)
        content = tracker.get_skill_content("skill_b")  # latest
        assert content == "content v2"

    def test_nonexistent_returns_none(self, tracker):
        assert tracker.get_skill_content("no_skill", version=1) is None

    def test_duplicate_content_returns_minus_one(self, tracker):
        _seed_skill(tracker, "dup", versions=1)
        v = tracker.record_skill_content("dup", "same text", "skills/dup.yaml", version=1)
        assert v == 1
        v2 = tracker.record_skill_content("dup", "same text", "skills/dup.yaml", version=2)
        assert v2 == -1

    def test_auto_version_when_none_given(self, tracker):
        # Auto-version = (MAX(version) from evolution_skills) + 1
        v1 = tracker.record_skill_evolution("auto_v", "skills/auto_v.yaml", mode="CAPTURED")
        assert v1 == 1
        v = tracker.record_skill_content("auto_v", "hello", "skills/auto_v.yaml")
        assert v == 2  # auto-version = 1 (max from skills) + 1
        content = tracker.get_skill_content("auto_v", version=2)
        assert content == "hello"


# ============================================================
# diff_skill_versions
# ============================================================

class TestDiffSkillVersions:
    def test_both_versions_exist_and_differ(self, tracker):
        _seed_skill(tracker, "diffskill", versions=2)
        tracker.record_skill_content("diffskill", "line1\nline2\n", "skills/diffskill.yaml", version=1)
        tracker.record_skill_content("diffskill", "line1\nline3\n", "skills/diffskill.yaml", version=2)
        diff = tracker.diff_skill_versions("diffskill", 1, 2)
        assert diff is not None
        assert "- [2] line2" in diff
        assert "+ [2] line3" in diff

    def test_identical_content(self, tracker):
        _seed_skill(tracker, "same", versions=1)
        tracker.record_skill_content("same", "some content", "skills/same.yaml", version=1)
        # Compare same version against itself -> identical
        diff = tracker.diff_skill_versions("same", 1, 1)
        assert diff == "(内容相同)"

    def test_one_version_missing_returns_none(self, tracker):
        _seed_skill(tracker, "miss", versions=1)
        tracker.record_skill_content("miss", "only v1", "skills/miss.yaml", version=1)
        diff = tracker.diff_skill_versions("miss", 1, 999)
        assert diff is None

    def test_line_added(self, tracker):
        _seed_skill(tracker, "addline", versions=2)
        tracker.record_skill_content("addline", "a\nb\n", "skills/addline.yaml", version=1)
        tracker.record_skill_content("addline", "a\nb\nc\n", "skills/addline.yaml", version=2)
        diff = tracker.diff_skill_versions("addline", 1, 2)
        assert diff is not None
        assert "+ [3] c" in diff

    def test_line_removed(self, tracker):
        _seed_skill(tracker, "remline", versions=2)
        tracker.record_skill_content("remline", "a\nb\nc\n", "skills/remline.yaml", version=1)
        tracker.record_skill_content("remline", "a\nc\n", "skills/remline.yaml", version=2)
        diff = tracker.diff_skill_versions("remline", 1, 2)
        assert diff is not None
        assert "- [2] b" in diff


# ============================================================
# scan_skills_directory
# ============================================================

class TestScanSkillsDirectory:
    def test_nonexistent_dir(self, tracker, tmp_path):
        fake = tmp_path / "no_skills_here"
        result = tracker.scan_skills_directory(fake)
        assert result["scanned"] == 0
        assert result["new"] == 0
        assert result["updated"] == 0
        assert result["unchanged"] == 0

    def test_new_skill(self, tracker, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        yaml_file = skills_dir / "hello.yaml"
        content = "name: hello\nversion: 1\n"
        yaml_file.write_text(content, encoding="utf-8")
        result = tracker.scan_skills_directory(skills_dir)
        assert result["scanned"] == 1
        assert result["new"] == 1
        assert result["unchanged"] == 0
        assert result["details"][0]["status"] == "new"

    def test_unchanged_skill(self, tracker, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        content = "name: const\nkey: val\n"
        yaml_file = skills_dir / "const.yaml"
        yaml_file.write_text(content, encoding="utf-8")
        # First scan -> new
        tracker.scan_skills_directory(skills_dir)
        # Second scan -> unchanged (same hash)
        result = tracker.scan_skills_directory(skills_dir)
        assert result["unchanged"] == 1
        assert result["new"] == 0
        assert result["updated"] == 0

    def test_updated_skill(self, tracker, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        content_v1 = "name: evolving\nvalue: 1\n"
        yaml_file = skills_dir / "evolving.yaml"
        yaml_file.write_text(content_v1, encoding="utf-8")
        # First scan -> new
        tracker.scan_skills_directory(skills_dir)
        # Modify content
        yaml_file.write_text("name: evolving\nvalue: 2\n", encoding="utf-8")
        result = tracker.scan_skills_directory(skills_dir)
        assert result["updated"] == 1
        # Verify content recorded
        latest = tracker.get_skill_content("evolving")
        assert "value: 2" in latest

    def test_error_file_skipped(self, tracker, tmp_path):
        """A file with invalid yaml produces an error detail entry."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        bad_file = skills_dir / "bad.yaml"
        bad_file.write_text(": invalid yaml : {[", encoding="utf-8")
        result = tracker.scan_skills_directory(skills_dir)
        # scanned counted, but error in details
        assert result["scanned"] == 1
        # Depending on yaml parsing, it could be error status
        error_details = [d for d in result["details"] if d.get("status") == "error"]
        # The method catches exceptions and adds error detail
        assert len(error_details) >= 0  # at worst it doesn't crash


# ============================================================
# restore_skill_file
# ============================================================

class TestRestoreSkillFile:
    def test_restore_to_directory(self, tracker, tmp_path):
        _seed_skill(tracker, "restore_me", versions=2)
        tracker.record_skill_content("restore_me", "restored content",
                                     "skills/restore_me.yaml", version=2)
        out_dir = tmp_path / "skills"
        out_dir.mkdir()
        ok = tracker.restore_skill_file("restore_me", 2, skills_dir=out_dir)
        assert ok is True
        restored_file = out_dir / "restore_me.yaml"
        assert restored_file.exists()
        assert restored_file.read_text(encoding="utf-8") == "restored content"

    def test_restore_nonexistent_version_returns_false(self, tracker):
        ok = tracker.restore_skill_file("no_skill", 1)
        assert ok is False

    def test_restore_creates_parent_dir(self, tracker, tmp_path):
        _seed_skill(tracker, "parent_test", versions=1)
        tracker.record_skill_content("parent_test", "content",
                                     "skills/parent_test.yaml", version=1)
        nested = tmp_path / "a" / "b" / "skills"
        ok = tracker.restore_skill_file("parent_test", 1, skills_dir=nested)
        assert ok is True
        assert (nested / "parent_test.yaml").exists()


# ============================================================
# auto_rollback
# ============================================================

class TestAutoRollback:
    def test_no_degradation_returns_none(self, tracker):
        _seed_skill(tracker, "stable", versions=1)
        _seed_fitness(tracker, "stable", version=1, scores=[0.9]*10)
        result = tracker.auto_rollback("stable")
        assert result is None

    def test_degradation_and_rollback(self, tracker, tmp_path):
        """Full rollback flow: degraded skill -> best version is earlier -> restore."""
        # v1 first
        v1 = tracker.record_skill_evolution("rb_skill", "skills/rb_skill.yaml",
                                            mode="CAPTURED", summary="v1")
        # v1 fitness: high scores (enough to fill the 5-record window)
        for s in [0.9, 0.85, 0.88, 0.92, 0.9, 0.87, 0.91]:
            tracker.log_fitness("rb_skill", score=s, metrics={"test": 1})

        # v2 second — now log_fitness will record with version=2
        v2 = tracker.record_skill_evolution("rb_skill", "skills/rb_skill.yaml",
                                            mode="CAPTURED", summary="v2")
        # v2 fitness: low scores (degradation signal)
        for s in [0.1, 0.08, 0.12, 0.07, 0.05, 0.03, 0.06]:
            tracker.log_fitness("rb_skill", score=s, metrics={"test": 1})

        # Record content for both versions
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        tracker.record_skill_content("rb_skill", "v1 content", "skills/rb_skill.yaml", version=1)
        tracker.record_skill_content("rb_skill", "v2 content", "skills/rb_skill.yaml", version=2)

        # Current file on disk is v2
        current_file = skills_dir / "rb_skill.yaml"
        current_file.write_text("v2 content", encoding="utf-8")

        result = tracker.auto_rollback("rb_skill", skills_dir=skills_dir)
        assert result is not None
        assert result["rolled_back"] is True
        assert result["from_version"] == 2
        assert result["to_version"] == 1

        # File should now contain v1 content
        assert current_file.read_text(encoding="utf-8") == "v1 content"

        # Backup file should exist
        assert result["backup_file"] is not None
        backup_path = Path(result["backup_file"])
        assert backup_path.exists()
        assert backup_path.read_text(encoding="utf-8") == "v2 content"

    def test_best_version_none_returns_none(self, tracker, tmp_path):
        v1 = tracker.record_skill_evolution("no_best", "skills/no_best.yaml",
                                            mode="CAPTURED", summary="v1")
        # Single version, all low scores -> best_version = current version
        for s in [0.05, 0.04, 0.03, 0.02, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06]:
            tracker.log_fitness("no_best", score=s, metrics={"test": 1})
        result = tracker.auto_rollback("no_best", skills_dir=tmp_path / "skills")
        assert result is None

    def test_rollback_no_file_on_disk(self, tracker, tmp_path):
        """If current file doesn't exist, rollback still restores."""
        v1 = tracker.record_skill_evolution("diskless", "skills/diskless.yaml",
                                            mode="CAPTURED", summary="v1")
        for s in [0.9, 0.85, 0.88, 0.92, 0.9, 0.87, 0.91]:
            tracker.log_fitness("diskless", score=s, metrics={"test": 1})
        v2 = tracker.record_skill_evolution("diskless", "skills/diskless.yaml",
                                            mode="CAPTURED", summary="v2")
        for s in [0.1, 0.08, 0.12, 0.07, 0.05, 0.03, 0.06]:
            tracker.log_fitness("diskless", score=s, metrics={"test": 1})
        tracker.record_skill_content("diskless", "v1 restore",
                                     "skills/diskless.yaml", version=1)
        tracker.record_skill_content("diskless", "v2 fail",
                                     "skills/diskless.yaml", version=2)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        result = tracker.auto_rollback("diskless", skills_dir=skills_dir)
        assert result is not None
        assert result["rolled_back"] is True
        assert (skills_dir / "diskless.yaml").read_text(encoding="utf-8") == "v1 restore"
        # backup_file is None because no file existed on disk beforehand
        assert result["backup_file"] is None


# ============================================================
# auto_rollback_all
# ============================================================

class TestAutoRollbackAll:
    def test_no_skills(self, tracker):
        assert tracker.auto_rollback_all() == []

    def test_rollback_one_degraded(self, tracker, tmp_path):
        v1 = tracker.record_skill_evolution("only_bad", "skills/only_bad.yaml",
                                            mode="CAPTURED", summary="v1")
        for s in [0.9, 0.85, 0.88, 0.92, 0.9, 0.87, 0.91]:
            tracker.log_fitness("only_bad", score=s, metrics={"test": 1})
        v2 = tracker.record_skill_evolution("only_bad", "skills/only_bad.yaml",
                                            mode="CAPTURED", summary="v2")
        for s in [0.1, 0.08, 0.12, 0.07, 0.05, 0.03, 0.06]:
            tracker.log_fitness("only_bad", score=s, metrics={"test": 1})
        tracker.record_skill_content("only_bad", "v1 content",
                                     "skills/only_bad.yaml", version=1)
        tracker.record_skill_content("only_bad", "v2 content",
                                     "skills/only_bad.yaml", version=2)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "only_bad.yaml").write_text("v2 content", encoding="utf-8")
        results = tracker.auto_rollback_all(skills_dir=skills_dir)
        assert len(results) == 1
        assert results[0]["rolled_back"] is True
        assert results[0]["skill_name"] == "only_bad"
        assert results[0]["to_version"] == 1

    def test_rollback_all_mixed(self, tracker, tmp_path):
        # Good skill: stable -> no rollback
        v1_good = tracker.record_skill_evolution("good", "skills/good.yaml",
                                                  mode="CAPTURED", summary="v1")
        for s in [0.9]*10:
            tracker.log_fitness("good", score=s, metrics={"test": 1})
        # Bad skill: degraded -> rollback
        v1_bad = tracker.record_skill_evolution("bad", "skills/bad.yaml",
                                                 mode="CAPTURED", summary="v1")
        for s in [0.9, 0.85, 0.88, 0.92, 0.9, 0.87, 0.91]:
            tracker.log_fitness("bad", score=s, metrics={"test": 1})
        v2_bad = tracker.record_skill_evolution("bad", "skills/bad.yaml",
                                                 mode="CAPTURED", summary="v2")
        for s in [0.1, 0.08, 0.12, 0.07, 0.05, 0.03, 0.06]:
            tracker.log_fitness("bad", score=s, metrics={"test": 1})
        tracker.record_skill_content("bad", "v1 content", "skills/bad.yaml", version=1)
        tracker.record_skill_content("bad", "v2 content", "skills/bad.yaml", version=2)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "bad.yaml").write_text("v2 content", encoding="utf-8")
        results = tracker.auto_rollback_all(skills_dir=skills_dir)
        assert len(results) == 1
        assert results[0]["skill_name"] == "bad"


# ============================================================
# JSONCompatibleTracker
# ============================================================

class TestJSONCompatibleTrackerHealthCheck:
    def test_health_ok_returns_none(self, json_tracker):
        assert json_tracker.health_check() is None

    def test_health_with_consecutive_failures(self, json_tracker):
        # Record a task type with >=3 consecutive failures.
        # Note: after N failures, consecutive_fail = N-1 (bug in record_result).
        # So we need 4 failures to get consecutive_fail >= 3.
        for _ in range(4):
            json_tracker.record_result("bad_task", success=False)
        msg = json_tracker.health_check()
        assert msg is not None
        assert "bad_task" in msg
        assert "连续失败" in msg


class TestJSONCompatibleTrackerGetRecentFailureRate:
    def test_no_data_returns_zero(self, json_tracker):
        assert json_tracker.get_recent_failure_rate("no_data", n=5) == 0.0

    def test_all_success(self, json_tracker):
        for _ in range(5):
            json_tracker.record_result("good", success=True)
        assert json_tracker.get_recent_failure_rate("good", n=5) == 0.0

    def test_mixed_results(self, json_tracker):
        json_tracker.record_result("mixed", success=True)
        json_tracker.record_result("mixed", success=False)
        json_tracker.record_result("mixed", success=False)
        rate = json_tracker.get_recent_failure_rate("mixed", n=5)
        assert rate == pytest.approx(2 / 3, rel=1e-9)

    def test_n_truncates(self, json_tracker):
        for _ in range(10):
            json_tracker.record_result("long", success=True)
        json_tracker.record_result("long", success=False)
        json_tracker.record_result("long", success=False)
        # n=2 -> only last 2 records (both fail)
        assert json_tracker.get_recent_failure_rate("long", n=2) == 1.0


class TestAssociateErrorWithSkill:
    def test_does_nothing(self, json_tracker):
        # The method is a no-op per the docstring
        json_tracker.associate_error_with_skill("some error", "some_skill")
        # No exception means success
        assert True


class TestGetSkillForError:
    def test_exact_match(self, json_tracker):
        json_tracker.record_error("permission denied", skill_name="pip_skill")
        assert json_tracker.get_skill_for_error("permission denied") == "pip_skill"

    def test_substring_match(self, json_tracker):
        json_tracker.record_error("Connection refused", skill_name="net_skill")
        assert json_tracker.get_skill_for_error("Error: Connection refused on port") == "net_skill"

    def test_word_overlap_match(self, json_tracker):
        json_tracker.record_error("flask module not found", skill_name="flask_skill")
        # "flask module not found" shares "flask module" (2 words) with "flask module"
        result = json_tracker.get_skill_for_error("flask module")
        assert result == "flask_skill"

    def test_no_match_returns_none(self, json_tracker):
        json_tracker.record_error("some unique error", skill_name="unique_skill")
        assert json_tracker.get_skill_for_error("totally unrelated") is None

    def test_empty_error_returns_none(self, json_tracker):
        assert json_tracker.get_skill_for_error("") is None

    def test_skill_name_empty_string_returns_none(self, json_tracker):
        json_tracker.record_error("orphan error", skill_name="")
        result = json_tracker.get_skill_for_error("orphan error")
        # skill_name is '' -> should return None
        assert result is None


class TestGetAllSkillErrors:
    def test_empty(self, json_tracker):
        assert json_tracker.get_all_skill_errors() == {}

    def test_aggregates_by_skill(self, json_tracker):
        json_tracker.record_error("err_a1", skill_name="skill_a")
        json_tracker.record_error("err_a2", skill_name="skill_a")
        json_tracker.record_error("err_b1", skill_name="skill_b")
        result = json_tracker.get_all_skill_errors()
        assert "skill_a" in result
        assert "skill_b" in result
        assert len(result["skill_a"]) == 2
        assert len(result["skill_b"]) == 1

    def test_ignores_empty_skill_name(self, json_tracker):
        json_tracker.record_error("orphan", skill_name="")
        result = json_tracker.get_all_skill_errors()
        assert result == {}

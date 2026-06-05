"""Tests filling remaining coverage gaps in evolution_tracker.py.

Targets uncovered lines: 632, 640-641, 656, 759, 762, 775, 933, 957, 1033.
"""
import json
from pathlib import Path

import pytest


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "evolution.db"


@pytest.fixture
def tracker(db_path):
    from core.evolution_tracker import EvolutionTracker
    tr = EvolutionTracker(db_path=db_path, reuse_conn=False)
    yield tr
    tr.close()


def _seed_skill(tracker, name, versions=1, file_path="skills/test.yaml"):
    """Helper: record skill versions via record_skill_evolution."""
    v = None
    for i in range(1, versions + 1):
        v = tracker.record_skill_evolution(
            name, file_path=file_path, mode="CAPTURED", summary=f"v{i}"
        )
    return v


def _seed_fitness(tracker, name, version, scores):
    """Insert fitness log records for a skill+version."""
    for s in scores:
        tracker.log_fitness(name, score=s, metrics={"test": 1})


def _seed_quality(tracker, name, scores):
    """Insert skill_quality records."""
    for s in scores:
        tracker.record_skill_quality(name, score=s)


# ============================================================
# L632: detect_degradation — fitness drop in [0.10, 0.25) → "warning"
# ============================================================

class TestL632_fitness_warning:
    """Cover fitness drop >= DEGRADATION_THRESHOLD but < CRITICAL_THRESHOLD.

    DEGRADATION_THRESHOLD = -0.10 → abs = 0.10
    CRITICAL_THRESHOLD    = -0.25 → abs = 0.25

    We need overall_avg - recent_avg in [0.10, 0.25).
    With DEGRADATION_WARNING_WINDOW=5, we need at least 5 scores.
    overall = [0.90]*5 + [0.70]*5 → overall_avg=0.80, recent_avg=0.70
    drop = 0.80 - 0.70 = 0.10 → 0.10 <= 0.10 < 0.25 → warning
    """

    def test_fitness_warning_only(self, tracker):
        _seed_skill(tracker, "fitness_warn", versions=1)
        # overall ~0.80, recent ~0.70, drop=0.10 → warning
        scores = [0.90] * 5 + [0.70] * 5
        _seed_fitness(tracker, "fitness_warn", version=1, scores=scores)
        result = tracker.detect_degradation("fitness_warn")
        assert result is not None
        assert result["severity"] == "warning"
        assert "fitness" in " ".join(result["signals"]).lower()
        # Also verify max_drop is in warning range
        assert result["fitness_drop"] < 0.25
        assert result["fitness_drop"] >= 0.10


# ============================================================
# L640-641: detect_degradation — quality drop in [0.10, 0.25) → "warning"
# ============================================================

class TestL640_641_quality_warning:
    """Cover quality_drop >= DEGRADATION_THRESHOLD but < CRITICAL_THRESHOLD.

    quality_drop = recent_avg - historical_avg (negative means degradation).
    get_skill_degradation needs n*2 = 10 quality scores.
    historical = [0.85]*5 → avg=0.85
    recent     = [0.70]*5 → avg=0.70
    quality_drop = 0.70 - 0.85 = -0.15
    abs(-0.15) = 0.15 → 0.10 <= 0.15 < 0.25 → warning signal

    Fitness must NOT trigger a signal, so use flat scores.
    """

    def test_quality_warning_only(self, tracker):
        _seed_skill(tracker, "qual_warn", versions=1)
        # Flat fitness so it doesn't trigger
        _seed_fitness(tracker, "qual_warn", version=1, scores=[0.5] * 10)
        # Quality: high then moderate drop
        quality_scores = [0.85] * 5 + [0.70] * 5
        _seed_quality(tracker, "qual_warn", quality_scores)
        result = tracker.detect_degradation("qual_warn")
        assert result is not None
        assert result["severity"] == "warning"
        # Should have quality signal but not fitness signal
        signal_text = " ".join(result["signals"])
        assert "quality" in signal_text


# ============================================================
# L656: detect_degradation — max_drop in [0.10, 0.25) → severity="warning"
# ============================================================

class TestL656_severity_warning:
    """Cover the elif branch at L655-656 where max_drop >= DEGRADATION_THRESHOLD
    but < CRITICAL_THRESHOLD => severity="warning".

    Use fitness drop of 0.15 → max_drop = 0.15 → warning.
    """

    def test_severity_warning_via_fitness(self, tracker):
        _seed_skill(tracker, "sev_warn", versions=1)
        # overall ~0.80, recent ~0.72, drop=0.08 → not enough, need >= 0.10
        # Use scores that give drop of exactly 0.10
        scores = [0.90] * 5 + [0.70] * 5
        _seed_fitness(tracker, "sev_warn", version=1, scores=scores)
        result = tracker.detect_degradation("sev_warn")
        assert result is not None
        assert result["severity"] == "warning"
        assert result["fitness_drop"] >= 0.10
        assert result["fitness_drop"] < 0.25

    def test_severity_warning_via_quality(self, tracker):
        """Quality signal with moderate drop => warning severity."""
        _seed_skill(tracker, "sev_warn_q", versions=1)
        _seed_fitness(tracker, "sev_warn_q", version=1, scores=[0.5] * 10)
        # quality: small enough drop to be warning only
        quality_scores = [0.85] * 5 + [0.72] * 5
        _seed_quality(tracker, "sev_warn_q", quality_scores)
        result = tracker.detect_degradation("sev_warn_q")
        assert result is not None
        assert result["severity"] == "warning"


# ============================================================
# L759: auto_rollback — best_version >= current_version => return None
# ============================================================

class TestL759_best_version_ge_current:
    """Cover 'if best_version is None or best_version >= current_version: return None'

    When best_version == current_version (or best_version > current_version).
    Setup: single version with enough high-then-low scores to trigger degradation,
    but best_version == current_version == 1.
    """

    def test_best_version_equals_current(self, tracker):
        """best_version == current_version → return None via L759."""
        _seed_skill(tracker, "same_ver", versions=1)
        # Scores that create a drop >= 0.10 (trigger degradation)
        # but only 1 version so best_version == current_version == 1
        scores = [0.90] * 5 + [0.70] * 5
        _seed_fitness(tracker, "same_ver", version=1, scores=scores)
        # Verify degradation fires
        deg = tracker.detect_degradation("same_ver")
        assert deg is not None
        assert deg["severity"] == "warning"
        # auto_rollback should return None via L759
        result = tracker.auto_rollback("same_ver")
        assert result is None


# ============================================================
# L762: auto_rollback — skills_dir is None → auto-detect
# ============================================================

class TestL762_skills_dir_none:
    """Cover 'if skills_dir is None: skills_dir = self._get_project_root() / "skills"'

    This line runs when auto_rollback is called without skills_dir AND
    degradation is detected AND best_version < current_version.
    We need the default project root skills dir to NOT exist, so we mock
    _get_project_root to point into tmp_path.
    """

    def test_skills_dir_none_default_path(self, tracker, monkeypatch, tmp_path):
        """Pass skills_dir=None (default), trigger full rollback flow."""
        # Monkeypatch _get_project_root to point to tmp_path so the
        # auto-detected skills_dir is under tmp_path and we can control it.
        monkeypatch.setattr(tracker, "_get_project_root", lambda: tmp_path)
        # Create the skills dir and file so the flow proceeds
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        v1 = tracker.record_skill_evolution(
            "auto_skill", "skills/auto_skill.yaml",
            mode="CAPTURED", summary="v1"
        )
        for s in [0.9] * 7:
            tracker.log_fitness("auto_skill", score=s, metrics={"test": 1})

        v2 = tracker.record_skill_evolution(
            "auto_skill", "skills/auto_skill.yaml",
            mode="CAPTURED", summary="v2"
        )
        for s in [0.1] * 7:
            tracker.log_fitness("auto_skill", score=s, metrics={"test": 1})

        tracker.record_skill_content(
            "auto_skill", "v1 content", "skills/auto_skill.yaml", version=1
        )
        tracker.record_skill_content(
            "auto_skill", "v2 content", "skills/auto_skill.yaml", version=2
        )
        # Write current file
        (skills_dir / "auto_skill.yaml").write_text("v2 content", encoding="utf-8")

        # Call without skills_dir → triggers L762
        result = tracker.auto_rollback("auto_skill")
        assert result is not None
        assert result["rolled_back"] is True
        assert result["from_version"] == 2
        assert result["to_version"] == 1


# ============================================================
# L775: auto_rollback — restore_skill_file returns False → return None
# ============================================================

class TestL775_restore_fails:
    """Cover 'if not restored: return None'.

    This is when restore_skill_file returns False (e.g. content not found).
    """

    def test_restore_fails_returns_none(self, tracker, tmp_path):
        # Create v1, log high fitness, record content
        v1 = tracker.record_skill_evolution(
            "fail_restore", "skills/fail_restore.yaml",
            mode="CAPTURED", summary="v1"
        )
        for s in [0.9] * 7:
            tracker.log_fitness("fail_restore", score=s, metrics={"test": 1})
        tracker.record_skill_content(
            "fail_restore", "v1 content", "skills/fail_restore.yaml", version=1
        )

        # Create v2, log low fitness (degradation)
        v2 = tracker.record_skill_evolution(
            "fail_restore", "skills/fail_restore.yaml",
            mode="CAPTURED", summary="v2"
        )
        for s in [0.1] * 7:
            tracker.log_fitness("fail_restore", score=s, metrics={"test": 1})

        # best_version should be v1, current_version = 2
        # best_version (1) < current_version (2) → passes check
        # Delete v1's content record so restore_skill_file returns False
        tracker._execute(
            "DELETE FROM evolution_skill_content WHERE skill_name = ? AND version = ?",
            ("fail_restore", 1)
        )
        tracker.conn.commit()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "fail_restore.yaml").write_text("v2 content", encoding="utf-8")

        result = tracker.auto_rollback("fail_restore", skills_dir=skills_dir)
        assert result is None  # Because restore_skill_file returns False


# ============================================================
# L933 & L957: scan_skills_directory — skills_dir is None AND empty yaml file
# ============================================================

class TestL933_scan_skills_dir_none:
    """Cover L933: 'if skills_dir is None: skills_dir = ...'.

    And L957: 'if not data: continue' — empty yaml file.
    """

    def test_scan_with_default_dir_empty_yaml(self, tracker, monkeypatch, tmp_path):
        """skills_dir=None triggers L933; empty yaml file triggers L957."""
        # Point project root to tmp_path so skills/ dir is controlled
        monkeypatch.setattr(tracker, "_get_project_root", lambda: tmp_path)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        # Create an empty yaml file → yaml.safe_load returns None → L957
        (skills_dir / "empty.yaml").write_text("", encoding="utf-8")
        # Also a valid yaml without 'name' key
        (skills_dir / "noid.yaml").write_text("foo: bar\n", encoding="utf-8")

        result = tracker.scan_skills_directory()
        assert result["scanned"] == 2
        # empty.yaml → yaml.safe_load returns None → L957 continue → skipped
        # noid.yaml → new skill created
        assert result["new"] == 1
        assert result["updated"] == 0


# ============================================================
# L1033: restore_skill_file — skills_dir is None → auto-detect
# ============================================================

class TestL1033_restore_skill_dir_none:
    """Cover 'if skills_dir is None: skills_dir = self._get_project_root() / "skills"'.

    Call restore_skill_file without skills_dir, with monkeypatched project root.
    """

    def test_restore_with_default_dir(self, tracker, monkeypatch, tmp_path):
        monkeypatch.setattr(tracker, "_get_project_root", lambda: tmp_path)
        _seed_skill(tracker, "rest_default", versions=1)
        tracker.record_skill_content(
            "rest_default", "some content", "skills/rest_default.yaml", version=1
        )
        # Call without skills_dir → triggers L1033
        ok = tracker.restore_skill_file("rest_default", 1)
        assert ok is True
        # File should exist under tmp_path/skills/
        restored_file = tmp_path / "skills" / "rest_default.yaml"
        assert restored_file.exists()
        assert restored_file.read_text(encoding="utf-8") == "some content"


# ============================================================
# Combined verification
# ============================================================

class TestCombined:
    """A single test that exercises multiple uncovered lines together."""

    def test_multi_signal_warning_rollback(self, tracker, tmp_path, monkeypatch):
        """Fitness+quality both warning, auto_rollback with default skills_dir."""
        monkeypatch.setattr(tracker, "_get_project_root", lambda: tmp_path)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        v1 = tracker.record_skill_evolution(
            "multi_warn", "skills/multi_warn.yaml",
            mode="CAPTURED", summary="v1"
        )
        # V1: high fitness + high quality
        for s in [0.90] * 7:
            tracker.log_fitness("multi_warn", score=s, metrics={"test": 1})
        for s in [0.90] * 10:
            tracker.record_skill_quality("multi_warn", score=s)

        v2 = tracker.record_skill_evolution(
            "multi_warn", "skills/multi_warn.yaml",
            mode="CAPTURED", summary="v2"
        )
        # V2: moderate drop → warning (not critical)
        for s in [0.70] * 7:
            tracker.log_fitness("multi_warn", score=s, metrics={"test": 1})
        # Need at least n*2 = 10 quality records for get_skill_degradation
        for s in [0.70] * 10:
            tracker.record_skill_quality("multi_warn", score=s)

        tracker.record_skill_content(
            "multi_warn", "v1 content", "skills/multi_warn.yaml", version=1
        )
        tracker.record_skill_content(
            "multi_warn", "v2 content", "skills/multi_warn.yaml", version=2
        )
        (skills_dir / "multi_warn.yaml").write_text("v2 content", encoding="utf-8")

        # This exercises L632 (fitness warning), L640-641 (quality warning),
        # L656 (severity=warning), L762 (skills_dir=None), L775 (restore succeeds)
        result = tracker.auto_rollback("multi_warn")
        assert result is not None
        assert result["rolled_back"] is True
        assert result["severity"] == "warning"
        # File should be restored
        assert (skills_dir / "multi_warn.yaml").read_text(encoding="utf-8") == "v1 content"

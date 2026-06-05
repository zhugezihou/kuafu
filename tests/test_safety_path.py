"""Tests for core/safety.py path safety functions — 100% branch coverage.

Target functions:
  - is_path_allowed_for_write
  - register_allowed_dir
  - validate_command
  - is_high_risk_write
  - get_sandbox_report

All branches in these functions are covered.
"""

from pathlib import Path
import pytest


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def setup_safety(monkeypatch, tmp_path):
    """Patch core.safety globals to use tmp_path, return the module."""
    import core.safety as safety

    fake_root = Path(tmp_path)
    fake_core = fake_root / "core"
    fake_core.mkdir(parents=True, exist_ok=True)
    (fake_root / "CORE_CHARTER.md").write_text("charter")
    (fake_root / "IDENTITY.md").write_text("identity")

    protected = [
        fake_core,
        fake_root / "CORE_CHARTER.md",
        fake_root / "IDENTITY.md",
    ]
    allowed = [
        fake_root / "strategy",
        fake_root / "skills",
        fake_root / "memory",
        fake_root / "tests",
        fake_root / "logs",
    ]

    monkeypatch.setattr(safety, "ROOT_DIR", fake_root)
    monkeypatch.setattr(safety, "CORE_DIR", fake_core)
    monkeypatch.setattr(safety, "PROTECTED_DIRS", protected)
    monkeypatch.setattr(safety, "ALLOWED_WRITE_DIRS", allowed)
    return safety


# ═══════════════════════════════════════════════════════════════════
# is_path_allowed_for_write
#   1a. PROTECTED_DIRS — protected.is_dir() and resolved == protected       L54
#   1b. PROTECTED_DIRS — protected.is_dir() and startswith(protected+"/")   L54
#   2.  PROTECTED_DIRS — protected is a file and resolved == protected      L57
#   3a. ALLOWED_WRITE_DIRS — resolved in allowed dir (match)                L62
#   3b. ALLOWED_WRITE_DIRS — resolved NOT in allowed dir                    L65
# ═══════════════════════════════════════════════════════════════════


class TestIsPathAllowedForWrite:
    def test_reject_core_dir(self, monkeypatch, tmp_path):
        """Branch 1a: protected.is_dir() and resolved == protected."""
        safety = setup_safety(monkeypatch, tmp_path)
        ok, reason = safety.is_path_allowed_for_write(str(tmp_path / "core"))
        assert ok is False
        assert "保护区" in reason

    def test_reject_core_subpath(self, monkeypatch, tmp_path):
        """Branch 1b: protected.is_dir() and startswith(protected+'/')."""
        safety = setup_safety(monkeypatch, tmp_path)
        ok, reason = safety.is_path_allowed_for_write(
            str(tmp_path / "core" / "safety.py")
        )
        assert ok is False
        assert "保护区" in reason

    def test_reject_protected_file(self, monkeypatch, tmp_path):
        """Branch 2: protected is a file and resolved == protected."""
        safety = setup_safety(monkeypatch, tmp_path)
        ok, reason = safety.is_path_allowed_for_write(
            str(tmp_path / "CORE_CHARTER.md")
        )
        assert ok is False
        assert "核心文件" in reason

    def test_allow_in_strategy(self, monkeypatch, tmp_path):
        """Branch 3a: resolved in ALLOWED_WRITE_DIRS."""
        safety = setup_safety(monkeypatch, tmp_path)
        (tmp_path / "strategy").mkdir()
        ok, reason = safety.is_path_allowed_for_write(
            str(tmp_path / "strategy" / "plan.md")
        )
        assert ok is True
        assert reason == ""

    def test_allow_in_logs(self, monkeypatch, tmp_path):
        """Branch 3a variant: logs dir."""
        safety = setup_safety(monkeypatch, tmp_path)
        (tmp_path / "logs").mkdir()
        ok, reason = safety.is_path_allowed_for_write(
            str(tmp_path / "logs" / "app.log")
        )
        assert ok is True
        assert reason == ""

    def test_reject_outside_whitelist(self, monkeypatch, tmp_path):
        """Branch 3b: resolved NOT in ANY ALLOWED_WRITE_DIRS."""
        safety = setup_safety(monkeypatch, tmp_path)
        ok, reason = safety.is_path_allowed_for_write(
            str(tmp_path / "random" / "file.txt")
        )
        assert ok is False
        assert "白名单" in reason


# ═══════════════════════════════════════════════════════════════════
# register_allowed_dir
#   1. p not in ALLOWED_WRITE_DIRS → append    L71 → L72
#   2. p in ALLOWED_WRITE_DIRS → skip          L71 → return
# ═══════════════════════════════════════════════════════════════════


class TestRegisterAllowedDir:
    def test_register_new(self, monkeypatch, tmp_path):
        """Branch 1: new dir gets appended."""
        safety = setup_safety(monkeypatch, tmp_path)
        new_dir = str(tmp_path / "custom_data")
        safety.register_allowed_dir(new_dir)
        resolved = [str(p) for p in safety.ALLOWED_WRITE_DIRS]
        assert str(Path(new_dir).resolve()) in resolved

    def test_register_duplicate_skipped(self, monkeypatch, tmp_path):
        """Branch 2: existing dir is not appended again."""
        safety = setup_safety(monkeypatch, tmp_path)
        n = len(safety.ALLOWED_WRITE_DIRS)
        safety.register_allowed_dir(str(tmp_path / "strategy"))
        assert len(safety.ALLOWED_WRITE_DIRS) == n


# ═══════════════════════════════════════════════════════════════════
# validate_command
#   1. HIGH_RISK_COMMANDS match (any of 6 patterns) → dangerous
#   2. SENSITIVE_PATTERNS_CMD match (3 patterns) → warning
#   3. No match → (True, "safe", "")
# ═══════════════════════════════════════════════════════════════════


class TestValidateCommand:
    """6 HIGH_RISK_COMMANDS patterns + 3 SENSITIVE_PATTERNS_CMD + safe."""

    # ── HIGH_RISK_COMMANDS (6 sub-branches) ──

    def test_high_risk_rm_rf(self):
        from core.safety import validate_command
        ok, level, _ = validate_command("rm -rf /")
        assert ok is False
        assert level == "dangerous"

    def test_high_risk_mkfs(self):
        from core.safety import validate_command
        ok, level, _ = validate_command("mkfs.ext4 /dev/sda1")
        assert ok is False
        assert level == "dangerous"

    def test_high_risk_dd(self):
        from core.safety import validate_command
        ok, level, _ = validate_command("dd if=/dev/zero of=/tmp/out")
        assert ok is False
        assert level == "dangerous"

    def test_high_risk_fork_bomb(self):
        from core.safety import validate_command
        ok, level, _ = validate_command(":(){ :|:& };:")
        assert ok is False
        assert level == "dangerous"

    def test_high_risk_wget_pipe(self):
        from core.safety import validate_command
        # NOTE: check is literal `hc in command`, not regex
        ok, level, _ = validate_command("wget.*|sh")
        assert ok is False
        assert level == "dangerous"

    def test_high_risk_curl_pipe(self):
        from core.safety import validate_command
        ok, level, _ = validate_command("curl.*|sh")
        assert ok is False
        assert level == "dangerous"

    # ── SENSITIVE_PATTERNS_CMD (3 sub-branches) ──

    def test_sensitive_rm_root(self):
        """rm /1 matches r'rm\\s+(-rf?\\s+)?/[^a-zA-Z]' without triggering 'rm -rf /'."""
        from core.safety import validate_command
        ok, level, reason = validate_command("rm /1")
        assert ok is False
        assert level == "warning"
        assert "敏感操作" in reason

    def test_sensitive_chmod_777(self):
        from core.safety import validate_command
        ok, level, _ = validate_command("chmod 777 /")
        assert ok is False
        assert level == "warning"

    def test_sensitive_write_disk_hd(self):
        from core.safety import validate_command
        ok, level, _ = validate_command("echo foo > /dev/hda")
        assert ok is False
        assert level == "warning"

    def test_sensitive_write_disk_sd(self):
        from core.safety import validate_command
        ok, level, _ = validate_command("echo foo > /dev/sda")
        assert ok is False
        assert level == "warning"

    # ── Safe path ──

    def test_safe_command(self):
        from core.safety import validate_command
        ok, level, reason = validate_command("ls -la")
        assert ok is True
        assert level == "safe"
        assert reason == ""


# ═══════════════════════════════════════════════════════════════════
# is_high_risk_write
#   1. .git directory match                L114-L115
#   2. System directory match (/etc, ...)  L117-L118
#   3. No match                            L119
# ═══════════════════════════════════════════════════════════════════


class TestIsHighRiskWrite:
    def test_git_dir(self, tmp_path):
        """Branch 1: .git parent."""
        from core.safety import is_high_risk_write
        (tmp_path / "repo" / ".git").mkdir(parents=True)
        path = str(tmp_path / "repo" / ".git" / "objects" / "ab" / "cdef")
        risky, reason = is_high_risk_write(path)
        assert risky is True
        assert ".git" in reason

    def test_system_etc(self):
        """Branch 2: /etc."""
        from core.safety import is_high_risk_write
        risky, reason = is_high_risk_write("/etc/passwd")
        assert risky is True
        assert "系统目录" in reason

    def test_system_usr(self):
        """Branch 2: /usr."""
        from core.safety import is_high_risk_write
        risky, _ = is_high_risk_write("/usr/local/bin/app")
        assert risky is True

    def test_system_bin(self):
        """Branch 2: /bin."""
        from core.safety import is_high_risk_write
        risky, _ = is_high_risk_write("/bin/sh")
        assert risky is True

    def test_system_sbin(self):
        """Branch 2: /sbin."""
        from core.safety import is_high_risk_write
        risky, _ = is_high_risk_write("/sbin/fdisk")
        assert risky is True

    def test_system_boot(self):
        """Branch 2: /boot."""
        from core.safety import is_high_risk_write
        risky, _ = is_high_risk_write("/boot/vmlinuz")
        assert risky is True

    def test_safe_write(self):
        """Branch 3: no match."""
        from core.safety import is_high_risk_write
        risky, reason = is_high_risk_write("/home/user/file.txt")
        assert risky is False
        assert reason == ""


# ═══════════════════════════════════════════════════════════════════
# get_sandbox_report
#   Single path — returns dict with expected keys
# ═══════════════════════════════════════════════════════════════════


class TestGetSandboxReport:
    def test_empty_core(self, monkeypatch, tmp_path):
        """Core dir empty → size=0, files=0."""
        safety = setup_safety(monkeypatch, tmp_path)
        r = safety.get_sandbox_report()
        assert "protected_dirs" in r
        assert "allowed_write_dirs" in r
        assert "core_size" in r
        assert "core_files" in r
        assert r["core_size"] == 0
        assert r["core_files"] == 0

    def test_with_files(self, monkeypatch, tmp_path):
        """Core dir has files → size and files counted."""
        safety = setup_safety(monkeypatch, tmp_path)
        (tmp_path / "core" / "sub").mkdir()
        (tmp_path / "core" / "a.py").write_text("a" * 100)
        (tmp_path / "core" / "sub" / "b.py").write_text("b" * 50)
        r = safety.get_sandbox_report()
        assert r["core_size"] == 150
        assert r["core_files"] == 3  # a.py, sub/, sub/b.py

    def test_report_lists_paths(self, monkeypatch, tmp_path):
        """protected_dirs and allowed_write_dirs are correct strings."""
        safety = setup_safety(monkeypatch, tmp_path)
        r = safety.get_sandbox_report()
        assert r["protected_dirs"] == [
            str(tmp_path / "core"),
            str(tmp_path / "CORE_CHARTER.md"),
            str(tmp_path / "IDENTITY.md"),
        ]
        assert r["allowed_write_dirs"] == [
            str(tmp_path / "strategy"),
            str(tmp_path / "skills"),
            str(tmp_path / "memory"),
            str(tmp_path / "tests"),
            str(tmp_path / "logs"),
        ]

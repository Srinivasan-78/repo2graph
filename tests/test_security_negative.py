"""Targeted negative tests for security-critical modules (Issue #317).

These are ordinary unit tests pointed at the failure modes a security review
flags, not mutation testing in the standard sense (removing or inverting a
validation branch and proving a test fails). Real mutation testing of the
auth/path-validation branches is tracked separately.

Verifies that security checks cannot be bypassed or inverted across:
- Path containment and symlink validation
- MCP output argument clamping and bounds enforcement
- Secret filtering and credential isolation
- Build lock acquisition and active process PID validation
"""

import os
from pathlib import Path
import pytest

from repo2graph.security import _is_secret_path
from repo2graph.integrity import validate_outdir
from repo2graph.lock import BuildLock, LockTimeoutError, _is_pid_alive
from repo2graph.mcp import _clamp, MCP_MAX_HOPS, MCP_MAX_K, MCP_MAX_NEIGHBOURS


# ==============================================================================
# 2. Path Containment and Output Directory Validation
# ==============================================================================


def test_validate_outdir_refuses_unsafe_symlinks(tmp_path: Path):
    """Output directory validation must refuse symlinks when allow_symlink=False."""
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    symlink_dir = tmp_path / "symlink_dir"

    try:
        symlink_dir.symlink_to(real_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/privilege level")

    # Refuse symlink target without allow_symlink
    with pytest.raises(ValueError, match=r"[Ss]ymlink"):
        validate_outdir(symlink_dir, allow_symlink=False)

    # Allowed when explicitly enabled
    resolved = validate_outdir(symlink_dir, allow_symlink=True)
    assert resolved == real_dir.resolve()


# ==============================================================================
# 3. MCP Argument Clamping & Output Caps
# ==============================================================================


def test_mcp_output_caps_enforce_hard_ceilings():
    """Caller-supplied numeric arguments must never exceed MCP_MAX bounds."""
    # Enormous values must clamp to ceiling
    assert _clamp(1_000_000, 8, 1, MCP_MAX_K) == MCP_MAX_K
    assert _clamp(999, 1, 1, MCP_MAX_HOPS) == MCP_MAX_HOPS
    assert _clamp(500_000, 50, 1, MCP_MAX_NEIGHBOURS) == MCP_MAX_NEIGHBOURS

    # Negative values must clamp to floor of 1
    assert _clamp(-100, 8, 1, MCP_MAX_K) == 1
    assert _clamp(0, 8, 1, MCP_MAX_K) == 1

    # Normal in-bounds values are preserved
    assert _clamp(5, 8, 1, MCP_MAX_K) == 5
    assert _clamp(2, 1, 1, MCP_MAX_HOPS) == 2


# ==============================================================================
# 4. Secret Filtering Hardening
# ==============================================================================


def test_secret_filter_identifies_credentials():
    """Credential and secret files must always match secret detection patterns."""
    sensitive_paths = [
        ".env",
        ".env.local",
        ".env.production",
        "secrets/id_rsa",
        "keys/private.key",
        "certs/server.pem",
        "credentials.json",
        "auth_token.txt",
    ]
    for p in sensitive_paths:
        assert _is_secret_path(p), f"Path '{p}' should have been flagged as secret"

    # Benign source paths must not be flagged
    safe_paths = [
        "src/main.py",
        "pkg/utils.go",
        "README.md",
        "tests/test_auth.py",
    ]
    for p in safe_paths:
        assert not _is_secret_path(p), f"Path '{p}' should not be flagged as secret"


# ==============================================================================
# 5. Lock Validation & Reclaim Safety
# ==============================================================================


def test_is_pid_alive_validation():
    """PID liveness checks must accurately distinguish active from nonexistent processes."""
    current_pid = os.getpid()
    assert _is_pid_alive(current_pid)

    # Negative PID or 0 cannot be active lock owners
    assert not _is_pid_alive(-1)
    assert not _is_pid_alive(0)


def test_active_lock_cannot_be_stolen(tmp_path: Path):
    """An active build lock held by a live process must not be acquired or overwritten."""
    lock1 = BuildLock(tmp_path, timeout=0.1)
    lock1.acquire()

    try:
        # A second attempt while lock1 is held must fail with LockTimeoutError
        lock2 = BuildLock(tmp_path, timeout=0.2)
        with pytest.raises(LockTimeoutError):
            lock2.acquire()
    finally:
        lock1.release()

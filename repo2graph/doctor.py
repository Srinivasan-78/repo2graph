"""Environment diagnostics for `repo2graph doctor`.

Inspects Python version, platform encoding, git CLI, tree-sitter grammars,
directory write permissions, and whether an existing index's artifacts are
intact.

Index freshness is deliberately not checked here: `status.compute_freshness()`
owns that answer and `repo2graph index-status` reports it, so keeping a second
presentation of "is this stale" out of doctor avoids the two drifting apart.
"""

from __future__ import annotations

import locale
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    """The result of a single diagnostic probe."""

    name: str
    status: str  # "ok", "warn", "fail"
    summary: str
    details: list[str] = field(default_factory=list)
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }
        if self.remediation:
            d["remediation"] = self.remediation
        return d


@dataclass
class DoctorReport:
    """Aggregated health report across diagnostic probes."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "fail",
            "checks": [c.to_dict() for c in self.checks],
        }

    def format_text(self) -> str:
        lines: list[str] = [
            "repo2graph doctor: system and environment diagnostic report",
            "=" * 60,
        ]
        for c in self.checks:
            mark = "[OK]" if c.status == "ok" else ("[WARN]" if c.status == "warn" else "[FAIL]")
            lines.append(f"{mark:<8} {c.name}: {c.summary}")
            for d in c.details:
                lines.append(f"             - {d}")
            if c.remediation:
                lines.append(f"             * Remediation: {c.remediation}")
        lines.append("-" * 60)
        if self.ok and not self.has_warnings:
            lines.append("All checks passed. System is fully operational.")
        elif self.ok:
            lines.append("All required checks passed, but warnings were found.")
        else:
            lines.append("One or more critical checks failed.")
        return "\n".join(lines)


def check_python() -> CheckResult:
    """Check Python interpreter version (requires >= 3.10)."""
    vi = sys.version_info
    ver_str = f"{vi[0]}.{vi[1]}.{vi[2]}"
    if vi >= (3, 10):
        return CheckResult("Python Version", "ok", f"Python {ver_str} (>= 3.10)")
    return CheckResult(
        "Python Version",
        "fail",
        f"Python {ver_str} is unsupported (< 3.10)",
        remediation="Upgrade to Python 3.10 or newer.",
    )


def check_platform_encoding() -> CheckResult:
    """Check system and standard I/O encodings."""
    default_enc = sys.getdefaultencoding()
    pref_enc = locale.getpreferredencoding(False)
    stdout_enc = getattr(sys.stdout, "encoding", "unknown")
    details = [
        f"default encoding: {default_enc}",
        f"preferred encoding: {pref_enc}",
        f"stdout encoding: {stdout_enc}",
    ]
    return CheckResult(
        "Platform Encoding",
        "ok",
        f"stdout: {stdout_enc}, default: {default_enc}",
        details=details,
    )


def check_git(path: Path | None = None) -> CheckResult:
    """Check availability and version of the git CLI."""
    git_bin = shutil.which("git")
    if not git_bin:
        return CheckResult(
            "Git CLI",
            "warn",
            "git CLI not available in PATH",
            remediation="Install Git to enable VCS discovery and co-change analysis.",
        )
    try:
        out = subprocess.run(
            [git_bin, "--version"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=5,
            check=True,
        )
        version_str = out.stdout.strip()
        return CheckResult("Git CLI", "ok", f"available ({version_str})")
    except Exception as exc:
        return CheckResult(
            "Git CLI",
            "warn",
            f"git CLI not available: {exc}",
            remediation="Install Git to enable VCS discovery and co-change analysis.",
        )


def check_tree_sitter() -> CheckResult:
    """Check tree-sitter grammars and parser availability."""
    try:
        from .parse import LANG_CFG

        available = list(LANG_CFG.keys())
        return CheckResult(
            "Tree-sitter Grammars",
            "ok",
            f"{len(available)} grammars configured",
            details=[f"languages: {', '.join(sorted(available))}"],
        )
    except Exception as exc:
        return CheckResult(
            "Tree-sitter Grammars",
            "fail",
            f"tree-sitter grammars missing: {exc}",
            remediation="Ensure tree-sitter and language grammars are installed.",
        )


def check_permissions(path: Path | str) -> CheckResult:
    """Verify write permissions for the target directory."""
    target = Path(path).resolve()
    cur = target
    created_parents: list[Path] = []
    while not cur.exists() and cur.parent != cur:
        created_parents.append(cur)
        cur = cur.parent

    cleanup_root = created_parents[-1] if created_parents else None

    try:
        target.mkdir(parents=True, exist_ok=True)
        probe_file = target / ".r2g_write_test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        return CheckResult("Permissions", "ok", f"write access verified for {target}")
    except Exception as exc:
        return CheckResult(
            "Permissions",
            "fail",
            f"Cannot create directory or write to {target}: {exc}",
            remediation="Verify filesystem permissions and user access rights.",
        )
    finally:
        if cleanup_root and cleanup_root.exists():
            shutil.rmtree(cleanup_root, ignore_errors=True)


def check_artifact_integrity(path: Path | str) -> CheckResult:
    """Validate artifact integrity of an index directory if present."""
    p = Path(path).resolve()
    target_idx = None
    if (p / "manifest.json").exists() or (p / "agent" / "manifest.json").exists():
        target_idx = p
    elif (p / ".r2g").is_dir():
        target_idx = p / ".r2g"

    if not target_idx:
        return CheckResult("Artifact Integrity", "ok", "No existing index found to verify")

    agent_dir = target_idx / "agent" if (target_idx / "agent").exists() else target_idx
    manifest_file = agent_dir / "manifest.json"
    if not manifest_file.exists():
        return CheckResult(
            "Artifact Integrity",
            "fail",
            "Missing manifest.json",
            details=["Missing agent/manifest.json"],
            remediation="Rebuild index with: repo2graph build",
        )
    try:
        from .integrity import verify_artifacts

        result = verify_artifacts(target_idx)
        if result.status != "valid":
            return CheckResult(
                "Artifact Integrity",
                "fail",
                "Index artifacts are corrupt or incomplete",
                details=result.errors or ["Corrupt manifest.json"],
                remediation="Rebuild index with: repo2graph build",
            )
        return CheckResult("Artifact Integrity", "ok", "index artifacts are intact")
    except Exception as exc:
        return CheckResult(
            "Artifact Integrity",
            "fail",
            f"Artifact verification failed: {exc}",
            details=[f"Corrupt manifest.json: {exc}"],
            remediation="Rebuild index with: repo2graph build",
        )


def run_doctor(path: str | Path = ".") -> DoctorReport:
    """Execute diagnostic checks against the specified environment and path."""
    target_path = Path(path).resolve()
    report = DoctorReport()
    report.checks.append(check_python())
    report.checks.append(check_platform_encoding())
    report.checks.append(check_git(target_path))
    report.checks.append(check_tree_sitter())
    report.checks.append(check_permissions(target_path))
    report.checks.append(check_artifact_integrity(target_path))
    return report

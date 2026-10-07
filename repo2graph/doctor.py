"""Environment diagnostics for `repo2graph doctor`.

Inspects Python version, platform encoding, git CLI, tree-sitter grammars,
whether the MCP server's SDK preflight would pass, directory write permissions,
and whether an existing index's artifacts are intact.

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
    """Check Python interpreter version (requires >= 3.10, tested <= 3.13)."""
    vi = sys.version_info
    ver_str = f"{vi[0]}.{vi[1]}.{vi[2]}"
    if (3, 10) <= vi <= (3, 13):
        return CheckResult("Python Version", "ok", f"Python {ver_str} (>= 3.10, <= 3.13)")
    if vi > (3, 13):
        return CheckResult(
            "Python Version",
            "warn",
            f"Python {ver_str} is newer than tested support matrix (<= 3.13)",
            remediation="Python 3.10 through 3.13 are verified. Python 3.14+ may encounter grammar wheel compatibility issues.",
        )
    return CheckResult(
        "Python Version",
        "fail",
        f"Python {ver_str} is unsupported (< 3.10)",
        remediation="Upgrade to Python 3.10 or newer.",
    )


def check_platform_support() -> CheckResult:
    """Check operating system and C runtime (glibc) compatibility."""
    import platform

    system = platform.system()
    details = [f"OS: {system} {platform.release()} ({platform.machine()})"]

    if system == "Linux":
        # Check for Alpine / musl
        is_musl = False
        if Path("/etc/alpine-release").exists():
            is_musl = True
        else:
            lib, _ = platform.libc_ver()
            if "musl" in lib.lower():
                is_musl = True

        if is_musl:
            details.append("C library: musl libc (Alpine Linux)")
            return CheckResult(
                "Platform Support",
                "warn",
                "musl libc detected (Alpine Linux)",
                details=details,
                remediation=(
                    "Precompiled tree-sitter grammar wheels are not distributed for musl libc. "
                    "Use a glibc-based container (Debian 12+, Ubuntu 22.04+) or compile "
                    "tree-sitter grammars with a C toolchain."
                ),
            )

        lib, ver = platform.libc_ver()
        if lib == "glibc" and ver:
            details.append(f"C library: glibc {ver}")
            try:
                parts = tuple(int(p) for p in ver.split(".")[:2] if p.isdigit())
                if len(parts) >= 2 and parts < (2, 34):
                    return CheckResult(
                        "Platform Support",
                        "fail",
                        f"glibc {ver} is below minimum requirement (>= 2.34)",
                        details=details
                        + [
                            "tree-sitter precompiled grammar wheels require glibc >= 2.34.",
                            "RHEL 8, CentOS 8, Amazon Linux 2, Debian 11, and Ubuntu 20.04 ship glibc < 2.34.",
                        ],
                        remediation=(
                            "Upgrade glibc to >= 2.34, run inside a newer container "
                            "(e.g., Ubuntu 22.04+, Debian 12+, RHEL 9+), or build tree-sitter "
                            "grammars from source."
                        ),
                    )
            except (ValueError, TypeError):
                pass
            return CheckResult(
                "Platform Support",
                "ok",
                f"Linux with glibc {ver} (>= 2.34)",
                details=details,
            )
        details.append(f"C library: {lib or 'unknown'} {ver or ''}".strip())
        return CheckResult(
            "Platform Support",
            "ok",
            f"Linux ({lib or 'unknown libc'})",
            details=details,
        )

    return CheckResult(
        "Platform Support",
        "ok",
        f"{system} is supported",
        details=details,
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
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            "Git CLI",
            "warn",
            f"git CLI not available: {exc}",
            remediation="Install Git to enable VCS discovery and co-change analysis.",
        )


def check_tree_sitter() -> CheckResult:
    """Check that grammars actually *load*, not merely that they are configured.

    This used to report `len(LANG_CFG)` grammars "configured" and return "ok".
    `LANG_CFG` is a dict literal in `parse.py`, so the count was a property of the
    source code and the check could not fail for the thing it was named after: with
    every grammar unloadable, doctor still said ok and `build` still exited 0 with an
    empty graph. `parser_for()` swallows LookupError/ValueError/ImportError/
    AttributeError and returns None, so "no grammar" and "nothing to parse" are
    indistinguishable downstream unless something actually asks for a parser.
    """
    try:
        from .parse import LANG_CFG, parser_for
    except ImportError as exc:
        return CheckResult(
            "Tree-sitter Grammars",
            "fail",
            f"tree-sitter grammars missing: {exc}",
            remediation="Ensure tree-sitter and language grammars are installed.",
        )

    configured = sorted(LANG_CFG.keys())
    loaded: list[str] = []
    failed: list[str] = []
    for lang in configured:
        try:
            ok = parser_for(lang) is not None
        except Exception:  # noqa: BLE001 - a broken grammar must read as failed, not crash doctor
            ok = False
        (loaded if ok else failed).append(lang)

    details = [f"loaded {len(loaded)}/{len(configured)}: {', '.join(loaded) or 'none'}"]
    if failed:
        details.append(f"failed to load: {', '.join(failed)}")

    if not loaded:
        return CheckResult(
            "Tree-sitter Grammars",
            "fail",
            f"no grammars could be loaded ({len(configured)} configured)",
            details=details,
            remediation=(
                "Every parse will yield an empty graph. Reinstall with grammars bundled: "
                "`pip install -U 'tree-sitter-language-pack>=0.7,<1.0'`. The 1.x line ships "
                "a loader and downloads grammars on first use, which fails in offline or "
                "egress-restricted environments."
            ),
        )
    if failed:
        return CheckResult(
            "Tree-sitter Grammars",
            "warn",
            f"{len(failed)} of {len(configured)} grammars could not be loaded",
            details=details,
            remediation=(
                "Files in the affected languages will contribute no symbols or edges. "
                "Reinstall `tree-sitter-language-pack` (>=0.7,<1.0 bundles all grammars)."
            ),
        )
    return CheckResult(
        "Tree-sitter Grammars",
        "ok",
        f"{len(loaded)} grammars loaded",
        details=details,
    )


def check_mcp_server() -> CheckResult:
    """Check that `repo2graph-mcp` could actually start, without launching it.

    An MCP client reports a server that fails its SDK preflight as "server failed
    to start" or simply shows no tools -- `server._require_sdk()` raises SystemExit
    with an accurate message, and the client swallows it. That makes a wrong or
    absent `mcp` SDK one of the setup failures users cannot diagnose from inside
    their editor, which is the case for answering it here.

    The version gate is imported from `mcp/server.py` rather than restated, so a
    doctor that says "ok" and a server that refuses to start cannot disagree.

    This deliberately does **not** read the client's own configuration
    (`~/.claude.json`, Cursor's `mcp.json`). Those live outside the repository,
    belong to other tools, and `doctor --json` is explicitly meant to be safe to
    paste into a bug report -- see `.github/SECURITY.md`.
    """
    name = "MCP Server"
    try:
        from .mcp.server import SDK_SPEC, _sdk_major, _sdk_version
    except Exception as exc:  # noqa: BLE001 - a broken import must read as a check, not a crash
        return CheckResult(name, "fail", f"repo2graph's own MCP module failed to import: {exc}")

    launcher = shutil.which("repo2graph-mcp")
    details = [f"required SDK: {SDK_SPEC}"]
    details.append(
        f"`repo2graph-mcp` on PATH: {launcher}"
        if launcher
        else "`repo2graph-mcp` is not on PATH (clients that invoke it via `uvx` do not need it)"
    )

    try:
        import mcp as mcp_sdk
    except ImportError:
        return CheckResult(
            name,
            "warn",
            "the mcp SDK is not installed, so repo2graph-mcp cannot start",
            details=details,
            remediation=(
                'Only the MCP server needs it: `pip install "repo2graph[mcp]"`. '
                "The CLI and the GitHub Action work without it."
            ),
        )

    installed = _sdk_version(mcp_sdk)
    details.insert(1, f"installed SDK: mcp {installed}")

    try:
        from mcp.server import Server as _Server  # noqa: F401
    except ImportError as exc:
        return CheckResult(
            name,
            "fail",
            f"the installed mcp SDK ({installed}) is unusable: {exc}",
            details=details,
            remediation=f'pip install "{SDK_SPEC}"',
        )

    major = _sdk_major(installed)
    if major is not None and major < 2:
        return CheckResult(
            name,
            "fail",
            f"mcp {installed} is a 1.x release; repo2graph-mcp requires {SDK_SPEC}",
            details=details,
            remediation=(
                f'pip install "{SDK_SPEC}" -- 1.x hangs on the first tool call '
                f"under repo2graph-mcp (#407), which an MCP client shows as a "
                f"server that never responds."
            ),
        )

    return CheckResult(
        name,
        "ok",
        f"mcp {installed} satisfies {SDK_SPEC}",
        details=details,
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
    except OSError as exc:
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
    except (OSError, ValueError) as exc:
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
    report.checks.append(check_platform_support())
    report.checks.append(check_platform_encoding())
    report.checks.append(check_git(target_path))
    report.checks.append(check_tree_sitter())
    report.checks.append(check_mcp_server())
    report.checks.append(check_permissions(target_path))
    report.checks.append(check_artifact_integrity(target_path))
    return report

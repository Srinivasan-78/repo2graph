"""System diagnostics and health checker for repo2graph.

Inspects Python version, platform encoding, git CLI, tree-sitter grammars,
and directory write permissions.
"""

from __future__ import annotations

import json
import locale
import os
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


def _find_index_dir(path: Path | str) -> Path | None:
    p = Path(path).resolve()
    dot_r2g = p if p.name == ".r2g" else p / ".r2g"
    if dot_r2g.is_dir():
        agent_dir = dot_r2g / "agent" if (dot_r2g / "agent").is_dir() else dot_r2g
        if (
            (dot_r2g / "agent").is_dir()
            or (agent_dir / "manifest.json").exists()
            or (agent_dir / "chunks.jsonl").exists()
        ):
            return dot_r2g
    if (p / "manifest.json").exists() or (p / "chunks.jsonl").exists():
        return p
    if (p / "agent" / "manifest.json").exists() or (p / "agent" / "chunks.jsonl").exists():
        return p
    return None


def _repo_root_for(path: Path, idx_dir: Path | None) -> Path:
    if idx_dir is None:
        return path
    if idx_dir == path:
        from .status import stored_source_root
        agent = idx_dir / "agent" if (idx_dir / "agent").exists() else idx_dir
        return stored_source_root(agent) or path.parent
    return path


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


def check_index_freshness(path: Path | str) -> CheckResult:
    """Check whether the index is up-to-date with repository source files."""
    p = Path(path).resolve()
    idx_dir = _find_index_dir(p)
    if idx_dir is None:
        return CheckResult(
            name="Index Freshness",
            status="ok",
            summary="no index found (nothing to be stale)",
            details=[f"Build one with: repo2graph build {p} -o {p}/.r2g"],
        )

    from .status import (
        compute_freshness,
        remote_freshness,
        stored_remote_source,
        stored_source_root,
    )

    agent = idx_dir / "agent" if (idx_dir / "agent").exists() else idx_dir
    remote = (
        stored_remote_source(agent)
        if idx_dir == p and stored_source_root(agent) is None
        else None
    )
    if remote:
        fresh = remote_freshness(remote, agent, idx_dir)
        return CheckResult(
            name="Index Freshness",
            status="ok",
            summary="freshness cannot be checked for a remote build (see notes)",
            details=[f"Index: {idx_dir}", f"Source: {remote}"]
            + [note[0].upper() + note[1:] for note in fresh.notes],
        )

    repo = _repo_root_for(p, idx_dir)
    fresh = compute_freshness(repo, idx_dir, agent)

    details = [f"Index: {idx_dir}", f"Source tree: {repo}"]
    for label, items in (
        ("added", fresh.added),
        ("removed", fresh.removed),
        ("modified", fresh.modified),
    ):
        if items:
            details.append(
                f"{len(items)} {label}: " + ", ".join(items[:5]) + ("..." if len(items) > 5 else "")
            )
    details.extend(note[0].upper() + note[1:] for note in fresh.notes)

    if fresh.status == "current":
        return CheckResult(
            name="Index Freshness",
            status="ok",
            summary="index is up to date with the source tree",
            details=details,
        )

    if fresh.status == "unknown":
        return CheckResult(
            name="Index Freshness",
            status="ok",
            summary="freshness could not be established (see notes)",
            details=details,
        )

    return CheckResult(
        name="Index Freshness",
        status="warn",
        summary="index is out of date: " + ", ".join(fresh.reasons),
        details=details,
        remediation="repo2graph build --incremental",
    )


def check_parsers(path: Path | str) -> CheckResult:
    """Check for source parse errors in the repository."""
    target = Path(path).resolve()
    errors: list[str] = []
    if target.is_dir():
        for py_file in target.glob("**/*.py"):
            if ".r2g" in py_file.parts or ".git" in py_file.parts:
                continue
            try:
                import ast
                ast.parse(py_file.read_text(encoding="utf-8", errors="replace"), filename=str(py_file))
            except SyntaxError as e:
                errors.append(f"{py_file.name}: {e}")
    if errors:
        return CheckResult(
            "Parser Health",
            "warn",
            f"{len(errors)} parse errors detected",
            details=errors,
            remediation="Install complete grammars: pip install tree-sitter-language-pack",
        )
    return CheckResult("Parser Health", "ok", "no syntax errors detected")


def check_vectors(path: Path | str) -> CheckResult:
    """Verify vector index metadata and chunk synchronisation."""
    p = Path(path).resolve()
    idx_dir = _find_index_dir(p) or p
    agent_dir = idx_dir / "agent" if (idx_dir / "agent").exists() else idx_dir
    vec_file = agent_dir / "vectors.npy"
    meta_file = agent_dir / "vectors.meta.json"
    chunks_file = agent_dir / "chunks.jsonl"

    if not vec_file.exists() and not meta_file.exists():
        return CheckResult("Dense Vector Integrity", "ok", "no dense vector index present")

    if vec_file.exists() != meta_file.exists():
        missing = "vectors.meta.json" if not meta_file.exists() else "vectors.npy"
        return CheckResult(
            "Dense Vector Integrity",
            "warn",
            "missing companion vector metadata or embedding file",
            details=[f"Missing file: {missing}"],
            remediation="Re-embed the index: repo2graph embed -o <out> --force",
        )

    try:
        from .integrity import MAX_METADATA_BYTES, read_bounded
        meta_raw = read_bounded(meta_file, MAX_METADATA_BYTES, what="vectors.meta.json")
        meta = json.loads(meta_raw.decode("utf-8", "replace"))
        expected_chunks = len(meta.get("chunk_ids", []))

        line_count = 0
        from .integrity import MAX_JSONL_LINE_BYTES
        with open(chunks_file, "rb") as f:
            for line in f:
                if len(line) > MAX_JSONL_LINE_BYTES:
                    raise ValueError(f"Line exceeds {MAX_JSONL_LINE_BYTES} bytes")
                if line.strip():
                    line_count += 1

        if expected_chunks != line_count:
            return CheckResult(
                "Dense Vector Integrity",
                "warn",
                f"vectors out of sync with chunks (expected {expected_chunks}, found {line_count})",
                remediation="Recompute embeddings with: repo2graph embed",
            )
        return CheckResult("Dense Vector Integrity", "ok", f"{line_count} vectors synchronized")
    except Exception as exc:
        return CheckResult("Dense Vector Integrity", "warn", f"vector check failed: {exc}")


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
    report.checks.append(check_index_freshness(target_path))
    return report

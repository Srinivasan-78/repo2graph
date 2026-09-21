"""System diagnostics and health checker for repo2graph.

Diagnoses environment, parser/language pack availability, MCP SDK version,
Git configuration, directory permissions, platform encoding, and artifact integrity.
Never discloses sensitive credentials or raw secret values.
"""

from __future__ import annotations

import importlib.metadata
import json
import locale
import os
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
    """Aggregated health report across all probes."""

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
            "has_warnings": self.has_warnings,
            "checks": [c.to_dict() for c in self.checks],
        }

    def format_text(self) -> str:
        lines: list[str] = []
        lines.append("repo2graph doctor: system and environment diagnostic report")
        lines.append("=" * 60)

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
            lines.append("All required checks passed, but warnings were detected.")
        else:
            lines.append("One or more critical checks failed. See remediation steps above.")

        return "\n".join(lines)


def check_python() -> CheckResult:
    v = sys.version_info
    ver_str = f"{v[0]}.{v[1]}.{v[2]}"
    if v < (3, 10):
        return CheckResult(
            name="Python Version",
            status="fail",
            summary=f"Python {ver_str} is unsupported",
            details=[f"Executable: {sys.executable}", "repo2graph requires Python >= 3.10"],
            remediation="Upgrade to Python 3.10 or newer.",
        )
    return CheckResult(
        name="Python Version",
        status="ok",
        summary=f"Python {ver_str} (>= 3.10)",
        details=[f"Executable: {sys.executable}"],
    )


def check_package() -> CheckResult:
    try:
        from . import __version__

        code_ver = __version__
    except Exception:
        code_ver = "unknown"

    dist_ver: str | None = None
    try:
        dist_ver = importlib.metadata.version("repo2graph")
    except Exception:
        pass

    details = [f"Module version: {code_ver}"]
    if dist_ver and dist_ver != code_ver:
        details.append(f"Installed dist-info: {dist_ver}")

    return CheckResult(
        name="Package Version",
        status="ok",
        summary=f"repo2graph v{code_ver}",
        details=details,
    )


def check_tree_sitter() -> CheckResult:
    details: list[str] = []
    ts_ver: str | None = None
    try:
        import tree_sitter

        ts_ver = getattr(tree_sitter, "__version__", None)
        if not ts_ver:
            try:
                ts_ver = importlib.metadata.version("tree-sitter")
            except Exception:
                ts_ver = "installed"
        details.append(f"tree-sitter: {ts_ver}")
    except ImportError:
        return CheckResult(
            name="Tree-Sitter & Languages",
            status="fail",
            summary="tree-sitter library is missing",
            details=["tree-sitter could not be imported"],
            remediation="Run: pip install tree-sitter>=0.23",
        )

    try:
        import tree_sitter_language_pack as tslp

        lp_ver = getattr(tslp, "__version__", None) or importlib.metadata.version(
            "tree-sitter-language-pack"
        )
        details.append(f"tree-sitter-language-pack: {lp_ver}")
    except Exception:
        return CheckResult(
            name="Tree-Sitter & Languages",
            status="fail",
            summary="tree-sitter-language-pack is missing",
            details=details,
            remediation="Run: pip install tree-sitter-language-pack>=0.7",
        )

    from .parse import LANG_CFG, _get_parser

    if _get_parser is None:
        return CheckResult(
            name="Tree-Sitter & Languages",
            status="fail",
            summary="_get_parser helper is unavailable",
            details=details,
            remediation="Reinstall tree-sitter and tree-sitter-language-pack.",
        )

    missing: list[str] = []
    available: list[str] = []
    for lang in sorted(LANG_CFG.keys()):
        try:
            parser = _get_parser(lang)
            if parser is not None:
                available.append(lang)
            else:
                missing.append(lang)
        except Exception:
            missing.append(lang)

    details.append(f"Active grammars ({len(available)}): {', '.join(available)}")
    if missing:
        details.append(f"Unavailable grammars ({len(missing)}): {', '.join(missing)}")
        return CheckResult(
            name="Tree-Sitter & Languages",
            status="warn",
            summary=f"{len(available)} grammars ready, {len(missing)} missing",
            details=details,
            remediation="Update tree-sitter-language-pack: pip install --upgrade tree-sitter-language-pack",
        )

    return CheckResult(
        name="Tree-Sitter & Languages",
        status="ok",
        summary=f"All {len(available)} language grammars ready",
        details=details,
    )


def check_git(repo_dir: Path) -> CheckResult:
    details: list[str] = []
    git_bin = "git"
    try:
        proc = subprocess.run(
            [git_bin, "-c", "core.quotepath=false", "--version"],
            capture_output=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            return CheckResult(
                name="Git Integration",
                status="warn",
                summary="git command returned non-zero exit code",
                details=[f"Exit code: {proc.returncode}"],
                remediation="Ensure Git is installed and working in PATH.",
            )
        git_ver = proc.stdout.decode("utf8", "surrogateescape").strip()
        details.append(git_ver)
    except Exception as exc:
        return CheckResult(
            name="Git Integration",
            status="warn",
            summary="Git is not available in PATH",
            details=[str(exc)],
            remediation="Install Git so repo2graph can discover files with git ls-files and compute CO_CHANGE edges.",
        )

    # Check if target directory is inside a git worktree
    try:
        proc = subprocess.run(
            [git_bin, "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        is_git = proc.returncode == 0 and proc.stdout.strip() == b"true"
        if is_git:
            rev_proc = subprocess.run(
                [git_bin, "-C", str(repo_dir), "rev-parse", "HEAD"],
                capture_output=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
            head_commit = rev_proc.stdout.decode("utf8", "surrogateescape").strip()[:10]
            details.append(f"Git repository detected (HEAD: {head_commit})")
        else:
            details.append("Directory is not a Git repository (os.walk fallback will be used)")
    except Exception:
        details.append("Directory is not a Git repository (os.walk fallback will be used)")

    return CheckResult(
        name="Git Integration",
        status="ok",
        summary="Git is functional",
        details=details,
    )


def check_permissions(target_path: Path) -> CheckResult:
    probe_dir = target_path if target_path.is_dir() else target_path.parent
    if not probe_dir.exists():
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
            created = True
        except OSError as exc:
            return CheckResult(
                name="Directory Permissions",
                status="fail",
                summary=f"Cannot create directory: {probe_dir}",
                details=[str(exc)],
                remediation=f"Ensure write permissions to {probe_dir.parent}.",
            )
    else:
        created = False

    test_file = probe_dir / f".r2g_doctor_probe_{os.getpid()}.tmp"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        if created:
            try:
                probe_dir.rmdir()
            except OSError:
                pass
        return CheckResult(
            name="Directory Permissions",
            status="ok",
            summary=f"Write permissions verified for {probe_dir}",
        )
    except OSError as exc:
        return CheckResult(
            name="Directory Permissions",
            status="fail",
            summary=f"Write permission test failed for {probe_dir}",
            details=[str(exc)],
            remediation=f"Grant write and execute permissions to {probe_dir}.",
        )


def _find_index_dir(path: Path) -> Path | None:
    """Locate an existing .r2g or index directory if present."""
    if (
        (path / "agent").is_dir()
        or (path / "manifest.json").exists()
        or (path / "chunks.jsonl").exists()
    ):
        return path
    if (path / ".r2g" / "agent").is_dir() or (path / ".r2g" / "manifest.json").exists():
        return path / ".r2g"
    if path.name == "agent" and path.is_dir():
        return path.parent
    return None


def check_artifact_integrity(path: Path) -> CheckResult:
    idx_dir = _find_index_dir(path)
    if idx_dir is None:
        return CheckResult(
            name="Artifact Integrity",
            status="ok",
            summary="No existing index found at path (ready for clean build)",
            details=[f"Target path: {path}"],
        )

    agent_dir = idx_dir / "agent" if (idx_dir / "agent").exists() else idx_dir
    manifest_file = agent_dir / "manifest.json"
    chunks_file = agent_dir / "chunks.jsonl"
    nodes_file = agent_dir / "nodes.jsonl"
    edges_file = agent_dir / "edges.jsonl"

    details = [f"Index located at: {idx_dir}"]
    issues: list[str] = []

    # 1. Manifest
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
            details.append(
                f"Manifest format: {manifest_data.get('format', 'unknown')}, repo: {manifest_data.get('repo', 'unknown')}"
            )
        except Exception as exc:
            issues.append(f"Corrupt manifest.json: {exc}")
    else:
        issues.append("Missing agent/manifest.json")

    # 2. Chunks
    chunk_count = 0
    if chunks_file.exists():
        try:
            with open(chunks_file, "r", encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    chunk_count += 1
                    chunk = json.loads(line)
                    if "id" not in chunk or "text" not in chunk:
                        issues.append(f"chunks.jsonl line {line_idx} missing 'id' or 'text'")
                        break
            details.append(f"Chunks verified: {chunk_count}")
        except Exception as exc:
            issues.append(f"Corrupt chunks.jsonl: {exc}")
    else:
        issues.append("Missing agent/chunks.jsonl")

    # 3. Nodes & Edges
    if nodes_file.exists():
        try:
            with open(nodes_file, "r", encoding="utf-8", errors="replace") as f:
                node_count = sum(1 for line in f if line.strip())
            details.append(f"Nodes verified: {node_count}")
        except Exception as exc:
            issues.append(f"Corrupt nodes.jsonl: {exc}")

    if edges_file.exists():
        try:
            with open(edges_file, "r", encoding="utf-8", errors="replace") as f:
                edge_count = sum(1 for line in f if line.strip())
            details.append(f"Edges verified: {edge_count}")
        except Exception as exc:
            issues.append(f"Corrupt edges.jsonl: {exc}")

    if issues:
        return CheckResult(
            name="Artifact Integrity",
            status="fail",
            summary=f"Integrity check failed with {len(issues)} issue(s)",
            details=details + issues,
            remediation="Run a clean build to recreate corrupted artifacts: repo2graph build <repo> -o <out>",
        )

    return CheckResult(
        name="Artifact Integrity",
        status="ok",
        summary="All index artifacts intact and valid",
        details=details,
    )


def check_vectors(path: Path) -> CheckResult:
    idx_dir = _find_index_dir(path)
    if idx_dir is None:
        return CheckResult(
            name="Dense Vector Integrity",
            status="ok",
            summary="No index found (dense vector check skipped)",
        )

    agent_dir = idx_dir / "agent" if (idx_dir / "agent").exists() else idx_dir
    vec_file = agent_dir / "vectors.npy"
    meta_file = agent_dir / "vectors.meta.json"
    chunks_file = agent_dir / "chunks.jsonl"

    if not vec_file.exists() and not meta_file.exists():
        return CheckResult(
            name="Dense Vector Integrity",
            status="ok",
            summary="Dense vectors not present (lexical BM25 index active)",
            details=["Run `repo2graph embed` if dense hybrid search is desired."],
        )

    details = []
    if vec_file.exists() != meta_file.exists():
        missing = "vectors.meta.json" if not meta_file.exists() else "vectors.npy"
        return CheckResult(
            name="Dense Vector Integrity",
            status="warn",
            summary="Incomplete vector index: missing companion file",
            details=[f"Missing file: {missing}"],
            remediation="Re-embed the index: repo2graph embed -o <out> --force",
        )

    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        model_id = meta.get("model_id", "unknown")
        dim = meta.get("dim", 0)
        chunk_ids = meta.get("chunk_ids", [])
        details.append(f"Model: {model_id}, Dimension: {dim}, Embedded chunks: {len(chunk_ids)}")

        # Check correspondence with chunks.jsonl
        if chunks_file.exists():
            with open(chunks_file, "r", encoding="utf-8", errors="replace") as f:
                actual_chunks = sum(1 for line in f if line.strip())
            if actual_chunks != len(chunk_ids):
                details.append(
                    f"Warning: chunks.jsonl has {actual_chunks} chunks but vectors has {len(chunk_ids)}"
                )
                return CheckResult(
                    name="Dense Vector Integrity",
                    status="warn",
                    summary="Vector count out of sync with chunks.jsonl",
                    details=details,
                    remediation="Recompute vectors: repo2graph embed -o <out> --force",
                )

        return CheckResult(
            name="Dense Vector Integrity",
            status="ok",
            summary="Dense vector index is consistent and valid",
            details=details,
        )
    except Exception as exc:
        return CheckResult(
            name="Dense Vector Integrity",
            status="warn",
            summary=f"Failed to parse vector metadata: {exc}",
            details=[str(exc)],
            remediation="Re-embed the index: repo2graph embed -o <out> --force",
        )


def check_mcp_sdk() -> CheckResult:
    details: list[str] = []
    try:
        import mcp

        mcp_ver = getattr(mcp, "__version__", None) or importlib.metadata.version("mcp")
        details.append(f"Installed mcp version: {mcp_ver}")
        return CheckResult(
            name="MCP SDK Compatibility",
            status="ok",
            summary=f"MCP SDK v{mcp_ver} installed",
            details=details,
        )
    except ImportError:
        return CheckResult(
            name="MCP SDK Compatibility",
            status="ok",
            summary="MCP SDK not installed (CLI and RAG functional)",
            details=["Install with `pip install repo2graph[mcp]` if MCP server is needed."],
        )


def check_provider_env() -> CheckResult:
    """Verify LLM provider environment variables without exposing sensitive values."""
    providers = [
        ("Gemini", "GEMINI_API_KEY"),
        ("OpenAI", "OPENAI_API_KEY"),
        ("Anthropic", "ANTHROPIC_API_KEY"),
        ("Ollama", "OLLAMA_HOST"),
    ]
    details: list[str] = []
    configured_count = 0

    for name, var in providers:
        val = os.environ.get(var)
        if val and val.strip():
            configured_count += 1
            masked = (
                f"{val[:3]}...{val[-2:]}" if len(val) >= 8 and var != "OLLAMA_HOST" else "(set)"
            )
            details.append(f"{name} ({var}): Configured [{masked}, length: {len(val)}]")
        else:
            details.append(f"{name} ({var}): Not set")

    summary = (
        f"{configured_count} provider(s) configured"
        if configured_count > 0
        else "No providers configured (offline-only)"
    )
    return CheckResult(
        name="LLM Provider Configuration",
        status="ok",
        summary=summary,
        details=details,
    )


def check_platform_encoding() -> CheckResult:
    stdout_enc = getattr(sys.stdout, "encoding", None) or "unknown"
    fs_enc = sys.getfilesystemencoding()
    pref_enc = locale.getpreferredencoding()

    details = [
        f"stdout encoding: {stdout_enc}",
        f"Filesystem encoding: {fs_enc}",
        f"Locale preferred encoding: {pref_enc}",
        f"OS / platform: {sys.platform}",
    ]

    status = "ok"
    summary = f"Platform encodings probed ({stdout_enc} / {fs_enc})"
    remediation = None

    if sys.platform == "win32" and stdout_enc.lower() in ("cp1252", "ascii"):
        details.append(
            "Note: Windows cp1252 stdout detected. repo2graph safe encoding emitter active."
        )

    return CheckResult(
        name="Platform & Encoding",
        status=status,
        summary=summary,
        details=details,
        remediation=remediation,
    )


def run_doctor(path: str | Path = ".") -> DoctorReport:
    """Execute all diagnostic checks against the specified path."""
    target_path = Path(path).resolve()

    report = DoctorReport()
    report.checks.append(check_python())
    report.checks.append(check_package())
    report.checks.append(check_tree_sitter())
    report.checks.append(check_git(target_path))
    report.checks.append(check_permissions(target_path))
    report.checks.append(check_artifact_integrity(target_path))
    report.checks.append(check_vectors(target_path))
    report.checks.append(check_mcp_sdk())
    report.checks.append(check_provider_env())
    report.checks.append(check_platform_encoding())

    return report

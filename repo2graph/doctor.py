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
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .status import MAX_FRESHNESS_FILES as _status_freshness_bound


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
        # Usually an editable install shadowed by a second copy on
        # sys.path, or a stale dist-info left by a partial upgrade -- either
        # way, `import repo2graph` and `pip show repo2graph` disagree about
        # what's running, which is worth a warning rather than a silent OK.
        return CheckResult(
            name="Package Version",
            status="warn",
            summary=f"repo2graph module v{code_ver} != installed dist-info v{dist_ver}",
            details=details,
            remediation="Reinstall to resync: pip install --force-reinstall -e .",
        )

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

    # Walk up to the closest ancestor that already exists, so the cleanup
    # below can rmdir() the *whole* chain mkdir(parents=True) creates -- not
    # just the leaf. A doctor run against a nested, not-yet-created path
    # (e.g. `repo2graph doctor a/b/c`) used to leave `a/` and `a/b/` behind
    # forever; this is a diagnostic probe, it should leave no trace.
    created_top: Path | None = None
    ancestor = probe_dir
    while not ancestor.exists():
        created_top = ancestor
        parent = ancestor.parent
        if parent == ancestor:
            break
        ancestor = parent

    if created_top is not None:
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return CheckResult(
                name="Directory Permissions",
                status="fail",
                summary=f"Cannot create directory: {probe_dir}",
                details=[str(exc)],
                remediation=f"Ensure write permissions to {probe_dir.parent}.",
            )

    def _cleanup_created_chain() -> None:
        if created_top is None:
            return
        d = probe_dir
        while True:
            try:
                d.rmdir()
            except OSError:
                return
            if d == created_top:
                return
            d = d.parent

    test_file = probe_dir / f".r2g_doctor_probe_{os.getpid()}.tmp"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        _cleanup_created_chain()
        return CheckResult(
            name="Directory Permissions",
            status="ok",
            summary=f"Write permissions verified for {probe_dir}",
        )
    except OSError as exc:
        _cleanup_created_chain()
        return CheckResult(
            name="Directory Permissions",
            status="fail",
            summary=f"Write permission test failed for {probe_dir}",
            details=[str(exc)],
            remediation=f"Grant write and execute permissions to {probe_dir}.",
        )


def _is_repo2graph_manifest(manifest_file: Path) -> bool:
    """True only if `manifest_file` parses as a repo2graph manifest.json.

    A bare `agent/` directory or a `chunks.jsonl` are not proof of a
    repo2graph index -- both names collide with unrelated projects (agent
    frameworks, ML repos with their own chunk files). The manifest's
    "format" key, written by export.write_manifest as "repo2graph/1", is
    the only reliable signal. A missing or unparseable manifest means "not
    our index", not "corrupt index" -- check_artifact_integrity only
    reports corruption once this function has confirmed the directory is
    actually ours.
    """
    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    return isinstance(data, dict) and str(data.get("format", "")).startswith("repo2graph/")


def _find_index_dir(path: Path) -> Path | None:
    """Locate an existing repo2graph index directory if present.

    ".r2g" is repo2graph's own default -o name (cli.py) and collides with
    nothing else, so a bare agent/ or chunks.jsonl found there is already
    corroborating evidence -- including a corrupt manifest.json, which
    check_artifact_integrity should go on to report as broken. Anywhere
    else, "agent/" and "chunks.jsonl" are generic names that unrelated
    projects (agent frameworks, ML repos) use too, so only a manifest.json
    that actually parses as ours counts as proof.
    """
    # `path` may itself be the ".r2g" dir (the default -o target, or a
    # caller who already resolved it) or its parent -- treat either as the
    # strong-signal case.
    dot_r2g = path if path.name == ".r2g" else path / ".r2g"
    if dot_r2g.is_dir():
        agent_dir = dot_r2g / "agent" if (dot_r2g / "agent").is_dir() else dot_r2g
        if (
            (dot_r2g / "agent").is_dir()
            or (agent_dir / "manifest.json").exists()
            or (agent_dir / "chunks.jsonl").exists()
        ):
            return dot_r2g

    # Note: if `path` itself is an agent/ dir with its own manifest.json,
    # this already matches it -- agent_dir falls back to `path` when there's
    # no nested agent/ subdir -- so there's no separate `path.name ==
    # "agent"` case to add.
    agent_dir = path / "agent" if (path / "agent").is_dir() else path
    if _is_repo2graph_manifest(agent_dir / "manifest.json"):
        return path
    return None


# Every scan below is bounded. `doctor` is the documented way to inspect an
# index built somewhere else, so the artifact sizes are not this process's to
# trust -- and a diagnostic that takes longer than the build it is diagnosing
# is not a diagnostic anyone runs twice.
MAX_NODE_LINES = 400_000
MAX_GENERATED_CONTENT_SCANS = 300
GENERATED_CONTENT_SCAN_BYTES = 2048
# Re-exported, not redefined: `index-status` and this check share one freshness
# implementation (status.compute_freshness) and must therefore share its bound.
MAX_FRESHNESS_FILES = _status_freshness_bound


def _agent_dir(idx_dir: Path) -> Path:
    """The directory holding agent artifacts, whether or not it is nested.

    A caller may hand `doctor` either the `-o` output directory (artifacts
    live under `agent/`) or the `agent/` directory itself.
    """
    return idx_dir / "agent" if (idx_dir / "agent").is_dir() else idx_dir


def _read_stats(idx_dir: Path) -> dict[str, Any]:
    """stats.json as a dict, or {} when it is absent or unreadable."""
    try:
        from .integrity import MAX_METADATA_BYTES, read_bounded

        raw = read_bounded(
            _agent_dir(idx_dir) / "stats.json", MAX_METADATA_BYTES, what="stats.json"
        )
        data = json.loads(raw.decode("utf8", "replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _iter_file_nodes(idx_dir: Path) -> Iterator[dict[str, Any]]:
    """Stream the `type == "file"` records out of nodes.jsonl.

    newline="\\n" and a per-line json.loads rather than read_jsonl: the whole
    node list of a large repository does not need to be resident to answer
    "which of these paths look generated", and `json.dumps(ensure_ascii=False)`
    passes U+2028/U+2029 through verbatim -- universal-newline mode would cut
    those records in half. Same reasoning as query.read_jsonl.
    """
    nodes = _agent_dir(idx_dir) / "nodes.jsonl"
    if not nodes.exists():
        return
    try:
        fh = open(nodes, encoding="utf8", errors="surrogateescape", newline="\n")
    except OSError:
        return
    with fh:
        for n, line in enumerate(fh):
            if n >= MAX_NODE_LINES:
                return
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Corruption is check_artifact_integrity's finding to report,
                # not this one's; skip the line rather than raise out of a
                # probe that is only trying to classify paths.
                continue
            if isinstance(rec, dict) and rec.get("type") == "file":
                yield rec


def _repo_root_for(path: Path, idx_dir: Path | None) -> Path:
    """The source tree an index describes, given whatever path was passed in.

    `doctor <repo>` and `doctor <repo>/.r2g` are both documented, so the
    source root is either `path` itself or the index directory's parent.
    """
    if idx_dir is None:
        return path
    if idx_dir == path:
        return path.parent
    return path


def check_artifact_integrity(path: Path) -> CheckResult:
    idx_dir = _find_index_dir(path)
    if idx_dir is None:
        return CheckResult(
            name="Artifact Integrity",
            status="ok",
            summary="No existing index found at path (ready for clean build)",
            details=[f"Target path: {path}"],
        )

    try:
        from .integrity import verify_artifacts

        report = verify_artifacts(idx_dir)
    except Exception as exc:
        # If integrity module itself fails, fall back gracefully
        return CheckResult(
            name="Artifact Integrity",
            status="warn",
            summary=f"Integrity check could not complete: {exc}",
            details=[f"Index located at: {idx_dir}"],
            remediation="Verify the repo2graph installation is intact.",
        )

    details: list[str] = [f"Index located at: {idx_dir}"]

    # Build_id, version, and source revision fields (present in new-format manifests)
    if report.build_id:
        details.append(f"Build ID: {report.build_id}")
    if report.tool_version:
        details.append(f"Tool version: {report.tool_version}")
    if report.source_revision:
        rev = report.source_revision
        commit_info = rev.get("short_commit") or rev.get("commit", "")
        if commit_info:
            tag = rev.get("tag", "")
            branch = rev.get("branch", "")
            dirty = " (dirty)" if rev.get("dirty") else ""
            details.append(
                f"Source: {commit_info}"
                + (f" tag={tag}" if tag else "")
                + (f" branch={branch}" if branch else "")
                + dirty
            )
    if report.checked_files:
        details.append(f"Checksum-verified files: {report.checked_files}")

    if report.warnings:
        details.extend(report.warnings)

    if report.status == "valid":
        return CheckResult(
            name="Artifact Integrity",
            status="ok",
            summary="All index artifacts intact and valid",
            details=details,
        )

    if report.status in ("stale",):
        return CheckResult(
            name="Artifact Integrity",
            status="warn",
            summary=f"Index may be stale: {'; '.join(report.warnings[:2])}",
            details=details + report.errors,
            remediation="Re-run `repo2graph build` and `repo2graph embed` to refresh the index.",
        )

    # corrupt, partial, incompatible
    return CheckResult(
        name="Artifact Integrity",
        status="fail",
        summary=f"Integrity check failed ({report.status}): {'; '.join(report.errors[:2])}",
        details=details + report.errors,
        remediation="Run a clean build to recreate corrupted artifacts: repo2graph build <repo> -o <out>",
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
        from .integrity import MAX_METADATA_BYTES, read_bounded

        # `doctor` is the documented way to check an index built elsewhere, so
        # this sidecar's size is attacker-chosen; see integrity.read_bounded.
        meta = json.loads(
            read_bounded(meta_file, MAX_METADATA_BYTES, what="vectors.meta.json").decode(
                "utf8", "replace"
            )
        )
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
    except ImportError:
        return CheckResult(
            name="MCP SDK Compatibility",
            status="ok",
            summary="MCP SDK not installed (CLI and RAG functional)",
            details=["Install with `pip install repo2graph[mcp]` if MCP server is needed."],
        )
    except Exception as exc:
        return CheckResult(
            name="MCP SDK Compatibility",
            status="warn",
            summary="mcp package is present but failed to import",
            details=[str(exc)],
            remediation='Reinstall the SDK: pip install "repo2graph[mcp]"',
        )

    try:
        mcp_ver = getattr(mcp, "__version__", None) or importlib.metadata.version("mcp")
    except Exception:
        mcp_ver = "unknown"
    details.append(f"Installed mcp version: {mcp_ver}")

    # A successful `import mcp` is not evidence the SDK is usable: serve()
    # (mcp.py) needs `mcp.server.Server` specifically, and that import is
    # what actually gates whether repo2graph-mcp can start. Probe the same
    # thing rather than just echoing the version string.
    try:
        from .mcp import _require_sdk

        _require_sdk()
    except SystemExit as exc:
        return CheckResult(
            name="MCP SDK Compatibility",
            status="fail",
            summary=f"mcp SDK v{mcp_ver} is installed but not usable by repo2graph-mcp",
            details=details + [str(exc)],
            remediation='Install a supported SDK: pip install "repo2graph[mcp]"',
        )
    except Exception as exc:
        return CheckResult(
            name="MCP SDK Compatibility",
            status="warn",
            summary=f"Could not verify mcp SDK v{mcp_ver} compatibility",
            details=details + [str(exc)],
        )

    return CheckResult(
        name="MCP SDK Compatibility",
        status="ok",
        summary=f"MCP SDK v{mcp_ver} installed and usable",
        details=details,
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
            # Report presence only -- no tail characters, no length. Either
            # leaks real key entropy or fingerprints the key, and this
            # check's whole point (see docs/cli.md, CHANGELOG.md) is that it
            # never discloses secret values.
            details.append(f"{name} ({var}): Configured")
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


def check_uv() -> CheckResult:
    """uv/uvx availability -- the install path the README leads with.

    Absent uv is only a problem for the `uvx repo2graph` flow, so this is
    never a failure: a pip install is equally supported. It fails only when
    neither uv nor pip can be found, because then nothing can install the
    optional extras (`[mcp]`, `[rag]`) the other checks recommend.
    """
    details: list[str] = []
    uv = shutil.which("uv")
    uvx = shutil.which("uvx")

    for label, found in (("uv", uv), ("uvx", uvx)):
        details.append(f"{label}: {found}" if found else f"{label}: not on PATH")

    if uv:
        try:
            proc = subprocess.run(
                [uv, "--version"], capture_output=True, timeout=10, stdin=subprocess.DEVNULL
            )
            if proc.returncode == 0:
                details.append(proc.stdout.decode("utf8", "replace").strip())
        except Exception as exc:
            return CheckResult(
                name="uv / pip Availability",
                status="warn",
                summary="uv is on PATH but could not be executed",
                details=details + [str(exc)],
                remediation="Reinstall uv (https://docs.astral.sh/uv/) or use pip instead.",
            )

    has_pip = shutil.which("pip") is not None or _pip_importable()
    details.append(f"pip: {'available' if has_pip else 'not available'}")

    if uv or uvx:
        return CheckResult(
            name="uv / pip Availability",
            status="ok",
            summary="uv is available (uvx repo2graph ... works)",
            details=details,
        )
    if has_pip:
        return CheckResult(
            name="uv / pip Availability",
            status="ok",
            summary="uv not installed; pip is available",
            details=details
            + ["The `uvx repo2graph ...` one-liner needs uv; `pip install repo2graph` does not."],
        )
    return CheckResult(
        name="uv / pip Availability",
        status="fail",
        summary="neither uv nor pip is available",
        details=details,
        remediation=(
            "Install uv (https://docs.astral.sh/uv/getting-started/installation/) "
            "or repair this Python's pip: python -m ensurepip --upgrade"
        ),
    )


def _pip_importable() -> bool:
    try:
        import pip  # noqa: F401
    except Exception:
        return False
    return True


def mcp_client_config_paths(cwd: Path | None = None) -> list[tuple[str, Path]]:
    """(client label, config path) for every MCP client config location.

    The list is the documented set from docs/mcp.md plus the two project-scoped
    files, and it is returned whether or not the files exist -- the caller
    decides what an absent one means.
    """
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    home = Path.home()
    appdata = os.environ.get("APPDATA")

    candidates: list[tuple[str, Path]] = [
        ("Claude Code (user)", home / ".claude.json"),
        ("Claude Code (project)", cwd / ".mcp.json"),
        ("Cursor (user)", home / ".cursor" / "mcp.json"),
        ("Cursor (project)", cwd / ".cursor" / "mcp.json"),
        ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
    ]
    if sys.platform == "darwin":
        candidates.append(
            (
                "Claude Desktop",
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            )
        )
    elif sys.platform == "win32" and appdata:
        candidates.append(
            ("Claude Desktop", Path(appdata) / "Claude" / "claude_desktop_config.json")
        )
    else:
        candidates.append(
            ("Claude Desktop", home / ".config" / "Claude" / "claude_desktop_config.json")
        )
    return candidates


def _is_repo2graph_server(key: str, entry: dict[str, Any]) -> bool:
    if "repo2graph" in key.lower():
        return True
    blob = " ".join([str(entry.get("command", ""))] + [str(a) for a in entry.get("args", []) or []])
    return "repo2graph" in blob.lower()


def _inspect_server_entry(label: str, key: str, entry: dict[str, Any]) -> tuple[str, list[str]]:
    """Validate one mcpServers entry. Returns (status, messages).

    Never reads or echoes `entry["env"]` values -- an MCP client config is
    exactly where an API key lives, and this report is meant to be pasteable
    into a bug report.
    """
    msgs: list[str] = []
    status = "ok"
    command = str(entry.get("command") or "")
    args = [str(a) for a in (entry.get("args") or [])]

    if not command:
        return "fail", [f"{label}: server '{key}' has no `command`"]

    resolved = shutil.which(command)
    if resolved is None:
        status = "fail"
        msgs.append(f"{label}: server '{key}' command '{command}' is not on PATH")
    else:
        msgs.append(f"{label}: server '{key}' -> {command}")

    base = Path(command).name.lower()
    base = base[:-4] if base.endswith(".exe") else base
    if base in ("uvx", "uv"):
        # `uvx repo2graph-mcp` resolves the *package* `repo2graph-mcp`, which
        # does not exist, and even `uvx --from repo2graph repo2graph-mcp`
        # installs without the `mcp` extra -- the server then exits telling
        # the client to install an SDK, which reads to the user as "repo2graph
        # is broken". The `[mcp]` extra in --from is the whole fix.
        from_spec = ""
        if "--from" in args:
            i = args.index("--from")
            if i + 1 < len(args):
                from_spec = args[i + 1]
        if not from_spec:
            status = "fail"
            msgs.append(
                f"{label}: server '{key}' runs {base} without `--from`; "
                'use --from "repo2graph[mcp]"'
            )
        elif "[mcp]" not in from_spec.replace('"', "").replace("'", ""):
            status = "fail"
            msgs.append(
                f"{label}: server '{key}' has --from {from_spec!r} without the [mcp] extra; "
                'the server cannot start without it -- use --from "repo2graph[mcp]"'
            )

    # The last positional arg is the repository to serve. A path that is not
    # there yet is the other half of "the server starts and returns nothing".
    positional = [a for a in args if not a.startswith("-")]
    target = positional[-1] if positional else ""
    if target and target not in ("repo2graph-mcp", "repo2graph"):
        tp = Path(target).expanduser()
        if not tp.is_absolute():
            status = "warn" if status == "ok" else status
            msgs.append(
                f"{label}: server '{key}' target '{target}' is relative; MCP clients "
                "launch servers from an unspecified working directory -- use an absolute path"
            )
        elif not tp.exists():
            status = "warn" if status == "ok" else status
            msgs.append(f"{label}: server '{key}' target path does not exist: {target}")

    return status, msgs


def check_mcp_client_config(cwd: Path | None = None) -> CheckResult:
    """Find and validate repo2graph entries in the MCP clients' config files."""
    details: list[str] = []
    found_files = 0
    found_servers = 0
    worst = "ok"

    def _worsen(new: str) -> None:
        nonlocal worst
        order = {"ok": 0, "warn": 1, "fail": 2}
        if order[new] > order[worst]:
            worst = new

    for label, cfg in mcp_client_config_paths(cwd):
        if not cfg.is_file():
            continue
        found_files += 1
        try:
            from .integrity import MAX_METADATA_BYTES, read_bounded

            data = json.loads(
                read_bounded(cfg, MAX_METADATA_BYTES, what=cfg.name).decode("utf8", "replace")
            )
        except Exception as exc:
            # A trailing comma in claude_desktop_config.json is the single
            # most common MCP setup failure, and the client reports it as
            # "server failed to start" with no mention of JSON.
            _worsen("fail")
            details.append(f"{label}: {cfg} is not valid JSON ({exc})")
            continue

        servers = data.get("mcpServers") or data.get("servers") or {}
        if not isinstance(servers, dict):
            _worsen("warn")
            details.append(f"{label}: {cfg} has a non-object `mcpServers`")
            continue

        ours = {
            k: v for k, v in servers.items() if isinstance(v, dict) and _is_repo2graph_server(k, v)
        }
        if not ours:
            details.append(f"{label}: {cfg} (no repo2graph server configured)")
            continue
        for key, entry in ours.items():
            found_servers += 1
            status, msgs = _inspect_server_entry(label, key, entry)
            _worsen(status)
            details.extend(msgs)

    if found_files == 0:
        return CheckResult(
            name="MCP Client Configuration",
            status="ok",
            summary="no MCP client config file found (not needed for CLI use)",
            details=[f"Looked in: {cfg}" for _lbl, cfg in mcp_client_config_paths(cwd)],
        )

    # `worst` is settled before the no-servers shortcut below on purpose: a
    # config file that does not parse is a blocking problem whether or not it
    # would have held a repo2graph entry, and the client reports it only as
    # "server failed to start". Returning "none configured, all fine" there
    # would hide the actual cause.
    if worst == "fail":
        return CheckResult(
            name="MCP Client Configuration",
            status="fail",
            summary=(
                f"{found_servers} repo2graph MCP server entry/entries have blocking problems"
                if found_servers
                else f"an MCP client config file is unusable ({found_files} found)"
            ),
            details=details,
            remediation=(
                'The working form is: {"command": "uvx", "args": ["--from", "repo2graph[mcp]", '
                '"repo2graph-mcp", "/absolute/path/to/project"]} -- see docs/mcp.md'
            ),
        )

    if found_servers == 0:
        return CheckResult(
            name="MCP Client Configuration",
            status="ok",
            summary=f"{found_files} MCP client config(s) found, none configure repo2graph",
            details=details,
            remediation=(
                'Add it with: claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" '
                "repo2graph-mcp /absolute/path/to/project"
            ),
        )

    if worst == "warn":
        return CheckResult(
            name="MCP Client Configuration",
            status="warn",
            summary=f"{found_servers} repo2graph MCP server entry/entries need attention",
            details=details,
            remediation="Use an absolute, existing repository path as the last argument.",
        )
    return CheckResult(
        name="MCP Client Configuration",
        status="ok",
        summary=f"{found_servers} repo2graph MCP server entry/entries look correct",
        details=details,
    )


def check_index_freshness(path: Path) -> CheckResult:
    """Does the index still describe the tree it was built from?

    Three independent signals, cheapest first: the commit the index records
    versus the tree's current HEAD; the set of files discovered now versus the
    set recorded in index.state.json; and, for files whose mtime is newer than
    the manifest's, a real sha256 against the hash the build recorded. The
    mtime is only used to choose what to hash -- a file that was touched but
    not changed hashes equal and is reported as unchanged, so a checkout that
    rewrites every mtime does not produce a false "stale".
    """
    idx_dir = _find_index_dir(path)
    if idx_dir is None:
        return CheckResult(
            name="Index Freshness",
            status="ok",
            summary="no index found (nothing to be stale)",
            details=[f"Build one with: repo2graph build {path} -o {path}/.r2g"],
        )

    from .status import compute_freshness

    agent = _agent_dir(idx_dir)
    repo = _repo_root_for(path, idx_dir)
    fresh = compute_freshness(repo, idx_dir, agent)

    details: list[str] = [f"Index: {idx_dir}", f"Source tree: {repo}"]
    if fresh.indexed_commit and fresh.head_commit:
        if fresh.commit_moved:
            details.append(
                f"Indexed commit {fresh.indexed_commit[:10]} != "
                f"current HEAD {fresh.head_commit[:10]}"
            )
        else:
            details.append(f"Indexed commit matches HEAD ({fresh.head_commit[:10]})")
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
        details.append(f"{fresh.files_checked} indexed files all match the working tree")
        return CheckResult(
            name="Index Freshness",
            status="ok",
            summary="index is up to date with the source tree",
            details=details,
        )

    if fresh.status == "unknown":
        # Nothing could be checked -- an old index with no state file, a
        # non-git tree, or the scan bound. That is not evidence of staleness,
        # so it is not a warning; it is a gap the reader should know about.
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
        remediation=(
            "Refresh in place (only re-parses what changed): "
            "repo2graph build <repo> -o <out> --incremental"
        ),
    )


def check_parsers(path: Path) -> CheckResult:
    """Parse failures and unparsed file types in an index that already exists.

    check_tree_sitter answers "can this machine load the grammars"; this one
    answers "did they work on this repository" -- a grammar that loads fine
    still produces a symbol-free file node when the source uses syntax it does
    not model, and that file is then reachable by text but has no CALLS edges.
    """
    idx_dir = _find_index_dir(path)
    if idx_dir is None:
        return CheckResult(
            name="Parser Coverage",
            status="ok",
            summary="no index found (parser coverage is measured on a built index)",
        )

    stats = _read_stats(idx_dir)
    discovered = int(stats.get("files") or 0)
    parsed = int(stats.get("parsed") or 0)
    failed_files = int(stats.get("files_with_parse_errors") or 0)
    errors = int(stats.get("parse_errors") or 0)

    details = [f"{parsed} of {discovered} discovered files were parsed into symbols"]
    langs = stats.get("languages")
    if isinstance(langs, dict) and langs:
        details.append(
            "Languages: "
            + ", ".join(f"{k}={v}" for k, v in sorted(langs.items(), key=lambda kv: -kv[1]))
        )

    # Unparsed files are not automatically a problem -- docs and config are
    # indexed as text on purpose. Group them so the reader can tell "a README"
    # from "every .kt file in the repo silently fell through".
    unparsed: dict[str, int] = {}
    for node in _iter_file_nodes(idx_dir):
        if node.get("file_type") == "code":
            continue
        ext = Path(str(node.get("path") or "")).suffix.lower() or "(no extension)"
        unparsed[ext] = unparsed.get(ext, 0) + 1

    from .parse import CONFIG_EXT, DOC_EXT, EXT_LANG

    unsupported = {
        ext: n
        for ext, n in unparsed.items()
        if ext not in DOC_EXT and ext not in CONFIG_EXT and ext not in EXT_LANG
    }
    if unsupported:
        top = sorted(unsupported.items(), key=lambda kv: -kv[1])[:8]
        details.append(
            "Indexed as plain text, no grammar: " + ", ".join(f"{ext} x{n}" for ext, n in top)
        )

    if failed_files:
        details.append(f"{errors} syntax error(s) across {failed_files} file(s)")
        ratio = failed_files / parsed if parsed else 1.0
        return CheckResult(
            name="Parser Coverage",
            status="warn" if ratio < 0.25 else "fail",
            summary=f"{failed_files} file(s) produced parse errors",
            details=details,
            remediation=(
                "List them with `repo2graph stats -o <out>`; a file that fails to parse still "
                "gets a node and text chunks but no CALLS edges. If a whole language is "
                "affected, upgrade the grammars: pip install --upgrade tree-sitter-language-pack"
            ),
        )

    if unsupported and sum(unsupported.values()) > max(10, discovered * 0.2):
        return CheckResult(
            name="Parser Coverage",
            status="warn",
            summary=f"{sum(unsupported.values())} indexed files have no grammar",
            details=details,
            remediation=(
                "These files are searchable as text but contribute no symbols or edges. "
                "Exclude them if they are noise: repo2graph build <repo> --exclude '*.ext'"
            ),
        )

    return CheckResult(
        name="Parser Coverage",
        status="ok",
        summary=f"{parsed} file(s) parsed with no syntax errors",
        details=details,
    )


# stats.json key -> what the reader should understand it to mean. Same labels
# the human overview's "What was skipped" section uses (export._SKIP_STAT_LABELS);
# kept here as prose rather than imported because doctor adds a remediation per
# category that the overview has no room for.
_SKIP_EXPLANATIONS: tuple[tuple[str, str, str | None], ...] = (
    ("skipped_gitignore", "ignored by .gitignore", None),
    ("skipped_vendor", "in a vendor/build directory", "pass --include-vendor to index vendor/"),
    ("skipped_dotfile", "in a dot-directory", None),
    (
        "skipped_binary",
        "detected as binary",
        None,
    ),
    (
        "skipped_too_large",
        "over the size ceiling",
        "raise it with --max-file-mb, or slice them with --chunk-large-files",
    ),
    (
        "skipped_secret",
        "matched a secret/credential path rule",
        "inspect one with `repo2graph explain-path <path>`; --include-secrets overrides it",
    ),
)


def check_ignored_paths(path: Path) -> CheckResult:
    """What discovery left out, and whether that is more than the user expects.

    The failure this catches is silent: a repository whose source lives under
    a directory name that happens to be in DEFAULT_SKIP_DIRS (`build/`,
    `target/`, `dist/`) indexes cleanly and answers every question with
    nothing, because most of it was never discovered.
    """
    idx_dir = _find_index_dir(path)
    if idx_dir is None:
        return CheckResult(
            name="Ignored Paths",
            status="ok",
            summary="no index found (run a build to see what discovery skips)",
        )

    stats = _read_stats(idx_dir)
    indexed = int(stats.get("files") or 0)
    discovery = str(stats.get("discovery") or "unknown")
    details = [
        f"Discovery mode: {discovery} ({'git ls-files' if discovery == 'git' else 'os.walk'})"
    ]
    if discovery != "git":
        details.append(
            "Not a git checkout: .gitignore is not consulted, and tool caches not named in "
            "DEFAULT_SKIP_DIRS are indexed."
        )

    skipped_total = 0
    remediations: list[str] = []
    for key, label, fix in _SKIP_EXPLANATIONS:
        n = int(stats.get(key) or 0)
        if not n:
            continue
        skipped_total += n
        details.append(f"{n} file(s) {label}")
        if fix:
            remediations.append(fix)

    details.insert(1, f"{indexed} file(s) indexed, {skipped_total} skipped")

    if skipped_total == 0:
        return CheckResult(
            name="Ignored Paths",
            status="ok",
            summary=f"{indexed} file(s) indexed, nothing skipped",
            details=details,
        )

    considered = indexed + skipped_total
    # Two thirds is well past a normal .gitignore + node_modules and into
    # "the include/exclude globs are not doing what you think".
    if considered and skipped_total / considered > 0.66 and skipped_total > 50:
        return CheckResult(
            name="Ignored Paths",
            status="warn",
            summary=f"{skipped_total} of {considered} candidate files were skipped",
            details=details,
            remediation=(
                "Check one specific path with `repo2graph explain-path <path> -r <repo>`, "
                "which names the single rule that decided it. " + "; ".join(remediations)
            ).strip(),
        )

    return CheckResult(
        name="Ignored Paths",
        status="ok",
        summary=f"{indexed} file(s) indexed, {skipped_total} skipped by policy",
        details=details,
        remediation="; ".join(remediations) or None,
    )


# Markers every major code generator writes into its output's first lines.
# These have no glob equivalent -- they are the one signal `--exclude-group`
# cannot express -- so they stay here rather than in exclusions.GROUPS.
_GENERATED_CONTENT_MARKERS = (b"@generated", b"Code generated by", b"DO NOT EDIT")


def _generated_reason(rel: str) -> str | None:
    """Why `rel` looks machine-written, or None. Path-only; never reads bytes.

    Delegates to `exclusions.classify`, which is also what
    `build --exclude-group` expands from. When this check and that flag kept
    separate tables, the check could flag a shape the flag had no pattern for,
    and the remediation it printed then changed nothing.
    """
    from .exclusions import classify

    found = classify(rel)
    return found[1] if found else None


def check_generated_code(path: Path) -> CheckResult:
    """Vendored or machine-written files that made it *into* the index.

    The cost is not disk: generated code is usually the largest and most
    repetitive text in a repository, so it dominates BM25 and crowds
    hand-written code out of a bounded pack. Finding it is the difference
    between "the answers are vague" and a one-flag fix.
    """
    idx_dir = _find_index_dir(path)
    if idx_dir is None:
        return CheckResult(
            name="Generated / Vendored Code",
            status="ok",
            summary="no index found (nothing indexed yet)",
        )

    from .exclusions import classify

    repo = _repo_root_for(path, idx_dir)
    by_reason: dict[str, list[str]] = {}
    # The exclusion groups the flagged paths belong to. Naming a group is a
    # better remediation than reconstructing globs from a reason string: the
    # group is what classified the path in the first place, so
    # `--exclude-group <name>` provably covers it.
    groups_hit: set[str] = set()
    total_files = 0
    # Generated output is typically the biggest thing in the tree, so the
    # content probe spends its bounded budget on the largest files rather than
    # on whatever nodes.jsonl happens to list first.
    biggest: list[tuple[int, str]] = []

    for node in _iter_file_nodes(idx_dir):
        rel = str(node.get("path") or "")
        if not rel:
            continue
        total_files += 1
        found = classify(rel)
        if found:
            group, reason = found
            groups_hit.add(group)
            by_reason.setdefault(reason, []).append(rel)
            continue
        if node.get("file_type") == "code":
            biggest.append((int(node.get("size") or 0), rel))

    biggest.sort(reverse=True)
    for _size, rel in biggest[:MAX_GENERATED_CONTENT_SCANS]:
        try:
            with open(repo / rel, "rb") as fh:
                head = fh.read(GENERATED_CONTENT_SCAN_BYTES)
        except OSError:
            continue
        for marker in _GENERATED_CONTENT_MARKERS:
            if marker in head:
                by_reason.setdefault(f'header says "{marker.decode()}"', []).append(rel)
                break

    if not by_reason:
        return CheckResult(
            name="Generated / Vendored Code",
            status="ok",
            summary=f"no generated or vendored files among the {total_files} indexed",
            details=[
                f"Checked {total_files} indexed paths and read the head of "
                f"{min(len(biggest), MAX_GENERATED_CONTENT_SCANS)} of the largest source files."
            ],
        )

    flagged = sum(len(v) for v in by_reason.values())
    details: list[str] = []
    suggested: list[str] = []
    for reason, rels in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        shown = ", ".join(sorted(rels)[:4])
        details.append(f"{len(rels)} {reason}: {shown}" + ("..." if len(rels) > 4 else ""))
        if reason.startswith("header says"):
            # The one signal no glob expresses. There is no pattern to derive:
            # guessing one from the path's top directory would exclude a whole
            # source package because a single file in it is machine-written.
            # Name the files instead.
            suggested.extend(f"--exclude '{r}'" for r in sorted(rels)[:3])

    groups = " ".join(f"--exclude-group {g}" for g in sorted(groups_hit))
    fix = " ".join(filter(None, [groups, " ".join(sorted(set(suggested))[:3])]))

    return CheckResult(
        name="Generated / Vendored Code",
        status="warn",
        summary=f"{flagged} of {total_files} indexed files look generated or vendored",
        details=details,
        remediation=(
            "Rebuild without them so they stop competing for the retrieval budget: "
            f"repo2graph build <repo> -o <out> {fix}"
        ),
    )


def run_doctor(path: str | Path = ".") -> DoctorReport:
    """Execute all diagnostic checks against the specified path."""
    target_path = Path(path).resolve()

    report = DoctorReport()
    # Environment first, then the index, then the client wiring -- a reader
    # scanning top-down hits the cause before the symptom.
    report.checks.append(check_python())
    report.checks.append(check_package())
    report.checks.append(check_uv())
    report.checks.append(check_tree_sitter())
    report.checks.append(check_git(target_path))
    report.checks.append(check_permissions(target_path))
    report.checks.append(check_artifact_integrity(target_path))
    report.checks.append(check_index_freshness(target_path))
    report.checks.append(check_parsers(target_path))
    report.checks.append(check_ignored_paths(target_path))
    report.checks.append(check_generated_code(target_path))
    report.checks.append(check_vectors(target_path))
    report.checks.append(check_mcp_sdk())
    report.checks.append(check_mcp_client_config(target_path))
    report.checks.append(check_provider_env())
    report.checks.append(check_platform_encoding())

    return report

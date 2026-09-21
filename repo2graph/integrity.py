"""Artifact integrity verification, provenance capture, and output path hardening.

Implements:
- Output path safety guards (prevent clobbering root, repo root, source files, symlinks).
- Checksum calculation for generated artifacts.
- Repository provenance capture (commit, branch, tag, dirty status, sanitized origin URL).
- Comprehensive index verification (detecting valid, corrupt, stale, incompatible, partial).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .secrets import sanitize_url

# Standard repo2graph markers indicating a valid index directory
INDEX_MARKERS = ("agent", "human", "manifest.json", ".r2glock")


@dataclass
class IntegrityReport:
    """Detailed diagnostic report on an index's integrity."""

    status: str  # "valid", "corrupt", "stale", "incompatible", "partial"
    build_id: str | None = None
    tool_version: str | None = None
    schema_version: int | None = None
    source_revision: dict[str, Any] = field(default_factory=dict)
    checked_files: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.status == "valid"


def validate_outdir(
    outdir: str | Path,
    repo_root: str | Path | None = None,
    *,
    allow_symlink: bool = False,
    force: bool = False,
) -> Path:
    """Validate that `outdir` is safe to write index artifacts into.

    Refuses:
    - Filesystem root or top-level drive root.
    - Target repository root (to avoid clobbering source code).
    - An existing file (not directory).
    - Symlinked path unless `allow_symlink` is True.
    - Existing non-empty directory with non-repo2graph files unless `force` is True.

    Returns:
        Resolved absolute Path.
    """
    target = Path(outdir).resolve()

    # 1. Filesystem root check
    if target == target.parent or target == Path(target.anchor):
        raise ValueError(f"Refusing to use filesystem root as output directory: {target}")

    if sys.platform != "win32":
        if str(target) in ("/", "/etc", "/usr", "/bin", "/sbin", "/var", "/dev"):
            raise ValueError(f"Refusing to use system directory as output directory: {target}")
    else:
        norm_str = str(target).lower().replace("/", "\\")
        windir = os.environ.get("SystemRoot", r"C:\Windows").lower()
        progfiles = os.environ.get("ProgramFiles", r"C:\Program Files").lower()
        if (
            norm_str.startswith(windir)
            or norm_str.startswith(progfiles)
            or norm_str in ("c:\\", "c:")
        ):
            raise ValueError(f"Refusing to use system or drive root as output directory: {target}")

    # 2. Target repository root check
    if repo_root is not None:
        r_root = Path(repo_root).resolve()
        if target == r_root:
            raise ValueError(
                f"Refusing to use repository root as output directory: {target}. "
                "Output directory must be a dedicated subfolder (e.g. .r2g)."
            )

    # 3. Existing file check
    if target.exists() and not target.is_dir():
        raise ValueError(f"Output path exists and is not a directory: {target}")

    # 4. Symlink check
    if target.is_symlink() and not allow_symlink:
        raise ValueError(
            f"Output path is a symlink: {target}. Pass --allow-symlink-out to explicitly allow writing through symlinks."
        )

    # 5. Foreign non-empty directory check
    if target.is_dir() and any(target.iterdir()) and not force:
        has_marker = any((target / marker).exists() for marker in INDEX_MARKERS) or (
            target / "agent" / "manifest.json"
        ).exists()
        if not has_marker:
            raise ValueError(
                f"Output directory exists and contains non-repo2graph files: {target}. "
                "Pass --force to allow overwriting an existing foreign directory."
            )

    return target


def compute_file_checksum(path: Path | str) -> str:
    """Compute sha256 checksum of a file formatted as 'sha256:<hex>'."""
    p = Path(path)
    hasher = hashlib.sha256()
    with open(p, "rb") as fh:
        while chunk := fh.read(65536):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def get_source_provenance(root: str | Path) -> dict[str, Any]:
    """Capture Git provenance metadata safely for the repository at `root`."""
    root_path = Path(root)
    provenance: dict[str, Any] = {}

    def _run_git(args: list[str]) -> str | None:
        try:
            res = subprocess.run(
                ["git", "-c", "core.quotepath=false", "-C", str(root_path), *args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=5,
            )
            if res.returncode == 0:
                return res.stdout.decode("utf8", "surrogateescape").strip()
        except Exception:
            pass
        return None

    commit = _run_git(["rev-parse", "HEAD"])
    if not commit:
        return {}

    provenance["commit"] = commit
    short_commit = _run_git(["rev-parse", "--short", "HEAD"])
    if short_commit:
        provenance["short_commit"] = short_commit

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch and branch != "HEAD":
        provenance["branch"] = branch

    tag = _run_git(["describe", "--tags", "--exact-match"])
    if tag:
        provenance["tag"] = tag

    # Dirty status: check if uncommitted changes exist
    status_out = _run_git(["status", "--porcelain"])
    provenance["dirty"] = bool(status_out and status_out.strip())

    # Remote origin URL with credentials scrubbed
    remote_url = _run_git(["config", "--get", "remote.origin.url"])
    if remote_url:
        provenance["remote_url"] = sanitize_url(remote_url)

    return provenance


def verify_artifacts(outdir: str | Path) -> IntegrityReport:
    """Verify an index's integrity, schema, checksums, and vector correspondence."""
    out = Path(outdir).resolve()
    report = IntegrityReport(status="valid")

    if not out.is_dir():
        report.status = "partial"
        report.errors.append(f"Directory does not exist: {out}")
        return report

    agent_dir = out / "agent" if (out / "agent").is_dir() else out
    manifest_path = agent_dir / "manifest.json"

    if not manifest_path.exists():
        report.status = "partial"
        report.errors.append(f"Missing manifest file: {manifest_path}")
        return report

    # 1. Parse manifest
    try:
        manifest_text = manifest_path.read_text(encoding="utf8", errors="replace")
        manifest = json.loads(manifest_text)
    except Exception as exc:
        report.status = "corrupt"
        report.errors.append(f"Corrupt manifest.json: {exc}")
        return report

    report.build_id = manifest.get("build_id")
    report.tool_version = manifest.get("tool_version")
    report.schema_version = manifest.get("schema_version")
    report.source_revision = manifest.get("source_revision") or {}

    # Check format compatibility
    fmt = manifest.get("format")
    if fmt and not str(fmt).startswith("repo2graph/"):
        report.status = "incompatible"
        report.errors.append(f"Incompatible manifest format: {fmt}")
        return report

    # 2. Verify files and checksums
    checksums = manifest.get("checksums") or {}

    # Check existence of critical files
    for req in ("nodes.jsonl", "edges.jsonl", "chunks.jsonl"):
        p = agent_dir / req
        if not p.exists():
            report.status = "partial"
            report.errors.append(f"Missing critical artifact: {req}")

    # Check checksums for all declared files
    for rel_path, expected_hash in checksums.items():
        artifact_file = out / rel_path
        if not artifact_file.exists():
            report.status = "partial"
            report.errors.append(f"Missing artifact: {rel_path}")
            continue
        try:
            actual_hash = compute_file_checksum(artifact_file)
            report.checked_files += 1
            if actual_hash != expected_hash:
                report.status = "corrupt"
                report.errors.append(
                    f"Checksum mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}"
                )
        except OSError as exc:
            report.status = "corrupt"
            report.errors.append(f"Cannot read artifact {rel_path}: {exc}")

    # 3. Validate JSONL syntax of chunks and nodes
    chunks_path = agent_dir / "chunks.jsonl"
    chunk_ids: set[str] = set()
    chunk_text_hashes: dict[str, str] = {}
    if chunks_path.exists():
        try:
            with open(chunks_path, encoding="utf8", errors="surrogateescape", newline="\n") as fh:
                for lineno, line in enumerate(fh, 1):
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        c = json.loads(line_s)
                        cid = c.get("id")
                        if cid:
                            chunk_ids.add(cid)
                            text = c.get("text") or ""
                            chunk_text_hashes[cid] = hashlib.sha256(
                                text.encode("utf8", "surrogateescape")
                            ).hexdigest()
                    except json.JSONDecodeError as jde:
                        report.status = "corrupt"
                        report.errors.append(f"chunks.jsonl line {lineno} corrupt JSON: {jde}")
                        break
        except OSError as exc:
            report.status = "corrupt"
            report.errors.append(f"Error reading chunks.jsonl: {exc}")

    # 4. Verify vector correspondence if present
    vectors_npy = agent_dir / "vectors.npy"
    vectors_meta_file = agent_dir / "vectors.meta.json"
    if vectors_npy.exists():
        if not vectors_meta_file.exists():
            report.status = "partial"
            report.errors.append("vectors.npy present but vectors.meta.json is missing")
        else:
            try:
                vmeta = json.loads(vectors_meta_file.read_text(encoding="utf8"))
                v_build_id = vmeta.get("build_id")
                if report.build_id and v_build_id and v_build_id != report.build_id:
                    report.status = "stale"
                    report.warnings.append(
                        f"Vectors build_id ({v_build_id}) differs from manifest build_id ({report.build_id})"
                    )

                v_ids = vmeta.get("chunk_ids") or []
                v_hashes = vmeta.get("text_hashes") or []
                if len(v_ids) != len(v_hashes):
                    report.status = "corrupt"
                    report.errors.append("vectors.meta.json chunk_ids and text_hashes length mismatch")
                else:
                    mismatched_texts = 0
                    for cid, v_hash in zip(v_ids, v_hashes):
                        if cid in chunk_text_hashes and chunk_text_hashes[cid] != v_hash:
                            mismatched_texts += 1
                    if mismatched_texts > 0:
                        report.status = "stale"
                        report.warnings.append(
                            f"{mismatched_texts} chunks have text differing from vector text_hashes"
                        )
            except Exception as exc:
                report.status = "corrupt"
                report.errors.append(f"Corrupt vectors.meta.json: {exc}")

    return report

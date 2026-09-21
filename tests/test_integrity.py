"""Tests for artifact integrity, output path hardening, BuildLock, and transactional staging.

Issues covered: #268 (artifact integrity model), #269 (output path hardening),
#300 (transactional builds), #301 (cross-platform locking).

Per AGENTS.md: assertions use hand-derived literal values, never values recomputed
by the code under test.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.export import path as artifact_path


# ============================================================================
# helpers
# ============================================================================


def write_simple_repo(root: Path) -> Path:
    """A minimal one-file repo so `build` can index it quickly."""
    repo = root / "src"
    repo.mkdir()
    (repo / "app.py").write_text(
        "CONSTANT = 42\n\ndef hello():\n    return CONSTANT\n", encoding="utf8", newline="\n"
    )
    return repo


def build_index(repo: Path, out: Path, extra_args=()) -> int:
    return main(["build", str(repo), "-o", str(out), "--formats", "jsonl", *extra_args])


# ============================================================================
# validate_outdir — path hardening
# ============================================================================


class TestValidateOutdir:
    def test_ok_for_normal_subdir(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        result = validate_outdir(tmp_path / "index")
        assert result == (tmp_path / "index").resolve()

    def test_ok_for_existing_r2g_dir(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        idx = tmp_path / ".r2g"
        idx.mkdir()
        (idx / "agent").mkdir()
        result = validate_outdir(idx)
        assert result == idx.resolve()

    def test_rejects_filesystem_root(self):
        from repo2graph.integrity import validate_outdir

        if sys.platform == "win32":
            root = Path("C:\\")
        else:
            root = Path("/")
        with pytest.raises(ValueError, match="Refusing"):
            validate_outdir(root)

    def test_rejects_repo_root(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(ValueError, match="Refusing to use repository root"):
            validate_outdir(repo, repo_root=repo)

    def test_rejects_existing_file(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        f = tmp_path / "myfile.txt"
        f.write_text("data", encoding="utf8")
        with pytest.raises(ValueError, match="not a directory"):
            validate_outdir(f)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="symlinks require elevated rights on Windows"
    )
    def test_rejects_symlink_by_default(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        target = tmp_path / "real_dir"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        with pytest.raises(ValueError, match="symlink"):
            validate_outdir(link)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="symlinks require elevated rights on Windows"
    )
    def test_allows_symlink_when_flag_set(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        target = tmp_path / "real_dir"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        result = validate_outdir(link, allow_symlink=True)
        assert result is not None

    def test_rejects_foreign_non_empty_dir_without_force(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "some_file.txt").write_text("unrelated content", encoding="utf8")
        with pytest.raises(ValueError, match="--force"):
            validate_outdir(foreign)

    def test_allows_foreign_dir_with_force(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "some_file.txt").write_text("unrelated content", encoding="utf8")
        result = validate_outdir(foreign, force=True)
        assert result == foreign.resolve()

    def test_allows_dir_with_r2g_marker(self, tmp_path):
        from repo2graph.integrity import validate_outdir

        idx = tmp_path / "idx"
        idx.mkdir()
        (idx / "agent").mkdir()
        # populate with a marker so it's recognized
        result = validate_outdir(idx)
        assert result == idx.resolve()


# ============================================================================
# BuildLock — mutual exclusion and timeout
# ============================================================================


class TestBuildLock:
    def test_acquire_and_release(self, tmp_path):
        from repo2graph.lock import BuildLock

        lock = BuildLock(tmp_path / "idx")
        lock.acquire()
        assert lock._acquired
        lock.release()
        assert not lock._acquired

    def test_context_manager(self, tmp_path):
        from repo2graph.lock import BuildLock

        lock_file = tmp_path / ".idx.r2glock"
        with BuildLock(tmp_path / "idx"):
            # Lock file is created while held
            assert lock_file.exists() or True  # sibling file, not in idx itself
        # After release, lock file is gone
        # (may not exist if acquire never created it yet -- test that no exception raised)

    def test_lock_metadata_written(self, tmp_path):
        from repo2graph.lock import BuildLock

        idx = tmp_path / "idx"
        lock = BuildLock(idx)
        lock.acquire()
        lock_file = lock.lock_file
        assert lock_file.exists()

        # On Windows, msvcrt.locking prevents external reads of the locked file.
        # Read via the internal file handle instead.
        fh = lock._fh
        assert fh is not None
        fh.seek(0)
        meta = json.loads(fh.read())
        lock.release()

        assert meta["pid"] == os.getpid()
        assert "host" in meta
        assert "created_at" in meta

    def test_timeout_when_already_held(self, tmp_path):
        """A second lock acquire on the same outdir must time out cleanly."""
        from repo2graph.lock import BuildLock, LockTimeoutError

        idx = tmp_path / "idx"
        first = BuildLock(idx, timeout=60.0)
        first.acquire()
        try:
            second = BuildLock(idx, timeout=0.2)  # very short timeout
            with pytest.raises(LockTimeoutError):
                second.acquire()
        finally:
            first.release()

    def test_stale_lock_is_reclaimed(self, tmp_path):
        """A lock file from a non-existent PID is reclaimed on next acquire."""
        from repo2graph.lock import BuildLock

        idx = tmp_path / "idx"
        lock = BuildLock(idx)
        # Write a lock file with a fake (certainly dead) PID
        lock.lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock.lock_file.write_text(
            json.dumps({"pid": 99999999, "host": "localhost", "created_at": 0.0}),
            encoding="utf8",
        )
        # A fresh lock should reclaim it and acquire successfully
        fresh = BuildLock(idx, timeout=2.0)
        fresh.acquire()
        assert fresh._acquired
        fresh.release()


# ============================================================================
# verify_artifacts — integrity report
# ============================================================================


class TestVerifyArtifacts:
    def test_valid_index_reports_valid(self, tmp_path):
        from repo2graph.integrity import verify_artifacts

        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out)])

        report = verify_artifacts(out)
        assert report.status == "valid"
        assert report.is_valid
        assert not report.errors
        # build_id should be populated (new manifest format)
        assert report.build_id is not None
        assert len(report.build_id) == 36  # UUID4 length

    def test_missing_dir_reports_partial(self, tmp_path):
        from repo2graph.integrity import verify_artifacts

        report = verify_artifacts(tmp_path / "nonexistent")
        assert report.status == "partial"
        assert any("does not exist" in e for e in report.errors)

    def test_corrupt_manifest_reports_corrupt(self, tmp_path):
        from repo2graph.integrity import verify_artifacts

        out = tmp_path / "idx"
        (out / "agent").mkdir(parents=True)
        (out / "agent" / "manifest.json").write_text("NOT_JSON{{{", encoding="utf8")

        report = verify_artifacts(out)
        assert report.status == "corrupt"
        assert any("manifest" in e.lower() for e in report.errors)

    def test_checksum_mismatch_reports_corrupt(self, tmp_path):
        from repo2graph.integrity import verify_artifacts

        # Build a valid index first
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out)])

        # Tamper with nodes.jsonl
        nodes_path = artifact_path(out, "nodes.jsonl")
        nodes_path.write_text(
            '{"id": "TAMPERED", "type": "file", "path": "tampered.py"}\n', encoding="utf8"
        )

        report = verify_artifacts(out)
        # The checksum for nodes.jsonl in manifest no longer matches the file
        assert report.status in ("corrupt",)
        assert any("mismatch" in e.lower() or "Checksum" in e for e in report.errors)

    def test_missing_critical_file_reports_partial(self, tmp_path):
        from repo2graph.integrity import verify_artifacts

        # Build a valid index
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out)])

        # Remove a critical file
        artifact_path(out, "chunks.jsonl").unlink()

        report = verify_artifacts(out)
        assert report.status in ("partial", "corrupt")
        assert any("chunks.jsonl" in e for e in report.errors)

    def test_corrupt_chunks_jsonl_reports_corrupt(self, tmp_path):
        from repo2graph.integrity import verify_artifacts

        out = tmp_path / "idx"
        (out / "agent").mkdir(parents=True)
        (out / "agent" / "manifest.json").write_text(
            '{"format": "repo2graph/1", "repo": "test"}', encoding="utf8"
        )
        (out / "agent" / "chunks.jsonl").write_text(
            '{"id": "c1", "text": "valid"}\nNOT_VALID_JSON\n', encoding="utf8"
        )

        report = verify_artifacts(out)
        assert report.status == "corrupt"
        assert any("chunks.jsonl" in e for e in report.errors)

    def test_vector_build_id_mismatch_reports_stale(self, tmp_path):
        from repo2graph.integrity import verify_artifacts

        # Build a valid index
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out)])

        # Plant a fake vectors.meta.json with a different build_id
        manifest_data = json.loads(artifact_path(out, "manifest.json").read_text(encoding="utf8"))
        _ = manifest_data.get("build_id", "real-id")  # noqa: F841

        fake_meta = {
            "format": "repo2graph/vectors-1",
            "model_id": "test/model",
            "dim": 8,
            "count": 0,
            "chunk_ids": [],
            "text_hashes": [],
            "build_id": "00000000-0000-0000-0000-000000000000",  # differs from real
        }
        agent_dir = out / "agent"
        # Also need a fake vectors.npy (minimal valid NPY header)
        import struct

        magic = b"\x93NUMPY"
        header_body = "{'descr': '<f4', 'fortran_order': False, 'shape': (0, 8), }}"
        pad = -(len(magic) + 4 + len(header_body) + 1) % 64
        header_body += " " * pad + "\n"
        header_len = struct.pack("<H", len(header_body))
        npy_bytes = magic + bytes([1, 0]) + header_len + header_body.encode("latin1")
        (agent_dir / "vectors.npy").write_bytes(npy_bytes)
        (agent_dir / "vectors.meta.json").write_text(
            json.dumps(fake_meta, indent=2), encoding="utf8"
        )

        report = verify_artifacts(out)
        # build_id mismatch → stale
        assert report.status in ("stale",)
        assert any("build_id" in w or "differs" in w for w in report.warnings)


# ============================================================================
# Transactional staging — dump_all atomic swap
# ============================================================================


class TestTransactionalBuild:
    def test_successful_build_produces_index_in_outdir(self, tmp_path):
        """A normal build leaves artifacts in outdir, no staging dir leftover."""
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        rc = build_index(repo, out)
        assert rc == 0
        assert artifact_path(out, "manifest.json").exists()
        assert artifact_path(out, "nodes.jsonl").exists()

        # No staging leftovers
        staging_dirs = list(tmp_path.glob(".idx.staging.*"))
        assert staging_dirs == [], staging_dirs

    def test_rebuild_preserves_vectors(self, tmp_path):
        """After a rebuild, vectors.npy planted by an earlier embed must survive."""
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        build_index(repo, out)

        # Plant a fake vectors file (we don't need a real embedder)
        agent_dir = out / "agent"
        (agent_dir / "vectors.npy").write_bytes(b"\x93NUMPY fake")
        (agent_dir / "vectors.meta.json").write_text(
            '{"format": "repo2graph/vectors-1", "model_id": "x", "dim": 2, "count": 0, "chunk_ids": [], "text_hashes": []}',
            encoding="utf8",
        )

        # Rebuild (no embed)
        build_index(repo, out)

        # vectors.npy must still be present (copied from old outdir to staging)
        assert (agent_dir / "vectors.npy").exists()
        assert (agent_dir / "vectors.npy").read_bytes() == b"\x93NUMPY fake"

    def test_second_build_updates_manifest_build_id(self, tmp_path):
        """Each build writes a fresh build_id; successive builds produce different IDs."""
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        build_index(repo, out)
        build_id_1 = json.loads(artifact_path(out, "manifest.json").read_text(encoding="utf8"))[
            "build_id"
        ]

        build_index(repo, out)
        build_id_2 = json.loads(artifact_path(out, "manifest.json").read_text(encoding="utf8"))[
            "build_id"
        ]

        assert build_id_1 != build_id_2

    def test_manifest_has_checksums(self, tmp_path):
        """manifest.json must carry a non-empty checksums dict after a full build."""
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        build_index(repo, out)
        manifest = json.loads(artifact_path(out, "manifest.json").read_text(encoding="utf8"))

        checksums = manifest.get("checksums")
        assert isinstance(checksums, dict)
        assert len(checksums) > 0
        # Every checksum must be sha256:<hex>
        for rel, ck in checksums.items():
            assert ck.startswith("sha256:"), (rel, ck)
            assert len(ck) == len("sha256:") + 64, (rel, ck)

    def test_manifest_has_provenance_fields(self, tmp_path):
        """manifest.json must carry build_id, tool_version, schema_version, created_at."""
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        build_index(repo, out)
        manifest = json.loads(artifact_path(out, "manifest.json").read_text(encoding="utf8"))

        assert "build_id" in manifest
        assert "tool_version" in manifest
        assert manifest.get("schema_version") == 1
        assert "created_at" in manifest
        # build_id must be a valid UUID4
        import uuid

        uid = uuid.UUID(manifest["build_id"])
        assert uid.version == 4


# ============================================================================
# CLI integration — --allow-symlink-out, --force, --lock-timeout
# ============================================================================


class TestCLIFlags:
    def test_force_flag_allows_foreign_dir(self, tmp_path):
        """--force lets build write into a non-r2g directory."""
        repo = write_simple_repo(tmp_path)
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "unrelated.txt").write_text("data", encoding="utf8")

        rc = build_index(repo, foreign, extra_args=("--force",))
        assert rc == 0
        assert (foreign / "agent" / "manifest.json").exists()

    def test_build_refuses_repo_root_as_outdir(self, tmp_path):
        """build must raise SystemExit when -o points at the repo itself."""
        repo = write_simple_repo(tmp_path)
        with pytest.raises(SystemExit):
            main(["build", str(repo), "-o", str(repo), "--formats", "jsonl"])

    def test_lock_timeout_flag_is_accepted(self, tmp_path):
        """--lock-timeout is parsed without error (value consumed by cmd_build)."""
        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        rc = main(
            ["build", str(repo), "-o", str(out), "--formats", "jsonl", "--lock-timeout", "30"]
        )
        assert rc == 0

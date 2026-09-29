"""Tests for ISS-408 (bounded JSONL reads), ISS-345 (_fit_lines linearity),
and ISS-378 (single-character identifier retrieval).

Per AGENTS.md: assertions use hand-derived literal values, never values
recomputed by the code under test.
"""

import json
from pathlib import Path

import pytest

from repo2graph.cli import main


def write_simple_repo(root: Path) -> Path:
    """A minimal one-file repo so `build` can index it quickly."""
    repo = root / "src"
    repo.mkdir()
    (repo / "app.py").write_text(
        "CONSTANT = 42\n\ndef hello():\n    return CONSTANT\n", encoding="utf8", newline="\n"
    )
    return repo


# ============================================================================
# ISS-408 -- bounded JSONL reads
# ============================================================================


class TestJsonlBounds:
    def test_read_jsonl_rejects_a_line_over_the_per_line_ceiling(self, tmp_path, monkeypatch):
        """query.read_jsonl: one enormous line (no trailing newline) must raise
        ValueError, not attempt to allocate the whole file."""
        import repo2graph.query as query_mod

        monkeypatch.setattr(query_mod, "MAX_JSONL_LINE_BYTES", 64)
        p = tmp_path / "chunks.jsonl"
        p.write_text('{"id": "c1", "text": "' + ("x" * 200) + '"}', encoding="utf8")

        with pytest.raises(ValueError, match="64-byte per-line limit"):
            query_mod.read_jsonl(p)

    def test_read_jsonl_bounds_the_allocation_not_just_the_report(self, monkeypatch):
        """ISS-408's first attack shape is a file with no newline in it at all.

        The sibling test above only proves a ValueError is *reported*, which a
        `for raw in fh` loop does too -- after reading the whole file into one
        bytes object, which is the thing the ceiling exists to prevent. So this
        counts the bytes the reader actually asks for: it must give up after
        roughly one block past the ceiling, never serve the whole file.

        Detector: restore `for lineno, raw in enumerate(fh, 1)` in read_jsonl
        and `served` jumps from ~64 KiB to the full 10 MiB.
        """
        import repo2graph.query as query_mod
        from repo2graph.integrity import JSONL_READ_BLOCK

        size = 10 << 20  # 10 MiB, none of it a newline

        class _NoNewlineFile:
            def __init__(self):
                self.served = 0

            def read(self, n=-1):
                room = size - self.served
                take = room if n is None or n < 0 else min(n, room)
                self.served += take
                return b"x" * take

            def __iter__(self):
                # What binary-file iteration does: read on until a newline or
                # EOF. There is no newline, so this is the whole file as one
                # line -- the unbounded allocation, reproduced faithfully so
                # that reverting the fix fails this test instead of erroring.
                while chunk := self.read(-1):
                    yield chunk

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        handle = _NoNewlineFile()
        monkeypatch.setattr(query_mod, "MAX_JSONL_LINE_BYTES", 64)
        monkeypatch.setattr(query_mod, "open", lambda *a, **k: handle, raising=False)

        with pytest.raises(ValueError, match="per-line limit"):
            query_mod.read_jsonl(Path("chunks.jsonl"))

        assert handle.served <= JSONL_READ_BLOCK * 2, (
            f"read {handle.served} bytes of a {size}-byte newline-less file; "
            "the per-line ceiling must bound the allocation, not just report it"
        )

    def test_read_jsonl_accepts_a_line_under_the_ceiling(self, tmp_path):
        """The ceiling does not disturb an ordinary well-formed file."""
        from repo2graph.query import read_jsonl

        p = tmp_path / "chunks.jsonl"
        p.write_text('{"id": "c1", "text": "hello"}\n', encoding="utf8")
        rows = read_jsonl(p)
        assert rows == [{"id": "c1", "text": "hello"}]

    def test_read_jsonl_has_no_total_bytes_ceiling(self, tmp_path, monkeypatch):
        """query.Index's read path is deliberately unbounded in total bytes --
        AGENTS.md: a wrong constant there breaks real indexes. Many small,
        individually-legal lines that sum well past a hypothetical total
        ceiling must still all come back."""
        import repo2graph.query as query_mod

        p = tmp_path / "chunks.jsonl"
        lines = [json.dumps({"id": f"c{i}", "text": "y" * 50}) for i in range(500)]
        p.write_text("\n".join(lines) + "\n", encoding="utf8")
        # Every line is well under the (real, unmonkeypatched) per-line ceiling,
        # and the total (500 * ~60 bytes ~= 30KB) is trivial, but the point is
        # there is no total-bytes parameter at all on this call.
        rows = query_mod.read_jsonl(p)
        assert len(rows) == 500

    def test_iter_jsonl_bounded_rejects_oversized_line(self, tmp_path):
        from repo2graph.integrity import iter_jsonl_bounded

        p = tmp_path / "x.jsonl"
        p.write_text('{"id": "c1", "text": "' + ("z" * 500) + '"}', encoding="utf8")

        with pytest.raises(ValueError, match="per-line limit"):
            list(iter_jsonl_bounded(p, max_line_bytes=32))

    def test_iter_jsonl_bounded_rejects_total_over_ceiling(self, tmp_path):
        from repo2graph.integrity import iter_jsonl_bounded

        p = tmp_path / "x.jsonl"
        lines = [json.dumps({"id": f"c{i}", "text": "a" * 20}) for i in range(20)]
        p.write_text("\n".join(lines) + "\n", encoding="utf8")

        # Each line individually fits max_line_bytes, but the running total
        # (20 lines * ~35 bytes ~= 700 bytes) exceeds a small total ceiling.
        with pytest.raises(ValueError, match="total limit"):
            list(iter_jsonl_bounded(p, max_line_bytes=1024, max_total_bytes=200))

    def test_iter_jsonl_bounded_accepts_within_both_ceilings(self, tmp_path):
        from repo2graph.integrity import iter_jsonl_bounded

        p = tmp_path / "x.jsonl"
        p.write_text('{"id": "c1", "text": "ok"}\n{"id": "c2", "text": "ok2"}\n', encoding="utf8")

        rows = list(iter_jsonl_bounded(p, max_line_bytes=1024, max_total_bytes=1024))
        assert [r for _, r in rows] == [
            {"id": "c1", "text": "ok"},
            {"id": "c2", "text": "ok2"},
        ]

    def test_iter_jsonl_bounded_rejects_malformed_json_as_valueerror(self, tmp_path):
        from repo2graph.integrity import iter_jsonl_bounded

        p = tmp_path / "x.jsonl"
        p.write_text("NOT_JSON\n", encoding="utf8")
        with pytest.raises(ValueError, match="not valid JSON"):
            list(iter_jsonl_bounded(p))

    def test_verify_artifacts_reports_corrupt_not_an_exception_on_oversized_line(
        self, tmp_path, monkeypatch
    ):
        """integrity.verify_artifacts: an oversized chunks.jsonl line must land
        as report.status == "corrupt", never raise out of the call."""
        import repo2graph.integrity as integrity_mod

        out = tmp_path / "idx"
        (out / "agent").mkdir(parents=True)
        (out / "agent" / "manifest.json").write_text(
            '{"format": "repo2graph/1", "repo": "test"}', encoding="utf8"
        )
        (out / "agent" / "chunks.jsonl").write_text(
            '{"id": "c1", "text": "' + ("x" * 300) + '"}\n', encoding="utf8"
        )

        monkeypatch.setattr(integrity_mod, "MAX_JSONL_LINE_BYTES", 64)
        report = integrity_mod.verify_artifacts(out)
        assert report.status == "corrupt"
        assert any("chunks.jsonl" in e for e in report.errors)

    def test_verify_artifacts_reports_corrupt_on_total_bytes_ceiling(self, tmp_path, monkeypatch):
        """A legitimately-framed but enormous chunks.jsonl must also trip the
        verification path's total-bytes ceiling and report "corrupt", not hang
        or exhaust memory reading it whole."""
        import repo2graph.integrity as integrity_mod

        out = tmp_path / "idx"
        (out / "agent").mkdir(parents=True)
        (out / "agent" / "manifest.json").write_text(
            '{"format": "repo2graph/1", "repo": "test"}', encoding="utf8"
        )
        lines = [json.dumps({"id": f"c{i}", "text": "y" * 20}) for i in range(50)]
        (out / "agent" / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf8")

        monkeypatch.setattr(integrity_mod, "MAX_JSONL_TOTAL_BYTES", 200)
        report = integrity_mod.verify_artifacts(out)
        assert report.status == "corrupt"
        assert any("chunks.jsonl" in e for e in report.errors)

    def test_verify_artifacts_unaffected_by_ceilings_on_a_real_small_index(self, tmp_path):
        """Detector-neutrality check: a normal small build must still verify
        as valid with the (real, generous) ceilings in place."""
        from repo2graph.integrity import verify_artifacts

        repo = write_simple_repo(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out)])
        assert verify_artifacts(out).status == "valid"

    def test_doctor_check_vectors_never_raises_on_oversized_chunks_jsonl(
        self, tmp_path, monkeypatch
    ):
        """doctor.check_vectors's chunk-count sweep (ISS-408's third site) must
        degrade to a CheckResult, never raise, when chunks.jsonl is hostile."""
        import repo2graph.integrity as integrity_mod
        from repo2graph.doctor import check_vectors

        agent_dir = tmp_path / ".r2g" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "vectors.npy").write_bytes(b"\x93NUMPY\x01\x00")
        (agent_dir / "vectors.meta.json").write_text(
            json.dumps({"model_id": "m", "dim": 4, "chunk_ids": ["c1"]}), encoding="utf8"
        )
        (agent_dir / "chunks.jsonl").write_text(
            '{"id": "c1", "text": "' + ("q" * 300) + '"}\n', encoding="utf8"
        )

        monkeypatch.setattr(integrity_mod, "MAX_JSONL_LINE_BYTES", 64)
        result = check_vectors(tmp_path / ".r2g")
        # Must come back as a CheckResult with a "warn"/"fail"-shaped status,
        # not propagate the ValueError.
        assert result.status in ("warn", "fail")

    def test_doctor_check_vectors_still_detects_desync_with_real_ceilings(self, tmp_path):
        """Neutrality: the existing desync detection (test_doctor.py's
        test_doctor_vector_checks) must survive switching the counting loop to
        the bounded reader."""
        from repo2graph.doctor import check_vectors

        agent_dir = tmp_path / ".r2g" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "vectors.npy").write_bytes(b"\x93NUMPY\x01\x00")
        (agent_dir / "vectors.meta.json").write_text(
            json.dumps({"model_id": "test-model", "dim": 384, "chunk_ids": ["c1", "c2"]}),
            encoding="utf8",
        )
        (agent_dir / "chunks.jsonl").write_text('{"id": "c1", "text": "one"}\n', encoding="utf8")
        result = check_vectors(tmp_path / ".r2g")
        assert result.status == "warn"
        assert "out of sync" in result.summary


# ============================================================================
# ISS-345 -- _fit_lines linearity, output unchanged
# ============================================================================


class TestFitLines:
    def test_fit_lines_default_measure_matches_hand_derived_prefix(self):
        from repo2graph.query import _fit_lines

        text = "aaa\nbb\ncccc\nd"
        # len("aaa") = 3, +1+2 = 6, +1+4 = 11, +1+1 = 13
        assert _fit_lines(text, 3) == "aaa"
        assert _fit_lines(text, 6) == "aaa\nbb"
        assert _fit_lines(text, 10) == "aaa\nbb"  # "aaa\nbb\ncccc" is 11, doesn't fit
        assert _fit_lines(text, 11) == "aaa\nbb\ncccc"
        assert _fit_lines(text, 13) == "aaa\nbb\ncccc\nd"
        assert _fit_lines(text, 100) == "aaa\nbb\ncccc\nd"

    def test_fit_lines_limit_zero_or_negative_is_empty(self):
        from repo2graph.query import _fit_lines

        assert _fit_lines("abc\ndef", 0) == ""
        assert _fit_lines("abc\ndef", -5) == ""

    def test_fit_lines_no_lines_fit_returns_empty(self):
        from repo2graph.query import _fit_lines

        assert _fit_lines("abcdefgh", 3) == ""

    def test_fit_lines_custom_measure_still_measures_whole_candidate(self):
        """Non-additive measure path: token-style count_tokens (len // 4,
        floor) must still be applied to the whole growing string each line,
        matching the pre-optimization behaviour exactly."""
        from repo2graph.query import _fit_lines, count_tokens

        text = "wxyz\nwxyz\nwxyz"  # each line is 4 chars -> 1 token via count_tokens
        # candidate 1: "wxyz" -> 4 chars -> 1 token
        # candidate 2: "wxyz\nwxyz" -> 9 chars -> 2 tokens
        # candidate 3: "wxyz\nwxyz\nwxyz" -> 14 chars -> 3 tokens
        assert _fit_lines(text, 1, count_tokens) == "wxyz"
        assert _fit_lines(text, 2, count_tokens) == "wxyz\nwxyz"
        assert _fit_lines(text, 3, count_tokens) == "wxyz\nwxyz\nwxyz"

    def test_fit_lines_is_linear_not_quadratic(self):
        """Detector for ISS-345. The old implementation called `measure` once
        per line either way -- the quadratic cost was in the *string building*,
        which no call counter can see -- so this has to be a wall-clock test.

        Comparing one input size against a larger one does not work here: the
        ceiling would be anchored on a small-input timing that is itself
        quadratic, and the two grow together. Measured, a 10x spread separates
        the regression from the allowance by only ~2x, which is how a timing
        test becomes a flake on a shared runner.

        So calibrate against a *known-linear* operation over the same text on
        the same machine instead. Measured here: the O(N) implementation costs
        ~2.7x the reference, the old join-per-line one ~434x. A 40x ceiling
        sits an order of magnitude clear of both.
        """
        import time

        from repo2graph.query import _fit_lines

        text = "\n".join("x" * 50 for _ in range(4000))
        limit = len(text) + 1

        def timed(fn) -> float:
            start = time.perf_counter()
            for _ in range(20):
                fn()
            return time.perf_counter() - start

        # split+join touches every byte exactly twice -- unambiguously O(N),
        # and it absorbs whatever this runner's constant factor happens to be.
        reference = timed(lambda: "\n".join(text.split("\n")))
        actual = timed(lambda: _fit_lines(text, limit))
        assert actual < reference * 40, (reference, actual, actual / reference)


# ============================================================================
# ISS-378 -- single-character identifiers are indexed and retrievable
# ============================================================================


class TestSingleCharIdentifiers:
    def _repo_with_single_char_symbol(self, root: Path) -> Path:
        repo = root / "src"
        repo.mkdir()
        (repo / "matrix.py").write_text(
            "def T(value):\n"
            '    """Transpose-ish helper, deliberately named with one letter."""\n'
            "    return value\n"
            "\n\n"
            "def other_helper(value):\n"
            "    return T(value) + 1\n",
            encoding="utf8",
            newline="\n",
        )
        return repo

    def test_tokenize_still_drops_single_chars_from_free_text(self):
        """TOKEN_RE / tokenize() are unchanged for body text -- only the
        declared-name path (Index.__init__, _boost_identifiers, score()'s
        query-side) admits length-1 terms."""
        from repo2graph.query import tokenize

        assert "t" not in tokenize("a T b")
        assert "a" not in tokenize("a T b")

    def test_query_for_single_char_name_retrieves_the_symbol(self, tmp_path):
        """Acceptance criterion from #378: a query of exactly "T" must return
        the symbol chunk for a function literally named T."""
        from repo2graph.query import Index

        repo = self._repo_with_single_char_symbol(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
        idx = Index(out)

        hits = idx.retrieve("T", k=5, hops=0)
        assert any(h.get("name") == "T" for h in hits), [h.get("name") for h in hits]

    def test_single_char_name_is_boosted_over_prose_mention(self, tmp_path):
        from repo2graph.query import Index

        repo = self._repo_with_single_char_symbol(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
        idx = Index(out)

        scored = idx.score("T")
        assert scored, "query 'T' produced zero results"
        top_i = scored[0][1]
        assert idx.chunks[top_i].get("name") == "T"

    def test_postings_growth_from_single_char_names_is_bounded(self, tmp_path):
        """Acceptance criterion from #378: postings count grows by less than a
        stated bound. Single-character names are drawn from
        `[A-Za-z_]`, so at most 53 distinct new postings terms
        (26 lower + 26 upper-folded-to-lower collapses to 26, + "_") can ever
        be added by this feature, regardless of corpus size -- unlike
        admitting single characters from free text, which would scale with
        the corpus.
        """
        from repo2graph.query import Index, tokenize

        repo = self._repo_with_single_char_symbol(tmp_path)
        out = tmp_path / "idx"
        main(["build", str(repo), "-o", str(out), "--formats", "jsonl"])
        idx = Index(out)

        single_char_terms = {t for t in idx.postings if len(t) == 1}
        # "t" (from the name "T") is present, and nothing else single-char is,
        # since none of the fixture's free text or other names is one letter.
        assert single_char_terms == {"t"}
        assert not tokenize("T")  # confirms "t" could only have come from name indexing

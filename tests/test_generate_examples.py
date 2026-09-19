"""Regression tests for scripts/generate_examples.py helpers."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from types import ModuleType

from conftest import REPO_ROOT


def _load_generate_examples():
    """Import the script without requiring the PyYAML tool extra.

    CI installs ``.[dev,mcp]`` only; ``yaml`` is used by the example
    generator, not by ``_read_jsonl_maybe_gz``.
    """
    existed = "yaml" in sys.modules
    if not existed:
        sys.modules["yaml"] = ModuleType("yaml")
    try:
        script_path = REPO_ROOT / "scripts" / "generate_examples.py"
        spec = importlib.util.spec_from_file_location("generate_examples_iss137", script_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if not existed:
            sys.modules.pop("yaml", None)


def test_iss137_jsonl_record_with_u2028_stays_one_line(tmp_path):
    """A JSONL record containing raw U+2028 must survive as one JSON line.

    ``str.splitlines()`` treats U+2028 as a break, so ``json.loads`` sees
    two fragments. AGENTS.md requires ``split("\\n")`` plus a trailing-``\\r``
    strip. The fixture is a detector: ``record.splitlines()`` is length 2
    while ``split("\\n")`` is length 1.
    """
    ge = _load_generate_examples()
    record = json.dumps({"id": "chunk-1", "text": "hello\u2028world"}, ensure_ascii=False)
    assert "\u2028" in record
    assert len(record.splitlines()) == 2
    assert len(record.split("\n")) == 1

    second = json.dumps({"id": "chunk-2", "text": "plain"})
    gz_path = tmp_path / "nodes.jsonl.gz"
    with gzip.open(gz_path, "wt", encoding="utf8", newline="\n") as fh:
        fh.write(record + "\n")
        fh.write(second + "\n")

    lines = ge._read_jsonl_maybe_gz(gz_path)
    assert lines == [record, second]
    parsed = json.loads(lines[0])
    assert parsed["id"] == "chunk-1"
    assert parsed["text"] == "hello\u2028world"

    plain_path = tmp_path / "nodes.jsonl"
    plain_path.write_text(record + "\n", encoding="utf8", newline="\n")
    assert ge._read_jsonl_maybe_gz(plain_path) == [record]
    assert json.loads(ge._read_jsonl_maybe_gz(plain_path)[0])["text"] == "hello\u2028world"

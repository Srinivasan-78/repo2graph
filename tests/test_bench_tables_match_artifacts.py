"""Every published benchmark number must match the artifact it claims to come from.

This exists because of a silent drift that shipped. The held-out structural
table in `benchmarks/real/README.md` was read off `results_holdout_knobs.json`
-- an intermediate artifact two phases old -- while naming
`results_holdout_final.json`. Two cells understated recall by 2.4 pp each, and
the scorecard built on them recorded a target as missed by 1 pp when it had in
fact passed.

Nothing caught it. The 18 tests in `test_doc_consistency.py` check that links
resolve, that command lines parse and that version strings agree; none of them
reads a number out of a table and compares it to the JSON beside it. A
regenerated artifact and a hand-updated table are two separate acts, and the
second is the one that gets forgotten.

How a table opts in
-------------------
Immediately before the table, an HTML comment names the artifact and the
summary key inside it::

    <!-- bench-table: results_holdout_final.json structural_summary -->

    | Budget | Retriever | Evidence found | Fully answered | Mean tokens |
    |---:|---|---:|---:|---:|
    | 2,000 | repo2graph | 17% | 6 / 40 | 1,962 |

That is the *long* form: a Budget column, a Retriever column, and one column per
metric, matched by header text. Tables that put methods in the columns instead
declare the metric and the column order::

    <!-- bench-table: results_holdout_final.json structural_summary
         wide=evidence_recall methods=repo2graph,repo2graph-bm25,ripgrep -->

Opting in is not optional: `test_every_benchmark_table_is_checked` fails for any
table that looks like a benchmark table and carries no marker, so the way to add
one without coverage is to deliberately rename its columns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH = REPO_ROOT / "benchmarks" / "real"
DOCS = [REPO_ROOT / "README.md", BENCH / "README.md", REPO_ROOT / "docs" / "comparison.md"]

MARKER_RE = re.compile(
    r"<!--\s*bench-table:\s*(?P<args>[^>]+?)\s*-->\s*\n+(?P<table>\|[^\n]*\n(?:\|[^\n]*\n)+)",
    re.MULTILINE,
)

# A table is "benchmark shaped" -- and so must carry a marker -- when its header
# row has a Budget column. That is the one column every one of these tables has
# and no other table in the docs does.
BUDGET_HEADER_RE = re.compile(r"^\|\s*Budget\s*\|", re.IGNORECASE)

# Header text -> (artifact field, kind). "count" fields are rendered as "n / total".
METRIC_COLUMNS: dict[str, tuple[str, str]] = {
    "evidence found": ("evidence_recall", "pct"),
    "evidence recall": ("evidence_recall", "pct"),
    "fully answered": ("tasks_fully_answered", "count"),
    "any evidence": ("tasks_any_evidence", "count"),
    "mean tokens": ("mean_tokens_used", "int"),
}


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _plain(cell: str) -> str:
    """A cell's value without markdown emphasis or thousands separators."""
    return cell.replace("**", "").replace("*", "").replace(",", "").strip()


def _tables(text: str) -> list[tuple[dict[str, str], list[str]]]:
    out = []
    for m in MARKER_RE.finditer(text):
        args: dict[str, str] = {}
        parts = m.group("args").split()
        args["file"], args["key"] = parts[0], parts[1]
        for p in parts[2:]:
            k, _, v = p.partition("=")
            args[k] = v
        out.append((args, [r for r in m.group("table").splitlines() if r.strip()]))
    return out


def _artifact(name: str, key: str) -> list[dict[str, Any]]:
    path = BENCH / name
    assert path.exists(), f"bench-table marker names a missing artifact: {name}"
    data = json.loads(path.read_text(encoding="utf8"))
    assert key in data, f"{name} has no {key!r}; keys are {sorted(data)}"
    rows = data[key]
    assert rows, f"{name}:{key} is empty"
    return rows


def _lookup(rows: list[dict[str, Any]], budget: int, method: str, where: str) -> dict[str, Any]:
    for r in rows:
        if r["budget"] == budget and r["method"] == method:
            return r
    raise AssertionError(
        f"{where}: no row for budget={budget} method={method!r} in the artifact; "
        f"it has {sorted({(r['budget'], r['method']) for r in rows})}"
    )


def _check(cell: str, row: dict[str, Any], field: str, kind: str, where: str) -> None:
    value = _plain(cell)
    if value in ("", "-", "n/a"):
        return
    if kind == "pct":
        want = round(row[field] * 100)
        got = int(value.rstrip("%"))
        assert got == want, (
            f"{where}: table says {got}% but {field} is {row[field]:.4f} ({want}%). "
            "Regenerate the table from the artifact, or the artifact from a clean tree."
        )
    elif kind == "count":
        num, _, denom = value.partition("/")
        assert int(num.strip()) == row[field], (
            f"{where}: table says {num.strip()} but {field} is {row[field]}"
        )
        if denom.strip():
            assert int(denom.strip()) == row["tasks"], (
                f"{where}: table denominator {denom.strip()} but the artifact scored "
                f"{row['tasks']} tasks -- a changed denominator is a changed question set"
            )
    else:
        assert int(value) == row[field], f"{where}: table says {value} but {field} is {row[field]}"


def _doc_tables() -> list[tuple[Path, dict[str, str], list[str]]]:
    found = []
    for doc in DOCS:
        for args, rows in _tables(doc.read_text(encoding="utf8")):
            found.append((doc, args, rows))
    return found


def test_there_are_marked_tables_to_check():
    """A regex that matches nothing is a test that passes for the wrong reason."""
    tables = _doc_tables()
    assert len(tables) >= 4, f"only {len(tables)} marked benchmark tables found"


@pytest.mark.parametrize(
    "doc,args,rows",
    _doc_tables(),
    ids=[f"{d.name}:{a['file']}:{a['key']}" for d, a, _r in _doc_tables()],
)
def test_a_marked_table_matches_its_artifact(doc, args, rows):
    artifact = _artifact(args["file"], args["key"])
    header = [h.lower() for h in _cells(rows[0])]
    body = rows[2:]
    assert body, f"{doc.name}: marked table has no data rows"

    if "wide" in args:
        methods = args["methods"].split(",")
        field, kind = args["wide"], "pct"
        assert len(header) - 1 == len(methods), (
            f"{doc.name}: marker lists {len(methods)} methods but the table has "
            f"{len(header) - 1} non-budget columns"
        )
        for row in body:
            cells = _cells(row)
            budget = int(_plain(cells[0]).split()[0])
            for method, cell in zip(methods, cells[1:]):
                where = f"{doc.name} [{args['file']}:{args['key']}] {budget}/{method}"
                _check(cell, _lookup(artifact, budget, method, where), field, kind, where)
        return

    assert header[0].startswith("budget"), f"{doc.name}: long-form table needs a Budget column"
    assert "retriever" in header[1] or "method" in header[1], (
        f"{doc.name}: long-form table needs a Retriever column, got {header[1]!r}"
    )
    for row in body:
        cells = _cells(row)
        budget = int(_plain(cells[0]).split()[0])
        method = _plain(cells[1])
        where = f"{doc.name} [{args['file']}:{args['key']}] {budget}/{method}"
        art = _lookup(artifact, budget, method, where)
        for name, cell in zip(header[2:], cells[2:]):
            for label, (field, kind) in METRIC_COLUMNS.items():
                if name.startswith(label):
                    _check(cell, art, field, kind, where)
                    break


def test_every_benchmark_table_is_checked():
    """A table with a Budget column and no marker is drift waiting to happen."""
    unmarked = []
    for doc in DOCS:
        text = doc.read_text(encoding="utf8")
        marked_lines = {text[: m.start("table")].count("\n") for m in MARKER_RE.finditer(text)}
        for i, line in enumerate(text.splitlines()):
            if BUDGET_HEADER_RE.match(line) and i not in marked_lines:
                unmarked.append(f"{doc.relative_to(REPO_ROOT)}:{i + 1}")
    assert not unmarked, (
        "benchmark tables with no `<!-- bench-table: ... -->` marker, so nothing "
        f"checks them against an artifact: {unmarked}"
    )


def test_artifacts_were_generated_from_a_clean_tree():
    """A `dirty: true` artifact was measured against uncommitted code, so the
    numbers cannot be reproduced from any commit."""
    named = {args["file"] for _doc, args, _rows in _doc_tables()}
    assert named, "no artifacts referenced"
    for name in sorted(named):
        data = json.loads((BENCH / name).read_text(encoding="utf8"))
        assert data.get("repo2graph_dirty") is False, (
            f"{name} was generated from a dirty tree (repo2graph_dirty="
            f"{data.get('repo2graph_dirty')!r}); rerun it from a committed tree"
        )
        assert data.get("repo2graph_commit"), f"{name} records no commit"

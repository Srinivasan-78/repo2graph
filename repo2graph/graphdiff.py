"""What changed structurally between two builds of a graph (#395).

One computation behind three outputs: `repo2graph diff`, the per-build
`human/CHANGELOG.md` (`changelog.py`), and through that file the GitHub
Action's job summary.

Two choices make the output readable rather than merely complete:

- **Renames are paired, not reported as delete + add.** A symbol id is
  `sym:<path>::<qualname>`, so moving a file renames every symbol in it. A
  removed and an added symbol of the same kind with the same body are one
  symbol renamed or moved; with the same qualname but a different body they
  are a *possible* rename, and are labelled so rather than guessed.
- **Confidence-only edge changes are not changes.** Adding one `get` anywhere
  shifts the `1/n` confidence of every ambiguous `get` call in the repository.
  An edge counts as changed when its source, target or type does; confidence
  moves are counted, and listed only on request.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

Record = dict[str, Any]


def body_hashes(chunks: Iterable[Record]) -> dict[str, str]:
    """node id -> sha256 of its source lines, for symbols held in one chunk.

    The chunk header names the file, so it is cut off: the last
    `end_line - start_line + 1` lines are the body. A symbol split across
    several chunks gets no hash and is never paired as an exact rename.
    """
    out: dict[str, str] = {}
    for c in chunks:
        nid = c.get("node_id")
        start, end = c.get("start_line"), c.get("end_line")
        if not nid or c.get("split") or not isinstance(start, int) or not isinstance(end, int):
            continue
        lines = (c.get("text") or "").split("\n")
        body = "\n".join(lines[-(end - start + 1) :]) if end >= start else ""
        out[nid] = hashlib.sha256(body.encode("utf8", "surrogateescape")).hexdigest()
    return out


def _edge_key(e: Record) -> tuple[Any, Any, Any]:
    return (e.get("src"), e.get("dst"), e.get("type"))


def _pair_renames(
    removed: dict[str, Record],
    added: dict[str, Record],
    old_hash: dict[str, str],
    new_hash: dict[str, str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(renames, possible renames) between removed and added symbols, one-to-one."""

    def kind(n: Record) -> tuple[Any, Any]:
        return (n.get("type"), n.get("kind"))

    renames: list[dict[str, str]] = []
    by_body: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for nid, n in added.items():
        if n.get("type") == "symbol" and nid in new_hash:
            by_body[(*kind(n), new_hash[nid])].append(nid)
    for nid in sorted(removed):
        n = removed[nid]
        if n.get("type") != "symbol" or nid not in old_hash:
            continue
        cands = by_body.get((*kind(n), old_hash[nid]), [])
        if len(cands) == 1:
            renames.append({"from": nid, "to": cands.pop(), "match": "same body"})
    paired_old = {r["from"] for r in renames}
    paired_new = {r["to"] for r in renames}

    possible: list[dict[str, str]] = []
    by_qual: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for nid, n in added.items():
        if n.get("type") == "symbol" and nid not in paired_new:
            by_qual[(*kind(n), n.get("qualname"))].append(nid)
    for nid in sorted(removed):
        n = removed[nid]
        if n.get("type") != "symbol" or nid in paired_old:
            continue
        cands = by_qual.get((*kind(n), n.get("qualname")), [])
        if len(cands) == 1:
            possible.append({"from": nid, "to": cands.pop(), "match": "same name, body changed"})
    return renames, possible


def _pair_files(
    removed: dict[str, Record], added: dict[str, Record], symbol_pairs: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Removed files whose paired symbols all moved to one added file."""
    targets: dict[str, set[str]] = defaultdict(set)
    for r in symbol_pairs:
        old_path, new_path = removed[r["from"]].get("path"), added[r["to"]].get("path")
        if old_path and new_path:
            targets[f"file:{old_path}"].add(f"file:{new_path}")
    taken: set[str] = set()
    out = []
    for fid in sorted(targets):
        (dest,) = targets[fid] if len(targets[fid]) == 1 else (None,)
        if fid in removed and dest in added and dest not in taken:
            taken.add(dest)
            out.append({"from": fid, "to": dest, "match": "its symbols moved"})
    return out


def diff_graphs(
    old_nodes: Iterable[Record],
    old_edges: Iterable[Record],
    new_nodes: Iterable[Record],
    new_edges: Iterable[Record],
    *,
    old_chunks: Iterable[Record] = (),
    new_chunks: Iterable[Record] = (),
    detect_renames: bool = True,
) -> dict[str, Any]:
    """The structural difference from the old graph to the new one.

    Returns a dict of sorted lists: `added_nodes`, `removed_nodes`, `renamed`,
    `possible_renames`, `added_edges`, `removed_edges`,
    `confidence_changed_edges`, and `callers_changed` (per node present in
    both builds, the CALLS sources gained and lost). Node and edge entries are
    the records themselves.
    """
    old_n = {n["id"]: n for n in old_nodes if "id" in n}
    new_n = {n["id"]: n for n in new_nodes if "id" in n}
    removed = {nid: n for nid, n in old_n.items() if nid not in new_n}
    added = {nid: n for nid, n in new_n.items() if nid not in old_n}

    renames: list[dict[str, str]] = []
    possible: list[dict[str, str]] = []
    if detect_renames and removed and added:
        renames, possible = _pair_renames(
            removed, added, body_hashes(old_chunks), body_hashes(new_chunks)
        )
        renames += _pair_files(removed, added, renames + possible)
    # Paired symbols are reported in their own sections, not as added/removed.
    # Only exact renames rewrite ids for the edge comparison: an edge of a
    # moved symbol is the same edge, but a possible rename is a guess and must
    # not hide a change.
    alias: dict[Any, Any] = {r["from"]: r["to"] for r in renames}
    for r in renames + possible:
        removed.pop(r["from"], None)
        added.pop(r["to"], None)

    def moved(e: Record) -> Record:
        src, dst = e.get("src"), e.get("dst")
        if src in alias or dst in alias:
            e = {**e, "src": alias.get(src, src), "dst": alias.get(dst, dst)}
        return e

    old_e = {_edge_key(e): e for e in map(moved, old_edges)}
    new_e = {_edge_key(e): e for e in new_edges}
    added_edges = [e for k, e in new_e.items() if k not in old_e]
    removed_edges = [e for k, e in old_e.items() if k not in new_e]
    conf_changed = [
        {"edge": e, "was": old_e[k].get("confidence"), "now": e.get("confidence")}
        for k, e in new_e.items()
        if k in old_e and old_e[k].get("confidence") != e.get("confidence")
    ]

    def callers(edges: Iterable[Record]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for e in edges:
            if e.get("type") == "CALLS":
                out[e["dst"]].add(e["src"])
        return out

    old_callers, new_callers = callers(old_e.values()), callers(new_e.values())
    callers_changed = []
    for nid in sorted(set(old_callers) | set(new_callers)):
        if nid not in new_n:
            continue
        gained = sorted(new_callers.get(nid, set()) - old_callers.get(nid, set()))
        lost = sorted(old_callers.get(nid, set()) - new_callers.get(nid, set()))
        if gained or lost:
            callers_changed.append({"node": nid, "gained": gained, "lost": lost})

    def edge_sort(e: Record) -> tuple[str, str, str]:
        return (e.get("type") or "", e.get("src") or "", e.get("dst") or "")

    return {
        "added_nodes": sorted(added.values(), key=lambda n: n["id"]),
        "removed_nodes": sorted(removed.values(), key=lambda n: n["id"]),
        "renamed": renames,
        "possible_renames": possible,
        "added_edges": sorted(added_edges, key=edge_sort),
        "removed_edges": sorted(removed_edges, key=edge_sort),
        "confidence_changed_edges": sorted(conf_changed, key=lambda c: edge_sort(c["edge"])),
        "callers_changed": callers_changed,
    }


def diff_indexes(old_dir: str | Path, new_dir: str | Path) -> dict[str, Any]:
    """`diff_graphs` over two index directories' nodes, edges and chunks."""
    from .export import path as artifact_path
    from .query import read_jsonl

    def load(d: str | Path, name: str) -> list[Record]:
        p = artifact_path(d, name)
        return read_jsonl(p) if p.exists() else []

    return diff_graphs(
        load(old_dir, "nodes.jsonl"),
        load(old_dir, "edges.jsonl"),
        load(new_dir, "nodes.jsonl"),
        load(new_dir, "edges.jsonl"),
        old_chunks=load(old_dir, "chunks.jsonl"),
        new_chunks=load(new_dir, "chunks.jsonl"),
    )


def render_text(d: dict[str, Any], *, limit: int = 50, show_confidence: bool = False) -> str:
    """The diff as plain text, each list capped at `limit` lines."""

    def capped(lines: list[str]) -> list[str]:
        if len(lines) > limit:
            return [*lines[:limit], f"  ... and {len(lines) - limit} more"]
        return lines

    def edge(e: Record) -> str:
        return f"{e.get('type')}: {e.get('src')} -> {e.get('dst')}"

    syms_add = [n for n in d["added_nodes"] if n.get("type") == "symbol"]
    syms_del = [n for n in d["removed_nodes"] if n.get("type") == "symbol"]
    files_moved = [r for r in d["renamed"] if str(r["from"]).startswith("file:")]
    syms_renamed = [r for r in d["renamed"] if r not in files_moved]
    out = [
        f"symbols: +{len(syms_add)} -{len(syms_del)}, {len(syms_renamed)} renamed, "
        f"{len(d['possible_renames'])} possible renames; {len(files_moved)} files moved",
        f"edges: +{len(d['added_edges'])} -{len(d['removed_edges'])} "
        f"({len(d['confidence_changed_edges'])} confidence-only changes not counted)",
    ]
    sections: list[tuple[str, list[str]]] = [
        ("added symbols", [f"  + {n['id']}" for n in syms_add]),
        ("removed symbols", [f"  - {n['id']}" for n in syms_del]),
        ("moved files", [f"  {r['from']} -> {r['to']}" for r in files_moved]),
        ("renamed (same body)", [f"  {r['from']} -> {r['to']}" for r in syms_renamed]),
        (
            "possible renames (same name, body changed)",
            [f"  {r['from']} -> {r['to']}" for r in d["possible_renames"]],
        ),
        (
            "callers changed",
            [
                f"  {c['node']}: "
                + ", ".join([*("+" + s for s in c["gained"]), *("-" + s for s in c["lost"])])
                for c in d["callers_changed"]
            ],
        ),
        ("added edges", [f"  + {edge(e)}" for e in d["added_edges"]]),
        ("removed edges", [f"  - {edge(e)}" for e in d["removed_edges"]]),
    ]
    if show_confidence:
        sections.append(
            (
                "confidence changed",
                [
                    f"  {edge(c['edge'])}: {c['was']} -> {c['now']}"
                    for c in d["confidence_changed_edges"]
                ],
            )
        )
    for title, lines in sections:
        if lines:
            out += ["", f"{title}:", *capped(lines)]
    return "\n".join(out) + "\n"


def _format_sym(nid: str) -> str:
    """Format node ID for human and CI display (e.g. sym:pkg/a.py::b -> pkg/a.py::b)."""
    if nid.startswith("sym:"):
        return nid[4:]
    if nid.startswith("route:"):
        return nid[6:]
    if nid.startswith("file:"):
        return nid[5:]
    return nid


def parse_git_diff_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse unified git diff output into file -> list of (start_line, end_line) spans."""
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    current_file: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" ")
            if len(parts) >= 4 and parts[3].startswith("b/"):
                b_path = parts[3][2:].strip()
                if b_path != "dev/null":
                    current_file = b_path.replace("\\", "/")
                    if current_file not in spans:
                        spans[current_file] = []
                elif parts[2].startswith("a/"):
                    a_path = parts[2][2:].strip().replace("\\", "/")
                    if a_path not in spans:
                        spans[a_path] = []
                    current_file = None
            else:
                current_file = None
            continue

        if current_file and line.startswith("@@ "):
            m = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start = int(m.group(3))
                count = int(m.group(4)) if m.group(4) is not None else 1
                end = start + max(1, count) - 1
                spans[current_file].append((start, end))

    return dict(spans)


def get_git_diff_spans(
    base_commit: str, repo_path: str | Path = "."
) -> dict[str, list[tuple[int, int]]]:
    """Get modified file spans by running git diff against base_commit."""
    cmd = ["git", "diff", "-U0", base_commit]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
        )
        return parse_git_diff_ranges(proc.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        raise ValueError(f"failed to run git diff against '{base_commit}': {exc}") from exc


def find_affected_tests(
    index_or_dir: Any,
    *,
    base_commit: str | None = None,
    repo_path: str | Path | None = None,
    modified_files: Iterable[str] | None = None,
    modified_spans: dict[str, list[tuple[int, int]]] | None = None,
    old_index_dir: str | Path | None = None,
    max_hops: int = 4,
    min_confidence: float = 0.0,
    follow_shadowed_builtins: bool = False,
) -> dict[str, Any]:
    """Identify modified symbols, impacted callers, and the tests that cover them.

    **The two error directions are not symmetric here.** Selecting a test that
    did not need to run costs CI a few seconds. *Missing* one means the run is
    green on code nobody exercised, which is the whole failure this feature is
    supposed to prevent -- so the reverse walk is tuned for recall, and anything
    that would narrow it has to be asked for.

    That is why `min_confidence` defaults to 0.0 rather than 0.5. A call to a
    symbol with `n` definitions in scope is recorded at `confidence = 1/n`, so a
    three-way-ambiguous call sits at 0.33: a 0.5 floor pruned the edge, severed
    the walk at that hop, and silently dropped every test reachable only
    through it. An ambiguous edge is a *candidate* call, and a candidate is
    exactly what test selection must keep. `untyped_receiver` edges are
    followed for the same reason.

    `shadowed_builtin` is the one exclusion, and it is dropped *unconditionally*
    rather than by the floor, because it is a different claim: an ambiguous edge
    says "one of these n real definitions was called", while a shadowed-builtin
    edge says "this call used a name that a local symbol happens to share with
    a builtin", and the builtin is almost always what ran. Following them made
    an edit to a function named `len` select every test that calls `len()`.
    The count is still reported in `shadowed_builtin_skipped`, and it marks the
    result `truncated`, because the classification is a heuristic and dropping a
    real edge on a heuristic is exactly what this function promises to admit to.
    `follow_shadowed_builtins=True` keeps them, which is the escape hatch for a
    codebase that really does define and call its own `len` or `open`: a
    heuristic with no override is one the caller cannot correct when it is
    wrong about their code.

    A `conftest.py` the walk reaches is reported in `conftests_reached` and
    marks the result `truncated`, but is kept *out* of `affected_tests`: pytest
    collects no test from a conftest, so handing one to a runner selects
    nothing, while reaching one is the signal that fixture consumers are
    invisible (#513). The list stays something a caller can pipe straight to
    `pytest`; the caveat goes in the warning.

    `max_hops` still bounds the walk, so the result can still be incomplete --
    but it says so now. When the frontier is still non-empty at the limit,
    `truncated` is True and `incomplete_reason` names the cause; a CI caller
    that sees it should run the full suite rather than trust the subset. Pass a
    `min_confidence > 0` to get the old narrowing back, and it is reported in
    `pruned_low_confidence` so the trade is visible rather than implicit.
    """
    from .query import Index
    from .testpaths import is_test_path

    if isinstance(index_or_dir, Index):
        index = index_or_dir
    else:
        index = Index(index_or_dir)

    spans: dict[str, list[tuple[int, int]]] = {}
    if modified_spans is not None:
        spans = {k.replace("\\", "/"): v for k, v in modified_spans.items()}
    elif base_commit is not None:
        spans = get_git_diff_spans(base_commit, repo_path=repo_path or ".")
    elif modified_files is not None:
        spans = {f.replace("\\", "/"): [] for f in modified_files}
    elif old_index_dir is not None:
        delta = diff_indexes(old_index_dir, index.dir)
        touched_files: set[str] = set()
        for n in delta.get("added_nodes", []) + delta.get("removed_nodes", []):
            if n.get("path"):
                touched_files.add(n["path"])
        for r in delta.get("renamed", []) + delta.get("possible_renames", []):
            f_from = r.get("from", "")
            f_to = r.get("to", "")
            if f_from.startswith("file:"):
                touched_files.add(f_from[5:])
            if f_to.startswith("file:"):
                touched_files.add(f_to[5:])
        for c in delta.get("callers_changed", []):
            nid = c.get("node", "")
            if nid.startswith("sym:"):
                touched_files.add(nid[4:].split("::")[0])
        spans = {f.replace("\\", "/"): [] for f in touched_files}

    modified_sym_ids: set[str] = set()
    affected_tests_set: set[str] = set()
    # Reached `conftest.py` files. Kept apart from `affected_tests_set` because
    # pytest never collects a test from a conftest -- handing one to a runner
    # selects nothing -- while reaching one is exactly the signal that fixture
    # consumers are invisible. So it belongs in the warning, not in the list a
    # caller pipes to `pytest`.
    conftests_reached: set[str] = set()

    def _record_test_path(path: str) -> None:
        if path.rsplit("/", 1)[-1] == "conftest.py":
            conftests_reached.add(path)
        else:
            affected_tests_set.add(path)

    for rel_path, line_spans in spans.items():
        rel_norm = rel_path.replace("\\", "/")
        if is_test_path(rel_norm):
            _record_test_path(rel_norm)

        sym_nodes = [
            n
            for n in index.nodes.values()
            if n.get("type") == "symbol" and (n.get("path") or "").replace("\\", "/") == rel_norm
        ]

        if line_spans:
            matched_any = False
            for s in sym_nodes:
                s_start = s.get("start_line")
                s_end = s.get("end_line")
                if isinstance(s_start, int) and isinstance(s_end, int):
                    if any(
                        not (span_end < s_start or span_start > s_end)
                        for span_start, span_end in line_spans
                    ):
                        modified_sym_ids.add(s["id"])
                        matched_any = True
            if not matched_any and sym_nodes:
                for s in sym_nodes:
                    modified_sym_ids.add(s["id"])
        else:
            for s in sym_nodes:
                modified_sym_ids.add(s["id"])

    reverse_edges: dict[str, list[Record]] = defaultdict(list)
    for e in index.edges:
        reverse_edges[e["dst"]].append(e)

    visited_nodes: set[str] = set(modified_sym_ids)
    impacted_callers_set: set[str] = set()

    frontier = list(modified_sym_ids)
    hop = 0
    pruned_low_confidence = 0
    shadowed_builtin_skipped = 0

    # `visited_nodes` is the blast radius -- everything the walk touched, which
    # is what the risk numbers are counted from. `expanded` is the separate
    # question of whether a node's own callers have been looked at. They have to
    # be separate sets: the `TESTS` branch used to mark a node visited without
    # ever expanding it, and the `CALLS` branch then skipped it as already seen,
    # so a helper module under `tests/` became a dead end and the real tests
    # calling it were never found -- while the frontier still drained, so
    # nothing reported the answer as incomplete.
    expanded: set[str] = set(modified_sym_ids)

    def _note(src: str) -> None:
        """Record `src`, and queue its own callers if they are still unexplored."""
        visited_nodes.add(src)
        src_node = index.store.node(src) or {}
        src_path = (src_node.get("path") or "").replace("\\", "/")
        if not src_path and src.startswith("sym:"):
            src_path = src[4:].split("::")[0].replace("\\", "/")

        if src_path and is_test_path(src_path):
            _record_test_path(src_path)
            # Deliberately not a sink. `is_test_path` is true of every module
            # under `tests/`, including `conftest.py`, fixtures and assertion
            # helpers that define no test of their own -- for those the real
            # tests are the callers, one hop further out.
        else:
            impacted_callers_set.add(src)
        if src not in expanded:
            expanded.add(src)
            next_frontier.append(src)

    while frontier and hop < max_hops:
        hop += 1
        next_frontier: list[str] = []
        for u in frontier:
            for e in reverse_edges.get(u, []):
                etype = e.get("type")
                src = e.get("src")
                if not src:
                    continue

                if etype == "TESTS":
                    _note(src)

                elif etype in ("CALLS", "INHERITS", "ROUTES_TO", "USES_FIXTURE"):
                    if etype == "CALLS":
                        # `shadowed_builtin` is dropped unconditionally, and it
                        # is not the same judgement as the confidence floor. An
                        # ambiguous edge is a *candidate* -- one of n real
                        # definitions that share the name, and the call went to
                        # one of them. A shadowed-builtin edge is a
                        # probably-wrong edge: the call almost certainly went to
                        # the builtin, and the user symbol merely shares its
                        # name. Following them made a two-line edit to a
                        # function named `len` select every test in the repo
                        # that calls `len()`, which is the feature failing in
                        # the other direction.
                        if e.get("shadowed_builtin") and not follow_shadowed_builtins:
                            shadowed_builtin_skipped += 1
                            continue
                        if min_confidence > 0:
                            try:
                                conf = float(e.get("confidence", 1.0))
                            except (TypeError, ValueError):
                                conf = 1.0
                            if conf < min_confidence or e.get("untyped_receiver"):
                                pruned_low_confidence += 1
                                continue

                    if src not in visited_nodes or src not in expanded:
                        _note(src)

            u_node = index.store.node(u) or {}
            u_path = (u_node.get("path") or "").replace("\\", "/")
            if u_path:
                file_id = f"file:{u_path}"
                visited_nodes.add(file_id)
                for e in reverse_edges.get(file_id, []):
                    if e.get("type") == "IMPORTS":
                        imp_src = e.get("src", "")
                        imp_path = (index.store.node(imp_src) or {}).get(
                            "path"
                        ) or imp_src.removeprefix("file:")
                        imp_path = imp_path.replace("\\", "/")
                        if imp_path and is_test_path(imp_path):
                            _record_test_path(imp_path)
                            visited_nodes.add(imp_src)

        frontier = next_frontier

    for rel_path in spans:
        rel_norm = rel_path.replace("\\", "/")
        file_id = f"file:{rel_norm}"
        visited_nodes.add(file_id)
        for e in reverse_edges.get(file_id, []):
            if e.get("type") == "IMPORTS":
                imp_src = e.get("src", "")
                imp_path = (index.store.node(imp_src) or {}).get("path") or imp_src.removeprefix(
                    "file:"
                )
                imp_path = imp_path.replace("\\", "/")
                if imp_path and is_test_path(imp_path):
                    _record_test_path(imp_path)
                    visited_nodes.add(imp_src)

    blast_radius = len(visited_nodes)
    risk_score = round(min(1.0, len(impacted_callers_set) * 0.08 + blast_radius * 0.02), 2)
    high_risk = bool(blast_radius >= 20 or len(impacted_callers_set) >= 8)

    # Say so when the subset is not provably complete, rather than returning a
    # short list that reads exactly like a complete one. A caller that selects
    # tests from `affected_tests` must treat `truncated` as "run everything".
    reasons: list[str] = []
    if frontier:
        reasons.append(
            f"the reverse walk still had {len(frontier)} node(s) to expand at the "
            f"max_hops={max_hops} limit"
        )
    if pruned_low_confidence:
        reasons.append(
            f"{pruned_low_confidence} ambiguous CALLS edge(s) were pruned by "
            f"min_confidence={min_confidence}"
        )
    if shadowed_builtin_skipped:
        reasons.append(
            f"{shadowed_builtin_skipped} CALLS edge(s) were skipped as calls to a "
            "shadowed builtin, which is a heuristic"
        )
    # Reaching a `conftest.py` means the walk is standing on pytest's fixture
    # mechanism, and that is the one incompleteness no bookkeeping here can
    # measure: a test that *receives* a fixture never calls it by name, so there
    # is no edge to follow and the consumers are invisible. The walk finishes
    # with an empty frontier and would otherwise report a complete answer whose
    # only entry is the conftest itself.
    #
    # Conditioned on actually having reached one, rather than on the repository
    # merely containing fixtures -- flagging every run would make `truncated`
    # noise, which is the failure the flag exists to avoid.
    conftests = sorted(conftests_reached)
    if conftests:
        fixture_tracking_active = any(e.get("type") == "USES_FIXTURE" for e in index.edges) or any(
            bool(n.get("is_fixture")) for n in index.nodes.values()
        )

        if not fixture_tracking_active:
            reasons.append(
                f"the walk reached {', '.join(conftests)}, and a test that consumes a fixture by "
                "injection rather than by calling it has no call edge to follow, so such tests "
                "cannot be detected"
            )
        else:
            conftest_set = set(conftests)
            reachable_conftest_syms = [
                s
                for s in visited_nodes
                if s.startswith("sym:")
                and any(
                    (index.store.node(s) or {})
                    .get("path", s[4:].split("::")[0])
                    .replace("\\", "/")
                    .endswith(cf)
                    for cf in conftest_set
                )
            ]

            reachable_fixtures = [
                s
                for s in reachable_conftest_syms
                if (index.store.node(s) or {}).get("is_fixture")
                or any(e.get("type") == "USES_FIXTURE" for e in reverse_edges.get(s, []))
            ]

            if not reachable_fixtures:
                reasons.append(
                    f"the walk reached {', '.join(conftests)}, but no fixture consumers could be identified"
                )
            else:
                unconsumed = []
                for fix in reachable_fixtures:
                    incoming_fixture_edges = [
                        e for e in reverse_edges.get(fix, []) if e.get("type") == "USES_FIXTURE"
                    ]
                    if not incoming_fixture_edges:
                        unconsumed.append(_format_sym(fix))
                if unconsumed:
                    reasons.append(
                        f"the walk reached {', '.join(conftests)}, but unconsumed fixture(s) "
                        f"({', '.join(sorted(unconsumed))}) have no identified test consumers"
                    )

    return {
        "modified_symbols": sorted({_format_sym(nid) for nid in modified_sym_ids}),
        "impacted_callers": sorted({_format_sym(nid) for nid in impacted_callers_set}),
        "affected_tests": sorted(affected_tests_set),
        "blast_radius_nodes": blast_radius,
        "risk_score": risk_score,
        "high_risk": high_risk,
        "truncated": bool(reasons),
        "incomplete_reason": "; ".join(reasons),
        "shadowed_builtin_skipped": shadowed_builtin_skipped,
        # Reported separately from `affected_tests`, which stays a list a caller
        # can hand to `pytest` unfiltered.
        "conftests_reached": conftests,
    }


def render_pr_summary_text(result: dict[str, Any]) -> str:
    """Format PR blast radius and affected tests as concise readable text."""
    mod_syms = result.get("modified_symbols", [])
    callers = result.get("impacted_callers", [])
    tests = result.get("affected_tests", [])
    nodes = result.get("blast_radius_nodes", 0)
    risk_score = result.get("risk_score", 0.0)
    high_risk = result.get("high_risk", False)
    risk_label = "High Risk" if high_risk else "Low Risk"

    lines = [
        "PR Semantic Blast Radius:",
        f"  Modified symbols ({len(mod_syms)}):",
        *(f"    + {s}" for s in mod_syms[:20]),
        *(["    ... and more"] if len(mod_syms) > 20 else []),
        f"  Impacted callers & routes ({len(callers)}):",
        *(f"    + {c}" for c in callers[:20]),
        *(["    ... and more"] if len(callers) > 20 else []),
        f"  Affected tests ({len(tests)}):",
        *(f"    * {t}" for t in tests),
        f"  Blast radius nodes: {nodes}",
        f"  Risk assessment: {risk_label} (score: {risk_score:g})",
    ]
    if result.get("truncated"):
        lines.append(
            "  WARNING: this test selection is incomplete -- "
            f"{result.get('incomplete_reason', '')}. Run the full suite."
        )
    return "\n".join(lines) + "\n"


def render_pr_summary_markdown(result: dict[str, Any]) -> str:
    """Format PR blast radius as a GitHub Actions markdown step summary card."""
    mod_syms = result.get("modified_symbols", [])
    callers = result.get("impacted_callers", [])
    tests = result.get("affected_tests", [])
    nodes = result.get("blast_radius_nodes", 0)
    high_risk = result.get("high_risk", False)
    risk_label = "🔴 High Risk" if high_risk else "🟢 Low Risk"

    lines = [
        "### 🎯 Semantic PR Blast Radius & Test Selection",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| **Directly Modified Symbols** | {len(mod_syms)} |",
        f"| **Impacted Callers & Routes** | {len(callers)} |",
        f"| **Affected Test Files** | {len(tests)} |",
        f"| **Blast Radius Nodes** | {nodes} |",
        f"| **Risk Assessment** | {risk_label} |",
        "",
    ]
    if result.get("truncated"):
        lines += [
            "> [!WARNING]",
            "> **This test selection is incomplete — run the full suite.**  ",
            f"> {result.get('incomplete_reason', '')}.",
            "",
        ]
    if mod_syms:
        lines.append("#### 📝 Directly Modified Symbols")
        for s in mod_syms:
            lines.append(f"- `{s}`")
        lines.append("")
    if tests:
        lines.append("#### 🧪 Selected Test Files to Run")
        for t in tests:
            lines.append(f"- `{t}`")
        lines.append("")
    if callers:
        lines.append("<details><summary><b>Impacted Callers & Routes</b></summary>\n")
        for c in callers:
            lines.append(f"- `{c}`")
        lines.append("\n</details>\n")
    return "\n".join(lines)

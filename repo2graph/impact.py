"""PR and diff impact analysis over repo2graph code graphs.

Compares a working branch, commit, or unified diff against a base ref (e.g. main)
to determine:
- What symbols changed (added, modified, deleted)
- Which public APIs may be affected
- Which callers, modules, and tests are impacted
- Which dependency paths cross the changed area
- Which changes look disconnected or suspicious
- Architectural blast radius and risk assessment with grounded evidence

Includes strict guardrails to report uncertainty and avoid claiming definite
runtime breakage when only static graph evidence exists.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .query import Index


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: list[int] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    status: str = "modified"  # "added", "modified", "deleted", "renamed"
    added_lines: set[int] = field(default_factory=set)
    deleted_lines_count: int = 0
    hunks: list[Hunk] = field(default_factory=list)


@dataclass
class SymbolChange:
    id: str
    name: str
    qualname: str
    kind: str
    path: str
    start_line: int
    end_line: int
    signature: str | None
    is_public: bool
    signature_changed: bool
    changed_lines_count: int
    change_type: str = "modified"  # "added", "modified", "deleted"


@dataclass
class ImpactedCaller:
    id: str
    name: str
    qualname: str
    kind: str
    path: str
    line: int | None
    target_symbol_id: str
    target_symbol_name: str
    depth: int
    confidence: float
    evidence: str | None
    is_test: bool


@dataclass
class ImpactedTest:
    test_file: str
    test_node_id: str | None
    test_name: str | None
    line: int | None
    target_symbol_id: str
    target_symbol_name: str
    confidence: float
    evidence: str | None


@dataclass
class DependencyPath:
    caller: str
    changed_symbol: str
    dependency: str
    path_str: str


@dataclass
class SuspiciousFinding:
    rule_id: str
    category: str
    severity: str  # "warning", "error", "note"
    path: str
    line: int
    title: str
    description: str


@dataclass
class ImpactReport:
    base_ref: str
    head_ref: str
    files_changed: list[FileDiff]
    symbols_changed: list[SymbolChange]
    public_apis_affected: list[SymbolChange]
    impacted_callers: list[ImpactedCaller]
    impacted_modules: list[str]
    impacted_tests: list[ImpactedTest]
    dependency_paths: list[DependencyPath]
    suspicious_findings: list[SuspiciousFinding]
    untested_public_apis: list[SymbolChange]
    blast_radius_score: int
    risk_level: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    guardrails: dict[str, Any] = field(default_factory=dict)


#: HTML comment `format_pr_comment` leads with, so a CI job can find the comment
#: it posted last time and update it in place. Changing it orphans every comment
#: already posted -- a second series of comments starts alongside the first.
PR_COMMENT_MARKER = "<!-- repo2graph-impact-comment -->"

#: Longest public-API list `format_pr_comment` will render. GitHub rejects an
#: issue comment body over 65536 characters, so every list in that renderer is
#: bounded; the full, uncapped report is the `markdown`/`json` format.
PR_COMMENT_MAX_APIS = 20

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIFF_GIT_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")


def parse_unified_diff(diff_text: str) -> dict[str, FileDiff]:
    """Parse a unified diff into structured FileDiff objects.

    Uses src.split("\\n") according to AGENTS.md text slicing rules.
    Extracts 1-indexed added/modified line numbers in new file coordinates.
    """
    files: dict[str, FileDiff] = {}
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None
    curr_new_line = 0

    lines = diff_text.split("\n")
    for raw_line in lines:
        line = raw_line.rstrip("\r")

        # Check for diff --git header
        m_git = DIFF_GIT_RE.match(line)
        if m_git:
            old_p, new_p = m_git.group(1), m_git.group(2)
            current_file = FileDiff(path=new_p, old_path=old_p if old_p != new_p else None)
            files[new_p] = current_file
            current_hunk = None
            continue

        if current_file is None:
            continue

        if line.startswith("new file mode"):
            current_file.status = "added"
            continue
        if line.startswith("deleted file mode"):
            current_file.status = "deleted"
            continue
        if line.startswith("similarity index") or line.startswith("rename to"):
            current_file.status = "renamed"
            continue

        if line.startswith("--- "):
            if line.startswith("--- /dev/null"):
                current_file.status = "added"
            continue
        if line.startswith("+++ "):
            if line.startswith("+++ /dev/null"):
                current_file.status = "deleted"
            else:
                path = line[4:].strip()
                if path.startswith("b/"):
                    path = path[2:]
                current_file.path = path
                files[path] = current_file
            continue

        m_hunk = HUNK_RE.match(line)
        if m_hunk:
            old_start = int(m_hunk.group(1))
            old_count = int(m_hunk.group(2)) if m_hunk.group(2) is not None else 1
            new_start = int(m_hunk.group(3))
            new_count = int(m_hunk.group(4)) if m_hunk.group(4) is not None else 1

            current_hunk = Hunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            current_file.hunks.append(current_hunk)
            curr_new_line = new_start
            continue

        if current_hunk is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current_file.added_lines.add(curr_new_line)
                current_hunk.added_lines.append(curr_new_line)
                curr_new_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                current_file.deleted_lines_count += 1
            else:
                curr_new_line += 1

    return files


def get_git_diff(repo_root: Path | str, base: str = "main", head: str | None = None) -> str:
    """Retrieve raw unified diff using git subprocess.

    Adheres strictly to AGENTS.md:
    - `-c core.quotepath=false`
    - bytes stdout decoded utf8 + surrogateescape
    - split("\\n"), never splitlines()
    - stdin DEVNULL, bounded timeout
    """
    root = str(repo_root)
    # Prefer three-dot diff (merge-base) if head is given
    cmd = ["git", "-c", "core.quotepath=false", "-C", root, "diff", "-U0"]
    if head:
        cmd.append(f"{base}...{head}")
    else:
        cmd.append(base)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"failed to run git diff: {exc}") from exc

    if proc.returncode != 0:
        # Fallback to two-dot diff if three-dot merge-base failed (e.g. shallow clone)
        cmd_fallback = ["git", "-c", "core.quotepath=false", "-C", root, "diff", "-U0", base]
        if head:
            cmd_fallback.append(head)
        try:
            proc = subprocess.run(
                cmd_fallback,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    if proc.returncode != 0:
        err = proc.stderr.decode("utf8", "surrogateescape").strip()
        raise RuntimeError(f"git diff failed with code {proc.returncode}: {err}")

    return proc.stdout.decode("utf8", "surrogateescape")


_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})
_TEST_BASENAME_SUFFIXES = (
    "_test.py",
    "_test.go",
    ".test.ts",
    ".test.js",
    ".test.tsx",
    ".test.jsx",
    ".spec.ts",
    ".spec.js",
    ".spec.tsx",
    ".spec.jsx",
)


def is_test_path(path: str) -> bool:
    """Identify whether a relative path represents test code.

    Matched per path component, never as a suffix of the whole path.
    `path.endswith("test.py")` -- what this used to do -- is true for
    `latest.py`, `fastest.py` and `manifest.py`, so those modules were
    classified as tests: excluded from `is_public_symbol`, credited as their own
    test coverage, and skipped by every rule that skips test files.
    """
    parts = path.replace("\\", "/").lower().split("/")
    if any(part in _TEST_DIR_NAMES or part.startswith("test_") for part in parts[:-1]):
        return True

    base = parts[-1]
    return base == "test.py" or base.startswith("test_") or base.endswith(_TEST_BASENAME_SUFFIXES)


def is_public_symbol(node: dict[str, Any]) -> bool:
    """Determine whether a symbol is part of the package or module's public surface."""
    name = node.get("name", "")
    path = node.get("path", "")
    lang = node.get("lang", "")
    vis = node.get("visibility")

    if vis in ("private", "protected", "internal"):
        return False
    if vis in ("public", "exported"):
        return True

    if is_test_path(path):
        return False

    # Python conventions: leading underscore (including __dunder__) is not public API
    if lang == "python" or path.endswith(".py"):
        if name.startswith("_"):
            return False
        return True

    # Go conventions: uppercase first rune is exported
    if lang == "go" or path.endswith(".go"):
        return bool(name and name[0].isupper())

    # TypeScript / JavaScript conventions
    if lang in ("typescript", "javascript", "tsx") or path.endswith((".ts", ".js", ".tsx")):
        if name.startswith("_"):
            return False
        return True

    # Java / Kotlin / default conventions
    if name.startswith("_"):
        return False
    return True


def analyze_diff_impact(
    index: Index,
    diff: str | dict[str, FileDiff],
    base: str = "main",
    head: str = "HEAD",
    max_depth: int = 2,
    min_confidence: float | None = None,
    exclude_secrets: bool = False,
) -> ImpactReport:
    """Analyze the impact of a git diff over a built repo2graph index."""
    if isinstance(diff, str):
        file_diffs = parse_unified_diff(diff)
    else:
        file_diffs = dict(diff)

    def _fallback_sec(p: str) -> bool:
        return False

    raw_sec = getattr(index, "_is_secret_path", None)
    is_sec = raw_sec if callable(raw_sec) else _fallback_sec

    if exclude_secrets:
        file_diffs = {p: fd for p, fd in file_diffs.items() if not is_sec(p)}

    symbols_changed: list[SymbolChange] = []
    public_apis_affected: list[SymbolChange] = []
    changed_sym_ids: set[str] = set()
    changed_file_paths: set[str] = set(file_diffs.keys())

    # Every path the parser produced at least one symbol for. Collected in the
    # same pass as the symbol match below so R2G-IMP-001 can tell "this file has
    # no relationship to the rest of the diff" apart from "the graph has nothing
    # to say about this file at all" -- see _relatable() below.
    symbol_paths: set[str] = set()

    # 1. Match diff line ranges against symbol nodes in the index
    for node_id, node in index.nodes.items():
        if node.get("type") != "symbol":
            continue
        path = node.get("path", "")
        symbol_paths.add(path)
        if path not in file_diffs:
            continue

        fd = file_diffs[path]
        start_line = node.get("start_line", 0)
        end_line = node.get("end_line", 0)

        # Added file: all symbols are newly added
        if fd.status == "added":
            is_pub = is_public_symbol(node)
            qualname = str(node.get("qualname") or node.get("name") or "")
            sc = SymbolChange(
                id=node_id,
                name=str(node.get("name") or ""),
                qualname=qualname,
                kind=str(node.get("kind") or "symbol"),
                path=path,
                start_line=start_line,
                end_line=end_line,
                signature=node.get("signature"),
                is_public=is_pub,
                signature_changed=True,
                changed_lines_count=max(1, end_line - start_line + 1),
                change_type="added",
            )
            symbols_changed.append(sc)
            changed_sym_ids.add(node_id)
            if is_pub:
                public_apis_affected.append(sc)
            continue

        # Check intersection with added/modified lines
        intersecting = [l for l in fd.added_lines if start_line <= l <= end_line]
        if intersecting:
            is_pub = is_public_symbol(node)
            sig_changed = start_line in intersecting
            qualname = str(node.get("qualname") or node.get("name") or "")
            sc = SymbolChange(
                id=node_id,
                name=str(node.get("name") or ""),
                qualname=qualname,
                kind=str(node.get("kind") or "symbol"),
                path=path,
                start_line=start_line,
                end_line=end_line,
                signature=node.get("signature"),
                is_public=is_pub,
                signature_changed=sig_changed,
                changed_lines_count=len(intersecting),
                change_type="modified",
            )
            symbols_changed.append(sc)
            changed_sym_ids.add(node_id)
            if is_pub:
                public_apis_affected.append(sc)

    # 2. Traverse callers and test reachability
    impacted_callers: list[ImpactedCaller] = []
    impacted_tests: list[ImpactedTest] = []
    seen_callers: set[tuple[str, str]] = set()  # (caller_id, target_symbol_id)
    seen_tests: set[tuple[str, str]] = set()

    for sc in symbols_changed:
        sid = sc.id
        visited_nodes: set[str] = {sid}
        # queue item: (current_nid, depth, origin_confidence)
        queue: list[tuple[str, int, float]] = [(sid, 1, 1.0)]

        while queue:
            curr_id, depth, parent_conf = queue.pop(0)
            if depth > max_depth:
                continue

            for neighbor_id, etype, direction, edge in index.adj.get(curr_id, []):
                if etype != "CALLS" or direction != "in":
                    continue

                conf = float(edge.get("confidence") or 1.0) * parent_conf
                if min_confidence is not None and conf < min_confidence:
                    continue

                caller_node = index.nodes.get(neighbor_id)
                if not caller_node:
                    continue

                caller_path = caller_node.get("path", "")
                if exclude_secrets and is_sec(caller_path):
                    continue
                evidence = edge.get("evidence")
                line_no = caller_node.get("start_line")
                if evidence and ":" in evidence:
                    try:
                        line_no = int(evidence.split(":")[-1])
                    except ValueError:
                        pass

                is_test = is_test_path(caller_path)
                caller_key = (neighbor_id, sc.id)

                if caller_key not in seen_callers:
                    seen_callers.add(caller_key)
                    c_name = str(caller_node.get("name") or neighbor_id)
                    c_qualname = str(caller_node.get("qualname") or c_name)
                    c_kind = str(caller_node.get("kind") or "symbol")
                    ic = ImpactedCaller(
                        id=neighbor_id,
                        name=c_name,
                        qualname=c_qualname,
                        kind=c_kind,
                        path=caller_path,
                        line=line_no,
                        target_symbol_id=sc.id,
                        target_symbol_name=sc.qualname,
                        depth=depth,
                        confidence=round(conf, 2),
                        evidence=evidence,
                        is_test=is_test,
                    )
                    impacted_callers.append(ic)

                    if is_test and (caller_path, sc.id) not in seen_tests:
                        seen_tests.add((caller_path, sc.id))
                        impacted_tests.append(
                            ImpactedTest(
                                test_file=caller_path,
                                test_node_id=neighbor_id,
                                test_name=caller_node.get("name"),
                                line=line_no,
                                target_symbol_id=sc.id,
                                target_symbol_name=sc.qualname,
                                confidence=round(conf, 2),
                                evidence=evidence,
                            )
                        )

                if neighbor_id not in visited_nodes and depth < max_depth:
                    visited_nodes.add(neighbor_id)
                    queue.append((neighbor_id, depth + 1, conf))

    # 3. Impacted dependent modules via IMPORTS
    impacted_modules_set: set[str] = set()
    for changed_path in changed_file_paths:
        fid = f"file:{changed_path}"
        for neighbor_id, etype, direction, edge in index.adj.get(fid, []):
            if etype == "IMPORTS" and direction == "in":
                importer_node = index.nodes.get(neighbor_id)
                if importer_node:
                    imp_path = importer_node.get("path", "")
                    if imp_path and imp_path != changed_path:
                        if exclude_secrets and is_sec(imp_path):
                            continue
                        impacted_modules_set.add(imp_path)
                        if is_test_path(imp_path) and (imp_path, fid) not in seen_tests:
                            seen_tests.add((imp_path, fid))
                            impacted_tests.append(
                                ImpactedTest(
                                    test_file=imp_path,
                                    test_node_id=neighbor_id,
                                    test_name=importer_node.get("name"),
                                    line=importer_node.get("start_line"),
                                    target_symbol_id=fid,
                                    target_symbol_name=changed_path,
                                    confidence=1.0,
                                    evidence=edge.get("evidence"),
                                )
                            )

    # 4. Dependency paths crossing changed area
    dependency_paths: list[DependencyPath] = []
    for sc in symbols_changed[:10]:
        # Upstream caller
        upstream = [c for c in impacted_callers if c.target_symbol_id == sc.id]
        # Downstream callee
        downstream = []
        for neighbor_id, etype, direction, _ in index.adj.get(sc.id, []):
            if (etype == "CALLS" or etype == "IMPORTS") and direction == "out":
                callee_node = index.nodes.get(neighbor_id)
                if callee_node and callee_node.get("path") != sc.path:
                    downstream.append(callee_node)

        if upstream and downstream:
            c = upstream[0]
            d = downstream[0]
            p_str = f"{c.path}::{c.name} -> {sc.path}::{sc.name} -> {d.get('path', '')}::{d.get('name', '')}"
            dependency_paths.append(
                DependencyPath(
                    caller=f"{c.path}::{c.name}",
                    changed_symbol=f"{sc.path}::{sc.name}",
                    dependency=f"{d.get('path', '')}::{d.get('name', '')}",
                    path_str=p_str,
                )
            )

    # 5. Suspicious / Disconnected change detection
    suspicious: list[SuspiciousFinding] = []

    # Rule R2G-IMP-001: Disconnected / Orphan Changes
    #
    # Only files the graph can actually relate are judged. "No graph
    # relationships connect this file to the other changes" is a falsifiable
    # claim for a parsed source file; for a workflow YAML, a lockfile or a
    # Markdown page it is true of every such file in every diff, and for a file
    # the index has never seen (an index built before the file was added) it is
    # an artifact of the index, not of the change. Each of those shapes produced
    # a code-scanning alert on PR #422 that named a perfectly ordinary edit.
    def _relatable(fpath: str) -> bool:
        if fpath in symbol_paths:
            return True
        return any(etype == "IMPORTS" for _, etype, _, _ in index.adj.get(f"file:{fpath}", []))

    relatable_paths = [p for p in sorted(changed_file_paths) if _relatable(p)]

    # >1 because the rule is about isolation *within* the diff: a single
    # relatable file has nothing in the change it could have been connected to.
    if len(relatable_paths) > 1:
        for fpath in relatable_paths:
            fid = f"file:{fpath}"
            # Check if this file has ANY edge connecting it to another changed file
            connected = False
            for neighbor_id, _, _, _ in index.adj.get(fid, []):
                n_node = index.nodes.get(neighbor_id)
                if (
                    n_node
                    and n_node.get("path") in changed_file_paths
                    and n_node.get("path") != fpath
                ):
                    connected = True
                    break
            # Also check if any symbol in this file connects to symbols in other changed files
            if not connected:
                syms_in_f = [s for s in symbols_changed if s.path == fpath]
                for s in syms_in_f:
                    for neighbor_id, _, _, _ in index.adj.get(s.id, []):
                        n_node = index.nodes.get(neighbor_id)
                        if (
                            n_node
                            and n_node.get("path") in changed_file_paths
                            and n_node.get("path") != fpath
                        ):
                            connected = True
                            break
                    if connected:
                        break

            if not connected and not is_test_path(fpath):
                suspicious.append(
                    SuspiciousFinding(
                        rule_id="R2G-IMP-001",
                        category="orphan_change",
                        severity="warning",
                        path=fpath,
                        line=1,
                        title=f"Disconnected modification in `{fpath}`",
                        description=(
                            f"`{fpath}` was modified alongside other files in this diff, but has no graph "
                            "relationships (calls, imports, or definitions) connecting it to the other changes."
                        ),
                    )
                )

    # Rule R2G-IMP-002: Untested Public APIs
    untested_public: list[SymbolChange] = []
    for pub in public_apis_affected:
        has_test = any(t.target_symbol_id == pub.id for t in impacted_tests)
        if not has_test:
            untested_public.append(pub)
            suspicious.append(
                SuspiciousFinding(
                    rule_id="R2G-IMP-002",
                    category="untested_public_api",
                    severity="warning",
                    path=pub.path,
                    line=pub.start_line,
                    title=f"Untested public API change: `{pub.qualname}`",
                    description=(
                        f"Public symbol `{pub.qualname}` in `{pub.path}:{pub.start_line}` was modified, "
                        "but no test caller was discovered exercising it in the repository graph."
                    ),
                )
            )

    # Rule R2G-IMP-003: High Blast Radius
    for sc in symbols_changed:
        # An *added* symbol cannot break a caller: nothing depended on it before
        # this diff, so its callers are all part of the same change and the
        # count measures how well the new code is wired in, not what the edit
        # puts at risk. A new module landing with its three consumers scored
        # "high blast radius" on every file of PR #422. Test helpers are skipped
        # for the reason R2G-IMP-001 skips them: a shared fixture with many
        # callers is how a suite is meant to look.
        if sc.change_type == "added" or is_test_path(sc.path):
            continue
        direct_callers = [
            c for c in impacted_callers if c.target_symbol_id == sc.id and c.depth == 1
        ]
        unique_caller_files = {c.path for c in direct_callers if c.path != sc.path}
        if len(direct_callers) >= 8 or len(unique_caller_files) >= 3:
            suspicious.append(
                SuspiciousFinding(
                    rule_id="R2G-IMP-003",
                    category="high_blast_radius",
                    severity="warning",
                    path=sc.path,
                    line=sc.start_line,
                    title=f"High blast radius: `{sc.qualname}` ({len(direct_callers)} callers across {len(unique_caller_files)} modules)",
                    description=(
                        f"Modifying `{sc.qualname}` carries broad structural impact with {len(direct_callers)} "
                        f"direct callers across {len(unique_caller_files)} separate modules."
                    ),
                )
            )

    # Rule R2G-IMP-004: Low-confidence call site
    for ic in impacted_callers:
        if ic.confidence < 0.7:
            suspicious.append(
                SuspiciousFinding(
                    rule_id="R2G-IMP-004",
                    category="ambiguous_call",
                    severity="note",
                    path=ic.path,
                    line=ic.line or 1,
                    title=f"Ambiguous call resolution from `{ic.qualname}`",
                    description=(
                        f"Call from `{ic.qualname}` to `{ic.target_symbol_name}` resolved with fractional "
                        f"confidence ({ic.confidence:.2f}), indicating potential name shadowing or dynamic dispatch."
                    ),
                )
            )

    # 6. Blast Radius Score and Risk Level
    direct_callers_count = sum(1 for c in impacted_callers if c.depth == 1)
    transitive_callers_count = sum(1 for c in impacted_callers if c.depth > 1)
    blast_radius = (
        (len(symbols_changed) * 2)
        + (direct_callers_count * 3)
        + (transitive_callers_count * 1)
        + (len(impacted_modules_set) * 2)
        + (len(untested_public) * 5)
        + (len(suspicious) * 3)
    )

    if (
        blast_radius >= 45
        or any(s.severity == "error" for s in suspicious)
        or (len(untested_public) >= 2 and direct_callers_count >= 5)
    ):
        risk_level = "CRITICAL"
    elif blast_radius >= 25 or len(public_apis_affected) >= 3 or direct_callers_count >= 8:
        risk_level = "HIGH"
    elif blast_radius >= 10 or len(symbols_changed) >= 2 or direct_callers_count >= 1:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    guardrails: dict[str, Any] = {
        "analysis_type": "static_ast_code_graph",
        "uncertainty_notice": (
            "Static analysis identifies structural reachability and potential call exposure. "
            "Dynamic dispatch (e.g. Python getattr, reflection, dependency injection, runtime decorators) "
            "and runtime conditionals may alter actual execution paths. Findings indicate potential impact "
            "and do not prove runtime breakage without running the test suite."
        ),
        "confidence_tiers": {
            "high": "Direct AST call resolution (confidence = 1.0)",
            "plausible": "Transitive reachability or ambiguous symbol names (confidence < 0.8)",
            "import_only": "Module-level import dependency without explicit symbol call site",
        },
    }

    # An index built on the *base* ref cannot contain a file the diff adds, and
    # its symbol line numbers are base-side while a diff's added_lines are
    # head-side -- so every added file reads as unreachable and every symbol
    # resolves against the wrong lines. That is a silent wrong answer, not an
    # error, so the report states its own coverage instead of implying none of
    # this happened. Build the index on the head commit being analyzed.
    unindexed = sorted(p for p in changed_file_paths if f"file:{p}" not in index.nodes)
    guardrails["index_coverage"] = {
        "changed_files": len(changed_file_paths),
        "files_absent_from_index": unindexed,
    }
    if unindexed:
        guardrails["stale_index_notice"] = (
            f"{len(unindexed)} of {len(changed_file_paths)} changed file(s) are absent from the "
            "index, so no caller, test or public-API finding can be reported for them. This is "
            "what an index built on a different commit than the analyzed head looks like: "
            "rebuild it on the head commit for accurate results."
        )

    return ImpactReport(
        base_ref=base,
        head_ref=head,
        files_changed=list(file_diffs.values()),
        symbols_changed=symbols_changed,
        public_apis_affected=public_apis_affected,
        impacted_callers=impacted_callers,
        impacted_modules=sorted(impacted_modules_set),
        impacted_tests=impacted_tests,
        dependency_paths=dependency_paths,
        suspicious_findings=suspicious,
        untested_public_apis=untested_public,
        blast_radius_score=blast_radius,
        risk_level=risk_level,
        guardrails=guardrails,
    )


# ---------- Formatters ----------


def format_json(report: ImpactReport) -> str:
    """Format impact report as human-inspectable, machine-readable JSON."""
    d: dict[str, Any] = {
        "base_ref": report.base_ref,
        "head_ref": report.head_ref,
        "risk_level": report.risk_level,
        "blast_radius_score": report.blast_radius_score,
        "metrics": {
            "files_changed_count": len(report.files_changed),
            "symbols_changed_count": len(report.symbols_changed),
            "public_apis_affected_count": len(report.public_apis_affected),
            "direct_callers_count": sum(1 for c in report.impacted_callers if c.depth == 1),
            "transitive_callers_count": sum(1 for c in report.impacted_callers if c.depth > 1),
            "impacted_modules_count": len(report.impacted_modules),
            "impacted_tests_count": len(report.impacted_tests),
            "untested_public_apis_count": len(report.untested_public_apis),
            "suspicious_findings_count": len(report.suspicious_findings),
        },
        "files_changed": [
            {
                "path": f.path,
                "old_path": f.old_path,
                "status": f.status,
                "added_lines": sorted(f.added_lines),
                "deleted_lines_count": f.deleted_lines_count,
            }
            for f in report.files_changed
        ],
        "symbols_changed": [asdict(s) for s in report.symbols_changed],
        "public_apis_affected": [asdict(s) for s in report.public_apis_affected],
        "impacted_callers": [asdict(c) for c in report.impacted_callers],
        "impacted_modules": report.impacted_modules,
        "impacted_tests": [asdict(t) for t in report.impacted_tests],
        "dependency_paths": [asdict(p) for p in report.dependency_paths],
        "suspicious_findings": [asdict(sf) for sf in report.suspicious_findings],
        "guardrails": report.guardrails,
    }
    return json.dumps(d, indent=2)


def format_markdown(report: ImpactReport) -> str:
    """Render full architectural impact report in GitHub-flavored Markdown."""
    risk_emojis = {
        "CRITICAL": "🛑 CRITICAL RISK",
        "HIGH": "🔴 HIGH RISK",
        "MEDIUM": "🟡 MEDIUM RISK",
        "LOW": "🟢 LOW RISK",
    }
    badge = risk_emojis.get(report.risk_level, report.risk_level)

    lines: list[str] = []
    lines.append(f"## 📐 Architectural PR Impact Report: `{report.base_ref}`...`{report.head_ref}`")
    lines.append("")
    lines.append(
        f"**Overall Assessment**: **{badge}** (Blast Radius Score: `{report.blast_radius_score}`)"
    )
    lines.append("")

    # Executive Summary Table
    lines.append("### 📊 Executive Summary")
    lines.append("")
    lines.append("| Metric | Count | Details |")
    lines.append("| :--- | :---: | :--- |")
    lines.append(
        f"| **Files Changed** | `{len(report.files_changed)}` | Diffs inspected across PR |"
    )
    lines.append(
        f"| **Symbols Changed** | `{len(report.symbols_changed)}` | Functions, classes, and methods modified |"
    )
    lines.append(
        f"| **Public APIs Affected** | `{len(report.public_apis_affected)}` | Exported / external surface changes |"
    )
    direct_callers = sum(1 for c in report.impacted_callers if c.depth == 1)
    transitive = sum(1 for c in report.impacted_callers if c.depth > 1)
    lines.append(
        f"| **Impacted Callers** | `{direct_callers}` direct / `{transitive}` transitive | Upstream callers reachable in graph |"
    )
    lines.append(
        f"| **Dependent Modules** | `{len(report.impacted_modules)}` | Modules importing changed files |"
    )
    lines.append(
        f"| **Impacted Tests** | `{len(report.impacted_tests)}` | Test files exercising changed code |"
    )
    lines.append(
        f"| **Suspicious Findings** | `{len(report.suspicious_findings)}` | Orphan changes, untested APIs, blast alerts |"
    )
    lines.append("")

    # Suspicious / Guardrail Alerts
    if report.suspicious_findings:
        lines.append("### ⚠️ Architectural & Risk Findings")
        lines.append("")
        for sf in report.suspicious_findings:
            sev_badge = (
                "🔴" if sf.severity == "error" else "🟡" if sf.severity == "warning" else "ℹ️"
            )
            lines.append(
                f"- {sev_badge} **[{sf.rule_id}] {sf.title}** ([cite: {sf.path}:{sf.line}])"
            )
            lines.append(f"  {sf.description}")
        lines.append("")

    # Public APIs Affected
    if report.public_apis_affected:
        lines.append("### 🌐 Affected Public APIs")
        lines.append("")
        lines.append("| Symbol | Kind | File:Line | Signature Changed | Direct Callers |")
        lines.append("| :--- | :--- | :--- | :---: | :---: |")
        for pub in report.public_apis_affected:
            sig_ch = "⚠️ Yes" if pub.signature_changed else "No"
            callers_count = sum(
                1 for c in report.impacted_callers if c.target_symbol_id == pub.id and c.depth == 1
            )
            lines.append(
                f"| `{pub.qualname}` | `{pub.kind}` | [cite: {pub.path}:{pub.start_line}] | {sig_ch} | `{callers_count}` |"
            )
        lines.append("")

    # Changed Symbols
    if report.symbols_changed:
        lines.append(
            "<details><summary><strong>🔍 All Changed Symbols ("
            + str(len(report.symbols_changed))
            + ")</strong></summary>\n"
        )
        lines.append("| Symbol | Change | Scope | Location | Lines Touched |")
        lines.append("| :--- | :---: | :---: | :--- | :---: |")
        for sc in report.symbols_changed:
            scope = "Public" if sc.is_public else "Internal"
            lines.append(
                f"| `{sc.qualname}` | `{sc.change_type}` | {scope} | [cite: {sc.path}:{sc.start_line}-{sc.end_line}] | `{sc.changed_lines_count}` |"
            )
        lines.append("\n</details>\n")

    # Impacted Tests
    lines.append("### 🧪 Impacted Test Coverage")
    lines.append("")
    if report.impacted_tests:
        lines.append("| Test File / Suite | Exercised Target | Confidence | Evidence Citation |")
        lines.append("| :--- | :--- | :---: | :--- |")
        for t in report.impacted_tests[:15]:
            ev = f"[cite: {t.evidence}]" if t.evidence else f"[cite: {t.test_file}:{t.line or 1}]"
            lines.append(
                f"| `{t.test_file}` | `{t.target_symbol_name}` | `{t.confidence:.2f}` | {ev} |"
            )
        if len(report.impacted_tests) > 15:
            lines.append(f"| *... and {len(report.impacted_tests) - 15} more test targets* | | | |")
    else:
        lines.append(
            "> ⚠️ **No test callers discovered for the modified symbols in the repository graph.** Consider adding regression coverage."
        )
    lines.append("")

    # Dependency Paths
    if report.dependency_paths:
        lines.append("### 🔗 Dependency Paths Crossing Changed Code")
        lines.append("")
        for p in report.dependency_paths[:8]:
            lines.append(f"- `{p.path_str}`")
        lines.append("")

    # Guardrails Notice
    stale = report.guardrails.get("stale_index_notice")
    if stale:
        lines.append("> [!WARNING]")
        lines.append("> **Index does not cover the whole diff**")
        lines.append(f"> {stale}")
        lines.append("")

    lines.append("> [!IMPORTANT]")
    lines.append("> **Static Analysis Guardrail & Uncertainty Notice**")
    lines.append(f"> {report.guardrails.get('uncertainty_notice', '')}")
    lines.append("")

    return "\n".join(lines)


def format_pr_comment(report: ImpactReport) -> str:
    """Render a compact, high-signal summary designed for GitHub PR comments."""
    risk_emojis = {
        "CRITICAL": "🛑 CRITICAL",
        "HIGH": "🔴 HIGH",
        "MEDIUM": "🟡 MEDIUM",
        "LOW": "🟢 LOW",
    }
    badge = risk_emojis.get(report.risk_level, report.risk_level)

    lines: list[str] = []
    # Marker, not decoration: the PR workflow finds its own previous comment by
    # this string and edits it, instead of posting one more comment per push.
    lines.append(PR_COMMENT_MARKER)
    lines.append(f"### 📐 repo2graph Impact Analysis: `{report.base_ref}`...`{report.head_ref}`")
    lines.append("")
    lines.append(
        f"**Risk Level**: **{badge}** | **Blast Radius Score**: `{report.blast_radius_score}`"
    )
    lines.append("")
    lines.append(
        f"- **Changed**: `{len(report.files_changed)}` files, `{len(report.symbols_changed)}` symbols "
        f"(`{len(report.public_apis_affected)}` public APIs)"
    )
    direct_callers = sum(1 for c in report.impacted_callers if c.depth == 1)
    lines.append(
        f"- **Reachability**: `{direct_callers}` direct callers across `{len(report.impacted_modules)}` modules"
    )
    test_count = len(report.impacted_tests)
    test_status = (
        f"`{test_count}` tests impacted" if test_count > 0 else "⚠️ **0 test callers found**"
    )
    lines.append(f"- **Test Coverage**: {test_status}")
    lines.append("")

    if report.suspicious_findings:
        lines.append("**Key Findings & Risk Alerts**:")
        for sf in report.suspicious_findings[:5]:
            sev = "🔴" if sf.severity == "error" else "🟡"
            lines.append(f"- {sev} **[{sf.rule_id}]**: {sf.title} ([cite: {sf.path}:{sf.line}])")
        lines.append("")

    if report.public_apis_affected:
        lines.append(
            "<details><summary><strong>Public APIs Affected ("
            + str(len(report.public_apis_affected))
            + ")</strong></summary>\n"
        )
        # Capped like the findings list above it. GitHub rejects a comment body
        # over 65536 characters outright, and this was the one unbounded section:
        # a refactor touching a few hundred public symbols would have made the
        # whole comment unpostable rather than merely long.
        for pub in report.public_apis_affected[:PR_COMMENT_MAX_APIS]:
            sig = " *(signature touched)*" if pub.signature_changed else ""
            lines.append(f"- `{pub.qualname}` in `{pub.path}:{pub.start_line}`{sig}")
        remaining = len(report.public_apis_affected) - PR_COMMENT_MAX_APIS
        if remaining > 0:
            lines.append(f"- *... and {remaining} more (see the full report artifact)*")
        lines.append("\n</details>\n")

    stale = report.guardrails.get("stale_index_notice")
    if stale:
        lines.append(f"> [!WARNING]\n> {stale}")
        lines.append("")

    lines.append("*(Report generated via `repo2graph impact` grounded in static code graph)*")
    return "\n".join(lines)


def format_sarif(report: ImpactReport) -> dict[str, Any]:
    """Generate SARIF v2.1.0 output for GitHub Code Scanning integration."""
    rules_dict = {
        "R2G-IMP-001": {
            "id": "R2G-IMP-001",
            "name": "DisconnectedOrphanChange",
            "shortDescription": {
                "text": "File changed without graph connection to other modifications"
            },
            "defaultConfiguration": {"level": "warning"},
        },
        "R2G-IMP-002": {
            "id": "R2G-IMP-002",
            "name": "UntestedPublicApiChange",
            "shortDescription": {"text": "Public API modified without test callers in repo graph"},
            "defaultConfiguration": {"level": "warning"},
        },
        "R2G-IMP-003": {
            "id": "R2G-IMP-003",
            "name": "HighBlastRadiusModification",
            "shortDescription": {"text": "Symbol modified with widespread caller reachability"},
            "defaultConfiguration": {"level": "warning"},
        },
        "R2G-IMP-004": {
            "id": "R2G-IMP-004",
            "name": "AmbiguousCallSite",
            "shortDescription": {"text": "Call site resolved with low static confidence"},
            "defaultConfiguration": {"level": "note"},
        },
    }

    results: list[dict[str, Any]] = []
    for sf in report.suspicious_findings:
        level = (
            "warning" if sf.severity == "warning" else "error" if sf.severity == "error" else "note"
        )
        results.append(
            {
                "ruleId": sf.rule_id,
                "level": level,
                "message": {"text": f"{sf.title}: {sf.description}"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": sf.path.replace("\\", "/")},
                            "region": {"startLine": max(1, sf.line)},
                        }
                    }
                ],
            }
        )

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "repo2graph-impact",
                        "informationUri": "https://github.com/Srinivasan-78/repo2graph",
                        "rules": list(rules_dict.values()),
                    }
                },
                "results": results,
            }
        ],
    }

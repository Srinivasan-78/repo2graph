#!/usr/bin/env python3
"""Regenerate examples/<id>/ from examples/repositories.yaml.

For each registry entry this:
  1. clones the pinned ref into a scratch workdir (network phase) — a partial,
     sparse clone when the entry scopes to specific subtrees, so bandwidth is
     spent only on what will actually be analyzed;
  2. records the exact commit SHA that was checked out;
  3. runs repo2graph.graph.build() + export.dump_all() against the local
     checkout only (analysis phase — no network access from this point on;
     see docs/benchmarks.md "Network behavior");
  4. runs a handful of real repo2graph queries against the built index to
     produce examples/<id>/flows/*.json, then discards the chunk text (source
     code) the index needed to answer them;
  5. copies only source-free artifacts (nodes.jsonl, edges.jsonl, overview.md,
     manifest.json, stats.json, graph.html) into examples/<id>/;
  6. validates the copied artifacts and writes examples/<id>/metadata.json +
     README.md;
  7. deletes the scratch workdir.

Never runs anything from the target repository itself (build scripts, tests,
package managers, git hooks, submodules) — see the module docstring in
`clone_scoped` for exactly what git operations run.

Usage:
    python scripts/generate_examples.py --repo django
    python scripts/generate_examples.py --repo kubernetes --repo linux
    python scripts/generate_examples.py --all
    python scripts/generate_examples.py --all --results benchmarks/results.json

Requires PyYAML (`pip install pyyaml`) — a dev-only tool dependency, not a
repo2graph runtime dependency.
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from repo2graph import __version__ as R2G_VERSION  # noqa: E402
from repo2graph.chunks import iter_chunks  # noqa: E402
from repo2graph.export import dump_all  # noqa: E402
from repo2graph.graph import build  # noqa: E402

GRAPH_SCHEMA_VERSION = "repo2graph/1"  # matches manifest.json "format"
REGISTRY = ROOT / "examples" / "repositories.yaml"
EXAMPLES_DIR = ROOT / "examples"
CLONE_TIMEOUT = 1800
GIT_TIMEOUT = 120

# Artifacts safe to commit: structure and metadata only, never chunk text
# (full source). See examples/ATTRIBUTIONS.md for why chunks.jsonl is excluded.
# nodes.jsonl/edges.jsonl are gzipped on the way in — JSON lines compress
# 4-9x (measured on these exact files) and a "committed generated data" file
# has no business being tens of MB of repeated field names.
COMMITTED_AGENT_FILES_GZ = ["nodes.jsonl", "edges.jsonl"]
COMMITTED_AGENT_FILES_PLAIN = ["manifest.json", "stats.json", "overview.md"]
COMMITTED_HUMAN_FILES = ["graph.html"]


def _gzip_copy(src: Path, dst: Path) -> None:
    with open(src, "rb") as fin, gzip.open(dst, "wb", compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout)


def _read_jsonl_maybe_gz(path: Path) -> list[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf8") as fh:
        return fh.read().splitlines()


def load_registry() -> list[dict]:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf8"))
    return data["repositories"]


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = GIT_TIMEOUT) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd, capture_output=True, timeout=timeout,
    )
    out = proc.stdout.decode("utf8", "surrogateescape")
    err = proc.stderr.decode("utf8", "surrogateescape")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
    return out


def clone_scoped(entry: dict, dest: Path) -> str:
    """Clone entry['url']@entry['ref'] into dest. Returns the checked-out commit SHA.

    Only ever runs: clone (--filter=blob:none, optionally --depth), sparse-checkout
    set, checkout. No submodules, no hooks (clone-triggered hooks do not exist;
    none are ever invoked), no build/test/install commands — that is the whole
    contract of "treat the target repository as untrusted input" from
    docs/benchmarks.md.
    """
    url, ref, depth = entry["url"], entry["ref"], entry["clone_depth"]
    clone_args = ["clone", "--quiet", "--filter=blob:none", "--no-checkout"]
    if depth:
        clone_args += ["--depth", str(depth)]
    clone_args += [url, str(dest)]
    _run_git(clone_args, timeout=CLONE_TIMEOUT)

    sparse_paths = entry.get("sparse_paths")
    if sparse_paths:
        _run_git(["sparse-checkout", "init", "--no-cone"], cwd=dest)
        (dest / ".git" / "info" / "sparse-checkout").write_text(
            "\n".join(sparse_paths) + "\n", encoding="utf8")
    _run_git(["checkout", "--quiet", ref], cwd=dest, timeout=CLONE_TIMEOUT)
    sha = _run_git(["rev-parse", "HEAD"], cwd=dest).strip()
    return sha


def run_queries(index_dir: Path, queries: list[str]) -> list[dict]:
    """Real repo2graph.query.Index.retrieve() calls against the just-built index.

    Runs while the index (including chunks.jsonl) still exists in the scratch
    workdir. Each flow keeps only the fields a citation needs — node id, path,
    line range, symbol name and the reason it was pulled in — never the chunk's
    `text`, so no source excerpt survives into the committed flow file.
    """
    from repo2graph.query import Index

    idx = Index(index_dir)
    flows = []
    for q in queries:
        picked = idx.retrieve(q, k=6, hops=1, budget_chars=8000)
        results = [
            {"node_id": c.get("node_id"), "path": c.get("path"), "lang": c.get("lang"),
             "kind": c.get("kind"), "name": c.get("qualname") or c.get("name"),
             "start_line": c.get("start_line"), "end_line": c.get("end_line"),
             "why": c.get("why"), "score": c.get("score")}
            for c in picked
        ]  # deliberately excludes `text` — no source excerpt in the committed flow
        flows.append({"query": q, "results": results})
    return flows


def validate_example(repo_dir: Path, meta: dict, workdir: Path) -> list[str]:
    """Section-29-style checks. Returns a list of problems (empty = clean)."""
    problems = []
    nodes_path = repo_dir / "nodes.jsonl.gz"
    edges_path = repo_dir / "edges.jsonl.gz"
    node_ids = set()
    for i, line in enumerate(_read_jsonl_maybe_gz(nodes_path)):
        if not line.strip():
            continue
        try:
            n = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"nodes.jsonl line {i}: invalid JSON ({e})")
            continue
        if n["id"] in node_ids:
            problems.append(f"nodes.jsonl: duplicate node id {n['id']!r}")
        node_ids.add(n["id"])
    edge_count = 0
    for i, line in enumerate(_read_jsonl_maybe_gz(edges_path)):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError as ex:
            problems.append(f"edges.jsonl line {i}: invalid JSON ({ex})")
            continue
        edge_count += 1
        if e["src"] not in node_ids:
            problems.append(f"edges.jsonl: dangling src {e['src']!r}")
        if e["dst"] not in node_ids:
            problems.append(f"edges.jsonl: dangling dst {e['dst']!r}")

    if meta["nodes"] != len(node_ids):
        problems.append(f"metadata nodes={meta['nodes']} != actual {len(node_ids)}")
    if meta["edges"] != edge_count:
        problems.append(f"metadata edges={meta['edges']} != actual {edge_count}")

    # No absolute path from *this machine's* clone/build scratch dir, and no
    # real home directory, leaked into any committed file. Deliberately not a
    # generic "C:\\" / "/home/" / "AppData" substring check: source code that
    # legitimately handles filesystem paths (e.g. vscode's own path.ts) says
    # things like "C:\\Windows" in docstrings and test fixtures, and a bare
    # substring check flags that real content as a false positive.
    import os as _os
    suspicious = [str(workdir), str(workdir.resolve())]
    home = _os.path.expanduser("~")
    if home and home not in ("~", "/"):
        suspicious.append(home)
    for f in list(repo_dir.glob("*")) + list(repo_dir.glob("flows/*")):
        if not f.is_file():
            continue
        if f.suffix == ".gz":
            text = "\n".join(_read_jsonl_maybe_gz(f))
        else:
            text = f.read_text(encoding="utf8", errors="replace")
        for s in suspicious:
            if s and s in text:
                problems.append(f"{f.relative_to(repo_dir)}: contains this machine's scratch/home path {s!r}")
    return problems


def _n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x)


def write_readme(entry: dict, meta: dict, repo_dir: Path) -> None:
    lang_table = "\n".join(f"- {lang}" for lang in entry["language_focus"])
    query_list = "\n".join(f"- {q}" for q in entry["queries"])
    scope_note = (
        "The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing."
        if entry["scope"] == "full" else
        "Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, "
        "not the whole repository) — see [docs/limitations.md](../../docs/limitations.md#extreme-scale)."
    )
    include_block = ""
    if entry.get("include"):
        paths = "\n".join(f"- `{p}`" for p in entry["include"])
        include_block = f"\n**Indexed paths:**\n{paths}\n"
    max_files = entry.get("max_files", 0)
    files_indexed = meta["stats"].get("files")
    if max_files and files_indexed == max_files:
        include_block += (
            f"\n**File cap reached:** the indexed paths above contain more than "
            f"`max_files: {max_files}` files; discovery is truncated at that count "
            f"(deterministic — `git ls-files` order — so re-running gets the same "
            f"{max_files} files for the same commit, not a random sample). This graph is "
            f"therefore a subset of even the scoped paths, not their complete contents.\n"
        )

    readme = f"""# {entry['name']} graph example

## Repository

[{entry['url']}]({entry['url']})

## Revision

Commit `{meta['commit']}` on `{entry['ref']}`, analyzed {meta['generated_at']}.

## Why this repository?

{entry['why'].strip()}

## Why this scope

{scope_note}
{include_block}
## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | {_n(meta['stats'].get('files', 'n/a'))} |
| Files parsed (code) | {_n(meta['stats'].get('parsed', 'n/a'))} |
| Parse errors | {_n(meta['stats'].get('parse_errors', 0))} |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | {_n(meta['nodes'])} |
| Edges | {_n(meta['edges'])} |
| Symbols (functions) | {_n(meta['stats'].get('symbol:function', 0))} |
| Symbols (classes) | {_n(meta['stats'].get('symbol:class', 0))} |
| CALLS edges | {_n(meta['stats'].get('edge:CALLS', 0))} |
| CALLS_EXTERNAL edges | {_n(meta['stats'].get('edge:CALLS_EXTERNAL', 0))} |
| IMPORTS edges | {_n(meta['stats'].get('edge:IMPORTS', 0))} |
| INHERITS edges | {_n(meta['stats'].get('edge:INHERITS', 0))} |
| DEFINES edges | {_n(meta['stats'].get('edge:DEFINES', 0))} |
| Ambiguous calls (name matched >1 candidate) | {_n(meta['stats'].get('ambiguous_calls', 0))} |
| Entrypoints | {_n(meta['stats'].get('entrypoints', 0))} |

## Supported languages

{lang_table}

## Example queries

These are real `repo2graph query` runs against this index (see `flows/`), not invented text:

{query_list}

`flows/` holds each query's real results as citations (node id, path, line range, why it matched)
with the source text stripped out. To run these queries yourself against a live, queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none {entry['url']} /tmp/{entry['id']}
cd /tmp/{entry['id']} && git checkout {meta['commit']}
repo2graph build . -o .r2g{(" --include " + " ".join(entry["include"])) if entry.get("include") else ""}
repo2graph query "{entry['queries'][0]}" -o .r2g
```

## Generated graph

- `nodes.jsonl.gz` / `edges.jsonl.gz` — the graph structure (identifiers, paths, line ranges; no
  source text), gzipped — JSON lines compress 4-9x and there is no reason to commit that redundancy
  raw; `gunzip -k nodes.jsonl.gz` to read it
- `graph.html` — the interactive map (self-contained, opens in any browser, no network needed), capped
  to the {meta.get('viz_nodes', 300)} best-connected nodes
- `overview.md` — the prose repo map: languages, most depended-on files, most called symbols
- `manifest.json` — what every field in the other files means
- `stats.json` — the raw counters above
- `flows/` — citation-only results of the example queries above

`chunks.jsonl` (the retrieval index, which embeds source text per symbol) is **not** committed —
see [ATTRIBUTIONS.md](../ATTRIBUTIONS.md#why-chunksjsonl-is-not-committed).

## Limitations

Call edges are matched by name, not by type — see
[docs/limitations.md](../../docs/limitations.md). Unresolved / ambiguous calls for this example:
{_n(meta['stats'].get('ambiguous_calls', 0))} out of {_n(meta['stats'].get('edge:CALLS', 0))} total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo {entry['id']}
```

This clones `{entry['url']}` at `{entry['ref']}` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/benchmarks.md](../../docs/benchmarks.md#staleness)).
"""
    (repo_dir / "README.md").write_text(readme, encoding="utf8", newline="\n")


def generate_one(entry: dict, results: dict) -> dict:
    repo_id = entry["id"]
    print(f"[{repo_id}] cloning {entry['url']}@{entry['ref']} ...", file=sys.stderr)
    workdir = Path(tempfile.mkdtemp(prefix=f"r2g-example-{repo_id}-"))
    t0 = time.monotonic()
    try:
        src = workdir / "src"
        sha = clone_scoped(entry, src)
        clone_seconds = round(time.monotonic() - t0, 1)
        print(f"[{repo_id}] cloned {sha[:12]} in {clone_seconds}s; analyzing (no network from here) ...",
              file=sys.stderr)

        t1 = time.monotonic()
        g = build(src, include=entry.get("include"), exclude=entry.get("exclude"),
                  git_history=entry.get("git_history", 0),
                  max_files=entry.get("max_files", 0), jobs=0)
        g.name = entry["id"]
        build_dir = workdir / "build"
        written, n_chunks = dump_all(g, iter_chunks(g), build_dir,
                                     {"jsonl", "overview", "html"}, viz_nodes=300)
        build_seconds = round(time.monotonic() - t1, 1)
        print(f"[{repo_id}] built {len(g.nodes)} nodes / {len(g.edges)} edges "
              f"in {build_seconds}s", file=sys.stderr)

        flows = run_queries(build_dir, entry["queries"])

        repo_dir = EXAMPLES_DIR / repo_id
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        repo_dir.mkdir(parents=True)
        (repo_dir / "flows").mkdir()
        for f in COMMITTED_AGENT_FILES_GZ:
            _gzip_copy(build_dir / "agent" / f, repo_dir / f"{f}.gz")
        for f in COMMITTED_AGENT_FILES_PLAIN:
            shutil.copy2(build_dir / "agent" / f, repo_dir / f)
        for f in COMMITTED_HUMAN_FILES:
            shutil.copy2(build_dir / "human" / f, repo_dir / f)
        for i, flow in enumerate(flows):
            slug = "".join(c if c.isalnum() else "-" for c in flow["query"].lower())[:40].strip("-")
            (repo_dir / "flows" / f"{i:02d}-{slug}.json").write_text(
                json.dumps(flow, indent=2) + "\n", encoding="utf8", newline="\n")

        meta = {
            "repository": entry["url"],
            "id": repo_id,
            "commit": sha,
            "branch": entry["ref"],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repo2graph_version": R2G_VERSION,
            "graph_schema_version": GRAPH_SCHEMA_VERSION,
            "languages": entry["language_focus"],
            "scope": entry["scope"],
            "include": entry.get("include"),
            "nodes": len(g.nodes),
            "edges": len(g.edges),
            "stats": dict(g.stats),
            "clone_seconds": clone_seconds,
            "build_seconds": build_seconds,
            "viz_nodes": 300,
        }
        (repo_dir / "metadata.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf8", newline="\n")
        write_readme(entry, meta, repo_dir)

        problems = validate_example(repo_dir, meta, workdir)
        if problems:
            raise SystemExit(f"[{repo_id}] validation FAILED:\n  " + "\n  ".join(problems))
        print(f"[{repo_id}] validated OK -> {repo_dir}", file=sys.stderr)

        results[repo_id] = {
            "repository": entry["url"], "commit": sha,
            "repo2graph_version": R2G_VERSION,
            "files": meta["stats"].get("files"), "nodes": meta["nodes"], "edges": meta["edges"],
            "clone_seconds": clone_seconds, "build_seconds": build_seconds,
            "generated_at": meta["generated_at"],
        }
        return meta
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[], help="repository id (repeatable)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--results", default=str(ROOT / "benchmarks" / "results.json"))
    args = ap.parse_args()

    registry = load_registry()
    if args.all:
        selected = registry
    elif args.repo:
        by_id = {e["id"]: e for e in registry}
        unknown = [r for r in args.repo if r not in by_id]
        if unknown:
            raise SystemExit(f"unknown repo id(s): {unknown}; known: {sorted(by_id)}")
        selected = [by_id[r] for r in args.repo]
    else:
        ap.print_help()
        return 0

    results_by_id = {}
    for entry in selected:
        generate_one(entry, results_by_id)

    results_path = Path(args.results)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if results_path.exists():
        existing = json.loads(results_path.read_text(encoding="utf8")).get("results", [])
    merged = {r["repository"]: r for r in existing}
    for repo_id, r in results_by_id.items():
        merged[r["repository"]] = r
    results_path.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "results": list(merged.values())}, indent=2) + "\n", encoding="utf8", newline="\n")
    print(f"wrote {results_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""A real LLM driving the MCP tools, scored on whether it found the evidence.

**What this measures that nothing else here does.** `scripts/agent_eval.py`
simulates an agent with a deterministic policy -- search, then read the
citations, then answer -- and it says so. That is the right way to measure
*retrieval*, because the policy is fixed and every change in the number comes
from the index. It is the wrong way to measure what this project actually
sells, which is a tool surface an LLM has to choose between: nine tools with
overlapping jobs, a token ceiling it cannot see, paged replies, and arguments
it has to pick. A deterministic policy cannot be confused by a tool
description, cannot page, and cannot waste a turn on the wrong tool. A model
can, and does.

So this runs the real loop: the model gets the real
`repo2graph.mcp.schemas.TOOL_SCHEMAS`, its tool calls go through the real
`repo2graph.mcp.tools.dispatch` against a real index, and the transcript is
scored on evidence the task file pins in advance.

**What it costs and why it is a script.** Every run spends provider tokens and
is non-deterministic, so it is not a CI gate and must never become one -- a
test that fails because a model had an off day teaches people to re-run tests.
It lives in `scripts/` beside the other benchmarks, is invoked by hand, and
writes a JSON artifact you commit next to the numbers it produced.

Usage:
    export ANTHROPIC_API_KEY=...          # or OPENAI_API_KEY
    python scripts/llm_agent_eval.py --tasks benchmarks/real/tasks_holdout_structural.json
    python scripts/llm_agent_eval.py --dry-run   # no API key, no requests

`--dry-run` exercises everything except the provider: the tool schemas, the
dispatch, the scoring and the report, with a scripted model that calls
`repo_search` once and answers. It is what the test suite uses, and it is how
to check a change here without spending anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repo2graph.answer import PROVIDER_MAP  # noqa: E402
from repo2graph.mcp.schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS  # noqa: E402
from repo2graph.mcp.tools import dispatch  # noqa: E402
from repo2graph.query import Index  # noqa: E402
from scripts.bench_real_repos import checkout, found  # noqa: E402

BENCH = ROOT / "benchmarks" / "real"

# A turn is one model response. Past this the loop stops and the task is a
# failure, which is the honest outcome: an agent that cannot find an answer in
# a dozen tool calls has not found it.
DEFAULT_MAX_TURNS = 12

# Characters of one tool result handed back to the model. The MCP server
# already clamps its replies to 12k tokens, so this is a second belt against a
# pathological result rather than the real bound.
MAX_TOOL_RESULT_CHARS = 24_000

HTTP_TIMEOUT = 120
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

SYSTEM = """\
You are answering a question about a code repository using only the tools provided.

Work in as few tool calls as you can. When you can answer, reply with prose that
names the file and the symbol that answers the question, and quote the lines you
relied on. Do not guess a path you have not seen in a tool result.

You cannot read the repository except through these tools."""

# Tools the eval exposes. `repo_cache_stats` and `repo_build_status` are
# operational rather than navigational -- they tell a client about the server,
# not about the code -- so a model offered them only has more ways to waste a
# turn, and the real question is how well the navigational surface works.
EVAL_TOOLS = (
    "repo_map",
    "repo_search",
    "repo_neighbours",
    "repo_find_symbol",
    "repo_read",
    "repo_path_between",
    "repo_blast_radius",
)


class Budget:
    """Tallies what a run spent, so a result can be read against its cost."""

    def __init__(self) -> None:
        self.calls = 0
        self.turns = 0
        self.tool_calls = 0
        self.in_tokens = 0
        self.out_tokens = 0

    def add_usage(self, usage: dict[str, Any]) -> None:
        # Providers name these differently; take whichever is present rather
        # than reporting zero for one of them.
        self.in_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        self.out_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)

    def as_dict(self) -> dict[str, int]:
        return {
            "provider_calls": self.calls,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "input_tokens": self.in_tokens,
            "output_tokens": self.out_tokens,
        }


# ---------------------------------------------------------------- providers


def _tool_specs(provider: str) -> list[dict[str, Any]]:
    """The MCP tool schemas in the provider's own tool-definition shape.

    Read out of `TOOL_SCHEMAS`/`TOOL_DESCRIPTIONS` rather than restated here:
    the point of the exercise is to measure the descriptions this project
    actually ships, so a copy that drifted would measure the copy.
    """
    out = []
    for name in EVAL_TOOLS:
        schema = TOOL_SCHEMAS[name]
        description = TOOL_DESCRIPTIONS[name]
        if provider == "anthropic":
            out.append({"name": name, "description": description, "input_schema": schema})
        else:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": schema,
                    },
                }
            )
    return out


def _post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf8", "replace")
        raise SystemExit(f"provider HTTP {exc.code}: {detail[:400]}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"provider request failed: {exc}") from None


class Provider:
    """One provider's tool-calling loop, in its own request/response shape.

    Deliberately not built on `answer.py`'s `_request`: that path is streaming
    and tool-less by design (it exists for `rag --answer`, which sends one pack
    and reads one answer back). Bending it to carry tools and a growing message
    list would complicate the one network path in the shipped package for the
    benefit of a benchmark script.
    """

    def __init__(self, name: str, key: str, model: str) -> None:
        self.name = name
        self.key = key
        self.model = model

    def start(self, question: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": question}]

    def call(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """One model turn. Returns `{text, tool_calls, usage}`."""
        if self.name == "anthropic":
            data = _post(
                "https://api.anthropic.com/v1/messages",
                {
                    "Content-Type": "application/json",
                    "x-api-key": self.key,
                    "anthropic-version": "2023-06-01",
                },
                {
                    "model": self.model,
                    "max_tokens": 2048,
                    "system": SYSTEM,
                    "tools": _tool_specs("anthropic"),
                    "messages": messages,
                },
            )
            text = "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
            calls = [
                {"id": b["id"], "name": b["name"], "arguments": b.get("input") or {}}
                for b in data.get("content", [])
                if b.get("type") == "tool_use"
            ]
            return {
                "text": text,
                "tool_calls": calls,
                "usage": data.get("usage") or {},
                "raw": data.get("content"),
            }

        data = _post(
            "https://api.openai.com/v1/chat/completions",
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
            {
                "model": self.model,
                "messages": [{"role": "system", "content": SYSTEM}, *messages],
                "tools": _tool_specs("openai"),
            },
        )
        choice = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for tc in choice.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append({"id": tc.get("id"), "name": fn.get("name"), "arguments": args})
        return {
            "text": choice.get("content") or "",
            "tool_calls": calls,
            "usage": data.get("usage") or {},
            "raw": choice,
        }

    def record(
        self,
        messages: list[dict[str, Any]],
        turn: dict[str, Any],
        results: list[tuple[str, str]],
    ) -> None:
        """Append the model's turn and the tool results to the transcript."""
        if self.name == "anthropic":
            messages.append({"role": "assistant", "content": turn["raw"]})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": cid, "content": out}
                        for cid, out in results
                    ],
                }
            )
        else:
            messages.append(turn["raw"])
            for cid, out in results:
                messages.append({"role": "tool", "tool_call_id": cid, "content": out})


class ScriptedProvider(Provider):
    """`--dry-run`: one `repo_search`, then an answer. No network.

    Exists so the loop, the dispatch wiring and the scoring can be tested and
    changed without an API key or a cent of spend -- and so a broken refactor
    here fails in the test suite rather than in someone's billing.
    """

    def __init__(self) -> None:
        super().__init__("scripted", "", "scripted-0")
        self._turn = 0

    def call(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self._turn += 1
        question = ""
        for m in messages:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                question = m["content"]
                break
        if self._turn == 1:
            return {
                "text": "",
                "tool_calls": [
                    {"id": "c1", "name": "repo_search", "arguments": {"query": question}}
                ],
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "raw": [],
            }
        last = ""
        for m in reversed(messages):
            if m.get("role") in ("user", "tool"):
                content = m.get("content")
                if isinstance(content, list):
                    last = " ".join(str(c.get("content", "")) for c in content)
                elif isinstance(content, str):
                    last = content
                if last:
                    break
        return {
            "text": f"Based on the search results: {last[:4000]}",
            "tool_calls": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "raw": [],
        }

    def record(
        self,
        messages: list[dict[str, Any]],
        turn: dict[str, Any],
        results: list[tuple[str, str]],
    ) -> None:
        for _cid, out in results:
            messages.append({"role": "tool", "content": out})


# ----------------------------------------------------------------- scoring


def cited_lines(transcript: str, root: Path) -> dict[str, set[int]]:
    """Source lines the model actually saw, per path, from `[cite: p:a-b]` heads.

    Scored on the citations in the transcript rather than on the model's prose:
    a `[cite:]` header is emitted by the tools themselves, so it says what the
    model was *shown*, which is what the index is responsible for. Whether the
    model then read it correctly is a different question and not this one.
    """
    import re

    out: dict[str, set[int]] = {}
    for path, start, end in re.findall(r"\[cite:\s*([^\s\]:]+):(\d+)-(\d+)\]", transcript):
        s, e = int(start), int(end)
        if e < s or e - s > 10_000:
            continue
        out.setdefault(path.replace("\\", "/"), set()).update(range(s, e + 1))
    return out


def run_task(
    task: dict[str, Any],
    index: Index,
    root: Path,
    provider: Provider,
    max_turns: int,
) -> dict[str, Any]:
    budget = Budget()
    messages = provider.start(task["query"])
    transcript_parts: list[str] = []
    answer = ""
    stopped = "answered"

    for _ in range(max_turns):
        turn = provider.call(messages)
        budget.calls += 1
        budget.turns += 1
        budget.add_usage(turn.get("usage") or {})
        if turn.get("text"):
            transcript_parts.append(turn["text"])
        calls = turn.get("tool_calls") or []
        if not calls:
            answer = turn.get("text") or ""
            break
        results = []
        for call in calls:
            budget.tool_calls += 1
            name = call.get("name") or ""
            if name not in EVAL_TOOLS:
                out = f"error: unknown tool {name!r}"
            else:
                try:
                    out = dispatch(index, name, call.get("arguments") or {})
                except Exception as exc:  # noqa: BLE001 - a tool error is data
                    # A refused argument is a legitimate outcome the model has
                    # to recover from, so it is handed back rather than
                    # crashing the run.
                    out = f"error: {type(exc).__name__}: {exc}"
            out = out[:MAX_TOOL_RESULT_CHARS]
            transcript_parts.append(out)
            results.append((call.get("id") or "0", out))
        provider.record(messages, turn, results)
    else:
        stopped = "max_turns"

    transcript = "\n".join(transcript_parts)
    lines = cited_lines(transcript, root)
    hits = found(task["evidence"], lines)
    return {
        "id": task["id"],
        "repo": task["repo"],
        "query": task["query"],
        "solved": all(hits) and bool(hits),
        "evidence_found": sum(1 for h in hits if h),
        "evidence_total": len(hits),
        "stopped": stopped,
        "answer_chars": len(answer),
        **budget.as_dict(),
    }


# -------------------------------------------------------------------- main


def pick(provider: str | None, dry_run: bool) -> Provider:
    if dry_run:
        return ScriptedProvider()
    configured = [n for n, env in PROVIDER_MAP.items() if (os.environ.get(env) or "").strip()]
    supported = [n for n in configured if n in ("anthropic", "openai")]
    name = provider or (supported[0] if len(supported) == 1 else None)
    if name is None:
        raise SystemExit(
            "set ANTHROPIC_API_KEY or OPENAI_API_KEY (and pass --provider if both are set), "
            "or use --dry-run to exercise the loop without a provider"
        )
    if name not in ("anthropic", "openai"):
        raise SystemExit(f"{name} has no tool-calling path here; use anthropic or openai")
    key = (os.environ.get(PROVIDER_MAP[name]) or "").strip()
    if not key:
        raise SystemExit(f"{PROVIDER_MAP[name]} is not set")
    default = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini"}[name]
    return Provider(name, key, default)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default=str(BENCH / "tasks_holdout_structural.json"))
    ap.add_argument("--repos", default=str(BENCH / "repos.json"))
    ap.add_argument("--cache", default=str(BENCH / ".cache"))
    ap.add_argument("--out", default=None, help="write the per-task rows here as JSON")
    ap.add_argument("--provider", choices=["anthropic", "openai"], default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    ap.add_argument("--limit", type=int, default=0, help="only the first N tasks")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="scripted model, no API key and no requests; exercises the whole loop",
    )
    ap.add_argument("--index-root", default=None, help="index this tree instead of cloning repos")
    ap.add_argument(
        "--index-out",
        default=None,
        help="where --index-root writes its index (default: a temp directory)",
    )
    args = ap.parse_args(argv)

    provider = pick(args.provider, args.dry_run)
    if args.model:
        provider.model = args.model

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))["tasks"]
    if args.limit:
        tasks = tasks[: args.limit]

    # `--index-root` keeps this runnable (and testable) without the network:
    # every task is answered against one local tree.
    roots: dict[str, Path] = {}
    indexes: dict[str, Index] = {}
    if args.index_root:
        local = Path(args.index_root).resolve()
        # A temp directory, not `local.parent`: writing the index beside the
        # tree being indexed left a `.r2g-llmeval/` inside the repository on
        # every test run. `--index-out` is there for when you want to keep it.
        out = Path(args.index_out) if args.index_out else Path(tempfile.mkdtemp()) / "idx"
        from repo2graph.cli import main as cli_main

        cli_main(["build", str(local), "-o", str(out), "--formats", "jsonl"])
        for t in tasks:
            roots[t["repo"]] = local
            indexes[t["repo"]] = Index(out)
    else:
        repos = {
            r["name"]: r for r in json.loads(Path(args.repos).read_text(encoding="utf-8"))["repos"]
        }
        cache = Path(args.cache)
        cache.mkdir(parents=True, exist_ok=True)
        from repo2graph.cli import main as cli_main

        for name in sorted({t["repo"] for t in tasks}):
            repo = repos[name]
            checkout_dir = checkout(repo, cache)
            root = checkout_dir / repo.get("root", "")
            out = cache / f"{name}.r2g"
            if not (out / "agent" / "nodes.jsonl").exists():
                cli_main(["build", str(root), "-o", str(out), "--formats", "jsonl"])
            roots[name] = root
            indexes[name] = Index(out)

    started = time.monotonic()
    rows = [
        run_task(t, indexes[t["repo"]], roots[t["repo"]], provider, args.max_turns) for t in tasks
    ]
    elapsed = time.monotonic() - started

    solved = sum(1 for r in rows if r["solved"])
    report = {
        "provider": provider.name,
        "model": provider.model,
        "tasks": len(rows),
        "solved": solved,
        "success_rate": round(solved / len(rows), 3) if rows else 0.0,
        "mean_turns": round(sum(r["turns"] for r in rows) / len(rows), 2) if rows else 0.0,
        "mean_tool_calls": (
            round(sum(r["tool_calls"] for r in rows) / len(rows), 2) if rows else 0.0
        ),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "hit_max_turns": sum(1 for r in rows if r["stopped"] == "max_turns"),
        "wall_seconds": round(elapsed, 1),
        "rows": rows,
    }
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

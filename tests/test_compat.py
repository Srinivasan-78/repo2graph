"""The backward-compatibility contract: AC-1 .. AC-9, plus AC-34 / AC-35.

These are characterization tests. The golden files under `tests/golden/` were
captured from the baseline commit ff0e3ca (the tree this run started from), by
running::

    R2G_REGEN_GOLDEN=1 python -m pytest tests/test_compat.py -q

with an unmodified checkout. Regenerating them is therefore how you *create* a
golden, never how you make a red test go green: a characterization test that
fails means either the golden was captured against a dirty tree or the
implementation broke something it promised not to touch.

Score fields in query_json.json / rag_json.json / pack_context.json were
refreshed for #142 (corpus BM25 avgdl). Ranking and pack membership are
unchanged; do not treat that refresh as a license to regen other goldens.

Every test names the acceptance criterion it encodes as `# AC-n`.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (
    MINI_QUERY,
    REPO_ROOT,
    build_mini_index,
    golden_json,
    golden_text,
    write_golden_json,
    write_golden_text,
)
from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.query import Index, read_jsonl

REGEN = os.environ.get("R2G_REGEN_GOLDEN") == "1"

# The four optional dependencies that a bare `pip install repo2graph` does not
# bring in. Importing any of them from a core module breaks the promise the
# whole plan is built on, so AC-5/6/7 assert on sys.modules out-of-process.
OPTIONAL_MODULES = ("numpy", "sentence_transformers", "torch", "mcp")


def _subprocess_modules(import_stmt: str, watched=OPTIONAL_MODULES):
    """Import `import_stmt` in a clean interpreter; report which of `watched`
    ended up in sys.modules. Out-of-process so that a pytest plugin which
    happens to have imported numpy cannot mask a regression."""
    code = (
        f"import sys\n{import_stmt}\nprint(' '.join(m for m in {watched!r} if m in sys.modules))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, timeout=180
    )
    out = proc.stdout.decode("utf8", "replace")
    err = proc.stderr.decode("utf8", "replace")
    return proc.returncode, out.strip(), err.strip()


# ==========================================================================
# AC-1 / AC-2 / AC-9 -- the byte-for-byte characterization goldens
# ==========================================================================


def _capture(capsys, argv):
    main(argv)
    return capsys.readouterr().out


def test_ac1_query_output_is_byte_identical_to_baseline(mini_index, capsys):
    """AC-1: `repo2graph query` on a vector-less index is unchanged."""
    text = _capture(capsys, ["query", MINI_QUERY, "-o", str(mini_index)])
    assert "vectors.npy" not in {p.name for p in mini_index.rglob("*")}
    if REGEN:
        write_golden_text("query_default.txt", text)
    assert text == golden_text("query_default.txt")


def test_ac1_query_json_output_is_byte_identical_to_baseline(mini_index, capsys):
    """AC-1: the same for `query --format json` (the machine-readable form)."""
    text = _capture(capsys, ["query", MINI_QUERY, "-o", str(mini_index), "--format", "json"])
    if REGEN:
        write_golden_text("query_json.json", text)
    assert text == golden_text("query_json.json")


def test_ac2_rag_markdown_is_byte_identical_to_baseline(mini_index, capsys):
    """AC-2 (a): `repo2graph rag` markdown on a vector-less index is unchanged."""
    text = _capture(capsys, ["rag", MINI_QUERY, "-o", str(mini_index)])
    if REGEN:
        write_golden_text("rag_markdown.md", text)
    assert text == golden_text("rag_markdown.md")


def test_ac2_rag_json_differs_only_by_the_two_token_keys(mini_index, capsys):
    """AC-2 (b): the JSON form gains exactly `tokens_used` and `tokens_budget`
    and changes the value of nothing else."""
    text = _capture(capsys, ["rag", MINI_QUERY, "-o", str(mini_index), "--format", "json"])
    payload = json.loads(text)
    if REGEN:
        write_golden_text("rag_json.json", text)
        pytest.skip("regenerating goldens")
    baseline = golden_json("rag_json.json")
    added = set(payload) - set(baseline)
    assert added == {"tokens_used", "tokens_budget"}, added
    assert not set(baseline) - set(payload), set(baseline) - set(payload)
    for key in baseline:
        assert payload[key] == baseline[key], key


def test_ac9_pack_context_without_vectors_matches_the_baseline(mini_index):
    """AC-9: markdown, chunks, seeds, neighbors and truncated are unchanged
    when no vectors.npy exists and no embedder is supplied."""
    idx = Index(mini_index)
    keys = ("markdown", "chunks", "seeds", "neighbors", "truncated")
    got = {
        "default": {k: idx.pack_context(MINI_QUERY)[k] for k in keys},
        "unbounded": {k: idx.pack_context(MINI_QUERY, budget_chars=0)[k] for k in keys},
        "tight": {k: idx.pack_context(MINI_QUERY, budget_chars=1200)[k] for k in keys},
    }
    if REGEN:
        write_golden_json("pack_context.json", got)
    assert got == golden_json("pack_context.json")


def test_ac3_score_rrf_without_vectors_is_exactly_score(mini_index):
    """AC-3: list equality (order and values), not set equality."""
    idx = Index(mini_index)
    for query in (MINI_QUERY, "audit event journal", "no such token anywhere"):
        base = idx.score(query)
        fused = idx.score_rrf(query, vectors=None, embedder=None)
        assert fused == base, query
        assert fused is not None


# ==========================================================================
# AC-4 -- the CLI surface, enumerated from argparse rather than from prose
# ==========================================================================


class _CapturedParser(Exception):
    def __init__(self, parser):
        super().__init__("parser captured")
        self.parser = parser


def _describe_action(action) -> dict:
    """Everything about one argparse action a caller could depend on."""
    return {
        "class": type(action).__name__,
        "options": sorted(action.option_strings),
        "dest": action.dest,
        "default": repr(action.default),
        "nargs": repr(action.nargs),
        "required": bool(action.required),
        "type": getattr(action.type, "__name__", None) if action.type else None,
        "choices": sorted(map(str, action.choices)) if action.choices else None,
    }


def _cli_inventory(monkeypatch) -> dict:
    """{subcommand: {flag-or-positional: description}} for the whole CLI."""

    def fake_parse_args(self, args=None, namespace=None):
        raise _CapturedParser(self)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    try:
        main(["version"])
    except _CapturedParser as captured:
        root = captured.parser
    else:  # pragma: no cover - guard
        raise AssertionError("main() never called parse_args")

    inventory = {"<root>": {}}
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                inventory[name] = {
                    (" ".join(sorted(a.option_strings)) or a.dest): _describe_action(a)
                    for a in sub._actions
                }
        else:
            key = " ".join(sorted(action.option_strings)) or action.dest
            inventory["<root>"][key] = _describe_action(action)
    return inventory


def test_ac4_every_baseline_subcommand_and_flag_survives(monkeypatch):
    """AC-4: every baseline subcommand still parses and still accepts every
    flag it accepted, with the same default, type, nargs and choices. New
    subcommands and new flags are allowed; removals and changes are not."""
    inventory = _cli_inventory(monkeypatch)
    if REGEN:
        write_golden_json("cli_inventory.json", inventory)
    baseline = golden_json("cli_inventory.json")

    assert set(baseline) <= set(inventory), set(baseline) - set(inventory)
    for command, flags in baseline.items():
        got = inventory[command]
        missing = set(flags) - set(got)
        assert not missing, (command, missing)
        for flag, spec in flags.items():
            assert got[flag] == spec, (command, flag, got[flag], spec)


def test_ac4_baseline_subcommands_are_all_present(monkeypatch):
    """AC-4: the named set, spelled out, so a silently dropped alias is loud."""
    inventory = _cli_inventory(monkeypatch)
    expected = {"build", "github", "gh", "query", "rag", "map", "stats", "version"}
    assert expected <= set(inventory), expected - set(inventory)


# ==========================================================================
# AC-5 / AC-6 / AC-7 -- the zero-dependency import contract
# ==========================================================================


# AC-5/6/7: importing any of these must not drag an optional extra in. Each row
# is a fresh subprocess that imports one module and prints anything watched that
# got imported, so the body is the same three lines every time and only the
# import line differs. The acceptance criterion each row belongs to is in its id,
# because that is what a failure needs to name.
@pytest.mark.parametrize(
    "statement, watched",
    [
        pytest.param("import repo2graph.query", None, id="ac5-query"),
        # embed legitimately knows about the rag extra's names, so it is watched
        # for those three only -- `mcp` is not a dependency of embedding.
        pytest.param(
            "import repo2graph.embed",
            ("numpy", "sentence_transformers", "torch"),
            id="ac6-embed",
        ),
        # Only serve() may touch the SDK, so the module has to import bare.
        pytest.param("import repo2graph.mcp", None, id="ac7-mcp"),
        # AC-5 corollary: `repo2graph query` goes through cli, so the entry
        # point must stay as clean as the module it calls.
        pytest.param("import repo2graph.cli", None, id="ac5-cli-entry-point"),
    ],
)
def test_importing_a_module_pulls_in_no_optional_dependency(statement, watched):
    kwargs = {} if watched is None else {"watched": watched}
    rc, out, err = _subprocess_modules(statement, **kwargs)
    assert rc == 0, err
    assert out == "", out


# ==========================================================================
# AC-8 -- action.yml inputs and outputs
# ==========================================================================

ACTION_YML = REPO_ROOT / "action.yml"

_NAME_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
_FIELD_RE = re.compile(r"^    ([A-Za-z0-9_-]+):\s*(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_action_block(text: str, block: str) -> dict:
    """The `inputs:` or `outputs:` mapping of action.yml, without PyYAML.

    action.yml's two declaration blocks are a flat two-level mapping of plain
    scalars, so a deliberately small parser covers them exactly. It is
    cross-checked against PyYAML in test_ac8_hand_parser_agrees_with_pyyaml
    whenever PyYAML happens to be installed, and the suite does not require it
    -- `pip install repo2graph[dev]` is pytest + ruff and nothing else.
    """
    out, current = {}, None
    inside = False
    for line in text.split("\n"):  # never splitlines(): see AGENTS.md
        if line.rstrip() == f"{block}:":
            inside = True
            continue
        if not inside:
            continue
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            break  # back at column 0: block is over
        name = _NAME_RE.match(line)
        if name:
            current = {}
            out[name.group(1)] = current
            continue
        field = _FIELD_RE.match(line)
        if field and current is not None:
            current[field.group(1)] = _unquote(field.group(2))
    return out


def test_ac8_hand_parser_agrees_with_pyyaml():
    """AC-8 (guard): the hand parser is not quietly wrong."""
    yaml = pytest.importorskip("yaml", reason="PyYAML is not a test dependency")
    with open(ACTION_YML, encoding="utf8", newline="\n") as fh:
        text = fh.read()
    data = yaml.safe_load(text)
    for block in ("inputs", "outputs"):
        mine = parse_action_block(text, block)
        theirs = {k: {kk: str(vv) for kk, vv in v.items()} for k, v in data[block].items()}
        assert set(mine) == set(theirs), (block, set(mine) ^ set(theirs))
        for name, fields in theirs.items():
            for key, value in fields.items():
                # PyYAML types `required: false` as a bool and str()s it as
                # "False"; the hand parser keeps the source spelling.
                got = _unquote(str(mine[name].get(key))).lower()
                want = _unquote(value).lower()
                assert got == want, (block, name, key, got, want)


def test_ac8_action_inputs_and_outputs_keep_their_baseline_contract():
    """AC-8: every baseline input keeps its default and every baseline output
    keeps its `value` expression; any newly added input is optional and has a
    default, so an existing workflow that omits it behaves as it did."""
    with open(ACTION_YML, encoding="utf8", newline="\n") as fh:
        text = fh.read()
    inputs = parse_action_block(text, "inputs")
    outputs = parse_action_block(text, "outputs")
    if REGEN:
        write_golden_json("action_inputs.json", inputs)
        write_golden_json("action_outputs.json", outputs)

    base_inputs = golden_json("action_inputs.json")
    base_outputs = golden_json("action_outputs.json")

    missing = set(base_inputs) - set(inputs)
    assert not missing, missing
    for name, spec in base_inputs.items():
        assert inputs[name].get("default") == spec.get("default"), name
        assert inputs[name].get("required") == spec.get("required"), name

    assert set(outputs) >= set(base_outputs), set(base_outputs) - set(outputs)
    for name, spec in base_outputs.items():
        assert outputs[name].get("value") == spec.get("value"), name

    for name, spec in inputs.items():
        if name in base_inputs:
            continue
        assert spec.get("required") in ("false", False), (name, spec)
        assert "default" in spec, (name, spec)


def test_ac8_new_action_inputs_default_to_baseline_behaviour():
    """AC-8: the three inputs this run may add must default to "off"."""
    with open(ACTION_YML, encoding="utf8", newline="\n") as fh:
        inputs = parse_action_block(fh.read(), "inputs")
    off_defaults = {
        "embed": "false",
        "embed-model": "",
        "query-budget-tokens": "",
        "incremental": "false",
        "parse-policy": "best-effort",
        "max-call-candidates": "5",
    }
    for name, expected in off_defaults.items():
        if name in inputs:
            assert inputs[name].get("default") == expected, (name, inputs[name])


def _cli_choices(command: str, flag: str, sentinel: str = "\x00not-a-value") -> tuple:
    """The argparse `choices` a subcommand declares for `flag`.

    Read off the live parser rather than re-stated here, so this test asks the
    CLI what it accepts instead of asserting a second copy of the list against
    the first. The parser is reached by handing it a value nothing can match
    and keeping the object argparse reports the error on -- `main` builds its
    parser inline and never returns it.
    """
    captured: dict = {}
    real_error = argparse.ArgumentParser.error

    def spy(self, message):
        captured.setdefault("p", self)
        real_error(self, message)

    argparse.ArgumentParser.error = spy  # type: ignore[method-assign]
    try:
        with pytest.raises(SystemExit):
            main([command, flag, sentinel])
    finally:
        argparse.ArgumentParser.error = real_error  # type: ignore[method-assign]
    for action in captured["p"]._actions:
        if flag in action.option_strings:
            return tuple(action.choices or ())
    raise AssertionError(f"{command} has no {flag}")


def test_action_parse_policy_values_are_values_the_cli_accepts():
    """`parse-policy` is forwarded verbatim to `repo2graph build
    --parse-policy`, whose argparse `choices` reject anything else. The input
    shipped `lenient` as its default and its documented enum; `lenient` is not
    a choice, and only the step's "drop the value when it equals the default"
    guard kept it from ever reaching the CLI."""
    with open(ACTION_YML, encoding="utf8", newline="\n") as fh:
        spec = parse_action_block(fh.read(), "inputs")["parse-policy"]
    choices = _cli_choices("build", "--parse-policy")
    assert spec["default"] in choices, (spec["default"], choices)
    # Every enum value named in the description must exist too, or the docs
    # send an operator to a value the build rejects.
    described = set(re.findall(r"[a-z]+(?:-[a-z]+)*", spec["description"].split(":", 1)[1]))
    assert set(choices) <= described, (choices, described)
    assert described <= set(choices) | {"default"}, (described, choices)


# ==========================================================================
# AC-34 / AC-35 -- per-file content hashes in index.state.json
# ==========================================================================

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _state(outdir: Path) -> dict:
    with open(artifact_path(outdir, "index.state.json"), encoding="utf8", newline="\n") as fh:
        return json.load(fh)


def test_ac34_build_writes_index_state_with_a_hash_per_file_node(mini_index):
    """AC-34: `files` keys are exactly the readable `file:` node paths and the
    values are 64-char lowercase hex digests."""
    state = _state(mini_index)
    assert isinstance(state.get("files"), dict), state
    nodes = read_jsonl(artifact_path(mini_index, "nodes.jsonl"))
    file_paths = {n["path"] for n in nodes if n.get("type") == "file"}
    assert set(state["files"]) == file_paths, set(state["files"]) ^ file_paths
    for rel, digest in state["files"].items():
        assert isinstance(digest, str) and HEX64.match(digest), (rel, digest)


def test_ac34_state_hash_is_the_sha256_of_the_file_bytes(mini_repo, mini_index):
    """AC-34: the digest is reproducible by hand from the file on disk."""
    state = _state(mini_index)
    rel = "pkg/gateway.py"
    expected = hashlib.sha256((mini_repo / rel).read_bytes()).hexdigest()
    assert state["files"][rel] == expected


def test_ac35_editing_one_file_changes_exactly_one_hash(mini_repo, tmp_path):
    """AC-35: a rebuild after a one-file edit leaves every other entry
    byte-identical."""
    out = build_mini_index(mini_repo, tmp_path / "state_idx")
    before = _state(out)["files"]

    target = mini_repo / "pkg" / "audit.py"
    with open(target, "a", encoding="utf8", newline="\n") as fh:
        fh.write("\n\nJOURNAL_VERSION = 2\n")

    out2 = build_mini_index(mini_repo, tmp_path / "state_idx2")
    after = _state(out2)["files"]

    assert set(before) == set(after)
    changed = {rel for rel in before if before[rel] != after[rel]}
    assert changed == {"pkg/audit.py"}, changed


# ==========================================================================
# Regression -- REVIEW iteration 3
# ==========================================================================
#
# R-6: the `embed` input is read by two gates in two languages. The embed step
# gates on `if: ${{ inputs.embed == 'true' }}`, and `==` in a GitHub expression
# compares strings case-insensitively; the rag step gated on bash `=`, which
# does not. `embed: "True"` therefore ran the embed step -- paying for the
# sentence-transformers install, the model download and vectors.npy -- and then
# built a rag argv with no `--vectors`, i.e. computed vectors and silently threw
# them away. These assert the *resolved command line*, not an exit code: a gate
# that agrees only on exit status would not have caught it.

BASH = shutil.which("bash")
if sys.platform == "win32":
    git_bash = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
    )
    if git_bash.is_file():
        BASH = str(git_bash)
    elif BASH and "system32" in BASH.lower():
        BASH = None

# Every casing a workflow author can plausibly write, on both sides of the gate.
EMBED_CASINGS = ["true", "True", "TRUE", "tRuE", "false", "False", "FALSE", ""]


def _action_step_by_name(text: str, name: str) -> str:
    for chunk in re.split(r"\n(?=    - (?:name|uses):)", text):
        if re.search(rf"^\s+- name: {re.escape(name)}\s*$", chunk, re.M):
            return chunk
    raise AssertionError(f"action.yml has no step named {name!r}")


def _run_body(chunk: str) -> str:
    """The dedented body of a composite step's `run: |` block."""
    lines = chunk.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "run: |":
            indent = len(line) - len(line.lstrip())
            body = []
            for nxt in lines[i + 1 :]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                body.append(nxt[indent + 2 :])
            return "\n".join(body) + "\n"
    raise AssertionError("step has no `run: |` block")


def _github_gate(value: str) -> bool:
    """`inputs.embed == '<literal>'` as GitHub evaluates it.

    Read the literal out of action.yml rather than hardcoding it, so this stays
    a comparison of the two real gates and not of two copies of one guess.
    """
    text = ACTION_YML.read_text(encoding="utf8")
    step = _action_step_by_name(text, "Embed the chunks")
    m = re.search(r"if:\s*\$\{\{\s*inputs\.embed\s*==\s*'([^']*)'\s*\}\}", step)
    assert m, step
    # GitHub compares strings with `==` ignoring case.
    return value.lower() == m.group(1).lower()


def _resolved_rag_argv(tmp_path: Path, embed: str) -> list:
    """The argv action.yml's rag step really builds, for `embed: <embed>`.

    The step's own bash is executed, truncated just before the `repo2graph`
    call and replaced by a `printf` of the array it assembled -- so the argv
    asserted on is the one the action would have run, not a transcription.
    """
    body = _run_body(
        _action_step_by_name(ACTION_YML.read_text(encoding="utf8"), "Pack a GraphRAG context")
    )
    head, sep, _ = body.partition('repo2graph "${args[@]}"')
    assert sep, body
    script = head + 'printf "%s\\n" "${args[@]}"\n'
    env = dict(os.environ)
    env.update(
        R2G_OUT="out",
        R2G_QUERY="who routes?",
        R2G_K="8",
        R2G_HOPS="1",
        R2G_BUDGET="24000",
        R2G_BUDGET_TOKENS="",
        R2G_MIN_CONF="1.0",
        R2G_FORMAT="markdown",
        R2G_QUERY_OUT="",
        R2G_EMBED=embed,
        R2G_EMBED_MODEL="",
    )
    proc = subprocess.run(
        [BASH, "-c", script], cwd=str(tmp_path), env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.replace("\r\n", "\n").split("\n") if line]


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
@pytest.mark.parametrize("embed", EMBED_CASINGS)
def test_r6_the_yaml_gate_and_the_shell_gate_agree_on_every_casing(tmp_path, embed):
    """R-6 (a): the step that computes vectors and the step that consumes them
    must switch on at exactly the same input values."""
    argv = _resolved_rag_argv(tmp_path, embed)
    assert ("--vectors" in argv) is _github_gate(embed), (embed, argv)


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_r6_a_capitalised_true_still_packs_with_vectors(tmp_path):
    """R-6 (b): the exact value that used to compute vectors and ignore them."""
    assert "--vectors" in _resolved_rag_argv(tmp_path, "True")
    assert "--vectors" in _resolved_rag_argv(tmp_path, "TRUE")


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_r6_the_off_path_is_still_the_baseline_command_line(tmp_path):
    """R-6 (c): with the input at its default, the argv is baseline's, in
    baseline's order -- the fix must not reach the workflows that never opt in."""
    baseline = [
        "rag",
        "-o",
        "out",
        "-k",
        "8",
        "--hops",
        "1",
        "--budget",
        "24000",
        "--min-conf",
        "1.0",
        "--format",
        "markdown",
    ]
    for embed in ("false", "False", ""):
        assert _resolved_rag_argv(tmp_path, embed) == baseline, embed


def test_r6_embed_is_the_only_boolean_input_with_a_split_gate():
    """R-6 (d): every other `if:` on an input tests non-emptiness, which bash's
    `-n`/`-z` agree with for every casing. If a second boolean input is added,
    this fails and the new gate has to be checked the way `embed` now is."""
    text = ACTION_YML.read_text(encoding="utf8")
    equality_gates = set(re.findall(r"if:\s*\$\{\{\s*inputs\.([\w-]+)\s*==", text))
    assert equality_gates == {"embed"}, equality_gates


# ==========================================================================
# Regressions -- VERIFY iteration 4
# ==========================================================================
#
# R-9: `repo2graph/__init__.py`'s fallback `__version__` (used only when the
# package is imported from a source tree with no installed dist-info) said
# "1.3.0" while `[project] version` had moved to 1.4.0, so the same build
# reported two different versions depending on how it was imported.


def test_r9_the_fallback_version_agrees_with_pyproject():
    """R-9: every `__version__` literal in the package equals the packaged
    version. There is no metadata to read in a source checkout, so the literal
    is the only thing that answers `repo2graph.__version__` there."""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py3.10
        pytest.skip("tomllib needs Python 3.11+")
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    source = (REPO_ROOT / "repo2graph" / "__init__.py").read_text(encoding="utf8")
    literals = re.findall(r"""__version__\s*=\s*["']([^"']+)["']""", source)
    assert literals, "no __version__ literal found"
    assert set(literals) == {declared}, (literals, declared)


# ==========================================================================
# Issue #165: the "Push graph to branch" step used to interpolate
# GITHUB_TOKEN directly into the `git push` argv --
#   git push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/..." "$R2G_BRANCH"
# -- which puts the token in a word any process on the host/runner can read
# via `ps aux` or `/proc/$PID/cmdline` for the duration of the push. The fix
# routes the credential through `http.extraheader` in the local git config
# instead, so `git push`'s own argv never contains it.


def _resolved_push_git_calls(
    tmp_path: Path, token: str, extra_env: dict[str, str] | None = None
) -> list:
    """Every `git ...` invocation the "Push graph to branch" step's real body
    makes, as the literal argv bash hands it -- captured by overriding `git`
    with a shell function instead of truncating/rewriting the script, so the
    step body under test is exactly what's in action.yml, unmodified."""
    body = _run_body(
        _action_step_by_name(ACTION_YML.read_text(encoding="utf8"), "Push graph to branch")
    )
    log = tmp_path / "git-calls.log"
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "graph.jsonl").write_text("{}\n", encoding="utf8")
    script = 'git() { printf "%s\\x1f" "$@" >> "$GIT_LOG"; printf "\\n" >> "$GIT_LOG"; }\n' + body
    env = dict(os.environ)
    env.update(
        GITHUB_TOKEN=token,
        GITHUB_REPOSITORY="acme/widgets",
        GITHUB_SHA="0" * 40,
        R2G_OUT=str(out_dir),
        R2G_BRANCH="r2g-graph",
        GIT_LOG=str(log),
    )
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [BASH, "-c", script], cwd=str(tmp_path), env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    calls = []
    for line in log.read_text(encoding="utf8").split("\n"):
        if line:
            calls.append(line.split("\x1f")[:-1])
    return calls


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_iss165_the_token_never_appears_in_a_git_argv_word(tmp_path):
    """The exact secret value must not be a word in any `git` invocation's
    argv -- not just the `push` call, since a leak in `remote add` or
    elsewhere would be just as visible to `ps`."""
    token = "ghs_TotallyFakeIssue165ProbeToken"  # noqa: S105 - test fixture, not a real credential
    calls = _resolved_push_git_calls(tmp_path, token)
    assert calls, "the step made no git calls"
    for call in calls:
        assert not any(token in word for word in call), call


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_iss165_the_push_call_carries_no_credential_and_no_literal_url(tmp_path):
    """`git push`'s own argv is exactly the safe, credential-free form --
    the URL-with-embedded-token this issue reports is gone, and so is any
    other rendering of the credential (e.g. a bare `x-access-token`)."""
    token = "ghs_TotallyFakeIssue165ProbeToken"  # noqa: S105 - test fixture, not a real credential
    calls = _resolved_push_git_calls(tmp_path, token)
    push_calls = [c for c in calls if c[:1] == ["push"]]
    assert len(push_calls) == 1, calls
    assert push_calls[0] == ["push", "-q", "--force", "origin", "r2g-graph"]
    for call in calls:
        assert not any("x-access-token" in word for word in call), call
        assert not any(word.startswith("https://") and "@" in word for word in call), call


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_commit_force_false_uses_plain_push(tmp_path):
    """Setting commit-force to false uses standard push without --force (#310)."""
    token = "ghs_TotallyFakeIssue310ProbeToken"  # noqa: S105
    calls = _resolved_push_git_calls(tmp_path, token, extra_env={"R2G_COMMIT_FORCE": "false"})
    push_calls = [c for c in calls if c[:1] == ["push"]]
    assert len(push_calls) == 1, calls
    assert push_calls[0] == ["push", "-q", "origin", "r2g-graph"]


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_push_refused_on_fork_pull_request(tmp_path):
    """The action refuses to push graph to branch when run from a fork PR (#310)."""
    body = _run_body(
        _action_step_by_name(ACTION_YML.read_text(encoding="utf8"), "Push graph to branch")
    )
    log = tmp_path / "git-calls.log"
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "graph.jsonl").write_text("{}\n", encoding="utf8")
    script = 'git() { printf "%s\\x1f" "$@" >> "$GIT_LOG"; printf "\\n" >> "$GIT_LOG"; }\n' + body
    env = dict(os.environ)
    env.update(
        GITHUB_TOKEN="ghs_FakeToken",  # noqa: S105
        GITHUB_REPOSITORY="acme/widgets",
        GITHUB_SHA="0" * 40,
        R2G_OUT=str(out_dir),
        R2G_BRANCH="r2g-graph",
        GIT_LOG=str(log),
        IS_FORK="true",
    )
    proc = subprocess.run(
        [BASH, "-c", script], cwd=str(tmp_path), env=env, capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "Refusing to push graph to branch from a fork pull request" in (
        proc.stdout + proc.stderr
    )


# ==========================================================================
# Issue #204: the `version` input was inert
# ==========================================================================
#
# The install step gated on `[ -f "$GITHUB_ACTION_PATH/pyproject.toml" ]`. For
# a composite action $GITHUB_ACTION_PATH is the checked-out action repository
# and pyproject.toml sits beside action.yml at its root, so that test was true
# on every run -- `uses: ./` and `uses: owner/repo@ref` alike -- the else
# branch was unreachable, and `version: repo2graph==1.5.0` silently installed
# whatever the action ref resolved to. It also meant the Action never used the
# signed PyPI artifact, so publish.yml's provenance covered nothing consumers
# actually install.
#
# These assert the *resolved pip argv*, not an exit code: the old body exits 0
# for both inputs, so a test that only checked exit status would have stayed
# green through the whole bug.


def _resolved_pip_calls(tmp_path: Path, version: str, *, pyproject: bool = True):
    """(returncode, [argv, ...]) for the "Install repo2graph" step's real body.

    `pip` is overridden by a shell function that logs its argv, so nothing is
    installed and the script under test is byte-for-byte the one in action.yml
    -- not a transcription of it.
    """
    body = _run_body(
        _action_step_by_name(ACTION_YML.read_text(encoding="utf8"), "Install repo2graph")
    )
    action_path = tmp_path / "action_checkout"
    action_path.mkdir()
    if pyproject:
        (action_path / "pyproject.toml").write_text(
            '[project]\nname = "repo2graph"\n', encoding="utf8"
        )
    log = tmp_path / "pip-calls.log"
    script = 'pip() { printf "%s\\x1f" "$@" >> "$PIP_LOG"; printf "\\n" >> "$PIP_LOG"; }\n' + body
    env = dict(os.environ)
    env.update(
        GITHUB_ACTION_PATH=action_path.as_posix(),
        R2G_VERSION=version,
        PIP_LOG=str(log),
    )
    proc = subprocess.run(
        [BASH, "-c", script], cwd=str(tmp_path), env=env, capture_output=True, text=True
    )
    calls = []
    if log.exists():
        for line in log.read_text(encoding="utf8").split("\n"):
            if line:
                calls.append(line.split("\x1f")[:-1])
    return proc.returncode, calls


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_iss204_a_blank_version_installs_the_action_checkout(tmp_path):
    """#204 (a): the default path is unchanged -- `uses: ./` and every workflow
    that never sets `version` still gets an editable install of the checkout."""
    action_path = (tmp_path / "action_checkout").as_posix()
    rc, calls = _resolved_pip_calls(tmp_path, "")
    assert rc == 0, calls
    assert calls == [["install", "-q", "-e", action_path]], calls


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
@pytest.mark.parametrize(
    "version",
    [
        "repo2graph==1.5.0",
        "repo2graph>=1.4,<2",
        "git+https://github.com/Srinivasan-78/repo2graph@v1",
    ],
)
def test_iss204_a_non_blank_version_is_the_spec_pip_receives(tmp_path, version):
    """#204 (b): the reported bug. The fixture writes a pyproject.toml into
    the action checkout -- the precondition the bug lived on, and the normal
    case for a composite action -- and a pinned spec must still reach pip
    verbatim rather than the checkout being installed instead of it."""
    action_path = (tmp_path / "action_checkout").as_posix()
    rc, calls = _resolved_pip_calls(tmp_path, version)
    assert rc == 0, calls
    assert calls == [["install", "-q", version]], calls
    for call in calls:
        assert "-e" not in call, call
        assert not any(action_path in word for word in call), call


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_iss204_a_blank_version_with_no_checkout_source_fails_loudly(tmp_path):
    """#204 (c): blank + nothing to install from is a hard error naming the
    input, not a silent `pip install ''` or a stale hardcoded fallback."""
    rc, calls = _resolved_pip_calls(tmp_path, "", pyproject=False)
    assert rc != 0, calls
    assert calls == [], calls


def test_iss204_the_version_input_defaults_to_blank():
    """#204 (d): the default must stay blank. A non-empty default would flip
    every existing workflow -- CI's own `uses: ./` included -- from installing
    the checkout to installing a published spec."""
    with open(ACTION_YML, encoding="utf8", newline="\n") as fh:
        inputs = parse_action_block(fh.read(), "inputs")
    assert inputs["version"].get("default") == "", inputs["version"]


def test_iss204_the_install_step_gates_on_the_version_input():
    """#204 (e): a source-level guard on the shape of the gate, so the file
    test cannot come back as the *first* branch. `-n`/`-z` on the input is
    also the form GitHub expressions and bash agree on for every casing
    (AGENTS.md), which is why no `inputs.version ==` gate is needed."""
    body = _run_body(
        _action_step_by_name(ACTION_YML.read_text(encoding="utf8"), "Install repo2graph")
    )
    first_test = re.search(r"^\s*if \[ (.+) \]; then\s*$", body, re.M)
    assert first_test, body
    assert first_test.group(1) == '-n "$R2G_VERSION"', first_test.group(1)


def test_iss108_examples_workflow_routes_inputs_through_env():
    """Issue 108: examples.yml must route ${{ inputs.repo }} via env:, not direct run: interpolation."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "examples.yml"
    assert workflow_path.exists()
    content = workflow_path.read_text(encoding="utf8")
    generate_block = content.split("- name: Generate")[1]
    run_part = generate_block.split("run:")[1].split("- uses:")[0]
    assert "${{ inputs.repo }}" not in run_part
    assert "INPUT_REPO: ${{ inputs.repo }}" in generate_block
    assert '"$INPUT_REPO"' in run_part


def test_iss137_generate_examples_split_rule(tmp_path):
    """Issue 137: generate_examples._read_jsonl_maybe_gz preserves records with embedded line separators like U+2028."""
    import gzip
    import importlib.util

    script_path = REPO_ROOT / "scripts" / "generate_examples.py"
    spec = importlib.util.spec_from_file_location("generate_examples", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _read_jsonl_maybe_gz = mod._read_jsonl_maybe_gz

    # A JSON record containing embedded U+2028 (line separator) in string
    record = '{"id": "chunk-1", "text": "hello\u2028world"}'
    jsonl_path = tmp_path / "test.jsonl.gz"
    with gzip.open(jsonl_path, "wt", encoding="utf8") as fh:
        fh.write(record + "\n")

    lines = _read_jsonl_maybe_gz(jsonl_path)
    assert len(lines) == 1
    assert lines[0] == record


def test_iss139_commit_release_api_timeout(monkeypatch):
    """Issue 139: api() in commit_release_via_api.py must pass timeout to urlopen."""
    import importlib.util
    import io

    script_path = REPO_ROOT / "scripts" / "commit_release_via_api.py"
    spec = importlib.util.spec_from_file_location("commit_release_via_api", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    recorded_timeouts = []

    class DummyResponse:
        def __enter__(self):
            return io.BytesIO(b'{"status": "ok"}')

        def __exit__(self, *args):
            pass

    def dummy_urlopen(req, timeout=None):
        recorded_timeouts.append(timeout)
        return DummyResponse()

    monkeypatch.setattr(mod.urllib.request, "urlopen", dummy_urlopen)
    res = mod.api("GET", "/test", "dummy-token")
    assert res == {"status": "ok"}
    assert recorded_timeouts == [30.0]


def _load_commit_release_module():
    import importlib.util

    script_path = REPO_ROOT / "scripts" / "commit_release_via_api.py"
    spec = importlib.util.spec_from_file_location("commit_release_via_api", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_commit_release(mod, monkeypatch, *, branch_exists, file_path="release.txt"):
    """Drive main() against fake REST + GraphQL, returning what each one saw.

    `branch_exists` selects the two paths through main(): a fresh branch is
    committed on directly, an existing one is staged and then moved.
    """
    import io

    rest_calls = []
    graphql_calls = []
    # ref_sha() probes base first, then the target branch.
    seen_refs = {"main"}
    if branch_exists:
        seen_refs.add("release-branch")

    def dummy_api(method, path, token, payload=None, timeout=30.0):
        rest_calls.append((method, path, payload))
        if method == "GET" and "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1]
            if name not in seen_refs:
                raise mod.urllib.error.HTTPError(path, 404, "Not Found", {}, None)
            return {"object": {"sha": "basesha"}}
        if method == "POST" and path.endswith("/git/refs"):
            seen_refs.add(payload["ref"].split("refs/heads/", 1)[1])
        return {}

    def dummy_graphql(query, variables, token, timeout=30.0):
        graphql_calls.append((query, variables))
        return {"createCommitOnBranch": {"commit": {"oid": "signedsha"}}}

    monkeypatch.setattr(mod, "api", dummy_api)
    monkeypatch.setattr(mod, "graphql", dummy_graphql)
    monkeypatch.setattr(mod, "open", lambda *a, **k: io.BytesIO(b"v1.0.0"), raising=False)
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setattr(
        mod.sys,
        "argv",
        [
            "commit_release_via_api.py",
            "--repo",
            "test/repo",
            "--branch",
            "release-branch",
            "--base",
            "main",
            "--message",
            "chore(release): bump version to 1.2.3\n\nbody line\n",
            "--files",
            file_path,
        ],
    )
    mod.main()
    return rest_calls, graphql_calls


def test_iss146_commit_release_api_path_posix(monkeypatch):
    """Issue 146: commit_release_via_api.py must normalize Windows paths."""
    mod = _load_commit_release_module()
    _, graphql_calls = _run_commit_release(
        mod, monkeypatch, branch_exists=False, file_path="dist\\sub\\release.txt"
    )

    assert len(graphql_calls) == 1
    additions = graphql_calls[0][1]["input"]["fileChanges"]["additions"]
    assert len(additions) == 1
    # Path must be POSIX normalized (no backslashes)
    assert "\\" not in additions[0]["path"]
    assert additions[0]["path"] == "dist/sub/release.txt"


def test_commit_release_uses_signing_mutation_not_git_data():
    """The commit must be created by GraphQL createCommitOnBranch.

    GitHub signs commits it authors (Contents API, createCommitOnBranch) and
    does not sign ones assembled through the Git Data API. `main` requires
    signed commits, so a regression back to blobs/trees/commits would leave
    every release PR unmergeable without an admin bypass.
    """
    import ast

    source = (REPO_ROOT / "scripts" / "commit_release_via_api.py").read_text(encoding="utf-8")
    assert "createCommitOnBranch" in source

    # Only literals the code evaluates count. The docstring names the Git Data
    # routes precisely to explain why they are wrong, and a plain substring
    # search over the file would read that explanation as the offence.
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = " ".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )
    for git_data_route in ("/git/blobs", "/git/trees", "/git/commits"):
        assert git_data_route not in literals, (
            f"Git Data route {git_data_route} produces unsigned commits"
        )


def test_commit_release_stages_an_existing_branch_before_moving_it(monkeypatch):
    """An existing PR branch must never be reset to base in place.

    Resetting it leaves the open PR with zero commits and GitHub closes the PR
    automatically, so the branch is rebuilt on a staging ref and moved in one
    force-update instead.
    """
    mod = _load_commit_release_module()
    rest_calls, graphql_calls = _run_commit_release(mod, monkeypatch, branch_exists=True)

    staging = f"release-branch{mod.STAGING_SUFFIX}"
    committed_on = graphql_calls[0][1]["input"]["branch"]["branchName"]
    assert committed_on == staging, "commit must land on the staging ref, not the PR branch"

    writes = [
        (method, path, payload)
        for method, path, payload in rest_calls
        if method in {"POST", "PATCH", "DELETE"}
    ]
    # The PR branch is written exactly once, and only to the finished commit.
    pr_branch_writes = [
        payload for _, path, payload in writes if path.endswith("/git/refs/heads/release-branch")
    ]
    assert pr_branch_writes == [{"sha": "signedsha", "force": True}]
    # And the staging ref is cleaned up.
    assert any(
        method == "DELETE" and path.endswith(f"/git/refs/heads/{staging}")
        for method, path, _ in writes
    )


def test_commit_release_commits_directly_on_a_new_branch(monkeypatch):
    """A branch that does not exist yet needs no staging ref."""
    mod = _load_commit_release_module()
    rest_calls, graphql_calls = _run_commit_release(mod, monkeypatch, branch_exists=False)

    assert graphql_calls[0][1]["input"]["branch"]["branchName"] == "release-branch"
    assert not any(method == "DELETE" for method, _, _ in rest_calls)


def test_commit_release_splits_headline_from_body(monkeypatch):
    """GraphQL takes headline and body separately, not one git-style blob."""
    mod = _load_commit_release_module()
    _, graphql_calls = _run_commit_release(mod, monkeypatch, branch_exists=False)

    message = graphql_calls[0][1]["input"]["message"]
    assert message["headline"] == "chore(release): bump version to 1.2.3"
    assert message["body"] == "body line"


def test_commit_release_graphql_raises_on_error_payload():
    """GraphQL reports failures as HTTP 200 with an `errors` array.

    Without this check a failed commit would return None and the release would
    carry on as though it had succeeded.
    """
    import io

    mod = _load_commit_release_module()
    body = json.dumps({"data": None, "errors": [{"message": "expectedHeadOid mismatch"}]})

    class DummyResponse:
        def __enter__(self):
            return io.BytesIO(body.encode())

        def __exit__(self, *args):
            return False

    original = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = lambda req, timeout=None: DummyResponse()
    try:
        with pytest.raises(RuntimeError, match="expectedHeadOid mismatch"):
            mod.graphql("query {}", {}, "token")
    finally:
        mod.urllib.request.urlopen = original


# ==========================================================================
# Issue #240: every subprocess in the package must close its stdin
# ==========================================================================
#
# `capture_output` redirects the child's stdout and stderr only, so a child
# spawned without `stdin=` inherits ours. Under `repo2graph-mcp` on stdio that
# handle is the client's JSON-RPC pipe: the child blocks reading it until its
# timeout fires, and while it holds the pipe it can swallow frames meant for
# us (the reasoning is spelled out at parse.py's _git_files). Every call site
# followed the rule except the two `cpp` invocations in parse.py.
#
# This is a source-level invariant rather than a behavioural one -- the two
# cpp calls were latent, not actively breaking, and a runtime test can only
# reach the sites it happens to exercise. Walking the AST of the whole package
# means the *next* call site added cannot skip it either, the same way the
# action.yml checks above pin a gate no Python test can see.


def _subprocess_spawn_calls(path: Path):
    """(lineno, dotted-name, has-stdin-keyword) for every subprocess spawn."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in ("run", "Popen", "call"):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "subprocess":
            continue
        kwnames = {kw.arg for kw in node.keywords}
        # `subprocess.run(**kwargs)` would carry stdin invisibly; None means a
        # `**` unpacking, so treat it as unverifiable-but-not-a-violation.
        ok = "stdin" in kwnames or None in kwnames
        found.append((node.lineno, f"subprocess.{func.attr}", ok))
    return found


def test_iss240_every_subprocess_spawn_in_the_package_closes_stdin():
    """#240: no `subprocess.run`/`Popen`/`call` in repo2graph/ may inherit our
    stdin. Anything new that does fails here, naming the file and line."""
    package = REPO_ROOT / "repo2graph"
    offenders = []
    total = 0
    for path in sorted(package.rglob("*.py")):
        for lineno, name, ok in _subprocess_spawn_calls(path):
            total += 1
            if not ok:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno} {name}")
    # A sweep that found nothing would pass vacuously; the package has had at
    # least a dozen spawn sites since fetch.py landed.
    assert total >= 10, total
    assert offenders == [], offenders

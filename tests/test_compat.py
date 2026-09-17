"""The backward-compatibility contract: AC-1 .. AC-9, plus AC-34 / AC-35.

These are characterization tests. The golden files under `tests/golden/` were
captured from the baseline commit ff0e3ca (the tree this run started from), by
running::

    R2G_REGEN_GOLDEN=1 python -m pytest tests/test_compat.py -q

with an unmodified checkout. Regenerating them is therefore how you *create* a
golden, never how you make a red test go green: a characterization test that
fails means either the golden was captured against a dirty tree or the
implementation broke something it promised not to touch.

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
        f"import sys\n{import_stmt}\n"
        f"print(' '.join(m for m in {watched!r} if m in sys.modules))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                          capture_output=True, timeout=180)
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
    text = _capture(capsys, ["query", MINI_QUERY, "-o", str(mini_index),
                             "--format", "json"])
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
    text = _capture(capsys, ["rag", MINI_QUERY, "-o", str(mini_index),
                             "--format", "json"])
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
        "unbounded": {k: idx.pack_context(MINI_QUERY, budget_chars=0)[k]
                      for k in keys},
        "tight": {k: idx.pack_context(MINI_QUERY, budget_chars=1200)[k]
                  for k in keys},
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
    else:                                       # pragma: no cover - guard
        raise AssertionError("main() never called parse_args")

    inventory = {"<root>": {}}
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                inventory[name] = {
                    (" ".join(sorted(a.option_strings)) or a.dest):
                        _describe_action(a)
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

def test_ac5_importing_query_pulls_in_no_optional_dependency():
    """AC-5: numpy, sentence_transformers, torch and mcp all stay unimported."""
    rc, out, err = _subprocess_modules("import repo2graph.query")
    assert rc == 0, err
    assert out == "", out


def test_ac6_importing_embed_pulls_in_no_optional_dependency():
    """AC-6: repo2graph.embed is importable with no extras installed."""
    rc, out, err = _subprocess_modules(
        "import repo2graph.embed",
        watched=("numpy", "sentence_transformers", "torch"))
    assert rc == 0, err
    assert out == "", out


def test_ac7_importing_repo2graph_mcp_does_not_import_the_mcp_sdk():
    """AC-7: only serve() may import the SDK, so the module imports bare."""
    rc, out, err = _subprocess_modules("import repo2graph.mcp")
    assert rc == 0, err
    assert out == "", out


def test_ac5_cli_import_chain_stays_clean():
    """AC-5 (corollary): `repo2graph query` must not drag an extra in either."""
    rc, out, err = _subprocess_modules("import repo2graph.cli")
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
    for line in text.split("\n"):            # never splitlines(): see AGENTS.md
        if line.rstrip() == f"{block}:":
            inside = True
            continue
        if not inside:
            continue
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            break                            # back at column 0: block is over
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
        theirs = {k: {kk: str(vv) for kk, vv in v.items()}
                  for k, v in data[block].items()}
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
    off_defaults = {"embed": "false", "embed-model": "", "query-budget-tokens": ""}
    for name, expected in off_defaults.items():
        if name in inputs:
            assert inputs[name].get("default") == expected, (name, inputs[name])


# ==========================================================================
# AC-34 / AC-35 -- per-file content hashes in index.state.json
# ==========================================================================

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _state(outdir: Path) -> dict:
    with open(artifact_path(outdir, "index.state.json"),
              encoding="utf8", newline="\n") as fh:
        return json.load(fh)


def test_ac34_build_writes_index_state_with_a_hash_per_file_node(mini_index):
    """AC-34: `files` keys are exactly the readable `file:` node paths and the
    values are 64-char lowercase hex digests."""
    state = _state(mini_index)
    assert isinstance(state.get("files"), dict), state
    nodes = read_jsonl(artifact_path(mini_index, "nodes.jsonl"))
    file_paths = {n["path"] for n in nodes if n.get("type") == "file"}
    assert set(state["files"]) == file_paths, (
        set(state["files"]) ^ file_paths)
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
    git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
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
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                body.append(nxt[indent + 2:])
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
    body = _run_body(_action_step_by_name(ACTION_YML.read_text(encoding="utf8"),
                                          "Pack a GraphRAG context"))
    head, sep, _ = body.partition('repo2graph "${args[@]}"')
    assert sep, body
    script = head + 'printf "%s\\n" "${args[@]}"\n'
    env = dict(os.environ)
    env.update(R2G_OUT="out", R2G_QUERY="who routes?", R2G_K="8", R2G_HOPS="1",
               R2G_BUDGET="24000", R2G_BUDGET_TOKENS="", R2G_MIN_CONF="1.0",
               R2G_FORMAT="markdown", R2G_QUERY_OUT="", R2G_EMBED=embed,
               R2G_EMBED_MODEL="")
    proc = subprocess.run([BASH, "-c", script], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True)
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
    baseline = ["rag", "-o", "out", "-k", "8", "--hops", "1",
                "--budget", "24000", "--min-conf", "1.0",
                "--format", "markdown"]
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
    except ModuleNotFoundError:                      # pragma: no cover - py3.10
        pytest.skip("tomllib needs Python 3.11+")
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    source = (REPO_ROOT / "repo2graph" / "__init__.py").read_text(encoding="utf8")
    literals = re.findall(r"""__version__\s*=\s*["']([^"']+)["']""", source)
    assert literals, "no __version__ literal found"
    assert set(literals) == {declared}, (literals, declared)

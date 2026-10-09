"""The repo config file (#391): `[tool.repo2graph]` / `.repo2graph.toml`."""

import argparse
import json
from pathlib import Path

import pytest

from repo2graph import cli, config
from repo2graph.cli import main
from repo2graph.config import ConfigError, load


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "gen").mkdir()
    (repo / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf8")
    (repo / "gen" / "made.py").write_text("def made():\n    return 2\n", encoding="utf8")
    return repo


def _pyproject(repo: Path, body: str) -> None:
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "x"\n\n[tool.repo2graph]\n' + body, encoding="utf8"
    )


def _paths(out: Path) -> set[str]:
    lines = (out / "agent" / "nodes.jsonl").read_text(encoding="utf8").splitlines()
    return {json.loads(line).get("path") for line in lines}


# --- loading and validation ---------------------------------------------------


def test_no_config_file_loads_nothing(tmp_path, capsys):
    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf8")
    assert not load(repo)
    assert capsys.readouterr().err == ""


def test_every_key_is_read_and_mapped_to_its_flag(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".repo2graph.toml").write_text(
        'include = ["src/**"]\nexclude = ["gen/**"]\nexclude-dir = ["fixtures"]\n'
        'git-history = 50\nviz-nodes = "all"\nmax-call-candidates = 3\n'
        'secret-policy = "exclude-file"\nsecret-keywords = ["acme"]\n'
        'secret-dirs = ["vaultcfg"]\nmax-file-mb = 2\nchunk-large-files = true\n'
        "include-vendor = true\n",
        encoding="utf8",
    )
    cfg = load(repo)
    assert cfg.values == {
        "include": ["src/**"],
        "exclude": ["gen/**"],
        "extra_exclude_dirs": ["fixtures"],
        "git_history": 50,
        "viz_nodes": None,
        "max_call_candidates": 3,
        "secret_policy": "exclude-file",
        "extra_secret_keywords": ["acme"],
        "extra_secret_dirs": ["vaultcfg"],
        "max_file_mb": 2.0,
        "chunk_large_files": True,
        "include_vendor": True,
    }
    assert set(cfg.sources.values()) == {".repo2graph.toml"}


def test_pyproject_table_beats_repo2graph_toml_key_by_key(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".repo2graph.toml").write_text('exclude = ["a/**"]\ngit-history = 7\n', encoding="utf8")
    _pyproject(repo, 'exclude = ["gen/**"]\n')
    cfg = load(repo)
    assert cfg.values == {"exclude": ["gen/**"], "git_history": 7}
    assert cfg.label("exclude") == "pyproject.toml [tool.repo2graph] exclude"
    assert cfg.label("git_history") == ".repo2graph.toml git-history"


def test_unknown_key_names_the_file_and_the_key(tmp_path):
    repo = _repo(tmp_path)
    _pyproject(repo, 'excludes = ["gen/**"]\n')
    with pytest.raises(ConfigError) as exc:
        load(repo)
    msg = str(exc.value)
    assert "pyproject.toml [tool.repo2graph]" in msg
    assert "'excludes'" in msg


@pytest.mark.parametrize(
    "line",
    [
        "git-history = true",  # a bool is not a count
        "git-history = -1",
        'exclude = "gen/**"',  # a string, not a list
        "exclude = [1]",
        "max-call-candidates = 0",
        "max-file-mb = 0.01",
        'viz-nodes = "some"',
        'secret-policy = "loud"',
        'chunk-large-files = "yes"',
    ],
)
def test_mistyped_values_are_rejected_with_file_and_key(tmp_path, line):
    repo = _repo(tmp_path)
    (repo / ".repo2graph.toml").write_text(line + "\n", encoding="utf8")
    with pytest.raises(ConfigError) as exc:
        load(repo)
    assert ".repo2graph.toml" in str(exc.value)
    assert repr(line.split(" = ")[0]) in str(exc.value)


def test_unparseable_repo2graph_toml_is_an_error(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".repo2graph.toml").write_text("exclude = [\n", encoding="utf8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load(repo)


def test_unparseable_pyproject_is_noted_and_ignored(tmp_path, capsys):
    """The project's file, not ours: indexing never needed it to parse."""
    repo = _repo(tmp_path)
    (repo / "pyproject.toml").write_text("[tool.repo2graph\n", encoding="utf8")
    assert not load(repo)
    assert "ignored" in capsys.readouterr().err


def test_without_a_toml_parser_config_is_skipped_with_one_note(tmp_path, monkeypatch, capsys):
    """Python 3.10 without tomli: no new hard dependency, so skip and say so."""
    monkeypatch.setattr(config, "_toml_loads", lambda: None)
    repo = _repo(tmp_path)
    assert not load(repo)
    assert capsys.readouterr().err == ""  # nothing to skip, nothing said
    _pyproject(repo, 'exclude = ["gen/**"]\n')
    assert not load(repo)
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "tomli" in err


# --- build -----------------------------------------------------------------


def test_build_applies_the_config_exclude(tmp_path):
    repo = _repo(tmp_path)
    _pyproject(repo, 'exclude = ["gen/**"]\n')
    out = tmp_path / "out"
    assert main(["build", str(repo), "-o", str(out), "--formats", "jsonl"]) == 0
    paths = _paths(out)
    assert "src/app.py" in paths
    assert "gen/made.py" not in paths


def test_cli_list_replaces_the_config_list(tmp_path):
    repo = _repo(tmp_path)
    _pyproject(repo, 'exclude = ["gen/**"]\n')
    out = tmp_path / "out"
    main(["build", str(repo), "-o", str(out), "--formats", "jsonl", "--exclude", "src/**"])
    paths = _paths(out)
    assert "gen/made.py" in paths
    assert "src/app.py" not in paths


def test_bad_config_stops_the_build_with_a_clear_error(tmp_path):
    repo = _repo(tmp_path)
    _pyproject(repo, "nope = 1\n")
    with pytest.raises(SystemExit) as exc:
        main(["build", str(repo), "-o", str(tmp_path / "out")])
    assert "pyproject.toml [tool.repo2graph]: unknown key 'nope'" in str(exc.value)


def _captured_args(monkeypatch, argv):
    seen = {}
    monkeypatch.setattr(cli, "cmd_build", lambda args: seen.setdefault("args", args) and 0)
    main(argv)
    return seen["args"]


def test_a_flag_typed_at_its_default_still_beats_the_file(tmp_path, monkeypatch):
    """`--git-history 0` typed on purpose is not "flag absent"."""
    repo = _repo(tmp_path)
    _pyproject(repo, 'git-history = 50\nsecret-keywords = ["acme"]\nmax-file-mb = 3.0\n')

    args = _captured_args(monkeypatch, ["build", str(repo), "--git-history", "0"])
    merged, applied = cli._with_repo_config(args, repo)
    assert merged.git_history == 0
    assert merged.max_file_mb == 3.0
    assert merged.extra_secret_keywords == ["acme"]
    assert "git_history" not in applied.values

    args = _captured_args(monkeypatch, ["build", str(repo), "--secret-keyword", "other"])
    merged, _ = cli._with_repo_config(args, repo)
    assert merged.git_history == 50
    assert merged.extra_secret_keywords == ["other"]


def test_explicit_flag_detection_restores_parser_defaults(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    args = _captured_args(monkeypatch, ["build", str(repo), "--max-file-mb", "1.5"])
    assert "max_file_mb" in args.explicit_flags
    assert "git_history" not in args.explicit_flags
    assert args.git_history == 0  # the real default survived the second parse


def test_hand_built_namespace_keeps_every_value_it_carries(tmp_path):
    repo = _repo(tmp_path)
    _pyproject(repo, "git-history = 50\ninclude-vendor = true\n")
    merged, applied = cli._with_repo_config(argparse.Namespace(git_history=0), repo)
    assert merged.git_history == 0
    assert merged.include_vendor is True
    assert set(applied.values) == {"include_vendor"}


# --- explain-path ------------------------------------------------------------


def _explain(capsys, repo, *extra):
    capsys.readouterr()
    main(["explain-path", *extra, "-r", str(repo), "--json"])
    return json.loads(capsys.readouterr().out)


def test_explain_path_attributes_a_config_exclude_to_the_file(tmp_path, capsys):
    repo = _repo(tmp_path)
    _pyproject(repo, 'exclude = ["gen/**"]\n')
    res = _explain(capsys, repo, "gen/made.py")
    assert res["rule"] == "exclude_glob"
    assert "pyproject.toml [tool.repo2graph] exclude" in res["reason"]
    assert res["source"] == "pyproject.toml [tool.repo2graph] exclude"

    res = _explain(capsys, repo, "gen/made.py", "--exclude", "other/**")
    assert res["included"] is True


def test_explain_path_attributes_config_include_and_exclude_dir(tmp_path, capsys):
    repo = _repo(tmp_path)
    (repo / ".repo2graph.toml").write_text(
        'include = ["src/**"]\nexclude-dir = ["gen"]\n', encoding="utf8"
    )
    res = _explain(capsys, repo, "gen/made.py")
    assert res["rule"] == "skip_dir"
    assert res["source"] == ".repo2graph.toml exclude-dir"
    (repo / "top.py").write_text("x = 1\n", encoding="utf8")
    res = _explain(capsys, repo, "top.py")
    assert res["rule"] == "not_included"
    assert res["source"] == ".repo2graph.toml include"


def test_explain_path_without_config_has_no_source_key(tmp_path, capsys):
    repo = _repo(tmp_path)
    res = _explain(capsys, repo, "gen/made.py", "--exclude", "gen/**")
    assert res["reason"] == "Path matches exclude glob 'gen/**'"
    assert "source" not in res


# --- the auto-builds -----------------------------------------------------------


def test_rag_source_auto_build_applies_the_config(tmp_path, capsys):
    repo = _repo(tmp_path)
    _pyproject(repo, 'exclude = ["gen/**"]\n')
    out = tmp_path / "idx"
    assert main(["rag", str(repo), "run", "-o", str(out)]) == 0
    assert "gen/made.py" not in _paths(out)
    assert "src/app.py" in _paths(out)


def test_mcp_auto_build_applies_the_config(tmp_path, monkeypatch):
    from repo2graph.mcp import indexes

    repo = _repo(tmp_path)
    (repo / "vaultcfg").mkdir()
    (repo / "vaultcfg" / "s.py").write_text("X = 1\n", encoding="utf8")
    (repo / "keep").mkdir()
    (repo / "keep" / "k.py").write_text("K = 1\n", encoding="utf8")
    _pyproject(repo, 'exclude = ["gen/**"]\nsecret-dirs = ["vaultcfg"]\n')
    out = repo / ".r2g"
    indexes._build_index(repo, out)
    paths = _paths(out)
    assert "gen/made.py" not in paths
    assert "vaultcfg/s.py" not in paths

    # The server's own --secret-dir replaces the file's list, as on the CLI.
    monkeypatch.setitem(indexes.SECRET_RULES, "dirs", ["keep"])
    out2 = tmp_path / "idx2"
    indexes._build_index(repo, out2)
    paths = _paths(out2)
    assert "keep/k.py" not in paths
    assert "vaultcfg/s.py" in paths


def test_mcp_auto_build_rejects_a_bad_config_as_a_value_error(tmp_path):
    from repo2graph.mcp import indexes

    repo = _repo(tmp_path)
    _pyproject(repo, "nope = 1\n")
    with pytest.raises(ValueError, match="unknown key 'nope'"):
        indexes._build_index(repo, repo / ".r2g")

"""Dedicated comprehensive hardening tests for secrets handling.

Covers:
- Issue 1 (#260): Secret exclusion secure-by-default across all artifact flows.
- Issue 2 (#261): Content-aware secret scanning and line-preserving redaction.
- Issue 3 (#262): Structured logging and audit log sanitization (depth/length/cycle guards, URL credentials, tokens).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from repo2graph.cli import main
from repo2graph.secrets import (
    MAX_CONTAINER_ITEMS,
    _is_secret_path,
    redact_content,
    sanitize_headers,
    sanitize_params,
    sanitize_url,
    sanitize_value,
    scan_content_secrets,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
if sys.platform == "win32":
    git_bash = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
    )
    if git_bash.is_file():
        BASH = str(git_bash)
    elif BASH and "system32" in BASH.lower():
        BASH = None


# ---------------------------------------------------------------------------
# Issue 1 (#260): Secure-by-default path exclusion
# ---------------------------------------------------------------------------


def test_is_secret_path_detection():
    """Verify built-in secret filenames, extensions, and directory paths."""
    # Dotenv variations
    assert _is_secret_path(".env")
    assert _is_secret_path(".env.local")
    assert _is_secret_path(".env.production")
    assert _is_secret_path("config/.env.test")
    assert _is_secret_path("sub/dir/custom.env")

    # Sensitive key/cert/token files
    assert _is_secret_path("id_rsa")
    assert _is_secret_path("id_ecdsa")
    assert _is_secret_path("id_ed25519")
    assert _is_secret_path("server.key")
    assert _is_secret_path("cert.pem")
    assert _is_secret_path("bundle.pfx")
    assert _is_secret_path("token.secret")
    assert _is_secret_path("secret_key.txt")
    assert _is_secret_path("credentials.json")
    assert _is_secret_path("gcp_credentials.json")

    # Secret directories
    assert _is_secret_path(".ssh/id_rsa.pub")
    assert _is_secret_path(".aws/credentials")
    assert _is_secret_path(".gnupg/pubring.kbx")
    assert _is_secret_path("secrets/app.conf")

    # Extra keywords and dirs
    assert _is_secret_path("config/corp_api_token_vault.yaml", extra_keywords=["api_token_vault"])
    assert _is_secret_path("custom_keys/key.txt", extra_dirs=["custom_keys"])

    # Safe files must NOT match
    assert not _is_secret_path("src/index.ts")
    assert not _is_secret_path("src/tokenizer.py")
    assert not _is_secret_path("react-app-env.d.ts")
    assert not _is_secret_path("tests/test_audit.py")
    assert not _is_secret_path("README.md")
    assert not _is_secret_path("package.json")


def test_build_excludes_secrets_by_default(tmp_path: Path):
    """By default, repo2graph build drops secrets and records skipped_secret count."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def hello(): return 'world'\n", encoding="utf8")
    (src / "service.py").write_text("class Service: pass\n", encoding="utf8")
    (src / ".env").write_text("DATABASE_URL=postgres://localhost/db\n", encoding="utf8")
    (src / ".env.local").write_text("SECRET=xyz\n", encoding="utf8")
    (src / "id_rsa").write_text("OPENSSH PRIVATE KEY\n", encoding="utf8")
    (src / "credentials.json").write_text('{"client_secret": "abc"}\n', encoding="utf8")
    (src / "server.pem").write_text("CERTIFICATE DATA\n", encoding="utf8")

    out = tmp_path / "out"
    rc = main(["build", str(src), "-o", str(out), "--formats", "jsonl,overview"])
    assert rc == 0

    stats_path = out / "agent" / "stats.json"
    assert stats_path.is_file()
    stats = json.loads(stats_path.read_text(encoding="utf8"))
    assert stats.get("skipped_secret") == 5

    manifest_path = out / "agent" / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    assert manifest.get("secret_filter_policy") == "redact-match"

    # Verify nodes.jsonl has no secret file nodes
    nodes_path = out / "agent" / "nodes.jsonl"
    nodes = [
        json.loads(line)
        for line in nodes_path.read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    indexed_paths = {n.get("path") for n in nodes if n.get("type") == "file"}
    assert "app.py" in indexed_paths
    assert "service.py" in indexed_paths
    assert ".env" not in indexed_paths
    assert ".env.local" not in indexed_paths
    assert "id_rsa" not in indexed_paths
    assert "credentials.json" not in indexed_paths
    assert "server.pem" not in indexed_paths


def test_build_include_secrets_flag(tmp_path: Path):
    """With --include-secrets, secret files are included and manifest records policy."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def hello(): return 'world'\n", encoding="utf8")
    (src / ".env").write_text("DB_HOST=127.0.0.1\n", encoding="utf8")

    out = tmp_path / "out"
    rc = main(["build", str(src), "-o", str(out), "--formats", "jsonl", "--include-secrets"])
    assert rc == 0

    stats = json.loads((out / "agent" / "stats.json").read_text(encoding="utf8"))
    assert stats.get("skipped_secret", 0) == 0

    manifest = json.loads((out / "agent" / "manifest.json").read_text(encoding="utf8"))
    assert manifest.get("secret_filter_policy") == "include-secrets"

    nodes = [
        json.loads(line)
        for line in (out / "agent" / "nodes.jsonl").read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    indexed_paths = {n.get("path") for n in nodes if n.get("type") == "file"}
    assert ".env" in indexed_paths
    assert "app.py" in indexed_paths


def test_build_custom_secret_flags(tmp_path: Path):
    """Custom --secret-keyword and --secret-dir flags exclude targeted paths."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def run(): pass\n", encoding="utf8")
    (src / "custom_auth_store.txt").write_text("token_secret = 123\n", encoding="utf8")
    corp_keys = src / "corp_keys"
    corp_keys.mkdir()
    (corp_keys / "prod.conf").write_text("key = abc\n", encoding="utf8")

    out = tmp_path / "out"
    rc = main(
        [
            "build",
            str(src),
            "-o",
            str(out),
            "--formats",
            "jsonl",
            "--secret-keyword",
            "auth_store",
            "--secret-dir",
            "corp_keys",
        ]
    )
    assert rc == 0

    nodes = [
        json.loads(line)
        for line in (out / "agent" / "nodes.jsonl").read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    indexed_paths = {n.get("path") for n in nodes if n.get("type") == "file"}
    assert "app.py" in indexed_paths
    assert "custom_auth_store.txt" not in indexed_paths
    assert "corp_keys/prod.conf" not in indexed_paths


# ---------------------------------------------------------------------------
# Issue 2 (#261): Content-aware secret scanning and redaction
# ---------------------------------------------------------------------------


def test_scan_content_secrets_matches():
    """Verify pattern matches for cloud keys, tokens, keys, DB URLs."""
    text = (
        "aws_key = 'AKIAIOSFODNN7EXAMPLE'\n"
        "gh_token = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345'\n"
        "slack = 'xoxb-1234567890-abcdef123456'\n"
        "openai = 'sk-abcdefghijklmnopqrstuvwxyz1234567890'\n"
        "google = 'AIza" + "B" * 35 + "'\n"
        "db_url = 'postgres://user:secretPassword123@db.example.com:5432/app'\n"
    )
    matches = scan_content_secrets(text)
    match_types = {m[0] for m in matches}
    assert "aws_access_key" in match_types
    assert "github_token" in match_types
    assert "slack_token" in match_types
    assert "openai_key" in match_types
    assert "google_key" in match_types
    assert "DATABASE_PASSWORD" in match_types


def test_redact_content_preserves_line_count():
    """Line preserving: text.split('\\n') count must match exactly before and after."""
    text = (
        "line 1: normal code\n"
        "line 2: api_key = 'AKIAIOSFODNN7EXAMPLE'\n"
        "line 3: -----BEGIN RSA PRIVATE KEY-----\n"
        "line 4: MIIEowIBAAKCAQEA0m...\n"
        "line 5: -----END RSA PRIVATE KEY-----\n"
        "line 6: normal ending\n"
    )
    redacted, count = redact_content(text)
    assert count >= 2
    assert text.split("\n") != redacted.split("\n")
    assert len(text.split("\n")) == len(redacted.split("\n"))
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "[REDACTED:aws_access_key" in redacted
    assert "line 1: normal code" in redacted
    assert "line 6: normal ending" in redacted


def test_chunking_with_secret_policy_redact(tmp_path: Path):
    """build_chunks with policy 'redact-match' redacts secrets in-place."""
    src = tmp_path / "src"
    src.mkdir()
    code = (
        "def connect():\n    token = 'ghp_0123456789abcdefghijklmnopqrstuvwxyz'\n    return token\n"
    )
    (src / "client.py").write_text(code, encoding="utf8")

    out = tmp_path / "out"
    rc = main(
        ["build", str(src), "-o", str(out), "--formats", "jsonl", "--secret-policy", "redact-match"]
    )
    assert rc == 0

    chunks_file = out / "agent" / "chunks.jsonl"
    assert chunks_file.is_file()
    lines = [
        json.loads(line)
        for line in chunks_file.read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    assert len(lines) >= 1
    found_redacted = False
    for chunk in lines:
        assert "ghp_0123456789" not in chunk["text"]
        if "[REDACTED:github_token" in chunk["text"]:
            found_redacted = True
    assert found_redacted


def test_chunking_with_secret_policy_exclude_file(tmp_path: Path):
    """build_chunks with policy 'exclude-file' skips chunks from infected file."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.py").write_text("def ok(): return 42\n", encoding="utf8")
    (src / "leaky.py").write_text(
        "def leak(): token = 'ghp_0123456789abcdefghijklmnopqrstuvwxyz'\n", encoding="utf8"
    )

    out = tmp_path / "out"
    rc = main(
        ["build", str(src), "-o", str(out), "--formats", "jsonl", "--secret-policy", "exclude-file"]
    )
    assert rc == 0

    chunks_file = out / "agent" / "chunks.jsonl"
    lines = [
        json.loads(line)
        for line in chunks_file.read_text(encoding="utf8").split("\n")
        if line.strip()
    ]
    paths = {c["path"] for c in lines}
    assert "clean.py" in paths
    assert "leaky.py" not in paths


def test_false_positive_resistance():
    """Ordinary programming patterns must NOT be falsely identified or redacted."""
    safe_snippets = [
        "token = get_token()",
        "api_key: str | None = None",
        "def authenticate(password: str) -> bool: pass",
        "import tokenizer",
        "MAX_TOKEN_LENGTH = 512",
        "from transformers import AutoTokenizer",
    ]
    for s in safe_snippets:
        matches = scan_content_secrets(s)
        assert matches == [], f"False positive in {s!r}: {matches}"
        redacted, count = redact_content(s)
        assert redacted == s
        assert count == 0


# ---------------------------------------------------------------------------
# Issue 3 (#262): Audit and logging sanitization
# ---------------------------------------------------------------------------


def test_sanitization_depth_and_cycle_limits():
    """sanitize_params handles circular references and depth recursion safely."""
    # Deeply nested structure > 12 levels
    deep = {}
    curr = deep
    for i in range(16):
        curr["nest"] = {}
        curr = curr["nest"]
    curr["val"] = "deep_secret"

    sanitized = sanitize_params(deep)
    # Check that it terminated and contains depth truncation marker
    assert "[truncated:depth]" in str(sanitized)

    # Circular reference
    circ: dict = {"name": "circ"}
    circ["self"] = circ
    sanitized_circ = sanitize_params(circ)
    assert sanitized_circ["name"] == "circ"
    assert sanitized_circ["self"] == "[circular:ref]"


def test_sanitization_container_item_limit():
    """Containers exceeding MAX_CONTAINER_ITEMS are capped."""
    big_list = list(range(200))
    sanitized = sanitize_value("", big_list)
    assert isinstance(sanitized, list)
    assert len(sanitized) == MAX_CONTAINER_ITEMS + 1
    assert sanitized[-1].startswith("[truncated:+")


def test_sanitization_url_credentials():
    """URLs with credentials have passwords scrubbed."""
    raw_url = "https://admin:SuperSecretPass123!@git.company.internal/org/repo.git"
    sanitized = sanitize_url(raw_url)
    assert "SuperSecretPass123" not in sanitized
    assert "[redacted:password]" in sanitized
    assert "git.company.internal/org/repo.git" in sanitized


def test_sanitization_headers():
    """Sensitive headers (Authorization, x-api-key, Cookie) are redacted."""
    headers = {
        "Authorization": "Bearer ya29.a0AfH6SM...",
        "x-api-key": "sk-1234567890abcdef",
        "Cookie": "session=xyz123; auth=true",
        "Content-Type": "application/json",
        "Accept": "*/*",
    }
    sanitized = sanitize_headers(headers)
    assert "[redacted:header:authorization" in sanitized["Authorization"]
    assert "[redacted:header:x-api-key" in sanitized["x-api-key"]
    assert "[redacted:header:cookie" in sanitized["Cookie"]
    assert sanitized["Content-Type"] == "application/json"
    assert sanitized["Accept"] == "*/*"


# ---------------------------------------------------------------------------
# Action.yml integration tests
# ---------------------------------------------------------------------------


def _action_step_by_name(text: str, name: str) -> str:
    import re

    for chunk in re.split(r"\n(?=    - (?:name|uses):)", text):
        if re.search(rf"^\s+- name: {re.escape(name)}\s*$", chunk, re.M):
            return chunk
    raise AssertionError(f"action.yml has no step named {name!r}")


def _run_body(chunk: str) -> str:
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


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_action_yml_secret_flags_forwarding(tmp_path: Path):
    """Verify action.yml passes --include-secrets and --secret-policy when configured."""
    action_yml = REPO_ROOT / "action.yml"
    body = _run_body(_action_step_by_name(action_yml.read_text(encoding="utf8"), "Build the graph"))

    log = tmp_path / "r2g-calls.log"
    script = (
        'repo2graph() { printf "%s\\x1f" "$@" >> "$R2G_LOG"; printf "\\n" >> "$R2G_LOG"; echo \'{"nodes": 1, "edges": 0, "chunks": 1}\'; }\n'
        + body
    )
    env = dict(os.environ)
    env.update(
        GH_TOKEN="",
        R2G_REPO="",
        R2G_REF="",
        R2G_PATH=".",
        R2G_OUT=str(tmp_path / "out"),
        R2G_FORMATS="jsonl",
        R2G_HISTORY="0",
        R2G_INCLUDE="",
        R2G_EXCLUDE="",
        R2G_INCLUDE_SECRETS="True",  # test case-insensitive truthiness
        R2G_SECRET_POLICY="exclude-file",
        R2G_LOG=str(log),
        GITHUB_OUTPUT=str(tmp_path / "github_output.txt"),
        SUMMARY_FILE=str(tmp_path / "summary.json"),
    )
    (tmp_path / "summary.json").write_text(
        '{"nodes": 1, "edges": 0, "chunks": 1}\n', encoding="utf8"
    )

    proc = subprocess.run(
        [BASH, "-c", script], cwd=str(tmp_path), env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr

    calls = []
    for line in log.read_text(encoding="utf8").split("\n"):
        if line:
            calls.append(line.split("\x1f")[:-1])

    assert len(calls) == 1
    call = calls[0]
    assert "--include-secrets" in call
    assert "--secret-policy" in call
    policy_idx = call.index("--secret-policy")
    assert call[policy_idx + 1] == "exclude-file"


@pytest.mark.skipif(not BASH, reason="the composite step's shell is bash")
def test_action_yml_incremental_and_parse_policy_forwarding(tmp_path: Path):
    """Verify action.yml passes --incremental, --parse-policy, and --max-call-candidates (#346)."""
    action_yml = REPO_ROOT / "action.yml"
    body = _run_body(_action_step_by_name(action_yml.read_text(encoding="utf8"), "Build the graph"))

    log = tmp_path / "r2g-calls.log"
    script = (
        'repo2graph() { printf "%s\\x1f" "$@" >> "$R2G_LOG"; printf "\\n" >> "$R2G_LOG"; echo \'{"nodes": 1, "edges": 0, "chunks": 1}\'; }\n'
        + body
    )
    env = dict(os.environ)
    env.update(
        GH_TOKEN="",
        R2G_REPO="",
        R2G_REF="",
        R2G_PATH=".",
        R2G_OUT=str(tmp_path / "out"),
        R2G_FORMATS="jsonl",
        R2G_HISTORY="0",
        R2G_INCLUDE="",
        R2G_EXCLUDE="",
        R2G_INCLUDE_SECRETS="false",
        R2G_SECRET_POLICY="redact-match",
        R2G_INCREMENTAL="TRUE",  # case-insensitive check
        R2G_PARSE_POLICY="strict",
        R2G_MAX_CALL_CANDIDATES="3",
        R2G_LOG=str(log),
        GITHUB_OUTPUT=str(tmp_path / "github_output.txt"),
        SUMMARY_FILE=str(tmp_path / "summary.json"),
    )
    (tmp_path / "summary.json").write_text(
        '{"nodes": 1, "edges": 0, "chunks": 1}\n', encoding="utf8"
    )

    proc = subprocess.run(
        [BASH, "-c", script], cwd=str(tmp_path), env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr

    calls = []
    for line in log.read_text(encoding="utf8").split("\n"):
        if line:
            calls.append(line.split("\x1f")[:-1])

    assert len(calls) == 1
    call = calls[0]
    assert "--incremental" in call
    assert "--parse-policy" in call
    assert call[call.index("--parse-policy") + 1] == "strict"
    assert "--max-call-candidates" in call
    assert call[call.index("--max-call-candidates") + 1] == "3"

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
import time
from pathlib import Path

import pytest

from repo2graph import secrets
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


def test_scan_content_secrets_returns_spans_not_plaintext():
    """A finding locates a credential; it must never carry one.

    Callers only count findings or report `f[0]`, so returning the matched
    bytes built a list of live credentials that existed solely to be thrown
    away -- and any later `emit(..., findings=findings)` would have shipped it
    verbatim. Offsets below are hand-counted against `text`: "tok = '" is 7
    characters, and the token is 36.
    """
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    text = f"tok = '{secret}'\n"

    findings = scan_content_secrets(text)

    assert findings == [("github_token", 7, 43)]
    # The span is the contract: a caller holding `text` can still recover it.
    stype, start, end = findings[0]
    assert text[start:end] == secret
    assert all(secret not in part for f in findings for part in f if isinstance(part, str))


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
# #338: the assignment guard skipped lowercase alphanumeric/hex secrets
#
# `ASSIGNMENT_RE`'s "is this just a plain identifier" gate used to be
# `re.fullmatch(r"[a-z0-9_]+", secret)`. A lowercase hex/alphanumeric secret
# -- an md5 hash, a lowercase API key -- matches that character class too, so
# the `not re.fullmatch(...)` guard was False and the digits-and-letters
# check below it never ran. Only a secret with an uppercase character (or a
# non-alnum char) ever reached the check. Fixed to `[a-z_]+`: a pure word
# (no digit) is still excluded; a lowercase token with digits now is not.
# ---------------------------------------------------------------------------


def test_iss338_lowercase_hex_assignment_is_detected():
    """The exact repro from #338: an all-lowercase hex value must be flagged."""
    text = 'api_key = "abcdef12345678901234567890123456"'
    findings = scan_content_secrets(text)
    assert findings == [("CREDENTIAL_ASSIGNMENT", 11, 43)]
    assert text[11:43] == "abcdef12345678901234567890123456"


def test_iss338_mixed_case_assignment_still_detected():
    """The pre-fix behaviour (one uppercase char flips detection on) must hold too."""
    text = 'api_key = "Abcdef12345678901234567890123456"'
    findings = scan_content_secrets(text)
    assert findings == [("CREDENTIAL_ASSIGNMENT", 11, 43)]


def test_iss338_lowercase_alphanumeric_api_key_is_redacted():
    text = 'api_key = "4f6a8b1c2d3e4f5a6b7c8d9e0f1a2b3c"'
    redacted, count = redact_content(text)
    assert count == 1
    assert "4f6a8b1c2d3e4f5a6b7c8d9e0f1a2b3c" not in redacted
    assert "[REDACTED:CREDENTIAL_ASSIGNMENT]" in redacted


@pytest.mark.parametrize(
    "text",
    [
        'default_option = "none"',
        'auth_key = "plainwordnodigitshere"',
        'secret = "just_snake_case_words_only"',
    ],
)
def test_iss338_pure_word_assignments_stay_ignored(text):
    """Plain identifiers/words -- no digit at all -- must still be ignored.

    This is the regression guard: a fix that widens detection to *any*
    lowercase string (not just ones mixing letters and digits) would flag
    these, which is exactly the false-positive failure mode this module must
    avoid.
    """
    assert scan_content_secrets(text) == []
    redacted, count = redact_content(text)
    assert redacted == text
    assert count == 0


# ---------------------------------------------------------------------------
# #368: modern vendor key prefixes and secret config paths
# ---------------------------------------------------------------------------

# One fixture per added prefix: (secret_type, sample_text). Built with
# structurally-invalid-but-shaped values (repeated filler characters) so
# nothing here resembles a live credential.
_VENDOR_FIXTURES: tuple[tuple[str, str], ...] = (
    ("anthropic_key", "sk-ant-" + "A" * 24),
    ("github_fine_grained_pat", "github_pat_" + "A" * 24),
    ("gitlab_token", "glpat-" + "A" * 24),
    ("google_oauth_client_secret", "GOCSPX-" + "A" * 24),
    ("stripe_key", "sk_live_" + "A" * 24),
    ("stripe_key", "rk_live_" + "A" * 24),
    ("stripe_webhook_secret", "whsec_" + "A" * 24),
    ("npm_token", "npm_" + "A" * 24),
    ("pypi_token", "pypi-AgEIcHlwaS5vcmc" + "A" * 24),
    ("huggingface_token", "hf_" + "A" * 24),
    ("digitalocean_token", "dop_v1_" + "a" * 24),
    ("shopify_token", "shpat_" + "a" * 24),
    ("shopify_token", "shpss_" + "a" * 24),
    ("sendgrid_key", "SG." + "A" * 20 + "." + "B" * 20),
    ("telegram_bot_token", "123456789:" + "A" * 35),
)


@pytest.mark.parametrize("expected_type,secret", _VENDOR_FIXTURES)
def test_iss368_vendor_prefix_is_scanned(expected_type, secret):
    text = f'token = "{secret}"'
    findings = scan_content_secrets(text)
    types = {f[0] for f in findings}
    assert expected_type in types, f"{secret!r} not detected as {expected_type}: {findings}"


@pytest.mark.parametrize("expected_type,secret", _VENDOR_FIXTURES)
def test_iss368_vendor_prefix_is_redacted_preserving_lines(expected_type, secret):
    """redact-match redacts the fixture and the newline count is unchanged."""
    text = f"line one\ntoken = '{secret}'\nline three\n"
    redacted, count = redact_content(text)
    assert count >= 1
    assert secret not in redacted
    assert f"[REDACTED:{expected_type}]" in redacted
    assert len(text.split("\n")) == len(redacted.split("\n"))
    assert "line one" in redacted
    assert "line three" in redacted


def test_iss368_anthropic_key_types_correctly_not_as_generic_openai():
    """`sk-ant-...` must be typed as anthropic_key, not the looser openai_key.

    Both patterns match the same span; this is the ordering/dedup contract
    documented next to CONTENT_SECRET_PATTERNS in secrets.py.
    """
    secret = "sk-ant-" + "A" * 24
    redacted, count = redact_content(f'x = "{secret}"')
    assert count == 1
    assert "[REDACTED:anthropic_key]" in redacted
    assert "openai_key" not in redacted


# _is_secret_path: one assertion per new path form added for #368.
@pytest.mark.parametrize(
    "path",
    [
        ".pypirc",
        "home/.pypirc",
        ".terraformrc",
        "opt/.terraformrc",
        "server.keytab",
        "keys/server.keytab",
        "putty.ppk",
        "keys/putty.ppk",
        ".docker/config.json",
        "home/user/.docker/config.json",
        ".m2/settings.xml",
        "project/.m2/settings.xml",
        ".gradle/gradle.properties",
        "repo/.gradle/gradle.properties",
        ".config/gh/hosts.yml",
        "home/.config/gh/hosts.yml",
    ],
)
def test_iss368_new_secret_paths_are_detected(path):
    assert _is_secret_path(path), f"{path!r} should be classified as a secret path"


@pytest.mark.parametrize(
    "path",
    [
        # The vendor-path match is deliberately narrow: sibling files in the
        # same dot-directory that are not the documented credential file must
        # stay unflagged, or an entire tool-config directory gets dropped
        # from every build for no reason.
        ".docker/daemon.json",
        ".config/gh/config.yml",
        "gradle.properties",  # bare filename outside a .gradle/ dir
        "settings.xml",  # bare filename outside a .m2/ dir
        "src/hosts.yml",
    ],
)
def test_iss368_sibling_files_in_vendor_dirs_stay_unflagged(path):
    assert not _is_secret_path(path), f"{path!r} should NOT be classified as a secret path"


# ---------------------------------------------------------------------------
# False-positive corpus for the widened detection (#338 + #368)
#
# A false positive here is worse than a false negative: it silently drops
# real source text out of every RAG pack (AGENTS.md). Every item below must
# come back clean from both the scanner and the redactor.
# ---------------------------------------------------------------------------

FALSE_POSITIVE_CORPUS: tuple[str, ...] = (
    # git-style SHAs (hex, no vendor prefix)
    "commit_sha = '4f6a8b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a'",
    "parent = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'",
    # UUIDs
    'request_id = "550e8400-e29b-41d4-a716-446655440000"',
    "trace_id: 6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    # base64 asset blobs
    "icon = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1"
    "HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='",
    # lockfile integrity hashes (npm/yarn style)
    "integrity sha512-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOP"
    "QRSTUVWXYZ+/==",
    "resolved 'https://registry.npmjs.org/foo/-/foo-1.0.0.tgz'",
    # CSS colors
    "color: #ffffff; background: #123abc;",
    # npm/huggingface environment-variable *names*, not token values
    "npm_config_registry=https://registry.npmjs.org/",
    "npm_package_version=1.2.3",
    "hf_dataset_cache_dir=/tmp/cache",
    "hf_home = os.environ.get('HF_HOME')",
    # ordinary identifiers and test fixtures
    "api_key: str | None = None",
    "def authenticate(password: str) -> bool: pass",
    "MAX_TOKEN_LENGTH = 512",
    "from transformers import AutoTokenizer",
    "token = get_token()",
    'default_option = "none"',
    'snake_case_identifier_without_digits = "just_a_plain_word"',
    'secret_type = "database_url"',
)


@pytest.mark.parametrize("text", FALSE_POSITIVE_CORPUS)
def test_false_positive_corpus_stays_clean(text):
    findings = scan_content_secrets(text)
    assert findings == [], f"False positive in {text!r}: {findings}"
    redacted, count = redact_content(text)
    assert redacted == text
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


# ---------------------------------------------------------------------------
# PEM pairing: correctness, and the cost of getting it wrong
#
# The single-pattern form (BEGIN, then a lazy [\s\S]*? to an optional END) is
# quadratic on input that repeats BEGIN without ever supplying an END: each
# BEGIN re-scans the whole remaining text before the optional group gives up.
# Repository content is attacker-supplied on every build and max_file_bytes
# defaults to 1.5 MB, so one committed file cost minutes of CPU per build.
# ---------------------------------------------------------------------------

BEGIN_PEM = "-----BEGIN RSA PRIVATE KEY-----"  # 31 chars
END_PEM = "-----END RSA PRIVATE KEY-----"  # 29 chars


def test_a_complete_pem_block_spans_begin_through_end():
    """Offsets hand-derived: 31 + len("\nBODY\n") + 29 == 66."""
    text = BEGIN_PEM + "\nBODY\n" + END_PEM
    assert len(text) == 66
    assert secrets._pem_spans(text) == [(0, 66)]
    assert ("private_key", 0, 66) in secrets.scan_content_secrets(text)


def test_an_unterminated_pem_block_spans_only_its_header():
    """No END means the header alone, which is what the optional group gave."""
    text = BEGIN_PEM + "\nnot actually a key\n"
    assert secrets._pem_spans(text) == [(0, 31)]


def test_a_begin_nested_inside_a_block_is_not_reported_twice():
    """finditer never restarts inside a match it already made; nor does this."""
    text = BEGIN_PEM + "\n" + BEGIN_PEM + "\n" + END_PEM
    spans = secrets._pem_spans(text)
    assert len(spans) == 1
    assert spans[0] == (0, len(text))


def test_two_separate_pem_blocks_pair_independently():
    one = BEGIN_PEM + "\nA\n" + END_PEM
    text = one + "\nfiller\n" + one
    spans = secrets._pem_spans(text)
    assert spans == [(0, len(one)), (len(one) + 8, len(text))]


def test_repeated_begin_without_end_stays_linear():
    """The detector for the quadratic form.

    At 200k characters the paired scan takes tens of milliseconds; the lazy
    single-pattern form took ~8 seconds, and ~8 minutes at the 1.5 MB file
    ceiling. The bound below sits far from both, so it survives a slow CI box
    without going vacuous.
    """
    text = (BEGIN_PEM + "\n") * (200_000 // 32)

    start = time.perf_counter()
    findings = secrets.scan_content_secrets(text)
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0, f"scan took {elapsed:.2f}s; the quadratic form is back"
    # Every BEGIN is still reported -- fast and wrong would be worse.
    assert len(findings) == 200_000 // 32
    assert {f[0] for f in findings} == {"private_key"}


def test_redact_content_on_repeated_begin_stays_linear():
    """redact_content runs the scan and then rewrites; both must stay bounded.

    Sized at 200k, not 100k: at 100k the quadratic form takes ~2s, which slips
    under this bound and makes the test a detector in name only.
    """
    text = (BEGIN_PEM + "\n") * (200_000 // 32)

    start = time.perf_counter()
    redacted, count = secrets.redact_content(text)
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0, f"redact took {elapsed:.2f}s"
    assert count == 200_000 // 32
    assert BEGIN_PEM not in redacted
    assert len(text.split("\n")) == len(redacted.split("\n"))


# ---------------------------------------------------------------------------
# NON_SECRET_KEYS: the audit log must stay readable
#
# SECRET_KEY_RE matches by substring, so `auth_modes` (which auth is in force)
# and `budget_tokens` (a count) were redacted -- costing an operator the two
# fields they most want when reading the log back, and telling them nothing.
# ---------------------------------------------------------------------------


def test_shape_describing_fields_are_not_redacted():
    """These name a shape; they never hold a credential."""
    assert sanitize_value("auth_modes", ["none", "token"]) == ["none", "token"]
    assert sanitize_value("budget_tokens", "4000") == "4000"
    assert sanitize_value("result_tokens", "1234") == "1234"


@pytest.mark.parametrize(
    "key",
    [
        "auth",
        "auth_token",
        "authorization",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "secret",
        "credential",
        "session",
        "cookie",
        "bearer",
        "signature",
        "access_key",
        "private_key",
        "AUTH_TOKEN",
        "Authorization",
    ],
)
def test_the_allowlist_does_not_weaken_the_key_rule(key):
    """Every credential-shaped name still redacts, in any casing."""
    out = sanitize_value(key, "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    assert out.startswith(f"[redacted:key:{key}"), out


def test_an_allowlisted_key_still_has_its_value_inspected():
    """Allowlisting the *name* must not blind the value checks.

    A credential that turns up under one of these names is still caught by the
    shape rules -- the allowlist skips the name test, not the rest.
    """
    got = sanitize_value("auth_modes", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    assert got.startswith("[redacted:github_token")

    got_url = sanitize_value("budget_tokens", "postgres://u:pw123456@host/db")
    assert got_url.startswith("[redacted:")
    assert "pw123456" not in got_url


def test_every_allowlisted_key_actually_matches_the_regex():
    """An entry that does not match SECRET_KEY_RE is dead weight.

    It would silently suggest the name is dangerous when the general rule
    never flagged it, which is how an allowlist rots into a list of guesses.
    """
    from repo2graph.secrets import NON_SECRET_KEYS, SECRET_KEY_RE

    for key in NON_SECRET_KEYS:
        assert SECRET_KEY_RE.search(key), f"{key!r} never needed allowlisting"
        assert key == key.lower(), f"{key!r} must be lowercased to be matched"

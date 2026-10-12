# SPDX-FileCopyrightText: 2026 Srinivasan Vijayaraghavan
#
# SPDX-License-Identifier: MIT

"""Credential formats and surfaces that slipped past secret handling.

Six verified gaps, each of which shipped a real credential in clear:

* docstrings and signatures were never scanned, yet they are copied into the
  ``# doc:`` chunk header, ``nodes.jsonl``, ``graph.html``, GraphML and Cypher;
* content redaction required a *quoted* value, so unquoted YAML, INI/.env,
  connection strings, Ansible ``*_pass`` keys and kubeconfig blobs all passed;
* a secret straddling the 4,000-character chunk split was scanned per-slice, so
  a PEM key matched only its BEGIN line and its body shipped intact;
* PGP and SSH2/PuTTY private keys were not recognised at all;
* MCP ``repo_read`` skipped the serve-time redaction that ``repo_search`` applies;
* a dozen common enterprise credential filenames were not treated as secrets.
"""

from pathlib import Path

import pytest

from repo2graph.graph import build
from repo2graph.security import (
    _is_secret_path,
    scan_content_secrets,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _types(text):
    return {t for t, _, _ in scan_content_secrets(text)}


# --------------------------------------------------------------------------
# Claim 4 - unquoted and enterprise credential formats
# --------------------------------------------------------------------------

UNQUOTED_CASES = {
    "ansible_become_pass": "ansible_become_pass: hunter2xyz\n",
    "ansible_ssh_pass": "ansible_ssh_pass: Sup3rSecret99\n",
    "unquoted_yaml": "db_password: s3cr3tValue\n",
    "dotenv": "DB_PASSWORD=hunter2!@#\n",
    "ini": "password=s3cr3tValue\n",
    "properties": "jdbc.password=Zm9vYmFy123\n",
    "ado_connection_string": "Server=db;User Id=sa;Password=hunter2!;\n",
    "jdbc_query": "jdbc:mysql://h/db?password=abc123xyz\n",
    "azure_account_key": "AccountKey=YWJjZGVmZ2hpamtsbW5vcA==;\n",
    "kubeconfig_client_key": "client-key-data: LS0tLS1CRUdJTlByaXZhdGU=\n",
    "pwd_key": "db_pwd=Passw0rd123\n",
    "special_characters": "PASSWORD=P@ssw0rd!#$\n",
}


def test_every_unquoted_credential_format_is_detected():
    missed = sorted(name for name, text in UNQUOTED_CASES.items() if not scan_content_secrets(text))
    assert missed == [], f"undetected credential formats: {missed}"


# Each of these contains a secret-ish *key* and must still not be flagged. The
# unquoted rule admits bare `pass`, `pwd` and `token`, which appear constantly
# in ordinary code, so over-redaction is the failure mode to guard against.
NOT_SECRETS = {
    "prose": "# password: required for login\n",
    "shell_placeholder": "password=${DB_PASSWORD}\n",
    "jinja_placeholder": "password: {{ vault_password }}\n",
    "angle_placeholder": "token=<your-token-here>\n",
    "masked": "password=********\n",
    "token_url": "tokenUrl: https://example.com/oauth/token\n",
    "secret_name": "secretName: my-app-secrets\n",
    "i18n_bundle": '"password": "Enter your password"\n',
    "function_call": "token = get_token()\n",
    "attribute_path": "self.token = self._refresh_token\n",
    "field_name": "PASSWORD_FIELD = 'password'\n",
    "api_key_header_name": "api_key_header = X-Api-Key\n",
    "length_constant": "secret_length = 32\n",
    "policy_reference": "password_policy = strict_policy\n",
    # A *mixed-case* dotted path. The escape hatch for code-shaped values read
    # `not _json_secret_value_ok(value) and <identifier>`, and the first conjunct
    # cancelled the second for exactly these: an attribute path has lower, upper
    # and -- because the dots count -- symbol, three of the four classes, so
    # `_json_secret_value_ok` said "real credential" and the hatch never opened.
    # Every case below shipped as `[REDACTED:CREDENTIAL_UNQUOTED]` into
    # chunks.jsonl, nodes.jsonl, graph.html, GraphML and Cypher under the default
    # `redact-match` policy, and this PR additionally routes every signature and
    # docstring through `redact_content`.
    "env_attribute_path": "const apiKey = process.env.API_KEY;\n",
    "settings_attribute": "secret = settings.SECRET_KEY\n",
    "environ_subscript": 'api_key = os.environ["API_KEY"]\n',
    "module_constant": "token = auth.DEFAULT_TOKEN\n",
    # Digits put this one outside the identifier class, so the call it opens is
    # what marks it as code -- the value class already excludes `(`, but that
    # only truncated the match at the paren instead of rejecting it.
    "dotted_call": "password = hashlib.sha256(raw).hexdigest()\n",
}


def test_ordinary_code_with_secret_shaped_names_is_not_redacted():
    flagged = sorted(name for name, text in NOT_SECRETS.items() if scan_content_secrets(text))
    assert flagged == [], f"over-redacted ordinary code: {flagged}"


# --------------------------------------------------------------------------
# Claim 6 - private-key armour beyond the PEM "-----BEGIN ... PRIVATE KEY-----"
# --------------------------------------------------------------------------


def test_pgp_private_key_block_is_recognised():
    text = (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
        "lQOYBF8tQ0YBCADKx\n"
        "-----END PGP PRIVATE KEY BLOCK-----\n"
    )
    assert "private_key" in _types(text)


def test_ssh2_private_key_is_recognised():
    """Four dashes and inner spaces, not the five-dash PEM form."""
    text = (
        "---- BEGIN SSH2 ENCRYPTED PRIVATE KEY ----\n"
        "Comment: rsa-key\n"
        "---- END SSH2 ENCRYPTED PRIVATE KEY ----\n"
    )
    assert "private_key" in _types(text)


def test_putty_private_key_header_is_recognised():
    """PuTTY has no BEGIN/END armour, so it cannot be paired -- matched alone."""
    assert "putty_private_key" in _types("PuTTY-User-Key-File-2: ssh-rsa\nEncryption: none\n")


def test_the_original_pem_labels_still_match():
    for label in ("", "RSA ", "DSA ", "EC ", "OPENSSH ", "ENCRYPTED ", "ENCRYPTED RSA "):
        text = f"-----BEGIN {label}PRIVATE KEY-----\nabc\n-----END {label}PRIVATE KEY-----\n"
        assert "private_key" in _types(text), label


def test_a_public_key_is_not_a_private_key():
    assert _types("-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----\n") == set()


# --------------------------------------------------------------------------
# Claim 8 - enterprise credential filenames
# --------------------------------------------------------------------------

SECRET_FILES = (
    "admin.conf",
    "cluster1.kubeconfig",
    ".vault_pass",
    "_netrc",
    "pgpass.conf",
    "local.settings.json",
    "appsettings.Production.json",
    "appsettings.Development.json",
    "web.config",
    "NuGet.Config",
    ".my.cnf",
    ".s3cfg",
    ".boto",
    "deploy.publishsettings",
)


def test_enterprise_credential_filenames_are_secret_paths():
    missed = sorted(p for p in SECRET_FILES if not _is_secret_path(p))
    assert missed == [], f"not treated as secret paths: {missed}"


def test_nested_enterprise_credential_paths_are_secret_paths():
    for p in ("deploy/admin.conf", "infra/group_vars/.vault_pass", "src/web.config"):
        assert _is_secret_path(p), p


def test_ordinary_config_files_are_not_secret_paths():
    """The base appsettings.json holds non-secret defaults, by convention."""
    for p in ("appsettings.json", "settings.json", "config.json", "app.conf", "tsconfig.json"):
        assert not _is_secret_path(p), p


# --------------------------------------------------------------------------
# Claim 5 - a secret longer than the chunk split
# --------------------------------------------------------------------------


def _pem_key(body_lines: int) -> str:
    body = "\n".join("MIIEowIBAAKCAQEAx" + "A" * 46 for _ in range(body_lines))
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n"


def test_a_pem_key_straddling_the_chunk_split_is_fully_redacted(tmp_path):
    """The key body used to survive: only the BEGIN line matched in its slice.

    `_pem_spans` pairs a BEGIN with the next END *within the text it is given*.
    Scanning each 4,000-character slice separately left slice one matching just
    its header line and slice two -- the base64 body and the END -- matching
    nothing, so the key shipped almost whole.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    # Padding pushes the split boundary into the middle of the key.
    padding = "\n".join(f"# filler line {i:04d} aaaaaaaaaaaaaaaaaaaaaaaa" for i in range(60))
    source = f'"""Module."""\n{padding}\n\nKEY = """\n{_pem_key(80)}"""\n'
    # The *source* has to cross the split for this to test anything. Asserting
    # on the chunk text instead would be circular: a correct fix redacts the key
    # body away, which shrinks the output back under the threshold.
    assert len(source) > 4000, "fixture must exceed the chunk split to be meaningful"
    (repo / "secrets.py").write_text(source, encoding="utf8", newline="\n")
    g = build(repo)
    from repo2graph.chunks import build_chunks

    text = "\n".join(c["text"] for c in build_chunks(g))
    # No run of real key material may survive anywhere in the chunk corpus.
    assert "MIIEowIBAAKCAQEAx" not in text
    assert "REDACTED" in text


# --------------------------------------------------------------------------
# Claim 3 - docstrings and signatures reach five outputs unscanned
# --------------------------------------------------------------------------

DOCSTRING_SECRET = (
    'def connect(host):\n    """Connect to the ledger.\n\n'
    "    Example:\n        ansible_become_pass: hunter2xyz\n"
    '    """\n    return host\n'
)


def _built(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(DOCSTRING_SECRET, encoding="utf8", newline="\n")
    return build(repo)


def test_a_secret_in_a_docstring_is_redacted_on_the_node(tmp_path):
    g = _built(tmp_path)
    docs = [n.get("docstring", "") for n in g.nodes.values() if n.get("type") == "symbol"]
    joined = "\n".join(docs)
    assert "hunter2xyz" not in joined, "docstring reaches nodes.jsonl, graph.html, GraphML, Cypher"
    assert "REDACTED" in joined


def test_a_secret_in_a_docstring_is_redacted_in_the_chunk_header(tmp_path):
    from repo2graph.chunks import build_chunks

    g = _built(tmp_path)
    text = "\n".join(c["text"] for c in build_chunks(g))
    assert "# doc:" in text, "fixture must produce a doc header to be meaningful"
    assert "hunter2xyz" not in text


def test_an_ordinary_docstring_is_left_alone(tmp_path):
    repo = tmp_path / "clean"
    repo.mkdir()
    (repo / "m.py").write_text(
        'def f():\n    """Return the password policy name."""\n    return 1\n',
        encoding="utf8",
        newline="\n",
    )
    g = build(repo)
    docs = "\n".join(n.get("docstring", "") for n in g.nodes.values())
    assert "Return the password policy name." in docs
    assert "REDACTED" not in docs


# --------------------------------------------------------------------------
# A token in a git remote must not reach manifest.json
# --------------------------------------------------------------------------

from repo2graph.security import sanitize_url  # noqa: E402


def test_a_bare_token_userinfo_in_a_remote_url_is_redacted():
    """`integrity.py` reads remote.origin.url into the manifest's provenance.

    The password rule required a `user:pass` pair, so the shape CI actually
    writes -- `https://<token>@github.com/o/r.git`, with the token as the whole
    userinfo and no password -- passed through untouched and was published in
    `manifest.json` alongside the index.
    """
    for token in ("ghp_AAAAAAAAAAAAAAAAAAAAAAAA", "glpat-xxxxxxxxxxxxxxxxxxxx"):
        url = f"https://{token}@github.com/o/r.git"
        out = sanitize_url(url)
        assert token not in out, out
        assert "redacted" in out


def test_the_user_colon_password_form_is_still_redacted():
    out = sanitize_url("https://x-access-token:ghp_SECRETVALUE@github.com/o/r.git")
    assert "ghp_SECRETVALUE" not in out
    assert "x-access-token" in out, "the user name is not the secret and stays readable"


def test_conventional_non_secret_usernames_are_left_alone():
    """`ssh://git@...` is the canonical SSH remote; redacting it loses information."""
    for url in ("ssh://git@github.com/o/r.git", "https://oauth2@gitlab.com/o/r.git"):
        assert sanitize_url(url) == url


def test_a_remote_with_no_credentials_is_unchanged():
    for url in ("https://github.com/o/r.git", "git@github.com:o/r.git"):
        assert sanitize_url(url) == url


# #453: formats stored in repositories by convention.
_K8S_SECRET = """apiVersion: v1
kind: Secret
metadata:
  name: db
type: Opaque
data:
  username: YWRtaW4=
  tls.crt: LS0tLS1CRUdJTg==
stringData:
  config: plainvalue
"""


def test_k8s_secret_data_values_are_redacted():
    from repo2graph.security import redact_content

    out, n = redact_content(_K8S_SECRET)
    for leaked in ("YWRtaW4=", "LS0tLS1CRUdJTg==", "plainvalue"):
        assert leaked not in out
    assert "name: db" in out and out.count("\n") == _K8S_SECRET.count("\n")


def test_k8s_secret_multiline_and_spaced_values_are_redacted():
    from repo2graph.security import redact_content

    src = (
        "kind: Secret\n"
        "stringData:\n"
        "  dsn: postgres admin hunter2\n"
        "  app.conf: |\n"
        "    upstream_user=admin\n"
        "    token=Zq8vLmN2pR\n"
        "metadata:\n"
        "  name: db\n"
    )
    out, _ = redact_content(src)
    for leaked in ("hunter2", "upstream_user=admin", "Zq8vLmN2pR"):
        assert leaked not in out, out
    assert "  app.conf: " in out and "name: db" in out
    assert out.count("\n") == src.count("\n")


def test_configmap_data_is_not_treated_as_secret():
    from repo2graph.security import redact_content

    src = "kind: ConfigMap\ndata:\n  color: blue\n"
    assert redact_content(src) == (src, 0)


def test_docker_config_auth_is_redacted():
    from repo2graph.security import redact_content

    src = '{"auths":{"r.example.com":{"auth":"dXNlcjpwYXNzd29yZDEyMw=="}}}'
    out, n = redact_content(src)
    assert n == 1 and "dXNlcjpwYXNzd29yZDEyMw" not in out
    assert redact_content('"auth": "basic"')[1] == 0


# #453: vendor formats, each with a near-miss that must stay in clear.
_VAULT = "hvs." + "CAESIJ" + "x" * 30
_SAS = "sv=2022-11-02&ss=b&srt=sco&sp=rl&se=2026-01-01&sig=" + "aB3%2Bz" * 6
_SLACK_HOOK = "https://hooks.slack.com/services/" + "T0000/B0000/" + "X" * 24
_TEAMS_HOOK = "https://acme.webhook.office.com/webhookb2/" + "a1b2c3d4-" * 4
_DISCORD_HOOK = "https://discord.com/api/webhooks/123456789/" + "Zz_" * 10


@pytest.mark.parametrize(
    "secret",
    [_VAULT, _SAS, _SLACK_HOOK, _TEAMS_HOOK, _DISCORD_HOOK],
    ids=["vault", "azure-sas", "slack-hook", "teams-hook", "discord-hook"],
)
def test_vendor_credentials_are_redacted(secret):
    from repo2graph.security import redact_content

    out, n = redact_content(f"url = '{secret}'\n")
    assert n >= 1 and secret not in out, out


@pytest.mark.parametrize(
    "text",
    [
        "hvs = compute_hash(values)\n",
        "docs: https://learn.microsoft.com/azure/storage/common/storage-sas-overview\n",
        "see https://api.slack.com/messaging/webhooks for the format\n",
        "sv=2022-11-02 is the service version\n",
    ],
)
def test_vendor_near_misses_are_left_alone(text):
    from repo2graph.security import redact_content

    assert redact_content(text) == (text, 0)


def test_ansible_vault_payload_is_redacted_with_its_header():
    from repo2graph.security import redact_content

    src = (
        "db_password: !vault |\n"
        "  $ANSIBLE_VAULT;1.1;AES256\n"
        "  62313365396662343061393464336163383764373764613633653634306231386433626436623361\n"
        "  6134333665353966363534333632666535333761666131620a663537646436643839616531643561\n"
        "other_key: plain\n"
    )
    out, n = redact_content(src)
    assert n >= 1 and "6231336539" not in out and "6134333665" not in out, out
    assert "other_key: plain" in out and out.count("\n") == src.count("\n")


def test_a_credentialed_url_under_a_secret_key_is_redacted_but_token_url_is_not():
    from repo2graph.security import redact_content

    out, _ = redact_content('{"webhookSecret": "https://user:hunter2pass@hooks.example.com/x"}\n')
    assert "hunter2pass" not in out
    src = "tokenUrl: https://example.com/oauth/token\n"
    assert redact_content(src) == (src, 0)


def test_source_dropped_for_its_directory_is_counted_and_announced(tmp_path, capsys):
    """S11: `secrets/manager.py` is skipped like any secret path, but not silently."""
    src = tmp_path / "proj"
    (src / "secrets").mkdir(parents=True)
    (src / "secrets" / "manager.py").write_text("def rotate():\n    return 1\n", encoding="utf8")
    (src / "secrets" / "prod.env").write_text("A=1\n", encoding="utf8")
    (src / "app.py").write_text("def main():\n    return 2\n", encoding="utf8")

    g = build(src)
    assert g.stats["skipped_secret"] == 2
    assert g.stats["skipped_secret_dir_source"] == 1
    assert "1 source file(s) under secret-named directories" in capsys.readouterr().err

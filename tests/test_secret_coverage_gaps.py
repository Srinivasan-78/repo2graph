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

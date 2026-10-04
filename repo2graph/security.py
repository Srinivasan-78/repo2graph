"""Centralized secret detection, content scanning, and redaction utilities.

Provides:
- Path-based secret classification (_is_secret_path).
- Content-aware secret scanning for AWS keys, tokens, private keys, JWTs, and DB URLs.
- Line-preserving redaction for chunks and code representations.
- Depth-bounded and cycle-safe sanitization for audit logs and structured events.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# File extensions that typically contain secrets/credentials.
SECRET_EXTS = frozenset(
    {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".pkcs12",
        ".p8",
        ".asc",
        ".gpg",
        ".der",
        ".cer",
        ".crt",
        ".ovpn",
        # A kubeconfig under any name (`cluster1.kubeconfig`): the bare
        # `kubeconfig` exact-name entry only caught the unsuffixed spelling.
        ".kubeconfig",
        # Azure publish profiles: deployment credentials in clear.
        ".publishsettings",
        ".kdbx",
        ".keystore",
        ".jks",
        ".secret",
        ".secrets",
        ".keytab",
        ".ppk",
        # Terraform state is a JSON dump of every managed resource's attributes,
        # database passwords and generated keys included, in plaintext; `.tfvars`
        # (and `.auto.tfvars`, which this suffix also covers) is where the inputs
        # to those resources -- `db_password = "..."` -- are written. Neither
        # extension is ever source code. `.tfstate.backup` is handled by the
        # `.backup` entry in BACKUP_SUFFIXES, which strips back to `.tfstate`.
        ".tfstate",
        ".tfvars",
        ".tfvars.json",
    }
)

# Config formats that may contain credentials if combined with sensitive keywords.
SECRET_CONFIG_EXTS = frozenset(
    {
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".ini",
        ".env",
        ".properties",
        ".conf",
        ".cfg",
        ".txt",
    }
)

# Keywords indicating sensitive files.
SECRET_KEYWORDS = (
    "secret",
    "credential",
    "token",
    "service-account",
    "service_account",
    "password",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    # Firebase names a downloaded Admin SDK key `<project>-firebase-adminsdk-<id>.json`;
    # the SECRET_CONFIG_EXTS gate below keeps `adminsdk.py`-style code indexable.
    "adminsdk",
)

# Exact filenames that are always treated as sensitive.
SECRET_EXACT_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".terraformrc",
        ".dockercfg",
        ".git-credentials",
        ".pgpass",
        ".htpasswd",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        # The file `KUBECONFIG` points at when it is not `~/.kube/config` (which
        # the `.kube` entry in SECRET_DIR_NAMES already covers). It holds cluster
        # credentials -- client certs, bearer tokens, or an exec plugin config.
        "kubeconfig",
        # The dotless spelling Apache and nginx docs use for the same file as
        # `.htpasswd`: user names and password hashes.
        "htpasswd",
        # --- Enterprise and cross-platform credential files ---
        # All of these were indexed as ordinary source: no entry here, no
        # matching extension, and no SECRET_KEYWORDS substring (note "settings"
        # is not "secret" and bare "pass" is not "password").
        #
        # kubeadm writes cluster-admin credentials here; the `.kube` directory
        # entry does not cover a file sitting in /etc/kubernetes or copied into
        # a repo.
        "admin.conf",
        # Windows spellings of files whose dotted forms are already listed:
        # PostgreSQL's password file and curl's netrc.
        "pgpass.conf",
        "_netrc",
        # Ansible vault password file -- the key to every vaulted secret in the
        # repo, and the one file that makes the rest decryptable.
        ".vault_pass",
        ".vault-password",
        # Azure Functions local settings: connection strings and AccountKeys.
        "local.settings.json",
        # ASP.NET / IIS: connection strings and machine keys.
        "web.config",
        # NuGet feed credentials (plaintext or reversibly encrypted).
        "nuget.config",
        # MySQL client credentials.
        ".my.cnf",
        # s3cmd and boto: AWS access key and secret key.
        ".s3cfg",
        ".boto",
        # Composer's per-project credential store (`http-basic`, `github-oauth`,
        # `gitlab-token` ...). Composer only ever reads this name for that purpose.
        "auth.json",
        # WordPress keeps DB_PASSWORD and the auth salts here; it is configuration,
        # not code anyone needs retrieved.
        "wp-config.php",
        # Rails encrypted credentials. Ciphertext, but useless to index and the
        # neighbour of `master.key` (caught by `.key`), which decrypts it.
        "credentials.yml.enc",
        # The name GCP docs and CI recipes give a downloaded service-account key
        # (`gcloud iam service-accounts keys create key.json`). Only this exact
        # name: `keys.json`, `keymap.json` and friends stay indexable.
        "key.json",
    }
)

#: Suffixes an editor, a script or a careless `cp` appends to a file it is about
#: to replace. `id_rsa.bak` and `server.key.bak` hold exactly what `id_rsa` and
#: `server.key` hold, so a backup is tested by stripping the suffix and asking
#: the same question again. Only one layer is stripped, and the `~` form is
#: handled separately because it carries no dot.
#: `.backup` is Terraform's spelling (`terraform.tfstate.backup`).
BACKUP_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".save", ".swp", ".tmp", "~")

#: Environment names in the *dotless* dotenv spelling. `.env.production` is
#: already caught by the `.env` family test, but `env.production` -- the spelling
#: a project uses when it wants the file visible in a listing -- was not, and it
#: holds the same values. Matched against an explicit set rather than by treating
#: every `env.*` as a secret: `env.py`, `env.ts` and `env.go` are ordinary
#: modules, and excluding those from the index would be a silent loss of code.
DOTENV_ENVIRONMENTS = frozenset(
    {
        "local",
        "dev",
        "development",
        "test",
        "testing",
        "stage",
        "staging",
        "prod",
        "production",
        "secret",
        "secrets",
    }
)

# Directory names that always contain sensitive files.
SECRET_DIR_NAMES = frozenset(
    {
        ".ssh",
        ".aws",
        ".kube",
        ".gnupg",
        "secrets",
        "credentials",
    }
)

# Specific multi-segment vendor config paths that hold credentials, but whose
# bare final component is too generic to blocklist outright -- "config.json"
# or "hosts.yml" alone are ordinary filenames elsewhere in a repo. Matched as
# a path *suffix* (whole path, or preceded by "/"), lowercase, "/"-normalized.
SECRET_PATH_SUFFIXES = (
    ".docker/config.json",
    ".m2/settings.xml",
    ".gradle/gradle.properties",
    ".config/gh/hosts.yml",
)

# `appsettings.<env>.json`, but not the base `appsettings.json`. Applied to the
# final path segment, already lowercased by `_is_secret_path`.
APPSETTINGS_OVERLAY_RE = re.compile(r"^appsettings\..+\.json$")

SECRET_WORD_RE = re.compile(r"[a-z0-9]+")

# Field names whose *value* is a credential whatever it looks like.
SECRET_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|api[-_]?key|auth|credential|private[-_]?key"
    r"|session|cookie|bearer|signature|access[-_]?key)",
    re.I,
)

# Field names that match SECRET_KEY_RE by substring but describe a *shape*
# rather than hold a credential -- `auth_modes` is ("none",)/("token",)/
# ("oidc",) and `budget_tokens` is a count. Redacting them cost the audit log
# the two fields an operator most wants when reading it back: which auth was
# in force, and how large the request was.
#
# An allowlist, not a narrower SECRET_KEY_RE: loosening the pattern to exclude
# `auth_modes` would also stop matching names nobody has written yet, and the
# failure mode there is a credential in a log. Every entry is an exact,
# lowercased field name, and adding one is a deliberate statement that this
# field's value is never sensitive. Note the values are not blindly trusted
# either -- they still go through the shape, URL and path checks below.
NON_SECRET_KEYS = frozenset({"auth_modes", "budget_tokens", "result_tokens", "max_tokens"})

# Sensitive HTTP headers to redact in logs.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-goog-api-key",
    }
)

# Sensitive query parameters to redact in logged URLs.
SENSITIVE_QUERY_PARAMS_RE = re.compile(
    r"(?i)([?&](?:token|key|api[-_]?key|secret|password|auth|access[-_]?token)=)([^&#]+)"
)

# A PEM block is matched as two separate anchors, paired in `_pem_spans`.
# Writing it as one pattern -- BEGIN, then a lazy `[\s\S]*?` to an optional
# END -- is quadratic: every BEGIN whose END is missing re-scans the entire
# remaining text before the optional group gives up. Repository content is
# attacker-supplied on every build and `max_file_bytes` defaults to 1.5 MB, so
# a single file of repeated BEGIN lines cost minutes of CPU per build.
#
# The label repetition is *bounded*, and that bound is the whole point rather
# than tidiness: `[-A-Z0-9_ ]` contains every character of the `PRIVATE KEY`
# literal that follows it, so an unbounded `*` has to backtrack the entire tail
# at every one of the n/11 offsets where `-----BEGIN ` matches. A body of
# repeated *incomplete* headers is therefore quadratic -- measured 1.1 s at
# 107 KB and ~90 s at 1 MB, which `MAX_BODY_BYTES` admits in a single request.
# That is reachable pre-authentication: `http_server._reject` calls `emit()`
# unconditionally, so sanitising a rejected request's own field burns the CPU
# before the 401 is written, and `AuditConfig(level="none")` does not avoid it.
# With `{0,40}` the engine tries at most 41 lengths per offset, which is linear
# (966 KB in 0.0084 s) and still admits every real label -- `RSA`, `DSA`, `EC`,
# `OPENSSH`, `ENCRYPTED`, `ENCRYPTED RSA`, and the bare `PRIVATE KEY`.
# Python 3.10 is the floor here, so possessive `*+` is not available.
_PEM_LABEL = r"[-A-Z0-9_ ]{0,40}"
# Three armour families, not one. The original pattern demanded `PRIVATE KEY`
# followed immediately by five dashes, which silently excluded two formats that
# carry real private keys:
#
#   -----BEGIN PGP PRIVATE KEY BLOCK-----     ("KEY BLOCK-----", not "KEY-----")
#   ---- BEGIN SSH2 ENCRYPTED PRIVATE KEY ----  (four dashes, and spaces inside)
#
# Both scanned clean and shipped in full under every policy. The bound on
# `_PEM_LABEL` stays {0,40} for the linear-time reason documented above.
_PEM_CORE = rf"-----BEGIN {_PEM_LABEL}PRIVATE KEY-----"
_PGP_BEGIN = r"-----BEGIN PGP PRIVATE KEY BLOCK-----"
_SSH2_BEGIN = rf"---- BEGIN {_PEM_LABEL}PRIVATE KEY ----"
PEM_BEGIN_RE = re.compile(rf"(?:{_PEM_CORE}|{_PGP_BEGIN}|{_SSH2_BEGIN})")
PEM_END_RE = re.compile(
    rf"(?:-----END {_PEM_LABEL}PRIVATE KEY-----"
    rf"|-----END PGP PRIVATE KEY BLOCK-----"
    rf"|---- END {_PEM_LABEL}PRIVATE KEY ----)"
)
# PuTTY's own format has no BEGIN/END armour at all -- the key material follows
# a `PuTTY-User-Key-File-N:` header -- so it cannot be paired and is matched as
# a plain single-line marker in CONTENT_SECRET_PATTERNS instead.
PUTTY_KEY_RE = re.compile(r"PuTTY-User-Key-File-\d+:")

# Types whose spans are computed by a dedicated pass rather than by running
# their entry below over the text. The entry is still the shape test used by
# `_looks_like_a_secret`.
PAIRED_TYPES = frozenset({"private_key"})

# Content scanning patterns: (type_name, regex)
#
# Ordering matters where two patterns can match the *same* span: `sk-ant-...`
# satisfies both `anthropic_key` and the looser `openai_key` (its tail is a
# subset of `openai_key`'s character class, so both regexes consume the same
# run and land on identical (start, end)). `scan_content_secrets` appends
# matches in tuple order and the sort below is stable, so listing the more
# specific vendor pattern first is what makes the redaction marker say
# `anthropic_key` instead of the generic, technically-also-true `openai_key`.
CONTENT_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_fine_grained_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[-0-9A-Za-z]{10,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[-A-Za-z0-9_]{20,}\b")),
    ("google_key", re.compile(r"\bAIza[-0-9A-Za-z_]{35}\b")),
    ("google_oauth_client_secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b")),
    ("stripe_webhook_secret", re.compile(r"\bwhsec_[A-Za-z0-9]{20,}\b")),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b")),
    ("pypi_token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}\b")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("digitalocean_token", re.compile(r"\bdop_v1_[a-f0-9]{20,}\b")),
    ("shopify_token", re.compile(r"\bshp(?:at|ss)_[a-fA-F0-9]{20,}\b")),
    ("sendgrid_key", re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("private_key", PEM_BEGIN_RE),
    # Not in PAIRED_TYPES: PuTTY keys carry no END marker to pair with.
    ("putty_private_key", PUTTY_KEY_RE),
    (
        "jwt",
        re.compile(r"\beyJ[-A-Za-z0-9_]{10,}\.eyJ[-A-Za-z0-9_]{10,}\.[-A-Za-z0-9_]+\b"),
    ),
    ("basic_auth_url", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
)


def _pem_spans(text: str) -> list[tuple[int, int]]:
    """Span of every PEM private-key block, in one linear pass.

    Each BEGIN takes the first END that follows it, exactly as the old lazy
    pattern did, and an unterminated BEGIN yields just its own header -- which
    is what the old optional group produced. The difference is cost: the ENDs
    are collected once and consumed by a cursor that only moves forward, so
    input with no END at all is O(n) instead of O(n*k).
    """
    ends = [m.end() for m in PEM_END_RE.finditer(text)]
    spans: list[tuple[int, int]] = []
    next_end = 0
    consumed_to = 0  # mirror finditer: never start a match inside an earlier one
    for begin in PEM_BEGIN_RE.finditer(text):
        if begin.start() < consumed_to:
            continue
        while next_end < len(ends) and ends[next_end] <= begin.end():
            next_end += 1
        if next_end < len(ends):
            spans.append((begin.start(), ends[next_end]))
            consumed_to = ends[next_end]
            next_end += 1
        else:
            spans.append((begin.start(), begin.end()))
            consumed_to = begin.end()
    return spans


# URL pattern with embedded credentials: postgres://user:password@host
DB_URL_RE = re.compile(
    r"\b((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|couchdb):\/\/[^\/\s:@]+:)([^\/\s@]+)(@[^\/\s]+\b)",
    re.I,
)

# Credential assignments: api_key = "...", DB_PASSWORD = "hunter2..."
ASSIGNMENT_RE = re.compile(
    r"""(?i)(?:^|(?<=[^A-Za-z0-9_]))([A-Za-z0-9_.-]{0,40}?(?:pass(?:word|wd)|secret|token|api[-_]?key|auth[-_]?key|access[-_]?token)[A-Za-z0-9_.-]{0,40}?\s*[:=]\s*["'])([A-Za-z0-9_\-\.\+\/=]{6,})(["'])"""
)

# JSON-style credential pairs: `"password": "..."`, `"client_secret": "..."`.
# ASSIGNMENT_RE cannot see these: it wants the quote straight after `[:=]`, but
# in JSON the *key* is quoted too, so `"password": "..."` has a `"` between the
# name and the colon -- and `\b` never fires inside `db_password` either. This
# is the shape of Terraform state, Composer/npm auth files, Firebase keys and
# most app `config.json`s, so it gets its own rule.
#
# The key must be a whole JSON string (`"...name..."`) whose text contains a
# secret-ish word; both the affixes around that word and the value are
# length-bounded, so the scan stays linear on hostile input. The value admits
# JSON escapes (`\"`, `\n` as two characters) but no raw whitespace: a value with
# a space is prose (`"password": "Enter your password"` in every i18n bundle),
# and one without a raw newline keeps the redaction trivially line-preserving. `_json_secret_value_ok` then decides
# whether the value is non-trivial.
#
# The same rule covers a single-quoted key (a Python dict literal,
# `'password': 'Sup3r...'`) and an unquoted key at the start of a line (YAML
# `db_password: "..."`); the value must still be quoted.
JSON_SECRET_RE = re.compile(
    r"(?im)(?:(?P<kq>[\"'])|^[ \t]*(?:-[ \t]+)?)"
    r"[A-Za-z0-9_.-]{0,40}?"
    r"(?:pass(?:word|wd)|secret|token|api[-_]?key|access[-_]?key|private[-_]?key)"
    r"(?P<suffix>[A-Za-z0-9_.-]{0,40})(?(kq)(?P=kq))\s*:\s*(?P<vq>[\"'])"
    r"(?P<value>(?:(?!(?P=vq))[^\\\s]|\\[^\r\n]){8,1024})(?P=vq)"
)

# Unquoted credential values. ASSIGNMENT_RE and JSON_SECRET_RE both require the
# value to be quoted, which covers source literals and JSON but misses the three
# places enterprise credentials actually live:
#
#   ansible_become_pass: hunter2            YAML with no quotes
#   password=hunter2!                       .env / .ini / .properties
#   Server=db;User Id=sa;Password=hunter2;  ADO/JDBC connection strings
#   AccountKey=Base64Key==;                 Azure storage
#   client-key-data: LS0tLS1CRUdJTi...      kubeconfig
#
# The key alternation is wider than the quoted rules': bare `pass` and `pwd` are
# admitted here (Ansible's `*_pass` family, `db_pwd`), along with `accountkey`
# and `client-key-data`. `pass` alone is why the value test below is strict --
# "pass" appears in ordinary prose constantly.
#
# The value runs to end-of-line, `;`, `&` or `"`, carries no whitespace (a space
# means prose), and must not open with a quote, which the quoted rules already
# own. Parentheses are excluded too: `token = get_token()` is a function call,
# and no credential carries an unquoted paren, so this one character class is
# what keeps the wider key set from firing on ordinary code.
# Length-bounded on both sides of the key so the scan stays linear.
_UNQUOTED_VALUE_CHAR = r"[^\s\"';&()]"
UNQUOTED_SECRET_RE = re.compile(
    r"(?im)(?:^|[^A-Za-z0-9_])"
    r"(?P<key>[A-Za-z0-9_.\- ]{0,40}?"
    r"(?:pass(?:word|wd)?|pwd|secret|token|api[-_]?key|auth[-_]?key|access[-_]?token"
    r"|accountkey|client[-_]key[-_]data)"
    r"(?P<suffix>[A-Za-z0-9_.-]{0,40}?))"
    r"[ \t]*[:=][ \t]*"
    rf"(?P<value>{_UNQUOTED_VALUE_CHAR}{{6,1024}})"
)

#: A secret word followed by one of these names a *property of* the credential
#: -- where to send it, what to call it, how long it is -- never the credential:
#: `tokenUrl`, `token_endpoint`, `tokenizer`, `secretName`, `passwordField`,
#: `api_key_header`, `token_type`, `password_policy`.
_NON_SECRET_SUFFIX_RE = re.compile(
    r"(?i)^[_.-]?(?:url|uri|endpoint|name|field|header|izer|type|id|length|policy)"
)

#: Values that are placeholders rather than credentials: `${DB_PASSWORD}`,
#: `{{ secret }}`, `<your-token>`, `%(pw)s`, `********`, `xxxxxxxx`.
_JSON_PLACEHOLDER_RE = re.compile(r"^(?:\$\{.*|\{\{.*|<.*>|%\(.*|(.)\1*)$")


def _json_secret_value_ok(value: str) -> bool:
    """True if a JSON value under a secret-ish key looks like a real credential.

    Requires a letter *and* a digit, or three of the four character classes
    (lower, upper, digit, symbol). That keeps `"password": "Passwort"` (an i18n
    label) and `"tokenType": "access-token"` indexable while every generated
    password or key -- and every human one with a digit in it -- is caught.
    """
    if _JSON_PLACEHOLDER_RE.match(value):
        return False
    lower = any(c.islower() for c in value)
    upper = any(c.isupper() for c in value)
    digit = any(c.isdigit() for c in value)
    symbol = any(not c.isalnum() for c in value)
    if digit and (lower or upper):
        return True
    return lower + upper + digit + symbol >= 3


def _json_secret_spans(text: str) -> list[tuple[int, int]]:
    """Spans of the *values* of JSON credential pairs in `text`."""
    return [
        (m.start("value"), m.end("value"))
        for m in JSON_SECRET_RE.finditer(text)
        if not _NON_SECRET_SUFFIX_RE.match(m.group("suffix"))
        # a URL is an endpoint; one carrying credentials is DB_URL_RE's job
        and "://" not in m.group("value")
        and _json_secret_value_ok(m.group("value"))
    ]


# Sanitization bounds
REDACTION_HASH_CHARS = 8
MAX_VALUE_CHARS = 512
ENTROPY_MIN_LEN = 24
MAX_SANITIZE_DEPTH = 12
MAX_CONTAINER_ITEMS = 128


def _fingerprint(value: str) -> str:
    """A short, stable, non-reversible tag for a redacted value."""
    digest = hashlib.blake2b(value.encode("utf8", "surrogateescape"), digest_size=16).hexdigest()
    return digest[:REDACTION_HASH_CHARS]


def redact(value: str, why: str) -> str:
    """Replace a secret with a structured tag useful for correlation."""
    return f"[redacted:{why} len={len(value)} fp={_fingerprint(value)}]"


#: Exact secret names that are also ordinary translation-bundle names: Composer's
#: `auth.json` holds credentials, but `locales/en/auth.json` holds the login
#: page's strings. Under a translation directory these are not secret paths.
I18N_AMBIGUOUS_NAMES = frozenset({"auth.json"})
I18N_DIR_NAMES = frozenset(
    {"locale", "locales", "i18n", "l10n", "lang", "langs", "translations", "messages"}
)


def _is_secret_path(
    path: str,
    extra_keywords: tuple[str, ...] | list[str] | set[str] | None = None,
    extra_dirs: tuple[str, ...] | list[str] | set[str] | None = None,
) -> bool:
    """Return True if path points to a sensitive file (secrets, keys, credentials)."""
    if not path:
        return False
    p = str(path).replace("\\", "/").lower()
    parts = p.strip("/").split("/")

    stripped = p.strip("/")
    if any(
        stripped == suffix or stripped.endswith("/" + suffix) for suffix in SECRET_PATH_SUFFIXES
    ):
        return True

    # ASP.NET environment overlays: `appsettings.Production.json`,
    # `appsettings.Development.json`. These routinely carry connection strings
    # with embedded credentials. Matched as a pattern rather than an exact name
    # because the middle segment is arbitrary, and deliberately *not* matching
    # the base `appsettings.json`, which conventionally holds the non-secret
    # defaults that the overlays override.
    if APPSETTINGS_OVERLAY_RE.match(parts[-1] if parts else ""):
        return True

    single_segment_extra = (
        {
            d.replace("\\", "/").strip("/").lower()
            for d in extra_dirs
            if d and d.strip() and "/" not in d.replace("\\", "/").strip("/")
        }
        if extra_dirs
        else set()
    )
    dir_names = (
        SECRET_DIR_NAMES | single_segment_extra if single_segment_extra else SECRET_DIR_NAMES
    )
    if any(part in dir_names for part in parts[:-1]):
        return True

    if extra_dirs:
        dir_path = "/".join(parts[:-1])
        for d in extra_dirs:
            if not d or not d.strip():
                continue
            norm_d = d.replace("\\", "/").strip("/").lower()
            if dir_path == norm_d or dir_path.startswith(norm_d + "/"):
                return True

    name = parts[-1]
    if not name:
        return False
    if name in SECRET_EXACT_NAMES and not (
        name in I18N_AMBIGUOUS_NAMES and any(part in I18N_DIR_NAMES for part in parts[:-1])
    ):
        return True
    if name.startswith(".env") or name.endswith(".env") or ".env." in name:
        return True
    # The dotless dotenv spelling: `env.production` holds what `.env.production`
    # holds. Restricted to known environment names so `env.py`/`env.ts` stay
    # indexable -- see DOTENV_ENVIRONMENTS.
    if name.startswith("env.") and name[len("env.") :] in DOTENV_ENVIRONMENTS:
        return True
    if any(name.endswith(ext) for ext in SECRET_EXTS):
        return True
    # A backup of a secret is a secret. Strip the editor/copy suffixes and re-ask
    # the same question, so `id_rsa.bak` and `server.key.bak` are caught without
    # every rule above needing its own `.bak` variant.
    #
    # Two things this has to get right:
    #
    #  - **Re-ask about the whole path, not the bare name.** Every
    #    `SECRET_PATH_SUFFIXES` rule is inherently multi-segment, so recursing on
    #    the basename alone skipped all of them: `.docker/config.json` was
    #    excluded but `.docker/config.json.bak` -- the same registry auth token --
    #    was indexed in the clear. Same for `.config/gh/hosts.yml.bak`,
    #    `.m2/settings.xml.bak`, `.gradle/gradle.properties.bak`.
    #  - **Strip every layer, not one.** `cp` twice and an editor once gives
    #    `id_rsa.bak.bak` and `id_rsa.bak~`, and stopping after one layer made
    #    those a miss while `id_rsa.bak` was caught.
    #
    # The loop is bounded: each pass removes at least one character, and it stops
    # as soon as no suffix matches or nothing but the suffix is left (so `.bak`
    # and `~` alone never strip to `""` and recurse on the empty string).
    stem = name
    while True:
        for suffix in BACKUP_SUFFIXES:
            if stem != suffix and stem.endswith(suffix) and len(stem) > len(suffix):
                stem = stem[: -len(suffix)]
                break
        else:
            break
    if stem != name and stem:
        return _is_secret_path("/".join([*parts[:-1], stem]), extra_keywords, extra_dirs)

    if extra_keywords:
        valid_kws = [kw.lower() for kw in extra_keywords if kw and kw.strip()]
        if any(kw in name for kw in valid_kws):
            return True

    name_words = None
    for kw in SECRET_KEYWORDS:
        if kw == "token":
            if name_words is None:
                name_words = SECRET_WORD_RE.findall(name)
            if "token" not in name_words:
                continue
        elif kw not in name:
            continue
        stem = name.lstrip(".")
        if "." not in stem or any(name.endswith(ext) for ext in SECRET_CONFIG_EXTS):
            return True
    return False


def scan_content_secrets(text: str) -> list[tuple[str, int, int]]:
    """Scan string for known credential patterns.

    The matched bytes are deliberately *not* returned. Every caller either
    counts the findings or reports their types, so carrying the plaintext would
    build a list of live credentials that exists only to be discarded -- one
    `emit(..., findings=findings)` away from being the leak this module exists
    to prevent. A caller that genuinely needs the bytes already holds `text`
    and can slice the span itself.

    Returns:
        List of (secret_type, start_idx, end_idx) spans into `text`.
    """
    if not text:
        return []

    findings: list[tuple[str, int, int]] = []

    # 1. Standard patterns (AWS, GitHub, Slack, OpenAI, Google, JWT)
    for stype, pattern in CONTENT_SECRET_PATTERNS:
        if stype in PAIRED_TYPES:
            continue  # spans come from the dedicated pass below
        for m in pattern.finditer(text):
            findings.append((stype, m.start(), m.end()))

    # 1b. PEM blocks, paired linearly rather than by a lazy scan per BEGIN.
    findings.extend(("private_key", start, end) for start, end in _pem_spans(text))

    # 2. Database URLs with credentials
    for m in DB_URL_RE.finditer(text):
        findings.append(("DATABASE_PASSWORD", m.start(2), m.end(2)))

    # 3. Credential assignments
    for m in ASSIGNMENT_RE.finditer(text):
        secret = m.group(2)
        if _json_secret_value_ok(secret):
            findings.append(("CREDENTIAL_ASSIGNMENT", m.start(2), m.end(2)))
        elif not re.fullmatch(r"[a-z_]+", secret):
            digits = sum(c.isdigit() for c in secret)
            letters = sum(c.isalpha() for c in secret)
            if digits and letters:
                findings.append(("CREDENTIAL_ASSIGNMENT", m.start(2), m.end(2)))

    # 4. JSON-style credential pairs (`"password": "..."`)
    findings.extend(("CREDENTIAL_JSON", start, end) for start, end in _json_secret_spans(text))

    # 5. Unquoted values: YAML, INI/.env/.properties, connection strings,
    #    kubeconfig. Reuses the same two false-positive filters as the quoted
    #    rules -- a `tokenUrl`/`secretName`-style property name is not a
    #    credential, and `${VAR}`/`<your-token>`/`*****` are placeholders.
    for m in UNQUOTED_SECRET_RE.finditer(text):
        if _NON_SECRET_SUFFIX_RE.match(m.group("suffix") or ""):
            continue
        value = m.group("value")
        if _JSON_PLACEHOLDER_RE.match(value):
            continue
        # The key set here includes bare `pass`/`pwd`/`token`, which appear all
        # over ordinary code, so a value shaped like an identifier or a dotted
        # attribute path is treated as code rather than a credential:
        # `token = self._refresh_token`, `password: required`. Requiring at least
        # one digit or symbol is what separates those from `hunter2xyz`,
        # `s3cr3tValue` and base64 key material. A purely alphabetic unquoted
        # password is the accepted cost; the quoted rules still catch it when it
        # is written as a literal.
        if not _json_secret_value_ok(value) and re.fullmatch(r"[A-Za-z_][A-Za-z_.]*", value):
            continue
        findings.append(("CREDENTIAL_UNQUOTED", m.start("value"), m.end("value")))

    # Sort by start index
    findings.sort(key=lambda x: x[1])
    return findings


def redact_content(text: str, policy: str = "redact-match") -> tuple[str, int]:
    """Apply line-preserving redaction to text containing secrets.

    Args:
        text: Source code or chunk text.
        policy: 'redact-match', 'warn-only', or 'off'.

    Returns:
        (redacted_text, count_of_redacted_secrets).
    """
    if policy in ("off", "warn-only") or not text:
        return text, 0

    findings = scan_content_secrets(text)
    if not findings:
        return text, 0

    # Redact from back to front so indices remain valid
    out = text
    count = 0
    # Deduplicate overlapping spans
    filtered_findings: list[tuple[str, int, int]] = []
    last_end = -1
    for stype, start, end in findings:
        if start >= last_end:
            filtered_findings.append((stype, start, end))
            last_end = end

    for stype, start, end in reversed(filtered_findings):
        # Line-preserving rule: preserve exact count of newlines. Count them in
        # the original `text` over the span's bounds -- `out` is rewritten
        # back-to-front, so this span is still untouched there either way, and
        # str.count(sub, start, end) never materialises the secret substring.
        nl_count = text.count("\n", start, end)
        repl = f"[REDACTED:{stype}]" + ("\n" * nl_count)
        out = out[:start] + repl + out[end:]
        count += 1

    return out, count


def _looks_like_a_secret(value: str) -> str | None:
    """Name the credential shape `value` matches, or None."""
    for stype, pattern in CONTENT_SECRET_PATTERNS:
        if pattern.search(value):
            return stype.lower()
    if DB_URL_RE.search(value):
        return "database_url"
    if ASSIGNMENT_RE.search(value):
        return "assignment"
    if _json_secret_spans(value):
        return "json_assignment"

    if len(value) >= ENTROPY_MIN_LEN and re.fullmatch(r"[A-Za-z0-9+/=_-]+", value):
        if re.fullmatch(r"[a-z0-9_]+", value):
            return None
        digits = sum(c.isdigit() for c in value)
        letters = sum(c.isalpha() for c in value)
        if digits and letters:
            return "high_entropy"
    return None


def sanitize_url(url: str) -> str:
    """Redact embedded credentials and sensitive query params from a URL."""
    if not url:
        return url
    # Redact password in http://user:pass@host
    s = DB_URL_RE.sub(r"\g<1>[redacted:db_password]\g<3>", url)
    s = re.sub(
        r"\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s@]+)(@[^\/\s]+)",
        r"\g<1>[redacted:password]\g<3>",
        s,
    )
    # Userinfo with no colon is the credential itself, not a user name. Git
    # remotes written by CI and by `gh auth setup-git` take exactly this shape --
    # `https://ghp_...@github.com/o/r.git` -- and the rule above cannot see them
    # because it requires a `user:pass` pair. `integrity.py` reads
    # `remote.origin.url` into the manifest's provenance, so a token in a remote
    # was published in `manifest.json` and shipped with the index.
    # `git` and `oauth2` are conventional literal user names carrying no secret
    # (`ssh://git@github.com/...` is the canonical SSH remote), so redacting
    # them would lose information without protecting anything.
    s = re.sub(
        r"\b([a-z][a-z0-9+.-]*://)(?!(?:git|oauth2)@)([^/\s:@]+)(@[^\/\s]+)",
        r"\g<1>[redacted:userinfo]\g<3>",
        s,
    )
    # Redact sensitive query parameters
    return SENSITIVE_QUERY_PARAMS_RE.sub(r"\g<1>[redacted:query_param]", s)


def sanitize_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive headers such as Authorization and Cookie."""
    sanitized = {}
    for k, v in headers.items():
        k_lower = str(k).lower()
        if k_lower in SENSITIVE_HEADERS or SECRET_KEY_RE.search(str(k)):
            val_str = str(v)
            sanitized[k] = redact(val_str, f"header:{k_lower}")
        else:
            sanitized[k] = sanitize_value(str(k), v)
    return sanitized


def sanitize_value(
    key: str,
    value: Any,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> Any:
    """Redact sensitive values and cap depth/length for structured logs and events.

    Args:
        key: The key or parameter name.
        value: Any data structure.
        depth: Current recursion depth.
        seen: Object IDs visited, for cycle detection.

    Returns:
        Sanitized representation guaranteed safe to log.
    """
    if seen is None:
        seen = set()

    # Recursion ceiling
    if depth >= MAX_SANITIZE_DEPTH:
        return "[truncated:depth]"

    val_id = id(value)
    if isinstance(value, (dict, list, tuple, set)):
        if val_id in seen:
            return "[circular:ref]"
        seen.add(val_id)

    try:
        if isinstance(value, dict):
            items = list(value.items())
            if len(items) > MAX_CONTAINER_ITEMS:
                truncated_dict = {
                    str(k): sanitize_value(str(k), v, depth=depth + 1, seen=seen)
                    for k, v in items[:MAX_CONTAINER_ITEMS]
                }
                truncated_dict["..."] = f"[truncated:+{len(items) - MAX_CONTAINER_ITEMS} items]"
                return truncated_dict
            return {
                str(k): sanitize_value(str(k), v, depth=depth + 1, seen=seen)
                for k, v in value.items()
            }

        if isinstance(value, (list, tuple)):
            if len(value) > MAX_CONTAINER_ITEMS:
                truncated_list = [
                    sanitize_value(key, v, depth=depth + 1, seen=seen)
                    for v in value[:MAX_CONTAINER_ITEMS]
                ]
                truncated_list.append(f"[truncated:+{len(value) - MAX_CONTAINER_ITEMS} items]")
                return truncated_list
            return [sanitize_value(key, v, depth=depth + 1, seen=seen) for v in value]

        if isinstance(value, set):
            s_list = sorted(str(x) for x in value)
            return sanitize_value(key, s_list, depth=depth, seen=seen)

        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return value

        if isinstance(value, str):
            text = value
        else:
            try:
                text = str(value)
            except Exception:
                return f"[unprintable:{type(value).__name__}]"

        # Check key name. NON_SECRET_KEYS names the handful that match by
        # substring but describe a shape rather than hold one; they fall
        # through to the value checks below rather than skipping them.
        if SECRET_KEY_RE.search(key or "") and (key or "").lower() not in NON_SECRET_KEYS:
            return redact(text, f"key:{key}")

        if "://" in text and ("?" in text or "@" in text):
            text = sanitize_url(text)

        shape = _looks_like_a_secret(text)
        if shape:
            return redact(text, shape)

        if ("/" in text or "\\" in text) and _is_secret_path(text):
            return f"[redacted:secret_path fp={_fingerprint(text)}]"

        if len(text) > MAX_VALUE_CHARS:
            return text[:MAX_VALUE_CHARS] + f"…[+{len(text) - MAX_VALUE_CHARS} chars]"

        return text
    finally:
        if isinstance(value, (dict, list, tuple, set)):
            seen.discard(val_id)


def sanitize_params(params: Any) -> dict[str, Any]:
    """Sanitize a mapping of arguments/parameters for audit/event logs."""
    if not isinstance(params, dict):
        return {"_": sanitize_value("", params)} if params else {}
    seen = {id(params)}
    return {str(k): sanitize_value(str(k), v, seen=seen) for k, v in params.items()}

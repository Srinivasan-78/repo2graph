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
        ".kdbx",
        ".keystore",
        ".jks",
        ".secret",
        ".secrets",
        ".keytab",
        ".ppk",
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
    }
)

#: Suffixes an editor, a script or a careless `cp` appends to a file it is about
#: to replace. `id_rsa.bak` and `server.key.bak` hold exactly what `id_rsa` and
#: `server.key` hold, so a backup is tested by stripping the suffix and asking
#: the same question again. Only one layer is stripped, and the `~` form is
#: handled separately because it carries no dot.
BACKUP_SUFFIXES = (".bak", ".old", ".orig", ".save", ".swp", ".tmp", "~")

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
PEM_BEGIN_RE = re.compile(rf"-----BEGIN {_PEM_LABEL}PRIVATE KEY-----")
PEM_END_RE = re.compile(rf"-----END {_PEM_LABEL}PRIVATE KEY-----")

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

# High-entropy credential assignments: api_key = "...", token: "..."
ASSIGNMENT_RE = re.compile(
    r"""(?i)\b((?:pass(?:word|wd)?|secret|api[-_]?key|auth[-_]?key|access[-_]?token)\s*[:=]\s*["'])([A-Za-z0-9_\-\.\+\/=]{16,})(["'])"""
)

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
    if name in SECRET_EXACT_NAMES:
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

    # 3. High-entropy assignments
    for m in ASSIGNMENT_RE.finditer(text):
        secret = m.group(2)
        # Reject simple identifiers / words -- but only *purely alphabetic*
        # ones (plus underscore). The guard used to be `[a-z0-9_]+`, which
        # also matches lowercase hex/alphanumeric secrets (an md5 hash, a
        # lowercase API key) and skipped them before the digits-and-letters
        # check below ever ran (#338). A word like `default_option` still has
        # no digit and is still excluded; `abcdef12345678901234567890123456`
        # now reaches the check and is flagged.
        if not re.fullmatch(r"[a-z_]+", secret):
            digits = sum(c.isdigit() for c in secret)
            letters = sum(c.isalpha() for c in secret)
            if digits and letters:
                findings.append(("CREDENTIAL_ASSIGNMENT", m.start(2), m.end(2)))

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

        # Check URL
        if "://" in text and ("?" in text or "@" in text):
            text = sanitize_url(text)

        # Check credential shape
        shape = _looks_like_a_secret(text)
        if shape:
            return redact(text, shape)

        # Check path
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

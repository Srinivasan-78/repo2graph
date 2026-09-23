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
        ".dockercfg",
        ".git-credentials",
        ".pgpass",
        ".htpasswd",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
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
PEM_BEGIN_RE = re.compile(r"-----BEGIN [-A-Z0-9_ ]*PRIVATE KEY-----")
PEM_END_RE = re.compile(r"-----END [-A-Z0-9_ ]*PRIVATE KEY-----")

# Types whose spans are computed by a dedicated pass rather than by running
# their entry below over the text. The entry is still the shape test used by
# `_looks_like_a_secret`.
PAIRED_TYPES = frozenset({"private_key"})

# Content scanning patterns: (type_name, regex)
CONTENT_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[-0-9A-Za-z]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-[-A-Za-z0-9_]{20,}\b")),
    ("google_key", re.compile(r"\bAIza[-0-9A-Za-z_]{35}\b")),
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
    if any(name.endswith(ext) for ext in SECRET_EXTS):
        return True

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
        # Avoid redacting simple identifiers, empty or trivial values
        if not re.fullmatch(r"[a-z0-9_]+", secret):
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

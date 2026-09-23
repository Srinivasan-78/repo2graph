"""Stream a grounded, citation-carrying answer from a packed context.

Optional by design: nothing here is imported unless `repo2graph rag --answer`
asks for it, and no provider SDK is used — stdlib `urllib.request` only, so the
core install stays pure Python. The provider is chosen from the environment.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

HTTP_TIMEOUT = 300
ERROR_SNIFF_LINES = 8  # unparsable lines kept, to explain an empty answer
ERROR_SNIPPET = 400  # chars of a provider error body echoed to the user
# An answer is a few thousand tokens of prose; a stream past this ceiling is not
# one, and reading it unbounded would let the endpoint exhaust memory. MAX_TOKENS
# is only *asked* of the provider and HTTP_TIMEOUT is a socket timeout, not a
# transfer bound -- a host that trickles bytes resets it forever -- so neither
# caps the body. OLLAMA_HOST is operator-supplied and _ollama_base accepts any
# http(s) host:port, which makes this endpoint attacker-choosable rather than
# merely misbehaving. Generous on purpose: per-token SSE envelopes cost far more
# bytes than the text they carry, and a real answer must never hit this.
MAX_ANSWER_BYTES = 8 << 20
# Size of one read() off the socket. The block read is what bounds the *per-line*
# axis: iterating the response reads until "\n", so a body that never sends one
# is buffered whole before any code of ours sees a byte.
READ_BLOCK = 1 << 16
PROVIDER_MAP = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "OLLAMA_HOST",
}
PROVIDER_ENV = tuple(PROVIDER_MAP.values())
# Best-effort cheap/fast ids; `rag --answer --model` overrides.
DEFAULT_MODELS = {
    "gemini": "gemini-3.6-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "ollama": "llama3.1",
}
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 2048

SYSTEM_PROMPT = (
    "You are a code assistant answering strictly from the repository map and the "
    "code chunks provided below. Use nothing else: if the answer is not in the "
    "provided material, say so plainly. Never invent an API, a file, a function "
    "name, a parameter or a return value that does not appear in the chunks. "
    "Cite every claim with the source it came from, as [path/file.py:start-end], "
    "copying the path and line numbers from the `### [cite: ...]` header of the "
    "chunk the claim rests on. A claim with no citation must not be made."
)


class _WriterError(Exception):
    """Wraps an exception from the stdout writer to isolate it from network errors."""

    def __init__(self, exc: Exception):
        super().__init__(str(exc))
        self.exc = exc


def pick_provider(env=None, provider=None) -> dict | None:
    """The configured provider, or first in GEMINI > OPENAI > ANTHROPIC > OLLAMA order."""
    env = os.environ if env is None else env
    if provider is not None:
        if provider not in PROVIDER_MAP:
            raise SystemExit(f"unknown provider {provider!r}")
        env_var = PROVIDER_MAP[provider]
        value = (env.get(env_var) or "").strip()
        if not value and provider == "gemini":
            gkey = (env.get("GOOGLE_API_KEY") or "").strip()
            if gkey:
                return {"name": "gemini", "env": "GOOGLE_API_KEY", "value": gkey}
        if not value:
            raise SystemExit(f"provider {provider!r} requested but {env_var} is not set")
        return {"name": provider, "env": env_var, "value": value}
    gemini_key = (env.get("GEMINI_API_KEY") or "").strip()
    if gemini_key:
        return {"name": "gemini", "env": "GEMINI_API_KEY", "value": gemini_key}
    google_key = (env.get("GOOGLE_API_KEY") or "").strip()
    if google_key:
        return {"name": "gemini", "env": "GOOGLE_API_KEY", "value": google_key}
    for name, var in (
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("ollama", "OLLAMA_HOST"),
    ):
        value = (env.get(var) or "").strip()
        if value:
            return {"name": name, "env": var, "value": value}
    return None


def build_prompt(pack) -> tuple[str, str]:
    """(system, user). The user turn carries the whole pack, verbatim."""
    markdown = (pack or {}).get("markdown") or ""
    question = (pack or {}).get("query") or ""
    user = (
        f"Question: {question}\n\n"
        f"Repository map and code chunks:\n\n{markdown}\n\n"
        f"Answer the question using only the material above, and cite every "
        f"claim as [path/file.py:start-end]."
    )
    return SYSTEM_PROMPT, user


def _request(spec: dict, model: str | None, system: str, user: str):
    """(url, headers, payload) for the chosen provider's streaming endpoint."""
    name = spec["name"]
    model = model or DEFAULT_MODELS[name]
    if name == "openai":
        return (
            "https://api.openai.com/v1/chat/completions",
            {"Content-Type": "application/json", "Authorization": f"Bearer {spec['value']}"},
            {
                "model": model,
                "stream": True,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
    if name == "anthropic":
        return (
            "https://api.anthropic.com/v1/messages",
            {
                "Content-Type": "application/json",
                "x-api-key": spec["value"],
                "anthropic-version": ANTHROPIC_VERSION,
            },
            {
                "model": model,
                "stream": True,
                "max_tokens": MAX_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
    if name == "gemini":
        # The key goes in x-goog-api-key, never in the query string: a `?key=`
        # lands in proxy/CDN access logs, in Request.full_url and in
        # HTTPError.url, so any later error message could leak it.
        clean_model = model.strip().lstrip("/")
        if ".." in clean_model:
            raise SystemExit(f"invalid model name {model!r}")
        model_path = clean_model if clean_model.startswith("models/") else f"models/{clean_model}"
        return (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"{urllib.parse.quote(model_path, safe='/')}:streamGenerateContent?alt=sse",
            {"Content-Type": "application/json", "x-goog-api-key": spec["value"]},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
            },
        )
    ollama_ctx = max(4096, int(len(user) / 2.5))
    return (
        _ollama_base(spec["value"]) + "/api/chat",
        {"Content-Type": "application/json"},
        {
            "model": model,
            "stream": True,
            "options": {"num_ctx": ollama_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
    )


def _ollama_base(value: str) -> str:
    """Normalise OLLAMA_HOST to an http(s) base URL.

    `ollama serve` documents (and exports) the schemeless `127.0.0.1:11434`,
    which urlopen rejects with `unknown url type: 127.0.0.1`. Default the
    scheme to http and refuse anything that is not http(s), so a stray
    `file://` value cannot turn into an urlopen target.
    """
    base = (value or "").strip().rstrip("/")
    if "://" not in base:
        base = "http://" + base
    parts = urllib.parse.urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SystemExit(f"OLLAMA_HOST must be an http(s) URL or host:port, got {value!r}")
    return base


def _delta(name: str, raw: bytes) -> str:
    """One streamed line -> the text it carries, or '' if it carries none."""
    line = raw.decode("utf8", "replace").strip()
    if line.startswith("data:"):
        line = line[len("data:") :].strip()
    if not line or line == "[DONE]":
        return ""
    try:
        d = json.loads(line)
    except ValueError:
        return ""  # keep-alives and comment lines are not fatal
    try:
        if name == "openai":
            return d["choices"][0]["delta"].get("content") or ""
        if name == "anthropic":
            if d.get("type") != "content_block_delta":
                return ""
            return d["delta"].get("text") or ""
        if name == "gemini":
            return d["candidates"][0]["content"]["parts"][0].get("text") or ""
        return d["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _blocks(resp) -> Iterator[bytes]:
    """Yield the response body in READ_BLOCK-sized pieces.

    `http.client.HTTPResponse.read(n)` is the real path here, and the sized read
    is the whole point: `for raw in resp` reads until a newline, so a body that
    never sends one is already a single unbounded `bytes` by the time the caller
    is handed it. Some response-like objects only support iteration (a test
    stand-in, a pre-split body); those keep their own framing, and the byte
    ceiling in `_BoundedLines` still applies to what they yield.
    """
    read = getattr(resp, "read", None)
    if callable(read):
        try:
            block = read(READ_BLOCK)
        except TypeError:
            block = None  # a read() that accepts no size argument
        if block is not None:
            while block:
                yield block
                block = read(READ_BLOCK)
            return
    yield from resp


class _BoundedLines:
    """Newline framing over a response body, with a hard byte ceiling.

    Both unbounded reads close here: the body arrives in fixed blocks, so no
    single newline-less line can grow without limit, and the running total stops
    the stream once `limit` bytes have been taken, so a trickle of well-formed
    small deltas cannot either. `truncated` records that the ceiling bound, so
    the caller can say so instead of returning a cut-off answer that reads as
    complete.
    """

    def __init__(self, resp, limit: int):
        self._resp = resp
        self._limit = limit
        self.read_bytes = 0
        self.truncated = False

    def __iter__(self) -> Iterator[bytes]:
        buf = b""
        for block in _blocks(self._resp):
            room = self._limit - self.read_bytes
            if len(block) > room:
                # Strictly greater, never >=: a body that ends exactly at the
                # ceiling lost nothing and must not be reported as truncated.
                # Same shape as auth._fetch_json reading MAX_JWKS_BYTES + 1.
                block = block[:room]
                self.truncated = True
            self.read_bytes += len(block)
            buf += block
            start = 0
            while (nl := buf.find(b"\n", start)) >= 0:
                yield buf[start:nl]
                start = nl + 1
            buf = buf[start:]
            if self.truncated:
                break
        if buf:
            yield buf


def _note_truncated(name: str, limit: int) -> None:
    """Say on stderr that the answer was cut off at the byte ceiling.

    A ceiling that silently drops the tail turns a partial answer into one that
    looks whole, which is the failure mode `_empty_answer` exists to avoid for
    the no-text case. stderr, not stdout, for the same reason `_disclose` uses
    it: stdout is the answer and has to stay pipeable.
    """
    from .events import write_safe

    write_safe(
        sys.stderr,
        f"repo2graph: answer truncated -- {name} sent more than {limit} bytes; "
        f"the rest was discarded and the answer above is incomplete",
    )
    _flush(sys.stderr)


def _writer(out=None):
    """A write callable that cannot raise UnicodeEncodeError.

    Always writes through the stream's own text-mode `write`, never through
    `sys.stdout.buffer`: on Windows the console expects bytes in its active
    codepage (cp1252, cp437, ...), and writing raw UTF-8 bytes straight to
    the buffer bypasses `TextIOWrapper`'s console translation, producing
    mojibake even though no exception is raised (#169). A non-ASCII token
    from a model would otherwise abort the whole answer mid-sentence, so the
    chunk is folded to what the stream's own encoding can represent first.
    """
    stream = sys.stdout if out is None else out

    def write_text(chunk: str) -> None:
        enc = getattr(stream, "encoding", None) or "utf8"
        try:
            chunk.encode(enc)
        except UnicodeEncodeError:
            chunk = chunk.encode(enc, "replace").decode(enc, "replace")
        except LookupError:
            chunk = chunk.encode("utf8", "replace").decode("utf8", "replace")
        stream.write(chunk)
        _flush(stream)

    return write_text


def _flush(stream) -> None:
    try:
        stream.flush()
    except (OSError, ValueError):
        pass


def _http_error(spec: dict, exc) -> str:
    """A one-line explanation of a provider HTTP error, without its URL.

    HTTPError.url can carry credentials for some providers, so it is never
    echoed; the status and the provider's own message are enough to act on.
    """
    detail = ""
    try:
        detail = exc.read().decode("utf8", "replace").strip()[:ERROR_SNIPPET]
    except (OSError, ValueError, AttributeError):
        pass
    head = f"{spec['name']} returned HTTP {getattr(exc, 'code', '?')}"
    return f"{head}: {detail}" if detail else head


def _empty_answer(spec: dict, raw_tail: list) -> str:
    """Why a 200 response carried no answer text.

    Ollama answers HTTP 200 with {"error": "model 'x' not found"}; without this
    the command would print nothing and exit 0, which reads as a valid empty
    answer.
    """
    for raw in raw_tail:
        line = raw.decode("utf8", "replace").strip()
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        try:
            d = json.loads(line)
        except ValueError:
            continue
        err = d.get("error") if isinstance(d, dict) else None
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
        if err:
            return f"{spec['name']} returned an error: {err}"
    return (
        f"{spec['name']} returned no answer text; check the model name and the {spec['env']} value"
    )


def _disclose(name: str, env: str, url: str, n_chars: int) -> None:
    """Tell the user, on stderr, where their repository content is going.

    Takes the three scalars it prints rather than the provider `spec`, because
    that dict also carries the resolved API key under "value". A logging
    function has no business holding a credential even if it never prints it:
    the only way to be sure a secret cannot be logged is for it not to be in
    scope. This also clears CodeQL alert #1 (py/clear-text-logging-sensitive-data),
    which flagged the whole-dict argument reaching a print.

    The pack can contain any indexed file, `.env` included. stderr, not stdout:
    stdout is the answer itself and has to stay pipeable.

    Args:
        name: Provider name, e.g. "openai".
        env: Name of the environment variable that selected it -- the variable's
            *name*, never its value.
        url: Endpoint URL; only its hostname is printed.
        n_chars: How many characters of repository context are being sent.
    """
    host = urllib.parse.urlsplit(url).hostname or url
    # write_safe, not print: a hostname from an IDN or a non-ASCII OLLAMA_HOST
    # must not make the disclosure itself the thing that crashes the command.
    from .events import write_safe

    write_safe(
        sys.stderr,
        f"repo2graph: sending {n_chars} chars of repository context to "
        f"provider {name} at {host} (selected by {env})",
    )
    _flush(sys.stderr)


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Keep a provider redirect on its origin, and never let it downgrade.

    The request carries the API key in a header -- `Authorization`,
    `x-api-key` or `x-goog-api-key`, depending on the provider -- and CPython's
    redirect handler forwards every header except `content-length` and
    `content-type` to the new target, whatever host and whatever scheme it
    names. A provider answering `302 http://somewhere-else` is therefore handed
    the credential, in clear, by a client that had no say in it.

    It would also make `_disclose` untrue. That function prints the provider
    and hostname to stderr before the first byte precisely so the operator
    knows where their repository context is going; a redirect that relocates
    the request makes the printed hostname the one place it did *not* go.

    An http -> https upgrade on the same host is allowed: OLLAMA_HOST is
    legitimately plain http, and moving that to TLS is never the attack.
    Returning None stops urllib with an `HTTPError`, which the caller already
    handles.
    """

    max_redirections = 3

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if new.netloc.lower() != old.netloc.lower():
            return None
        if new.scheme not in ("http", "https"):
            return None
        if old.scheme == "https" and new.scheme != "https":
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Module-level so a test can swap it; `urlopen` would rebuild the default
# opener, and with it the permissive redirect handler replaced above.
_OPENER = urllib.request.build_opener(_SameOriginRedirect)


def stream_answer(pack, model=None, env=None, out=None, provider=None) -> str:
    """Ask the configured provider and stream the answer out. Returns the text."""
    spec = pick_provider(env, provider=provider)
    if spec is None:
        raise SystemExit(
            "no LLM provider configured: set one of "
            + ", ".join(PROVIDER_ENV[:-1])
            + f" or {PROVIDER_ENV[-1]}"
        )
    system, user = build_prompt(pack)
    url, headers, payload = _request(spec, model, system, user)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf8"), headers=headers, method="POST"
    )
    _disclose(str(spec["name"]), str(spec["env"]), url, len(user))
    write = _writer(out)
    parts: list[str] = []
    raw_tail: list[bytes] = []
    lines: _BoundedLines | None = None
    # _OPENER is looked up on the module at call time, so a test can swap it.
    try:
        with _OPENER.open(req, timeout=HTTP_TIMEOUT) as resp:
            lines = _BoundedLines(resp, MAX_ANSWER_BYTES)
            for raw in lines:
                piece = _delta(spec["name"], raw)
                if piece:
                    parts.append(piece)
                    try:
                        write(piece)
                    except (OSError, ValueError) as exc:
                        raise _WriterError(exc) from exc
                elif len(raw_tail) < ERROR_SNIFF_LINES:
                    raw_tail.append(raw)
    except _WriterError as werr:
        raise werr.exc
    except urllib.error.HTTPError as exc:
        raise SystemExit(_http_error(spec, exc)) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SystemExit(f"{spec['name']} request failed: {exc}") from None
    except KeyboardInterrupt:
        raise SystemExit(130)
    # Before the empty-answer check: a stream that hit the ceiling without ever
    # carrying decodable text is two separate facts, and both are worth saying.
    if lines is not None and lines.truncated:
        _note_truncated(str(spec["name"]), MAX_ANSWER_BYTES)
    if not parts:
        raise SystemExit(_empty_answer(spec, raw_tail))
    try:
        write("\n")
    except (OSError, ValueError):
        pass
    return "".join(parts)

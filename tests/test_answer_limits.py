"""#241: the provider response must be read under a byte ceiling, on both axes.

`stream_answer` used to iterate the response directly (`for raw in resp`) and
accumulate every decoded delta for the life of the call. That is two unbounded
reads, not one: a body with no newline in it is buffered whole by the line
iterator before any repo2graph code sees a byte, and a body that *is* newline
framed is still accumulated without limit. `HTTP_TIMEOUT` bounds neither -- it
is a socket timeout, so a host trickling bytes resets it forever.

The tests below pin both axes against the real `MAX_ANSWER_BYTES`, not against a
shrunken one, so a fixture that is simply smaller than the ceiling cannot pass
them by accident. Every expected value is hand-written here; none is computed by
calling the code under test.
"""

import http.client
import io
import json
import pathlib
import urllib.request

import pytest

import repo2graph.answer as answer

PACK = {
    "markdown": "# repo map\n\n---\n\n### [cite: pkg/a.py:1-2] `f` (seed)\ndef f():\n    return 1\n",
    "query": "what does f return",
}

PROVIDER_ENV = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_HOST",
)


def only_provider(monkeypatch, name, value):
    """Leave exactly one provider variable set, whatever the real environment has."""
    for var in PROVIDER_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(name, value)


class SizedResponse:
    """A response double that serves sized reads the way urlopen's does.

    `served` counts every byte handed out through *either* access path -- the
    sized `read(n)` the fix uses and the line iteration the old code used -- so
    an assertion on it measures what left the socket, not what the caller
    happened to keep.
    """

    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)
        self.served = 0
        self.status = 200
        self.headers = {"Content-Type": "text/event-stream"}

    def read(self, size=-1):
        block = self._buf.read(size)
        self.served += len(block)
        return block

    def __iter__(self):
        for line in self._buf:
            self.served += len(line)
            yield line

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class FakeOpener:
    """Stand-in for `answer._OPENER`, delegating to a urlopen-shaped callable.

    `stream_answer` goes through an opener rather than `urlopen` so that its
    redirect handler cannot be bypassed (`_SameOriginRedirect`); patching the
    opener is what keeps these tests off the network.
    """

    def __init__(self, fn):
        self._fn = fn

    def open(self, req, *a, **kw):
        return self._fn(req, *a, **kw)


def serve(monkeypatch, body: bytes) -> SizedResponse:
    """Point the opener at one canned body; return the response for its counters."""
    resp = SizedResponse(body)
    monkeypatch.setattr(answer, "_OPENER", FakeOpener(lambda req, *a, **kw: resp))
    return resp


def ndjson(pieces) -> bytes:
    """An ollama-shaped newline-delimited stream carrying `pieces` in order."""
    return b"".join(json.dumps({"message": {"content": p}}).encode("utf8") + b"\n" for p in pieces)


def sse(pieces) -> bytes:
    """An openai-shaped SSE stream carrying `pieces` in order."""
    out = []
    for p in pieces:
        d = {"choices": [{"delta": {"content": p}}]}
        out.append(b"data: " + json.dumps(d).encode("utf8") + b"\n\n")
    out.append(b"data: [DONE]\n\n")
    return b"".join(out)


# ------------------------------------------------- axis 1: one huge line ----


def test_iss241_a_body_with_no_newline_cannot_be_read_unbounded(monkeypatch, capsys):
    """Axis 1: a response that never sends "\\n" is still bounded.

    The old line iterator had to reach EOF before yielding anything, so the
    whole body became one `bytes` object regardless of its size. Here the body
    is a single valid JSON object a full megabyte past the ceiling -- more than
    the one-block overshoot the bound allows, so an unbounded read cannot slip
    under it -- and carries no newline anywhere.
    """
    limit = answer.MAX_ANSWER_BYTES
    filler = "a" * (limit + (1 << 20))
    body = json.dumps({"message": {"content": filler}}).encode("utf8")  # no "\n"
    assert b"\n" not in body and len(body) > limit

    only_provider(monkeypatch, "OLLAMA_HOST", "http://127.0.0.1:11434")
    resp = serve(monkeypatch, body)
    sink = io.StringIO()

    # Truncating the object mid-string leaves invalid JSON, so no delta decodes
    # and the call ends on the existing empty-answer path -- after the note.
    # Caught rather than asserted on: the bound below is the claim under test,
    # and it must be the assertion that fails if the bound ever goes away.
    try:
        answer.stream_answer(PACK, out=sink)
    except SystemExit:
        pass

    assert resp.served <= limit + 2 * answer.READ_BLOCK, resp.served
    assert len(sink.getvalue()) <= limit
    assert "truncated" in capsys.readouterr().err


# --------------------------------------------- axis 2: many small deltas ----


def test_iss241_many_small_deltas_are_capped_in_total(monkeypatch, capsys):
    """Axis 2: a well-framed stream of small deltas stops at the ceiling.

    Every line here is short and valid, so the per-line bound never binds; only
    the running total can stop this stream. 9000 deltas of 1000 characters is
    ~9.2 MB on the wire against an 8 MiB ceiling.
    """
    limit = answer.MAX_ANSWER_BYTES
    body = ndjson(["z" * 1000] * 9000)
    assert len(body) > limit

    only_provider(monkeypatch, "OLLAMA_HOST", "http://127.0.0.1:11434")
    resp = serve(monkeypatch, body)
    sink = io.StringIO()

    text = answer.stream_answer(PACK, out=sink)

    assert resp.served <= limit + 2 * answer.READ_BLOCK, resp.served
    assert len(text) <= limit
    assert set(text) == {"z"}, "the deltas that did arrive must arrive intact"
    assert "truncated" in capsys.readouterr().err


# ------------------------------------------------------- the stderr note ----


def test_iss241_the_truncation_note_goes_to_stderr_not_stdout(monkeypatch, capsys):
    """stdout stays the answer and stays pipeable; the warning is on stderr.

    stdout now carries the answer *and* the "Confidence and limitations"
    segment, which is answer content -- a reader piping `rag --answer` to a
    file wants the caveats in that file. The ISS-241 property this test
    exists for is narrower and unchanged: the *provider stream truncation
    warning* is a tool diagnostic, so it goes to stderr and never pollutes
    the answer on stdout.
    """
    only_provider(monkeypatch, "OLLAMA_HOST", "http://127.0.0.1:11434")
    serve(monkeypatch, ndjson(["y" * 1000] * 9000))
    sink = io.StringIO()

    answer.stream_answer(PACK, out=sink)

    captured = capsys.readouterr()
    assert "truncated" in captured.err

    out = sink.getvalue()
    # The model's answer is everything before the appended segment.
    model_answer, sep, segment = out.partition("\n---\n**Confidence and limitations**")
    assert sep, "the confidence segment was not appended to stdout"
    assert set(model_answer.replace("\n", "")) == {"y"}, (
        "something other than the model's answer leaked into the answer body"
    )
    # The provider-stream warning stays out of stdout entirely -- including
    # out of the segment, which reports on the *pack*, not on the HTTP stream.
    assert "truncated" not in model_answer
    assert "stream" not in segment.lower()


# ------------------------------------------------ no false truncation ----


def test_iss241_a_short_answer_is_never_reported_truncated(monkeypatch, capsys):
    """An ordinary answer must not grow a warning it did not earn."""
    only_provider(monkeypatch, "OLLAMA_HOST", "http://127.0.0.1:11434")
    serve(monkeypatch, ndjson(["Hello ", "world"]))
    sink = io.StringIO()

    text = answer.stream_answer(PACK, out=sink)

    assert text == "Hello world"
    assert "truncated" not in capsys.readouterr().err


def test_iss241_a_body_ending_exactly_on_the_ceiling_is_not_truncated(monkeypatch, capsys):
    """The bound is `>`, not `>=`: nothing was dropped, so say nothing.

    The ceiling is lowered for this one case because the point is the exact
    boundary byte, which is unreachable by padding an 8 MiB body to the byte.
    """
    filler = "q" * 400
    body = ndjson([filler])
    monkeypatch.setattr(answer, "MAX_ANSWER_BYTES", len(body))
    only_provider(monkeypatch, "OLLAMA_HOST", "http://127.0.0.1:11434")
    serve(monkeypatch, body)

    text = answer.stream_answer(PACK, out=io.StringIO())

    assert text == filler
    assert "truncated" not in capsys.readouterr().err


# -------------------------------------------------- framing still works ----


@pytest.mark.parametrize(
    "env,value,body,expected",
    [
        (
            "OPENAI_API_KEY",
            "sk-test",
            sse(["Hel", "lo"]),
            "Hello",
        ),
        (
            "ANTHROPIC_API_KEY",
            "sk-ant-test",
            b'data: {"type":"content_block_delta","delta":{"text":"Hel"}}\n\n'
            b'data: {"type":"content_block_delta","delta":{"text":"lo"}}\n\n'
            b"data: [DONE]\n\n",
            "Hello",
        ),
        (
            "GEMINI_API_KEY",
            "gk-test",
            b'data: {"candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}\n\n'
            b'data: {"candidates":[{"content":{"parts":[{"text":"lo"}]}}]}\n\n',
            "Hello",
        ),
        (
            "OLLAMA_HOST",
            "http://127.0.0.1:11434",
            ndjson(["Hel", "lo"]),
            "Hello",
        ),
    ],
)
def test_iss241_every_provider_framing_survives_the_reframer(
    monkeypatch, env, value, body, expected
):
    """Re-framing the byte stream must not drop line semantics for any provider."""
    only_provider(monkeypatch, env, value)
    serve(monkeypatch, body)
    assert answer.stream_answer(PACK, out=io.StringIO()) == expected


def test_iss241_a_line_spanning_several_read_blocks_is_reassembled(monkeypatch):
    """A delta larger than one read() must arrive whole, not in pieces.

    Reading in blocks only works if the framer rejoins a line split across a
    block boundary; dropping that would corrupt every long delta into unparsable
    JSON and silently shorten the answer.
    """
    piece = "m" * (3 * answer.READ_BLOCK + 17)
    only_provider(monkeypatch, "OPENAI_API_KEY", "sk-test")
    serve(monkeypatch, sse(["start ", piece, " end"]))

    text = answer.stream_answer(PACK, out=io.StringIO())

    assert text == "start " + piece + " end"


def test_iss241_crlf_framing_survives(monkeypatch):
    """Some proxies rewrite the SSE terminator; the framer splits on "\\n" only."""
    only_provider(monkeypatch, "OPENAI_API_KEY", "sk-test")
    serve(
        monkeypatch,
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\r\n\r\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\r\n\r\n'
        b"data: [DONE]\r\n\r\n",
    )
    assert answer.stream_answer(PACK, out=io.StringIO()) == "Hello"


# ------------------------------------------- the response-double contract ----


def test_iss241_an_iteration_only_response_still_works(monkeypatch):
    """`_blocks` falls back to iteration for objects with a no-argument read().

    `tests/test_rag.py`'s FakeResponse is exactly that shape, and so is anything
    wrapping a pre-split body; the fallback keeps them working rather than
    raising TypeError on the first sized read.
    """

    class IterOnly:
        def __init__(self, lines):
            self._lines = list(lines)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return iter(self._lines)

        def read(self):
            return b"".join(self._lines)

    only_provider(monkeypatch, "OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setattr(
        answer,
        "_OPENER",
        FakeOpener(
            lambda req, *a, **kw: IterOnly(
                [b'{"message":{"content":"Hel"}}\n', b'{"message":{"content":"lo"}}\n']
            )
        ),
    )
    assert answer.stream_answer(PACK, out=io.StringIO()) == "Hello"


# ---------------------------------------------------------------------------
# Redirect handling on the provider call
#
# CPython's HTTPRedirectHandler forwards every header except content-length
# and content-type to the redirect target, whatever host and scheme it names.
# The provider request carries the API key in one of those headers, so an
# unconstrained redirect hands the credential to wherever the response points
# -- and makes the hostname _disclose() printed the one place the request did
# not go.
# ---------------------------------------------------------------------------


def _redirect(from_url, to_url):
    """Ask the real handler what it does with `from_url` -> `to_url`."""
    handler = answer._SameOriginRedirect()
    req = urllib.request.Request(from_url, data=b"{}", method="POST")
    return handler.redirect_request(req, None, 302, "Found", http.client.HTTPMessage(), to_url)


@pytest.mark.parametrize(
    "target",
    [
        "http://api.openai.com/v1/chat/completions",  # scheme downgrade
        "https://evil.example.com/v1/chat/completions",  # different host
        "http://evil.example.com/v1/chat/completions",  # both
        "file:///etc/passwd",
        "ftp://api.openai.com/x",
    ],
)
def test_a_provider_redirect_that_would_leak_the_key_is_refused(target):
    assert _redirect("https://api.openai.com/v1/chat/completions", target) is None


def test_a_same_origin_provider_redirect_is_followed():
    onward = _redirect(
        "https://api.openai.com/v1/chat/completions",
        "https://api.openai.com/v2/chat/completions",
    )
    assert onward is not None
    assert onward.full_url == "https://api.openai.com/v2/chat/completions"


def test_an_ollama_upgrade_to_https_on_the_same_host_is_allowed():
    """OLLAMA_HOST is legitimately plain http; moving it to TLS is not an attack."""
    onward = _redirect("http://127.0.0.1:11434/api/chat", "https://127.0.0.1:11434/api/chat")
    assert onward is not None and onward.full_url.startswith("https://")


def test_stream_answer_goes_through_the_guarded_opener():
    """A bare urlopen would rebuild the default, permissive redirect handler."""
    src = pathlib.Path(answer.__file__).read_text(encoding="utf8")
    assert "urllib.request.urlopen(" not in src
    assert "_OPENER.open(req" in src

    installed = [
        h for h in answer._OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
    ]
    assert len(installed) == 1
    assert isinstance(installed[0], answer._SameOriginRedirect)
    assert answer._SameOriginRedirect.max_redirections == 3

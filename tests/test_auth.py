"""Bearer and OIDC authentication, including the forgeries it must refuse.

Signing happens here in pure Python for the same reason verification does in
`repo2graph.auth`: the suite must not need a crypto package to test a feature
whose whole point is not needing one. The keypair is generated deterministically
from a fixed seed, so every run signs with the same key and a failure is always
reproducible.

The negative cases matter more than the positive one. A JWT verifier that
accepts a valid token is easy; the bugs that get CVEs are `alg: none`, algorithm
confusion (HS256 signed with the RSA *public* key), an unenforced `exp`, and an
unenforced `aud` -- each of which leaves the happy path working perfectly.
"""

import base64
import hashlib
import http.client
import json
import random
import threading
import time
import urllib.error
import urllib.request

import pytest

from repo2graph import auth
from repo2graph.auth import (
    AuthConfig,
    AuthError,
    Authenticator,
    JWKSCache,
    client_metadata_document,
    decode_jwt,
    rsa_verify,
)

# --------------------------------------------------------------- keygen ----


def _is_probable_prime(n, rounds=24, rng=None):
    """Miller-Rabin. Deterministic enough for a test key, fast enough to run."""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    rng = rng or random.Random(0)
    for _ in range(rounds):
        a = rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits, rng):
    while True:
        cand = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(cand, rng=rng):
            return cand


def _make_key(seed=20260916, bits=512):
    """A deterministic RSA keypair. 1024-bit modulus: a test key, not a real one."""
    rng = random.Random(seed)
    while True:
        p, q = _prime(bits, rng), _prime(bits, rng)
        if p == q:
            continue
        n = p * q
        phi = (p - 1) * (q - 1)
        e = 65537
        if phi % e == 0:
            continue
        return {"n": n, "e": e, "d": pow(e, -1, phi)}


KEY = _make_key()
KID = "test-key-1"
ISSUER = "https://issuer.example.com"
AUDIENCE = "repo2graph"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def int_b64u(value: int) -> str:
    return b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def jwks_doc(kid=KID, key=KEY, alg="RS256"):
    """A one-key JWKS. Pass `alg=None` to omit the field (RFC 7517)."""
    entry = {"kty": "RSA", "kid": kid, "n": int_b64u(key["n"]), "e": int_b64u(key["e"])}
    if alg is not None:
        entry["alg"] = alg
    return {"keys": [entry]}


_ABSENT = object()


def jwks_with(**over):
    """A one-key JWKS built from the test key, with fields overridden or dropped.

    `_ABSENT` as a value removes the field outright, which is how a JWK that
    never declares `n`, `e`, `use` or `key_ops` gets expressed. `jwks_doc()`
    above is the same document; this one exists so a test can name the single
    field it is interested in and leave every other one correct.
    """
    entry = {
        "kty": "RSA",
        "kid": KID,
        "alg": "RS256",
        "n": int_b64u(KEY["n"]),
        "e": int_b64u(KEY["e"]),
    }
    entry.update(over)
    return {"keys": [{k: v for k, v in entry.items() if v is not _ABSENT}]}


def sign(claims, kid=KID, key=KEY, alg="RS256", hash_name="sha256"):
    """Produce a real RS256 JWT, signing with the private exponent."""
    header = b64u(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    payload = b64u(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode("ascii")

    k = (key["n"].bit_length() + 7) // 8
    digest = hashlib.new(hash_name, signing_input).digest()
    tail = auth.DIGEST_INFO_PREFIX[hash_name] + digest
    block = b"\x00\x01" + b"\xff" * (k - len(tail) - 3) + b"\x00" + tail
    sig = pow(int.from_bytes(block, "big"), key["d"], key["n"]).to_bytes(k, "big")
    return f"{header}.{payload}.{b64u(sig)}"


def claims(**over):
    base = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-42", "exp": time.time() + 600}
    base.update(over)
    return base


class FakeIssuer:
    """Serves the discovery and JWKS documents, counting every fetch."""

    def __init__(self, jwks=None, issuer=ISSUER):
        self.issuer = issuer
        self.jwks = jwks or jwks_doc()
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": self.issuer, "jwks_uri": f"{self.issuer}/jwks"}
        if url.endswith("/jwks"):
            return self.jwks
        raise AssertionError(f"unexpected fetch: {url}")


class Clock:
    """A monotonic clock a test can advance without sleeping."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def cache(issuer=None, ttl=300.0, clock=None, **kwargs):
    issuer = issuer or FakeIssuer()
    return (
        JWKSCache(ISSUER, ttl, opener=issuer, clock=clock or Clock(), **kwargs),
        issuer,
    )


def _jwks_fetches(issuer: FakeIssuer) -> int:
    return issuer.calls.count(f"{ISSUER}/jwks")


def junk_jwt(kid: str) -> str:
    """Three-segment RS256-shaped JWT whose signature is never valid."""
    header = b64u(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    payload = b64u(json.dumps(claims()).encode())
    return f"{header}.{payload}.{b64u(b'not-a-signature')}"


# ----------------------------------------------------------------- rsa ----


def test_rsa_verify_accepts_a_real_signature():
    token = sign(claims())
    head, payload, sig = token.split(".")
    assert rsa_verify(
        KEY["n"], KEY["e"], auth.b64url_decode(sig), f"{head}.{payload}".encode(), "sha256"
    )


def test_rsa_verify_rejects_a_tampered_message():
    token = sign(claims())
    head, payload, sig = token.split(".")
    assert not rsa_verify(
        KEY["n"], KEY["e"], auth.b64url_decode(sig), f"{head}.{payload}x".encode(), "sha256"
    )


def test_rsa_verify_rejects_a_wrong_length_signature():
    assert not rsa_verify(KEY["n"], KEY["e"], b"\x01\x02", b"msg", "sha256")


def test_rsa_verify_rejects_a_zero_modulus():
    """A malicious JWK with n=0 must fail closed, not raise ValueError from pow()."""
    assert rsa_verify(0, KEY["e"], b"", b"msg", "sha256") is False


def test_rsa_verify_rejects_a_negative_modulus():
    """A negative n must fail closed, not raise OverflowError from to_bytes()."""
    n = -KEY["n"]
    k = (KEY["n"].bit_length() + 7) // 8
    sig = b"\x01" * k
    assert rsa_verify(n, KEY["e"], sig, b"msg", "sha256") is False


def test_rsa_verify_rejects_a_zero_exponent():
    assert (
        rsa_verify(KEY["n"], 0, b"\x01" * ((KEY["n"].bit_length() + 7) // 8), b"msg", "sha256")
        is False
    )


def test_rsa_verify_rejects_garbage_in_the_padding():
    """The whole block is compared, so a forged DigestInfo in slack space fails.

    A verifier that *scans* for the digest instead of rebuilding the block
    accepts Bleichenbacher-style forgeries against low exponents.
    """
    digest = hashlib.sha256(b"msg").digest()
    tail = auth.DIGEST_INFO_PREFIX["sha256"] + digest
    k = (KEY["n"].bit_length() + 7) // 8
    forged = b"\x00\x01" + b"\x00" * (k - len(tail) - 3) + b"\x00" + tail
    sig = int.from_bytes(forged, "big").to_bytes(k, "big")
    assert not rsa_verify(KEY["n"], KEY["e"], sig, b"msg", "sha256")


# ----------------------------------------------------------------- jwt ----


def test_a_valid_token_decodes():
    jwks, _ = cache()
    got = decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert got["sub"] == "user-42"


def test_alg_none_is_refused():
    """The oldest JWT forgery: claim there is no signature and supply none."""
    header = b64u(json.dumps({"alg": "none", "kid": KID}).encode())
    payload = b64u(json.dumps(claims()).encode())
    jwks, _ = cache()
    with pytest.raises(AuthError, match="unsupported token algorithm"):
        decode_jwt(f"{header}.{payload}.", jwks, ISSUER, AUDIENCE)


def test_hmac_algorithm_confusion_is_refused():
    """HS256 signed with the RSA public key must not validate.

    If the verifier picks its algorithm from the token, an attacker who has the
    public key -- which is public -- can mint tokens at will.
    """
    header = b64u(json.dumps({"alg": "HS256", "kid": KID}).encode())
    payload = b64u(json.dumps(claims()).encode())
    pub = int_b64u(KEY["n"]).encode()
    import hmac as _hmac

    sig = _hmac.new(pub, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    jwks, _ = cache()
    with pytest.raises(AuthError, match="unsupported token algorithm"):
        decode_jwt(f"{header}.{payload}.{b64u(sig)}", jwks, ISSUER, AUDIENCE)


def test_declared_jwk_alg_rejects_a_token_override():
    """A JWK that names RS384 must not let the token pick RS256's hash (#207)."""
    jwks, _ = cache(FakeIssuer(jwks=jwks_doc(alg="RS384")))
    with pytest.raises(AuthError, match="does not match the signing key"):
        decode_jwt(sign(claims(), alg="RS256", hash_name="sha256"), jwks, ISSUER, AUDIENCE)


def test_unsupported_jwk_alg_is_refused_even_if_token_is_rsa():
    """A declared-but-unknown JWK alg must not fall back to the token (#207)."""
    jwks, _ = cache(FakeIssuer(jwks=jwks_doc(alg="HS256")))
    with pytest.raises(AuthError, match="unsupported key algorithm"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_declared_jwk_alg_drives_the_verify_hash(monkeypatch):
    """When the JWK names RS384, verification hashes with SHA-384 (#207)."""
    seen = []
    real = auth.rsa_verify

    def spy(n, e, signature, message, hash_name):
        seen.append(hash_name)
        return real(n, e, signature, message, hash_name)

    monkeypatch.setattr(auth, "rsa_verify", spy)
    jwks, _ = cache(FakeIssuer(jwks=jwks_doc(alg="RS384")))
    got = decode_jwt(sign(claims(), alg="RS384", hash_name="sha384"), jwks, ISSUER, AUDIENCE)
    assert got["sub"] == "user-42"
    assert seen == ["sha384"]


def test_jwk_without_alg_accepts_token_rs256():
    """RFC 7517 makes JWK alg optional; the token may still pick RS256 (#207)."""
    jwks, _ = cache(FakeIssuer(jwks=jwks_doc(alg=None)))
    got = decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert got["sub"] == "user-42"


@pytest.mark.parametrize("alg", ["none", "HS256"])
def test_alg_none_and_hs256_are_refused_when_jwk_omits_alg(alg):
    """Omitting JWK alg must not let the token pick a non-RSA algorithm (#207)."""
    header = b64u(json.dumps({"alg": alg, "kid": KID}).encode())
    payload = b64u(json.dumps(claims()).encode())
    jwks, _ = cache(FakeIssuer(jwks=jwks_doc(alg=None)))
    with pytest.raises(AuthError, match="unsupported token algorithm"):
        decode_jwt(f"{header}.{payload}.", jwks, ISSUER, AUDIENCE)


def test_an_expired_token_is_refused():
    jwks, _ = cache()
    with pytest.raises(AuthError, match="expired"):
        decode_jwt(sign(claims(exp=time.time() - 3600)), jwks, ISSUER, AUDIENCE)


def test_a_token_with_no_exp_is_refused():
    body = claims()
    body.pop("exp")
    jwks, _ = cache()
    with pytest.raises(AuthError, match="no exp"):
        decode_jwt(sign(body), jwks, ISSUER, AUDIENCE)


def test_a_not_yet_valid_token_is_refused():
    jwks, _ = cache()
    with pytest.raises(AuthError, match="not valid yet"):
        decode_jwt(sign(claims(nbf=time.time() + 3600)), jwks, ISSUER, AUDIENCE)


def test_a_nan_exp_claim_is_refused():
    """NaN compares False against every relational operator in IEEE 754.

    `float("nan") + CLOCK_SKEW < now` is False, so a naive expiry check lets a
    NaN `exp` claim through as if the token never expires. It must fail closed.
    """
    jwks, _ = cache()
    with pytest.raises(AuthError, match="expired"):
        decode_jwt(sign(claims(exp=float("nan"))), jwks, ISSUER, AUDIENCE)


def test_an_infinite_exp_claim_is_refused():
    """+Infinity satisfies `exp + CLOCK_SKEW < now` as False forever too."""
    jwks, _ = cache()
    with pytest.raises(AuthError, match="expired"):
        decode_jwt(sign(claims(exp=float("inf"))), jwks, ISSUER, AUDIENCE)


def test_a_nan_nbf_claim_is_refused():
    jwks, _ = cache()
    with pytest.raises(AuthError, match="not valid yet"):
        decode_jwt(sign(claims(nbf=float("nan"))), jwks, ISSUER, AUDIENCE)


def test_a_negative_infinite_nbf_claim_is_refused():
    """-Infinity is not finite either; a not-a-finite-number claim must reject,

    not be reinterpreted as "always valid".
    """
    jwks, _ = cache()
    with pytest.raises(AuthError, match="not valid yet"):
        decode_jwt(sign(claims(nbf=float("-inf"))), jwks, ISSUER, AUDIENCE)


def test_the_wrong_issuer_is_refused():
    jwks, _ = cache()
    with pytest.raises(AuthError, match="issuer"):
        decode_jwt(sign(claims(iss="https://evil.example.com")), jwks, ISSUER, AUDIENCE)


def test_the_wrong_audience_is_refused():
    jwks, _ = cache()
    with pytest.raises(AuthError, match="audience"):
        decode_jwt(sign(claims(aud="some-other-service")), jwks, ISSUER, AUDIENCE)


def test_a_list_audience_containing_ours_is_accepted():
    jwks, _ = cache()
    got = decode_jwt(sign(claims(aud=["other", AUDIENCE])), jwks, ISSUER, AUDIENCE)
    assert got["sub"] == "user-42"


def test_a_signature_from_another_key_is_refused():
    other = _make_key(seed=999)
    jwks, _ = cache()
    with pytest.raises(AuthError, match="signature is invalid"):
        decode_jwt(sign(claims(), key=other), jwks, ISSUER, AUDIENCE)


def test_a_tampered_payload_is_refused():
    token = sign(claims())
    head, _payload, sig = token.split(".")
    forged = b64u(json.dumps(claims(sub="root")).encode())
    jwks, _ = cache()
    with pytest.raises(AuthError, match="signature is invalid"):
        decode_jwt(f"{head}.{forged}.{sig}", jwks, ISSUER, AUDIENCE)


def test_a_jwk_with_no_modulus_is_refused():
    """Issue 238: kty RSA and no `n` indexed straight into the dict.

    `key["n"]` on a key set that never published one is a KeyError, and a
    KeyError is not an AuthError -- it becomes a 500 on the tool path and a
    dead handler thread on the refusal path, which catches AuthError only.
    """
    jwks, _ = cache(issuer=FakeIssuer(jwks=jwks_with(n=_ABSENT)))
    with pytest.raises(AuthError, match="missing its RSA parameters"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_a_jwk_with_no_exponent_is_refused():
    """The same hole, one field over: `e` is read unguarded too."""
    jwks, _ = cache(issuer=FakeIssuer(jwks=jwks_with(e=_ABSENT)))
    with pytest.raises(AuthError, match="missing its RSA parameters"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_a_jwk_whose_modulus_is_a_json_number_is_refused():
    """Presence is not enough: the parameter has to be the base64url string.

    `str()` of a JSON integer is a run of decimal digits, which base64url
    either decodes to some unrelated modulus or rejects as bad padding. Either
    way the key is silently reinterpreted rather than refused, so the type
    check matters as much as the presence check.
    """
    jwks, _ = cache(issuer=FakeIssuer(jwks=jwks_with(n=KEY["n"])))
    with pytest.raises(AuthError, match="missing its RSA parameters"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_a_jwk_declaring_use_enc_is_refused():
    """RFC 7517 §4.2: `{"use": "enc"}` is the issuer saying "not for signatures".

    A JWKS publishes encryption and signing keys side by side, so this is a
    real shape, not a hypothetical one.
    """
    jwks, _ = cache(issuer=FakeIssuer(jwks=jwks_with(use="enc")))
    with pytest.raises(AuthError, match="declares use 'enc'"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_a_jwk_whose_key_ops_excludes_verify_is_refused():
    """RFC 7517 §4.3: an explicit op list that omits "verify" forbids verifying."""
    jwks, _ = cache(issuer=FakeIssuer(jwks=jwks_with(key_ops=["encrypt", "decrypt"])))
    with pytest.raises(AuthError, match="does not permit the verify operation"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_a_jwk_declaring_neither_use_nor_key_ops_still_verifies():
    """Absent means unconstrained, per RFC 7517 §4.2/§4.3.

    This is the ordinary JWKS entry that every real issuer serves, and a
    purpose check that reads "absent" as "forbidden" would lock out all of
    them. Pinned as its own case because the two rejections above pass just as
    happily under a check that rejects everything.
    """
    entry = jwks_with()["keys"][0]
    assert "use" not in entry and "key_ops" not in entry
    jwks, _ = cache(issuer=FakeIssuer(jwks={"keys": [entry]}))
    assert decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)["sub"] == "user-42"


def test_a_jwk_declaring_use_sig_and_a_verify_op_is_accepted():
    """The positive declaration is honoured, not merely tolerated."""
    jwks, _ = cache(issuer=FakeIssuer(jwks=jwks_with(use="sig", key_ops=["verify"])))
    assert decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)["sub"] == "user-42"


# ---------------------------------------------------------------- jwks ----


def test_jwks_is_cached_between_calls():
    jwks, issuer = cache()
    for _ in range(3):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 1, issuer.calls


def test_an_expired_ttl_refetches():
    """Keys are re-read once the TTL has elapsed.

    The clock is injected rather than slept through. An earlier version used
    ttl=0.0 and two back-to-back calls, which passes only if the monotonic
    clock ticks between them -- so it passed on one machine and failed on
    another, where Windows falls back to GetTickCount64's ~15.6ms granularity
    and both calls read the same instant.
    """
    clock = Clock()
    jwks, issuer = cache(ttl=300.0, clock=clock)

    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 1

    clock.advance(299)  # still inside the TTL
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 1, "refetched too early"

    clock.advance(2)  # now past it
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 2


def test_a_zero_ttl_never_caches():
    """`ttl=0` means "do not cache", whatever the clock's resolution.

    With a strict `>` comparison this promise silently depended on the clock
    ticking between calls, which is exactly the flake above in its other form:
    a configuration that says "never cache" would keep serving stale keys.
    """
    frozen = Clock()  # never advances
    jwks, issuer = cache(ttl=0.0, clock=frozen)

    for _ in range(3):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 3, issuer.calls


def test_an_unknown_kid_refetches_exactly_once_then_fails():
    """Key rotation costs one refetch; a kid flood must not cost one each."""
    jwks, issuer = cache()
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    before = _jwks_fetches(issuer)

    with pytest.raises(AuthError, match="unknown signing key"):
        decode_jwt(sign(claims(), kid="rotated-in"), jwks, ISSUER, AUDIENCE)
    assert _jwks_fetches(issuer) == before + 1


def test_the_same_unknown_kid_does_not_refetch_on_repeat_lookups():
    """ISS-195: a negative kid lookup is remembered, so N repeats cost 1 fetch.

    The previous test asked once. Asking again is what distinguishes a
    per-request refetch (the bug) from a per-kid cap (the control the
    module docstring claimed).
    """
    jwks, issuer = cache(ttl=3600.0)
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    before = _jwks_fetches(issuer)

    for _ in range(5):
        with pytest.raises(AuthError, match="unknown signing key"):
            jwks.key_for("no-such-kid")
    assert _jwks_fetches(issuer) == before + 1, issuer.calls


def test_distinct_unknown_kids_share_a_min_refresh_interval():
    """ISS-195: a stream of distinct kids must not force one fetch each."""
    clock = Clock()
    jwks, issuer = cache(ttl=3600.0, clock=clock)
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    before = _jwks_fetches(issuer)

    for i in range(6):
        with pytest.raises(AuthError, match="unknown signing key"):
            jwks.key_for(f"rand-{i}")
    assert _jwks_fetches(issuer) == before + 1, issuer.calls

    clock.advance(auth.DEFAULT_MIN_REFRESH_INTERVAL / 2)
    with pytest.raises(AuthError, match="unknown signing key"):
        jwks.key_for("rand-still-inside-window")
    assert _jwks_fetches(issuer) == before + 1, "min-interval did not bind"


def test_a_new_kid_is_fetched_once_the_min_interval_elapses():
    """The interval is a floor, not a permanent disable of rotation checks."""
    clock = Clock()
    jwks, issuer = cache(ttl=3600.0, clock=clock)
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    with pytest.raises(AuthError, match="unknown signing key"):
        jwks.key_for("ghost-1")
    mid = _jwks_fetches(issuer)

    clock.advance(auth.DEFAULT_MIN_REFRESH_INTERVAL)
    new_key = _make_key(seed=7)
    issuer.jwks = jwks_doc(kid="kid-2", key=new_key)
    got = decode_jwt(sign(claims(), kid="kid-2", key=new_key), jwks, ISSUER, AUDIENCE)
    assert got["sub"] == "user-42"
    assert _jwks_fetches(issuer) == mid + 1


def test_unsigned_unknown_kid_tokens_do_not_amplify_jwks_fetches():
    """decode_jwt looks up the kid before rsa_verify.

    Unsigned junk with random kids is therefore enough to reach key_for, and
    must still hit the per-window cap — the issue 195 repro, at unit level.
    """
    jwks, issuer = cache(ttl=3600.0)
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    before = _jwks_fetches(issuer)

    for i in range(10):
        with pytest.raises(AuthError, match="unknown signing key"):
            decode_jwt(junk_jwt(f"junk-{i}"), jwks, ISSUER, AUDIENCE)
    assert _jwks_fetches(issuer) == before + 1, issuer.calls


def test_jwks_network_fetch_is_not_held_under_the_cache_lock():
    """ISS-195: take the lock to decide/swap, not across the HTTPS round-trip."""
    issuer = FakeIssuer()
    jwks = JWKSCache(ISSUER, 3600.0, opener=issuer, clock=Clock())
    real_open = jwks._open

    def wrapped(url):
        assert not jwks._lock.locked(), "JWKS fetch held the cache lock"
        return real_open(url)

    jwks._open = wrapped
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    with pytest.raises(AuthError, match="unknown signing key"):
        jwks.key_for("missing")


def test_cached_key_lookup_is_not_blocked_by_a_slow_unknown_kid_fetch():
    """A slow issuer on an unknown kid must not serialise a cached lookup."""
    ready = threading.Event()
    release = threading.Event()
    issuer = FakeIssuer()
    jwks_hits = {"n": 0}

    def opener(url):
        if url.endswith("/jwks"):
            jwks_hits["n"] += 1
            if jwks_hits["n"] > 1:
                ready.set()
                assert release.wait(timeout=5)
        return issuer(url)

    jwks = JWKSCache(ISSUER, 3600.0, opener=opener, clock=time.monotonic)
    assert jwks.key_for(KID)["kid"] == KID

    errors: list[BaseException] = []

    def miss() -> None:
        try:
            jwks.key_for("ghost")
        except AuthError as exc:
            errors.append(exc)
        except Exception as exc:
            errors.append(exc)

    t_miss = threading.Thread(target=miss)
    t_miss.start()
    assert ready.wait(timeout=2), "unknown-kid fetch never started"

    held: list[dict] = []

    def hit() -> None:
        held.append(jwks.key_for(KID))

    t_hit = threading.Thread(target=hit)
    t_hit.start()
    t_hit.join(timeout=0.4)
    blocked = t_hit.is_alive()
    release.set()
    t_miss.join(timeout=5)
    t_hit.join(timeout=5)
    assert not blocked, "cached key_for waited on the unknown-kid fetch"
    assert held and held[0]["kid"] == KID
    assert errors


def test_concurrent_unknown_kids_do_not_serialise_behind_one_slow_fetch():
    """ISS-195: eight distinct unknown kids must not become eight serial RTTs."""
    sleep_s = 0.15
    primed = {"done": False}
    issuer = FakeIssuer()

    def opener(url):
        if url.endswith("/jwks") and primed["done"]:
            time.sleep(sleep_s)
        return issuer(url)

    jwks = JWKSCache(ISSUER, 3600.0, opener=opener, clock=time.monotonic)
    assert jwks.key_for(KID)["kid"] == KID
    before = _jwks_fetches(issuer)
    primed["done"] = True

    n = 8
    barrier = threading.Barrier(n)
    errors: list[str] = []

    def worker(i: int) -> None:
        barrier.wait()
        try:
            jwks.key_for(f"missing-{i}")
        except AuthError:
            errors.append(f"missing-{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    t0 = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.monotonic() - t0

    assert len(errors) == n
    assert _jwks_fetches(issuer) == before + 1, issuer.calls
    # Old code held the lock across each sleep: ~n * sleep_s of wall time.
    assert wall < sleep_s * n * 0.6, f"lookups serialised: {wall:.3f}s"


def test_a_rotated_key_is_picked_up_on_the_refetch():
    issuer = FakeIssuer()
    jwks = JWKSCache(ISSUER, 300.0, opener=issuer)
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)

    new_key = _make_key(seed=4242)
    issuer.jwks = jwks_doc(kid="kid-2", key=new_key)
    got = decode_jwt(sign(claims(), kid="kid-2", key=new_key), jwks, ISSUER, AUDIENCE)
    assert got["sub"] == "user-42"


def test_a_discovery_document_declaring_another_issuer_is_refused():
    issuer = FakeIssuer(issuer=ISSUER)

    def lying(url):
        if url.endswith("openid-configuration"):
            return {
                "issuer": "https://evil.example.com",
                "jwks_uri": "https://evil.example.com/jwks",
            }
        return issuer(url)

    jwks = JWKSCache(ISSUER, 300.0, opener=lying)
    with pytest.raises(AuthError, match="issuer mismatch"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_plain_http_issuers_are_refused(monkeypatch):
    """Fetching signing keys over http would put them on the wire in clear."""
    with pytest.raises(AuthError, match="must be https"):
        auth._fetch_json("http://issuer.example.com/.well-known/openid-configuration")


# --------------------------------------------------- discovery hardening ----
#
# GHSA-f896-f643-87cf and GHSA-mqm8-mc66-wjvj. Both are about where the signing
# keys come from rather than how a token is checked: every other guarantee in
# this module is conditional on the key set being the issuer's.


class FakeOpener:
    """Stand-in for `auth._OPENER`, delegating to a urlopen-shaped callable.

    `_fetch_json` goes through an opener rather than `urlopen` so that
    `_HTTPSOnlyRedirect` cannot be bypassed; patching the opener is what keeps
    these tests off the network.
    """

    def __init__(self, fn):
        self._fn = fn

    def open(self, req, *a, **kw):
        return self._fn(req, *a, **kw)


def _serves(doc):
    """An opener that answers discovery with `doc` and refuses anything else.

    The AssertionError is the point of the helper: it proves the key fetch is
    never reached, so a rejection happened before anything was sourced from
    the host the document named.
    """

    def opener(url):
        if url.endswith("/.well-known/openid-configuration"):
            return doc
        raise AssertionError(f"must not fetch {url}")

    return opener


def test_a_jwks_uri_on_another_host_is_refused():
    """One field in a fetched document must not relocate the trust anchor."""
    jwks = JWKSCache(
        ISSUER,
        300.0,
        opener=_serves({"issuer": ISSUER, "jwks_uri": "https://evil.example.com/jwks"}),
        clock=Clock(),
    )
    with pytest.raises(AuthError, match="jwks_uri origin"):
        jwks.key_for(KID)


def test_a_jwks_uri_downgraded_to_http_is_refused():
    """Same host, cleartext scheme: still not where these keys come from."""
    jwks = JWKSCache(
        ISSUER,
        300.0,
        opener=_serves({"issuer": ISSUER, "jwks_uri": "http://issuer.example.com/jwks"}),
        clock=Clock(),
    )
    with pytest.raises(AuthError, match="jwks_uri origin"):
        jwks.key_for(KID)


def test_a_discovery_document_without_an_issuer_is_refused():
    """RFC 8414 §3.2 makes `issuer` REQUIRED.

    Absence used to skip the mismatch check entirely, so a document opted out
    of being compared by leaving the field off.
    """
    jwks = JWKSCache(
        ISSUER,
        300.0,
        opener=_serves({"jwks_uri": f"{ISSUER}/jwks"}),
        clock=Clock(),
    )
    with pytest.raises(AuthError, match="no issuer"):
        jwks.key_for(KID)


def test_a_redirect_off_https_is_refused():
    """urlopen follows redirects; CPython's handler allows http, https and ftp.

    Without this the scheme check in `_fetch_json` covers the first hop only,
    and a 302 to http:// puts the signing keys on the wire in clear -- where
    an on-path attacker substitutes their own modulus and mints tokens that
    pass every remaining check in the module.
    """
    handler = auth._HTTPSOnlyRedirect()
    req = urllib.request.Request(f"{ISSUER}/jwks")
    headers = http.client.HTTPMessage()

    for target in (
        "http://issuer.example.com/jwks",
        "ftp://issuer.example.com/jwks",
        "//issuer.example.com/jwks",
    ):
        assert handler.redirect_request(req, None, 302, "Found", headers, target) is None, target

    onward = handler.redirect_request(
        req, None, 302, "Found", headers, "https://issuer.example.com/keys"
    )
    assert onward is not None and onward.full_url == "https://issuer.example.com/keys"


def test_fetch_json_goes_through_the_guarded_opener(monkeypatch):
    """The handler only helps if `_fetch_json` stops using the default opener.

    A bare `urlopen` would silently keep the permissive redirect handler, so
    pin the call site rather than only the handler's own logic.
    """
    used = []

    class FakeOpener:
        def open(self, request, timeout=None):
            used.append((request.full_url, timeout))
            raise urllib.error.URLError("stop here")

    monkeypatch.setattr(auth, "_OPENER", FakeOpener())
    with pytest.raises(AuthError, match="could not reach the issuer"):
        auth._fetch_json(f"{ISSUER}/jwks")

    assert used == [(f"{ISSUER}/jwks", auth.HTTP_TIMEOUT)]


def test_the_guarded_opener_replaces_the_permissive_handler():
    """build_opener drops its default only because ours subclasses it."""
    installed = [
        h for h in auth._OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
    ]
    assert len(installed) == 1
    assert isinstance(installed[0], auth._HTTPSOnlyRedirect)
    assert auth._HTTPSOnlyRedirect.max_redirections == 3


# ------------------------------------------------------- authenticator ----


def test_no_auth_configured_admits_everything():
    who = Authenticator(AuthConfig()).authenticate(None)
    assert who.subject == "anonymous" and who.mode == "none"


def test_no_auth_configured_ignores_a_supplied_header():
    who = Authenticator(AuthConfig()).authenticate("Bearer whatever")
    assert who.mode == "none"


def test_the_right_static_token_is_admitted():
    who = Authenticator(AuthConfig(token="s3cret")).authenticate("Bearer s3cret")
    assert who.mode == "bearer" and who.subject == "bearer"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "s3cret",
        "Basic s3cret",
        "Bearer",
        "Bearer ",
        "Bearer wrong",
    ],
)
def test_a_bad_static_token_is_refused(header):
    with pytest.raises(AuthError):
        Authenticator(AuthConfig(token="s3cret")).authenticate(header)


def test_a_non_ascii_credential_is_refused_rather_than_raising_typeerror():
    """Issue 232: `hmac.compare_digest` raises on a non-ASCII str operand.

    The credential is the raw Authorization header, so any client can trigger
    it. A TypeError is not an AuthError, so the transport's refusal path never
    sees it: the handler thread dies and the caller gets no response at all,
    not a 401 and not a 500. Only AuthError is an acceptable outcome here.
    """
    with pytest.raises(AuthError):
        Authenticator(AuthConfig(token="s3cret")).authenticate("Bearer p\xe4ssw\xf6rd")


def test_a_non_ascii_credential_is_refused_with_oidc_also_configured():
    """Same header, the other configuration: the static branch runs first and
    falls through to OIDC, so both wirings have to survive it."""
    who = Authenticator(
        AuthConfig(token="s3cret", oidc_issuer=ISSUER, audience=AUDIENCE),
        opener=FakeIssuer(),
    )
    with pytest.raises(AuthError):
        who.authenticate("Bearer p\xe4ssw\xf6rd")


def test_a_non_ascii_configured_token_refuses_rather_than_raising():
    """The operator's own token may be non-ASCII too.

    Encoding only the credential would move the TypeError one operand across
    and leave the dead thread exactly where it was -- reachable, this time, by
    any request at all rather than only a non-ASCII one.
    """
    who = Authenticator(AuthConfig(token="p\xe4ssw\xf6rd"))
    with pytest.raises(AuthError):
        who.authenticate("Bearer p\xe4ssw\xf6rd")
    with pytest.raises(AuthError):
        who.authenticate("Bearer s3cret")


def test_the_static_token_comparison_is_constant_time():
    """A `==` here would leak the secret's length and prefix through timing."""
    import inspect

    src = inspect.getsource(Authenticator.authenticate)
    assert "compare_digest" in src
    assert "credential == self.config.token" not in src


def test_an_oidc_token_yields_the_sub_claim():
    issuer = FakeIssuer()
    who = Authenticator(
        AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE), opener=issuer
    ).authenticate("Bearer " + sign(claims()))
    assert who.mode == "oidc" and who.subject == "user-42"


def test_an_expired_oidc_token_is_refused():
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE), opener=issuer)
    # Comfortably past CLOCK_SKEW: a token only seconds stale is deliberately
    # still accepted, because host clocks disagree by more than that routinely.
    with pytest.raises(AuthError, match="expired"):
        who.authenticate("Bearer " + sign(claims(exp=time.time() - 3600)))


def test_a_token_just_inside_the_skew_window_is_still_accepted():
    """Clock skew tolerance is deliberate, so pin it rather than discover it."""
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE), opener=issuer)
    assert who.authenticate("Bearer " + sign(claims(exp=time.time() - 5))).mode == "oidc"


def test_a_wrong_issuer_oidc_token_is_refused():
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE), opener=issuer)
    with pytest.raises(AuthError, match="issuer"):
        who.authenticate("Bearer " + sign(claims(iss="https://evil.example.com")))


def test_both_modes_together_accept_either_credential():
    issuer = FakeIssuer()
    who = Authenticator(
        AuthConfig(token="s3cret", oidc_issuer=ISSUER, audience=AUDIENCE), opener=issuer
    )
    assert who.authenticate("Bearer s3cret").mode == "bearer"
    assert who.authenticate("Bearer " + sign(claims())).mode == "oidc"
    with pytest.raises(AuthError):
        who.authenticate("Bearer neither")


def test_auth_modes_reported_for_discovery():
    assert AuthConfig().modes == ["none"]
    assert AuthConfig(token="x").modes == ["bearer"]
    assert AuthConfig(oidc_issuer=ISSUER).modes == ["oidc"]
    assert AuthConfig(token="x", oidc_issuer=ISSUER).modes == ["bearer", "oidc"]


def test_an_error_never_echoes_the_credential():
    """A refusal is logged; it must not put the attempted secret in the log."""
    try:
        Authenticator(AuthConfig(token="s3cret")).authenticate("Bearer hunter2")
    except AuthError as exc:
        assert "hunter2" not in str(exc)
    else:
        raise AssertionError("expected a refusal")


# ---------------------------------------------------------------- cimd ----


def test_client_metadata_document_is_self_describing():
    """RFC 7591 client metadata, with client_id equal to its own URL."""
    doc = client_metadata_document("https://example.test:8719")
    assert doc["client_id"] == "https://example.test:8719/.well-known/oauth-client-metadata"
    for field in (
        "client_name",
        "grant_types",
        "response_types",
        "token_endpoint_auth_method",
        "redirect_uris",
    ):
        assert doc[field], field
    assert "authorization_code" in doc["grant_types"]
    json.dumps(doc)  # must be serialisable as-is


# ----------------------------------------------------------- refusals & edge cases ----


def test_b64url_decode_invalid():
    with pytest.raises(AuthError, match="malformed token encoding"):
        auth.b64url_decode("!!!invalid_base64!!!")


def test_rsa_verify_degenerate_and_unsupported_inputs():
    assert not rsa_verify(KEY["n"], KEY["e"], b"sig", b"msg", "unsupported_hash")
    assert not rsa_verify(0, KEY["e"], b"sig", b"msg", "sha256")
    assert not rsa_verify(KEY["n"], 0, b"sig", b"msg", "sha256")
    assert not rsa_verify(-1, KEY["e"], b"sig", b"msg", "sha256")
    assert not rsa_verify(KEY["n"], KEY["e"], b"short", b"msg", "sha256")
    # Small modulus k < len(tail) + 11
    assert not rsa_verify(15, 3, b"\x00", b"msg", "sha256")


def test_jwks_cache_discovery_missing_jwks_uri():
    cache = JWKSCache(
        "https://test.issuer",
        opener=lambda url: {"issuer": "https://test.issuer"},
    )
    with pytest.raises(AuthError, match="issuer discovery document has no jwks_uri"):
        cache.key_for("some-kid")


def test_jwks_cache_document_missing_keys_list():
    def opener(url):
        if "openid-configuration" in url:
            return {"issuer": "https://test.issuer", "jwks_uri": "https://test.issuer/jwks"}
        return {"not_keys": "invalid"}

    cache = JWKSCache("https://test.issuer", opener=opener)
    with pytest.raises(AuthError, match="issuer JWKS has no key list"):
        cache.key_for("some-kid")


def test_jwks_cache_negative_cache_expiration():
    clock_val = [100.0]

    def fake_clock():
        return clock_val[0]

    cache = JWKSCache(ISSUER, opener=FakeIssuer(), clock=fake_clock)
    with pytest.raises(AuthError, match="unknown signing key"):
        cache.key_for("nonexistent-1")
    assert cache._is_negative("nonexistent-1", 100.0)

    # Advance clock past min_refresh_interval
    clock_val[0] += cache.min_refresh_interval + 1.0
    assert not cache._is_negative("nonexistent-1", clock_val[0])


def test_jwks_cache_miss_eviction_and_install_clearing():
    from repo2graph.auth import MAX_NEGATIVE_KIDS

    cache = JWKSCache(ISSUER, opener=FakeIssuer())
    for i in range(MAX_NEGATIVE_KIDS + 10):
        cache._remember_miss(f"kid-{i}", float(i))
    assert len(cache._misses) <= MAX_NEGATIVE_KIDS
    assert "kid-0" not in cache._misses
    assert f"kid-{MAX_NEGATIVE_KIDS + 9}" in cache._misses

    # Test _install clearing seen keys from _misses
    cache._misses["test-key-1"] = 100.0
    cache._install({"test-key-1": {"kid": "test-key-1"}}, "https://issuer/jwks", 150.0)
    assert "test-key-1" not in cache._misses


def test_jwks_cache_in_flight_concurrency_branches():
    cache = JWKSCache(ISSUER, opener=FakeIssuer())
    cache._keys = {"existing": {"kid": "existing"}}
    cache._fetched_at = 1.0  # stale
    cache._in_flight = threading.Event()

    # Cached key returned immediately even if stale when in-flight fetch is active
    assert cache.key_for("existing") == {"kid": "existing"}

    # Unknown key refused when in-flight fetch is already active
    with pytest.raises(AuthError, match="unknown signing key"):
        cache.key_for("missing")

    # When _keys is empty, key_for waits on _in_flight
    cache2 = JWKSCache(ISSUER, opener=FakeIssuer())
    cache2._keys = {}
    cache2._fetched_at = 0.0
    ev = threading.Event()
    cache2._in_flight = ev

    def trigger():
        time.sleep(0.02)
        cache2._keys = {"waited-kid": {"kid": "waited-kid"}}
        ev.set()

    t = threading.Thread(target=trigger)
    t.start()
    found = cache2.key_for("waited-kid")
    t.join()
    assert found == {"kid": "waited-kid"}

    # Waiter finishes but key still not found
    cache3 = JWKSCache(ISSUER, opener=FakeIssuer())
    cache3._keys = {}
    cache3._fetched_at = 0.0
    ev3 = threading.Event()
    cache3._in_flight = ev3

    def trigger3():
        time.sleep(0.02)
        cache3._keys = {"other-kid": {"kid": "other-kid"}}
        ev3.set()

    t3 = threading.Thread(target=trigger3)
    t3.start()
    with pytest.raises(AuthError, match="unknown signing key"):
        cache3.key_for("waited-kid-absent")
    t3.join()


def test_fetch_json_errors(monkeypatch):
    with pytest.raises(AuthError, match="issuer URLs must be https"):
        auth._fetch_json("http://insecure.example.com")

    with pytest.raises(AuthError, match="could not reach the issuer"):
        auth._fetch_json("https://127.0.0.1:9")

    class FakeLargeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n):
            return b"x" * (auth.MAX_JWKS_BYTES + 2)

    # Patched on the opener, not on urlopen: `_fetch_json` goes through
    # `_OPENER` so its https-only redirect handler cannot be bypassed, and a
    # urlopen patch would leave this reaching the real network.
    monkeypatch.setattr(auth, "_OPENER", FakeOpener(lambda req, timeout: FakeLargeResp()))
    with pytest.raises(AuthError, match="implausibly large"):
        auth._fetch_json("https://valid.example.com")

    class FakeInvalidJsonResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n):
            return b"not json {"

    monkeypatch.setattr(auth, "_OPENER", FakeOpener(lambda req, timeout: FakeInvalidJsonResp()))
    with pytest.raises(AuthError, match="not valid JSON"):
        auth._fetch_json("https://valid.example.com")


def test_decode_jwt_malformed_components():
    cache = JWKSCache(ISSUER, opener=FakeIssuer())

    # Not json
    with pytest.raises(AuthError, match="token header or payload is not JSON"):
        decode_jwt(f"{b64u(b'not json')}.{b64u(b'{}')}.sig", cache, ISSUER, None)

    # Not object (e.g. integer)
    with pytest.raises(AuthError, match="token header or payload is not an object"):
        decode_jwt(f"{b64u(b'123')}.{b64u(b'{}')}.sig", cache, ISSUER, None)

    with pytest.raises(AuthError, match="token header or payload is not an object"):
        decode_jwt(f"{b64u(b'{}')}.{b64u(b'[1, 2]')}.sig", cache, ISSUER, None)

    # Missing kid
    h_nokid = b64u(json.dumps({"alg": "RS256"}).encode("utf8"))
    with pytest.raises(AuthError, match="token header has no kid"):
        decode_jwt(f"{h_nokid}.{b64u(b'{}')}.sig", cache, ISSUER, None)

    # Non-RSA kty
    h_ec = b64u(json.dumps({"alg": "RS256", "kid": "ec-key"}).encode("utf8"))
    p = b64u(json.dumps({"iss": ISSUER, "exp": time.time() + 100}).encode("utf8"))
    cache._keys["ec-key"] = {"kid": "ec-key", "kty": "EC"}
    cache._fetched_at = time.monotonic()
    with pytest.raises(AuthError, match="unsupported key type"):
        decode_jwt(f"{h_ec}.{p}.sig", cache, ISSUER, None)

    # Non-numeric exp
    token_badexp = sign(claims(exp="not-a-number"))
    with pytest.raises(AuthError, match="token exp claim is not a number"):
        decode_jwt(token_badexp, cache, ISSUER, None)

    # Non-numeric nbf
    token_badnbf = sign(claims(nbf="not-a-number"))
    with pytest.raises(AuthError, match="token nbf claim is not a number"):
        decode_jwt(token_badnbf, cache, ISSUER, None)


def test_authenticator_unconfigured_refusal():
    who = Authenticator(AuthConfig(token="secret"))
    who.config.oidc_issuer = "https://example.com"
    who._jwks = None
    with pytest.raises(AuthError, match="invalid bearer token"):
        who.authenticate("Bearer nonmatching")

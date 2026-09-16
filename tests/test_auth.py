# @authormark v1 -- do not remove (authorship watermark)
# Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
# Author: https://github.com/Srinivasan-78
# SPDX-License-Identifier: MIT
# Fingerprint: AMK1.Aqu0WfjVygD5wUWGsayoAh
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
import json
import random
import time

import pytest

from repo2graph import auth
from repo2graph.auth import (AuthConfig, AuthError, Authenticator, JWKSCache,
                             client_metadata_document, decode_jwt, rsa_verify)

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
    return {"keys": [{"kty": "RSA", "kid": kid, "alg": alg,
                      "n": int_b64u(key["n"]), "e": int_b64u(key["e"])}]}


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
    base = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-42",
            "exp": time.time() + 600}
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


def cache(issuer=None, ttl=300.0, clock=None):
    issuer = issuer or FakeIssuer()
    return (JWKSCache(ISSUER, ttl, opener=issuer, clock=clock or Clock()),
            issuer)


# ----------------------------------------------------------------- rsa ----

def test_rsa_verify_accepts_a_real_signature():
    token = sign(claims())
    head, payload, sig = token.split(".")
    assert rsa_verify(KEY["n"], KEY["e"], auth.b64url_decode(sig),
                      f"{head}.{payload}".encode(), "sha256")


def test_rsa_verify_rejects_a_tampered_message():
    token = sign(claims())
    head, payload, sig = token.split(".")
    assert not rsa_verify(KEY["n"], KEY["e"], auth.b64url_decode(sig),
                          f"{head}.{payload}x".encode(), "sha256")


def test_rsa_verify_rejects_a_wrong_length_signature():
    assert not rsa_verify(KEY["n"], KEY["e"], b"\x01\x02", b"msg", "sha256")


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


def test_the_wrong_issuer_is_refused():
    jwks, _ = cache()
    with pytest.raises(AuthError, match="issuer"):
        decode_jwt(sign(claims(iss="https://evil.example.com")),
                   jwks, ISSUER, AUDIENCE)


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

    clock.advance(299)                      # still inside the TTL
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 1, "refetched too early"

    clock.advance(2)                        # now past it
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 2


def test_a_zero_ttl_never_caches():
    """`ttl=0` means "do not cache", whatever the clock's resolution.

    With a strict `>` comparison this promise silently depended on the clock
    ticking between calls, which is exactly the flake above in its other form:
    a configuration that says "never cache" would keep serving stale keys.
    """
    frozen = Clock()                        # never advances
    jwks, issuer = cache(ttl=0.0, clock=frozen)

    for _ in range(3):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == 3, issuer.calls


def test_an_unknown_kid_refetches_exactly_once_then_fails():
    """Key rotation costs one refetch; a kid flood must not cost one each."""
    jwks, issuer = cache()
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)
    before = issuer.calls.count(f"{ISSUER}/jwks")

    with pytest.raises(AuthError, match="unknown signing key"):
        decode_jwt(sign(claims(), kid="rotated-in"), jwks, ISSUER, AUDIENCE)
    assert issuer.calls.count(f"{ISSUER}/jwks") == before + 1


def test_a_rotated_key_is_picked_up_on_the_refetch():
    issuer = FakeIssuer()
    jwks = JWKSCache(ISSUER, 300.0, opener=issuer)
    decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)

    new_key = _make_key(seed=4242)
    issuer.jwks = jwks_doc(kid="kid-2", key=new_key)
    got = decode_jwt(sign(claims(), kid="kid-2", key=new_key),
                     jwks, ISSUER, AUDIENCE)
    assert got["sub"] == "user-42"


def test_a_discovery_document_declaring_another_issuer_is_refused():
    issuer = FakeIssuer(issuer=ISSUER)

    def lying(url):
        if url.endswith("openid-configuration"):
            return {"issuer": "https://evil.example.com",
                    "jwks_uri": "https://evil.example.com/jwks"}
        return issuer(url)

    jwks = JWKSCache(ISSUER, 300.0, opener=lying)
    with pytest.raises(AuthError, match="issuer mismatch"):
        decode_jwt(sign(claims()), jwks, ISSUER, AUDIENCE)


def test_plain_http_issuers_are_refused(monkeypatch):
    """Fetching signing keys over http would put them on the wire in clear."""
    with pytest.raises(AuthError, match="must be https"):
        auth._fetch_json("http://issuer.example.com/.well-known/openid-configuration")


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


@pytest.mark.parametrize("header", [
    None, "", "s3cret", "Basic s3cret", "Bearer", "Bearer ", "Bearer wrong",
])
def test_a_bad_static_token_is_refused(header):
    with pytest.raises(AuthError):
        Authenticator(AuthConfig(token="s3cret")).authenticate(header)


def test_the_static_token_comparison_is_constant_time():
    """A `==` here would leak the secret's length and prefix through timing."""
    import inspect
    src = inspect.getsource(Authenticator.authenticate)
    assert "compare_digest" in src
    assert "credential == self.config.token" not in src


def test_an_oidc_token_yields_the_sub_claim():
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
                        opener=issuer).authenticate("Bearer " + sign(claims()))
    assert who.mode == "oidc" and who.subject == "user-42"


def test_an_expired_oidc_token_is_refused():
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
                        opener=issuer)
    # Comfortably past CLOCK_SKEW: a token only seconds stale is deliberately
    # still accepted, because host clocks disagree by more than that routinely.
    with pytest.raises(AuthError, match="expired"):
        who.authenticate("Bearer " + sign(claims(exp=time.time() - 3600)))


def test_a_token_just_inside_the_skew_window_is_still_accepted():
    """Clock skew tolerance is deliberate, so pin it rather than discover it."""
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
                        opener=issuer)
    assert who.authenticate(
        "Bearer " + sign(claims(exp=time.time() - 5))).mode == "oidc"


def test_a_wrong_issuer_oidc_token_is_refused():
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
                        opener=issuer)
    with pytest.raises(AuthError, match="issuer"):
        who.authenticate("Bearer " + sign(claims(iss="https://evil.example.com")))


def test_both_modes_together_accept_either_credential():
    issuer = FakeIssuer()
    who = Authenticator(AuthConfig(token="s3cret", oidc_issuer=ISSUER,
                                   audience=AUDIENCE), opener=issuer)
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
    assert doc["client_id"] == \
        "https://example.test:8719/.well-known/oauth-client-metadata"
    for field in ("client_name", "grant_types", "response_types",
                  "token_endpoint_auth_method", "redirect_uris"):
        assert doc[field], field
    assert "authorization_code" in doc["grant_types"]
    json.dumps(doc)      # must be serialisable as-is

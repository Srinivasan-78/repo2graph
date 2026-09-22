"""Targeted negative and mutation tests for security-critical modules (Issue #317).

Verifies that security checks cannot be bypassed or inverted across:
- Token verification (issuer, audience, expiration, not-before, algorithm allowlist, RSA degeneration)
- Path containment and symlink validation
- MCP output argument clamping and bounds enforcement
- Secret filtering and credential isolation
- Build lock acquisition and active process PID validation
"""

import json
import os
from pathlib import Path
import time
import pytest

from repo2graph.auth import (
    AuthConfig,
    AuthError,
    Authenticator,
    decode_jwt,
    rsa_verify,
)
from repo2graph.secrets import _is_secret_path
from repo2graph.integrity import validate_outdir
from repo2graph.lock import BuildLock, LockTimeoutError, _is_pid_alive
from repo2graph.mcp import _clamp, MCP_MAX_HOPS, MCP_MAX_K, MCP_MAX_NEIGHBOURS
from test_auth import (
    AUDIENCE,
    ISSUER,
    KEY,
    KID,
    FakeIssuer,
    b64u,
    cache,
    claims,
    sign,
)


# ==============================================================================
# 1. Authentication Security Checks (JWT & JWK)
# ==============================================================================


def test_mutation_issuer_check_refuses_mismatched_issuer():
    """Mutating or spoofing the 'iss' claim must always raise AuthError."""
    fake_issuer = FakeIssuer()
    authenticator = Authenticator(
        AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
        opener=fake_issuer,
    )

    # Valid token works
    valid_token = sign(claims(iss=ISSUER))
    ident = authenticator.authenticate(f"Bearer {valid_token}")
    assert ident.subject == "user-42"

    # Mismatched/spoofed issuer must fail
    spoofed_token = sign(claims(iss="https://attacker-issuer.example.com"))
    with pytest.raises(AuthError, match=r"token issuer .* is not"):
        authenticator.authenticate(f"Bearer {spoofed_token}")


def test_mutation_audience_check_refuses_mismatched_audience():
    """Mutating or omitting the expected 'aud' claim must always raise AuthError."""
    fake_issuer = FakeIssuer()
    authenticator = Authenticator(
        AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
        opener=fake_issuer,
    )

    # Mismatched audience
    wrong_aud_token = sign(claims(aud="other-service"))
    with pytest.raises(AuthError, match=r"token audience .* does not include"):
        authenticator.authenticate(f"Bearer {wrong_aud_token}")

    # Completely missing audience claim
    no_aud_claims = claims()
    no_aud_claims.pop("aud")
    no_aud_token = sign(no_aud_claims)
    with pytest.raises(AuthError, match=r"token audience None does not include"):
        authenticator.authenticate(f"Bearer {no_aud_token}")


def test_mutation_expiration_check_refuses_expired_token():
    """Tokens with past expiration timestamps must be rejected."""
    fake_issuer = FakeIssuer()
    authenticator = Authenticator(
        AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
        opener=fake_issuer,
    )

    # Expired token (exp in past relative to now)
    expired_token = sign(claims(exp=time.time() - 3600))
    with pytest.raises(AuthError, match="token has expired"):
        authenticator.authenticate(f"Bearer {expired_token}")


def test_mutation_not_before_check_refuses_future_token():
    """Tokens with future 'nbf' timestamps must be rejected."""
    fake_issuer = FakeIssuer()
    authenticator = Authenticator(
        AuthConfig(oidc_issuer=ISSUER, audience=AUDIENCE),
        opener=fake_issuer,
    )

    # Future token (nbf ahead of now)
    future_token = sign(claims(nbf=time.time() + 3600, exp=time.time() + 7200))
    with pytest.raises(AuthError, match="token is not valid yet"):
        authenticator.authenticate(f"Bearer {future_token}")


def test_mutation_algorithm_allowlist_rejects_insecure_algorithms():
    """Non-RS256 algorithms ('none', symmetric HS256, etc.) must be strictly rejected."""
    jwks, _ = cache()

    # Algorithm 'none' attack
    header_none = b64u(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = b64u(json.dumps(claims()).encode())
    token_none = f"{header_none}.{payload}."
    with pytest.raises(AuthError, match="unsupported token algorithm"):
        decode_jwt(token_none, jwks, ISSUER, AUDIENCE)

    # Algorithm 'HS256' confusion attack
    header_hs = b64u(json.dumps({"alg": "HS256", "kid": KID, "typ": "JWT"}).encode())
    token_hs = f"{header_hs}.{payload}.AAAA"
    with pytest.raises(AuthError, match="unsupported token algorithm"):
        decode_jwt(token_hs, jwks, ISSUER, AUDIENCE)

    # Unsupported asymmetric algorithms (ES256)
    header_es = b64u(json.dumps({"alg": "ES256", "kid": KID, "typ": "JWT"}).encode())
    token_es = f"{header_es}.{payload}.AAAA"
    with pytest.raises(AuthError, match="unsupported token algorithm"):
        decode_jwt(token_es, jwks, ISSUER, AUDIENCE)


def test_mutation_rsa_verify_rejects_degenerate_keys():
    """Degenerate public keys (e <= 1, zero/negative modulus) must not verify."""
    msg = b"test message"
    sig = b"\x01" * 128

    # e = 1 (trivial exponent)
    assert not rsa_verify(KEY["n"], 1, sig, msg, "sha256")

    # e = 0
    assert not rsa_verify(KEY["n"], 0, sig, msg, "sha256")

    # negative e
    assert not rsa_verify(KEY["n"], -3, sig, msg, "sha256")

    # n <= 0
    assert not rsa_verify(-KEY["n"], 65537, sig, msg, "sha256")
    assert not rsa_verify(0, 65537, sig, msg, "sha256")


# ==============================================================================
# 2. Path Containment and Output Directory Validation
# ==============================================================================


def test_mutation_validate_outdir_refuses_unsafe_symlinks(tmp_path: Path):
    """Output directory validation must refuse symlinks when allow_symlink=False."""
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    symlink_dir = tmp_path / "symlink_dir"

    try:
        symlink_dir.symlink_to(real_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/privilege level")

    # Refuse symlink target without allow_symlink
    with pytest.raises(ValueError, match=r"[Ss]ymlink"):
        validate_outdir(symlink_dir, allow_symlink=False)

    # Allowed when explicitly enabled
    resolved = validate_outdir(symlink_dir, allow_symlink=True)
    assert resolved == real_dir.resolve()


# ==============================================================================
# 3. MCP Argument Clamping & Output Caps
# ==============================================================================


def test_mutation_mcp_output_caps_enforce_hard_ceilings():
    """Caller-supplied numeric arguments must never exceed MCP_MAX bounds."""
    # Enormous values must clamp to ceiling
    assert _clamp(1_000_000, 8, 1, MCP_MAX_K) == MCP_MAX_K
    assert _clamp(999, 1, 1, MCP_MAX_HOPS) == MCP_MAX_HOPS
    assert _clamp(500_000, 50, 1, MCP_MAX_NEIGHBOURS) == MCP_MAX_NEIGHBOURS

    # Negative values must clamp to floor of 1
    assert _clamp(-100, 8, 1, MCP_MAX_K) == 1
    assert _clamp(0, 8, 1, MCP_MAX_K) == 1

    # Normal in-bounds values are preserved
    assert _clamp(5, 8, 1, MCP_MAX_K) == 5
    assert _clamp(2, 1, 1, MCP_MAX_HOPS) == 2


# ==============================================================================
# 4. Secret Filtering Hardening
# ==============================================================================


def test_mutation_secret_filter_identifies_credentials():
    """Credential and secret files must always match secret detection patterns."""
    sensitive_paths = [
        ".env",
        ".env.local",
        ".env.production",
        "secrets/id_rsa",
        "keys/private.key",
        "certs/server.pem",
        "credentials.json",
        "auth_token.txt",
    ]
    for p in sensitive_paths:
        assert _is_secret_path(p), f"Path '{p}' should have been flagged as secret"

    # Benign source paths must not be flagged
    safe_paths = [
        "src/main.py",
        "pkg/utils.go",
        "README.md",
        "tests/test_auth.py",
    ]
    for p in safe_paths:
        assert not _is_secret_path(p), f"Path '{p}' should not be flagged as secret"


# ==============================================================================
# 5. Lock Validation & Reclaim Safety
# ==============================================================================


def test_mutation_is_pid_alive_validation():
    """PID liveness checks must accurately distinguish active from nonexistent processes."""
    current_pid = os.getpid()
    assert _is_pid_alive(current_pid)

    # Negative PID or 0 cannot be active lock owners
    assert not _is_pid_alive(-1)
    assert not _is_pid_alive(0)


def test_mutation_active_lock_cannot_be_stolen(tmp_path: Path):
    """An active build lock held by a live process must not be acquired or overwritten."""
    lock1 = BuildLock(tmp_path, timeout=0.1)
    lock1.acquire()

    try:
        # A second attempt while lock1 is held must fail with LockTimeoutError
        lock2 = BuildLock(tmp_path, timeout=0.2)
        with pytest.raises(LockTimeoutError):
            lock2.acquire()
    finally:
        lock1.release()


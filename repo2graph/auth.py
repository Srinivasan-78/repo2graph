"""Bearer-token and OIDC authentication for the HTTP MCP transport.

**This module is for the HTTP transport only.** Authenticating the stdio
transport is not a thing that exists: a stdio server is spawned as a child
process by its client and speaks over an anonymous pipe, so there are no
headers to carry a token and anyone able to write to the pipe already has the
parent process's privileges. The MCP auth specification scopes OAuth 2.0 to
HTTP transports for exactly this reason. `repo2graph-mcp` therefore grew a real
HTTP transport rather than a token check that would have looked like security
without being any.

Everything here is stdlib. RS256 signature verification is the interesting part
of that claim: an RSA PKCS#1 v1.5 verify is `sig**e mod n` followed by a padding
and digest comparison, and Python's integers are arbitrary-precision, so the
whole operation is a `pow()` and a handful of byte comparisons. Pulling in
`pyjwt` and `cryptography` -- two packages, one of them with a compiled
extension -- to avoid sixty lines would have broken the zero-dependency promise
for every user who never turns authentication on.

Security properties this module is responsible for, none of them optional:

* Token comparison is constant time (`hmac.compare_digest`). A `==` on a secret
  leaks its length and prefix to anyone who can time the response.
* `alg` comes from the *key*, never from the token. A token claiming
  `{"alg": "none"}` or `{"alg": "HS256"}` against an RSA JWKS is the classic
  JWT forgery, and the only defence is refusing to let the attacker pick the
  algorithm.
* `iss`, `aud` and `exp` are all enforced. A signature check alone proves the
  issuer minted *a* token, not that it minted one for this server.
* A `kid` miss triggers at most one JWKS refetch, then fails. Without the cap,
  a stream of tokens carrying random `kid`s is a free amplification attack
  against the issuer.
"""
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

# How long a fetched JWKS is trusted before it is re-read.
DEFAULT_JWKS_TTL = 300.0
# Network timeout for discovery and JWKS fetches, in seconds.
HTTP_TIMEOUT = 10.0
# A JWKS is a small JSON document; anything larger is not one, and reading it
# unbounded would let a hostile issuer exhaust memory.
MAX_JWKS_BYTES = 1 << 20
# Tolerance for clock skew between this host and the issuer, in seconds.
CLOCK_SKEW = 60.0

# The DER prefix of a PKCS#1 v1.5 DigestInfo for each supported hash. The
# verifier rebuilds the whole expected padded block and compares it whole, so
# these are data, not parsing.
DIGEST_INFO_PREFIX = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}
# Signing algorithms this server will accept, and the hash each one uses. RSA
# only: an HMAC algorithm in this table would let a token signed with the
# *public* key validate, which is the other classic JWT forgery.
ALGORITHMS = {"RS256": "sha256", "RS384": "sha384", "RS512": "sha512"}


class AuthError(Exception):
    """A request that must be refused, carrying the status to refuse it with.

    Args:
        message: Human-readable reason, safe to log. Never contains the token.
        status: HTTP status to return; 401 unless the caller says otherwise.
    """

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Identity:
    """Who made a request, as far as the server can tell.

    Attributes:
        subject: The token's `sub` claim under OIDC, "anonymous" with no auth
            configured, or "bearer" for a shared static token, which by
            construction identifies no one in particular.
        mode: Which authentication mode admitted this request.
        claims: The validated JWT claims, empty for the other two modes.
    """

    subject: str = "anonymous"
    mode: str = "none"
    claims: dict[str, Any] = field(default_factory=dict)


def b64url_decode(text: str) -> bytes:
    """Decode base64url without padding, as every JWT field is encoded.

    Args:
        text: The base64url segment.

    Returns:
        The decoded bytes.

    Raises:
        AuthError: If the segment is not valid base64url.
    """
    import base64
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception:
        raise AuthError("malformed token encoding") from None


def _int_from_b64url(text: str) -> int:
    return int.from_bytes(b64url_decode(text), "big")


def rsa_verify(n: int, e: int, signature: bytes, message: bytes, hash_name: str) -> bool:
    """Verify an RSA PKCS#1 v1.5 signature using only stdlib arithmetic.

    Rebuilds the complete EMSA-PKCS1-v1_5 encoded block that a correct signer
    would have produced and compares it to what `sig**e mod n` yields. Comparing
    the whole block rather than parsing it out is what makes this immune to the
    Bleichenbacher '06 forgery class, where a verifier that *scans* for the
    digest accepts garbage in the padding it skipped over.

    Args:
        n: RSA modulus.
        e: RSA public exponent.
        signature: The raw signature bytes.
        message: The signed bytes (`header.payload` of the JWT).
        hash_name: One of "sha256", "sha384", "sha512".

    Returns:
        True if the signature is valid for this key and message.
    """
    prefix = DIGEST_INFO_PREFIX.get(hash_name)
    if prefix is None:
        return False
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    decoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")

    digest = hashlib.new(hash_name, message).digest()
    tail = prefix + digest
    # EM = 0x00 || 0x01 || PS (0xff...) || 0x00 || DigestInfo
    if k < len(tail) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(tail) - 3) + b"\x00" + tail
    return hmac.compare_digest(decoded, expected)


class JWKSCache:
    """Fetches and caches an issuer's signing keys, with a bounded refetch.

    Args:
        issuer: The OIDC issuer URL.
        ttl: Seconds a fetched key set is trusted before it is re-read.
        opener: Callable taking a URL and returning decoded JSON. Injected so
            tests never touch the network; defaults to a urllib fetch.
        clock: Monotonic time source, injected so a test can age the cache
            without sleeping. `ResultCache` takes one for the same reason: a
            test that waits for real time to pass is a test that fails on
            somebody else's machine.
    """

    def __init__(self, issuer: str, ttl: float = DEFAULT_JWKS_TTL,
                 opener: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.issuer = issuer.rstrip("/")
        self.ttl = ttl
        self._open = opener or _fetch_json
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at = 0.0
        self._jwks_uri: str | None = None

    def discovery_url(self) -> str:
        """The OIDC discovery document URL for this issuer."""
        return f"{self.issuer}/.well-known/openid-configuration"

    def _resolve_jwks_uri(self) -> str:
        if self._jwks_uri:
            return self._jwks_uri
        doc = self._open(self.discovery_url())
        uri = doc.get("jwks_uri") if isinstance(doc, dict) else None
        if not isinstance(uri, str) or not uri:
            raise AuthError("issuer discovery document has no jwks_uri")
        # The issuer in the discovery document must be the issuer we asked for,
        # or a compromised DNS answer could point us at someone else's keys.
        declared = str(doc.get("issuer") or "").rstrip("/")
        if declared and declared != self.issuer:
            raise AuthError(
                f"issuer mismatch: asked {self.issuer!r}, document declares {declared!r}")
        self._jwks_uri = uri
        return uri

    def _refresh(self) -> None:
        doc = self._open(self._resolve_jwks_uri())
        keys = doc.get("keys") if isinstance(doc, dict) else None
        if not isinstance(keys, list):
            raise AuthError("issuer JWKS has no key list")
        self._keys = {str(k.get("kid")): k for k in keys
                      if isinstance(k, dict) and k.get("kid")}
        self._fetched_at = self._clock()

    def key_for(self, kid: str) -> dict[str, Any]:
        """Return the JWK with this `kid`, refetching at most once on a miss.

        Args:
            kid: The key id from the token header.

        Returns:
            The matching JWK as a dict.

        Raises:
            AuthError: If the key is unknown even after one refetch.
        """
        with self._lock:
            # >=, not >: `ttl=0` means "do not cache this at all", and with a
            # strict > that promise depends on the clock's resolution rather
            # than on the configuration. Two calls inside one tick of a coarse
            # monotonic clock -- Windows can fall back to GetTickCount64, at
            # ~15.6ms -- read an elapsed time of exactly 0.0 and would keep
            # serving keys a ttl of 0 said to discard.
            stale = (self._clock() - self._fetched_at) >= self.ttl
            if not self._keys or stale:
                self._refresh()
            key = self._keys.get(kid)
            if key is None:
                # A kid we have never seen is the signal for key rotation. One
                # refetch, then a refusal: without the cap, tokens carrying
                # random kids would turn this server into a traffic amplifier
                # pointed at the issuer.
                self._refresh()
                key = self._keys.get(kid)
            if key is None:
                raise AuthError(f"unknown signing key {kid!r}")
            return key


def _fetch_json(url: str) -> Any:
    """GET `url` and parse the JSON body. The only network call in this package.

    Args:
        url: An https URL to fetch.

    Returns:
        The parsed JSON body.

    Raises:
        AuthError: On a non-https URL, a transport failure, or a bad body.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        # http:// would put bearer-token validation keys on the wire in clear,
        # and localhost is not an exception worth the branch.
        raise AuthError(f"issuer URLs must be https, got {parts.scheme!r}")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read(MAX_JWKS_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AuthError(f"could not reach the issuer: {exc}") from None
    if len(raw) > MAX_JWKS_BYTES:
        raise AuthError("issuer document is implausibly large")
    try:
        return json.loads(raw.decode("utf8", "replace"))
    except ValueError:
        raise AuthError("issuer document is not valid JSON") from None


def decode_jwt(token: str, jwks: JWKSCache, issuer: str,
               audience: str | None) -> dict[str, Any]:
    """Validate a JWT's signature and claims, returning them.

    Args:
        token: The compact-serialised JWT.
        jwks: Key source for the issuer.
        issuer: The expected `iss` value.
        audience: The expected `aud` value, or None to skip the check.

    Returns:
        The validated claims.

    Raises:
        AuthError: On any structural, signature or claim failure.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("token is not a JWT")
    head_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(b64url_decode(head_b64))
        claims = json.loads(b64url_decode(payload_b64))
    except ValueError:
        raise AuthError("token header or payload is not JSON") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise AuthError("token header or payload is not an object")

    alg = header.get("alg")
    hash_name = ALGORITHMS.get(str(alg))
    if hash_name is None:
        # Covers alg:none and every HMAC algorithm. Accepting HS256 against an
        # RSA JWKS would let anyone sign a token with the *public* key.
        raise AuthError(f"unsupported token algorithm {alg!r}")
    kid = header.get("kid")
    if not kid:
        raise AuthError("token header has no kid")

    key = jwks.key_for(str(kid))
    if key.get("kty") != "RSA":
        raise AuthError(f"unsupported key type {key.get('kty')!r}")
    # The algorithm is taken from the key when the key declares one, never from
    # the token: the attacker controls the token and must not choose the maths.
    if key.get("alg") and ALGORITHMS.get(str(key["alg"])) != hash_name:
        raise AuthError("token algorithm does not match the signing key")

    signing_input = f"{head_b64}.{payload_b64}".encode("ascii", "strict")
    if not rsa_verify(_int_from_b64url(str(key["n"])),
                      _int_from_b64url(str(key["e"])),
                      b64url_decode(sig_b64), signing_input, hash_name):
        raise AuthError("token signature is invalid")

    _check_claims(claims, issuer, audience)
    return claims


def _check_claims(claims: dict[str, Any], issuer: str,
                  audience: str | None) -> None:
    """Enforce iss, aud, exp and nbf. A valid signature is not a valid token."""
    now = time.time()
    if str(claims.get("iss") or "").rstrip("/") != issuer.rstrip("/"):
        raise AuthError(f"token issuer {claims.get('iss')!r} is not {issuer!r}")

    exp = claims.get("exp")
    if exp is None:
        raise AuthError("token has no exp claim")
    try:
        if float(exp) + CLOCK_SKEW < now:
            raise AuthError("token has expired")
    except (TypeError, ValueError):
        raise AuthError("token exp claim is not a number") from None

    nbf = claims.get("nbf")
    if nbf is not None:
        try:
            if float(nbf) - CLOCK_SKEW > now:
                raise AuthError("token is not valid yet")
        except (TypeError, ValueError):
            raise AuthError("token nbf claim is not a number") from None

    if audience is not None:
        aud = claims.get("aud")
        allowed = aud if isinstance(aud, list) else [aud]
        if audience not in [str(a) for a in allowed if a is not None]:
            raise AuthError(f"token audience {aud!r} does not include {audience!r}")


@dataclass
class AuthConfig:
    """How the server authenticates requests.

    Attributes:
        token: A shared bearer token, or None.
        oidc_issuer: An OIDC issuer URL, or None.
        audience: Expected `aud` for OIDC tokens, or None to skip the check.
        jwks_ttl: Seconds a fetched JWKS is trusted.
        cimd: Whether to publish a Client ID Metadata Document.
    """

    token: str | None = None
    oidc_issuer: str | None = None
    audience: str | None = None
    jwks_ttl: float = DEFAULT_JWKS_TTL
    cimd: bool = False

    @property
    def modes(self) -> list[str]:
        """The auth modes in force, for the discovery document."""
        out = []
        if self.token:
            out.append("bearer")
        if self.oidc_issuer:
            out.append("oidc")
        return out or ["none"]

    @property
    def enabled(self) -> bool:
        """True when any credential is required."""
        return bool(self.token or self.oidc_issuer)


class Authenticator:
    """Turns an Authorization header into an Identity, or refuses it.

    Args:
        config: The configured modes.
        opener: JSON fetcher for OIDC discovery, injected for tests.
        clock: Monotonic time source for the JWKS cache, injected for tests.
    """

    def __init__(self, config: AuthConfig,
                 opener: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._jwks = (JWKSCache(config.oidc_issuer, config.jwks_ttl, opener,
                                clock)
                      if config.oidc_issuer else None)

    def authenticate(self, header: str | None) -> Identity:
        """Validate one Authorization header.

        Args:
            header: The raw header value, or None when absent.

        Returns:
            The caller's Identity. With no auth configured this is always the
            anonymous identity and the header is ignored entirely.

        Raises:
            AuthError: When a credential is required and this one does not pass.
        """
        if not self.config.enabled:
            return Identity()
        scheme, _, credential = (header or "").partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            raise AuthError("missing or malformed Authorization: Bearer header")
        credential = credential.strip()

        if self.config.token:
            # compare_digest, not ==: an equality test on a secret leaks its
            # length and matching prefix through response timing.
            if hmac.compare_digest(credential, self.config.token):
                return Identity(subject="bearer", mode="bearer")
            # Fall through rather than refusing: both modes may be configured,
            # and a token that is not the static one may still be a valid JWT.
            if not self.config.oidc_issuer:
                raise AuthError("invalid bearer token")

        if self._jwks is not None and self.config.oidc_issuer:
            claims = decode_jwt(credential, self._jwks,
                                self.config.oidc_issuer, self.config.audience)
            subject = str(claims.get("sub") or "unknown")
            return Identity(subject=subject, mode="oidc", claims=claims)
        raise AuthError("invalid bearer token")


def client_metadata_document(base_url: str,
                             name: str = "repo2graph") -> dict[str, Any]:
    """A Client ID Metadata Document, as RFC 7591 client metadata.

    CIMD serves this at a URL and uses that URL as the `client_id`, so the
    document must be reachable at the id it advertises.

    Args:
        base_url: The externally reachable base URL of this server.
        name: Human-readable client name.

    Returns:
        The metadata document.
    """
    base = base_url.rstrip("/")
    return {
        "client_id": f"{base}/.well-known/oauth-client-metadata",
        "client_name": name,
        "client_uri": "https://github.com/Srinivasan-78/repo2graph",
        "software_id": "io.github.Srinivasan-78/repo2graph",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "openid profile",
        "redirect_uris": [f"{base}/oauth/callback"],
        "application_type": "native",
    }

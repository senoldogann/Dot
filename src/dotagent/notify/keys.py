"""The keys of a browser's web push subscription (item 52): what a message to it is encrypted with. They are checked
where a subscription comes in and before each send, so a subscription whose keys cannot encrypt anything is refused
or dropped instead of failing every push after it. Kept apart from :mod:`dotagent.notify.push`, whose sender pulls in
HTTP clients the server's request models do not need."""

import base64
import binascii
import re

from cryptography.hazmat.primitives.asymmetric import ec

_BASE64URL: re.Pattern[str] = re.compile(r"[A-Za-z0-9_-]+={0,2}")
# An uncompressed point of P-256 (RFC 8291 section 3.1) and the authentication secret (section 3.2).
_POINT_BYTES: int = 65
_AUTH_BYTES: int = 16


def _base64url(value: str) -> bytes | None:
    """The bytes ``value`` holds in URL-safe base64, padded or not, or None when it is something else."""
    if _BASE64URL.fullmatch(value) is None:
        return None
    bare: str = value.rstrip("=")
    try:
        return base64.urlsafe_b64decode(bare + "=" * (-len(bare) % 4))
    except binascii.Error:
        return None


def keys_problem(p256dh: str, auth: str) -> str | None:
    """What keeps ``p256dh`` and ``auth`` from encrypting a message, or None when they can: as the Push API gives them,
    ``p256dh`` is a public key on P-256 (65 bytes, uncompressed) and ``auth`` 16 bytes, both in URL-safe base64."""
    point: bytes | None = _base64url(p256dh)
    if point is None or len(point) != _POINT_BYTES:
        return f"p256dh is not {_POINT_BYTES} bytes in URL-safe base64"
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError:
        return "p256dh is not a point of P-256"
    secret: bytes | None = _base64url(auth)
    if secret is None or len(secret) != _AUTH_BYTES:
        return f"auth is not {_AUTH_BYTES} bytes in URL-safe base64"
    return None

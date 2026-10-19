"""PKCE (RFC 7636) and the state of a sign-in: a fresh verifier, its S256 challenge, and a state that cannot be
guessed."""

import base64
import hashlib
import secrets


def new_verifier() -> str:
    """A code verifier: 86 characters of the URL-safe alphabet (RFC 7636 asks for 43 to 128)."""
    return secrets.token_urlsafe(64)


def challenge_of(verifier: str) -> str:
    """The S256 challenge of ``verifier``: its SHA-256, base64url without padding."""
    digest: bytes = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(24)

"""Pushes to Dot's iPhone app through APNs (item 997, docs/phone.md): the alerts the web app's Web Push gives, and the
Live Activity of a run at work on the lock screen and in the Dynamic Island.

The daemon signs with the APNs key of ``[apns]`` (a .p8 file kept outside Dot's home, which no run reads), whose
token it makes anew every 50 minutes (APNs refuses one older than an hour and one made too often). The key is read
when the sender is made and lives in this process alone: it is never logged, returned or written anywhere.

Apple reads what APNs carries, where Web Push is encrypted end to end: an alert carries its title alone (``Open Dot to
see it``), and a Live Activity the first 40 characters of its chat's title, which is what makes it worth seeing.

The pure parts (the payloads, the token's claims) are apart from the one that talks to Apple (:class:`ApnsSender` and
its :class:`ApnsTransport`), which the tests replace."""

import asyncio
import base64
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from pydantic import BaseModel, ValidationError

from dotagent.config import ApnsConfig
from dotagent.core.errors import ConfigError, DotError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.models import ApnsEnvironment

log = get_logger(__name__)

HOSTS: dict[ApnsEnvironment, str] = {
    "sandbox": "https://api.sandbox.push.apple.com",
    "production": "https://api.push.apple.com",
}
# How long a provider token is used before a new one is made: APNs refuses one older than an hour.
TOKEN_EVERY_S: float = 50 * 60.0
# What an alert says under its title: nothing of what happened, which Apple would read.
ALERT_BODY: str = "Open Dot to see it."
# What the banner of a Live Activity's start says: the chat's title is in the activity alone.
LIVE_START_BODY: str = "A task started"
# How much of a chat's title a Live Activity shows.
TITLE_CHARS: int = 40
# How long a Live Activity of a run whose end never came stays before iOS marks it stale.
STALE_AFTER_S: float = 4 * 3600.0
# How long a run's ended Live Activity stays on the lock screen.
DISMISS_AFTER_S: float = 15 * 60.0
# What APNs answers about a token that is no use any more: the app was removed, or the token is of another topic or
# environment. The device is forgotten.
_GONE_REASONS: frozenset[str] = frozenset({"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"})
_GONE_STATUS: int = 410
_JWT_PARTS: int = 32

type LiveStatus = Literal["working", "waiting", "done", "failed"]
type PushType = Literal["alert", "liveactivity"]


class LiveState(FrozenModel):
    """What a Live Activity of a run shows, as the app's ``DotRunAttributes.ContentState`` reads it."""

    title: str
    status: LiveStatus
    line: str
    startedAt: float


class ApnsReply(FrozenModel):
    """What APNs answered: its status and, when it refused, its reason (``BadDeviceToken``)."""

    status: int
    reason: str


class ApnsRefusal(BaseModel):
    """What APNs answers when it refuses a push: ``{"reason": "BadDeviceToken"}`` (and, for a gone token, when)."""

    reason: str


class ApnsError(DotError):
    """APNs refused a push, or could not be reached."""


class ApnsTransport(Protocol):
    """Where a push goes: APNs over HTTP/2, or a stand-in in the tests."""

    async def post(self, url: str, headers: Mapping[str, str], body: bytes) -> ApnsReply: ...


def gone(reply: ApnsReply) -> bool:
    """Whether APNs said the token is no use any more."""
    return reply.status == _GONE_STATUS or reply.reason in _GONE_REASONS


def short_title(title: str) -> str:
    """A chat's title as a Live Activity shows it: one line, at most :data:`TITLE_CHARS` characters."""
    line: str = " ".join(title.split())
    return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rstrip() + "…"


def alert_payload(title: str, route: str) -> JsonObject:
    """An alert: its title, the line that says to open Dot, and the route a tap opens."""
    return {"aps": {"alert": {"title": title, "body": ALERT_BODY}, "sound": "default"}, "route": route}


def live_start_payload(run_id: str, agent_name: str, state: LiveState, now: float) -> JsonObject:
    """The push that starts a run's Live Activity (push-to-start), with the alert iOS needs to show it."""
    return {
        "aps": {
            "timestamp": int(now),
            "event": "start",
            "content-state": state.model_dump(),
            "attributes-type": "DotRunAttributes",
            "attributes": {"runId": run_id, "agentName": agent_name},
            "alert": {"title": agent_name, "body": LIVE_START_BODY},
            "stale-date": int(now + STALE_AFTER_S),
        }
    }


def live_update_payload(state: LiveState, now: float) -> JsonObject:
    """The push that changes a run's Live Activity."""
    return {
        "aps": {
            "timestamp": int(now),
            "event": "update",
            "content-state": state.model_dump(),
            "stale-date": int(now + STALE_AFTER_S),
        }
    }


def live_end_payload(state: LiveState, now: float) -> JsonObject:
    """The push that ends a run's Live Activity, which stays on the lock screen :data:`DISMISS_AFTER_S` more."""
    return {
        "aps": {
            "timestamp": int(now),
            "event": "end",
            "content-state": state.model_dump(),
            "dismissal-date": int(now + DISMISS_AFTER_S),
        }
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def provider_token(key: ec.EllipticCurvePrivateKey, key_id: str, team_id: str, issued_at: int) -> str:
    """The ES256 token APNs takes as the provider's: the key's id in its head, the team and when it was made in its
    claims, the signature as the two 32-byte halves JWT wants (not the DER the library gives)."""
    head: str = _b64url(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode())
    claims: str = _b64url(json.dumps({"iss": team_id, "iat": issued_at}, separators=(",", ":")).encode())
    signing: bytes = f"{head}.{claims}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    return f"{head}.{claims}.{_b64url(r.to_bytes(_JWT_PARTS, 'big') + s.to_bytes(_JWT_PARTS, 'big'))}"


def load_key(path: Path) -> ec.EllipticCurvePrivateKey:
    """The APNs key at ``path``: a P-256 private key in PEM, as Apple gives it. Raises :class:`ConfigError` naming the
    path, never the key, when it is missing or of another kind."""
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError) as error:
        raise ConfigError("the APNs key cannot be read", {"path": str(path), "error": type(error).__name__}) from error
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ConfigError("the APNs key is not a P-256 key", {"path": str(path)})
    return key


class HttpTransport:
    """APNs over HTTP/2 (httpx with ``h2``), one connection kept for every push, as Apple asks."""

    def __init__(self) -> None:
        import httpx

        self._client: httpx.AsyncClient = httpx.AsyncClient(http2=True, timeout=10.0)

    async def post(self, url: str, headers: Mapping[str, str], body: bytes) -> ApnsReply:
        import httpx

        try:
            answer: httpx.Response = await self._client.post(url, headers=dict(headers), content=body)
        except httpx.HTTPError as error:
            raise ApnsError("APNs could not be reached", {"url": url, "error": str(error)}) from error
        if answer.status_code == 200:
            return ApnsReply(status=200, reason="")
        try:
            refusal: ApnsRefusal = ApnsRefusal.model_validate_json(answer.content)
        except ValidationError as error:
            raise ApnsError(
                "APNs refused a push with an answer that is not its own",
                {"url": url, "status": answer.status_code, "body": answer.text[:200]},
            ) from error
        return ApnsReply(status=answer.status_code, reason=refusal.reason)

    async def aclose(self) -> None:
        await self._client.aclose()


class ApnsSender:
    """Sends pushes to the app with the key of ``config``, through ``transport``."""

    def __init__(self, config: ApnsConfig, key: ec.EllipticCurvePrivateKey, transport: ApnsTransport) -> None:
        self._config: ApnsConfig = config
        self._key: ec.EllipticCurvePrivateKey = key
        self._transport: ApnsTransport = transport
        self._token: str = ""
        self._token_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def _bearer(self) -> str:
        async with self._lock:
            now: float = time.time()
            if self._token == "" or now - self._token_at >= TOKEN_EVERY_S:
                self._token = provider_token(self._key, self._config.key_id, self._config.team_id, int(now))
                self._token_at = now
            return self._token

    async def send(
        self, environment: ApnsEnvironment, device_token: str, push_type: PushType, payload: JsonObject
    ) -> ApnsReply:
        """Post ``payload`` to the device of ``device_token``; an alert at once, a Live Activity's at its own topic."""
        topic: str = self._config.topic if push_type == "alert" else f"{self._config.topic}.push-type.liveactivity"
        headers: dict[str, str] = {
            "authorization": f"bearer {await self._bearer()}",
            "apns-topic": topic,
            "apns-push-type": push_type,
            "apns-priority": "10",
        }
        reply: ApnsReply = await self._transport.post(
            f"{HOSTS[environment]}/3/device/{device_token}", headers, json.dumps(payload).encode("utf-8")
        )
        if reply.status != 200 and not gone(reply):
            log.warning("apns_refused", status=reply.status, reason=reply.reason, push_type=push_type)
        return reply


def read_key(config: ApnsConfig) -> ec.EllipticCurvePrivateKey:
    """The APNs key of ``config``'s ``key_path`` (see :func:`load_key`)."""
    return load_key(Path(config.key_path).expanduser())


def apns_set(config: ApnsConfig) -> bool:
    """Whether ``config`` names a key: without one the app gets no pushes, and no connection to Apple is made."""
    return config.key_path != "" and config.key_id != "" and config.team_id != ""

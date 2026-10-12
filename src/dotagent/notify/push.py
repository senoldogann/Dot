"""Web push to the owner's phone (item 52): the VAPID key pair the daemon signs with, the message a lifecycle event
turns into, the send to a subscription's push service, and the watcher that sends every subscription what the web
UI's desktop notifications say (an approval or a question asked, a run ended). The subscriptions come from the web
UI (``POST /api/push/subscriptions``) and live in the store; one whose push service says it is gone is deleted."""

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Literal

import structlog
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid, b64urlencode
from pywebpush import WebPushException, webpush

from dotagent.core.errors import DotError
from dotagent.core.types import FrozenModel
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, PushSubscription, RunRecord

log = structlog.get_logger(__name__)

# The claim a push service sees in the signed header; the daemon has no address of its own.
_SUBJECT: str = "mailto:dot@localhost"
_TTL_S: int = 600
_SEND_TIMEOUT_S: float = 10.0
_WAIT_S: float = 1.0
_BODY_CHARS: int = 140
_PRIVATE_MODE: int = stat.S_IRUSR | stat.S_IWUSR
_GONE: frozenset[int] = frozenset({404, 410})
_ASKING_TITLE: dict[str, str] = {"question": "Dot has a question", "handoff": "Dot needs your help"}
_FINISHED_TITLE: dict[str, str] = {"done": "A run is done", "failed": "A run failed", "cancelled": "A run was stopped"}
type PushState = Literal["idle", "waiting", "done", "failed"]
_FINISHED_STATE: dict[str, PushState] = {"done": "done", "failed": "failed"}
PUSH_KINDS: frozenset[str] = frozenset({"approval_requested", "run_finished"})


class PushError(DotError):
    """A push service refused or could not be reached."""


class PushKeys(FrozenModel):
    """The daemon's VAPID key pair: the private key as PEM (never logged), the public key as the base64url of its
    uncompressed point, which is what a page gives ``PushManager.subscribe`` as ``applicationServerKey``."""

    private_pem: str
    public_key: str


class PushMessage(FrozenModel):
    """What the phone shows: the same words as the web UI's desktop notifications, and the route a tap opens."""

    title: str
    body: str
    route: str
    state: PushState


def _keys_of(vapid: Vapid) -> PushKeys:
    point: bytes = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return PushKeys(private_pem=vapid.private_pem().decode("ascii"), public_key=b64urlencode(point))


def read_or_create_keys(path: Path) -> PushKeys:
    """The key pair at ``path``, made on first use and kept readable by the owner alone."""
    vapid: Vapid = Vapid.from_file(str(path))
    os.chmod(path, _PRIVATE_MODE)
    return _keys_of(vapid)


def _shorten(text: str) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= _BODY_CHARS else f"{flat[: _BODY_CHARS - 1]}…"


def push_message(event: EventRecord, run: RunRecord) -> PushMessage | None:
    """The message an event is worth, or ``None``: an approval or a question asked, and the end of a run of its own
    (the sub-runs of a run stay quiet, as in the web UI)."""
    if event.kind == "approval_requested":
        severity: object = event.data.get("severity")
        args: object = event.data.get("args")
        question: object = args.get("question") if isinstance(args, dict) else None
        summary: object = event.data.get("summary")
        text: str = question if severity == "question" and isinstance(question, str) else str(summary or "")
        title: str = _ASKING_TITLE.get(str(severity), "Dot asks for approval")
        return PushMessage(title=title, body=_shorten(text), route=f"#/runs/{run.id}", state="waiting")
    if event.kind == "run_finished" and run.parent_run_id is None:
        status: str = str(event.data.get("status", ""))
        return PushMessage(
            title=_FINISHED_TITLE.get(status, "A run has ended"),
            body=_shorten(run.goal),
            route=f"#/runs/{run.id}",
            state=_FINISHED_STATE.get(status, "idle"),
        )
    return None


def _send(keys: PushKeys, subscription: PushSubscription, message: PushMessage) -> None:
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(message.model_dump()),
            vapid_private_key=Vapid.from_pem(keys.private_pem.encode("ascii")),
            vapid_claims={"sub": _SUBJECT},
            ttl=_TTL_S,
            timeout=_SEND_TIMEOUT_S,
        )
    except WebPushException as exc:
        status: int | None = None if exc.response is None else int(exc.response.status_code)
        raise PushError(
            "the push service did not take the message",
            {"endpoint": subscription.endpoint, "status": status, "detail": str(exc)},
        ) from exc
    except OSError as exc:
        # The network failed on the way (requests raises its errors as OSError): the service never answered.
        raise PushError(
            "the push service could not be reached",
            {"endpoint": subscription.endpoint, "status": None, "detail": str(exc)},
        ) from exc


async def send_push(keys: PushKeys, subscription: PushSubscription, message: PushMessage) -> None:
    """Encrypt ``message`` for ``subscription`` and post it to its push service. Raises ``PushError``."""
    await asyncio.to_thread(_send, keys, subscription, message)


def gone(error: PushError) -> bool:
    """Whether the push service said the subscription no longer exists."""
    return error.context.get("status") in _GONE


async def _next_event(queue: asyncio.Queue[EventRecord], stopping: asyncio.Event) -> EventRecord | None:
    """The next event of ``queue``, or None once the daemon starts to stop. It waits a second at a time so that a stop
    is seen while nothing happens, in a loop: a call per quiet second would run out of stack in a quarter of an hour
    (item 116)."""
    while not stopping.is_set():
        try:
            return await asyncio.wait_for(queue.get(), _WAIT_S)
        except TimeoutError:
            continue
    return None


async def push_event(store: Store, keys: PushKeys, event: EventRecord) -> None:
    """Send one event to every subscription; a subscription its push service calls gone is deleted, any other
    refusal is logged and the rest still get theirs."""
    run: RunRecord = await store.get_run(event.run_id)
    message: PushMessage | None = push_message(event, run)
    if message is None:
        return
    for subscription in await store.list_push_subscriptions():
        try:
            await send_push(keys, subscription, message)
        except PushError as error:
            if gone(error):
                await store.delete_push_subscription(subscription.endpoint)
                log.info("push_subscription_gone", endpoint=subscription.endpoint)
            else:
                log.warning("push_failed", endpoint=subscription.endpoint, status=error.context.get("status"))


async def watch_pushes(store: Store, bus: EventBus, keys: PushKeys, stopping: asyncio.Event) -> None:
    """Send every subscription what the lifecycle events say, until the daemon starts to stop. An event that cannot
    be pushed (its run was deleted, the store failed) is logged and the next one still goes."""
    async with bus.subscribe(None) as feed:
        while not stopping.is_set():
            event: EventRecord | None = await _next_event(feed.queue, stopping)
            if event is None or event.kind not in PUSH_KINDS:
                continue
            try:
                await push_event(store, keys, event)
            except DotError as error:
                log.warning("push_event_failed", run_id=event.run_id, kind=event.kind, error=str(error))

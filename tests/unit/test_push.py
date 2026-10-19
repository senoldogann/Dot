"""Web push (item 52): the key pair is made once and kept private, an event of a run or of Dot itself becomes the
message the phone shows (or none), and a subscription's push service that says gone makes the subscription go."""

import base64
import json
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pywebpush import WebPushException

from dotagent.events import SYSTEM_RUN_ID
from dotagent.notify import push
from dotagent.notify.keys import keys_problem
from dotagent.notify.push import (
    PushError,
    PushKeys,
    PushMessage,
    PushTotals,
    gone,
    notice_message,
    push_event,
    push_message,
    read_or_create_keys,
)
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, PushSubscription, RunRecord

_NOW: float = 1_700_000_000.0


def _run(parent: str | None) -> RunRecord:
    return RunRecord(
        id="run-1",
        thread_id="thr-1",
        goal="Find ten leads for the launch and write them down",
        mode="task",
        model="scripted",
        status="running",
        created_at=_NOW,
        finished_at=None,
        result="",
        steps=0,
        input_tokens=0,
        output_tokens=0,
        parent_run_id=parent,
        source="test",
        effort=None,
        speed="standard",
        once=None,
        approvals="ask",
        goal_mode=False,
        campaign_id=None,
        agent_id=None,
        rewindable=False,
    )


def _event(kind: str, data: dict[str, object]) -> EventRecord:
    return EventRecord(id=1, run_id="run-1", ts=_NOW, kind=kind, data=data)


def _notice(kind: str, data: dict[str, object]) -> EventRecord:
    return EventRecord(id=0, run_id=SYSTEM_RUN_ID, ts=_NOW, kind=kind, data=data)


def test_the_keys_are_made_once_private_and_reused(tmp_path: Path) -> None:
    path: Path = tmp_path / "vapid-key.pem"
    first: PushKeys = read_or_create_keys(path)
    assert path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert "PRIVATE KEY" in first.private_pem and len(first.public_key) == 87
    assert read_or_create_keys(path) == first


@pytest.mark.parametrize(
    ("kind", "data", "parent", "expected"),
    [
        (
            "approval_requested",
            {"severity": "question", "summary": "Which colour? — asked", "args": {"question": "Which colour?"}},
            None,
            PushMessage(title="Dot has a question", body="Which colour?", route="#/runs/run-1", state="waiting"),
        ),
        (
            "question_left",
            {
                "approval_id": "apr-1",
                "tool": "ask_user",
                "summary": "Which colour?",
                "severity": "question",
                "args": {"question": "Which colour?", "options": []},
            },
            None,
            PushMessage(title="Dot has a question", body="Which colour?", route="#/runs/run-1", state="waiting"),
        ),
        (
            "approval_requested",
            {"severity": "handoff", "summary": "Open https://example.com/ — rule", "args": {}},
            "run-0",
            PushMessage(
                title="Dot needs your help",
                body="Open https://example.com/ — rule",
                route="#/runs/run-1",
                state="waiting",
            ),
        ),
        (
            "approval_requested",
            {"severity": "high", "summary": "rm -rf build — destructive", "args": {"kind": "shell"}},
            None,
            PushMessage(
                title="Dot asks for approval", body="rm -rf build — destructive", route="#/runs/run-1", state="waiting"
            ),
        ),
        (
            "run_finished",
            {"status": "done", "result": "\n  \nTen leads are in leads.csv.\n\n| name | site |", "needs": ""},
            None,
            PushMessage(title="A run is done", body="Ten leads are in leads.csv.", route="#/runs/run-1", state="done"),
        ),
        (
            "run_finished",
            {"status": "failed", "result": "The model provider failed: timed out", "needs": ""},
            None,
            PushMessage(
                title="A run failed",
                body="The model provider failed: timed out",
                route="#/runs/run-1",
                state="failed",
            ),
        ),
        ("run_finished", {"status": "done", "result": "Done.", "needs": ""}, "run-0", None),
        (
            "campaign_ended",
            {
                "campaign_id": "cmp-1",
                "title": "Fifty leads",
                "status": "achieved",
                "note": "The goal checker accepted the outcome: leads.csv has 50 leads.",
            },
            None,
            PushMessage(
                title="A campaign is achieved",
                body="Fifty leads: The goal checker accepted the outcome: leads.csv has 50 leads.",
                route="#/campaigns/cmp-1",
                state="done",
            ),
        ),
        (
            "campaign_ended",
            {
                "campaign_id": "cmp-1",
                "title": "Fifty leads",
                "status": "stopped",
                "note": "Stopped after 30 sessions: [agent] campaign_max_sessions is 30.",
            },
            "run-0",
            PushMessage(
                title="A campaign stopped",
                body="Fifty leads: Stopped after 30 sessions: [agent] campaign_max_sessions is 30.",
                route="#/campaigns/cmp-1",
                state="idle",
            ),
        ),
        ("run_started", {}, None, None),
    ],
)
def test_an_event_becomes_the_message_the_phone_shows_or_none(
    kind: str, data: dict[str, object], parent: str | None, expected: PushMessage | None
) -> None:
    assert push_message(_event(kind, data), _run(parent)) == expected


@pytest.mark.parametrize(
    ("kind", "data", "expected"),
    [
        (
            "schedule_missed",
            {
                "schedule_id": "sch-1",
                "name": "Morning digest",
                "status": "skipped: 125 minutes overdue (the daemon was not running)",
            },
            PushMessage(
                title="A schedule did not run",
                body="Morning digest: skipped: 125 minutes overdue (the daemon was not running)",
                route="#/schedules",
                state="failed",
            ),
        ),
        (
            "daemon_restarted",
            {"runs_cut_off": 0},
            PushMessage(
                title="Dot restarted after an unexpected stop",
                body="No run was cut off.",
                route="#/runs",
                state="failed",
            ),
        ),
        (
            "daemon_restarted",
            {"runs_cut_off": 1},
            PushMessage(
                title="Dot restarted after an unexpected stop",
                body="1 run was cut off.",
                route="#/runs",
                state="failed",
            ),
        ),
        (
            "daemon_restarted",
            {"runs_cut_off": 3},
            PushMessage(
                title="Dot restarted after an unexpected stop",
                body="3 runs were cut off.",
                route="#/runs",
                state="failed",
            ),
        ),
        (
            "learning_stalled",
            {"reason": "Drawing lessons from runs failed 3 times in a row: the embedder is down"},
            PushMessage(
                title="Dot's learning has stopped",
                body="Drawing lessons from runs failed 3 times in a row: the embedder is down",
                route="#/settings/memory",
                state="failed",
            ),
        ),
        ("run_finished", {"status": "done", "result": "Done.", "needs": ""}, None),
    ],
)
def test_an_event_of_dot_itself_becomes_the_message_the_phone_shows_or_none(
    kind: str, data: dict[str, object], expected: PushMessage | None
) -> None:
    assert notice_message(_notice(kind, data)) == expected


def test_gone_is_what_the_push_service_says_with_404_or_410() -> None:
    assert gone(PushError("gone", {"endpoint": "https://push.example/1", "status": 410}))
    assert gone(PushError("gone", {"endpoint": "https://push.example/1", "status": 404}))
    assert not gone(PushError("refused", {"endpoint": "https://push.example/1", "status": 500}))
    assert not gone(PushError("unreachable", {"endpoint": "https://push.example/1", "status": None}))


@pytest.mark.asyncio
async def test_subscriptions_are_kept_by_endpoint_and_deleted(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        first: PushSubscription = await store.add_push_subscription("https://push.example/a", "p1", "a1")
        await store.add_push_subscription("https://push.example/a", "p2", "a2")
        await store.add_push_subscription("https://push.example/b", "p3", "a3")
        listed: list[PushSubscription] = await store.list_push_subscriptions()
        assert [(s.endpoint, s.p256dh, s.auth) for s in listed] == [
            ("https://push.example/a", "p2", "a2"),
            ("https://push.example/b", "p3", "a3"),
        ]
        assert listed[0].created_at == first.created_at
        await store.delete_push_subscription("https://push.example/a")
        assert [s.endpoint for s in await store.list_push_subscriptions()] == ["https://push.example/b"]
    finally:
        await store.close()


def test_keys_that_cannot_encrypt_a_message_are_named() -> None:
    point: bytes = (
        ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    p256dh: str = base64.urlsafe_b64encode(point).decode("ascii")
    auth: str = base64.urlsafe_b64encode(b"0123456789abcdef").decode("ascii")
    assert keys_problem(p256dh, auth) is None
    assert keys_problem(p256dh.rstrip("="), auth.rstrip("=")) is None
    off_curve: str = base64.urlsafe_b64encode(b"\x04" + bytes(64)).decode("ascii")
    assert keys_problem(off_curve, auth) == "p256dh is not a point of P-256"
    assert keys_problem("!!!", auth) == "p256dh is not 65 bytes in URL-safe base64"
    assert keys_problem(p256dh, "c2hvcnQ") == "auth is not 16 bytes in URL-safe base64"


class _Answer:
    """What a push service answers, as pywebpush hands it on in a ``WebPushException``."""

    def __init__(self, status_code: int) -> None:
        self.status_code: int = status_code
        self.text: str = f"MARKER-answer-{status_code}"


def _usable_keys() -> tuple[str, str]:
    point: bytes = (
        ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    return base64.urlsafe_b64encode(point).decode("ascii"), base64.urlsafe_b64encode(b"0123456789abcdef").decode(
        "ascii"
    )


@pytest.mark.asyncio
async def test_each_subscription_is_pushed_on_its_own_and_the_caller_gets_the_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code review's Y15: one subscription that fails keeps no other from its message. A push service that calls a
    subscription gone (410) or keys that cannot encrypt get it deleted; any other refusal is logged and counted, and
    the subscription kept; the good one gets the message (pywebpush is a fake here: no push service is called)."""
    answers: dict[str, int] = {"https://push.example/gone": 410, "https://push.example/refusing": 500}
    taken: list[tuple[str, object]] = []

    def webpush(subscription_info: dict[str, str], data: str, **signing: object) -> None:
        endpoint: str = subscription_info["endpoint"]
        if endpoint in answers:
            raise WebPushException("Push failed", response=_Answer(answers[endpoint]))
        taken.append((endpoint, json.loads(data)))

    monkeypatch.setattr(push, "webpush", webpush)
    keys: PushKeys = read_or_create_keys(tmp_path / "vapid-key.pem")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        p256dh, auth = _usable_keys()
        for endpoint in ("https://push.example/gone", "https://push.example/refusing", "https://push.example/good"):
            await store.add_push_subscription(endpoint, p256dh, auth)
        await store.add_push_subscription("https://push.example/unusable", "!!!", auth)
        totals: PushTotals = await push_event(store, keys, _notice("daemon_restarted", {"runs_cut_off": 0}))
        assert totals == PushTotals(sent=1, failed=1, removed=2)
        assert taken == [
            (
                "https://push.example/good",
                {
                    "title": "Dot restarted after an unexpected stop",
                    "body": "No run was cut off.",
                    "route": "#/runs",
                    "state": "failed",
                },
            )
        ]
        assert [kept.endpoint for kept in await store.list_push_subscriptions()] == [
            "https://push.example/refusing",
            "https://push.example/good",
        ]
        # An event the phone is not told of reaches no push service.
        assert await push_event(store, keys, _notice("run_started", {})) == PushTotals(sent=0, failed=0, removed=0)
        assert len(taken) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_a_busy_push_service_is_asked_once_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 503 lost the message before (the outside audit of 2026-10-17, R13); the service is asked once more, and a
    second refusal is the outcome (pywebpush is a fake here: no push service is called)."""
    answers: list[int] = [503]
    taken: list[str] = []

    def webpush(subscription_info: dict[str, str], data: str, **signing: object) -> None:
        if answers:
            raise WebPushException("Push failed", response=_Answer(answers.pop(0)))
        taken.append(subscription_info["endpoint"])

    monkeypatch.setattr(push, "webpush", webpush)
    keys: PushKeys = read_or_create_keys(tmp_path / "vapid-key.pem")
    p256dh, auth = _usable_keys()
    subscription: PushSubscription = PushSubscription(
        endpoint="https://push.example/busy", p256dh=p256dh, auth=auth, created_at=_NOW
    )
    message: PushMessage = PushMessage(title="t", body="b", route="#/runs", state="failed")
    await push.send_push(keys, subscription, message)
    assert taken == ["https://push.example/busy"]
    answers.extend([503, 503])
    with pytest.raises(PushError):
        await push.send_push(keys, subscription, message)
    assert answers == []

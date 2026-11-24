"""Dot's iPhone app (item 997): the provider token APNs takes, the payloads an alert and a Live Activity carry (no more
than a fixed title and a chat's short title), and the watch that sends them, with a stand-in for Apple."""

import base64
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.campaign import CAMPAIGN_ENDED_EVENT
from dotagent.agent.learning_health import LEARNING_STALLED_EVENT
from dotagent.agent.manager import BACKGROUND_SOURCE
from dotagent.config import ApnsConfig, AppConfig, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.drafts.service import DRAFT_CREATED_EVENT
from dotagent.events import DAEMON_RESTARTED_EVENT, SYSTEM_RUN_ID
from dotagent.notify.apns import (
    ALERT_BODY,
    APPROVAL_CATEGORY,
    ATTRIBUTES_TYPE,
    DISMISS_AFTER_S,
    FLEET_ID,
    START_TITLE,
    ApnsError,
    ApnsReply,
    ApnsSender,
    FleetState,
    LiveState,
    alert_payload,
    apns_set,
    approval_payload,
    badged,
    live_end_payload,
    live_start_payload,
    live_update_payload,
    load_key,
    provider_token,
    short_title,
)
from dotagent.notify.apns_watch import (
    ACTIVITY_LIFE_S,
    CHECK_EVERY_S,
    LIVE_REGISTERED_EVENT,
    OPEN_WAIT_S,
    PROGRESS_EVERY_S,
    START_AFTER_S,
    START_AT_ONCE,
    START_EVERY_S,
    TOLD_TITLE,
    ApnsWatch,
    follows_live,
)
from dotagent.notify.leases import PhoneLeases
from dotagent.notify.live_now import LiveNow, tool_now
from dotagent.notify.live_start import keep_live_start_token, live_start_token_of
from dotagent.notify.owner import OWNER_TOLD_EVENT
from dotagent.notify.settings import PushSettings, keep_push_settings
from dotagent.runtime import _guarded_files
from dotagent.scheduler import SCHEDULE_MISSED_EVENT, SCHEDULE_RUN_ENDED_EVENT, SCHEDULE_STOPPED_EVENT
from dotagent.scheduler.notices import tells
from dotagent.store.db import Store
from dotagent.store.models import DraftRecord, EventRecord, RunRecord

_NOW: float = 1_700_000_000.0
_DEVICE: str = "ab" * 32
_OTHER_DEVICE: str = "12" * 32
_START: str = "cd" * 40
_ACTIVITY: str = "ef" * 40
_CONFIG: ApnsConfig = ApnsConfig(
    key_path="/nowhere/key.p8", key_id="ABCDE12345", team_id="79DZ4AA4DW", topic="dev.dotagent.phone"
)


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class _Apple:
    """A stand-in for APNs: keeps what was posted and answers what it is told to."""

    def __init__(self, answers: Mapping[str, ApnsReply]) -> None:
        self.posted: list[tuple[str, dict[str, str], JsonObject]] = []
        self._answers: Mapping[str, ApnsReply] = answers

    async def post(self, url: str, headers: Mapping[str, str], body: bytes) -> ApnsReply:
        self.posted.append((url, dict(headers), json.loads(body)))
        return self._answers.get(url.rsplit("/", 1)[1], ApnsReply(status=200, reason=""))


def test_the_provider_token_is_es256_with_the_key_id_and_the_team_and_verifies() -> None:
    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    token: str = provider_token(key, "ABCDE12345", "79DZ4AA4DW", int(_NOW))
    head, claims, signature = token.split(".")
    assert json.loads(_unb64(head)) == {"alg": "ES256", "kid": "ABCDE12345"}
    assert json.loads(_unb64(claims)) == {"iss": "79DZ4AA4DW", "iat": int(_NOW)}
    raw: bytes = _unb64(signature)
    assert len(raw) == 64
    der: bytes = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{head}.{claims}".encode("ascii"), ec.ECDSA(hashes.SHA256()))


def test_a_key_that_is_missing_or_not_p256_is_named_by_its_path_alone(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as missing:
        load_key(tmp_path / "none.p8")
    assert missing.value.context == {"path": str(tmp_path / "none.p8"), "error": "FileNotFoundError"}
    other: Path = tmp_path / "other.p8"
    other.write_bytes(
        ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    with pytest.raises(ConfigError, match="P-256"):
        load_key(other)
    assert apns_set(_CONFIG) and not apns_set(_CONFIG.model_copy(update={"key_path": ""}))


def test_an_alert_carries_its_title_and_a_fixed_line_and_a_live_activity_a_short_title() -> None:
    assert alert_payload("A task is done", "#/runs/run-1") == {
        "aps": {"alert": {"title": "A task is done", "body": ALERT_BODY}, "sound": "default"},
        "route": "#/runs/run-1",
    }
    assert short_title("  Find   ten leads\nfor the launch ") == "Find ten leads for the launch"
    assert short_title("x" * 80) == "x" * 39 + "…"
    row = LiveState(
        runId="run-1", title="Leads", agent="Dot", status="working", kind="thinking", line="Thinking", startedAt=_NOW
    )
    # A Live Activity's push brings no notification (item 1096): the owner is told when a run ends or waits.
    for payload in (
        live_update_payload(FleetState(active=1, rows=(row,)), _NOW),
        live_end_payload(FleetState(active=0, rows=()), _NOW),
    ):
        aps: object = payload["aps"]
        assert isinstance(aps, dict) and "alert" not in aps and "sound" not in aps


async def _run(store: Store, source: str, parent: str | None) -> RunRecord:
    thread = await store.create_thread("Find ten leads for the launch", source)
    return await store.create_run(
        thread.id, "goal", "task", "m:m", parent, source, "medium", "standard", None, "ask", False, None, None, None
    )


def _event(run_id: str, kind: str, data: JsonObject) -> EventRecord:
    return EventRecord(id=1, run_id=run_id, ts=_NOW, kind=kind, data=data)


def _state(posted: tuple[str, dict[str, str], JsonObject]) -> JsonObject:
    """The content state of a Live Activity's push read back."""
    aps: object = posted[2]["aps"]
    assert isinstance(aps, dict)
    state: object = aps["content-state"]
    assert isinstance(state, dict)
    return state


def _rows(posted: tuple[str, dict[str, str], JsonObject]) -> list[tuple[str, str]]:
    """The rows of a Live Activity's push read back, as (run id, status)."""
    rows: object = _state(posted)["rows"]
    assert isinstance(rows, list)
    return [(str(row["runId"]), str(row["status"])) for row in rows if isinstance(row, dict)]


def _registered() -> EventRecord:
    return EventRecord(id=0, run_id=SYSTEM_RUN_ID, ts=_NOW, kind=LIVE_REGISTERED_EVENT, data={"phone_id": "p1"})


async def test_the_owners_runs_are_rows_of_the_activity_the_app_opened_from_its_token_to_the_last_end(
    tmp_path: Path,
) -> None:
    """Items 1093 and 1096: the daemon starts no activity, which a push-to-start would announce; once the app opened
    one and gave its token, every change goes to it, the runs at work first, and it ends once the last ended run has
    stood its time."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, _START, "sandbox", _NOW)
        first: RunRecord = await _run(store, "chat", None)
        second: RunRecord = await _run(store, "chat", None)
        assert follows_live(first)
        await watch.on_event(_event(first.id, "run_started", {}), _NOW)
        await watch.on_event(_event(second.id, "run_started", {}), _NOW + 1)
        assert apple.posted == []
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW + 2)
        await watch.on_event(_registered(), _NOW + 2)
        url, headers, _ = apple.posted[-1]
        assert url == f"https://api.sandbox.push.apple.com/3/device/{_ACTIVITY}"
        assert headers["apns-topic"] == "dev.dotagent.phone.push-type.liveactivity"
        assert headers["apns-push-type"] == "liveactivity" and headers["authorization"].startswith("bearer ")
        assert _state(apple.posted[-1])["active"] == 2
        # The newest run at work first.
        assert _rows(apple.posted[-1]) == [(second.id, "working"), (first.id, "working")]
        row: object = _state(apple.posted[-1])["rows"][0]  # type: ignore[index]  # a JSON payload read back
        assert isinstance(row, dict) and row["title"] == "Find ten leads for the launch" and row["agent"] == "Dot"
        # A wait is told as an alert that says nothing of the step, and on the row.
        await watch.on_event(
            _event(first.id, "approval_requested", {"severity": "approval", "summary": "Send it"}), _NOW + 3
        )
        alert_url, alert_headers, alert = apple.posted[-2]
        assert alert_url.endswith(_DEVICE) and alert_headers["apns-topic"] == "dev.dotagent.phone"
        assert alert["aps"]["alert"]["body"] == ALERT_BODY and "Send it" not in json.dumps(alert)  # type: ignore[index]  # a JSON payload read back
        assert (first.id, "waiting") in _rows(apple.posted[-1])
        # An ended run stays a row, after the runs at work.
        await watch.on_event(_event(second.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 4)
        assert _state(apple.posted[-1])["active"] == 1
        assert _rows(apple.posted[-1]) == [(first.id, "waiting"), (second.id, "done")]
        await watch.on_event(_event(first.id, "run_finished", {"status": "cancelled", "result": "x"}), _NOW + 5)
        assert _rows(apple.posted[-1]) == [(first.id, "failed"), (second.id, "done")]
        # Each ended row goes once it has stood its time, and the activity with the last.
        posted: int = len(apple.posted)
        await watch.flush(_NOW + 4 + DISMISS_AFTER_S - 1)
        assert len(apple.posted) == posted
        await watch.flush(_NOW + 4 + DISMISS_AFTER_S)
        assert _rows(apple.posted[-1]) == [(first.id, "failed")]
        await watch.flush(_NOW + 5 + DISMISS_AFTER_S)
        url, _, ended = apple.posted[-1]
        assert url.endswith(_ACTIVITY) and ended["aps"]["event"] == "end"  # type: ignore[index]  # a JSON payload read back
        assert await store.live_activities_of(FLEET_ID) == []
        # The next run is told nothing until the app opens a new activity.
        posted = len(apple.posted)
        third: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(third.id, "run_started", {}), _NOW + DISMISS_AFTER_S + 10)
        assert len(apple.posted) == posted
    finally:
        await store.close()


async def test_an_activity_whose_token_comes_before_its_run_waits_for_it_and_a_restart_ends_what_no_row_holds(
    tmp_path: Path,
) -> None:
    """Item 1096: the app opens the activity as the run it starts is made, and its token may reach the daemon first;
    the activity is not ended for having no row yet, and the run's start fills it; one whose request started no run
    (a message queued behind a held queue) ends after :data:`OPEN_WAIT_S`. And a daemon that starts again ends the
    activity it finds kept with no row (the pre-push review of item 1093)."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        await watch.on_event(_registered(), _NOW)
        await watch.flush(_NOW + OPEN_WAIT_S - 1)
        assert apple.posted == [] and len(await store.live_activities_of(FLEET_ID)) == 1
        await watch.flush(_NOW + OPEN_WAIT_S)
        assert apple.posted[-1][0].endswith(_ACTIVITY) and apple.posted[-1][2]["aps"]["event"] == "end"  # type: ignore[index]  # a JSON payload read back
        assert await store.live_activities_of(FLEET_ID) == []
        apple.posted.clear()
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        await watch.on_event(_registered(), _NOW)
        run: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW + 1)
        assert apple.posted[-1][0].endswith(_ACTIVITY) and _rows(apple.posted[-1]) == [(run.id, "working")]
        posted: int = len(apple.posted)
        await watch.flush(_NOW + OPEN_WAIT_S)
        assert len(apple.posted) == posted
        # A daemon that starts again goes on showing the run still at work on the activity it finds, and ends it once
        # it finds none at work.
        again = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await again.settle(_NOW + 2000)
        assert apple.posted[-1][0].endswith(_ACTIVITY) and apple.posted[-1][2]["aps"]["event"] == "update"  # type: ignore[index]  # a JSON payload read back
        assert _rows(apple.posted[-1]) == [(run.id, "working")]
        await store.finish_run(run.id, "done", "x")
        later = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await later.settle(_NOW + 3000)
        assert apple.posted[-1][0].endswith(_ACTIVITY) and apple.posted[-1][2]["aps"]["event"] == "end"  # type: ignore[index]  # a JSON payload read back
        assert await store.live_activities_of(FLEET_ID) == []
        # An answer to a question the run left comes after it ended: nothing goes.
        posted = len(apple.posted)
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 3001)
        alerts: int = len(apple.posted) - posted
        await watch.on_event(_event(run.id, "approval_decided", {}), _NOW + 3002)
        assert len(apple.posted) == posted + alerts
    finally:
        await store.close()


async def test_an_activity_older_than_ios_keeps_one_is_replaced_by_a_new_one(tmp_path: Path) -> None:
    """iOS ends a Live Activity after eight hours: its token goes nowhere and is forgotten, and the app opens a new one
    at the next run it starts."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, _START, "sandbox", _NOW)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        run: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW + 60)
        assert apple.posted[-1][0].endswith(_ACTIVITY)
        await watch.on_event(_event(run.id, "approval_requested", {"severity": "approval"}), _NOW + ACTIVITY_LIFE_S)
        assert not apple.posted[-1][0].endswith(_ACTIVITY)
        assert all(not url.endswith(_START) for url, _, _ in apple.posted)
        assert await store.live_activities_of(FLEET_ID) == []
    finally:
        await store.close()


async def test_a_helpers_or_a_schedules_run_has_no_live_activity_and_a_gone_token_is_forgotten(tmp_path: Path) -> None:
    """No push goes to a start token (item 1096); a device token that is gone takes the device."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple(
            {
                _DEVICE: ApnsReply(status=400, reason="BadDeviceToken"),
            }
        )
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, _START, "sandbox", _NOW)
        parent: RunRecord = await _run(store, "chat", None)
        for run in (await _run(store, "schedule", None), await _run(store, "chat", parent.id)):
            assert not follows_live(run)
            await watch.on_event(_event(run.id, "run_started", {}), _NOW)
        assert apple.posted == []
        await watch.on_event(_event(parent.id, "run_started", {}), _NOW)
        assert apple.posted == []
        await watch.on_event(_event(parent.id, "run_finished", {"status": "done", "result": "x"}), _NOW)
        assert await store.list_apns_devices() == []
    finally:
        await store.close()


async def test_a_message_dot_wrote_goes_with_a_fixed_title(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "production", _NOW)
        told: JsonObject = {"title": "Your rent is due", "text": "Pay it", "url": "#/"}
        await watch.on_event(EventRecord(id=0, run_id=SYSTEM_RUN_ID, ts=_NOW, kind=OWNER_TOLD_EVENT, data=told), _NOW)
        url, _, alert = apple.posted[-1]
        assert url.startswith("https://api.push.apple.com/")
        assert alert["aps"]["alert"]["title"] == TOLD_TITLE and "rent" not in json.dumps(alert)  # type: ignore[index]  # a JSON payload read back
    finally:
        await store.close()


def test_the_apns_key_is_guarded_from_the_runs_once_it_is_set() -> None:
    """Item 997 (review): the key of ``[apns] key_path`` is among the files no run reads, by the guard or the
    sandbox."""
    config: AppConfig = load_default_config()
    key: Path = Path("~/Library/Application Support/Dot-keys/AuthKey_TEST.p8")
    with_key: AppConfig = config.model_copy(update={"apns": config.apns.model_copy(update={"key_path": str(key)})})
    assert key.expanduser() in _guarded_files(with_key)
    assert all(path.suffix != ".p8" for path in _guarded_files(config))


async def test_an_activity_whose_token_comes_after_its_last_run_ended_is_ended_at_once(tmp_path: Path) -> None:
    """A short run ends before the app sends the activity's token, and stands as a row until its time is over: the
    activity is told it as the token comes. A question left for later does not make a working run wait."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, _START, "sandbox", _NOW)
        run: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW)
        await watch.on_event(_event(run.id, QUESTION_LEFT_EVENT, {}), _NOW)
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 1)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW + 2)
        await watch.on_event(_registered(), _NOW + 2)
        assert apple.posted[-1][0].endswith(_ACTIVITY)
        assert _rows(apple.posted[-1]) == [(run.id, "done")] and _state(apple.posted[-1])["active"] == 0
        await watch.flush(_NOW + 1 + DISMISS_AFTER_S)
        assert apple.posted[-1][2]["aps"]["event"] == "end"  # type: ignore[index]  # a JSON payload read back
    finally:
        await store.close()


async def test_a_runs_steps_reach_its_live_activity_as_what_it_does_now_at_most_every_few_seconds(
    tmp_path: Path,
) -> None:
    """Item 1004: a step says what the run does now (a kind and a fixed line, never what the step was about), sent at
    once when the last push is old enough and kept for later otherwise, the latest winning; a wait is told at once."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, _START, "sandbox", _NOW)
        run: RunRecord = await _run(store, "chat", None)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW)
        [started] = _state(apple.posted[-1])["rows"]  # type: ignore[misc]  # a JSON payload read back
        assert started["kind"] == "thinking"  # type: ignore[index]  # a JSON payload read back
        before: int = len(apple.posted)
        secret_url: dict[str, object] = {"tool": "browser_open", "args": {"url": "https://bank.example/account/42"}}
        await watch.on_event(_event(run.id, "tool_call", secret_url), _NOW + 1)
        await watch.on_event(
            _event(run.id, "tool_call", {"tool": "shell", "args": {"command": "cat secrets"}}), _NOW + 2
        )
        assert len(apple.posted) == before
        await watch.flush(_NOW + 3)
        assert len(apple.posted) == before
        await watch.flush(_NOW + PROGRESS_EVERY_S)
        url, headers, update = apple.posted[-1]
        assert url.endswith(_ACTIVITY) and headers["apns-priority"] == "5"
        [row] = _state(apple.posted[-1])["rows"]  # type: ignore[misc]  # a JSON payload read back
        assert (row["kind"], row["line"]) == ("command", "Running a command")  # type: ignore[index]  # a JSON payload read back
        assert "bank.example" not in json.dumps(update) and "secrets" not in json.dumps(update)
        await watch.on_event(_event(run.id, "tool_result", {"tool": "shell"}), _NOW + PROGRESS_EVERY_S * 2)
        assert _state(apple.posted[-1])["rows"][0]["line"] == "Thinking"  # type: ignore[index]  # a JSON payload read back
        await watch.on_event(
            _event(run.id, "approval_requested", {"severity": "approval"}), _NOW + PROGRESS_EVERY_S * 2 + 1
        )
        waiting_url, waiting_headers, waiting = apple.posted[-1]
        assert waiting_url.endswith(_ACTIVITY) and waiting_headers["apns-priority"] == "10"
        assert waiting["aps"]["content-state"]["rows"][0]["kind"] == "waiting"  # type: ignore[index]  # a JSON payload read back
    finally:
        await store.close()


def test_each_tool_says_what_the_run_does_in_a_fixed_line() -> None:
    assert tool_now("web_search") == LiveNow(kind="searching", line="Searching the web")
    assert tool_now("browser_act") == LiveNow(kind="browsing", line="Browsing the web")
    assert tool_now("mcp__google-workspace__gmail_send") == LiveNow(kind="app", line="Using Google Workspace")
    assert tool_now("finish") is None and tool_now("update_plan") is None
    assert tool_now("something_new") == LiveNow(kind="working", line="Working")


async def test_an_approval_alert_offers_the_lock_screens_answer_and_a_payment_does_not(tmp_path: Path) -> None:
    """Item 1008: an approval of a step of low or medium risk goes with the app's category for Approve and Deny and its
    id, never what it asks; a step of high risk (a payment), a question, a secret and a handoff go as a plain alert,
    which opens Dot, where the owner sees what is asked (the pre-push review)."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        run: RunRecord = await _run(store, "schedule", None)
        step: JsonObject = {"approval_id": "apr_1", "severity": "medium", "summary": "Open the bank's page", "args": {}}
        await watch.on_event(_event(run.id, "approval_requested", step), _NOW)
        asked: JsonObject = apple.posted[-1][2]
        assert asked["aps"]["category"] == APPROVAL_CATEGORY and asked["approval_id"] == "apr_1"  # type: ignore[index]  # a JSON payload read back
        assert "bank" not in json.dumps(asked)
        for severity in ("high", "question", "secret", "handoff"):
            other: JsonObject = {"approval_id": "apr_2", "severity": severity, "summary": "x", "args": {}}
            await watch.on_event(_event(run.id, "approval_requested", other), _NOW)
            assert "category" not in apple.posted[-1][2]["aps"] and "approval_id" not in apple.posted[-1][2]  # type: ignore[operator]  # a JSON payload read back
    finally:
        await store.close()


async def test_a_row_whose_run_ended_where_the_feed_did_not_tell_is_ended_within_a_minute(tmp_path: Path) -> None:
    """Item 1108: a run closed by the store alone (the daemon's start, a command's process found gone) sent no
    ``run_finished`` to the watch, and its row stood "Working" until the daemon started again."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        run: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW)
        await watch.flush(_NOW + 1)
        assert _rows(apple.posted[-1]) == [(run.id, "working")]
        assert await store.finish_run(run.id, "failed", "The daemon stopped.")
        posted: int = len(apple.posted)
        await watch.flush(_NOW + CHECK_EVERY_S)
        assert len(apple.posted) == posted
        await watch.flush(_NOW + 1 + CHECK_EVERY_S)
        assert _rows(apple.posted[-1]) == [(run.id, "failed")]
    finally:
        await store.close()


def _alerts_to(apple: _Apple, token: str) -> list[JsonObject]:
    """The alerts posted to the device ``token``, as the phone would read them."""
    return [body for url, headers, body in apple.posted if headers["apns-push-type"] == "alert" and url.endswith(token)]


def _system(kind: str, data: JsonObject) -> EventRecord:
    """An event of Dot itself: a notice, which belongs to no run."""
    return EventRecord(id=0, run_id=SYSTEM_RUN_ID, ts=_NOW, kind=kind, data=data)


def _draft_written(run: RunRecord) -> EventRecord:
    """The event of a draft a run wrote for the owner to send."""
    draft: DraftRecord = DraftRecord(
        id="drf_1",
        run_id=run.id,
        channel="mail",
        to=("a@example.com",),
        cc=(),
        subject="Hi",
        body="x",
        status="waiting",
        created_at=_NOW,
        decided_at=None,
        error=None,
    )
    return _event(run.id, DRAFT_CREATED_EVENT, draft.model_dump(mode="json"))


async def test_a_phone_that_shows_dot_hears_no_alert_of_a_runs_end_and_every_other_alert_still_goes(
    tmp_path: Path,
) -> None:
    """Item 1420: while a phone's lease holds (the app said it is in the foreground), the end of a run, of a schedule's
    run and of a campaign, which the app shows itself, are not sent to it as alerts, for the chat it shows and for any
    other; an approval, a question, a secret and a hand-off are, and so are a draft, what Dot tells the owner and the
    warnings of Dot itself, which the app shows nowhere; every Live Activity update is told all the same. Another phone,
    and the same phone once the lease ran out or the app said it left, hear all of it. The clock is the test's own."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        clock: list[float] = [_NOW]
        leases = PhoneLeases(lambda: clock[0])
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple), leases)
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_apns_device("p2", _OTHER_DEVICE, None, "sandbox", _NOW)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        run: RunRecord = await _run(store, "chat", None)
        leases.renew("p1", "foreground", run.thread_id, None)

        # The end of the run is told to the phone that does not show Dot, and not to the one that does; its Live
        # Activity is told all the same.
        await watch.on_event(_event(run.id, "run_started", {}), _NOW + 1)
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 2)
        assert _alerts_to(apple, _DEVICE) == [] and len(_alerts_to(apple, _OTHER_DEVICE)) == 1
        assert apple.posted[-1][0].endswith(_ACTIVITY) and _rows(apple.posted[-1]) == [(run.id, "done")]
        # So are a schedule's run and a campaign, ended.
        ended: JsonObject = {"schedule_id": "sch_1", "run_id": run.id, "status": "done", "result": "x"}
        await watch.on_event(_system(SCHEDULE_RUN_ENDED_EVENT, ended), _NOW + 3)
        campaign: JsonObject = {"campaign_id": "cmp_1", "title": "Leads", "status": "achieved", "note": "x"}
        await watch.on_event(_event(run.id, CAMPAIGN_ENDED_EVENT, campaign), _NOW + 3)
        assert _alerts_to(apple, _DEVICE) == [] and len(_alerts_to(apple, _OTHER_DEVICE)) == 3

        # What asks something of the owner goes to the phone that shows Dot, whatever it asks.
        for index, severity in enumerate(("low", "medium", "high", "question", "secret", "handoff")):
            asked: JsonObject = {"approval_id": f"apr_{index}", "severity": severity, "summary": "x", "args": {}}
            await watch.on_event(_event(run.id, "approval_requested", asked), _NOW + 5)
            assert len(_alerts_to(apple, _DEVICE)) == index + 1
        left: JsonObject = {"approval_id": "apr_9", "severity": "question"}
        await watch.on_event(_event(run.id, QUESTION_LEFT_EVENT, left), _NOW + 6)
        assert len(_alerts_to(apple, _DEVICE)) == 7
        # A draft written for the owner, what Dot tells them on its own and the warnings of Dot itself go too.
        await watch.on_event(_draft_written(run), _NOW + 6)
        told: JsonObject = {"title": "Your rent is due", "text": "Pay it", "url": "#/"}
        await watch.on_event(_system(OWNER_TOLD_EVENT, told), _NOW + 6)
        missed: JsonObject = {"schedule_id": "sch_1", "name": "Brief", "status": "missed"}
        await watch.on_event(_system(SCHEDULE_MISSED_EVENT, missed), _NOW + 6)
        stopped: JsonObject = {"schedule_id": "sch_1", "name": "Brief", "failures": 3}
        await watch.on_event(_system(SCHEDULE_STOPPED_EVENT, stopped), _NOW + 6)
        await watch.on_event(_system(LEARNING_STALLED_EVENT, {"reason": "no model"}), _NOW + 6)
        await watch.on_event(_system(DAEMON_RESTARTED_EVENT, {"runs_cut_off": 0}), _NOW + 6)
        heard: int = len(_alerts_to(apple, _DEVICE))
        assert heard == 7 + 6

        # The chat on screen is another one: the phone still shows Dot, and the end of a run stays quiet.
        leases.renew("p1", "foreground", "thr_another_chat", None)
        await watch.on_event(_event(run.id, "run_finished", {"status": "failed", "result": "x"}), _NOW + 7)
        assert len(_alerts_to(apple, _DEVICE)) == heard

        # The lease holds its 45 seconds from the last time the app said it, not a moment more.
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 44.9)
        assert len(_alerts_to(apple, _DEVICE)) == heard
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 45.0)
        assert len(_alerts_to(apple, _DEVICE)) == heard + 1

        # The app says it left: the alert goes at once, though the time it named has not passed.
        leases.renew("p1", "foreground", None, 90.0)
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 46)
        assert len(_alerts_to(apple, _DEVICE)) == heard + 1
        leases.renew("p1", "background", None, None)
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 47)
        assert len(_alerts_to(apple, _DEVICE)) == heard + 2
    finally:
        await store.close()


async def test_a_watch_that_was_given_no_leases_sends_every_alert(tmp_path: Path) -> None:
    """Item 1420: a daemon that keeps no lease (a test's, an older server) sends the app every alert it did before."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        run: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW)
        assert len(_alerts_to(apple, _DEVICE)) == 1
    finally:
        await store.close()


async def test_a_phone_is_told_only_the_kinds_of_push_it_switched_on_each_phone_for_itself(tmp_path: Path) -> None:
    """Item 1421: an approval, a question, a secret and a hand-off, and the failures of Dot's own work, are
    ``needs_you``; a draft and what Dot tells the owner are ``ready``; the end of a run, of a schedule's run or of a
    campaign is ``done``. A kind a phone switched off is not sent to it, and the others are; the phone's lock-screen
    approval is ``needs_you`` too; the Live Activity is not a kind and follows the run as before."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        third: str = "34" * 32
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_apns_device("p2", _OTHER_DEVICE, None, "sandbox", _NOW)
        await store.set_apns_device("p3", third, None, "sandbox", _NOW)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        # p1 wants to be asked and nothing more; p2 never said, so it hears all; p3 wants all but being asked.
        await keep_push_settings(store, "p1", PushSettings(needs_you=True, ready=False, done=False))
        await keep_push_settings(store, "p3", PushSettings(needs_you=False, ready=True, done=True))
        run: RunRecord = await _run(store, "chat", None)
        step: JsonObject = {"approval_id": "apr_1", "severity": "low", "summary": "x", "args": {}}
        sent: JsonObject = {"schedule_id": "s", "run_id": run.id, "status": "failed", "result": "x"}
        missed: JsonObject = {"schedule_id": "s", "name": "Brief", "status": "missed"}
        events: list[EventRecord] = [
            _event(run.id, "approval_requested", step),
            _event(run.id, "approval_requested", {**step, "severity": "question"}),
            _draft_written(run),
            _system(OWNER_TOLD_EVENT, {"title": "Brief", "text": "x", "url": "#/"}),
            _event(run.id, "run_finished", {"status": "done", "result": "x"}),
            _system(SCHEDULE_RUN_ENDED_EVENT, sent),
            _system(SCHEDULE_MISSED_EVENT, missed),
        ]
        for event in events:
            await watch.on_event(event, _NOW + 1)
        heard: dict[str, int] = {
            "p1": len(_alerts_to(apple, _DEVICE)),
            "p2": len(_alerts_to(apple, _OTHER_DEVICE)),
            "p3": len(_alerts_to(apple, third)),
        }
        assert heard == {"p1": 3, "p2": 7, "p3": 4}
        # The lock screen's approval is a ``needs_you``: p3 has it off and is sent none, p1 and p2 are.
        assert [body.get("approval_id") for body in _alerts_to(apple, third)] == [None] * 4
        assert next(iter(_alerts_to(apple, _DEVICE))).get("approval_id") == "apr_1"
        # The Live Activity is told the row of the run that ended, to the phone that switched ``done`` off too.
        told: list[tuple[str, dict[str, str], JsonObject]] = [
            post for post in apple.posted if post[0].endswith(_ACTIVITY)
        ]
        assert told and _rows(told[-1]) == [(run.id, "done")]
    finally:
        await store.close()


async def test_a_schedules_notice_level_still_decides_whether_its_end_is_told_and_the_done_switch_comes_after(
    tmp_path: Path,
) -> None:
    """Item 1421: the level of a schedule (``never``, ``problems``, ``always``) decides whether the end of its run is an
    event at all, as it did; a phone that switched ``done`` off is not sent the ends that the level told."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_apns_device("p2", _OTHER_DEVICE, None, "sandbox", _NOW)
        await keep_push_settings(store, "p2", PushSettings(needs_you=True, ready=True, done=False))
        run: RunRecord = await _run(store, "schedule", None)
        # (level, how the run ended, what it needs, failures in a row): whether the scheduler emits the event.
        endings: list[tuple[str, str, str, int, bool]] = [
            ("never", "failed", "", 1, False),
            ("problems", "done", "", 1, False),
            ("problems", "failed", "", 1, True),
            ("problems", "failed", "", 2, False),
            ("always", "done", "", 1, True),
        ]
        emitted: int = 0
        for level, status, needs, in_row, told in endings:
            assert tells(level, status, needs, in_row) is told, (level, status)  # type: ignore[arg-type]  # the level's literal
            if told:
                emitted += 1
                data: JsonObject = {"schedule_id": "s", "run_id": run.id, "status": status, "result": "x"}
                await watch.on_event(_system(SCHEDULE_RUN_ENDED_EVENT, data), _NOW + emitted)
        assert emitted == 2
        assert len(_alerts_to(apple, _DEVICE)) == 2 and _alerts_to(apple, _OTHER_DEVICE) == []
    finally:
        await store.close()


async def test_an_alert_sets_the_badge_to_how_many_things_wait_on_the_owner_and_carries_nothing_more(
    tmp_path: Path,
) -> None:
    """Item 1422: the badge of the app is the count of what waits on the owner, a number in the alert's ``aps`` (the
    lock-screen approval's too); the lock-screen text is what it was, and the Live Activity carries no badge. A count
    that cannot be made leaves the alert as it was, and a watch with no count sets no badge."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        waiting: list[int] = [3]
        calls: list[int] = []

        async def count() -> int:
            calls.append(waiting[0])
            return waiting[0]

        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple), None, count)
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_apns_device("p2", _OTHER_DEVICE, None, "sandbox", _NOW)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        run: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW)
        ended: JsonObject = _alerts_to(apple, _DEVICE)[0]
        assert ended == {**alert_payload("A task is done", f"#/runs/{run.id}"), "aps": {**ended["aps"], "badge": 3}}  # type: ignore[dict-item]  # a JSON payload read back
        assert ended["aps"]["alert"] == {"title": "A task is done", "body": ALERT_BODY}  # type: ignore[index]  # a JSON payload read back
        # One count for an alert, whatever the number of phones; both phones read the same number.
        assert calls == [3] and _alerts_to(apple, _OTHER_DEVICE)[0]["aps"]["badge"] == 3  # type: ignore[index]  # a JSON payload read back
        # The lock screen's approval carries it too, and the count moves as what waits does.
        waiting[0] = 4
        step: JsonObject = {"approval_id": "apr_1", "severity": "low", "summary": "x", "args": {}}
        await watch.on_event(_event(run.id, "approval_requested", step), _NOW + 1)
        asked: JsonObject = _alerts_to(apple, _DEVICE)[-1]
        assert asked["aps"]["badge"] == 4 and asked["aps"]["category"] == APPROVAL_CATEGORY  # type: ignore[index]  # a JSON payload read back
        # No badge in a Live Activity's push.
        for url, headers, body in apple.posted:
            if headers["apns-push-type"] == "liveactivity":
                assert "badge" not in json.dumps(body), url

        # A count that cannot be made does not cost the alert.
        async def cannot() -> int:
            raise ApnsError("the count failed", {})

        broken = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple), None, cannot)
        before: int = len(_alerts_to(apple, _DEVICE))
        await broken.on_event(_event(run.id, "run_finished", {"status": "failed", "result": "x"}), _NOW + 2)
        assert len(_alerts_to(apple, _DEVICE)) == before + 1 and "badge" not in _alerts_to(apple, _DEVICE)[-1]["aps"]  # type: ignore[operator]  # a JSON payload read back

        # Nor does a stored record that cannot be read (pydantic's error, which is no DotError): the alert goes, with no
        # badge, and the watch goes on.
        class Options(FrozenModel):
            options: list[str]

        async def unreadable() -> int:
            return len(Options.model_validate({"options": "not a list"}).options)

        unread = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple), None, unreadable)
        await unread.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 2.5)
        assert len(_alerts_to(apple, _DEVICE)) == before + 2 and "badge" not in _alerts_to(apple, _DEVICE)[-1]["aps"]  # type: ignore[operator]  # a JSON payload read back
        # A watch given no count sets none.
        plain = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await plain.on_event(_event(run.id, "run_finished", {"status": "done", "result": "x"}), _NOW + 3)
        assert "badge" not in _alerts_to(apple, _DEVICE)[-1]["aps"]  # type: ignore[operator]  # a JSON payload read back
    finally:
        await store.close()


def test_the_badge_is_a_number_put_into_an_alert_and_nothing_else_is_changed() -> None:
    alert: JsonObject = alert_payload("A task is done", "#/runs/run-1")
    assert badged(alert, 2) == {
        "aps": {"alert": {"title": "A task is done", "body": ALERT_BODY}, "sound": "default", "badge": 2},
        "route": "#/runs/run-1",
    }
    asked: JsonObject = approval_payload("Dot asks for approval", "#/runs/run-1", "apr_1")
    assert badged(asked, 0)["approval_id"] == "apr_1" and badged(asked, 0)["aps"]["badge"] == 0  # type: ignore[index]  # a JSON payload read back
    # The payload it was given is not changed.
    assert "badge" not in alert["aps"]  # type: ignore[operator]  # a JSON payload read back
    with pytest.raises(ApnsError, match="aps"):
        badged({"route": "#/"}, 1)


async def test_a_handoffs_alert_is_a_fixed_title_and_line_and_the_route_of_its_run_and_nothing_of_the_step(
    tmp_path: Path,
) -> None:
    """Item 1423: the lock-screen text of a hand-off, which Apple reads, is what it was: the fixed title, the fixed
    line, the run's route; never the host, the page or a credential."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        run: RunRecord = await _run(store, "chat", None)
        words: str = "Type hunter2 into the password field of https://bank.example/login?token=abc"
        step: JsonObject = {
            "approval_id": "apr_1", "severity": "handoff", "summary": words,
            "args": {"target": "https://bank.example/login?token=abc", "flags": ["credential"]},
        }  # fmt: skip
        await watch.on_event(_event(run.id, "approval_requested", step), _NOW)
        [alert] = _alerts_to(apple, _DEVICE)
        assert alert == {
            "aps": {"alert": {"title": "Dot needs your help", "body": ALERT_BODY}, "sound": "default"},
            "route": f"#/runs/{run.id}",
        }
        for leaked in ("hunter2", "bank.example", "token=abc", "password"):
            assert leaked not in json.dumps(alert), leaked
    finally:
        await store.close()


def test_the_start_of_a_live_activity_is_the_payload_apple_asks_for_with_a_fixed_alert_and_nothing_else_added() -> None:
    """Item 1425: ``event`` ``start``, the attributes' type and its attributes, the state it opens with, ``timestamp``,
    ``input-push-token`` (so that iOS 18 and later give the started activity a token of its own) and the alert Apple
    requires of a start, fixed words, with no sound; the sources that start at once are the ones the app names."""
    row = LiveState(
        runId="run-1", title="Leads", agent="Dot", status="working", kind="thinking", line="Thinking", startedAt=_NOW
    )
    state: FleetState = FleetState(active=1, rows=(row,))
    assert live_start_payload(state, _NOW) == {
        "aps": {
            "timestamp": int(_NOW),
            "event": "start",
            "content-state": state.model_dump(),
            "attributes-type": "DotFleetAttributes",
            "attributes": {"fleetId": "fleet"},
            "input-push-token": 1,
            "alert": {"title": START_TITLE, "body": ALERT_BODY},
            "stale-date": int(_NOW + 4 * 3600),
        }
    }
    assert ATTRIBUTES_TYPE == "DotFleetAttributes" and FLEET_ID == "fleet"
    aps: object = live_start_payload(state, _NOW)["aps"]
    assert isinstance(aps, dict) and "sound" not in aps and "sound" not in json.dumps(aps["alert"])
    # The one source that starts at once is the command that ended in the background, as the manager names it.
    assert {BACKGROUND_SOURCE} == START_AT_ONCE


async def _watch(store: Store, apple: _Apple, leases: PhoneLeases | None = None) -> ApnsWatch:
    return ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple), leases)


def _starts(apple: _Apple) -> list[tuple[str, dict[str, str], JsonObject]]:
    """The pushes that started a Live Activity, as posted."""
    return [post for post in apple.posted if post[2]["aps"].get("event") == "start"]  # type: ignore[union-attr]  # JSON


async def test_a_run_of_dots_own_starts_the_live_activity_on_a_phone_that_shows_none_once_in_ten_minutes(
    tmp_path: Path,
) -> None:
    """Item 1425: a phone that gave its push-to-start token, shows no activity and is not in the foreground is sent the
    start the moment a command that ended in the background begins to work: to the start token, at priority 10, with
    the alert Apple requires; a second start waits ten minutes and a run that is new; the same run is never started
    again."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch: ApnsWatch = await _watch(store, apple)
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await keep_live_start_token(store, "p1", _START)
        first: RunRecord = await _run(store, "background", None)
        await watch.on_event(_event(first.id, "run_started", {}), _NOW)
        [(url, headers, body)] = _starts(apple)
        assert url == f"https://api.sandbox.push.apple.com/3/device/{_START}"
        assert headers["apns-topic"] == "dev.dotagent.phone.push-type.liveactivity"
        assert headers["apns-push-type"] == "liveactivity" and headers["apns-priority"] == "10"
        aps: JsonObject = body["aps"]  # type: ignore[assignment]  # a JSON payload read back
        assert aps["event"] == "start" and aps["timestamp"] == int(_NOW) and aps["attributes"] == {"fleetId": "fleet"}
        assert aps["attributes-type"] == "DotFleetAttributes"
        assert aps["alert"] == {"title": START_TITLE, "body": ALERT_BODY} and "sound" not in aps
        assert _rows(_starts(apple)[0]) == [(first.id, "working")]
        # The same run, however often the rows are read, is not started for again, nor another within ten minutes.
        await watch.flush(_NOW + START_EVERY_S - 1)
        second: RunRecord = await _run(store, "background", None)
        await watch.on_event(_event(second.id, "run_started", {}), _NOW + 60)
        assert len(_starts(apple)) == 1
        # Past the ten minutes the run that is new earns a start, with both rows; the old one alone would not.
        await watch.flush(_NOW + START_EVERY_S)
        assert len(_starts(apple)) == 2 and {run for run, _ in _rows(_starts(apple)[1])} == {first.id, second.id}
        await watch.flush(_NOW + 2 * START_EVERY_S + 1)
        assert len(_starts(apple)) == 2
        # An activity that starts sends its token: the rows go to it as they always did, and no start is sent again.
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW + START_EVERY_S + 1)
        await watch.on_event(_registered(), _NOW + START_EVERY_S + 1)
        assert apple.posted[-1][0].endswith(_ACTIVITY) and len(_starts(apple)) == 2
    finally:
        await store.close()


async def test_a_run_the_owner_started_starts_one_only_after_thirty_seconds_and_never_where_the_phone_shows_dot(
    tmp_path: Path,
) -> None:
    """Item 1425: the owner's own run waits until it has worked :data:`START_AFTER_S`; a phone in the foreground (its
    lease, item 1420), one that shows an activity, one that gave no token and a run that is no row of the activity (a
    schedule's, which nobody follows) are sent no start."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        clock: list[float] = [_NOW]
        leases = PhoneLeases(lambda: clock[0])
        apple = _Apple({})
        watch: ApnsWatch = await _watch(store, apple, leases)
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_apns_device("p2", _OTHER_DEVICE, None, "sandbox", _NOW)
        await store.set_apns_device("p3", "34" * 32, None, "sandbox", _NOW)
        await keep_live_start_token(store, "p1", _START)
        await keep_live_start_token(store, "p2", "ab" * 40)
        # p3 never gave a token; p2 shows an activity already.
        run: RunRecord = await _run(store, "ui", None)
        began: float = run.created_at
        await store.set_live_activity(FLEET_ID, "p2", _ACTIVITY, began)
        clock[0] = began
        leases.renew("p1", "foreground", None, 90.0)
        await watch.on_event(_event(run.id, "run_started", {}), began)
        await watch.flush(began + START_AFTER_S - 1)
        assert _starts(apple) == []
        # Thirty seconds of work, but the phone is in the foreground: no start.
        await watch.flush(began + START_AFTER_S + 6)
        assert _starts(apple) == []
        # The app leaves: the next look starts it, on p1 alone (p2 shows one, p3 has no token).
        leases.renew("p1", "background", None, None)
        await watch.flush(began + START_AFTER_S + 12)
        [(url, _, _)] = _starts(apple)
        assert url.endswith(_START)
        # A schedule's run is no row of the activity: nothing to start for.
        scheduled: RunRecord = await _run(store, "schedule", None)
        await watch.on_event(_event(scheduled.id, "run_started", {}), began + START_AFTER_S + 13)
        await watch.flush(scheduled.created_at + START_AFTER_S + START_EVERY_S + 10)
        assert len(_starts(apple)) == 1
        assert await live_start_token_of(store, "p1") == _START
    finally:
        await store.close()


async def test_a_start_token_that_apns_calls_gone_is_forgotten_and_a_push_that_fails_is_not_asked_again_for_ten_minutes(
    tmp_path: Path,
) -> None:
    """Item 1425: a token APNs refuses as gone is forgotten (the phone's device is not: its alerts go on), and one start
    that could not be sent counts as the try for ten minutes, so a failing push service is not asked every few seconds;
    the runs count only once a start was sent for them, so when the ten minutes are over it is asked again for them."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({_START: ApnsReply(status=410, reason="Unregistered")})
        watch: ApnsWatch = await _watch(store, apple)
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await keep_live_start_token(store, "p1", _START)
        run: RunRecord = await _run(store, "background", None)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW)
        assert len(_starts(apple)) == 1 and await live_start_token_of(store, "p1") is None
        assert [device.phone_id for device in await store.list_apns_devices()] == ["p1"]

        class _Down:
            """APNs that cannot be reached, until it is told it can."""

            posts: int = 0
            reachable: bool = False

            async def post(self, url: str, headers: Mapping[str, str], body: bytes) -> ApnsReply:
                self.posts += 1
                if not self.reachable:
                    raise ApnsError("APNs could not be reached", {"url": url})
                return ApnsReply(status=200, reason="")

        down = _Down()
        failing = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), down))
        await keep_live_start_token(store, "p1", _START)
        await failing.on_event(_event(run.id, "run_started", {}), _NOW)
        for later in (1, 6, 60, START_EVERY_S - 1):
            await failing.flush(_NOW + later)
        assert down.posts == 1
        # The ten minutes are over and nothing was sent for the run: it is tried again, and again while it fails.
        await failing.flush(_NOW + START_EVERY_S + 6)
        assert down.posts == 2
        await failing.flush(_NOW + START_EVERY_S + 12)
        assert down.posts == 2
        # Once a start went out for it, it is not started again, however long it works.
        down.reachable = True
        await failing.flush(_NOW + 2 * START_EVERY_S + 12)
        assert down.posts == 3
        for later in (3 * START_EVERY_S, 4 * START_EVERY_S):
            await failing.flush(_NOW + later)
        assert down.posts == 3
    finally:
        await store.close()

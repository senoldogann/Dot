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
from dotagent.config import ApnsConfig, AppConfig, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject
from dotagent.events import SYSTEM_RUN_ID
from dotagent.notify.apns import (
    ALERT_BODY,
    APPROVAL_CATEGORY,
    DISMISS_AFTER_S,
    FLEET_ID,
    ApnsReply,
    ApnsSender,
    FleetState,
    LiveState,
    alert_payload,
    apns_set,
    live_end_payload,
    live_update_payload,
    load_key,
    provider_token,
    short_title,
)
from dotagent.notify.apns_watch import (
    ACTIVITY_LIFE_S,
    LIVE_REGISTERED_EVENT,
    PROGRESS_EVERY_S,
    TOLD_TITLE,
    ApnsWatch,
    follows_live,
)
from dotagent.notify.live_now import LiveNow, tool_now
from dotagent.notify.owner import OWNER_TOLD_EVENT
from dotagent.runtime import _guarded_files
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord

_NOW: float = 1_700_000_000.0
_DEVICE: str = "ab" * 32
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
    the activity is not ended for having no row yet, and the run's start fills it. And a daemon that starts again ends
    the activity it finds kept with no row (the pre-push review of item 1093)."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, None, "sandbox", _NOW)
        await store.set_live_activity(FLEET_ID, "p1", _ACTIVITY, _NOW)
        await watch.on_event(_registered(), _NOW)
        assert apple.posted == [] and len(await store.live_activities_of(FLEET_ID)) == 1
        run: RunRecord = await _run(store, "chat", None)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW + 1)
        assert apple.posted[-1][0].endswith(_ACTIVITY) and _rows(apple.posted[-1]) == [(run.id, "working")]
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
        posted: int = len(apple.posted)
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

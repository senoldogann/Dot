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

from dotagent.config import ApnsConfig
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject
from dotagent.events import SYSTEM_RUN_ID
from dotagent.notify.apns import (
    ALERT_BODY,
    ApnsReply,
    ApnsSender,
    LiveState,
    alert_payload,
    live_start_payload,
    load_key,
    provider_token,
    sender_of,
    short_title,
)
from dotagent.notify.apns_watch import TOLD_TITLE, ApnsWatch, follows_live
from dotagent.notify.owner import OWNER_TOLD_EVENT
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
    assert sender_of(_CONFIG.model_copy(update={"key_path": ""}), _Apple({})) is None


def test_an_alert_carries_its_title_and_a_fixed_line_and_a_live_activity_a_short_title() -> None:
    assert alert_payload("A task is done", "#/runs/run-1") == {
        "aps": {"alert": {"title": "A task is done", "body": ALERT_BODY}, "sound": "default"},
        "route": "#/runs/run-1",
    }
    assert short_title("  Find   ten leads\nfor the launch ") == "Find ten leads for the launch"
    assert short_title("x" * 80) == "x" * 39 + "…"
    state = LiveState(title="Leads", status="working", line="Working", startedAt=_NOW)
    aps: object = live_start_payload("run-1", "Dot", state, _NOW)["aps"]
    assert isinstance(aps, dict)
    assert aps["event"] == "start" and aps["attributes-type"] == "DotRunAttributes"
    assert aps["attributes"] == {"runId": "run-1", "agentName": "Dot"}
    assert aps["stale-date"] > _NOW


async def _run(store: Store, source: str, parent: str | None) -> RunRecord:
    thread = await store.create_thread("Find ten leads for the launch", source)
    return await store.create_run(
        thread.id, "goal", "task", "m:m", parent, source, "medium", "standard", None, "ask", False, None, None, None
    )


def _event(run_id: str, kind: str, data: JsonObject) -> EventRecord:
    return EventRecord(id=1, run_id=run_id, ts=_NOW, kind=kind, data=data)


async def test_a_run_of_the_owners_starts_updates_and_ends_its_live_activity(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, _START, "sandbox", _NOW)
        run: RunRecord = await _run(store, "chat", None)
        assert follows_live(run)
        await watch.on_event(_event(run.id, "run_started", {}), _NOW)
        url, headers, start = apple.posted[-1]
        assert url == f"https://api.sandbox.push.apple.com/3/device/{_START}"
        assert headers["apns-topic"] == "dev.dotagent.phone.push-type.liveactivity"
        assert headers["apns-push-type"] == "liveactivity" and headers["authorization"].startswith("bearer ")
        assert start["aps"]["content-state"]["title"] == "Find ten leads for the launch"  # type: ignore[index]  # a JSON payload read back
        await store.set_live_activity(run.id, "p1", _ACTIVITY, _NOW)
        await watch.on_event(_event(run.id, "approval_requested", {"severity": "approval", "summary": "Send it"}), _NOW)
        alert_url, alert_headers, alert = apple.posted[-2]
        assert alert_url.endswith(_DEVICE) and alert_headers["apns-topic"] == "dev.dotagent.phone"
        assert alert["aps"]["alert"]["body"] == ALERT_BODY and "Send it" not in json.dumps(alert)  # type: ignore[index]  # a JSON payload read back
        assert apple.posted[-1][0].endswith(_ACTIVITY)
        assert apple.posted[-1][2]["aps"]["content-state"]["status"] == "waiting"  # type: ignore[index]  # a JSON payload read back
        await watch.on_event(_event(run.id, "run_finished", {"status": "done", "result": "Ten leads"}), _NOW)
        ended: JsonObject = apple.posted[-1][2]
        assert ended["aps"]["event"] == "end" and ended["aps"]["content-state"]["status"] == "done"  # type: ignore[index]  # a JSON payload read back
        assert await store.live_activities_of(run.id) == []
    finally:
        await store.close()


async def test_a_helpers_or_a_schedules_run_has_no_live_activity_and_a_gone_device_is_forgotten(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        apple = _Apple({_START: ApnsReply(status=410, reason="Unregistered")})
        watch = ApnsWatch(store, ApnsSender(_CONFIG, ec.generate_private_key(ec.SECP256R1()), apple))
        await store.set_apns_device("p1", _DEVICE, _START, "sandbox", _NOW)
        parent: RunRecord = await _run(store, "chat", None)
        for run in (await _run(store, "schedule", None), await _run(store, "chat", parent.id)):
            assert not follows_live(run)
            await watch.on_event(_event(run.id, "run_started", {}), _NOW)
        assert apple.posted == []
        await watch.on_event(_event(parent.id, "run_started", {}), _NOW)
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

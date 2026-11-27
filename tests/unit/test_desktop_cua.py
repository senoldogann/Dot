"""cua-driver, the opt-in background driver (item 2007): what Dot reads of its answers, and the adapter that starts
it and speaks to it, against a stand-in on a socket of its own (``tests/fixtures/fake_cua_driver.py``): no real
driver and no screen."""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import cua
from dotagent.desktop.cua import CuaBinary, CuaDriver, CuaFrame
from dotagent.desktop.types import Point, Rect

_FAKE: Path = Path(__file__).resolve().parents[1] / "fixtures" / "fake_cua_driver.py"


def test_a_point_is_placed_in_the_pixels_of_the_windows_last_capture() -> None:
    frame: CuaFrame = cua.frame_of(
        {"window_bounds": {"x": 535.0, "y": 154.0, "width": 640.0, "height": 548.0}, "screenshot_width": 1280}
    )
    assert cua.pixel_of(Point(625.0, 246.0), frame) == (180, 184)
    small: CuaFrame = CuaFrame(bounds=Rect(535.0, 154.0, 640.0, 548.0), width_px=320)
    assert cua.pixel_of(Point(625.0, 246.0), small) == (45, 46)
    with pytest.raises(DesktopError, match="outside the window"):
        cua.pixel_of(Point(10.0, 10.0), frame)
    with pytest.raises(DesktopError, match="no pixel frame"):
        cua.frame_of({"window_bounds": {"x": 0, "y": 0, "width": 10, "height": 10}})


def test_what_the_driver_answers_is_read_and_its_refusals_raise() -> None:
    done: cua.CuaResult = cua.result_of(
        {
            "id": 3,
            "result": {
                "content": [{"type": "text", "text": "Pressed  tab"}],
                "structuredContent": {"route": "synthetic_events", "effect": "confirmed"},
            },
        },
        "press_key",
    )
    assert (done.text, done.route, done.effect, done.is_error) == (
        "Pressed tab",
        "synthetic_events",
        "confirmed",
        False,
    )
    failed: cua.CuaResult = cua.result_of({"id": 4, "result": {"content": [], "isError": True}}, "click")
    assert failed.is_error and failed.route is None
    with pytest.raises(DesktopError, match="refused"):
        cua.result_of({"id": 5, "error": {"code": -32601, "message": "no such tool"}}, "click")


def test_only_the_measured_version_signed_by_cua_ai_is_used() -> None:
    path: Path = Path(cua.DEFAULT_PATH)
    assert cua.refusal_of(CuaBinary(path, cua.PINNED_VERSION, cua.TEAM_ID)) is None
    newer: str | None = cua.refusal_of(CuaBinary(path, "0.63.2", cua.TEAM_ID))
    assert newer is not None and cua.PINNED_VERSION in newer and cua.INSTALL_COMMAND in newer
    unsigned: str | None = cua.refusal_of(CuaBinary(path, cua.PINNED_VERSION, None))
    assert unsigned is not None and "not signed by Cua AI" in unsigned
    assert cua.team_of("Identifier=com.trycua.driver\nTeamIdentifier=YCK386LBJ7\n") == "YCK386LBJ7"
    assert cua.team_of("TeamIdentifier=not set\n") is None
    assert cua.version_of('{"binary_version": "0.28.2"}') == "0.28.2"
    assert "CUA_DRIVER_RS_VERSION=0.28.2" in cua.INSTALL_COMMAND and "--no-connect" in cua.INSTALL_COMMAND


def test_a_scroll_is_counted_in_wheel_notches_and_the_setting_is_off_by_default() -> None:
    assert cua.notches_of(400) == 20 and cua.notches_of(5) == 1 and cua.notches_of(20000) == cua.MAX_NOTCHES
    assert "z" in cua.KEYS and "f12" in cua.KEYS and "forwarddelete" not in cua.KEYS
    assert load_default_config().desktop.background_driver == "off"
    assert cua.starter_of(load_default_config().desktop) is None


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An executable that runs the stand-in, and its log of calls."""
    program: Path = tmp_path / "cua-driver"
    program.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_FAKE}" "$@"\n', encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("FAKE_CUA_LOG", str(tmp_path / "calls.jsonl"))
    return program


@pytest.fixture
def driver(fake: Path) -> Iterator[CuaDriver]:
    started: CuaDriver = CuaDriver(fake, 15.0, 10.0)
    yield started
    started.close()


def _calls(fake: Path) -> list[dict[str, object]]:
    log: Path = fake.parent / "calls.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_the_driver_runs_embedded_without_its_overlay_or_telemetry_and_answers_a_call(
    fake: Path, driver: CuaDriver
) -> None:
    result: cua.CuaResult = driver.call("click", cua.click_args(42, 7, (180, 184), "left", 1))
    assert result.text == "did click" and result.route == "synthetic_events"
    [call] = _calls(fake)
    assert call["tool"] == "click"
    assert call["arguments"] == {
        "pid": 42,
        "window_id": 7,
        "x": 180,
        "y": 184,
        "button": "left",
        "count": 1,
        "delivery_mode": "background",
        "session": "dot",
    }
    argv: object = call["argv"]
    assert isinstance(argv, list) and argv[:3] == ["serve", "--embedded", "--no-overlay"]
    assert call["env"] == {
        "CUA_DRIVER_EMBEDDED": "1",
        "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false",
        "CUA_DRIVER_RS_UPDATE_CHECK": "0",
    }
    # Its socket's folder is a short one in /tmp: a Unix socket's path has room for 104 bytes on macOS.
    assert len(str(driver._folder / "s")) < 40
    folder: Path = driver._folder
    driver.close()
    assert not driver.alive and not folder.exists()
    with pytest.raises(DesktopError, match="not running"):
        driver.call("click", {})


def test_an_error_of_a_tool_raises_and_keeps_the_driver(
    fake: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAKE_CUA_FAIL", "type_text")
    started: CuaDriver = CuaDriver(fake, 15.0, 10.0)
    try:
        with pytest.raises(DesktopError, match="could not do type_text"):
            started.call("type_text", cua.type_args(42, 7, "hi"))
        assert started.alive
        assert started.call("press_key", cua.key_args(42, 7, "tab", ())).text == "did press_key"
    finally:
        started.close()


def test_a_call_that_gets_no_answer_in_time_stops_the_driver_and_is_not_sent_again(
    fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CUA_HANG", "click")
    started: CuaDriver = CuaDriver(fake, 15.0, 0.5)
    try:
        with pytest.raises(DesktopError, match="did not answer click in time"):
            started.call("click", cua.click_args(42, 7, (1, 1), "left", 1))
        assert not started.alive
        assert [call["tool"] for call in _calls(fake)] == ["click"]
    finally:
        started.close()


def test_a_driver_that_is_missing_or_not_signed_by_cua_ai_is_never_run(
    fake: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The stand-in carries no signature: it is refused before it runs, not even asked its version (on Linux, where
    there is no codesign, the check itself fails, and it is not run either)."""
    with pytest.raises(DesktopError, match="not installed") as missing:
        cua.open_driver(tmp_path / "nowhere" / "cua-driver", 5.0, 5.0)
    assert missing.value.context["fix"] == cua.INSTALL_COMMAND
    with pytest.raises(DesktopError, match=r"not signed by Cua AI|could not be run"):
        cua.open_driver(fake, 5.0, 5.0)
    assert not (fake.parent / "calls.jsonl").exists()


def test_the_version_of_a_driver_signed_by_cua_ai_is_read_and_another_one_refused(
    fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cua, "signer_at", lambda path, timeout_s: cua.TEAM_ID)
    monkeypatch.setenv("FAKE_CUA_VERSION", "0.63.2")
    with pytest.raises(DesktopError, match=r"measured with 0\.28\.2 only"):
        cua.open_driver(fake, 5.0, 5.0)
    [ran] = _calls(fake)
    assert ran["tool"] == "manifest"


def test_a_long_text_is_cut_in_pieces() -> None:
    assert cua.chunks_of("abcdefg", 3) == ["abc", "def", "g"]
    assert cua.chunks_of("", 3) == []
    assert "".join(cua.chunks_of("ğüşiöç İI" * 30, cua.TEXT_CHUNK_CHARS)) == "ğüşiöç İI" * 30


def test_the_driver_is_not_given_the_secrets_of_dots_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-the-driver")
    monkeypatch.setenv("GH_TOKEN", "ghp-not-for-the-driver")
    given: dict[str, str] = cua._child_env()
    assert "OPENAI_API_KEY" not in given and "GH_TOKEN" not in given
    assert given["PATH"] == os.environ["PATH"] and given["CUA_DRIVER_EMBEDDED"] == "1"

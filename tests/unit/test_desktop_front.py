"""The front of the Mac goes back where it was when a run that acted on the desktop ends (item 702), and only when
nobody else moved it: not when the owner went to another application meanwhile, not when that application has quit,
not while another run acts on the desktop, and not when the setting is off.

A stand-in Mac: which application is in front, which run, and what is brought forward; nothing reads the real one.
"""

from pathlib import Path

import pytest

from dotagent.config import DesktopConfig, load_default_config
from dotagent.desktop import apps
from dotagent.desktop.session import DesktopSession, no_browser_chrome
from dotagent.desktop.types import AppInfo


def _app(pid: int, name: str) -> AppInfo:
    return AppInfo(
        pid=pid, name=name, bundle_id=f"com.example.{name}", bundle_path=f"/Applications/{name}.app", kind="regular",
        frontmost=False,
    )  # fmt: skip


_NOTES: AppInfo = _app(10, "Notes")
_FIGMA: AppInfo = _app(20, "Figma")
_MAIL: AppInfo = _app(30, "Mail")


class _Mac:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, front: AppInfo, running: tuple[AppInfo, ...]) -> None:
        self.front: int = front.pid
        self.running: tuple[AppInfo, ...] = running
        self.raised: list[str] = []
        monkeypatch.setattr(apps, "frontmost_pid", lambda: self.front)
        monkeypatch.setattr(apps, "list_apps", lambda: self.running)
        monkeypatch.setattr(apps, "activate", self._activate)

    def _activate(self, app: AppInfo, timeout_s: float) -> None:
        self.raised.append(app.name)
        self.front = app.pid


def _session(tmp_path: Path, give_back: bool = True) -> DesktopSession:
    config: DesktopConfig = load_default_config().desktop.model_copy(update={"give_front_back": give_back})
    return DesktopSession(config, tmp_path / "stop", no_browser_chrome)


async def _acts(session: DesktopSession, mac: _Mac, thread: str, brings: AppInfo) -> None:
    """What an act of ``thread`` notes around it, the act bringing ``brings`` to the front."""
    await session._note_front_before(thread)
    mac.front = brings.pid
    await session._note_front_left(thread)


async def test_the_application_the_run_took_the_front_from_comes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mac: _Mac = _Mac(monkeypatch, _NOTES, (_NOTES, _FIGMA))
    session: DesktopSession = _session(tmp_path)
    try:
        await _acts(session, mac, "chat", _FIGMA)
        await _acts(session, mac, "chat", _FIGMA)  # a second act keeps what was in front before the first
        await session.end_of_run("chat")
        assert mac.raised == ["Notes"] and mac.front == _NOTES.pid
        # Once given back, a second end does nothing.
        await session.end_of_run("chat")
        assert mac.raised == ["Notes"]
    finally:
        await session.close()


async def test_nothing_moves_when_the_owner_went_elsewhere_the_app_quit_or_the_setting_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mac: _Mac = _Mac(monkeypatch, _NOTES, (_NOTES, _FIGMA, _MAIL))
    session: DesktopSession = _session(tmp_path)
    try:
        await _acts(session, mac, "moved", _FIGMA)
        mac.front = _MAIL.pid  # the owner went to Mail
        await session.end_of_run("moved")
        mac.front = _NOTES.pid
        await _acts(session, mac, "quit", _FIGMA)
        mac.running = (_FIGMA, _MAIL)  # Notes has quit
        await session.end_of_run("quit")
        # A run that never acted on the desktop has nothing to give back.
        await session.end_of_run("never")
    finally:
        await session.close()
    assert mac.raised == []
    off: DesktopSession = _session(tmp_path, give_back=False)
    mac.running = (_NOTES, _FIGMA)
    mac.front = _NOTES.pid
    try:
        await _acts(off, mac, "chat", _FIGMA)
        await off.end_of_run("chat")
    finally:
        await off.close()
    assert mac.raised == []


async def test_no_front_is_taken_from_another_run_at_work_on_the_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mac: _Mac = _Mac(monkeypatch, _NOTES, (_NOTES, _FIGMA, _MAIL))
    session: DesktopSession = _session(tmp_path)
    try:
        await _acts(session, mac, "first", _FIGMA)
        await _acts(session, mac, "second", _MAIL)
        mac.front = _FIGMA.pid
        await session.end_of_run("first")
        assert mac.raised == []
        # Its own end, with no run left at work, gives back what was in front before its first act: Figma, which the
        # first run had brought forward, is not where it left the front (Mail), so nothing moves either.
        await session.end_of_run("second")
        assert mac.raised == []
    finally:
        await session.close()

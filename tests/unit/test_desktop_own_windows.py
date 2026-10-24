"""No look at Dot's own windows while the eye of Settings › Vault shows a secret (item 621). The eye puts the saved
password in a box of Dot's window, and the accessibility tree of a window gives the text of what it shows: a
``desktop_snapshot`` or a ``desktop_read`` of that window would hand the model the secret that no screenshot, no view of
a page and no result of a browser tool hands it. As a password manager is never read (``_refuse_secrets``), Dot's own
window is not, for as long as the secret may be on the screen (:mod:`dotagent.vault.shown`)."""

from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import screen
from dotagent.desktop.safety import shows_own_window
from dotagent.desktop.session import DesktopSession, no_browser_chrome
from dotagent.desktop.types import AppInfo, Rect, WindowInfo
from dotagent.desktop.views import ViewRequest
from dotagent.vault import shown

_BOUNDS: Rect = Rect(10, 10, 50, 50)
_DOT: AppInfo = AppInfo(
    pid=1, name="Dot", bundle_id="dev.dot.desktop", bundle_path="/Applications/Dot.app", kind="regular", frontmost=False
)
_CHROME: AppInfo = AppInfo(
    pid=2,
    name="Google Chrome",
    bundle_id="com.google.Chrome",
    bundle_path="/Applications/Chrome.app",
    kind="regular",
    frontmost=True,
)
_FINDER: AppInfo = AppInfo(
    pid=3,
    name="Finder",
    bundle_id="com.apple.finder",
    bundle_path="/System/Finder.app",
    kind="regular",
    frontmost=False,
)


def _window(app: AppInfo, title: str, on_screen: bool = True) -> WindowInfo:
    return WindowInfo(
        window_id=app.pid,
        pid=app.pid,
        owner=app.name,
        title=title,
        bounds=_BOUNDS,
        layer=0,
        alpha=1.0,
        on_screen=on_screen,
    )


def test_the_desktop_app_and_a_window_that_shows_the_web_ui_are_dots_own() -> None:
    ui: WindowInfo = _window(_CHROME, "Vault \N{MIDDLE DOT} Dot - Google Chrome")
    news: WindowInfo = _window(_CHROME, "News of the day - Google Chrome")
    # The desktop app is Dot's whatever its windows say.
    assert shows_own_window(_DOT.name, _DOT.bundle_id, _DOT.pid, ())
    assert shows_own_window(_CHROME.name, _CHROME.bundle_id, _CHROME.pid, (ui, news))
    assert not shows_own_window(_CHROME.name, _CHROME.bundle_id, _CHROME.pid, (news,))
    # A window of Dot's that is minimized or on another Space (the window list says it is not on the screen) counts all
    # the same, and a window of another application is not the application's.
    assert shows_own_window(_CHROME.name, _CHROME.bundle_id, _CHROME.pid, (_window(_CHROME, ui.title, False),))
    assert not shows_own_window(_CHROME.name, _CHROME.bundle_id, _CHROME.pid, (_window(_CHROME, news.title, False),))
    assert not shows_own_window(_FINDER.name, _FINDER.bundle_id, _FINDER.pid, (ui,))


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: tuple[WindowInfo, ...]) -> DesktopSession:
    """A session that reads no Mac: the permissions are granted, the running applications are Chrome and Finder, and
    the windows are those given. Listing the windows is noted."""
    session: DesktopSession = DesktopSession(load_default_config().desktop, tmp_path / "stop", no_browser_chrome)

    async def granted(job: object) -> None:
        return None

    monkeypatch.setattr(session, "_prepare", granted)
    monkeypatch.setattr(session, "_running", lambda: ((_CHROME, _FINDER), frozenset()))
    monkeypatch.setattr(screen, "windows_everywhere", lambda: windows)
    return session


_WINDOW: ViewRequest = ViewRequest(scope="window", ref=None, query=None, full=False, max_chars=2000)


async def test_no_look_at_a_window_of_dot_while_a_secret_may_be_on_the_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot and a read of the element of a window that shows the web UI are refused for as long as the eye may
    show a secret, and the refusal says why. The windows are listed only then."""
    session: DesktopSession = _session(tmp_path, monkeypatch, (_window(_CHROME, "Vault \N{MIDDLE DOT} Dot"),))
    monkeypatch.setattr(shown, "_until", 0.0)
    shown.note_shown()
    with pytest.raises(DesktopError, match="Dot's own window may show a secret"):
        await session.snapshot("t", "Google Chrome", _WINDOW)
    with pytest.raises(DesktopError, match="Dot's own window may show a secret"):
        await session.read("t", "Google Chrome", 3, 0, 100)
    await session.close()


async def test_no_look_at_a_window_of_dot_that_is_minimized_or_on_another_space_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The accessibility tree of an application lists its minimized windows and gives their text: where the window is
    does not decide, its being Dot's does."""
    away: WindowInfo = _window(_CHROME, "Vault \N{MIDDLE DOT} Dot", on_screen=False)
    session: DesktopSession = _session(tmp_path, monkeypatch, (away,))
    monkeypatch.setattr(shown, "_until", 0.0)
    shown.note_shown()
    with pytest.raises(DesktopError, match="Dot's own window may show a secret"):
        await session.snapshot("t", "Google Chrome", _WINDOW)
    with pytest.raises(DesktopError, match="Dot's own window may show a secret"):
        await session.read("t", "Google Chrome", 3, 0, 100)
    await session.close()


async def test_a_window_that_is_not_dots_is_looked_at_while_a_secret_is_on_the_screen_and_dots_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is for Dot's own windows and for the time the secret may be on the screen: Finder is read, and so is
    the window of Chrome once the secret is gone (here: as far as there is an element to read, which there is not)."""
    session: DesktopSession = _session(tmp_path, monkeypatch, (_window(_CHROME, "Vault \N{MIDDLE DOT} Dot"),))
    monkeypatch.setattr(shown, "_until", 0.0)
    shown.note_shown()
    await session._refuse_own_while_shown(_FINDER)
    with pytest.raises(DesktopError, match="not an element of Finder that this conversation has been shown"):
        await session.read("t", "Finder", 3, 0, 100)
    monkeypatch.setattr(shown, "_until", 0.0)
    listed: list[str] = []
    monkeypatch.setattr(screen, "windows_everywhere", lambda: listed.append("listed") or ())
    await session._refuse_own_while_shown(_CHROME)
    with pytest.raises(DesktopError, match="not an element of Google Chrome that this conversation has been shown"):
        await session.read("t", "Google Chrome", 3, 0, 100)
    assert listed == [], "no window is listed while nothing is on the screen"
    await session.close()

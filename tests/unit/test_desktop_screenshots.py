"""A screenshot blacks out the windows of password managers whatever language the Mac is in (item 555): a window is
known by the bundle id of its application, not only by the name the window server gives it, which is the application's
name in the Mac's language."""

import re
from pathlib import Path

import pytest
from PIL import Image

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import apps, capture, perms, screen, shots
from dotagent.desktop.render import render_screenshot_caption
from dotagent.desktop.safety import hidden_regions
from dotagent.desktop.types import DisplayInfo, Point, Rect, WindowInfo
from dotagent.vault import shown

_BOUNDS: Rect = Rect(10, 10, 50, 50)


def _window(pid: int, owner: str, on_screen: bool = True) -> WindowInfo:
    return WindowInfo(
        window_id=pid, pid=pid, owner=owner, title="", bounds=_BOUNDS, layer=0, alpha=1.0, on_screen=on_screen
    )


@pytest.mark.parametrize(
    ("owner", "bundle"),
    [
        ("Schlüsselbundverwaltung", "com.apple.keychainaccess"),
        ("Trousseaux d'accès", "com.apple.keychainaccess"),
        ("Passwörter", "com.apple.passwords"),
        ("Mots de passe", "com.apple.passwords"),
        ("Contraseñas", "com.apple.passwords"),
        ("Vault", "com.1password.1password"),
    ],
)
def test_a_password_manager_is_blacked_out_under_whatever_name_the_mac_gives_it(owner: str, bundle: str) -> None:
    """The audit's F31: the window server's name was all that was read, so on a Mac in German or French Keychain Access
    and Passwords were sent to the model as they were."""
    windows: tuple[WindowInfo, ...] = (_window(7, owner),)
    assert hidden_regions(windows, {7: bundle}) == (_BOUNDS,)
    assert hidden_regions(windows, {}) == ()


def test_only_what_is_on_screen_and_a_password_manager_is_blacked_out() -> None:
    windows: tuple[WindowInfo, ...] = (
        _window(1, "Notes"),
        _window(2, "Passwörter", on_screen=False),
        _window(3, "1Password"),
    )
    bundles: dict[int, str | None] = {1: "com.apple.Notes", 2: "com.apple.passwords", 3: None}
    # 1Password is known by its name as well when no bundle id is known.
    assert hidden_regions(windows, bundles) == (_BOUNDS,)
    assert hidden_regions(windows[:2], bundles) == ()


def test_a_screenshot_reads_the_bundle_id_of_the_windows_it_blacks_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perms, "require_screen_recording", lambda: None)
    monkeypatch.setattr(
        capture, "capture_native", lambda area, timeout_s: Image.new("RGB", (100, 100), (255, 255, 255))
    )
    monkeypatch.setattr(apps, "bundle_ids", lambda: {7: "com.apple.passwords"})
    picture, redacted = shots._picture_of(
        load_default_config().desktop, Rect(0, 0, 100, 100), (_window(7, "Passwörter"), _window(8, "Notes"))
    )
    assert redacted == 1
    assert picture.getpixel((30, 30)) == (0, 0, 0)
    assert picture.getpixel((80, 80)) == (255, 255, 255)


def _titled(pid: int, owner: str, title: str) -> WindowInfo:
    return WindowInfo(
        window_id=pid, pid=pid, owner=owner, title=title, bounds=_BOUNDS, layer=0, alpha=1.0, on_screen=True
    )


def test_dots_own_windows_are_blacked_out_only_while_a_secret_of_its_vault_may_be_on_the_screen() -> None:
    """The eye shows a saved password or card number in Dot's own window, which no password manager's rule covers: the
    desktop app's window, and a window of any application that shows the web UI (known by the title the UI gives its
    page), are left out of a screenshot of the Mac while a secret may be on the screen (item 621)."""
    app: WindowInfo = _titled(1, "Dot", "Dot")
    browser: WindowInfo = _titled(2, "Google Chrome", "Vault \N{MIDDLE DOT} Dot - Google Chrome")
    other: WindowInfo = _titled(3, "Google Chrome", "News of the day - Google Chrome")
    bundles: dict[int, str | None] = {1: "dev.dot.desktop", 2: "com.google.Chrome", 3: "com.google.Chrome"}
    assert hidden_regions((app, browser, other), bundles) == ()
    assert hidden_regions((app, browser, other), bundles, own=True) == (_BOUNDS, _BOUNDS)
    assert hidden_regions((other,), bundles, own=True) == ()
    # A password manager's window is blacked out whether or not Dot's are.
    manager: WindowInfo = _titled(4, "1Password", "Vault")
    assert hidden_regions((manager, other), {4: "com.1password.1password"}, own=True) == (_BOUNDS,)


def test_a_screenshot_leaves_out_dots_window_while_the_eye_shows_a_secret_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(perms, "require_screen_recording", lambda: None)
    monkeypatch.setattr(
        capture, "capture_native", lambda area, timeout_s: Image.new("RGB", (100, 100), (255, 255, 255))
    )
    monkeypatch.setattr(apps, "bundle_ids", lambda: {1: "dev.dot.desktop"})
    monkeypatch.setattr(shown, "_until", 0.0)
    windows: tuple[WindowInfo, ...] = (_titled(1, "Dot", "Dot"),)
    area: Rect = Rect(0, 0, 100, 100)
    picture, redacted = shots._picture_of(load_default_config().desktop, area, windows)
    assert redacted == 0 and picture.getpixel((30, 30)) == (255, 255, 255)
    shown.note_shown()
    picture, redacted = shots._picture_of(load_default_config().desktop, area, windows)
    assert redacted == 1 and picture.getpixel((30, 30)) == (0, 0, 0)


def test_a_secret_is_on_the_screen_for_the_time_the_page_shows_it_and_a_little_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now: list[float] = [1000.0]
    monkeypatch.setattr(shown.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(shown, "_until", 0.0)
    assert not shown.on_screen()
    shown.note_shown()
    assert shown.on_screen()
    now[0] += shown.SHOWN_FOR_S
    assert shown.on_screen(), "the page may still show it: the request and the timer take a while"
    now[0] += shown.COVERED_FOR_S
    assert not shown.on_screen()
    assert shown.COVERED_FOR_S > shown.SHOWN_FOR_S


def test_the_time_the_page_shows_a_secret_is_the_time_the_daemon_leaves_dots_windows_out_for() -> None:
    """The page hides a secret again by itself after ``SHOWN_FOR_MS``; the daemon's time is built on the same number."""
    page: str = (
        Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static" / "js" / "vaultforms.js"
    ).read_text(encoding="utf-8")
    found: re.Match[str] | None = re.search(r"const SHOWN_FOR_MS = ([0-9_]+);", page)
    assert found is not None
    assert int(found.group(1).replace("_", "")) == int(shown.SHOWN_FOR_S * 1000)


# The main display of a laptop as a run saw it: 1366 units wide, 888.299 high (item 744).
_LAPTOP: DisplayInfo = DisplayInfo(display_id=1, bounds=Rect(0, 0, 1366, 888.299), main=True)


@pytest.mark.parametrize(
    ("region", "clipped"),
    [
        (Rect(0, 0, 1600, 40), Rect(0, 0, 1366, 40)),
        (Rect(-100, -20, 300, 200), Rect(0, 0, 200, 180)),
        (Rect(1000, 800, 500, 500), Rect(1000, 800, 366, 88)),
        (Rect(10, 10, 100, 100), Rect(10, 10, 100, 100)),
        (Rect(0, 0, 1366, 888), Rect(0, 0, 1366, 888)),
        (Rect(1366, 0, 100, 100), None),
        (Rect(-300, 0, 200, 100), None),
        (Rect(0, 900, 100, 100), None),
    ],
)
def test_a_region_is_cut_to_the_screen_and_refused_only_when_none_of_it_is_on_it(
    region: Rect, clipped: Rect | None
) -> None:
    assert shots.clip_region(region, 1366, 888.299) == clipped


def test_a_zoom_that_leaves_the_screen_looks_at_what_is_on_it_and_says_what_was_cut() -> None:
    area, looked, note = shots._region_in_points(Rect(0, 0, 1600, 40), 1.0, _LAPTOP)
    assert (area, looked) == (Rect(0, 0, 1366, 40), Rect(0, 0, 1366, 40))
    assert note == "The region (0, 0) 1600x40 left the screen (1366x888.299); it was cut to (0, 0) 1366x40."
    # On a screen drawn smaller than it is (scale 0.5) the region is in units, the area in points.
    area, looked, note = shots._region_in_points(Rect(600, 400, 400, 400), 0.5, _LAPTOP)
    assert looked == Rect(600, 400, 83, 44)
    assert area == Rect(1200, 800, 166, 88)
    assert note.endswith("it was cut to (600, 400) 83x44.")
    assert shots._region_in_points(Rect(10, 10, 100, 100), 1.0, _LAPTOP)[2] == ""


def test_a_region_with_nothing_or_a_sliver_of_it_on_the_screen_fails() -> None:
    with pytest.raises(DesktopError, match="not on the screen"):
        shots._region_in_points(Rect(1400, 0, 100, 100), 1.0, _LAPTOP)
    with pytest.raises(DesktopError, match="only a sliver"):
        shots._region_in_points(Rect(1360, 0, 100, 100), 1.0, _LAPTOP)
    with pytest.raises(DesktopError, match="too small"):
        shots._region_in_points(Rect(0, 0, 10, 100), 1.0, _LAPTOP)


def test_a_zoomed_screenshot_of_a_region_that_left_the_screen_maps_its_pixels_from_the_cut_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(screen, "main_display", lambda: _LAPTOP)
    monkeypatch.setattr(screen, "windows_on_screen", lambda: ())
    monkeypatch.setattr(shots, "_picture_of", lambda config, area, windows: (Image.new("RGB", (1366, 40)), 0))
    monkeypatch.setattr(shots, "_pointer_units", lambda scale: Point(5, 6))
    monkeypatch.setattr(shots, "_frontmost_name", lambda windows: "Finder")
    monkeypatch.setattr(shots, "_text_of", lambda config, picture, area: ())
    view = shots.take_region(load_default_config().desktop, Rect(0, 0, 1600, 40))
    assert (view.left, view.top, view.zoom) == (0, 0, 1.0)
    assert view.clipped.startswith("The region (0, 0) 1600x40 left the screen")
    assert view.clipped in render_screenshot_caption(view, "n0nce")
    whole = shots.take_region(load_default_config().desktop, Rect(0, 0, 1366, 40))
    assert whole.clipped == "" and "left the screen" not in render_screenshot_caption(whole, "n0nce")

"""A screenshot blacks out the windows of password managers whatever language the Mac is in (item 555): a window is
known by the bundle id of its application, not only by the name the window server gives it, which is the application's
name in the Mac's language."""

import pytest
from PIL import Image

from dotagent.config import load_default_config
from dotagent.desktop import apps, capture, perms, shots
from dotagent.desktop.safety import hidden_regions
from dotagent.desktop.types import Rect, WindowInfo

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

"""The icon of an application of this Mac (Customize › Plugins), read from its bundle: the ``.icns`` file its
``Info.plist`` names, drawn as a PNG, once for each version of the file. Bundles are made here; Launch Services is asked
for real about Calendar, which every Mac has."""

import io
import plistlib
from pathlib import Path

import pytest
from PIL import Image

from dotagent.connections import app_icons, mac
from dotagent.connections.catalogue import FOLDER_APPS
from dotagent.core.errors import DesktopError

_CALENDAR: str = "com.apple.iCal"
_ONEDRIVE_MAC: str = "com.microsoft.OneDrive-mac"
_ONEDRIVE: str = "com.microsoft.OneDrive"


def _bundle(root: Path, info: dict[str, object] | None, icon_files: tuple[str, ...]) -> Path:
    """An application bundle in ``root`` with the ``Info.plist`` ``info`` (none when ``None``) and an icon of 512
    pixels under each of ``icon_files`` in its Resources."""
    bundle: Path = root / "Fixture.app"
    resources: Path = bundle / "Contents" / "Resources"
    resources.mkdir(parents=True)
    if info is not None:
        (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    for name in icon_files:
        Image.new("RGBA", (512, 512), (200, 30, 30, 255)).save(resources / name, format="ICNS")
    return bundle


def test_the_icon_is_the_icns_file_the_info_plist_names_with_or_without_its_suffix(tmp_path: Path) -> None:
    for name in ("AppIcon", "AppIcon.icns"):
        bundle: Path = _bundle(tmp_path / name, {"CFBundleIconFile": name}, ("AppIcon.icns",))
        assert app_icons.icon_file(bundle) == bundle / "Contents" / "Resources" / "AppIcon.icns"


def test_a_bundle_with_no_icon_file_to_find_has_none(tmp_path: Path) -> None:
    assert app_icons.icon_file(_bundle(tmp_path / "no-plist", None, ())) is None
    assert app_icons.icon_file(_bundle(tmp_path / "no-key", {"CFBundleName": "Fixture"}, ("AppIcon.icns",))) is None
    assert app_icons.icon_file(_bundle(tmp_path / "no-file", {"CFBundleIconFile": "Gone"}, ("AppIcon.icns",))) is None
    # A name that is a path reaches outside the Resources: it names no icon.
    assert app_icons.icon_file(_bundle(tmp_path / "path", {"CFBundleIconFile": "../Info"}, ("AppIcon.icns",))) is None
    assert app_icons.icon_file(_bundle(tmp_path / "blank", {"CFBundleIconFile": ""}, ("AppIcon.icns",))) is None
    unreadable: Path = _bundle(tmp_path / "unreadable", None, ())
    (unreadable / "Contents" / "Info.plist").write_bytes(b"<plist><dict>")
    assert app_icons.icon_file(unreadable) is None


def test_the_icon_is_drawn_as_a_png_no_wider_than_the_page_shows(tmp_path: Path) -> None:
    bundle: Path = _bundle(tmp_path, {"CFBundleIconFile": "AppIcon"}, ("AppIcon.icns",))
    icns: Path | None = app_icons.icon_file(bundle)
    assert icns is not None
    drawn: bytes = app_icons.icon_png(icns)
    with Image.open(io.BytesIO(drawn)) as picture:
        assert picture.format == "PNG"
        assert picture.size == (app_icons.ICON_PX, app_icons.ICON_PX)


def test_a_file_that_is_no_icon_is_refused_with_where_it_was(tmp_path: Path) -> None:
    not_an_icon: Path = tmp_path / "AppIcon.icns"
    not_an_icon.write_bytes(b"this is not an icon")
    with pytest.raises(DesktopError) as refused:
        app_icons.icon_png(not_an_icon)
    assert str(not_an_icon) in str(refused.value.context)


def test_launch_services_finds_calendar_and_a_bundle_id_nobody_has_is_not_installed() -> None:
    found: Path | None = mac.installed_app(_CALENDAR)
    assert found is not None and found.name == "Calendar.app"
    assert mac.installed_app("com.example.dot-tests.nobody") is None
    assert mac.installed_one_of(("com.example.dot-tests.nobody", _CALENDAR)) == (_CALENDAR, found)
    assert mac.installed_one_of(("com.example.dot-tests.nobody",)) is None


def test_onedrive_is_found_by_whichever_bundle_id_its_build_goes_by(monkeypatch: pytest.MonkeyPatch) -> None:
    onedrive = next(app for app in FOLDER_APPS if app.id == "onedrive")
    assert set(onedrive.bundle_ids) == {_ONEDRIVE_MAC, _ONEDRIVE}
    for installed in (_ONEDRIVE_MAC, _ONEDRIVE):
        where: Path = Path("/Applications/OneDrive.app")
        monkeypatch.setattr(
            mac,
            "installed_app",
            lambda bundle_id, installed=installed, where=where: where if bundle_id == installed else None,
        )
        assert mac.installed_one_of(onedrive.bundle_ids) == (installed, where)

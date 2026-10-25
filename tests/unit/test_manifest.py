"""The web app manifest (item 381): the identity a phone keeps for Dot once it is added to the home screen, the
colours it shows while Dot opens, the icons it draws (one maskable, for a launcher that rounds or crops them), and the
two shortcuts of its long press. These tests read the packaged files the way the platform does, with no runtime and no
image library: a PNG's size is in its IHDR header."""

import json
import re
import struct
from pathlib import Path
from typing import Any

from dotagent.server.app import STATIC_DIR

MANIFEST: Path = STATIC_DIR / "manifest.webmanifest"
PAGE: Path = STATIC_DIR / "index.html"
OFFLINE: Path = STATIC_DIR / "offline.html"
SIZE: re.Pattern[str] = re.compile(r"^(?P<width>\d+)x(?P<height>\d+)$")
TOUCH_LINK: re.Pattern[str] = re.compile(
    r'<link rel="apple-touch-icon" sizes="(?P<sizes>\d+x\d+)" href="(?P<href>[^"]+)">'
)
LIGHT_THEME_COLOUR: re.Pattern[str] = re.compile(
    r'<meta name="theme-color" content="(?P<colour>#[0-9a-fA-F]{3,8})" media="\(prefers-color-scheme: light\)">'
)
PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"
# The first chunk of a PNG, which holds its width and height as two unsigned 32-bit big-endian numbers.
IHDR: struct.Struct = struct.Struct(">II")
_IHDR_AT: int = 16


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def light_theme_colour() -> str:
    """The colour the page gives the browser's bar in the light scheme (``index.html``)."""
    found: re.Match[str] | None = LIGHT_THEME_COLOUR.search(PAGE.read_text(encoding="utf-8"))
    assert found is not None, "index.html names no theme colour for the light scheme"
    return found["colour"]


def png_size(path: Path) -> tuple[int, int]:
    """The pixel width and height a PNG file declares in its IHDR chunk."""
    header: bytes = path.read_bytes()[: _IHDR_AT + IHDR.size]
    assert header.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG"
    assert len(header) == _IHDR_AT + IHDR.size and header[12:16] == b"IHDR", f"{path.name} has no IHDR chunk"
    return IHDR.unpack(header[_IHDR_AT : _IHDR_AT + IHDR.size])


def icons_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every icon entry of the manifest: the app's own and those of its shortcuts."""
    entries: list[dict[str, Any]] = list(data["icons"])
    for shortcut in data["shortcuts"]:
        entries.extend(shortcut.get("icons", []))
    return entries


def test_the_manifest_names_the_app_and_the_page_s_light_colours() -> None:
    """The id is the address the platform keeps the app by, and the colours are the page's own ground in the light
    scheme: the bar and the screen between the tap and the first frame wear them, so a light flash or a strip of Dot's
    old amber never shows."""
    data: dict[str, Any] = manifest()
    assert data["id"] == "/"
    light: str = light_theme_colour().lower()
    assert data["theme_color"].lower() == light
    assert data["background_color"].lower() == light


def test_every_icon_the_manifest_names_is_a_file_of_the_size_it_claims() -> None:
    entries: list[dict[str, Any]] = icons_of(manifest())
    assert entries != []
    problems: list[str] = []
    for entry in entries:
        path: Path = STATIC_DIR / entry["src"]
        if not path.is_file():
            problems.append(f"{entry['src']} is not a file of the interface")
            continue
        claimed: re.Match[str] | None = SIZE.match(entry["sizes"])
        if claimed is None:
            continue
        size: tuple[int, int] = png_size(path)
        if size != (int(claimed["width"]), int(claimed["height"])):
            problems.append(f"{entry['src']} is {size[0]}x{size[1]} and the manifest says {entry['sizes']}")
    assert problems == [], problems


def test_the_page_names_the_home_screen_icons_ios_asks_for() -> None:
    """``apple-touch-icon``: iOS draws an added app with one of these (items 410 and 550), each of the sizes an iPhone
    or an iPad asks for, at an absolute path under the icons folder. The 180 px files at the root stay for the names
    iOS tries by itself when a page names none, and its ``-precomposed`` twin for older ones; the page kept for a Mac
    out of reach names the 180 px icon too."""
    links: list[tuple[str, str]] = [
        (found["sizes"], found["href"]) for found in TOUCH_LINK.finditer(PAGE.read_text(encoding="utf-8"))
    ]
    assert links == [
        ("180x180", "/icons/touch-icon-180.png"),
        ("167x167", "/icons/touch-icon-167.png"),
        ("152x152", "/icons/touch-icon-152.png"),
        ("120x120", "/icons/touch-icon-120.png"),
    ]
    for sizes, href in links:
        assert href.startswith("/"), href
        width, height = sizes.split("x")
        assert png_size(STATIC_DIR / href.removeprefix("/")) == (int(width), int(height)), href
    for name in (
        "apple-touch-icon.png",
        "apple-touch-icon-precomposed.png",
        "apple-touch-icon-180x180.png",
        "apple-touch-icon-180x180-precomposed.png",
    ):
        assert png_size(STATIC_DIR / name) == (180, 180), name
    assert '<link rel="apple-touch-icon" sizes="180x180" href="/icons/touch-icon-180.png">' in OFFLINE.read_text(
        encoding="utf-8"
    )


def test_at_least_one_icon_is_maskable() -> None:
    """A launcher that rounds or crops the icon draws the maskable one, which keeps its mark inside the safe circle."""
    maskable: list[dict[str, Any]] = [entry for entry in icons_of(manifest()) if entry.get("purpose") == "maskable"]
    assert maskable != []
    assert all(entry["sizes"] == "512x512" for entry in maskable), maskable


def test_the_shortcuts_open_a_new_chat_and_the_settings() -> None:
    """A long press on Dot's icon on the home screen offers the two pages a phone is most opened for."""
    shortcuts: dict[str, dict[str, Any]] = {shortcut["name"]: shortcut for shortcut in manifest()["shortcuts"]}
    assert set(shortcuts) == {"New chat", "Settings"}
    assert shortcuts["New chat"]["short_name"] == "New chat"
    assert shortcuts["New chat"]["url"] == "/#/runs"
    assert shortcuts["Settings"]["short_name"] == "Settings"
    assert shortcuts["Settings"]["url"] == "/#/settings"


def test_every_icon_of_the_manifest_is_a_picture_a_phone_draws() -> None:
    """Item 661: iOS 26 opens what is added to the Home Screen as a web app and takes its icon from the manifest, where
    an SVG of "any" size outranks every picture and is not drawn: the owner's iPhone added Dot with the letter D. Only
    PNGs are named, the 180 px of the touch icons among them."""
    icons: list[dict[str, Any]] = icons_of(manifest())
    assert {entry["type"] for entry in icons} == {"image/png"}, icons
    assert all(entry["sizes"] != "any" for entry in icons), icons
    assert any(entry["sizes"] == "180x180" for entry in icons), icons

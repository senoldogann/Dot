"""Customize › Plugins (item 316): the directory of the services Dot works with. At least 25, each once, each saying
what Dot does with it; every application and MCP service of Connect apps' catalogues has a listing, so the page holds
all of them; a listing is tried exactly when one of its ways is a catalogue's, and an untried way says what it would
take; each mark is a logo file (a well-formed SVG that runs nothing and reaches nothing, named in the folder's LICENSE,
whose source and licence it says), an icon of the interface or letters; an application of the Mac names the application
whose own icon its mark wears once it is installed."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dotagent.connections.catalogue import FOLDER_APPS, MAC_APPS, WEB_APPS
from dotagent.connections.directory import PLUGINS, PluginListing, PluginWay, plugin_view, plugin_views
from dotagent.mcp.catalogue import MCP_SERVICES

_STATIC: Path = Path(__file__).parents[2] / "src" / "dotagent" / "server" / "static"
_LOGOS: Path = _STATIC / "icons" / "plugins"
_SVG: str = "{http://www.w3.org/2000/svg}"
_SECTIONS: tuple[str, ...] = (
    "popular",
    "passwords",
    "code",
    "design",
    "data",
    "research",
    "communication",
    "productivity",
    "business",
    "support",
    "mac",
)


def _icon_names() -> set[str]:
    """The names of the interface's icons, as icons.js's SHAPES has them."""
    text: str = (_STATIC / "js" / "icons.js").read_text(encoding="utf-8")
    return set(re.findall(r"^  \"?([A-Za-z][A-Za-z0-9]*)\"?: \[", text, re.MULTILINE))


def test_the_directory_lists_25_services_or_more_each_once_and_says_what_each_does() -> None:
    views = plugin_views()
    assert len(views) >= 25
    assert len({view.id for view in views}) == len(views)
    assert len({view.name for view in views}) == len(views)
    assert all(view.does.strip() and view.section in _SECTIONS for view in views)
    # The sections show in the page's order, each listing in the section it names.
    assert [view.section for view in views] == sorted((view.section for view in views), key=_SECTIONS.index)
    assert sum(view.tried for view in views) >= 25


def test_every_application_and_service_of_connect_apps_has_a_listing() -> None:
    reached: set[tuple[str, str | None]] = {(way.kind, way.ref) for listing in PLUGINS for way in listing.ways}
    assert {("mcp", service.id) for service in MCP_SERVICES} <= reached
    assert {("mac", app.id) for app in MAC_APPS} <= reached
    assert {("folder", app.id) for app in FOLDER_APPS} <= reached
    assert {("web", app.id) for app in WEB_APPS} <= reached


def test_a_listing_is_tried_when_one_of_its_ways_is_a_catalogue_s_and_an_untried_way_says_what_it_would_take() -> None:
    for view in plugin_views():
        assert view.tried == any(way.kind != "untried" for way in view.ways), view.id
        for way in view.ways:
            if way.kind == "untried":
                assert way.ref is None and way.how and way.data is None and way.does is None, view.id
            else:
                assert way.ref and way.how and way.data and way.does, view.id
    untried: list[str] = [view.id for view in plugin_views() if not view.tried]
    assert untried == ["vercel", "canva", "asana", "box"]


def test_a_way_no_catalogue_holds_is_refused() -> None:
    listing: PluginListing = PLUGINS[0].model_copy(update={"ways": (PluginWay(kind="mcp", ref="nowhere", how=None),)})
    with pytest.raises(LookupError, match="no service nowhere"):
        plugin_view(listing)
    silent: PluginListing = PLUGINS[0].model_copy(update={"ways": (PluginWay(kind="untried", ref=None, how=None),)})
    with pytest.raises(LookupError):
        plugin_view(silent)


# What an SVG that is shown as a picture must not hold: it would run something or load something.
_FORBIDDEN_TAGS: frozenset[str] = frozenset({"script", "foreignObject", "image", "use", "style", "a", "animate", "set"})
_FILL: re.Pattern[str] = re.compile(r"#[0-9A-Fa-f]{6}")
_LOGO_REFERENCE: re.Pattern[str] = re.compile(r"/icons/plugins/([A-Za-z0-9-]+\.svg)")


def _licence_paragraph(licence: str, start: str) -> str:
    return next(paragraph for paragraph in licence.split("\n\n") if paragraph.startswith(start))


def _logo_names(paragraph: str) -> set[str]:
    return set(re.findall(r"[a-z0-9-]+\.svg", paragraph))


def test_each_mark_is_a_logo_file_an_icon_of_the_interface_or_letters() -> None:
    icons: set[str] = _icon_names()
    logos: set[str] = set()
    for listing in PLUGINS:
        assert listing.colour is None or re.fullmatch(r"[0-9A-F]{6}", listing.colour), listing.id
        if listing.logo is not None:
            logos.add(listing.logo)
            # A logo is drawn in its own colours: the listing's colour is for a glyph or letters.
            assert listing.colour is None and listing.glyph is None and listing.monogram is None, listing.id
        elif listing.glyph is not None:
            assert listing.glyph in icons, listing.glyph
            assert listing.monogram is None, listing.id
        else:
            assert listing.monogram is not None and 1 <= len(listing.monogram) <= 2, listing.id
    # Every file of the folder is a mark some listing shows, and every listing's file is there.
    assert {file.name for file in _LOGOS.glob("*.svg")} == logos


def test_the_logo_files_are_svg_pictures_that_run_nothing_and_reach_nothing_each_named_in_the_licence() -> None:
    licence: str = (_LOGOS / "LICENSE").read_text(encoding="utf-8")
    assert "CC0 1.0" in licence and "trademarks of their owners" in licence
    full: set[str] = _logo_names(_licence_paragraph(licence, "gilbarbara/logos"))
    simple: set[str] = _logo_names(_licence_paragraph(licence, "Simple Icons"))
    files: set[str] = {file.name for file in _LOGOS.glob("*.svg")}
    # Each file is named once in the paragraph of its source; the files of the two sources do not overlap.
    assert full | simple == files and not full & simple
    for name in sorted(files):
        root: ET.Element = ET.fromstring((_LOGOS / name).read_text(encoding="utf-8"))
        assert root.tag == f"{_SVG}svg", name
        box: list[str] = (root.get("viewBox") or "").split()
        assert len(box) == 4 and all(float(number) >= 0 for number in box[2:]), name
        for element in root.iter():
            assert element.tag.removeprefix(_SVG) not in _FORBIDDEN_TAGS, (name, element.tag)
            for attribute, value in element.attrib.items():
                assert not attribute.startswith("on"), (name, attribute)
                if "href" in attribute or "url(" in value:
                    assert value.startswith("#") or value.startswith("url(#"), (name, attribute, value)
        if name in simple:
            # A Simple Icons symbol is one path in the brand's colour, which the file's fill gives it.
            assert [child.tag for child in root] == [f"{_SVG}path"], name
            assert _FILL.fullmatch(root.get("fill") or ""), name


def test_the_pages_script_names_only_logo_files_that_are_there() -> None:
    """The Accounts card and the first chat's card draw Google's and Microsoft's marks by file name."""
    named: set[str] = set()
    for script in sorted((_STATIC / "js").rglob("*.js")):
        named |= set(_LOGO_REFERENCE.findall(script.read_text(encoding="utf-8")))
    assert {"google-icon.svg", "microsoft-icon.svg"} <= named
    assert all((_LOGOS / name).is_file() for name in named), named


def test_a_listing_of_an_application_of_the_mac_names_it() -> None:
    apps: dict[str, str] = {app.id: app.name for app in (*MAC_APPS, *FOLDER_APPS)}
    for view in plugin_views():
        ways: list[str] = [way.ref for way in view.ways if way.kind in ("mac", "folder") and way.ref is not None]
        assert view.app == (ways[0] if ways else None), view.id
        assert view.app is None or view.app in apps, view.id
    # The ten applications of the Mac and OneDrive, each listed under its own id.
    assert sorted(view.id for view in plugin_views() if view.app is not None) == sorted(apps)

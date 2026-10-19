"""Customize › Plugins (item 316): the directory of the services Dot works with. At least 25, each once, each saying
what Dot does with it; every application and MCP service of Connect apps' catalogues has a listing, so the page holds
all of them; a listing is tried exactly when one of its ways is a catalogue's, and an untried way says what it would
take; each mark is a logo file of Simple Icons' (one path, named in the folder's LICENSE), an icon of the interface
or letters."""

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
_SECTIONS: tuple[str, ...] = ("popular", "communication", "development", "business", "productivity", "mac")


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


def test_each_mark_is_a_logo_of_one_path_an_icon_of_the_interface_or_letters() -> None:
    icons: set[str] = _icon_names()
    licence: str = (_LOGOS / "LICENSE").read_text(encoding="utf-8")
    logos: set[str] = set()
    for listing in PLUGINS:
        assert listing.colour is None or re.fullmatch(r"[0-9A-F]{6}", listing.colour), listing.id
        if listing.logo is not None:
            logos.add(listing.logo)
            root: ET.Element = ET.fromstring((_LOGOS / listing.logo).read_text(encoding="utf-8"))
            assert root.get("viewBox") == "0 0 24 24" and [child.tag for child in root] == [f"{_SVG}path"]
            assert listing.logo.removesuffix(".svg") in licence, listing.logo
        elif listing.glyph is not None:
            assert listing.glyph in icons, listing.glyph
        else:
            assert listing.monogram is not None and 1 <= len(listing.monogram) <= 2, listing.id
    # Every file of the folder is a mark some listing shows.
    assert {file.name for file in _LOGOS.glob("*.svg")} == logos

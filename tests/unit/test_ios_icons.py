"""The icons of Dot for iPhone (item 1461, scripts/export_ios_icons.py): the asset catalog holds exactly the icons the
phone draws, each the web app's own drawing (icons.js) or a Lucide shape the web app lacks, and an icon the web app
changes reaches the phone."""

from __future__ import annotations

import importlib.util
import json
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from types import ModuleType

import pytest

_ROOT: Path = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = _load("export_ios_icons")
_SVG: str = "{http://www.w3.org/2000/svg}"
_SWIFT: Path = _ROOT / "ios"


def _drawn(path: Path) -> list[tuple[str, dict[str, str]]]:
    """The shapes of an exported SVG, without the look the exporter gives them."""
    skin: set[str] = {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"}
    root: ElementTree.Element = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    return [
        (child.tag.removeprefix(_SVG), {name: value for name, value in child.attrib.items() if name not in skin})
        for child in root
    ]


def test_the_catalog_is_what_the_script_exports() -> None:
    """The web app's icons change and the phone's set must follow: this fails until `export_ios_icons.py` is run."""
    assert export.stale() == []


def test_every_icon_of_the_phone_is_the_web_apps_drawing_or_a_named_extra() -> None:
    web: dict[str, list[tuple[str, dict[str, str]]]] = export.web_shapes()
    names: list[str] = export.icon_names()
    assert len(names) >= 40
    for name in names:
        path: Path = export.CATALOG / f"{name}.imageset" / f"{name}.svg"
        assert path.exists(), f"{name} has no image in the catalog"
        expected: list[tuple[str, dict[str, str]]] | None = web.get(name, export.EXTRA.get(name))
        assert expected is not None, f"{name} is neither an icon of icons.js nor an extra"
        assert _drawn(path) == expected, f"{name} is not drawn as icons.js (or EXTRA) draws it"


def test_an_icon_the_web_app_draws_leaves_the_extras() -> None:
    """When icons.js gains one of the extras, the web app's drawing is the one the phone follows."""
    assert set(export.EXTRA) & set(export.web_shapes()) == set()


def test_the_extras_are_the_names_swift_can_draw() -> None:
    """An extra nothing draws is a copy nobody reads: it goes, with its case."""
    assert set(export.EXTRA) <= set(export.icon_names())


def test_the_stroke_and_the_frame_are_the_pages() -> None:
    """The web page draws an icon through its frame with a stroke that stays 1.75 on the grid whatever the frame
    (icons.js, item 849); the picture holds the half of the stroke a close frame leaves past its edge."""
    web: dict[str, list[tuple[str, dict[str, str]]]] = export.web_shapes()
    framed: str = export.svg(web["check"], export.web_frames()["check"])
    root: ElementTree.Element = ElementTree.fromstring(framed)
    x, y, width, height = (float(part) for part in root.attrib["viewBox"].split())
    stroke: float = float(root[0].attrib["stroke-width"])
    assert width == height
    # The check's frame is [3.27, 2.77, 17.45]: its stroke is 1.75 of the grid's 24 on that side, and the picture is the
    # frame with half of it on every edge.
    assert stroke == pytest.approx(1.75 * 17.45 / 24, abs=0.001)
    assert (x, y) == (pytest.approx(3.27 - stroke / 2, abs=0.001), pytest.approx(2.77 - stroke / 2, abs=0.001))
    assert width == pytest.approx(17.45 + stroke, abs=0.001)
    plain: ElementTree.Element = ElementTree.fromstring(export.svg(export.EXTRA["bot"], None))
    assert plain.attrib["viewBox"] == "-0.875 -0.875 25.75 25.75"
    assert plain[0].attrib["stroke-width"] == "1.75"
    # Black, so that the template takes the text's colour; no `currentColor`, which the system's renderer may not read.
    assert plain[0].attrib["stroke"] == "#000000" and "currentColor" not in export.svg(export.EXTRA["bot"], None)


def test_an_imageset_is_a_single_scale_template_that_keeps_its_vectors() -> None:
    contents: dict[str, object] = json.loads((export.CATALOG / "bot.imageset" / "Contents.json").read_text())
    assert contents["images"] == [{"filename": "bot.svg", "idiom": "universal"}]
    assert contents["properties"] == {"preserves-vector-representation": True, "template-rendering-intent": "template"}


def test_the_licence_ships_with_the_app() -> None:
    """The licence of Lucide stands in Settings › About › Licences, read from this copy of the web app's file."""
    assert export.LICENCE_COPY.read_text(encoding="utf-8") == export.LICENCE.read_text(encoding="utf-8")
    assert "ISC License" in export.LICENCE_COPY.read_text(encoding="utf-8")


def test_the_reader_of_icons_js_takes_its_literals_and_nothing_else() -> None:
    source: str = (
        "// a comment\n"
        "const SHAPES = {\n"
        '  ok: [["path", { d: "M1 1" }], ["circle", { cx: 12, cy: 1.5, r: "2" }]],\n'
        '  "quoted": [["line", { x1: 1, x2: 2, y1: 3, y2: 4 }]], /* and one more */\n'
        "};\n"
        "const FRAMES = {\n  ok: [-0.97, 2, 25.95],\n};\n"
    )
    assert export.web_shapes(source) == {
        "ok": [("path", {"d": "M1 1"}), ("circle", {"cx": "12", "cy": "1.5", "r": "2"})],
        "quoted": [("line", {"x1": "1", "x2": "2", "y1": "3", "y2": "4"})],
    }
    assert export.web_frames(source) == {"ok": (-0.97, 2.0, 25.95)}
    with pytest.raises(ValueError, match="no `const SHAPES =`"):
        export.web_shapes("const OTHER = {}")


def test_a_name_that_is_nowhere_is_refused_not_drawn_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "icon_names", lambda: ["bot", "no-such-icon"])
    with pytest.raises(ValueError, match=r"not an icon of icons\.js, and not in EXTRA"):
        export.render()


def test_the_swift_names_are_one_case_each_and_the_icon_view_draws_them() -> None:
    """`IconName` is the phone's list: a case is a line of its own, which the script reads, and `DotIcon` is the one
    place that turns a case into an image of the catalog."""
    names: list[str] = export.icon_names()
    assert names == sorted(names, key=str.lower), "keep the cases in order: a new one is a line, not a rearrangement"
    icon: str = (_SWIFT / "Dot" / "Features" / "DotIcon.swift").read_text(encoding="utf-8")
    assert "Image(name.rawValue)" in icon and ".renderingMode(.template)" in icon


def test_no_swift_file_names_an_icon_the_catalog_lacks() -> None:
    """A case is used as `.bot` of an `IconName`: every dotted name written next to a `DotIcon(`, `icon:` or `symbol:`
    is a case of the list."""
    cases: set[str] = set(export.icon_names())
    drawn: set[str] = set()
    pattern: re.Pattern[str] = re.compile(r"(?:DotIcon\(|icon: |symbol: |Icon: |glyph: )\.([A-Za-z][A-Za-z0-9]*)")
    for folder in ("Dot", "DotLiveActivity", "DotShare", "Shared"):
        for path in (_SWIFT / folder).rglob("*.swift"):
            drawn |= set(pattern.findall(path.read_text(encoding="utf-8")))
    assert drawn, "no Swift file draws an icon: the pattern of this test no longer finds them"
    assert drawn <= cases, f"drawn but not in IconName: {sorted(drawn - cases)}"

"""The icons of Dot for iPhone, exported from the web app's set (item 1461).

The web app draws its icons from `src/dotagent/server/static/js/icons.js`: Lucide's shapes (ISC licence, kept in
`LICENSE-Lucide.txt` beside it), each in the frame the page sees it through (item 849) and with the page's stroke. The
phone draws the same shapes: `DotIcon` shows an SVG image of the asset catalog `ios/Dot/Resources/Icons.xcassets` as a
template, so it takes the colour of the text round it. This writes that catalog, so that an icon the web app changes
reaches the phone by running it again, and `tests/unit/test_ios_icons.py` fails until it has been run.

Which icons: exactly the cases of `ios/Dot/Core/IconName.swift`, the phone's one list (a Swift file can only draw one
that is a case). A name icons.js has is read from there; a name it lacks must be in `EXTRA` below, Lucide shapes the
phone needs and the web app does not draw yet. The test fails when the web app gains one of them, so that its own
drawing replaces the copy here.

Usage:
    uv run python scripts/export_ios_icons.py           write the catalog and the licence beside it
    uv run python scripts/export_ios_icons.py --check   exit 1 when they are not what this would write
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
ICONS_JS: Path = ROOT / "src" / "dotagent" / "server" / "static" / "js" / "icons.js"
LICENCE: Path = ROOT / "src" / "dotagent" / "server" / "static" / "js" / "LICENSE-Lucide.txt"
NAMES: Path = ROOT / "ios" / "Dot" / "Core" / "IconName.swift"
CATALOG: Path = ROOT / "ios" / "Dot" / "Resources" / "Icons.xcassets"
LICENCE_COPY: Path = ROOT / "ios" / "Dot" / "Resources" / "Licences" / "LICENSE-Lucide.txt"

# The stroke of the web's icons on the 24 grid (`--icon-stroke` in app.css), before a frame scales it.
STROKE: float = 1.75
GRID: float = 24.0
# The size an icon's image says it is in points: what a menu or a toolbar draws it at; `DotIcon` sets its own.
IMAGE_POINTS: int = 20

# A shape: its SVG tag and its attributes, as icons.js writes them.
Shape = tuple[str, dict[str, str]]
# The square of the 24 grid an icon is seen through: x, y and its side.
Frame = tuple[float, float, float]

# Lucide shapes (v0.577.0, ISC licence: `LICENSE-Lucide.txt`) that the phone draws and icons.js does not have. A name is
# the one the Swift side uses (`loop` is Lucide's `repeat`, which Swift reserves).
EXTRA: dict[str, list[Shape]] = {
    "arrowUpToLine": [("path", {"d": "M5 3h14"}), ("path", {"d": "m18 13-6-6-6 6"}), ("path", {"d": "M12 7v14"})],
    "audioWaveform": [
        (
            "path",
            {
                "d": "M2 13a2 2 0 0 0 2-2V7a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0"
                "V4a2 2 0 0 1 4 0v13a2 2 0 0 0 4 0v-4a2 2 0 0 1 2-2"
            },
        )
    ],
    "bot": [
        ("path", {"d": "M12 8V4H8"}),
        ("rect", {"width": "16", "height": "12", "x": "4", "y": "8", "rx": "2"}),
        ("path", {"d": "M2 14h2"}),
        ("path", {"d": "M20 14h2"}),
        ("path", {"d": "M15 13v2"}),
        ("path", {"d": "M9 13v2"}),
    ],
    "chevronDown": [("path", {"d": "m6 9 6 6 6-6"})],
    "chevronUp": [("path", {"d": "m18 15-6-6-6 6"})],
    "chevronsUpDown": [("path", {"d": "m7 15 5 5 5-5"}), ("path", {"d": "m7 9 5-5 5 5"})],
    "circleAlert": [
        ("circle", {"cx": "12", "cy": "12", "r": "10"}),
        ("line", {"x1": "12", "x2": "12", "y1": "8", "y2": "12"}),
        ("line", {"x1": "12", "x2": "12.01", "y1": "16", "y2": "16"}),
    ],
    "circleCheck": [("circle", {"cx": "12", "cy": "12", "r": "10"}), ("path", {"d": "m9 12 2 2 4-4"})],
    "circleMinus": [("circle", {"cx": "12", "cy": "12", "r": "10"}), ("path", {"d": "M8 12h8"})],
    "circlePause": [
        ("circle", {"cx": "12", "cy": "12", "r": "10"}),
        ("line", {"x1": "10", "x2": "10", "y1": "15", "y2": "9"}),
        ("line", {"x1": "14", "x2": "14", "y1": "15", "y2": "9"}),
    ],
    "circleX": [
        ("circle", {"cx": "12", "cy": "12", "r": "10"}),
        ("path", {"d": "m15 9-6 6"}),
        ("path", {"d": "m9 9 6 6"}),
    ],
    "cpu": [
        ("path", {"d": "M12 20v2"}),
        ("path", {"d": "M12 2v2"}),
        ("path", {"d": "M17 20v2"}),
        ("path", {"d": "M17 2v2"}),
        ("path", {"d": "M2 12h2"}),
        ("path", {"d": "M2 17h2"}),
        ("path", {"d": "M2 7h2"}),
        ("path", {"d": "M20 12h2"}),
        ("path", {"d": "M20 17h2"}),
        ("path", {"d": "M20 7h2"}),
        ("path", {"d": "M7 20v2"}),
        ("path", {"d": "M7 2v2"}),
        ("rect", {"x": "4", "y": "4", "width": "16", "height": "16", "rx": "2"}),
        ("rect", {"x": "8", "y": "8", "width": "8", "height": "8", "rx": "1"}),
    ],
    "history": [
        ("path", {"d": "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"}),
        ("path", {"d": "M3 3v5h5"}),
        ("path", {"d": "M12 7v5l4 2"}),
    ],
    "inbox": [
        ("polyline", {"points": "22 12 16 12 14 15 10 15 8 12 2 12"}),
        (
            "path",
            {
                "d": "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89"
                "A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"
            },
        ),
    ],
    "laptop": [
        (
            "path",
            {
                "d": "M18 5a2 2 0 0 1 2 2v8.526a2 2 0 0 0 .212.897l1.068 2.127a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45"
                "l1.068-2.127A2 2 0 0 0 4 15.526V7a2 2 0 0 1 2-2z"
            },
        ),
        ("path", {"d": "M20.054 15.987H3.946"}),
    ],
    "listPlus": [
        ("path", {"d": "M16 5H3"}),
        ("path", {"d": "M11 12H3"}),
        ("path", {"d": "M16 19H3"}),
        ("path", {"d": "M18 9v6"}),
        ("path", {"d": "M21 12h-6"}),
    ],
    "loop": [
        ("path", {"d": "m17 2 4 4-4 4"}),
        ("path", {"d": "M3 11v-1a4 4 0 0 1 4-4h14"}),
        ("path", {"d": "m7 22-4-4 4-4"}),
        ("path", {"d": "M21 13v1a4 4 0 0 1-4 4H3"}),
    ],
    "mailOpen": [
        (
            "path",
            {
                "d": "M21.2 8.4c.5.38.8.97.8 1.6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2"
                "V10a2 2 0 0 1 .8-1.6l8-6a2 2 0 0 1 2.4 0l8 6Z"
            },
        ),
        ("path", {"d": "m22 10-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 10"}),
    ],
    "monitorOff": [
        ("path", {"d": "M12 17v4"}),
        ("path", {"d": "M17 17H4a2 2 0 0 1-2-2V5a2 2 0 0 1 1.184-1.826"}),
        ("path", {"d": "m2 2 20 20"}),
        ("path", {"d": "M8 21h8"}),
        ("path", {"d": "M8.656 3H20a2 2 0 0 1 2 2v10a2 2 0 0 1-.293 1.042"}),
    ],
    "network": [
        ("rect", {"x": "16", "y": "16", "width": "6", "height": "6", "rx": "1"}),
        ("rect", {"x": "2", "y": "16", "width": "6", "height": "6", "rx": "1"}),
        ("rect", {"x": "9", "y": "2", "width": "6", "height": "6", "rx": "1"}),
        ("path", {"d": "M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"}),
        ("path", {"d": "M12 12V8"}),
    ],
    "shieldCheck": [
        (
            "path",
            {
                "d": "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13"
                "V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72"
                "a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"
            },
        ),
        ("path", {"d": "m9 12 2 2 4-4"}),
    ],
    "wifiOff": [
        ("path", {"d": "M12 20h.01"}),
        ("path", {"d": "M8.5 16.429a5 5 0 0 1 7 0"}),
        ("path", {"d": "M5 12.859a10 10 0 0 1 5.17-2.69"}),
        ("path", {"d": "M19 12.859a10 10 0 0 0-2.007-1.523"}),
        ("path", {"d": "M2 8.82a15 15 0 0 1 4.177-2.643"}),
        ("path", {"d": "M22 8.82a15 15 0 0 0-11.288-3.764"}),
        ("path", {"d": "m2 2 20 20"}),
    ],
}


class _Reader:
    """Reads the literals icons.js is written in: arrays, objects with bare or quoted keys, strings and numbers, with
    comments and trailing commas between them. It reads no other JavaScript."""

    def __init__(self, text: str, at: int) -> None:
        self.text: str = text
        self.at: int = at

    def _skip(self) -> None:
        while self.at < len(self.text):
            if self.text[self.at].isspace():
                self.at += 1
            elif self.text.startswith("//", self.at):
                end: int = self.text.find("\n", self.at)
                self.at = len(self.text) if end < 0 else end
            elif self.text.startswith("/*", self.at):
                end = self.text.find("*/", self.at)
                if end < 0:
                    raise ValueError("an unclosed comment in icons.js")
                self.at = end + 2
            else:
                return

    def value(self) -> object:
        self._skip()
        char: str = self.text[self.at]
        if char == "[":
            return self._array()
        if char == "{":
            return self._object()
        if char in "\"'":
            return self._string()
        return self._number()

    def _array(self) -> list[object]:
        found: list[object] = []
        self.at += 1
        while True:
            self._skip()
            if self.text[self.at] == "]":
                self.at += 1
                return found
            found.append(self.value())
            self._skip()
            if self.text[self.at] == ",":
                self.at += 1

    def _object(self) -> dict[str, object]:
        found: dict[str, object] = {}
        self.at += 1
        while True:
            self._skip()
            if self.text[self.at] == "}":
                self.at += 1
                return found
            key: str = self._string() if self.text[self.at] in "\"'" else self._word()
            self._skip()
            if self.text[self.at] != ":":
                raise ValueError(f"icons.js: expected ':' after {key!r} at {self.at}")
            self.at += 1
            found[key] = self.value()
            self._skip()
            if self.text[self.at] == ",":
                self.at += 1

    def _string(self) -> str:
        quote: str = self.text[self.at]
        self.at += 1
        out: list[str] = []
        while self.text[self.at] != quote:
            if self.text[self.at] == "\\":
                self.at += 1
            out.append(self.text[self.at])
            self.at += 1
        self.at += 1
        return "".join(out)

    def _word(self) -> str:
        start: int = self.at
        while self.at < len(self.text) and (self.text[self.at].isalnum() or self.text[self.at] in "_$"):
            self.at += 1
        if start == self.at:
            raise ValueError(f"icons.js: no key at {start}: {self.text[start : start + 20]!r}")
        return self.text[start : self.at]

    def _number(self) -> float:
        start: int = self.at
        while self.at < len(self.text) and (self.text[self.at].isdigit() or self.text[self.at] in "+-.eE"):
            self.at += 1
        if start == self.at:
            raise ValueError(f"icons.js: no value at {start}: {self.text[start : start + 20]!r}")
        return float(self.text[start : self.at])


def _constant(text: str, name: str) -> object:
    """The literal `const <name> = ...;` of icons.js."""
    found: re.Match[str] | None = re.search(rf"^const {name} = ", text, re.MULTILINE)
    if found is None:
        raise ValueError(f"icons.js has no `const {name} =`")
    return _Reader(text, found.end()).value()


def _text(value: object) -> str:
    """An attribute as the SVG writes it: a whole number without its point."""
    if isinstance(value, float):
        return _number(value)
    return str(value)


def web_shapes(source: str | None = None) -> dict[str, list[Shape]]:
    """Every icon of the web app by its name, as its shapes: `{"check": [("path", {"d": "M20 6 9 17l-5-5"})]}`."""
    declared: object = _constant(ICONS_JS.read_text(encoding="utf-8") if source is None else source, "SHAPES")
    if not isinstance(declared, dict):
        raise ValueError("icons.js: SHAPES is not an object")
    shapes: dict[str, list[Shape]] = {}
    for name, drawing in declared.items():
        if not isinstance(drawing, list):
            raise ValueError(f"icons.js: {name} is not a list of shapes")
        parts: list[Shape] = []
        for shape in drawing:
            if not (
                isinstance(shape, list) and len(shape) == 2 and isinstance(shape[0], str) and isinstance(shape[1], dict)
            ):
                raise ValueError(f"icons.js: a shape of {name} is not [tag, attributes]")
            parts.append((shape[0], {key: _text(value) for key, value in shape[1].items()}))
        shapes[name] = parts
    return shapes


def web_frames(source: str | None = None) -> dict[str, Frame]:
    """The frame each icon is seen through (item 849): the square of the 24 grid it fills, by name."""
    declared: object = _constant(ICONS_JS.read_text(encoding="utf-8") if source is None else source, "FRAMES")
    if not isinstance(declared, dict):
        raise ValueError("icons.js: FRAMES is not an object")
    frames: dict[str, Frame] = {}
    for name, frame in declared.items():
        if not (isinstance(frame, list) and len(frame) == 3 and all(isinstance(part, float) for part in frame)):
            raise ValueError(f"icons.js: the frame of {name} is not [x, y, side]")
        frames[name] = (float(frame[0]), float(frame[1]), float(frame[2]))
    return frames


def icon_names() -> list[str]:
    """The cases of `IconName`, in the order the Swift file lists them."""
    cases: list[str] = re.findall(
        r"^\s*case ([A-Za-z][A-Za-z0-9]*)\s*$", NAMES.read_text(encoding="utf-8"), re.MULTILINE
    )
    if not cases:
        raise ValueError(f"{NAMES} lists no icon: its `case` lines are one name each")
    if len(set(cases)) != len(cases):
        raise ValueError(f"{NAMES} lists an icon twice")
    return cases


def _number(value: float) -> str:
    text: str = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def svg(shapes: list[Shape], frame: Frame | None) -> str:
    """One icon as an SVG document: the page's frame and stroke, drawn black (the app tints it), with the half of the
    stroke that a close frame leaves past its edge inside the picture, as the page draws it, not cut (item 849)."""
    x, y, side = frame if frame is not None else (0.0, 0.0, GRID)
    stroke: float = STROKE * side / GRID
    reach: float = stroke / 2
    box: str = " ".join(_number(part) for part in (x - reach, y - reach, side + stroke, side + stroke))
    skin: str = (
        f'fill="none" stroke="#000000" stroke-width="{_number(stroke)}" stroke-linecap="round" stroke-linejoin="round"'
    )
    lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{IMAGE_POINTS}" height="{IMAGE_POINTS}" viewBox="{box}">'
    ]
    for tag, attributes in shapes:
        drawn: str = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in attributes.items())
        lines.append(f"  <{tag}{drawn} {skin}/>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _json(value: object) -> str:
    return json.dumps(value, indent=2) + "\n"


def render() -> dict[Path, str]:
    """Every file of the catalog, and the licence copy, as this would write it."""
    web: dict[str, list[Shape]] = web_shapes()
    frames: dict[str, Frame] = web_frames()
    files: dict[Path, str] = {CATALOG / "Contents.json": _json({"info": {"author": "xcode", "version": 1}})}
    for name in icon_names():
        in_web: bool = name in web
        if in_web and name in EXTRA:
            raise ValueError(f"{name}: icons.js draws it now, so it leaves EXTRA and the web app's drawing is used")
        shapes: list[Shape] | None = web[name] if in_web else EXTRA.get(name)
        if shapes is None:
            raise ValueError(f"{name}: not an icon of icons.js, and not in EXTRA")
        folder: Path = CATALOG / f"{name}.imageset"
        files[folder / f"{name}.svg"] = svg(shapes, frames.get(name) if in_web else None)
        files[folder / "Contents.json"] = _json(
            {
                "images": [{"filename": f"{name}.svg", "idiom": "universal"}],
                "info": {"author": "xcode", "version": 1},
                "properties": {"preserves-vector-representation": True, "template-rendering-intent": "template"},
            }
        )
    files[LICENCE_COPY] = LICENCE.read_text(encoding="utf-8")
    return files


def stale() -> list[str]:
    """What is not as `render` says: a file that differs or is missing, and one in the catalog that nothing asks for."""
    wanted: dict[Path, str] = render()
    found: list[str] = []
    for path, text in wanted.items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            found.append(str(path.relative_to(ROOT)))
    if CATALOG.exists():
        for path in sorted(CATALOG.rglob("*")):
            if path.is_file() and path not in wanted:
                found.append(f"{path.relative_to(ROOT)} (nothing asks for it)")
    return found


def write() -> int:
    """Write the catalog and the licence; returns how many files changed."""
    wanted: dict[Path, str] = render()
    changed: int = 0
    if CATALOG.exists():
        for folder in sorted(CATALOG.glob("*.imageset")):
            if not any(path.parent == folder for path in wanted):
                shutil.rmtree(folder)
                changed += 1
    for path, text in wanted.items():
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Export the iPhone app's icons from icons.js."
    )
    parser.add_argument("--check", action="store_true", help="exit 1 when the catalog is not what this would write")
    arguments: argparse.Namespace = parser.parse_args(argv)
    if arguments.check:
        behind: list[str] = stale()
        for line in behind:
            print(line)
        if behind:
            print("run `uv run python scripts/export_ios_icons.py` and commit what it writes", file=sys.stderr)
        return 1 if behind else 0
    changed: int = write()
    print(f"{len(icon_names())} icons exported; {changed} files changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The visual system (item 50): the colours of the interface live in ``themes.css`` alone, and the other sheets draw
every control on the tokens: no literal colour, font size or radius outside the token blocks."""

import re
from pathlib import Path

import pytest

_CSS: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static" / "css"
_DRAWN: tuple[str, ...] = ("app.css", "shell.css", "run.css", "settings.css")
_COLOUR: re.Pattern[str] = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\bwhite\b|\bblack\b")
_FONT_SIZE: re.Pattern[str] = re.compile(r"font-size:\s*[0-9.]+px")
_RADIUS: re.Pattern[str] = re.compile(r"border-radius:\s*[0-9.]+px")


def _lines(name: str) -> list[tuple[int, str]]:
    return [(number, line) for number, line in enumerate((_CSS / name).read_text(encoding="utf-8").splitlines(), 1)]


@pytest.mark.parametrize("name", _DRAWN)
def test_no_literal_colour_outside_the_themes(name: str) -> None:
    hits: list[str] = [
        f"{name}:{n}: {line.strip()}" for n, line in _lines(name) if _COLOUR.search(line) and "white-space" not in line
    ]
    assert hits == [], hits


@pytest.mark.parametrize("name", _DRAWN)
def test_font_sizes_and_radii_are_tokens(name: str) -> None:
    hits: list[str] = [
        f"{name}:{n}: {line.strip()}"
        for n, line in _lines(name)
        if (_FONT_SIZE.search(line) or _RADIUS.search(line)) and not line.strip().startswith("--")
    ]
    assert hits == [], hits


def test_the_tokens_are_declared_once_each_in_the_base_look() -> None:
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    themes: str = (_CSS / "themes.css").read_text(encoding="utf-8")
    for token in ("--fs-xs", "--fs-2xl", "--radius-xs", "--radius-pill", "--space-1", "--pad-card", "--gap-stack"):
        assert app.count(f"{token}:") >= 1, token
    for token in (
        "--bg",
        "--surface",
        "--text",
        "--accent",
        "--bubble",
        "--ok",
        "--warn",
        "--bad",
        "--knob",
        "--scrim",
        "--computer-backdrop",
    ):
        assert f"{token}:" in themes, token
        assert f"{token}:" not in app, f"{token} is a colour and belongs to themes.css"

"""The visual system (item 50): the colours of the interface live in ``themes.css`` alone, and the other sheets draw
every control on the tokens: no literal colour, font size or radius outside the token blocks. The soft look (item 63):
every text tier reads on every surface of every look in both schemes, measured as WCAG contrast."""

import re
from pathlib import Path

import pytest

_CSS: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static" / "css"
_DRAWN: tuple[str, ...] = ("app.css", "shell.css", "run.css", "settings.css")
_COLOUR: re.Pattern[str] = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\bwhite\b|\bblack\b")
_FONT_SIZE: re.Pattern[str] = re.compile(r"font-size:\s*[0-9.]+px")
_RADIUS: re.Pattern[str] = re.compile(r"border-radius:\s*[0-9.]+px")
# A literal duration (0.2s, 150ms); 0s is the instant and no token.
_DURATION: re.Pattern[str] = re.compile(r"(?<![\w.-])(?!0s\b)\d*\.?\d+m?s\b")


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


@pytest.mark.parametrize("name", _DRAWN)
def test_every_transition_and_animation_runs_on_a_motion_token(name: str) -> None:
    """Item 73: the durations live in the motion tokens; a sheet names one, never a number of its own."""
    hits: list[str] = [
        f"{name}:{n}: {line.strip()}"
        for n, line in _lines(name)
        if _DURATION.search(line) and not line.strip().startswith("--") and not line.strip().startswith("/*")
    ]
    assert hits == [], hits


def test_the_tokens_are_declared_once_each_in_the_base_look() -> None:
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    themes: str = (_CSS / "themes.css").read_text(encoding="utf-8")
    for token in (
        "--fs-xs",
        "--fs-2xl",
        "--radius-xs",
        "--radius-pill",
        "--space-1",
        "--pad-card",
        "--gap-stack",
        "--motion-quick",
        "--motion-base",
        "--motion-slow",
        "--ease",
        "--ease-emphasis",
    ):
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


_LOOKS: tuple[tuple[str, str], ...] = (("neutral", ":root {"), ("paper", ':root[data-theme="paper"] {'))
_SURFACES: tuple[str, ...] = ("--bg", "--sidebar", "--surface", "--surface-2", "--surface-3")
_PAIR: re.Pattern[str] = re.compile(r"(--[a-z0-9-]+): light-dark\((#[0-9a-f]{6}), (#[0-9a-f]{6})\);")
# The least contrast of a text tier against a surface: body text and its second tier read at AA; the muted and the
# faint tiers, which label and annotate, at the AA level of large text.
_LEAST: tuple[tuple[str, float], ...] = (("--text", 4.5), ("--text-2", 4.5), ("--muted", 3.0), ("--faint", 3.0))
_ON: tuple[tuple[str, str], ...] = (
    ("--on-accent", "--accent"),
    ("--on-bubble", "--bubble"),
    ("--ok", "--ok-weak"),
    ("--warn", "--warn-weak"),
    ("--bad", "--bad-weak"),
    ("--on-bad", "--bad"),
)


def _block(look: str) -> str:
    """The token block of ``look``: the first block under its selector that declares ``--bg``."""
    themes: str = (_CSS / "themes.css").read_text(encoding="utf-8")
    opening: str = dict(_LOOKS)[look]
    start: int = 0
    while True:
        start = themes.index(opening, start) + len(opening)
        block: str = themes[start : themes.index("}", start)]
        if "--bg:" in block:
            return block


def _pairs(look: str) -> dict[str, tuple[str, str]]:
    """Each token of ``look`` with its light and its dark hex, for the tokens written as a pair of hexes."""
    pairs: dict[str, tuple[str, str]] = {name: (light, dark) for name, light, dark in _PAIR.findall(_block(look))}
    if look == "neutral":
        pairs["--on-bubble"] = ("#1c1b19", "#1c1b19")
    else:
        pairs["--bubble"] = pairs["--accent-weak"]
        pairs["--on-bubble"] = pairs["--text"]
    return pairs


def _luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        c: float = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(one: str, other: str) -> float:
    """The WCAG contrast ratio of two hex colours, 1 to 21."""
    lighter, darker = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("look", [look for look, _ in _LOOKS])
@pytest.mark.parametrize("scheme", [0, 1], ids=["light", "dark"])
def test_every_text_tier_reads_on_every_surface_and_every_ink_on_its_ground(look: str, scheme: int) -> None:
    pairs: dict[str, tuple[str, str]] = _pairs(look)
    short: list[str] = []
    for tier, least in _LEAST:
        for surface in _SURFACES:
            ratio: float = contrast(pairs[tier][scheme], pairs[surface][scheme])
            if ratio < least:
                short.append(f"{tier} on {surface}: {ratio:.2f} < {least}")
    for ink, ground in _ON:
        ratio = contrast(pairs[ink][scheme], pairs[ground][scheme])
        if ratio < 4.5:
            short.append(f"{ink} on {ground}: {ratio:.2f} < 4.5")
    assert short == [], short


def test_the_surfaces_step_up_by_lightness_in_dark_and_down_in_light() -> None:
    for look, _ in _LOOKS:
        pairs: dict[str, tuple[str, str]] = _pairs(look)
        steps: list[str] = ["--surface", "--surface-2", "--surface-3"]
        light: list[float] = [_luminance(pairs[name][0]) for name in steps]
        dark: list[float] = [_luminance(pairs[name][1]) for name in steps]
        assert light == sorted(light, reverse=True), (look, light)
        assert dark == sorted(dark), (look, dark)
        assert _luminance(pairs["--bg"][1]) < dark[0], look

"""The visual system (item 50): the colours of the interface live in ``themes.css`` alone, and the other sheets draw
every control on the tokens: no literal colour, font size or radius outside the token blocks. The soft look (item 63):
every text tier reads on every surface of every look in both schemes, measured as WCAG contrast. The type system
(item 77): every size, weight, line-height, letter-spacing and family in the sheets is a token, the two faces are
served from the static directory, and the CSP lets the page load them."""

import re
from pathlib import Path

import pytest

_CSS: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static" / "css"
_DRAWN: tuple[str, ...] = ("app.css", "shell.css", "run.css", "settings.css")
_COLOUR: re.Pattern[str] = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\bwhite\b|\bblack\b")
_FONT_SIZE: re.Pattern[str] = re.compile(r"font-size:\s*[0-9.]+(px|em|rem|%)")
# A font size that multiplies a token by a number (`calc(var(--text-xs) * 0.75)`) is a size of its own that no token
# names and the phone's floor of 13 px does not reach (item 1327's review).
_FONT_SIZE_SCALED: re.Pattern[str] = re.compile(r"font-size:[^;{}]*\bcalc\([^;{}]*\*")
# A weight, line-height, letter-spacing or family written as a number or a name instead of a token (item 77); the
# @font-face blocks declare the faces and their weight ranges and are the one place a number belongs.
_TYPE_LITERAL: re.Pattern[str] = re.compile(
    r"^\s*((font-weight|line-height|letter-spacing|font-family):(?!\s*var\()|font:(?!\s*(inherit|var\()))"
)
_FONTS: Path = _CSS.parent / "fonts"
_RADIUS: re.Pattern[str] = re.compile(r"border-radius:\s*[0-9.]+px")
# A gap, padding or margin, whose pixel values are on the 4 px grid or tokens (item 74).
_SPACING: re.Pattern[str] = re.compile(r"^\s*(gap|row-gap|column-gap|padding(-[a-z-]+)?|margin(-[a-z-]+)?):")
_PX: re.Pattern[str] = re.compile(r"(\d+)px")
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
def test_every_comment_of_a_sheet_closes_where_it_opens(name: str) -> None:
    """Item 1335's review: a merge left a second line of a comment under a line that already closed it, so the
    browser read the words as a selector and threw the whole rule after it away; nothing else sees it. Take every
    comment out: no `/*` or `*/` may be left."""
    text: str = (_CSS / name).read_text(encoding="utf-8")
    bare: str = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    stray: list[str] = [part for part in ("/*", "*/") if part in bare]
    assert stray == [], f"{name}: a comment that does not close, or words after one that did ({stray})"


@pytest.mark.parametrize("name", _DRAWN)
def test_no_font_size_multiplies_a_token_by_a_number(name: str) -> None:
    """Item 1327's review: `.side-badge` drew its letters at `calc(var(--text-xs) * 0.75)`, 9 px on a Mac and under the
    phone's floor of 13, a size no token names, which the literal-size rule did not see."""
    assert _FONT_SIZE_SCALED.search("font-size: calc(var(--text-xs) * 0.75);")
    assert _FONT_SIZE_SCALED.search("font-size: calc(0.5 * var(--text-ui))")
    assert _FONT_SIZE_SCALED.search("font-size: var(--text-xs);") is None
    hits: list[str] = [f"{name}:{n}: {line.strip()}" for n, line in _lines(name) if _FONT_SIZE_SCALED.search(line)]
    assert hits == [], hits


@pytest.mark.parametrize("name", _DRAWN)
def test_every_gap_padding_and_margin_is_a_token_or_on_the_four_pixel_grid(name: str) -> None:
    """Item 74: one rhythm of 8 px, 4 for the smallest; the hairline is the token --space-half."""
    hits: list[str] = [
        f"{name}:{n}: {line.strip()}"
        for n, line in _lines(name)
        if _SPACING.match(line)
        and not line.strip().startswith("--")
        and any(int(px) % 4 != 0 for px in _PX.findall(line))
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


_KEYFRAMES: re.Pattern[str] = re.compile(r"@keyframes ([\w-]+) \{(.*?)\n\}", re.DOTALL)
_ENDLESS: re.Pattern[str] = re.compile(r"animation:([^;]*\binfinite\b[^;]*);")
# What the compositor draws without the page drawing again: an animation of these alone costs next to nothing.
_COMPOSITED: frozenset[str] = frozenset({"opacity", "transform"})
# The endless animations that still change what the page draws, each only while something is at work in front of the
# owner: the ring of the microphone while it listens and of the plan's step at work (``pulse``, a growing shadow), and
# the light that runs over the words of a thought being written (``shimmer`` and ``sunshine``, a text's background).
# A chat being opened has three dots, which only rise and fade (item 643).
_REDRAWN_WHILE_AT_WORK: frozenset[str] = frozenset({"pulse", "shimmer", "sunshine"})


def test_what_repeats_forever_is_drawn_by_the_compositor() -> None:
    """Item 645: an animation that never stops changes only opacity and transform. The loop mark's glow, a shadow
    that changed, was drawn again on every frame: with three loops in the sidebar the page at rest kept a third of a
    core at work in Dot.app. Those that still redraw are named, and run only while something works."""
    sheets: str = "\n".join((_CSS / name).read_text(encoding="utf-8") for name in _DRAWN)
    changed: dict[str, set[str]] = {}
    for name, body in _KEYFRAMES.findall(sheets):
        changed.setdefault(name, set()).update(re.findall(r"^\s*([a-z-]+)\s*:", body, re.MULTILINE))
    endless: set[str] = {word for value in _ENDLESS.findall(sheets) for word in value.split() if word in changed}
    redrawn: dict[str, set[str]] = {
        name: changed[name] - _COMPOSITED for name in endless if changed[name] - _COMPOSITED
    }
    assert set(redrawn) == _REDRAWN_WHILE_AT_WORK, redrawn


def test_what_repeats_forever_stands_still_while_dots_window_is_away() -> None:
    """Item 662: every endless animation takes its play state from ``--endless``, which the page sets to paused while
    Dot's window is not the one in front or is out of sight, so that nothing is drawn at the screen's rate with nobody
    looking. A new endless animation without it fails here."""
    sheets: str = "\n".join((_CSS / name).read_text(encoding="utf-8") for name in _DRAWN)
    endless: int = len(re.findall(r"\binfinite\b[^;{}]*;", sheets))
    switched: int = len(
        re.findall(
            r"\binfinite\b[^;{}]*;\s*\n(?:\s*/\*[^*]*\*/\n)?\s*animation-play-state: (?:running, )*var\(--endless\);",
            sheets,
        )
    )
    assert endless > 0 and switched == endless, (switched, endless)
    assert re.search(r":root\[data-away\] \{\s*--endless: paused;\s*\}", sheets)


def test_the_tokens_are_declared_once_each_in_the_base_look() -> None:
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    themes: str = (_CSS / "themes.css").read_text(encoding="utf-8")
    for token in (
        "--text-xs",
        "--text-2xl",
        "--leading-ui",
        "--weight-title",
        "--tracking-title",
        "--radius-xs",
        "--radius-pill",
        "--space-1",
        "--pad-card",
        "--gap-stack",
        "--space-half",
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
# The least contrast of a text tier against a surface: every tier reads at AA, the muted and the faint ones too, as
# small labels and annotations are set in them (item 1205; they were held to 3:1, the level of large text).
_LEAST: tuple[tuple[str, float], ...] = (("--text", 4.5), ("--text-2", 4.5), ("--muted", 4.5), ("--faint", 4.5))
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


@pytest.mark.parametrize("look", [look for look, _ in _LOOKS])
@pytest.mark.parametrize("scheme", [0, 1], ids=["light", "dark"])
def test_the_muted_ink_and_the_sidebars_group_labels_read_at_aa_in_the_two_looks_of_themes_css(
    look: str, scheme: int
) -> None:
    """Item 437: small labels are set in the muted ink, so it reads at 4.5:1 on every surface, and the sidebar's group
    labels (Chats, Today) are set in a tier that reads at 4.5:1 on the sidebar, not in the faint one (4.13:1)."""
    pairs: dict[str, tuple[str, str]] = _pairs(look)
    for surface in _SURFACES:
        ratio: float = contrast(pairs["--muted"][scheme], pairs[surface][scheme])
        assert ratio >= 4.5, f"--muted on {surface}: {ratio:.2f} < 4.5"
    label: re.Match[str] | None = re.search(
        r"^\.side-label \{[^}]*?\bcolor: var\((--[a-z0-9-]+)\);", (_CSS / "shell.css").read_text(encoding="utf-8"), re.M
    )
    assert label is not None, "the .side-label rule sets no colour token"
    ratio = contrast(pairs[label.group(1)][scheme], pairs["--sidebar"][scheme])
    assert ratio >= 4.5, f".side-label ({label.group(1)}) on --sidebar: {ratio:.2f} < 4.5"


def test_a_phone_has_no_look_of_its_own_in_the_neutral_look() -> None:
    """Item 1324: a phone draws the same greys as a Mac (one design language), so themes.css holds no block that
    changes the neutral look's surfaces for a touch screen."""
    themes: str = (_CSS / "themes.css").read_text(encoding="utf-8")
    assert ':root[data-theme="neutral"] {' not in themes
    assert "(pointer: coarse)" not in themes


def _mix(one: str, other: str, share: float) -> str:
    """``color-mix(in srgb, one share%, other)`` of two hex colours, as a hex colour."""
    mixed: list[int] = [
        round(int(one[i : i + 2], 16) * share + int(other[i : i + 2], 16) * (1 - share)) for i in (1, 3, 5)
    ]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def test_the_ink_on_dots_warm_backdrop_reads_on_every_stop_of_it_in_both_looks_and_both_schemes() -> None:
    """Item 1324 (review, round 2): the hint and the title of Dot's computer took the page's ink, which is light in dark
    and was 1.4:1 on the amber. They take ``--brand-ink``, which is the same in every look and scheme and reads at 4.5:1
    on each of the three stops of ``--computer-backdrop``; no rule that sits on the backdrop names ``--on-bubble``."""
    themes: str = (_CSS / "themes.css").read_text(encoding="utf-8")
    brand: re.Match[str] | None = re.search(r"^\s+--brand: (#[0-9a-f]{6});", themes, re.M)
    ink: re.Match[str] | None = re.search(r"^\s+--brand-ink: (#[0-9a-f]{6});", themes, re.M)
    backdrop: re.Match[str] | None = re.search(r"^\s+--computer-backdrop: ([^;]+);", themes, re.M)
    assert brand is not None and ink is not None and backdrop is not None
    stops: list[str] = re.findall(r"color-mix\(in srgb, var\(--brand\) (\d+)%, (#[0-9a-f]{6})\)", backdrop.group(1))
    assert len(stops) == 2, backdrop.group(1)  # the two ends; the middle one is --brand itself
    colours: list[str] = [_mix(brand.group(1), other, int(share) / 100) for share, other in stops] + [brand.group(1)]
    for colour in colours:
        ratio: float = contrast(ink.group(1), colour)
        assert ratio >= 4.5, f"--brand-ink on the backdrop stop {colour}: {ratio:.2f} < 4.5"
    run: str = (_CSS / "run.css").read_text(encoding="utf-8")
    for selector in (".computer-screen .hint", ".computer-empty-mark", ".computer-empty-title"):
        rule: re.Match[str] | None = re.search(rf"^{re.escape(selector)} \{{([^}}]*)\}}", run, re.M)
        assert rule is not None, selector
        assert "--on-bubble" not in rule.group(1), f"{selector} sits on the warm backdrop and takes --brand-ink"
        assert "--brand-ink" in rule.group(1), selector


@pytest.mark.parametrize("look", [look for look, _ in _LOOKS])
@pytest.mark.parametrize("scheme", [0, 1], ids=["light", "dark"])
def test_the_focus_ring_is_the_solid_accent_and_reads_at_3_to_1_on_every_surface(look: str, scheme: int) -> None:
    """Item 1325 (review, round 2): the ring was the accent at 60 %, 2.19:1 on the dark card and 2.9:1 on the light
    page, under the 3:1 of a focus indicator. It is the accent itself, and the accent reads at 3:1 on every surface."""
    themes: str = (_CSS / "themes.css").read_text(encoding="utf-8")
    assert re.search(r"^\s+--ring: var\(--accent\);", themes, re.M), "--ring is not the solid accent"
    pairs: dict[str, tuple[str, str]] = _pairs(look)
    short: list[str] = []
    for surface in _SURFACES:
        ratio: float = contrast(pairs["--accent"][scheme], pairs[surface][scheme])
        if ratio < 3.0:
            short.append(f"--accent on {surface}: {ratio:.2f} < 3")
    assert short == [], short


_TEXT_COLOUR: re.Pattern[str] = re.compile(r"^\s*color:\s*var\(--accent\)\s*(!important\s*)?;")


@pytest.mark.parametrize("name", _DRAWN)
def test_no_text_is_set_in_the_accent_which_is_a_fill_colour(name: str) -> None:
    """Item 1325 (review, round 2): the accent fills (a button, a switch, a ring, a border) and reads at 4.06:1 as text
    on the dark surfaces; text takes ``--accent-strong``, which reads at 4.5:1. A ``color`` line that names
    ``--accent`` is text (a background, a border, an outline, ``accent-color`` and the rest are other properties and
    stay)."""
    left: list[str] = [f"{name}:{number}: {line.strip()}" for number, line in _lines(name) if _TEXT_COLOUR.match(line)]
    assert left == [], left


def test_the_surfaces_step_up_by_lightness_in_dark_and_down_in_light() -> None:
    for look, _ in _LOOKS:
        pairs: dict[str, tuple[str, str]] = _pairs(look)
        steps: list[str] = ["--surface", "--surface-2", "--surface-3"]
        light: list[float] = [_luminance(pairs[name][0]) for name in steps]
        dark: list[float] = [_luminance(pairs[name][1]) for name in steps]
        assert light == sorted(light, reverse=True), (look, light)
        assert dark == sorted(dark), (look, dark)
        assert _luminance(pairs["--bg"][1]) < dark[0], look


def _outside_font_face(name: str) -> list[tuple[int, str]]:
    """The lines of a sheet that are not inside an ``@font-face`` block."""
    kept: list[tuple[int, str]] = []
    inside: bool = False
    for number, line in _lines(name):
        if line.startswith("@font-face"):
            inside = True
        elif inside and line.startswith("}"):
            inside = False
        elif not inside:
            kept.append((number, line))
    return kept


@pytest.mark.parametrize("name", (*_DRAWN, "themes.css"))
def test_every_weight_line_height_letter_spacing_and_family_is_a_token(name: str) -> None:
    """Item 77: three weights, seven line-heights, two trackings and three families, all named; no number of its own."""
    hits: list[str] = [
        f"{name}:{n}: {line.strip()}" for n, line in _outside_font_face(name) if _TYPE_LITERAL.match(line)
    ]
    assert hits == [], hits


def test_the_faces_are_served_from_the_static_directory_with_their_licences() -> None:
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    for face in ("InterVariable.woff2", "InterVariable-Italic.woff2", "GeistMono-Variable.woff2"):
        assert (_FONTS / face).stat().st_size > 10_000, face
        assert f'url("../fonts/{face}")' in app, face
    for licence in ("LICENSE-Inter.txt", "LICENSE-GeistMono.txt"):
        assert "SIL Open Font License" in (_FONTS / licence).read_text(encoding="utf-8"), licence


def test_the_content_security_policy_lets_the_page_load_its_own_fonts() -> None:
    from dotagent.server.guard import SECURITY_HEADERS

    policy: str = dict(SECURITY_HEADERS)["Content-Security-Policy"]
    assert "font-src 'self'" in policy


def test_the_sunshine_over_work_that_goes_on_is_the_texts_own_colour_not_an_amber() -> None:
    """The light that slides through the words of a running call and a live thought is the text colour, dark on a light
    theme and light on a dark one (item 435): it was an amber once, and the owner had it black and white."""
    css: str = (_CSS / "app.css").read_text(encoding="utf-8")
    block: str = css[css.index("@keyframes sunshine") :]
    gradient: str = block[block.index("linear-gradient(") : block.index(");")]
    assert "var(--text) 50%" in gradient, gradient
    assert "--warn" not in gradient and "--accent" not in gradient, gradient


def test_a_call_in_the_chat_wears_no_low_medium_high_badge() -> None:
    """The row of a command or any other call says what it did, not a risk level (item 438): the owner took the label
    off, and it came back. The approval card keeps its own severity, which is a question, not a label of a call."""
    tools: str = (_CSS.parent / "js" / "tools.js").read_text(encoding="utf-8")
    assert "badge r-" not in tools
    assert "outcome.risk" not in tools
    assert ".r-low" not in (_CSS / "app.css").read_text(encoding="utf-8")


def test_what_keeps_clear_of_a_block_above_it_names_the_boxes_code_and_tables_are_drawn_in() -> None:
    """A block of code and a table are drawn in a box of their own with a copy bar (items 403 and 439), so a rule that
    names what ends an answer must name the box and not only the ``pre`` and the ``table`` in it."""
    css: str = (_CSS / "app.css").read_text(encoding="utf-8")
    rule: str = css[css.index(".answer > .prose:has(> :is(") :]
    rule = rule[: rule.index("{")]
    assert ".prose-code" in rule and ".prose-table" in rule, rule


_POSITIONED_BUTTON: re.Pattern[str] = re.compile(
    r"(^|[\s,>+~])\.(btn\.(sm\.)?icon-only|icon-btn)(?![\w-]|::)[^{]*\{[^}]*\bposition:", re.M
)


@pytest.mark.parametrize("name", ("app.css", "shell.css"))
def test_the_ring_of_a_fingers_target_does_not_take_a_placed_button_out_of_its_place(name: str) -> None:
    """Item 787: an icon button is positioned for the ring that widens its target only under ``:where``, whose weight is
    none, so that a button a rule of its own places (the key field's eye, the way down) keeps its place on a phone."""
    text: str = re.sub(r"/\*.*?\*/", "", (_CSS / name).read_text(encoding="utf-8"), flags=re.S)
    assert [match.group(0)[:80] for match in _POSITIONED_BUTTON.finditer(text)] == []


def test_the_foundation_is_the_plain_look_in_its_tokens() -> None:
    """Item 1324: the dark page is true black with the raised surfaces a hair lighter, the accent is a calm blue, the
    radii are the 0.5 rem scale and the type has the 10 and 11 px metadata sizes; item 1325: the controls have a
    height."""
    pairs: dict[str, tuple[str, str]] = _pairs("neutral")
    assert pairs["--bg"][1] == "#000000"
    assert pairs["--surface"][1] == "#111111"
    assert pairs["--surface"][0] == "#ffffff"
    assert contrast(pairs["--on-accent"][1], pairs["--accent"][1]) >= 4.5
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    declared: dict[str, str] = {}
    for name, value in re.findall(r"^\s+(--[a-z0-9-]+): ([^;]+);", app, re.M):
        declared.setdefault(name, value)  # the first is the Mac's; the phone's block that follows changes a few
    assert declared["--text-3xs"] == "calc(10px * var(--type-scale))"
    assert declared["--text-2xs"] == "calc(11px * var(--type-scale))"
    for token, value in (
        ("--radius-control", "8px"),
        ("--radius", "12px"),
        ("--radius-menu", "10px"),
        ("--radius-bubble", "10px"),
        ("--radius-composer", "14px"),
        ("--radius-lg", "16px"),
        ("--control-h", "32px"),
        ("--control-h-sm", "28px"),
        ("--menu-row", "28px"),
    ):
        assert declared[token] == value, (token, declared[token])
    assert declared["--weight-text"] == "400"
    assert declared["--weight-label"] == "calc(var(--weight-text) + 100)"
    assert declared["--weight-title"] == "calc(var(--weight-text) + 200)"


def test_the_docs_name_the_easing_the_sheets_use_and_not_an_older_one() -> None:
    """Item 1326: the page of the daemon told the springy easing of before item 1324 for a release after it, so the
    docs hold the one ``--ease`` the sheets declare and say no other cubic-bezier."""
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    ease: re.Match[str] | None = re.search(r"^\s+--ease: (cubic-bezier\([^)]*\));", app, re.M)
    assert ease is not None
    docs: Path = Path(__file__).resolve().parents[2] / "docs"
    for page in ("daemon.md", "dot-ui.md"):
        text: str = (docs / page).read_text(encoding="utf-8")
        assert ease.group(1) in text, page
        assert [found for found in re.findall(r"cubic-bezier\([^)]*\)", text) if found != ease.group(1)] == [], page


def test_the_daemon_page_tells_the_conversation_once_and_with_no_bubble_on_a_phone() -> None:
    """Item 1335's review: a rebase onto item 1324 left ``docs/daemon.md`` with two copies of the conversation
    paragraph, main's and a stale one from before the look (an 18 px corner, a quiet ring, 7 by 12 px of room), and
    main's still said Dot's words sit in "a soft bubble of its own" on a phone, which item 1335 took away. The page
    says the conversation once, says no bubble round what Dot says, and tells none of the stale values; the sheet
    agrees."""
    docs: Path = Path(__file__).resolve().parents[2] / "docs"
    text: str = (docs / "daemon.md").read_text(encoding="utf-8")
    flat: str = " ".join(text.split())
    assert "soft bubble of its own" not in flat
    # The composer's frame is the one thing that is rightly on an 18 px corner.
    corners: list[str] = [flat[max(0, m.start() - 40) : m.end()] for m in re.finditer(r"18 px corner", flat)]
    assert [near for near in corners if "composer is one frame on an" not in near] == []
    for stale in ("quiet ring", "7 by 12 px", "as iMessage draws them", "an ink bubble"):
        assert stale not in flat, stale
    assert flat.count("a chat is drawn as a messaging app draws one (item 832)") == 1
    assert "item 1335 took it away again" in flat
    run: str = (_CSS / "run.css").read_text(encoding="utf-8")
    bubble_on_phone: str = r"@media[^{]*max-width: 720px\)\s*\{\s*\.run-thread :is\(\.answer, \.bubble\.say\)"
    assert re.search(bubble_on_phone, run) is None


def test_a_phone_draws_no_text_token_under_13_px() -> None:
    """Item 1324's review: the phone's two metadata sizes were 11 and 12 px while the sheet, the docs and the phone type
    test said no text is drawn under 13 px there."""
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    start: int = app.index(
        "@media (pointer: coarse) and (max-width: 720px), (pointer: coarse) and (max-height: 500px) {"
    )
    block: str = app[start : app.index("\n}\n", start)]
    sizes: list[tuple[str, int]] = [
        (name, int(px)) for name, px in re.findall(r"(--text-[a-z0-9]+): calc\((\d+)px \* var\(--type-scale\)\)", block)
    ]
    assert len(sizes) >= 8, sizes
    assert [(name, px) for name, px in sizes if px < 13] == []


# What is round is a fact of the rules that name `--radius-pill`; a sentence that lists it is a list that drifts.
_ROUND_LIST: re.Pattern[str] = re.compile(
    r"\bare (fully )?round\b|\bonly an? [^.;]*\b(round|pill)\b|\bstays only for\b", re.I
)


def test_no_sheet_comment_or_doc_lists_what_is_round() -> None:
    """Item 1324's review, round 3: the token's comment, the design page, the daemon page and the item's record each
    listed the round things and no two agreed (the composer's + and microphone are on the pill token too)."""
    root: Path = Path(__file__).resolve().parents[2]
    pages: list[Path] = [
        _CSS / "app.css",
        root / "docs" / "dot-ui.md",
        root / "docs" / "daemon.md",
        *(root / "roadmap" / f"{number}.md" for number in (1324, 1325, 1326)),
    ]
    hits: list[str] = [
        f"{page.name}:{n}: {line.strip()[:100]}"
        for page in pages
        for n, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1)
        if _ROUND_LIST.search(line)
    ]
    assert hits == [], hits


def _oklab(hex_colour: str) -> tuple[float, float, float]:
    def linear(value: int) -> float:
        c: float = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(int(hex_colour[i : i + 2], 16)) for i in (1, 3, 5))
    long: float = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    medium: float = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    short: float = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * long + 0.7936177850 * medium - 0.0040720468 * short,
        1.9779984951 * long - 2.4285922050 * medium + 0.4505937099 * short,
        0.0259040371 * long + 0.7827717662 * medium - 0.8086757660 * short,
    )


def _mix_oklab(one: str, other: str, share: float) -> str:
    """``color-mix(in oklab, one share%, other)`` of two hex colours, as a hex colour."""
    a, b = _oklab(one), _oklab(other)
    lightness, green, blue = (x * share + y * (1 - share) for x, y in zip(a, b, strict=True))
    long: float = (lightness + 0.3963377774 * green + 0.2158037573 * blue) ** 3
    medium: float = (lightness - 0.1055613458 * green - 0.0638541728 * blue) ** 3
    short: float = (lightness - 0.0894841775 * green - 1.2914855480 * blue) ** 3
    linear: list[float] = [
        4.0767416621 * long - 3.3077115913 * medium + 0.2309699292 * short,
        -1.2684380046 * long + 2.6097574011 * medium - 0.3413193965 * short,
        -0.0041960863 * long - 0.7034186147 * medium + 1.7076147010 * short,
    ]

    def encode(c: float) -> int:
        c = min(1.0, max(0.0, c))
        return round(255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055))

    return "#" + "".join(f"{encode(c):02x}" for c in linear)


@pytest.mark.parametrize("look", [look for look, _ in _LOOKS])
@pytest.mark.parametrize("scheme", [0, 1], ids=["light", "dark"])
def test_a_link_in_a_message_has_no_chip_and_reads_at_aa_on_every_ground_it_stands_on(look: str, scheme: int) -> None:
    """Item 1346: a link in a message is the words round it in a shade tilted toward the accent, with no frame, fill or
    icon and no line until it is pointed at. Its colour is a mix the sheet writes, and it reads at 4.5:1 on each ground
    a message has (the page, the card, the agent's and the owner's messages), in both schemes of both looks."""
    app: str = (_CSS / "app.css").read_text(encoding="utf-8")
    rule: re.Match[str] | None = re.search(r"^\.prose a\.msg-link \{([^}]*)\}", app, re.M)
    assert rule is not None
    for forbidden in ("border:", "background", "padding", "box-shadow", "display: inline-flex"):
        assert forbidden not in rule.group(1), f"a link in a message takes no {forbidden}"
    assert "text-decoration: none;" in rule.group(1)
    shares: list[str] = re.findall(
        r"color-mix\(in oklab, var\(--accent-strong\) (\d+)%, var\(--text\)\)", rule.group(1)
    )
    assert len(shares) == 2, rule.group(1)  # light, then dark
    pairs: dict[str, tuple[str, str]] = _pairs(look)
    link: str = _mix_oklab(pairs["--accent-strong"][scheme], pairs["--text"][scheme], int(shares[scheme]) / 100)
    for ground in ("--bg", "--surface", "--surface-2", "--surface-3"):
        ratio: float = contrast(link, pairs[ground][scheme])
        assert ratio >= 4.5, f"a link ({link}) on {ground}: {ratio:.2f} < 4.5"
    # It is no longer the text's own ink: a shade of difference at the least.
    assert contrast(link, pairs["--text"][scheme]) >= 1.15, link
    mine: re.Match[str] | None = re.search(
        r"^\.bubble\.user \.prose a\.msg-link \{[^}]*"
        r"color-mix\(in oklab, var\(--accent-strong\) (\d+)%, var\(--on-msg-me\)\)",
        app,
        re.M,
    )
    assert mine is not None
    in_bubble: str = _mix_oklab(pairs["--accent-strong"][scheme], pairs["--text"][scheme], int(mine.group(1)) / 100)
    ratio = contrast(in_bubble, pairs["--surface-3"][scheme])
    assert ratio >= 4.5, f"a link ({in_bubble}) in the owner's bubble: {ratio:.2f} < 4.5"

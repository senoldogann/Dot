"""Dot's mark (item 114) is one drawing in three places: ``desktop/icons/render.py`` writes the favicon from its numbers,
the page's opening (``static/index.html``) and the desktop shell's page for a daemon that is down
(``desktop/ui/down.html``) set the same disc and eyes as the o of the wordmark between the same d and t, and every
copy wears the ``--brand`` and ``--on-brand`` of the base look."""

import re
from pathlib import Path

_REPO: Path = Path(__file__).resolve().parents[2]
_STATIC: Path = _REPO / "src" / "dotagent" / "server" / "static"
_FAVICON: Path = _STATIC / "favicon.svg"
_PAGE: Path = _STATIC / "index.html"
_DOWN: Path = _REPO / "desktop" / "ui" / "down.html"
_EYE: re.Pattern[str] = re.compile(r'<path[^>]*\sd="(M-10\.5[^"]+)"[^>]*\stransform="(matrix\([^"]+\))"')
_LETTER: re.Pattern[str] = re.compile(r'<path class="[^"]*\bletter\b[^"]*" d="([^"]+)"')
_TOKEN: re.Pattern[str] = re.compile(r"^\s*(--brand|--on-brand): (#[0-9a-f]{6});", re.M)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_eyes_of_the_mark_are_one_drawing_in_every_place() -> None:
    eyes: list[tuple[str, str]] = _EYE.findall(_read(_FAVICON))
    assert len(eyes) == 2
    assert _EYE.findall(_read(_PAGE)) == eyes
    assert _EYE.findall(_read(_DOWN)) == eyes


def test_the_wordmark_is_one_drawing_in_the_page_and_in_the_desktop_shell() -> None:
    letters: list[str] = _LETTER.findall(_read(_PAGE))
    assert len(letters) == 2
    assert _LETTER.findall(_read(_DOWN)) == letters


def test_every_copy_of_the_mark_wears_the_colours_of_the_base_look() -> None:
    tokens: dict[str, str] = dict(_TOKEN.findall(_read(_STATIC / "css" / "themes.css")))
    favicon: str = _read(_FAVICON)
    assert f'<circle r="100" fill="{tokens["--brand"]}"/>' in favicon
    assert favicon.count(f'fill="{tokens["--on-brand"]}"') == 2
    down: str = _read(_DOWN.with_suffix(".css"))
    assert f".disc {{ fill: {tokens['--brand']}; }}" in down
    assert f".eye {{ fill: {tokens['--on-brand']}; }}" in down

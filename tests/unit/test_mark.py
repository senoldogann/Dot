"""Dot's mark (item 114) is one drawing in every place: ``desktop/icons/render.py`` writes the favicon from its
numbers; the page's opening (``static/index.html``), the page kept for a Mac out of reach (``static/offline.html``), the
page a sign-in comes back to (``callback_page.WORDMARK``) and the desktop shell's page for a daemon that is down
(``desktop/ui/down.html``) set the same ball and eyes as the o of the wordmark between the same d and t; and every copy
wears the same obsidian, gold and light (item 642), the ball the obsidian and the eyes the gold."""

import re
from pathlib import Path

from dotagent.server.callback_page import WORDMARK

_REPO: Path = Path(__file__).resolve().parents[2]
_SRC: Path = _REPO / "src"
_STATIC: Path = _SRC / "dotagent" / "server" / "static"
_FAVICON: Path = _STATIC / "favicon.svg"
_PAGE: Path = _STATIC / "index.html"
_OFFLINE: Path = _STATIC / "offline.html"
_DOWN: Path = _REPO / "desktop" / "ui" / "down.html"
_EYE: re.Pattern[str] = re.compile(r'<path[^>]*\sd="(M-10\.5[^"]+)"[^>]*\stransform="(matrix\([^"]+\))"')
_LETTER: re.Pattern[str] = re.compile(r'<path class="[^"]*\bletter\b[^"]*" d="([^"]+)"')
_GRADIENT: re.Pattern[str] = re.compile(r'<(radialGradient|linearGradient) id="(mark-[a-z]+)"([^>]*)>(.*?)</\1>', re.S)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _served() -> dict[str, str]:
    """The copies of the mark the daemon serves, which draw it with app.css's classes, by where they are."""
    return {"index.html": _read(_PAGE), "offline.html": _read(_OFFLINE), "callback_page.WORDMARK": WORDMARK}


def test_the_eyes_of_the_mark_are_one_drawing_in_every_place() -> None:
    eyes: list[tuple[str, str]] = _EYE.findall(_read(_FAVICON))
    assert len(eyes) == 2
    for where, text in {**_served(), "down.html": _read(_DOWN)}.items():
        assert _EYE.findall(text) == eyes, where


def test_the_wordmark_is_one_drawing_in_the_page_and_in_the_desktop_shell() -> None:
    letters: list[str] = _LETTER.findall(_read(_PAGE))
    assert len(letters) == 2
    assert _LETTER.findall(_read(_DOWN)) == letters


def _gradients(text: str) -> dict[str, tuple[str, str]]:
    """The mark's gradients in a file, by id: their attributes and their stops, as written."""
    return {name: (attributes, stops) for _, name, attributes, stops in _GRADIENT.findall(text)}


def test_every_copy_of_the_mark_wears_the_same_obsidian_and_gold() -> None:
    favicon: str = _read(_FAVICON)
    gradients: dict[str, tuple[str, str]] = _gradients(favicon)
    assert set(gradients) == {"mark-obsidian", "mark-gold", "mark-shine"}
    for where, text in {**_served(), "down.html": _read(_DOWN)}.items():
        assert _gradients(text) == gradients, where
    # The light and the hairline of gold lie on the ball of every copy the daemon serves.
    for where, text in _served().items():
        assert '<ellipse class="mark-shine"' in text, where
        assert '<circle class="mark-rim" r="99"/>' in text, where
    # The ball takes the obsidian and the two eyes the gold, in each copy; a copy without the gradients would still
    # draw them, in their plain colours.
    assert '<circle r="100" fill="url(#mark-obsidian)"/>' in favicon
    assert favicon.count('fill="url(#mark-gold)"') == 2
    page: str = _read(_STATIC / "css" / "app.css")
    assert ".mark-disc {\n  fill: url(#mark-obsidian) var(--mark-ball);\n}" in page
    assert ".mark-eye {\n  fill: url(#mark-gold) var(--mark-rim);\n}" in page
    down: str = _read(_DOWN.with_suffix(".css"))
    assert ".disc { fill: url(#mark-obsidian); }" in down
    assert ".eye { fill: url(#mark-gold); }" in down


def test_no_file_draws_the_mark_but_the_copies_held_here() -> None:
    """A page that drew the mark with app.css's classes but without its gradients showed no ball at all: the offline
    page and the callback page did, until the review of item 642 found them. A new copy is added to ``_served``, and so
    held to the same gradients."""
    drawing: set[str] = {
        path.relative_to(_SRC).as_posix()
        for path in _SRC.rglob("*")
        if path.is_file() and path.suffix in {".html", ".py", ".js"} and "mark-disc" in _read(path)
    }
    assert drawing == {
        "dotagent/server/static/index.html",
        "dotagent/server/static/offline.html",
        "dotagent/server/callback_page.py",
    }

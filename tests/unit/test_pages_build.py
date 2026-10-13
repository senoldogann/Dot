"""What the pages are (item 190): the digest the daemon tells with its status follows the bytes and the place of every
file it serves, and nothing else (where the files are, when they were written), so that a page tells an update of Dot
from a restart."""

from pathlib import Path

from dotagent.server.app import pages_build

FILES: dict[str, str] = {"index.html": "<p>Dot</p>", "js/main.js": "export {};"}


def _pages(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path: Path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_pages_build_follows_the_files_and_nothing_else(tmp_path: Path) -> None:
    first: str = pages_build(_pages(tmp_path / "a", FILES))
    assert pages_build(_pages(tmp_path / "b", FILES)) == first
    assert pages_build(_pages(tmp_path / "c", {**FILES, "js/main.js": "export const a = 1;"})) != first
    assert pages_build(_pages(tmp_path / "d", {"index.html": "<p>Dot</p>", "js/app.js": "export {};"})) != first

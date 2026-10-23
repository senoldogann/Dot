"""A page Dot made in its workspace is shown whole (item 600): the files beside it that it names, a style sheet, a
script, a picture, a font the style uses, are written into it, since a shown page is sandboxed and loads nothing of
this Mac. A file read_file would not read without a question (outside the workspace, through a link out of it, in a
protected place, kept from a crawl) is left out and named, and so is one that is missing; a web address, a data:
address and a fragment are left as they are. A file that would make the page longer than a page may be is left out and
named, and the page is shown without it (the pre-push review)."""

import base64
import os
import time
from pathlib import Path

import pytest

from dotagent.agent.pages import PAGE_MAX_CHARS
from dotagent.tools import page_files
from dotagent.tools.page_files import PageFiles, with_its_files
from dotagent.tools.pages import whole_page_file
from dotagent.tools.third_party import CRAWL_FOLDER

PNG: bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
# A style sheet names its files from its own folder.
CSS: str = (
    "body { background: url('../img/bg.png'); }\n@font-face { font-family: Herald; src: url(../fonts/herald.woff2); }\n"
)
JS: str = "document.title = 'Herald'; const end = '</script>';\n"
PAGE: str = """<!doctype html>
<html><head>
<link rel="stylesheet" href="css/site.css" media="screen">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/x@1/x.css">
<script src="js/app.js" defer></script>
<style>.logo { background-image: url("img/bg.png"); }</style>
</head><body>
<img src="img/logo.png" alt="Logo &amp; mark">
<img src="data:image/png;base64,AAAA" alt="inline">
<a href="#top">Top</a>
<div style="background: url(img/bg.png) no-repeat">Banner</div>
<svg><image href="img/logo.png"/><rect width="1" height="1"/></svg>
<img src="../outside.png" alt="out">
<img src="secret/key.png" alt="key">
<img src="../crawl/page.png" alt="crawl">
<img src="gone.png" alt="gone">
<img src="link.png" alt="link">
</body></html>
"""


def _site(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace with a site: its page, a style sheet, a script, pictures and a font; a picture outside the
    workspace, a link in the site to it, a protected folder and a crawl's folder."""
    home: Path = Path(os.path.realpath(tmp_path))
    workspace: Path = home / "workspace"
    site: Path = workspace / "site"
    for folder in ("css", "js", "img", "fonts", "secret"):
        (site / folder).mkdir(parents=True)
    (site / "index.html").write_text(PAGE, encoding="utf-8")
    (site / "css" / "site.css").write_text(CSS, encoding="utf-8")
    (site / "js" / "app.js").write_text(JS, encoding="utf-8")
    for name in ("img/bg.png", "img/logo.png", "secret/key.png"):
        (site / name).write_bytes(PNG)
    (site / "fonts" / "herald.woff2").write_bytes(b"wOF2font")
    (workspace / "outside.png").write_bytes(PNG)
    (home / "elsewhere.png").write_bytes(PNG)
    (site / "link.png").symlink_to(home / "elsewhere.png")
    (workspace / CRAWL_FOLDER).mkdir()
    (workspace / CRAWL_FOLDER / "page.png").write_bytes(PNG)
    return workspace, site / "index.html"


def test_the_files_a_page_names_beside_it_are_written_into_it(tmp_path: Path) -> None:
    workspace, page = _site(tmp_path)
    picture: str = f"data:image/png;base64,{base64.b64encode(PNG).decode('ascii')}"
    files: PageFiles = with_its_files(page.read_text(encoding="utf-8"), page, workspace, (page.parent / "secret",), ())
    html: str = files.html
    # The style sheet is a style block, with its picture and font written in, for the screen as it was.
    assert '<style media="screen">body { background: url("' + picture + '"); }' in html
    assert f'url("data:font/woff2;base64,{base64.b64encode(b"wOF2font").decode("ascii")}")' in html
    assert 'href="css/site.css"' not in html
    # A web address stays; so do a data: address and a fragment.
    assert '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/x@1/x.css">' in html
    assert 'src="data:image/png;base64,AAAA"' in html and 'href="#top"' in html
    # The script is its text, its other attributes kept, and its words cannot end the script element early.
    assert "<script defer>document.title = 'Herald'; const end = '<\\/script>';\n</script>" in html
    # The page's own style block uses the picture too, and so does a tag's style; the tags that show it, the SVG one
    # closed as it was.
    assert f'.logo {{ background-image: url("{picture}"); }}' in html
    assert f'<div style="background: url(&quot;{picture}&quot;) no-repeat">' in html
    assert f'<img src="{picture}" alt="Logo &amp; mark">' in html
    assert f'<image href="{picture}" /><rect width="1" height="1"/>' in html
    # The picture of the workspace one folder up is the workspace's, and is written in.
    assert f'<img src="{picture}" alt="out">' in html
    # Left out, and named: a protected one, a crawl's, a missing one, and one through a link out of the workspace.
    for name in ("secret/key.png", "../crawl/page.png", "gone.png", "link.png"):
        assert f'src="{name}"' in html, name
    assert files.left_out == ["secret/key.png", "../crawl/page.png", "gone.png", "link.png"]
    assert files.too_large == []
    assert sorted(files.written) == sorted(
        [
            "css/site.css",
            "../img/bg.png",
            "../fonts/herald.woff2",
            "js/app.js",
            "img/bg.png",
            "img/bg.png",
            "img/logo.png",
            "img/logo.png",
            "../outside.png",
        ]
    )


def test_a_file_that_would_not_fit_is_left_out_and_the_page_shown_without_it(tmp_path: Path) -> None:
    """The pre-push review: a photo of a few hundred kilobytes made the whole page refused, where item 598 showed it
    without the photo. It is measured before it is read, left out and named, and the rest is written in."""
    workspace, page = _site(tmp_path)
    # Larger than a page as a data: address: left out wherever the page names it.
    (page.parent / "img" / "logo.png").write_bytes(b"x" * PAGE_MAX_CHARS)
    files: PageFiles = whole_page_file(page, workspace, (), ())
    assert len(files.html) <= PAGE_MAX_CHARS
    assert files.too_large == ["img/logo.png", "img/logo.png"]
    assert 'src="img/logo.png" alt="Logo &amp; mark"' in files.html
    assert "css/site.css" in files.written and "js/app.js" in files.written


def test_an_imported_sheet_is_named_as_left_out(tmp_path: Path) -> None:
    """The pre-push review's nit: a sheet a style imports loads from no data: address in a shown page, so it is not
    written in, and the answer names it rather than leaving it unsaid."""
    workspace, page = _site(tmp_path)
    (page.parent / "css" / "site.css").write_text('@import url("more.css");\n' + CSS, encoding="utf-8")
    html: str = '<link rel="stylesheet" href="css/site.css"><style>@import "theme.css"; p { color: red; }</style>'
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert files.left_out == ["more.css", "theme.css"]
    assert '@import url("more.css");' in files.html and '@import "theme.css";' in files.html
    assert "../img/bg.png" in files.written


def test_a_page_that_comes_out_longer_than_a_page_is_shown_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-push review's nit: the files are measured before they are read, and what the escapes add is kept free;
    a page that still comes out longer than a page (a file that grew while it was read) is shown as it was written,
    its files named as too large."""
    workspace, page = _site(tmp_path)
    (page.parent / "img" / "logo.png").write_bytes(b"x" * (PAGE_MAX_CHARS // 2))
    monkeypatch.setattr(page_files, "_MARGIN", -PAGE_MAX_CHARS)
    written: str = page.read_text(encoding="utf-8")
    files: PageFiles = with_its_files(written, page, workspace, (), ())
    assert files.html == written
    assert "img/logo.png" in files.too_large and "css/site.css" in files.too_large
    assert files.written == []


def test_a_style_of_unclosed_urls_is_read_in_linear_time(tmp_path: Path) -> None:
    """The independent review: the pattern of url() went back over a run of unclosed "url(" for each of them, holding
    the interpreter, and so the daemon, for minutes on a page's style. It reads 400,000 characters of them at once."""
    workspace, page = _site(tmp_path)
    css: str = "url(" * 100_000
    started: float = time.perf_counter()
    files: PageFiles = with_its_files(f"<style>{css}</style>", page, workspace, (), ())
    assert time.perf_counter() - started < 2.0
    assert files.html == f"<style>{css}</style>" and files.left_out == []


def test_a_large_file_named_many_times_is_measured_and_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The independent review: a page that named one large picture forty times read and encoded it forty times before
    it was refused. It is measured against the room the page has left, and not read."""
    workspace, page = _site(tmp_path)
    (page.parent / "big.png").write_bytes(b"x" * (4 * 1024 * 1024))
    reads: list[Path] = []
    read = page_files._Writer._read

    def counted(writer: page_files._Writer, path: Path, limit: int) -> bytes | None:
        reads.append(path)
        return read(writer, path, limit)

    monkeypatch.setattr(page_files._Writer, "_read", counted)
    files: PageFiles = with_its_files('<img src="big.png">' * 40, page, workspace, (), ())
    assert files.too_large == ["big.png"] * 40
    assert reads == []


def test_a_file_swapped_for_a_link_after_it_was_judged_is_not_read(tmp_path: Path) -> None:
    """The independent review: a file is judged by its path and read by its path, so a file of the workspace replaced
    by a link to a protected one, or its folder by a link to a protected folder, in between, was read. It is opened
    without following a link and judged again by the path the kernel opened."""
    workspace, page = _site(tmp_path)
    keys: Path = page.parent / "secret"
    for swap in ("file", "folder"):
        writer = page_files._Writer(PageFiles(""), workspace, (keys,), ())
        named: str = f"{swap}/logo.png"
        (page.parent / swap).mkdir()
        (page.parent / named).write_bytes(PNG)
        judged = writer.file(named, page.parent)
        assert judged is not None
        if swap == "file":
            (page.parent / named).unlink()
            (page.parent / named).symlink_to(keys / "key.png")
        else:
            (page.parent / named).unlink()
            (page.parent / swap).rmdir()
            (page.parent / swap).symlink_to(keys)
            (keys / "logo.png").write_bytes(PNG)
        # Read as judged, by the path that was judged: refused, the link not followed or the place judged again.
        _, path, size = judged
        assert writer._read(path, size) is None, swap


def test_a_path_that_cannot_be_read_is_left_out_and_named(tmp_path: Path) -> None:
    """The independent review: a NUL in a link, or a picture this process may not read, made show_page fail with an
    unexpected error; each is left out and named."""
    workspace, page = _site(tmp_path)
    shut: Path = page.parent / "img" / "shut.png"
    shut.write_bytes(PNG)
    shut.chmod(0)
    try:
        files: PageFiles = with_its_files('<img src="a%00b.png"><img src="img/shut.png">', page, workspace, (), ())
    finally:
        shut.chmod(0o600)
    assert files.left_out == ["a\x00b.png", "img/shut.png"]


def test_a_script_full_of_end_tags_is_counted_with_its_escapes(tmp_path: Path) -> None:
    """The pre-push review: each "</script" written in takes one character more, which only a margin of 2,000 covered,
    so a script with thousands of them made the whole page fall back and lose every file. They are counted in."""
    workspace, page = _site(tmp_path)
    (page.parent / "js" / "app.js").write_text("'</script>';\n" * 5000 + "'</SCRIPT>';\n", encoding="utf-8")
    files: PageFiles = with_its_files(page.read_text(encoding="utf-8"), page, workspace, (), ())
    assert "js/app.js" in files.written and "css/site.css" in files.written and files.too_large == []
    # In any letter case, as a browser ends the element (the pre-push review).
    assert files.html.count("<\\/script>") == 5000 and "<\\/SCRIPT>" in files.html
    assert "</SCRIPT>" not in files.html


def test_an_import_without_its_semicolon_leaves_the_rest_of_the_style_read(tmp_path: Path) -> None:
    """The pre-push review: an @import with no ";" took the rest of its line and the next ones up to 2,048 characters,
    and the pictures there were neither written in nor named. It ends at a line or a brace."""
    workspace, page = _site(tmp_path)
    css: str = '@import "theme.css"\nbody { background: url(img/bg.png); }'
    files: PageFiles = with_its_files(f"<style>{css}</style>", page, workspace, (), ())
    assert files.left_out == ["theme.css"] and files.written == ["img/bg.png"]

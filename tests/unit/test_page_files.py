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
from dotagent.store.models import ShownPage
from dotagent.tools import page_files
from dotagent.tools.page_files import PageFiles, with_its_files
from dotagent.tools.pages import shown_words, whole_page_file
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


# ------------------------------------------------------------------------------------------------ item 612


def _bare_site(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace with a folder for a page, and the path of the page in it."""
    workspace: Path = Path(os.path.realpath(tmp_path)) / "workspace"
    (workspace / "site").mkdir(parents=True)
    return workspace, workspace / "site" / "index.html"


def _still_named(files: PageFiles) -> set[str]:
    """The files beside the page that the written page still names, in the places the module writes files in or names:
    the addresses of the tags it knows, of their ``srcset``, of their ``style`` and of its style blocks."""
    tags: page_files._Tags = page_files._Tags(files.html)
    tags.feed(files.html)
    tags.close()
    urls: list[str] = []
    for _, _, tag, attrs, _ in tags.tags:
        for name, value in attrs:
            if not value:
                continue
            if name == "srcset":
                urls.extend(value[start:end] for start, end in page_files._srcset_urls(value))
            elif name == "style":
                urls.extend(found["url"] for found in page_files._CSS_URL.finditer(value) if found["url"])
            elif (
                name in page_files._SHOWN.get(tag, ())
                or page_files._FRAMED.get(tag) == name
                or (tag == "link" and name == "href")
                or (tag == "script" and name == "src")
            ):
                urls.append(value)
    for start, end in tags.styles:
        urls.extend(found["url"] for found in page_files._CSS_URL.finditer(files.html[start:end]) if found["url"])
    return {name for name in map(page_files._beside, urls) if name is not None}


# The page of the final review's probe: a picture in a ``srcset``, in a ``picture``, an icon, a frame and an object.
PROBE: str = """<!doctype html><html><head>
<link rel="icon" href="icon.png">
<link rel="stylesheet" href="style.css">
</head><body>
<img src="a.png" srcset="a.png 1x, b.png 2x" alt="x">
<picture><source srcset="b.png" type="image/png"><img src="a.png"></picture>
<iframe src="part.html"></iframe>
<object data="a.png"></object>
<script src="app.js"></script>
</body></html>"""


def _probe_site(tmp_path: Path) -> tuple[Path, Path]:
    workspace, page = _bare_site(tmp_path)
    for name, data in {
        "a.png": b"\x89PNG-a",
        "b.png": b"\x89PNG-b",
        "icon.png": b"\x89PNG-icon",
        "part.html": b"<p>a part</p>",
        "style.css": b"body{background:url(a.png)}",
        "app.js": b"console.log(1)",
    }.items():
        (page.parent / name).write_bytes(data)
    page.write_text(PROBE, encoding="utf-8")
    return workspace, page


def _address(data: bytes, kind: str = "image/png") -> str:
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"


def test_the_pictures_of_a_srcset_and_the_icon_of_a_page_are_written_in(tmp_path: Path) -> None:
    """Item 612 (the final review): a page with ``srcset`` took the picture its browser chose (the second on every
    Retina Mac) from the file beside it, which a shown page cannot load, though the answer said nothing was left out."""
    workspace, page = _probe_site(tmp_path)
    files: PageFiles = with_its_files(PROBE, page, workspace, (), ())
    a, b, icon = _address(b"\x89PNG-a"), _address(b"\x89PNG-b"), _address(b"\x89PNG-icon")
    assert f'<link rel="icon" href="{icon}">' in files.html
    # The candidates are written in and their descriptors stay; the comma of a data: address is no end of a candidate.
    assert f'<img src="{a}" srcset="{a} 1x, {b} 2x" alt="x">' in files.html
    assert f'<picture><source srcset="{b}" type="image/png"><img src="{a}"></picture>' in files.html
    assert files.left_out == [] and files.too_large == []
    assert sorted(set(files.written)) == ["a.png", "app.js", "b.png", "icon.png", "style.css"]


@pytest.mark.parametrize("rel", ["icon", "shortcut icon", "apple-touch-icon", "apple-touch-icon-precomposed", "ICON"])
def test_every_icon_link_of_a_page_is_written_in_and_other_links_are_not(tmp_path: Path, rel: str) -> None:
    workspace, page = _probe_site(tmp_path)
    html: str = f'<link rel="{rel}" href="icon.png"><link rel="alternate" href="part.html">'
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert files.html == f'<link rel="{rel}" href="{_address(b"\x89PNG-icon")}"><link rel="alternate" href="part.html">'


@pytest.mark.parametrize(
    ("srcset", "addresses"),
    [
        ("a.png", ["a.png"]),
        ("a.png 1x, b.png 2x", ["a.png", "b.png"]),
        ("  a.png   320w ,b.png 640w,c.png  ", ["a.png", "b.png", "c.png"]),
        # A comma ends an address when it is its last character, and is a part of it when it is not (a data: address has
        # one): "a.png,b.png" is one address.
        ("a.png, b.png,c.png 2x", ["a.png", "b.png,c.png"]),
        ("a.png,b.png 2x", ["a.png,b.png"]),
        ("data:image/png;base64,AAAA 1x, b.png 2x", ["data:image/png;base64,AAAA", "b.png"]),
        # A comma inside parentheses of a descriptor does not end it.
        ("a.png (min-width: 1px, 2x), b.png", ["a.png", "b.png"]),
        ("", []),
        (" , ,, ", []),
    ],
)
def test_the_addresses_of_a_srcset_are_found_as_the_standard_reads_them(srcset: str, addresses: list[str]) -> None:
    assert [srcset[start:end] for start, end in page_files._srcset_urls(srcset)] == addresses


def test_a_candidate_of_a_srcset_that_cannot_be_written_in_stays_and_is_named(tmp_path: Path) -> None:
    workspace, page = _probe_site(tmp_path)
    html: str = '<img srcset="a.png 1x, gone.png 2x, https://example.com/c.png 3x">'
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert files.html == f'<img srcset="{_address(b"\x89PNG-a")} 1x, gone.png 2x, https://example.com/c.png 3x">'
    assert files.left_out == ["gone.png"]


def test_a_frame_an_object_and_an_embed_are_named_as_not_loaded_and_the_answer_names_what_is_left(
    tmp_path: Path,
) -> None:
    """Item 612: the policy of a shown page has no frame-src, object-src or child-src and its default-src is none, so
    nothing can be written into them. They stay as they were, and are named."""
    workspace, page = _probe_site(tmp_path)
    files: PageFiles = with_its_files(PROBE, page, workspace, (), ())
    assert '<iframe src="part.html"></iframe>' in files.html and '<object data="a.png"></object>' in files.html
    assert files.framed == ["part.html", "a.png"]
    more: PageFiles = with_its_files(
        '<embed src="clip.swf"><iframe src="https://example.com/x"></iframe><iframe src="#here"></iframe>',
        page,
        workspace,
        (),
        (),
    )
    # A web address and a fragment are no file beside the page.
    assert more.framed == ["clip.swf"]
    # What the page still names beside it, in every place the module reads, is said in the answer: it was true before
    # that "left out: none" (the page of the probe) when it left these as written without a word.
    assert _still_named(files) <= {*files.left_out, *files.too_large, *files.framed}
    assert _still_named(files) == {"part.html", "a.png"}
    page_shown: ShownPage = ShownPage(id="page_x", run_id="run_1", title="Probe", html=files.html, created_at=0.0)
    words: str = shown_words(page_shown, "http://127.0.0.1:8765/pages/page_x", files)
    assert "Not loaded, as a shown page loads no frame, object or embed" in words
    assert "part.html, a.png." in words and "Not written in" not in words


def test_a_page_that_names_nothing_beside_it_is_answered_as_before(tmp_path: Path) -> None:
    workspace, page = _bare_site(tmp_path)
    files: PageFiles = with_its_files("<p>Hello</p>", page, workspace, (), ())
    page_shown: ShownPage = ShownPage(id="page_x", run_id="run_1", title="Hello", html=files.html, created_at=0.0)
    assert files.html == "<p>Hello</p>" and (files.framed, files.cut) == ([], "")
    assert shown_words(page_shown, "http://127.0.0.1:8765/pages/page_x", files).endswith(
        "which your browser may open to see it as they do."
    )


def test_a_picture_that_many_img_tags_name_is_written_once_and_each_takes_it_from_there(tmp_path: Path) -> None:
    """Item 612 (the final review): an icon in every row of a thousand was written in a row at a time, used up the
    500,000 characters of the page after 360 rows, and left the other 640 rows with a broken picture."""
    workspace, page = _bare_site(tmp_path)
    icon: bytes = b"\x89PNG" + bytes(range(256)) * 4
    (page.parent / "check.png").write_bytes(icon)
    rows: int = 1000
    html: str = (
        "<table>"
        + "".join(f'<tr><td>row {number}</td><td><img src="check.png" alt="ok"></td></tr>' for number in range(rows))
        + "</table>"
    )
    started: float = time.perf_counter()
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert time.perf_counter() - started < 5.0
    assert files.too_large == [] and files.left_out == [] and files.written == ["check.png"] * rows
    assert len(files.html) < PAGE_MAX_CHARS // 4
    # The picture is in the page once, in a table at its end; a row has only the mark of its entry.
    payload: str = base64.b64encode(icon).decode("ascii")
    assert files.html.count(payload) == 1
    assert files.html.count('<img data-dot-pic="1" alt="ok">') == rows and 'src="check.png"' not in files.html
    assert files.html.count("<script>") == 1 and files.html.endswith("</script>")
    assert _still_named(files) == set()


def test_the_table_of_pictures_stands_before_the_end_of_the_body_and_cannot_end_its_script_early(
    tmp_path: Path,
) -> None:
    workspace, page = _bare_site(tmp_path)
    (page.parent / "a.png").write_bytes(b"\x89PNG-a")
    (page.parent / "b.png").write_bytes(b"\x89PNG-b")
    html: str = "<body>" + '<img src="a.png">' * 3 + '<img src="b.png">' * 4 + "</BODY></html>"
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    # Two pictures, two entries; the script is in the body, before its end in any letter case, and holds no "</".
    assert files.html.count('<img data-dot-pic="1">') == 3 and files.html.count('<img data-dot-pic="2">') == 4
    script: str = files.html[files.html.index("<script>") : files.html.index("</script>") + len("</script>")]
    assert files.html.index("<script>") < files.html.index("</BODY>") and script.count("</") == 1
    assert f'"1":"{_address(b"\x89PNG-a")}"' in script and f'"2":"{_address(b"\x89PNG-b")}"' in script
    assert files.html.endswith("</script></BODY></html>") or files.html.endswith("</BODY></html>")


def test_a_picture_named_less_than_three_times_stays_in_its_tags_and_one_with_a_srcset_does_too(
    tmp_path: Path,
) -> None:
    workspace, page = _bare_site(tmp_path)
    (page.parent / "a.png").write_bytes(b"\x89PNG-a")
    address: str = _address(b"\x89PNG-a")
    twice: PageFiles = with_its_files('<img src="a.png"><img src="a.png">', page, workspace, (), ())
    assert twice.html == f'<img src="{address}"><img src="{address}">' and "data-dot-pic" not in twice.html
    # Three tags with a srcset are written as each is: the table is for tags that have a src alone.
    html: str = '<img src="a.png" srcset="a.png 2x">' * 3
    several: PageFiles = with_its_files(html, page, workspace, (), ())
    assert several.html == f'<img src="{address}" srcset="{address} 2x">' * 3


def test_a_large_picture_that_many_tags_name_fits_the_page_once(tmp_path: Path) -> None:
    """A picture of 150,000 bytes is 200,000 characters: two of them were all a page held, the third was left out."""
    workspace, page = _bare_site(tmp_path)
    (page.parent / "hero.png").write_bytes(b"\x89PNG" + bytes(150_000))
    files: PageFiles = with_its_files('<img src="hero.png">' * 6, page, workspace, (), ())
    assert files.too_large == [] and files.written == ["hero.png"] * 6
    assert files.html.count('data-dot-pic="1"') == 6 and len(files.html) < PAGE_MAX_CHARS


def test_a_shared_picture_past_half_the_room_is_in_the_page_once_for_every_tag(tmp_path: Path) -> None:
    """The review of item 612: a picture whose address was more than half the room left fitted its first tag, and each
    tag after it asked for its whole size again: 999 rows of a thousand kept a name the page never loads, and the
    answer said the picture was left out though it was in the page. A tag after the first costs its mark alone."""
    workspace, page = _bare_site(tmp_path)
    (page.parent / "icon.png").write_bytes(b"\x89PNG" + bytes(240_000))
    html: str = "<table>" + '<tr><td><img src="icon.png"></td></tr>' * 1000 + "</table>"
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert files.too_large == [] and files.written == ["icon.png"] * 1000
    assert files.html.count('data-dot-pic="1"') == 1000 and 'src="icon.png"' not in files.html
    assert len(files.html) < PAGE_MAX_CHARS


def test_a_shared_picture_that_does_not_fit_is_named_as_too_large_in_every_tag_and_the_page_is_shown(
    tmp_path: Path,
) -> None:
    workspace, page = _bare_site(tmp_path)
    (page.parent / "huge.png").write_bytes(b"x" * PAGE_MAX_CHARS)
    html: str = '<img src="huge.png" alt="h">' * 4
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert files.html == html and files.too_large == ["huge.png"] * 4 and files.written == []


def test_a_file_named_many_times_is_found_read_and_encoded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 612: each naming of a file resolved it and read it again, 1.6 milliseconds each, and 22,000 of them took 36
    seconds of the daemon's interpreter."""
    workspace, page = _bare_site(tmp_path)
    (page.parent / "p.png").write_bytes(b"\x89PNG-p")
    (page.parent / "s.css").write_text("a{color:red}", encoding="utf-8")
    finds: list[str] = []
    reads: list[Path] = []
    find = page_files._Writer._find
    read = page_files._Writer._read

    def found(writer: page_files._Writer, named: str, folder: Path) -> tuple[str, Path, int] | None:
        finds.append(named)
        return find(writer, named, folder)

    def counted(writer: page_files._Writer, path: Path, limit: int) -> bytes | None:
        reads.append(path)
        return read(writer, path, limit)

    monkeypatch.setattr(page_files._Writer, "_find", found)
    monkeypatch.setattr(page_files._Writer, "_read", counted)
    html: str = (
        '<link rel="stylesheet" href="s.css">' * 50
        + '<svg><image href="p.png"/></svg>' * 50
        + '<div style="background:url(p.png)"></div>' * 50
        + '<img srcset="p.png 1x, p.png 2x">' * 50
        + '<video poster="p.png"></video>' * 50
    )
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert sorted(finds) == ["p.png", "s.css"] and sorted(path.name for path in reads) == ["p.png", "s.css"]
    assert files.left_out == [] and len(files.written) == 50 + 50 + 50 + 100 + 50


def test_the_writing_in_has_a_time_limit_after_which_the_rest_is_as_written_and_the_answer_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 612: a page that names thousands of different files has each found, judged, read and encoded, and holds the
    interpreter while it does. After the time it has, the page is shown as written from the tag it came to."""
    workspace, page = _bare_site(tmp_path)
    for number in range(300):
        (page.parent / f"f{number}.png").write_bytes(b"\x89PNG" + bytes([number % 256]) * 8)
    html: str = "".join(f'<img src="f{number}.png">' for number in range(300))
    monkeypatch.setattr(page_files, "WORK_S", 0.0)
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    # The time was up at the first file: nothing is written in, and the page is as it was written.
    assert files.html == html and files.written == [] and files.too_large == []
    assert files.cut == (
        "Writing the files in took more than 0 seconds, so the files named in the rest of the page are not written in."
    )
    # Given a little time it goes on a while and stops: what is written in is a whole number of tags, the rest is as
    # it was written.
    monkeypatch.setattr(page_files, "WORK_S", 0.02)
    more: PageFiles = with_its_files(html, page, workspace, (), ())
    assert 0 <= len(more.written) < 300 and more.cut.startswith("Writing the files in took more than 0.02 seconds")
    done: str = "".join(
        f'<img src="{_address(b"\x89PNG" + bytes([number % 256]) * 8)}">' for number in range(len(more.written))
    )
    assert more.html == done + "".join(f'<img src="f{number}.png">' for number in range(len(more.written), 300))
    # A page that does not take the time says nothing of it.
    monkeypatch.setattr(page_files, "WORK_S", 10.0)
    assert with_its_files(html, page, workspace, (), ()).cut == ""


def test_a_page_cut_by_the_time_limit_takes_back_the_names_of_the_tag_it_was_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``style`` attribute that names many files and is not finished is not written, so it names none as written."""
    workspace, page = _bare_site(tmp_path)
    (page.parent / "first.png").write_bytes(b"\x89PNG-1")
    for number in range(200):
        (page.parent / f"g{number}.png").write_bytes(b"\x89PNG-g")
    style: str = "".join(f"background:url(g{number}.png);" for number in range(200))
    html: str = f'<img src="first.png"><div style="{style}"></div>'
    real = page_files._Writer._find
    seen: list[str] = []

    def slow(writer: page_files._Writer, named: str, folder: Path) -> tuple[str, Path, int] | None:
        seen.append(named)
        if len(seen) == 5:
            monkeypatch.setattr(page_files, "WORK_S", 0.0)
            writer._deadline = 0.0
        return real(writer, named, folder)

    monkeypatch.setattr(page_files._Writer, "_find", slow)
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert files.html == f'<img src="{_address(b"\x89PNG-1")}"><div style="{style}"></div>'
    assert files.written == ["first.png"] and files.cut != ""


@pytest.mark.parametrize(
    "page_of",
    [
        lambda size: "<svg>" + '<image href="p.png"/>' * (size // 22) + "</svg>",
        lambda size: '<div style="' + "background:url(p.png);" * (size // 22) + '"></div>',
        lambda size: '<link rel="stylesheet" href="s.css">' * (size // 38),
        lambda size: '<img src="p.png">' * (size // 17),
        lambda size: '<img srcset="p.png 1x, p.png 2x">' * (size // 34),
        lambda size: '<link rel="icon" href="p.png">' * (size // 30),
        lambda size: '<iframe src="s.css"></iframe>' * (size // 29),
    ],
    ids=["svg image", "style attribute", "linked sheets", "img tags", "srcset", "icons", "frames"],
)
def test_a_pathological_page_of_a_page_of_characters_is_written_in_in_a_moment(tmp_path: Path, page_of: object) -> None:
    """Item 612 (the final review): 22,000 ``image`` tags took 36 seconds, one ``style`` attribute with 22,000 ``url()``
    26, 13,000 linked sheets 21: each naming of a file was found and read again."""
    workspace, page = _bare_site(tmp_path)
    (page.parent / "p.png").write_bytes(b"\x89PNG" + bytes(10))
    (page.parent / "s.css").write_text("a{color:red}", encoding="utf-8")
    html: str = page_of(480_000)  # type: ignore[operator]
    started: float = time.perf_counter()
    files: PageFiles = with_its_files(html, page, workspace, (), ())
    assert time.perf_counter() - started < 5.0
    assert len(files.html) <= PAGE_MAX_CHARS and files.cut == ""


def test_the_answer_of_a_page_the_time_cut_says_so() -> None:
    page_shown: ShownPage = ShownPage(id="page_x", run_id="run_1", title="Big", html="<p>x</p>", created_at=0.0)
    files: PageFiles = PageFiles("<p>x</p>", cut="Writing the files in took more than 10 seconds, so ...")
    assert shown_words(page_shown, "http://127.0.0.1:8765/pages/page_x", files).endswith(
        " Writing the files in took more than 10 seconds, so ..."
    )

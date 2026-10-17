"""What the pages are (item 190) and which pages the daemon serves (item 309). The digest the daemon tells with its
status follows the bytes and the place of every file it serves, and nothing else (where the files are, when they were
written, a hidden file), so that a page tells an update of Dot from a restart. The daemon serves the pages as it read
them when it started, as Starlette's StaticFiles serves a folder: a file changed, added or removed on disk afterwards
is served as it was, or not at all, until the daemon starts again."""

from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Mount

from dotagent.server.kept_pages import KeptPages, ServedPages, keep_pages

FILES: dict[str, str] = {"index.html": "<p>Dot</p>", "js/main.js": "export {};"}


def _pages(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path: Path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_pages_build_follows_the_files_and_nothing_else(tmp_path: Path) -> None:
    first: str = keep_pages(_pages(tmp_path / "a", FILES)).build
    assert keep_pages(_pages(tmp_path / "b", FILES)).build == first
    assert keep_pages(_pages(tmp_path / "c", {**FILES, "js/main.js": "export const a = 1;"})).build != first
    assert keep_pages(_pages(tmp_path / "d", {"index.html": "<p>Dot</p>", "js/app.js": "export {};"})).build != first
    # A hidden file, or one in a hidden folder (a tool's cache), is no page.
    hidden: KeptPages = keep_pages(_pages(tmp_path / "e", {**FILES, ".DS_Store": "x", "js/.mypy_cache/a.json": "{}"}))
    assert (hidden.build, sorted(hidden.files)) == (first, ["index.html", "js/main.js"])


def _client(pages: KeptPages) -> httpx.AsyncClient:
    app: Starlette = Starlette(routes=[Mount("/", app=ServedPages(pages))])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dot.test")


async def test_the_pages_served_are_those_read_when_the_daemon_started(tmp_path: Path) -> None:
    root: Path = _pages(tmp_path / "pages", {**FILES, "help/index.html": "<p>Help</p>"})
    pages: KeptPages = keep_pages(root)
    # The files change on disk while the daemon works: a merge, a batch of work ported in.
    (root / "js" / "main.js").write_text("export const changed = true;", encoding="utf-8")
    (root / "js" / "new.js").write_text("export {};", encoding="utf-8")
    (root / "index.html").unlink()
    async with _client(pages) as client:
        index: httpx.Response = await client.get("/")
        assert (index.status_code, index.text) == (200, "<p>Dot</p>")
        assert index.headers["content-type"] == "text/html; charset=utf-8"
        script: httpx.Response = await client.get("/js/main.js")
        assert (script.status_code, script.text) == (200, "export {};")
        assert script.headers["content-type"] == "text/javascript; charset=utf-8"
        assert (await client.get("/js/new.js")).status_code == 404
        # As StaticFiles serves them: a page the browser holds as it is answers 304, HEAD has no body, only GET and
        # HEAD are taken, and a folder's path is sent on to the one that ends in "/".
        held: httpx.Response = await client.get("/js/main.js", headers={"if-none-match": script.headers["etag"]})
        assert (held.status_code, held.content) == (304, b"")
        head: httpx.Response = await client.head("/js/main.js")
        assert (head.status_code, head.content, head.headers["content-length"]) == (200, b"", "10")
        assert (await client.post("/js/main.js")).status_code == 405
        folder: httpx.Response = await client.get("/help")
        assert (folder.status_code, folder.headers["location"]) == (307, "http://dot.test/help/")
        assert (await client.get("/help/")).text == "<p>Help</p>"

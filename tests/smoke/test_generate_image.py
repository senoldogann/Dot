"""``generate_image``'s drawn path (item 1969), end to end in a real headless Chrome: an SVG and an HTML page are
drawn to a PNG of the size asked and saved in the workspace, the run is shown the picture, and a page the run wrote can
run no script and load nothing from the network."""

import base64
import io
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image
from test_browser_eval import _run_context

from dotagent.config import load_config
from dotagent.core.errors import ToolInputError
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import tools_for_mode

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_SVG: str = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#fff"/>'
    '<rect x="40" y="20" width="80" height="50" fill="#d00000"/></svg>'
)
_TINY: bytes = base64.b64decode("R0lGODlhAQABAIAAAAUEBAAAACwAAAAAAQABAAACAkQBADs=")
_hits: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        _hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "image/gif")
        self.send_header("Content-Length", str(len(_TINY)))
        self.end_headers()
        self.wfile.write(_TINY)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


def _picture(result: ToolResult) -> Image.Image:
    """The PNG the owner is shown and the file keeps: what ``shown_images`` holds."""
    assert len(result.shown_images) == 1 and result.shown_images[0].media_type == "image/png"
    return Image.open(io.BytesIO(base64.b64decode(result.shown_images[0].data_b64))).convert("RGB")


async def test_an_svg_is_drawn_at_the_size_asked_saved_in_the_workspace_and_shown(runtime: Runtime) -> None:
    assert "generate_image" in {tool.spec.name for tool in tools_for_mode(runtime.registry, "task")}
    assert "generate_image" not in {tool.spec.name for tool in tools_for_mode(runtime.registry, "research")}
    ctx: ToolContext = await _run_context(runtime)
    call: dict[str, object] = {"path": "cards/logo.png", "svg": _SVG, "width": 640, "height": 360}
    prepared: PreparedCall = await runtime.registry.tools["generate_image"].prepare(call, ctx)
    action: ActionInfo = prepared.action
    assert (action.tool, action.kind, action.risk, action.flags) == ("generate_image", "write", "low", ())
    assert action.target == str(ctx.workspace / "cards" / "logo.png")
    result: ToolResult = await prepared.run(ctx)
    assert not result.is_error, result.text
    picture: Image.Image = _picture(result)
    assert picture.size == (640, 360)
    assert picture.getpixel((320, 180)) == (208, 0, 0)
    assert picture.getpixel((10, 10)) == (255, 255, 255)
    saved: Image.Image = Image.open(ctx.workspace / "cards" / "logo.png").convert("RGB")
    assert saved.size == (640, 360) and saved.getpixel((320, 180)) == (208, 0, 0)
    # The run itself is shown the picture too, small enough to read: a JPEG of at most 1024 pixels on a side.
    assert len(result.images) == 1 and result.images[0].media_type == "image/jpeg"
    assert "Saved" in result.text and "640x360" in result.text


async def test_a_page_the_run_wrote_draws_its_css_and_runs_no_script_and_loads_nothing(
    runtime: Runtime, site: str
) -> None:
    ctx: ToolContext = await _run_context(runtime)
    page: str = (
        "<!doctype html><html><body style='background:#0000d0;width:400px;height:200px'>"
        "<script>document.body.style.background='#00d000'; new Image().src='" + site + "/script.gif'</script>"
        f"<img src='{site}/remote.gif' width='50' height='50'>"
        "<style>body{background-image:url(" + site + "/css.gif)}</style></body></html>"
    )
    prepared: PreparedCall = await runtime.registry.tools["generate_image"].prepare(
        {"path": "cards/page.png", "html": page, "width": 400, "height": 200}, ctx
    )
    result: ToolResult = await prepared.run(ctx)
    assert not result.is_error, result.text
    picture: Image.Image = _picture(result)
    assert picture.size == (400, 200)
    # Its stylesheet drew; its script did not run, and nothing was fetched from the site it named.
    assert picture.getpixel((300, 150)) == (0, 0, 208)
    assert _hits == []


async def test_a_path_that_is_no_png_and_text_that_is_no_svg_are_refused_before_anything_is_drawn(
    runtime: Runtime,
) -> None:
    ctx: ToolContext = await _run_context(runtime)
    with pytest.raises(ToolInputError, match=r"must end in \.png"):
        await runtime.registry.tools["generate_image"].prepare({"path": "a.jpg", "svg": _SVG}, ctx)
    prepared: PreparedCall = await runtime.registry.tools["generate_image"].prepare(
        {"path": "cards/not-svg.png", "svg": "<p>not a drawing</p>"}, ctx
    )
    with pytest.raises(ToolInputError, match="not an SVG document"):
        await prepared.run(ctx)
    assert not (ctx.workspace / "cards" / "not-svg.png").exists()

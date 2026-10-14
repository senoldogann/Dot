"""The agent drives the browser end to end: a live model opens a form, fills it through the browser tools and submits it.

The page comes from a local fixture server that keeps what the form sent, so the check is what reached the server and
not what the model says about it. The test uses the real local Ollama server with a cloud model and a real headless
Chrome; it is skipped when the model is not there.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.live, pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

OLLAMA_ROOT: str = "http://localhost:11434"
MODEL: str = "gpt-oss:20b-cloud"
MODEL_REF: str = f"ollama:{MODEL}"
RUN_TIMEOUT_S: float = 240.0
SUBMISSION: str = "/done?name=Ada&color=blue&agree=on"
_PAGES: dict[str, str] = {
    "/form": """<!doctype html><title>Newsletter form</title><h1>Newsletter</h1>
<form action="/done" method="get">
<label>Name <input name="name"></label>
<label>Color <select name="color"><option value="red">Red</option><option value="blue">Blue</option></select></label>
<label><input type="checkbox" name="agree"> I agree</label>
<button type="submit">Submit form</button>
</form>""",
    "/done": "<!doctype html><title>Done</title><h1>Received</h1><p>Thank you, your answers were recorded.</p>",
}


def model_is_listed() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == MODEL for model in answer.json()["models"])


requires_model = pytest.mark.skipif(not model_is_listed(), reason=f"needs the local Ollama server with {MODEL}")


class _FormSite(ThreadingHTTPServer):
    """A local web server with one form page; it keeps the address of every submission it receives."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _FormHandler)
        self.submissions: list[str] = []


class _FormHandler(BaseHTTPRequestHandler):
    server: _FormSite

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        if path == "/done":
            self.server.submissions.append(self.path)
        body: bytes = _PAGES.get(path, "not found").encode()
        self.send_response(200 if path in _PAGES else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[_FormSite]:
    server: _FormSite = _FormSite()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("home")
    (path / "config.toml").write_text(
        f'[models]\nmain = "{MODEL_REF}"\n\n[browser]\nheadless = true\n\n[agent]\nmax_concurrent_runs = 1\n',
        encoding="utf-8",
    )
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@requires_model
async def test_the_agent_fills_in_and_submits_a_form_in_the_browser(runtime: Runtime, site: _FormSite) -> None:
    manager: RunManager = RunManager(runtime)
    goal: str = (
        f"Open http://127.0.0.1:{site.server_address[1]}/form in the browser. Fill the Name field with Ada, choose the "
        "color blue, tick the 'I agree' checkbox and press the 'Submit form' button. Then call finish and tell me what "
        "the confirmation page says."
    )
    try:
        run: RunRecord = await manager.start(goal, "task", None, None, "test", None, None, "standard", None, None, ())
        finished: RunRecord = await asyncio.wait_for(manager.wait(run.id), RUN_TIMEOUT_S)
    finally:
        await manager.shutdown()
    assert finished.status == "done", finished.result
    assert SUBMISSION in site.submissions, site.submissions

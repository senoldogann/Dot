"""How fast browser use is in the Chrome Dot attaches to (item 339), measured the same way before and after a change: a
scratch Chrome with remote debugging stands in for the owner's (no test touches the owner's Chrome), on a page served on
this Mac.

- the connection to Chrome as the daemon's first browser call makes it (the driver started, Chrome connected to);
- (a) browser_open to its first view: in a new chat (its tab is made, the page loaded, the view read) and again in the
  chat's tab;
- (b) one browser_act of three actions (type into a field, choose an option, click a button);
- a snapshot and a screenshot of the page as it is;
- a page that draws its content in a frame of its own, as an app does: browser_open and the wait for its text;
- (d) a task of ten steps through the real loop, on the scripted model: the time Dot itself takes, as the model's is nil.

Each is done five times; the medians (milliseconds) are printed as JSON, and written to the file DOT_SPEED_OUT names when
it is set. The test checks that each call did its work, never how fast: the numbers are for the record of a change.
"""

import asyncio
import json
import os
import statistics
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from test_browser import ref_of
from test_browser_attach import SERVER, ScratchChrome, start_chrome, stop_chrome

from dotagent.agent.manager import RunManager
from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ActOutcome, PageView, Step, TextWait
from dotagent.config import BrowserConfig, load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_RUNS: int = 5
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 120.0
_FORM: str = """<!doctype html><html><head><title>Form</title></head><body>
<h1>Weekly report</h1>
<label>Name <input id="name" name="name"></label>
<label>Week <select id="week"><option>1</option><option>2</option><option>3</option></select></label>
<button id="save" onclick="document.getElementById('said').textContent = 'Saved ' + document.getElementById('name').value">Save</button>
<p id="said"></p>
<a href="/second">Second page</a>
<ul>{items}</ul>
</body></html>"""
_SECOND: str = "<!doctype html><title>Second</title><h1>Second page</h1><a href='/form'>Back to the form</a>"
# Drawn two frames after it loads, as an app draws its first view: a tab Chrome draws no frames for never shows it.
_APP: str = """<!doctype html><title>App</title><div id="root">Starting</div>
<script>requestAnimationFrame(() => requestAnimationFrame(() => {
  document.getElementById('root').innerHTML = '<h1>Ready</h1><button>Go</button><a href="/second">Next</a>';
}));</script>"""
_APP_WAIT_S: float = 5.0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        page: str = _SECOND
        if self.path.startswith("/form"):
            page = _FORM.format(items="".join(f"<li><a href='/item/{n}'>Item {n}</a></li>" for n in range(60)))
        elif self.path.startswith("/app"):
            page = _APP
        body: bytes = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("chrome")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chrome(data_dir: Path, site: str) -> AsyncIterator[ScratchChrome]:
    running: ScratchChrome = await start_chrome(data_dir, f"{site}/second")
    try:
        yield running
    finally:
        await stop_chrome(running)


def _attach_config(home: Path) -> BrowserConfig:
    settings: Path = home / "config.toml"
    settings.write_text('[browser]\nmode = "attach"\nheadless = true\nattach_timeout_s = 20.0\n', encoding="utf-8")
    return load_config(settings).browser


def _record(name: str, medians: dict[str, float]) -> None:
    """Print ``medians`` under ``name``, and add them to the file DOT_SPEED_OUT names."""
    print(f"SPEED {name} {json.dumps(medians)}")
    out: str | None = os.environ.get("DOT_SPEED_OUT")
    if out is None:
        return
    path: Path = Path(out)
    kept: dict[str, dict[str, float]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    kept[name] = medians
    path.write_text(json.dumps(kept, indent=2), encoding="utf-8")


def _median_ms(seconds: list[float]) -> float:
    return round(statistics.median(seconds) * 1000, 1)


async def test_browser_use_in_an_attached_chrome(chrome: ScratchChrome, site: str, tmp_path: Path) -> None:
    session: BrowserSession = BrowserSession(
        _attach_config(tmp_path), SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
    )
    timings: dict[str, list[float]] = {
        "connect": [],
        "open": [],
        "open_again": [],
        "act3": [],
        "snapshot": [],
        "screenshot": [],
        "app": [],
    }
    drawn: int = 0
    # A connection as the daemon's first browser call makes it: the driver started, Chrome connected to.
    for _ in range(_RUNS):
        fresh: BrowserSession = BrowserSession(
            _attach_config(tmp_path), SERVER, chrome.data_dir, tmp_path / "downloads", no_runs_at_work
        )
        try:
            started: float = time.monotonic()
            await fresh.attachment()
            timings["connect"].append(time.monotonic() - started)
        finally:
            await fresh.aclose()
    try:
        await session.attachment()
        for run in range(_RUNS):
            thread: str = f"speed-{run}"
            started = time.monotonic()
            view: PageView = await session.open_url(thread, f"{site}/form")
            timings["open"].append(time.monotonic() - started)
            assert view.title == "Form"
            steps: list[Step] = [
                Step(action="type", ref=ref_of(view.body, "textbox"), text=f"Run {run}"),
                Step(action="select", ref=ref_of(view.body, "combobox"), text="2"),
                Step(action="click", ref=ref_of(view.body, 'button "Save"')),
            ]
            started = time.monotonic()
            done: ActOutcome = await session.act(thread, steps, False, refuse_secret_field)
            timings["act3"].append(time.monotonic() - started)
            assert [report.outcome for report in done.steps] == ["done", "done", "done"], done.steps
            started = time.monotonic()
            again: PageView = await session.snapshot(thread)
            timings["snapshot"].append(time.monotonic() - started)
            assert f"Saved Run {run}" in again.body
            started = time.monotonic()
            await session.screenshot(thread)
            timings["screenshot"].append(time.monotonic() - started)
            started = time.monotonic()
            reopened: PageView = await session.open_url(thread, f"{site}/form?again")
            timings["open_again"].append(time.monotonic() - started)
            assert reopened.title == "Form"
            started = time.monotonic()
            await session.open_url(thread, f"{site}/app")
            ready: TextWait = await session.wait_text(thread, "Ready", _APP_WAIT_S)
            timings["app"].append(time.monotonic() - started)
            drawn += ready.appeared
            await session.release_thread(thread)
    finally:
        await session.aclose()
    _record("browser", {**{name: _median_ms(seconds) for name, seconds in timings.items()}, "app_drawn": drawn})


def _call(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


def _task_home(tmp_path: Path) -> Path:
    """A Dot home whose runs work in the Chrome Dot attaches to, on the scripted model, with no learning after."""
    home: Path = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'[browser]\nmode = "attach"\nheadless = true\nattach_timeout_s = 20.0\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        '\n[memory]\nembed_enabled = false\n\n[agent]\nlessons = "off"\nplaybooks = false\n',
        encoding="utf-8",
    )
    return home


def _task_turns(site: str) -> list[ScriptedTurn]:
    """The ten steps of one task: pages opened, read, searched and left, then the answer."""
    return [
        _call("browser_open", url=f"{site}/form"),
        _call("browser_snapshot"),
        _call("browser_find", text="Save"),
        _call("browser_open", url=f"{site}/second"),
        _call("browser_snapshot"),
        _call("browser_back"),
        _call("browser_snapshot"),
        _call("browser_open", url=f"{site}/second"),
        _call("browser_snapshot"),
        ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
    ]


async def test_a_task_of_ten_steps_through_the_loop(chrome: ScratchChrome, site: str, tmp_path: Path) -> None:
    """Ten steps on the scripted model, in the Chrome Dot attaches to: what a run spends around the model."""
    home: Path = await asyncio.to_thread(_task_home, tmp_path)
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(chrome.data_dir))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    totals: list[float] = []
    tools: list[float] = []
    by_tool: dict[str, list[float]] = {}
    try:
        await runtime.router.register(
            _PROVIDER, ScriptedProvider(_PROVIDER, [turn for _ in range(_RUNS) for turn in _task_turns(site)])
        )
        manager: RunManager = RunManager(runtime)
        try:
            for run_number in range(_RUNS):
                started: float = time.monotonic()
                run: RunRecord = await manager.start(
                    f"Speed task {run_number}",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    "test",
                    None,
                    None,
                    "standard",
                    None,
                    "yolo",
                    None,
                    (),
                )
                async with asyncio.timeout(_RUN_WAIT_S):
                    ended: RunRecord = await manager.wait(run.id)
                totals.append(time.monotonic() - started)
                assert ended.status == "done", ended.result
                results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 50)
                assert len(results) >= 9, len(results)
                tools.append(sum(float(str(event.data.get("seconds", 0.0))) for event in results))
                for event in results:
                    by_tool.setdefault(str(event.data.get("tool")), []).append(
                        float(str(event.data.get("seconds", 0.0)))
                    )
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)
        patch.undo()
    _record(
        "task",
        {
            "ten_steps": _median_ms(totals),
            "tools_in_it": _median_ms(tools),
            **{f"tool {name}": _median_ms(seconds) for name, seconds in sorted(by_tool.items())},
        },
    )

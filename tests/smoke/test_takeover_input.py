"""Using a held run's page from the screen that shows it (item 470), end to end on a local fixture page and a real
headless Chrome: the screen's taps, scrolls and keys, sent as fractions of the picture, reach the page whatever its zoom
or size; input waits out a step under way and lands only while the run is held; a page that fails keeps what was typed
out of its error. Through the daemon: a run held from the screen, in a browser without a window, takes the owner's input
and records counts only, the routes answer the owner only, and a campaign's cast and input come from its own browser.

The run is played by the scripted model (no model quota)."""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.gate import HANDBACK_EVENT, TAKEOVER_EVENT
from dotagent.agent.manager import RunManager
from dotagent.browser.attach import DebugEndpoint
from dotagent.browser.remote_input import InputBatch, InputEvent, InputResult
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import Frame, ScriptRun
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError, RunConflict
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
_PROVIDER: str = "ollama"
TOKEN: str = "takeover-input-token"
_RUN_WAIT_S: float = 60.0
_SCRIPT_S: float = 5.0
# What the owner types in the tests: it must be found in the page and nowhere in Dot's records.
SENTINEL: str = "Typed-by-the-owner-7f3a9c"
# A page whose button fills the middle fifth of the window and whose field is near its top left corner, both fixed, so
# that the same fractions name them however the page is drawn; it is taller than the window, to be scrolled.
_PAGE: bytes = b"""<!doctype html><html><head><title>Play</title><style>
body { margin: 0; height: 3000px; font: 16px sans-serif; }
#press { position: fixed; left: 40vw; top: 40vh; width: 20vw; height: 20vh; }
#form { position: fixed; left: 5vw; top: 5vh; width: 30vw; height: 6vh; margin: 0; }
#field { width: 100%; height: 100%; box-sizing: border-box; font-size: 16px; }
</style></head><body>
<form id="form"><input id="field" autocomplete="off"></form>
<button id="press" type="button">Press</button>
<p id="count">0</p><p id="submitted">0</p>
<script>
document.getElementById("press").addEventListener("click", () => {
  const count = document.getElementById("count");
  count.textContent = String(Number(count.textContent) + 1);
});
document.getElementById("form").addEventListener("submit", (event) => {
  event.preventDefault();
  const submitted = document.getElementById("submitted");
  submitted.textContent = String(Number(submitted.textContent) + 1);
});
</script></body></html>"""
_BUTTON: tuple[float, float] = (0.5, 0.5)
_FIELD: tuple[float, float] = (0.2, 0.08)


class _Site(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Recorder:
    """The scripted model under any name, registered once for the module; each run sets the ``script`` it plays."""

    def __init__(self) -> None:
        self.script: ScriptedProvider | None = None

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        assert self.script is not None, "the test did not set a script"
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await ScriptedProvider(_PROVIDER, ()).model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await ScriptedProvider(_PROVIDER, ()).list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest.fixture(scope="module")
def site() -> Iterator[_Site]:
    server: _Site = _Site()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _headless(tmp_path: Path, update: dict[str, object]) -> BrowserConfig:
    return load_config(tmp_path / "none.toml").browser.model_copy(update={"headless": True, **update})


@pytest_asyncio.fixture(loop_scope="module")
async def session(tmp_path: Path) -> AsyncIterator[BrowserSession]:
    """A headless Chrome of its own, at the page's own size."""
    built: BrowserSession = BrowserSession(
        _headless(tmp_path, {}), SERVER, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    try:
        yield built
    finally:
        await built.aclose()


def _batch(*events: dict[str, object]) -> list[InputEvent]:
    return InputBatch.model_validate({"events": list(events)}).events


def _tap(at: tuple[float, float]) -> list[InputEvent]:
    x, y = at
    return _batch(
        {"kind": "move", "x": x, "y": y},
        {"kind": "down", "x": x, "y": y, "button": "left", "clicks": 1},
        {"kind": "up", "x": x, "y": y, "button": "left", "clicks": 1},
    )


def _held() -> bool:
    return True


async def _read(browser: BrowserSession, thread: str, script: str) -> object:
    """What ``script`` gives back in the thread's page (its value comes as JSON)."""
    ran: ScriptRun = await browser.run_script(thread, script, _SCRIPT_S)
    value: object = json.loads(ran.text)
    return value


async def _taps_scrolls_and_keys_reach(browser: BrowserSession, thread: str) -> None:
    taken: InputResult = await browser.send_input(thread, _tap(_BUTTON), _held)
    assert taken.accepted == 3 and taken.width > 0 and taken.height > 0, taken
    assert await _read(browser, thread, "document.getElementById('count').textContent") == "1"
    await browser.send_input(thread, _batch({"kind": "wheel", "x": 0.5, "y": 0.5, "dx": 0, "dy": 600}), _held)
    await asyncio.sleep(0.3)
    scrolled: object = await _read(browser, thread, "window.scrollY")
    assert isinstance(scrolled, int | float) and scrolled > 0, scrolled
    await browser.send_input(thread, _tap(_FIELD), _held)
    await browser.send_input(thread, _batch({"kind": "text", "text": SENTINEL}, {"kind": "key", "key": "Enter"}), _held)
    assert await _read(browser, thread, "document.getElementById('field').value") == SENTINEL
    assert await _read(browser, thread, "document.getElementById('submitted').textContent") == "1"


@pytest.mark.browser
@pytest.mark.parametrize(
    ("zoom", "fit_window"), [(100, False), (125, False), (100, True)], ids=["as-set", "zoom-125", "fit-window"]
)
async def test_the_screens_taps_scrolls_and_keys_reach_the_page_whatever_its_zoom_or_size(
    site: _Site, tmp_path: Path, zoom: int, fit_window: bool
) -> None:
    """The same fractions of the picture name the same button with Chrome's zoom at 125 % and with the page filling
    its window: they are turned into the page's CSS pixels as the page measures them."""
    browser: BrowserSession = BrowserSession(
        _headless(tmp_path, {"zoom": zoom, "fit_window": fit_window}),
        SERVER,
        tmp_path / "profile",
        tmp_path / "downloads",
        no_runs_at_work,
    )
    try:
        await browser.open_url("play", f"{site.base}/play", True)
        await _taps_scrolls_and_keys_reach(browser, "play")
    finally:
        await browser.aclose()


@pytest.mark.browser
async def test_input_waits_out_a_step_under_way_and_lands_only_while_the_run_is_held(
    session: BrowserSession, site: _Site
) -> None:
    with pytest.raises(RunConflict, match="no page open"):
        await session.send_input("nothing", _tap(_BUTTON), _held)
    await session.open_url("held", f"{site.base}/play", True)
    # A step under way holds the thread's lock: the input waits a moment, then is refused.
    thread = session._threads["held"]
    async with thread.lock:
        started: float = time.monotonic()
        with pytest.raises(RunConflict, match="still going"):
            await session.send_input("held", _tap(_BUTTON), _held)
        assert 1.5 <= time.monotonic() - started <= 5.0
    # Handed back while the input waited: it is not done.
    with pytest.raises(RunConflict, match="not taken over"):
        await session.send_input("held", _tap(_BUTTON), lambda: False)
    assert await _read(session, "held", "document.getElementById('count').textContent") == "0"
    session.set_remote_input(False)
    try:
        with pytest.raises(RunConflict, match="remote_input"):
            await session.send_input("held", _tap(_BUTTON), _held)
    finally:
        session.set_remote_input(True)
    await session.send_input("held", _tap(_BUTTON), _held)
    assert await _read(session, "held", "document.getElementById('count').textContent") == "1"


@pytest.mark.browser
async def test_a_page_that_fails_keeps_what_was_typed_out_of_its_error(
    session: BrowserSession, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever fails while the input is done, the error says the page's address and the kinds of the events, never
    what was typed, and does not carry the failure that might."""

    async def broken(page: object, viewport: object, events: object) -> None:
        raise RuntimeError(f"the page broke on {SENTINEL}")

    await session.open_url("broken", f"{site.base}/play", True)
    monkeypatch.setattr("dotagent.browser.session.dispatch", broken)
    with capture_logs() as logs, pytest.raises(BrowserError) as failed:
        await session.send_input("broken", _batch({"kind": "text", "text": SENTINEL}), _held)
    assert failed.value.context["events"] == ["text"] and failed.value.context["error_type"] == "RuntimeError"
    assert SENTINEL not in str(failed.value) and failed.value.__cause__ is None
    assert SENTINEL not in json.dumps(logs, default=str)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A daemon's runtime whose Chrome has no window: Take over has nothing to bring up there, the screen has."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime, manager: RunManager) -> AsyncIterator[str]:
    """A real uvicorn server on a free port: its address."""
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving


@pytest_asyncio.fixture(loop_scope="module")
async def client(served: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
    ) as http:
        yield http


def _call(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


_FINISH: ScriptedTurn = _call(FINISH_TOOL, {"answer": "The page was used.", "success": True})


def _events(*events: dict[str, object]) -> dict[str, object]:
    return {"events": list(events)}


def _tapped(at: tuple[float, float]) -> dict[str, object]:
    x, y = at
    return _events(
        {"kind": "down", "x": x, "y": y, "button": "left", "clicks": 1},
        {"kind": "up", "x": x, "y": y, "button": "left", "clicks": 1},
    )


async def _results(runtime: Runtime, run_id: str, count: int) -> None:
    """Wait until ``count`` steps of the run have their results."""
    async with asyncio.timeout(_RUN_WAIT_S):
        while True:
            if len(await runtime.store.events_of_kind(run_id, "tool_result", 10)) >= count:
                return
            await asyncio.sleep(0.1)


@pytest.mark.browser
async def test_a_run_held_from_the_screen_takes_the_owners_input_and_records_counts_only(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, site: _Site
) -> None:
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _call("browser_open", {"url": f"{site.base}/play"}),
            # Taken over while this step runs, the run holds at the next one.
            _call("shell", {"command": "sleep 5"}),
            _call("shell", {"command": "echo on"}),
            _FINISH,
        ],
    )
    with capture_logs() as logs:
        run: RunRecord = await manager.start(
            "Use the page", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        # The page is open once the first step has its result.
        await _results(runtime, run.id, 1)
        early: httpx.Response = await client.post(f"/api/runs/{run.id}/input", json=_tapped(_BUTTON))
        assert early.status_code == 409, early.text
        # Take over still needs a window to bring up; the screen does not.
        on_the_mac: httpx.Response = await client.post(f"/api/runs/{run.id}/takeover")
        assert on_the_mac.status_code == 409, on_the_mac.text
        taken: httpx.Response = await client.post(f"/api/runs/{run.id}/takeover/screen")
        assert taken.status_code == 202, taken.text
        assert taken.json()["held"] is True
        tapped: httpx.Response = await client.post(f"/api/runs/{run.id}/input", json=_tapped(_BUTTON))
        assert tapped.status_code == 200, tapped.text
        assert tapped.json()["accepted"] == 2 and tapped.json()["url"] == f"{site.base}/play"
        scrolled: httpx.Response = await client.post(
            f"/api/runs/{run.id}/input", json=_events({"kind": "wheel", "x": 0.5, "y": 0.5, "dx": 0, "dy": 500})
        )
        assert scrolled.status_code == 200, scrolled.text
        assert (await client.post(f"/api/runs/{run.id}/input", json=_tapped(_FIELD))).status_code == 200
        typed: httpx.Response = await client.post(
            f"/api/runs/{run.id}/input",
            json=_events({"kind": "text", "text": SENTINEL}, {"kind": "key", "key": "Enter"}),
        )
        assert typed.status_code == 200, typed.text
        assert await _read(runtime.browser, run.thread_id, "document.getElementById('count').textContent") == "1"
        assert await _read(runtime.browser, run.thread_id, "document.getElementById('field').value") == SENTINEL
        # Switched off in Settings › Browser, the page is not used from the screen.
        assert (await client.post("/api/browser/remote-input", json={"remote_input": False})).json()[
            "remote_input"
        ] is False
        try:
            off: httpx.Response = await client.post(f"/api/runs/{run.id}/input", json=_tapped(_BUTTON))
            assert off.status_code == 409 and "remote_input" in off.text, off.text
        finally:
            await client.post("/api/browser/remote-input", json={"remote_input": True})
        refused: httpx.Response = await client.post(
            f"/api/runs/{run.id}/input", json=_events({"kind": "key", "key": "Enter; rm -rf"})
        )
        assert refused.status_code == 422, refused.text
        # The step under way when the run was taken over ends, and the next one waits for the owner.
        await _results(runtime, run.id, 2)
        back: httpx.Response = await client.post(f"/api/runs/{run.id}/handback")
        assert back.status_code == 202, back.text
        late: httpx.Response = await client.post(f"/api/runs/{run.id}/input", json=_tapped(_BUTTON))
        assert late.status_code == 409, late.text
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    takeovers: list[EventRecord] = await runtime.store.events_of_kind(run.id, TAKEOVER_EVENT, 5)
    assert [(event.data["by"], event.data["via_reach_name"]) for event in takeovers] == [("screen", False)]
    handbacks: list[EventRecord] = await runtime.store.events_of_kind(run.id, HANDBACK_EVENT, 5)
    assert [event.data["inputs"] for event in handbacks] == [{"clicks": 2, "scrolls": 1, "typed": True}]
    # The call that waited is told the owner used the browser, so the model looks again first.
    results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 10)
    assert any("took over the browser" in str(event.data["text"]) for event in results), results
    # What the owner typed is in the page only: in no event, no record of the run and no log.
    events: list[EventRecord] = await runtime.store.list_events(run.id, 0, 1000)
    assert SENTINEL not in json.dumps([event.data for event in events])
    assert SENTINEL not in (await runtime.store.get_run(run.id)).model_dump_json()
    assert SENTINEL not in json.dumps(logs, default=str)


async def test_the_input_routes_answer_only_the_owner(served: str) -> None:
    """The routes sit behind the daemon's guard as every other: no token, another site's page and another name for
    the daemon are refused before any run is looked at."""
    body: dict[str, object] = _tapped(_BUTTON)
    async with httpx.AsyncClient(base_url=served, timeout=10.0) as bare:
        for path in ("/api/runs/run-1/input", "/api/runs/run-1/takeover/screen"):
            assert (await bare.post(path, json=body)).status_code == 401, path
            owner: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
            foreign: httpx.Response = await bare.post(
                path, json=body, headers={**owner, "Origin": "https://evil.example"}
            )
            assert foreign.status_code == 403, (path, foreign.text)
            renamed: httpx.Response = await bare.post(path, json=body, headers={**owner, "Host": "evil.example"})
            assert renamed.status_code == 421, (path, renamed.text)


async def test_the_cast_and_the_input_of_a_campaign_come_from_its_own_browser(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A campaign working in the owner's Chrome casts and takes input there, where Take over brings its tab up
    (item 136); the daemon's own Chrome is not asked. The sessions are stood in for: no Chrome is started."""
    asked: list[str] = []
    owners: BrowserSession = runtime.browsers.session_for("attach")

    async def cast_there(thread_id: str) -> AsyncIterator[Frame | None]:
        asked.append("cast")
        yield Frame(url="https://example.test/", data=b"\xff\xd8 a frame")

    async def input_there(thread_id: str, events: list[InputEvent], still_held: object) -> InputResult:
        asked.append("input")
        return InputResult(accepted=len(events), url="https://example.test/", width=1280, height=860)

    async def not_here(thread_id: str) -> AsyncIterator[Frame | None]:
        raise AssertionError("the daemon's own Chrome was asked")
        yield None

    def chrome_is_open(data_dir: Path) -> DebugEndpoint:
        return DebugEndpoint(port=9222, path="/devtools/browser/test")

    monkeypatch.setattr(owners, "screencast", cast_there)
    monkeypatch.setattr(owners, "send_input", input_there)
    monkeypatch.setattr(owners, "current_url", lambda thread_id: "https://example.test/")
    monkeypatch.setattr(runtime.browser, "screencast", not_here)
    monkeypatch.setattr("dotagent.agent.manager.read_debug_endpoint", chrome_is_open)
    MODEL.script = ScriptedProvider(_PROVIDER, [_call("shell", {"command": "sleep 5"}), _FINISH, _FINISH])
    campaign: RunRecord = await manager.start_campaign(
        "Wait", "task", SCRIPTED_MODEL, None, "test", None, "standard", None, "yolo", None, (), "attach"
    )
    try:
        taken: httpx.Response = await client.post(f"/api/runs/{campaign.id}/takeover/screen")
        assert taken.status_code == 202, taken.text
        sent: httpx.Response = await client.post(f"/api/runs/{campaign.id}/input", json=_tapped(_BUTTON))
        assert sent.status_code == 200 and sent.json()["url"] == "https://example.test/", sent.text
        async with client.stream("GET", f"/api/runs/{campaign.id}/screencast") as cast:
            first: bytes = await asyncio.wait_for(anext(cast.aiter_bytes()), _RUN_WAIT_S)
        assert b"https://example.test/" in first
        assert sorted(asked) == ["cast", "input"]
        await client.post(f"/api/runs/{campaign.id}/handback")
    finally:
        manager.cancel(campaign.id)
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(campaign.id)


async def test_a_hand_back_waits_for_input_under_way_and_counts_it(
    runtime: Runtime, manager: RunManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return control while a batch of input is still being done: the batch ends first and is counted, a button it left
    down is let go, and the next hold starts from nothing. The page is stood in for: no Chrome is started."""
    going: asyncio.Event = asyncio.Event()
    let_go: list[frozenset[str]] = []

    async def slow_input(thread_id: str, events: list[InputEvent], still_held: object) -> InputResult:
        await going.wait()
        return InputResult(accepted=len(events), url="https://example.test/", width=1280, height=860)

    async def letting_go(thread_id: str, buttons: frozenset[str]) -> None:
        let_go.append(buttons)

    monkeypatch.setattr(runtime.browser, "send_input", slow_input)
    monkeypatch.setattr(runtime.browser, "let_go_of_buttons", letting_go)
    MODEL.script = ScriptedProvider(_PROVIDER, [_call("shell", {"command": "sleep 5"}), _FINISH, _FINISH])
    run: RunRecord = await manager.start(
        "Wait", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    try:
        await manager.take_over_on_screen(run.id, "test", False)
        pressed: InputBatch = InputBatch.model_validate(
            {"events": [{"kind": "down", "x": 0.5, "y": 0.5, "button": "left", "clicks": 1}]}
        )
        sending: asyncio.Task[InputResult] = asyncio.create_task(manager.send_input(run.id, pressed))
        await asyncio.sleep(0.2)
        handing: asyncio.Task[float] = asyncio.create_task(manager.hand_back(run.id, "test"))
        await asyncio.sleep(0.2)
        assert not handing.done() and manager.held(run.id)
        going.set()
        assert (await sending).accepted == 1
        await handing
        assert let_go == [frozenset({"left"})]
        with pytest.raises(RunConflict, match="not taken over"):
            await manager.send_input(run.id, pressed)
        await manager.take_over_on_screen(run.id, "test", False)
        await manager.hand_back(run.id, "test")
        handbacks: list[EventRecord] = await runtime.store.events_of_kind(run.id, HANDBACK_EVENT, 5)
        assert [event.data["inputs"] for event in handbacks] == [
            {"clicks": 1, "scrolls": 0, "typed": False},
            {"clicks": 0, "scrolls": 0, "typed": False},
        ]
    finally:
        manager.cancel(run.id)
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)

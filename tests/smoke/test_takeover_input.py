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
from collections.abc import AsyncIterator, Iterator, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, CDPSession, Locator, Page, async_playwright, expect
from structlog.testing import capture_logs

from dotagent.agent.gate import HANDBACK_EVENT, TAKEOVER_EVENT
from dotagent.agent.manager import RunManager
from dotagent.browser.attach import DebugEndpoint
from dotagent.browser.remote_input import InputBatch, InputEvent, InputResult, InputTally, ScreenHold, dispatch
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import Frame, ScriptRun, Viewport
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
from dotagent.server.links import ui_url
from dotagent.server.stream import OPENING
from dotagent.store.models import EventRecord, RuleRecord, RunRecord
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
window.pressed = 0;
document.addEventListener("mousedown", () => { window.pressed += 1; });
document.addEventListener("mouseup", () => { window.pressed -= 1; });
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


def _ended() -> ScreenHold:
    """A hold the owner gave back."""
    over: ScreenHold = ScreenHold(True)
    over.end()
    return over


async def _read(browser: BrowserSession, thread: str, script: str) -> object:
    """What ``script`` gives back in the thread's page (its value comes as JSON)."""
    ran: ScriptRun = await browser.run_script(thread, script, _SCRIPT_S, False)
    value: object = json.loads(ran.text)
    return value


async def _taps_scrolls_and_keys_reach(browser: BrowserSession, thread: str) -> None:
    hold: ScreenHold = ScreenHold(True)
    taken: InputResult = await browser.send_input(thread, _tap(_BUTTON), hold)
    assert taken.accepted == 3 and taken.width > 0 and taken.height > 0, taken
    assert await _read(browser, thread, "document.getElementById('count').textContent") == "1"
    await browser.send_input(thread, _batch({"kind": "wheel", "x": 0.5, "y": 0.5, "dx": 0, "dy": 600}), hold)
    await asyncio.sleep(0.3)
    scrolled: object = await _read(browser, thread, "window.scrollY")
    assert isinstance(scrolled, int | float) and scrolled > 0, scrolled
    await browser.send_input(thread, _tap(_FIELD), hold)
    await browser.send_input(thread, _batch({"kind": "text", "text": SENTINEL}, {"kind": "key", "key": "Enter"}), hold)
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
    hold: ScreenHold = ScreenHold(True)
    with pytest.raises(RunConflict, match="no page open"):
        await session.send_input("nothing", _tap(_BUTTON), hold)
    await session.open_url("held", f"{site.base}/play", True)
    # A step under way holds the thread's lock: the input waits a moment, then is refused, and is not counted.
    thread = session._threads["held"]
    async with thread.lock:
        started: float = time.monotonic()
        with pytest.raises(RunConflict, match="still going"):
            await session.send_input("held", _tap(_BUTTON), hold)
        assert 1.5 <= time.monotonic() - started <= 5.0
    assert (hold.tally.clicks, hold.under_way) == (0, 0)
    # Handed back while the input waited: it is not done.
    with pytest.raises(RunConflict, match="not taken over"):
        await session.send_input("held", _tap(_BUTTON), _ended())
    assert await _read(session, "held", "document.getElementById('count').textContent") == "0"
    session.set_remote_input(False)
    try:
        with pytest.raises(RunConflict, match="remote_input"):
            await session.send_input("held", _tap(_BUTTON), hold)
    finally:
        session.set_remote_input(True)
    await session.send_input("held", _tap(_BUTTON), hold)
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
        await session.send_input("broken", _batch({"kind": "text", "text": SENTINEL}), ScreenHold(True))
    assert failed.value.context["events"] == ["text"] and failed.value.context["error_type"] == "RuntimeError"
    assert SENTINEL not in str(failed.value) and failed.value.__cause__ is None
    assert SENTINEL not in json.dumps(logs, default=str)


# A press in the page's empty corner, which neither the button nor the field takes, left down: no release follows it.
_PRESS: dict[str, object] = {"kind": "down", "x": 0.9, "y": 0.9, "button": "left", "clicks": 1}
_WHEEL: dict[str, object] = {"kind": "wheel", "x": 0.5, "y": 0.5, "dx": 0, "dy": 200}


def _held_up(going: asyncio.Event) -> object:
    """The page's own dispatch, done once ``going`` is set: a batch under way for as long as the test wants."""

    async def held_up(page: Page, viewport: Viewport, events: Sequence[InputEvent]) -> None:
        await going.wait()
        await dispatch(page, viewport, events)

    return held_up


async def _until_counted(hold: ScreenHold, scrolls: int) -> None:
    """Wait until ``hold`` counted ``scrolls`` scrolls: a batch may begin once it is counted."""
    async with asyncio.timeout(10.0):
        while True:
            if hold.tally.scrolls >= scrolls:
                return
            await asyncio.sleep(0.02)


@pytest.mark.browser
async def test_the_last_batch_of_a_hold_that_ended_lets_go_of_its_press_before_anyone_else_has_the_page(
    session: BrowserSession, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 479: a batch under way when its hold ends is done and counted, and, the last of the hold's batches, lets go
    of the press the input left down before the page is anyone else's: a step of the run that waits for the page finds
    no button down. The end itself waits on nothing, and leaves the release to the batch under way."""
    await session.open_url("release", f"{site.base}/play", True)
    hold: ScreenHold = ScreenHold(True)
    await session.send_input("release", _batch(_PRESS), hold)
    assert await _read(session, "release", "window.pressed") == 1
    going: asyncio.Event = asyncio.Event()
    monkeypatch.setattr("dotagent.browser.session.dispatch", _held_up(going))
    scrolling: asyncio.Task[InputResult] = asyncio.create_task(session.send_input("release", _batch(_WHEEL), hold))
    await _until_counted(hold, 1)
    used: InputTally = hold.end()
    assert (used.clicks, used.scrolls, hold.under_way) == (1, 1, 1)
    assert hold.take_release() == frozenset()
    # A step of the run waits for the page meanwhile, and reads it once the batch is over.
    step: asyncio.Task[object] = asyncio.create_task(_read(session, "release", "window.pressed"))
    await asyncio.sleep(0.2)
    assert not step.done()
    going.set()
    assert (await scrolling).accepted == 1
    assert await step == 0
    assert hold.take_release() == frozenset()
    with pytest.raises(RunConflict, match="not taken over"):
        await session.send_input("release", _batch(_PRESS), hold)


@pytest.mark.browser
async def test_a_page_that_does_not_take_a_batch_holds_it_and_the_page_a_moment_only(
    session: BrowserSession, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 479: the page is given a moment to measure itself and take a batch; one that does not answer fails the
    batch as the page's failure, and the page is the run's again after."""
    await session.open_url("stuck", f"{site.base}/play", True)

    async def never(*parts: object) -> None:
        await asyncio.Event().wait()

    for target in ("dispatch", "measure"):
        if target == "dispatch":
            monkeypatch.setattr("dotagent.browser.session.dispatch", never)
        else:
            monkeypatch.setattr(session, "_viewport", never)
        hold: ScreenHold = ScreenHold(True)
        started: float = time.monotonic()
        with pytest.raises(BrowserError) as failed:
            await session.send_input("stuck", _tap(_BUTTON), hold)
        assert failed.value.context["error_type"] == "TimeoutError", target
        assert 2.5 <= time.monotonic() - started <= 6.0, target
        assert hold.under_way == 0
        monkeypatch.undo()
        assert await _read(session, "stuck", "document.getElementById('count').textContent") == "0"


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


async def _waiting_at_the_gate(manager: RunManager, run_id: str) -> None:
    """Wait until one of the run's tool calls waits for the owner to give the run back (item 479)."""
    async with asyncio.timeout(_RUN_WAIT_S):
        while True:
            if manager._handles[run_id].gate.waiting > 0:
                return
            await asyncio.sleep(0.05)


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
        # A step of the run comes to the gate and waits there for the owner: the one under way when the run was taken
        # over, or the next one.
        await _waiting_at_the_gate(manager, run.id)
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

    async def input_there(thread_id: str, events: list[InputEvent], hold: ScreenHold) -> InputResult:
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
            frames: AsyncIterator[bytes] = cast.aiter_bytes()
            # The stream's opening line (item 709), then its first frame.
            assert await asyncio.wait_for(anext(frames), _RUN_WAIT_S) == OPENING
            first: bytes = await asyncio.wait_for(anext(frames), _RUN_WAIT_S)
        assert b"https://example.test/" in first
        assert sorted(asked) == ["cast", "input"]
        await client.post(f"/api/runs/{campaign.id}/handback")
    finally:
        manager.cancel(campaign.id)
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(campaign.id)


def _opened(site: _Site) -> ScriptedProvider:
    """A run that opens the fixture page, then is held at its next step, which sleeps."""
    return ScriptedProvider(
        _PROVIDER,
        [_call("browser_open", {"url": f"{site.base}/play"}), _call("shell", {"command": "sleep 5"}), _FINISH, _FINISH],
    )


@pytest.mark.browser
async def test_a_hand_back_waits_for_nothing_and_the_batch_under_way_lets_go_of_its_press(
    runtime: Runtime, manager: RunManager, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 479: Return control while a batch of input is still being done waits for nothing: the run is given back at
    once and no batch begins after; the batch under way is counted in the hand-back's record, and, done, lets go of the
    press the owner's input left down."""
    MODEL.script = _opened(site)
    run: RunRecord = await manager.start(
        "Wait", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    try:
        await _results(runtime, run.id, 1)
        await manager.take_over_on_screen(run.id, "test", False)
        await manager.send_input(run.id, InputBatch.model_validate(_events(_PRESS)))
        assert await _on_the_page(runtime, run.thread_id, "window.pressed") == 1
        going: asyncio.Event = asyncio.Event()
        monkeypatch.setattr("dotagent.browser.session.dispatch", _held_up(going))
        hold: ScreenHold = manager._holds[run.id].hold
        scrolling: asyncio.Task[InputResult] = asyncio.create_task(
            manager.send_input(run.id, InputBatch.model_validate(_events(_WHEEL)))
        )
        await _until_counted(hold, 1)
        started: float = time.monotonic()
        await manager.hand_back(run.id, "test")
        assert time.monotonic() - started < 1.0 and not manager.held(run.id)
        handbacks: list[EventRecord] = await runtime.store.events_of_kind(run.id, HANDBACK_EVENT, 5)
        assert [event.data["inputs"] for event in handbacks] == [{"clicks": 1, "scrolls": 1, "typed": False}]
        with pytest.raises(RunConflict, match="not taken over"):
            await manager.send_input(run.id, InputBatch.model_validate(_events(_PRESS)))
        going.set()
        assert (await scrolling).accepted == 1
        await _until(runtime, run.thread_id, "window.pressed", 0)
    finally:
        manager.cancel(run.id)
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)


@pytest.mark.browser
async def test_a_run_that_ends_held_ends_at_once_and_lets_go_of_the_press_left_down(
    runtime: Runtime, manager: RunManager, site: _Site
) -> None:
    """Item 479: a run stopped while held from the screen, the owner's press still down, ends without waiting on the
    page; the press is let go of, so that the chat's next run does not click with it down, and nothing of the hold is
    kept."""
    MODEL.script = _opened(site)
    run: RunRecord = await manager.start(
        "Wait", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await _results(runtime, run.id, 1)
    await manager.take_over_on_screen(run.id, "test", False)
    await manager.send_input(run.id, InputBatch.model_validate(_events(_PRESS)))
    assert await _on_the_page(runtime, run.thread_id, "window.pressed") == 1
    manager.cancel(run.id)
    async with asyncio.timeout(10.0):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "cancelled", ended
    await _until(runtime, run.thread_id, "window.pressed", 0)
    assert run.id not in manager._holds
    with pytest.raises(RunConflict):
        await manager.send_input(run.id, InputBatch.model_validate(_events(_PRESS)))


@pytest.mark.browser
async def test_a_run_held_after_a_script_ran_gives_the_owner_a_page_the_script_is_not_in(
    runtime: Runtime, manager: RunManager, site: _Site
) -> None:
    """Item 554: the owner takes the run over to type what it may not, and a script it ran may have left a listener
    that copies it: a hold, from the screen, opens the page again, a new document; a script that ran in the chat is no
    longer one of the chat's afterwards."""
    listener: str = "() => { window.__copied = ['listening']; return 1; }"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _call("browser_open", {"url": f"{site.base}/play"}),
            _call("browser_eval", {"script": listener}),
            _call("shell", {"command": "sleep 5"}),
            _FINISH,
            _FINISH,
        ],
    )
    run: RunRecord = await manager.start(
        "Wait", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    try:
        await _results(runtime, run.id, 2)
        thread = runtime.browser._threads[run.thread_id]
        assert thread.scripted
        await manager.take_over_on_screen(run.id, "test", False)
        assert not thread.scripted
        assert await _on_the_page(runtime, run.thread_id, "typeof window.__copied") == "undefined"
    finally:
        manager.cancel(run.id)
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)


async def _on_the_page(runtime: Runtime, thread_id: str, script: str) -> object:
    return await _read(runtime.browser, thread_id, script)


async def _touch(cdp: CDPSession, kind: str, points: list[tuple[float, float]]) -> None:
    """A touch of the screen as a phone sends it: a finger down, moved or up (``kind`` as the protocol names it)."""
    await cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y} for x, y in points]})


async def _screen_of(page: Page) -> Locator:
    """Dot's computer of the run page, through Task details and its Screen tab."""
    await page.get_by_role("button", name="Task details").click()
    await page.get_by_role("tab", name="Screen").click()
    card: Locator = page.locator("section.computer")
    await expect(card).to_be_visible()
    return card


# Where the desktop window centres its traffic lights and the room the page keeps for them (test_desktop_lights.py).
_LIGHTS_Y: float = 26.0
_LIGHTS_ROOM: float = 96.0


@pytest.mark.browser
async def test_a_phone_uses_the_page_from_its_screen(
    runtime: Runtime, manager: RunManager, served: str, site: _Site, tmp_path: Path
) -> None:
    """Item 475, at 390 by 844 as a touch screen in the dark: Use it here holds the run and opens the view over the
    whole page; a tap on the picture over the page's button clicks it, a finger dragged up scrolls the page, the
    Keyboard button types into the page's field and Enter submits its form; Return control stops it all."""
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [_call("browser_open", {"url": f"{site.base}/play"}), _call("shell", {"command": "sleep 30"}), _FINISH],
    )
    run: RunRecord = await manager.start(
        "Use the page", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    try:
        await _results(runtime, run.id, 1)
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(
                    viewport={"width": 390, "height": 844},
                    color_scheme="dark",
                    is_mobile=True,
                    has_touch=True,
                    reduced_motion="reduce",
                )
                await page.goto(ui_url("127.0.0.1", int(served.rsplit(":", 1)[1]), TOKEN))
                await page.goto(f"{served}/#/runs/{run.id}")
                card: Locator = await _screen_of(page)
                await card.get_by_role("button", name="Use it here").click()
                full: Locator = page.locator("dialog.computer-full")
                await expect(full).to_be_visible()
                screen: Locator = full.locator(".computer-screen[data-controlling]")
                await expect(screen).to_be_visible()
                image: Locator = full.locator(".computer-image")
                await expect(image).to_be_visible()
                assert manager.held(run.id)
                box = await image.bounding_box()
                assert box is not None
                await page.touchscreen.tap(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
                await _until(runtime, run.thread_id, "document.getElementById('count').textContent", "1")
                # A finger dragged up the picture scrolls the page down.
                cdp: CDPSession = await page.context.new_cdp_session(page)
                x: float = box["x"] + box["width"] * 0.7
                await _touch(cdp, "touchStart", [(x, box["y"] + box["height"] * 0.8)])
                for step in range(1, 9):
                    await _touch(cdp, "touchMove", [(x, box["y"] + box["height"] * (0.8 - step * 0.06))])
                    await asyncio.sleep(0.03)
                await _touch(cdp, "touchEnd", [])
                await _until_true(runtime, run.thread_id, "window.scrollY > 0")
                await page.touchscreen.tap(box["x"] + box["width"] * 0.2, box["y"] + box["height"] * 0.08)
                await full.get_by_role("button", name="Keyboard").click()
                await page.keyboard.type("hello")
                await page.keyboard.press("Enter")
                await _until(runtime, run.thread_id, "document.getElementById('field').value", "hello")
                await _until(runtime, run.thread_id, "document.getElementById('submitted').textContent", "1")
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await page.screenshot(path=str(tmp_path / "phone-control-390-dark.png"))
                # A finger held still on an empty corner of the page presses there; dragged off the picture and lifted
                # there, it is let go at the page's edge.
                held_at: tuple[float, float] = (box["x"] + box["width"] * 0.9, box["y"] + box["height"] * 0.9)
                await _touch(cdp, "touchStart", [held_at])
                await asyncio.sleep(0.7)
                await _until(runtime, run.thread_id, "window.pressed", 1)
                await _touch(cdp, "touchMove", [(held_at[0], 830.0)])
                await _touch(cdp, "touchEnd", [])
                await _until(runtime, run.thread_id, "window.pressed", 0)
                # Pressed again and the view closed meanwhile: the press is let go as the control ends.
                await _touch(cdp, "touchStart", [held_at])
                await asyncio.sleep(0.7)
                await _until(runtime, run.thread_id, "window.pressed", 1)
                await full.get_by_role("button", name="Close Dot's computer").click()
                await _touch(cdp, "touchEnd", [])
                await _until(runtime, run.thread_id, "window.pressed", 0)
                await card.get_by_role("button", name="Use it here").click()
                await expect(full.locator(".computer-screen[data-controlling]")).to_be_visible()
                box = await image.bounding_box()
                assert box is not None
                # Two fingers parted around the middle zoom the picture there; a tap there still clicks the button.
                middle: tuple[float, float] = (box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
                await _touch(cdp, "touchStart", [(middle[0] - 30, middle[1]), (middle[0] + 30, middle[1])])
                for step in range(1, 7):
                    spread: float = 30 + step * 15
                    await _touch(cdp, "touchMove", [(middle[0] - spread, middle[1]), (middle[0] + spread, middle[1])])
                    await asyncio.sleep(0.03)
                await _touch(cdp, "touchEnd", [])
                zoomed = await image.bounding_box()
                assert zoomed is not None and zoomed["width"] > box["width"] * 2, (box, zoomed)
                await page.touchscreen.tap(middle[0], middle[1])
                await _until(runtime, run.thread_id, "document.getElementById('count').textContent", "2")
                await page.screenshot(path=str(tmp_path / "phone-control-390-dark-zoomed.png"))
                # Return control gives the run back and closes the view Use it here opened, back to the run's page.
                await full.get_by_role("button", name="Return control").click()
                await expect(full).to_have_count(0)
                await expect(card.locator(".computer-screen[data-controlling]")).to_have_count(0)
                assert not manager.held(run.id)
                await page.touchscreen.tap(middle[0], middle[1])
                await asyncio.sleep(0.5)
                assert await _on_the_page(runtime, run.thread_id, "document.getElementById('count').textContent") == "2"
            finally:
                await browser.close()
    finally:
        manager.cancel(run.id)
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)


@pytest.mark.browser
async def test_a_hand_off_of_a_browser_step_offers_to_use_the_page_from_this_screen(
    runtime: Runtime, manager: RunManager, served: str, site: _Site
) -> None:
    """Item 475: the card of a browser step handed to the owner, on the run's page, offers Use it here beside Take the
    browser over; it holds the run and opens the view over the whole page."""
    rule: RuleRecord = await runtime.store.add_rule("handoff", "browser_open", "handed", "Open it yourself")
    MODEL.script = ScriptedProvider(_PROVIDER, [_call("browser_open", {"url": f"{site.base}/handed"}), _FINISH])
    run: RunRecord = await manager.start(
        "Open the page", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(
                    viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, reduced_motion="reduce"
                )
                await page.goto(ui_url("127.0.0.1", int(served.rsplit(":", 1)[1]), TOKEN))
                await page.goto(f"{served}/#/runs/{run.id}")
                offer: Locator = page.locator(".approval .takeover-offer").get_by_role("button", name="Use it here")
                await expect(offer).to_be_visible(timeout=_RUN_WAIT_S * 1000)
                await expect(
                    page.locator(".approval").get_by_role("button", name="Take the browser over")
                ).to_be_visible()
                await offer.click()
                await expect(page.locator("dialog.computer-full .computer-screen[data-controlling]")).to_be_visible()
                assert manager.held(run.id)

                # In the desktop shell the view's close and title stand right of the traffic lights, on their line, and
                # Return control closes the view, back to the run's page.
                desk: Page = await browser.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
                await desk.add_init_script("window.__TAURI__ = {}")
                await desk.goto(ui_url("127.0.0.1", int(served.rsplit(":", 1)[1]), TOKEN))
                await desk.goto(f"{served}/#/runs/{run.id}")
                await desk.locator(".approval .takeover-offer").get_by_role("button", name="Use it here").click()
                full: Locator = desk.locator("dialog.computer-full")
                close = await full.get_by_role("button", name="Close Dot's computer").bounding_box()
                title = await full.locator(".panel-section-title").bounding_box()
                assert close is not None and title is not None
                assert close["x"] >= _LIGHTS_ROOM, close
                assert abs(close["y"] + close["height"] / 2 - _LIGHTS_Y) <= 1.0, close
                assert title["x"] >= close["x"] + close["width"], (close, title)
                await full.get_by_role("button", name="Return control").click()
                await expect(full).to_have_count(0)
                assert not manager.held(run.id)
            finally:
                await browser.close()
    finally:
        await runtime.store.delete_rule(rule.id)
        manager.cancel(run.id)
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)


async def _until(runtime: Runtime, thread_id: str, script: str, value: object) -> None:
    """Wait until ``script`` gives ``value`` in the run's page."""
    async with asyncio.timeout(10.0):
        while True:
            if await _on_the_page(runtime, thread_id, script) == value:
                return
            await asyncio.sleep(0.1)


async def _until_true(runtime: Runtime, thread_id: str, script: str) -> None:
    await _until(runtime, thread_id, script, True)

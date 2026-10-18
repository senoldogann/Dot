"""Dot on the phone, end to end (item 52): a browser subscribes to the daemon's web push through the API, the
watcher posts an encrypted, VAPID-signed message to the subscription's push service when a run asks a question and
when it ends, a push service that says gone makes the subscription go, and the shell of the web app (manifest, service
worker) is served to a headless Chrome. The push service is a local fixture; a real phone on the owner's tailnet is
the owner's own check (ROADMAP item 52)."""

import asyncio
import base64
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.notify.push import PUSH_KINDS, watch_pushes
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = "ollama"
TOKEN: str = "phone-test-token"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 90.0


class _PushService(ThreadingHTTPServer):
    """A push service of the test: records every POST it takes and answers with the status it is told to."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.status: int = 201
        self.posts: list[tuple[dict[str, str], bytes]] = []
        self.arrived: threading.Condition = threading.Condition()

    @property
    def endpoint(self) -> str:
        return f"https://127.0.0.1:{self.server_address[1]}/push/sub-1"

    @property
    def plain_endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/push/sub-1"

    def wait_posts(self, count: int) -> list[tuple[dict[str, str], bytes]]:
        with self.arrived:
            self.arrived.wait_for(lambda: len(self.posts) >= count, _STEP_WAIT_S)
            return list(self.posts)


class _Handler(BaseHTTPRequestHandler):
    server: _PushService

    def do_POST(self) -> None:
        length: int = int(self.headers.get("Content-Length", "0"))
        body: bytes = self.rfile.read(length)
        with self.server.arrived:
            self.server.posts.append(({key.lower(): value for key, value in self.headers.items()}, body))
            self.server.arrived.notify_all()
        self.send_response(self.server.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class _Recorder:
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
def push_service() -> Iterator[_PushService]:
    service: _PushService = _PushService()
    worker: threading.Thread = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()
    try:
        yield service
    finally:
        service.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n',
        encoding="utf-8",
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
async def client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
    """A client of a real uvicorn server on a free port, with the push watcher running as in the daemon."""
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
    async with runtime.bus.subscribe_kinds(PUSH_KINDS) as feed:
        pusher: asyncio.Task[None] = asyncio.create_task(watch_pushes(runtime.store, feed, runtime.push_keys, stopping))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
            ) as http:
                yield http
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await pusher


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _browser_keys() -> dict[str, str]:
    """What a browser's ``PushSubscription.toJSON()`` carries: a P-256 public key and a 16-byte secret, base64url."""
    point: bytes = (
        ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    return {
        "p256dh": base64.urlsafe_b64encode(point).rstrip(b"=").decode("ascii"),
        "auth": base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode("ascii"),
    }


async def _pending_question(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


async def test_a_subscription_gets_the_question_and_the_end_and_goes_when_its_service_says_so(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, push_service: _PushService
) -> None:
    key: httpx.Response = await client.get("/api/push/key")
    assert key.status_code == 200 and len(key.json()["public_key"]) == 87, key.text
    refused: httpx.Response = await client.post(
        "/api/push/subscriptions", json={"endpoint": push_service.plain_endpoint, "keys": _browser_keys()}
    )
    assert refused.status_code == 422, "a push service is reached over HTTPS only"
    # The test's push service speaks plain HTTP: the watcher is pointed at it through the endpoint it posts to.
    added: httpx.Response = await client.post(
        "/api/push/subscriptions", json={"endpoint": push_service.endpoint, "keys": _browser_keys()}
    )
    assert added.status_code == 201, added.text
    subscriptions = await runtime.store.list_push_subscriptions()
    assert [s.endpoint for s in subscriptions] == [push_service.endpoint]
    await runtime.store.delete_push_subscription(push_service.endpoint)
    await runtime.store.add_push_subscription(
        push_service.plain_endpoint, subscriptions[0].p256dh, subscriptions[0].auth
    )

    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [_turn(ASK_USER_TOOL, {"question": "Which colour?"}), _turn(FINISH_TOOL, {"answer": "Blue.", "success": True})],
    )
    run: RunRecord = await manager.start(
        "Pick a colour", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    question: ApprovalRecord = await _pending_question(runtime, run.id)
    posts = await asyncio.to_thread(push_service.wait_posts, 1)
    assert len(posts) == 1, "the question reached the push service"
    headers, body = posts[0]
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["authorization"].startswith("vapid t=") and ",k=" in headers["authorization"]
    assert headers["ttl"] == "600"
    assert len(body) > 100 and b"Which colour" not in body, "the message travels encrypted"

    await manager.decide(question.id, True, "test", "blue")
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    posts = await asyncio.to_thread(push_service.wait_posts, 2)
    assert len(posts) == 2, "the end of the run reached the push service"

    push_service.status = 410
    MODEL.script = ScriptedProvider(_PROVIDER, [_turn(FINISH_TOOL, {"answer": "Done.", "success": True})])
    gone_run: RunRecord = await manager.start(
        "Say done", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(gone_run.id)
    posts = await asyncio.to_thread(push_service.wait_posts, 3)
    assert len(posts) == 3
    for _ in range(int(_STEP_WAIT_S * 10)):
        if not await runtime.store.list_push_subscriptions():
            break
        await asyncio.sleep(0.1)
    assert await runtime.store.list_push_subscriptions() == [], "a subscription its service calls gone is deleted"


async def test_a_question_a_yolo_run_leaves_reaches_the_phone_and_the_run_goes_on(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, push_service: _PushService
) -> None:
    """Item 151: a yolo run asks nobody and waits for no answer; the question it leaves reaches the phone all the
    same, and so does its end."""
    push_service.status = 201
    keys: dict[str, str] = _browser_keys()
    await runtime.store.add_push_subscription(push_service.plain_endpoint, keys["p256dh"], keys["auth"])
    sent: int = len(push_service.posts)
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(ASK_USER_TOOL, {"question": "Which colour?"}),
            _turn(FINISH_TOOL, {"answer": "Blue, as nobody said otherwise.", "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "Pick a colour", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended
    assert [question.status for question in await runtime.store.run_questions(run.id, ASK_USER_TOOL)] == ["left"]
    posts = await asyncio.to_thread(push_service.wait_posts, sent + 2)
    assert len(posts) == sent + 2, "the question the run left and the end of the run reached the push service"


async def test_keys_that_cannot_encrypt_are_refused_and_a_stored_one_goes_while_the_others_get_theirs(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, push_service: _PushService
) -> None:
    """The code review's Y15: a subscription whose keys could not encrypt a message stopped the watcher, and with it
    every push, until the daemon started again."""
    push_service.status = 201
    for stored in await runtime.store.list_push_subscriptions():
        await runtime.store.delete_push_subscription(stored.endpoint)
    good: dict[str, str] = _browser_keys()
    for keys in ({"p256dh": "!!!", "auth": good["auth"]}, {"p256dh": good["p256dh"], "auth": "c2hvcnQ"}):
        refused: httpx.Response = await client.post(
            "/api/push/subscriptions", json={"endpoint": push_service.endpoint, "keys": keys}
        )
        assert refused.status_code == 422, refused.text
    # One stored before the door checked keys, next to a good one.
    await runtime.store.add_push_subscription("https://push.invalid/unusable", "!!!", "!!!")
    await runtime.store.add_push_subscription(push_service.plain_endpoint, good["p256dh"], good["auth"])
    sent: int = len(push_service.posts)
    for goal in ("Say done", "Say done again"):
        MODEL.script = ScriptedProvider(_PROVIDER, [_turn(FINISH_TOOL, {"answer": "Done.", "success": True})])
        run: RunRecord = await manager.start(
            goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run.id)
    posts = await asyncio.to_thread(push_service.wait_posts, sent + 2)
    assert len(posts) == sent + 2, "the good subscription got the end of both runs"
    assert [stored.endpoint for stored in await runtime.store.list_push_subscriptions()] == [
        push_service.plain_endpoint
    ]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _home_left_by_an_unclean_end(home: Path) -> Path:
    """Make ``home`` a Dot home whose daemon listens on a free port, with the mark of a daemon that did not end cleanly
    in it; returns the mark."""
    home.mkdir()
    (home / "config.toml").write_text(f"[server]\nport = {_free_port()}\n", encoding="utf-8")
    mark: Path = home / "daemon.running"
    mark.touch()
    return mark


async def test_a_daemon_that_starts_after_an_unclean_end_tells_the_phone(
    push_service: _PushService, tmp_path: Path
) -> None:
    """Item 151: a daemon that was killed, or ended with an error, leaves its mark behind; a real daemon that starts and
    finds it tells the phone so, and one that stops cleanly takes its own mark away."""
    home: Path = tmp_path / "home"
    mark: Path = _home_left_by_an_unclean_end(home)
    keys: dict[str, str] = _browser_keys()
    store: Store = await Store.open(home / "dot.sqlite3", home / "screenshots")
    try:
        await store.add_push_subscription(push_service.plain_endpoint, keys["p256dh"], keys["auth"])
    finally:
        await store.close()
    push_service.status = 201
    sent: int = len(push_service.posts)
    daemon: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "dotagent",
        "daemon",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env={
            **os.environ,
            "DOT_HOME": str(home),
            "DOT_LOG_DIR": str(tmp_path / "logs"),
            "DOT_CHROME_DIR": str(tmp_path / "chrome"),
        },
    )
    try:
        posts = await asyncio.to_thread(push_service.wait_posts, sent + 1)
        assert len(posts) == sent + 1, "the restart reached the push service"
        assert await asyncio.to_thread(mark.exists), "a running daemon keeps its mark"
    finally:
        daemon.send_signal(signal.SIGTERM)
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: int = await daemon.wait()
    assert ended == 0
    assert not await asyncio.to_thread(mark.exists), "a daemon that stops cleanly takes its mark away"


async def test_the_shell_of_the_web_app_is_served_and_the_service_worker_registers(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    manifest: httpx.Response = await client.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["start_url"] == "/" and manifest.json()["display"] == "standalone"
    assert (await client.get("/icons/icon-192.png")).status_code == 200
    assert (await client.get("/sw.js")).status_code == 200
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            # One browser's pages, which share what it stores: the token and the chat it was in last.
            context: BrowserContext = await browser.new_context(viewport={"width": 390, "height": 844})
            page: Page = await context.new_page()
            await page.goto(ui_url(host, port, TOKEN))
            scope: str = await page.evaluate("navigator.serviceWorker.ready.then((registration) => registration.scope)")
            assert scope == f"http://{host}:{port}/"
            # The app opens on the chat it was in last (item 286): at the address without a route, and as a home-screen
            # app added when it started on Activity; in a browser's tab, Activity stays a page of its own.
            thread: ThreadRecord = await runtime.store.create_thread("Plan the garden", "test")
            run: RunRecord = await runtime.store.create_run(
                thread.id,
                "Plan the garden",
                "chat",
                SCRIPTED_MODEL,
                None,
                "test",
                None,
                "standard",
                None,
                "ask",
                False,
                None,
                None,
                None,
            )
            await page.goto(f"http://{host}:{port}/#/runs/{run.id}")
            await page.wait_for_function(f"() => localStorage.getItem('dot.lastChat') === '/runs/{run.id}'")
            await page.goto(f"http://{host}:{port}/")
            await expect(page).to_have_url(f"http://{host}:{port}/#/runs/{run.id}")
            app: Page = await context.new_page()
            await app.add_init_script("Object.defineProperty(navigator, 'standalone', { get: () => true })")
            await app.goto(f"http://{host}:{port}/#/activity")
            await expect(app).to_have_url(f"http://{host}:{port}/#/runs/{run.id}")
            # The Activity page is gone (item 364): its address leads to the home.
            await page.goto(f"http://{host}:{port}/#/activity")
            await expect(page).to_have_url(f"http://{host}:{port}/#/runs")
        finally:
            await browser.close()

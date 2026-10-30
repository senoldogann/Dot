"""A card a run asks on stands in the chat open in WebKit (Safari, the iPhone, Dot.app) as it is asked (item 859). The
run writes its policy line and its card a moment apart, then waits for the owner: WebKit handed the page the line and
held the card until the stream's next write, its keepalive 15 s on, and the owner saw the chat say it needed an
approval with nothing to approve until they left the chat and came back. A real server on a scripted model, its preset
cautious so that each command asks; the model takes a few seconds before its second step, so that the page is open
when the run asks, and the run asks three times, while another run streams its words all along (item 859's review:
the stream hears every run's events, and theirs must not put its keepalive off)."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.asyncio, pytest.mark.browser]

TOKEN: str = "card-arrives-token"
ASKS: int = 3
# How long after its line the card may come: the stream's quarter of a second and the page's drawing, far below the
# keepalive's 15 s.
MOST_S: float = 3.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 30.0


def _turns() -> list[ScriptedTurn]:
    asking: list[ScriptedTurn] = [
        ScriptedTurn(
            text=f"Step {at}.", calls=(ScriptedCall(tool="shell", arguments={"command": f"touch made-{at}.txt"}),)
        )
        for at in range(1, ASKS + 1)
    ]
    finish = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    return [*asking, finish]


class _Unhurried(ScriptedProvider):
    """The scripted model, a few seconds before its first answer: the page is open before the run asks."""

    def __init__(self, name: str, turns: Sequence[ScriptedTurn]) -> None:
        super().__init__(name, turns)
        self._answered: int = 0

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self._answered += 1
        if self._answered == 1:
            await asyncio.sleep(3.0)
        return await super().complete(request, sink)


@pytest_asyncio.fixture
async def served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[RunManager, httpx.AsyncClient]]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n\n[policy]\npreset = "cautious"\n',
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, _Unhurried(_PROVIDER, _turns()))
    manager: RunManager = RunManager(runtime)
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as client:
            yield manager, client
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)


async def test_each_card_stands_in_the_chat_as_its_line_does(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser = await playwright.webkit.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            noise: asyncio.Task[None] = asyncio.create_task(_another_run_streams(manager))
            started = await client.post("/api/runs", json={"goal": "Make three files", "approvals": "ask"})
            run_id: str = started.json()["id"]
            await page.goto(f"http://127.0.0.1:{port}/#/runs/{run_id}")
            for at in range(1, ASKS + 1):
                line = page.locator(".note", has_text=f"touch made-{at}.txt")
                await expect(line).to_be_visible(timeout=_WAIT_S * 1000)
                seen: float = time.monotonic()
                card = page.locator(".approval", has_text=f"touch made-{at}.txt")
                await expect(card).to_be_visible(timeout=_WAIT_S * 1000)
                waited: float = time.monotonic() - seen
                assert waited < MOST_S, f"card {at} came {waited:.1f} s after its line"
                pending = (await client.get("/api/approvals", params={"status": "pending", "limit": 10})).json()
                (asked,) = pending
                answered = await client.post(
                    f"/api/approvals/{asked['id']}/decision", json={"approve": True, "note": ""}
                )
                assert answered.status_code == 200, answered.text
            async with asyncio.timeout(_WAIT_S):
                assert (await manager.wait(run_id)).status == "done"
            noise.cancel()
            # Dot's first line wears its face; those after a card do not: a card is no one else speaking.
            for at in range(1, ASKS + 1):
                said = page.locator(".bubble.say", has_text=f"Step {at}.")
                await expect(said.locator(".turn-face")).to_have_count(1 if at == 1 else 0)
        finally:
            await browser.close()


async def _another_run_streams(manager: RunManager) -> None:
    """Another run's words as they are written, a piece every 50 ms, on the bus every run's stream hears."""
    while True:
        await manager._runtime.bus.emit("run_another", "delta", {"step": 1, "text": "word "}, False)
        await asyncio.sleep(0.05)

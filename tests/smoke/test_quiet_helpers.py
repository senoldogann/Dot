"""A chat whose helpers work writes on its page only what changed (item 310), on the pages of a daemon in Chrome as a
phone. A chat hands three helpers a part each; each lists a folder step after step. While they work the page is watched
for four seconds: no text on it is written again as it was, while the time of the group of helpers and each helper's
step line still move on. Before, the page wrote every helper's time and the head of their group again at every event of
the chat and its helpers, the same text nearly every time, and a helper's line lost its tool's name at every step and got
it back.

No network and no model service is used: the model is a stand-in that answers in code, every role is it and Ollama's
address is a closed port.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, Message, ModelInfo, ModelReply, ToolCall, Usage
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.delegate import DELEGATE_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TOKEN: str = "MARKER-quiet-helpers-token"
_GOAL: str = "Hand three helpers a part each."
_HELPERS: int = 3
_STEPS: int = 150
_STEP_S: float = 0.1
_WATCH_MS: int = 4000
_WAIT_S: float = 30.0
# Every text node that a change put in place of one that read the same, by the class of the element it is in; and the
# number of other changes.
_WATCH: str = """() => {
  window.__same = [];
  window.__changed = 0;
  new MutationObserver((records) => {
    for (const record of records) {
      const [gone] = record.removedNodes;
      const [came] = record.addedNodes;
      const one = record.removedNodes.length === 1 && record.addedNodes.length === 1;
      if (one && gone.nodeType === 3 && came.nodeType === 3 && gone.textContent === came.textContent) {
        window.__same.push(record.target.className || record.target.localName);
      } else {
        window.__changed += 1;
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
}"""


def _first_user_text(messages: tuple[Message, ...]) -> str:
    return next(
        (message.content for message in messages if message.role == "user" and isinstance(message.content, str)), ""
    )


class _Steady:
    """The chat hands three helpers a part each at its first step; a helper lists a folder at each step, its depth going
    round so that no two steps in a row are the same, and answers after ``_STEPS`` of them. A moment slow each time, as a
    model is; a call without tools (a title, a review) gets a short answer."""

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        await asyncio.sleep(_STEP_S)
        done: int = sum(1 for message in request.messages if message.role == "tool")
        calls: tuple[ToolCall, ...] = ()
        if request.tools and _GOAL in _first_user_text(request.messages):
            if done == 0:
                calls = tuple(
                    ToolCall(id=f"call_d{index}", name=DELEGATE_TOOL, arguments={"goal": f"Helper {index}: list it."})
                    for index in range(_HELPERS)
                )
        elif request.tools and done < _STEPS:
            calls = (ToolCall(id=f"call_{done}", name="list_dir", arguments={"path": ".", "depth": done % 4 + 1}),)
        return ModelReply(
            model=request.model,
            text="" if calls else "Done.",
            thinking="",
            tool_calls=calls,
            usage=Usage(input_tokens=1000, output_tokens=50, cached_tokens=0),
            finish="tool_calls" if calls else "stop",
            raw_finish="tool_calls" if calls else "stop",
            raw=(),
        )

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=False,
            tools=True,
            thinking=False,
            context_length=128_000,
            own_context_length=None,
            window_source="assumed",
            efforts=(),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("scripted"),)

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon in a throw-away home whose every model role is the stand-in, served at the address it yields."""
    home: Path = tmp_path_factory.mktemp("home")
    roles: str = "".join(f'{role} = "{SCRIPTED_MODEL}"\n' for role in ("main", "fast", "review", "vision"))
    (home / "config.toml").write_text(
        f"[browser]\nheadless = true\n\n[models]\n{roles}\n[memory]\nembed_enabled = false\n\n"
        '[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        await runtime.router.register(_PROVIDER, _Steady())
        manager: RunManager = RunManager(runtime)
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=_TOKEN,
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)
        patch.undo()


async def test_a_chat_whose_helpers_work_writes_only_what_changed(served: tuple[RunManager, str]) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        _GOAL, "chat", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", None, ()
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), _TOKEN))
            await page.goto(f"{address}/#/runs/{run.id}")
            head_time: Locator = page.locator(".helpers-time")
            lines: Locator = page.locator(".child-last")
            await expect(page.locator(".helpers-state")).to_have_text(f"{_HELPERS} working", timeout=_WAIT_S * 1000)
            await expect(lines).to_have_count(_HELPERS)
            await expect(lines.first).to_contain_text("list_dir", timeout=_WAIT_S * 1000)
            before: tuple[str, list[str]] = (await head_time.inner_text(), await lines.all_inner_texts())
            await page.evaluate(_WATCH)
            await page.wait_for_timeout(_WATCH_MS)
            same: list[str] = await page.evaluate("() => window.__same")
            changed: int = await page.evaluate("() => window.__changed")
            after: tuple[str, list[str]] = (await head_time.inner_text(), await lines.all_inner_texts())
            assert await page.locator(".helpers-state").inner_text() == f"{_HELPERS} working"
        finally:
            await browser.close()
    await manager.stop(run.id)
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(run.id)
    assert same == [], f"{len(same)} texts written again as they were, {changed} other changes: {sorted(set(same))}"
    assert after[0] != before[0], (before, after)
    assert all(line != earlier for line, earlier in zip(after[1], before[1], strict=True)), (before, after)

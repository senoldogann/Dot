"""A failed embedding never fails a run (item 256): with an Ollama server whose model runner breaks off on every
embedding (an HTTP 400 whose words are the failed request to the runner, as the owner's run met it), a run of the real
loop asks again as for a broken connection, then recalls its notes and lists its skills by their words alone and ends
done; the timeline says so in two warning lines, in Chrome, wide and on a phone, light and dark. Before, the run failed
at its start with the error as its result.

No network and no model service is used: the scripted model plays the run, and a stand-in on the loopback plays the
embedding route.
"""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, OllamaConfig, load_default_config
from dotagent.core.paths import skills_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, MemoryRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "embedding-failure-token"
GOAL: str = "Write the report for the team."
NOTE: str = "The report goes to the team on Fridays, as a PDF."
# Ollama's words when its request to the model's runner on the loopback broke off.
RUNNER_LOST: str = (
    'Post "http://127.0.0.1:60949/tokenize": read tcp 127.0.0.1:49762->127.0.0.1:60949: read: connection reset by peer'
)
SKILLS: dict[str, str] = {
    "weekly-report": "Write the weekly report from the week's notes, in the owner's format.",
    "invoice-pdf": "Fill in the invoice PDF form of a client.",
}
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "report.md is written.", "success": True}),)
)
_TIME_LIMIT_S: float = 60.0
# Each embedding call is tried as often as the embedder's retry policy allows (memory/embedder.py _RETRY).
_ATTEMPTS: int = 3
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


class _BrokenRunner(BaseHTTPRequestHandler):
    """Ollama's embedding route whose runner breaks off: every request answers 400 with the runner's words."""

    asked: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        _BrokenRunner.asked.append(self.path)
        body: bytes = json.dumps({"error": RUNNER_LOST}).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def _broken_ollama() -> Iterator[str]:
    """The base address of the stand-in, serving until the block ends."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _BrokenRunner)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _write_skills() -> None:
    """The skills of ``SKILLS`` in the home's skills folder."""
    for name, description in SKILLS.items():
        folder: Path = skills_dir() / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n", encoding="utf-8"
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _served(runtime: Runtime, manager: RunManager) -> AsyncIterator[str]:
    """The address of a real server over ``runtime``."""
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


async def _setup_warnings(page: Page, width: int) -> list[str]:
    """The warning lines of how the run was set up, in the side panel's Run tab (item 365): the panel turned on beside
    a wide conversation, opened as a drawer on a phone."""
    if width < 960:
        await page.locator("button[aria-label='Run details']").click()
    else:
        await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
        await page.reload()
    setup: Locator = page.locator("#panel-run-setup")
    await expect(setup).to_be_visible()
    return [text.strip() for text in await setup.locator(".note.warn").all_inner_texts()]


@pytest.mark.browser
async def test_a_run_whose_embeddings_fail_finds_its_notes_and_skills_by_their_words_and_ends_done(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    _BrokenRunner.asked.clear()
    with scratch_home() as home, eval_environment(home, {}), _broken_ollama() as ollama_url:
        _write_skills()
        base: AppConfig = eval_config(load_default_config())
        ollama: OllamaConfig = base.providers.ollama.model_copy(update={"base_url": ollama_url})
        config: AppConfig = base.model_copy(
            update={
                "providers": base.providers.model_copy(update={"ollama": ollama}),
                "memory": base.memory.model_copy(update={"embed_enabled": True}),
                # Fewer listed than offered, so the skills are picked by meaning.
                "skills": base.skills.model_copy(update={"listed": 1}),
            }
        )
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            note, _ = await runtime.store.add_memory_once(NOTE, [], None, None, None, None)
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(run.id)
                events: list[EventRecord] = await runtime.store.list_events(run.id, 0, 500)
                assert done.status == "done", done.result
                failed: list[EventRecord] = [event for event in events if event.kind == "embedding_failed"]
                assert [event.data["purpose"] for event in failed] == ["notes", "skills"]
                assert all(str(event.data["error"]).startswith("provider answered HTTP 400: Post") for event in failed)
                # Each failure was asked again before the run went on by the words.
                assert _BrokenRunner.asked == ["/api/embed"] * (2 * _ATTEMPTS)
                recalled: list[EventRecord] = [event for event in events if event.kind == "memories"]
                assert len(recalled) == 1 and recalled[0].data["ids"] == [note.id]
                kept: MemoryRecord = await runtime.store.get_memory(note.id)
                assert kept.content == NOTE
                async with _served(runtime, manager) as address, async_playwright() as playwright:
                    host, port = "127.0.0.1", int(address.rpartition(":")[2])
                    browser: Browser = await playwright.chromium.launch(
                        channel=runtime.config.browser.channel, headless=True
                    )
                    try:
                        for width, scheme in _SHOTS:
                            page: Page = await browser.new_page(
                                viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                            )
                            await page.goto(ui_url(host, port, TOKEN))
                            await page.goto(f"{address}/#/runs/{run.id}")
                            await expect(page.locator(".timeline")).to_contain_text("report.md is written.")
                            # Nothing of it stands in the conversation: the side panel lists both, and the head
                            # of its Set up says them once (item 365).
                            await expect(page.locator(".timeline")).not_to_contain_text("by their words alone")
                            lines: list[str] = await _setup_warnings(page, width)
                            assert [line.split(" — ")[0] for line in lines] == [
                                "Notes recalled by their words alone: the embedding service failed",
                                "Skills listed by their words alone: the embedding service failed",
                            ], lines
                            await expect(page.locator(".setup-summary")).to_contain_text(
                                "notes and skills found by their words alone"
                            )
                            await page.screenshot(path=str(tmp_path / f"words-{width}-{scheme}.png"))
                            await page.close()
                    finally:
                        await browser.close()
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)

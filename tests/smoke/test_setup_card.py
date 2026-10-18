"""The home's Set up a model card (item 333), in WebKit as the owner's iPhone, on a served daemon whose default model is
OpenAI's while no OpenAI key is set. The card says so, and each of its ways leads to a model:

- **Add a key:** a key saved in Settings › API keys makes OpenAI list the model, and the card goes. A stand-in OpenAI
  service on this Mac lists its models to that key alone.
- **Removed key:** with the key removed the card comes back.
- **Choose a model:** the scripted model, made the default in Settings › Models, answers a message from the home.

Before, the home said nothing until a run failed. No model or service outside this Mac is asked; the key is not a real
one.
"""

import asyncio
import json
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "setup-card-token"
KEY_NAME: str = "DOT_TEST_SETUP_CARD_OPENAI_KEY"
KEY: str = "MARKER-setup-card-openai-key"
UNSET: str = "DOT_TEST_SURELY_UNSET_SECRET"
MODEL: str = "openai:gpt-5.5"
ANSWER: str = "Good morning. The scripted model answers."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# The run's answer, and the calls of Dot's own work after it (a title, a summary) that the scripted model also answers.
_TURNS: tuple[ScriptedTurn, ...] = tuple(ScriptedTurn(text=ANSWER, calls=()) for _ in range(4))


class _OpenAI(BaseHTTPRequestHandler):
    """OpenAI's list of models, served to the key ``KEY`` alone, as OpenAI's own refuses any other (HTTP 401)."""

    def do_GET(self) -> None:
        allowed: bool = self.path == "/v1/models" and self.headers.get("Authorization") == f"Bearer {KEY}"
        body: dict[str, object] = (
            {"object": "list", "data": [{"id": "gpt-5.5", "object": "model"}]}
            if allowed
            else {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}}
        )
        payload: bytes = json.dumps(body).encode("utf-8")
        self.send_response(200 if allowed else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def _openai() -> Iterator[str]:
    """The stand-in OpenAI service, on a free port of this Mac, for as long as the block runs."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAI)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    monkeypatch.delenv(KEY_NAME, raising=False)
    monkeypatch.delenv(UNSET, raising=False)
    ensure_layout()
    yield home


@asynccontextmanager
async def _served(home: Path, openai: str) -> AsyncIterator[str]:
    """A daemon on the Dot home ``home`` whose default model is OpenAI's, served by the stand-in at ``openai``, and
    whose Ollama is the scripted model: the address it is at. No other provider has a key."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{MODEL}"\n\n'
        f'[providers.openai]\nbase_url = "{openai}/v1"\napi_key_env = "{KEY_NAME}"\n'
        f'organization_env = "{UNSET}"\nproject_env = "{UNSET}"\n\n'
        f'[providers.anthropic]\napi_key_env = "{UNSET}"\n\n'
        f'[providers.opencode_go]\napi_key_env = "{UNSET}"\n\n'
        f'[providers.ollama]\napi_key_env = "{UNSET}"\n',
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(_TURNS)))
    manager: RunManager = RunManager(runtime)
    try:
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
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _home(page: Page, address: str, state: str) -> Locator:
    """The home, once its card read the models: ``state`` is ``ready`` when the default model is listed, else
    ``needed``."""
    await page.goto(f"{address}/#/")
    card: Locator = page.locator(".home-setup")
    await expect(card).to_have_attribute("data-state", state, timeout=15_000)
    return card


async def test_each_way_of_the_card_leads_to_a_model(own_home: Path) -> None:
    with _openai() as openai:
        async with _served(own_home, openai) as address, async_playwright() as playwright:
            browser: Browser = await playwright.webkit.launch(headless=True)
            try:
                context: BrowserContext = await browser.new_context(
                    **playwright.devices["iPhone 17 Pro Max"], color_scheme="light", reduced_motion="reduce"
                )
                await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                page: Page = await context.new_page()
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))

                card: Locator = await _home(page, address, "needed")
                await expect(card).to_be_visible()
                await expect(card).to_contain_text("New chats start on gpt-5.5 from OpenAI")
                await expect(card).to_contain_text("No OpenAI key is set.")

                await card.get_by_role("link", name="Add a key").click()
                await expect(page).to_have_url(re.compile(r"#/settings/keys$"))
                row: Locator = page.locator(".provider-item").filter(
                    has=page.locator(".provider-name", has_text="OpenAI")
                )
                await row.click()
                await page.get_by_role("button", name="Add key").click()
                await page.get_by_label("OpenAI API key").fill(KEY)
                await page.get_by_role("button", name="Save").click()
                await expect(row).to_have_attribute("data-state", "set")
                card = await _home(page, address, "ready")
                await expect(card).to_be_hidden()

                await page.goto(f"{address}/#/settings/keys")
                await (
                    page.locator(".provider-item").filter(has=page.locator(".provider-name", has_text="OpenAI")).click()
                )
                await page.get_by_role("button", name="Remove the OpenAI key saved in Dot").click()
                await page.get_by_role("dialog").get_by_role("button", name="Remove").click()
                await expect(row).to_have_attribute("data-state", "unset")
                card = await _home(page, address, "needed")
                await expect(card).to_be_visible()

                await card.get_by_role("link", name="Choose a model").click()
                await expect(page).to_have_url(re.compile(r"#/settings/models$"))
                await page.locator(".models-default select").select_option(SCRIPTED_MODEL)
                await expect(page.get_by_text("New chats start on scripted.")).to_be_visible()
                card = await _home(page, address, "ready")
                await expect(card).to_be_hidden()
                await page.get_by_role("textbox", name="Goal").fill("Good morning")
                await page.locator(".home .send").click()
                await expect(page).to_have_url(re.compile(r"#/runs/"))
                await expect(page.get_by_text(ANSWER).first).to_be_visible(timeout=15_000)
            finally:
                await browser.close()

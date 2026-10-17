"""Import from other apps (item 219): a button at the head of Customize › Skills opens a checklist of what the Mac's
other agents set up and Dot can add, the skills chosen at first and the MCP servers not; one request adds what the
owner chose, each as the one scan before them found it, and says what it could not add and why. A home folder of the
test's own with skills of Claude Code and Codex and a server of Claude Desktop, served by a real server, wide and on
a phone, light and dark."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import added_servers_path, skills_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "import-elsewhere-token"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))
_DIALOG: str = "dialog.import-dialog"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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


def _skill(folder: Path, name: str, description: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nFollow the steps.\n", encoding="utf-8"
    )


def _other_agents(home: Path) -> None:
    """Two skills of Claude Code, one of Codex (named as one of Claude Code's) and a server of Claude Desktop."""
    _skill(home / ".claude" / "skills", "pdf-tools", "Fill and merge PDF forms.")
    _skill(home / ".claude" / "skills", "csv-clean", "Clean a CSV file.")
    _skill(home / ".codex" / "skills", "pdf-tools", "Split PDF files into pages.")
    desktop: Path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(json.dumps({"mcpServers": {"notion": {"command": "npx", "args": ["-y", "notion-mcp"]}}}))


async def test_import_adds_what_the_owner_chose_and_says_what_it_could_not(
    runtime: Runtime, served: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await asyncio.to_thread(_other_agents, tmp_path)
    host, port = "127.0.0.1", int(served.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        monkeypatch.setenv("HOME", str(tmp_path))
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{served}/#/customize/skills")
                button: Locator = page.get_by_role("button", name="Import from other apps")
                await expect(button).to_be_enabled()
                await button.click()
                dialog: Locator = page.locator(_DIALOG)
                await expect(dialog.get_by_role("heading")).to_have_text("Import from other apps")
                # The skills by agent, chosen; the server not, as it starts a program.
                await expect(dialog.locator(".import-group-head")).to_have_count(3)
                rows: Locator = dialog.locator(".import-row")
                await expect(rows).to_have_count(4)
                await expect(dialog.locator(".import-row input:checked")).to_have_count(3)
                server: Locator = rows.filter(has_text="notion")
                await expect(server.locator("input")).not_to_be_checked()
                await expect(server).to_contain_text("Claude Desktop · Starts npx")
                await expect(dialog.get_by_role("button", name="Import 3")).to_be_focused()
                await page.screenshot(path=str(tmp_path / f"import-{width}-{scheme}.png"), animations="disabled")
                if index < len(_SHOTS) - 1:
                    # Choose all, then nothing: the button counts, and is off at none; Cancel changes nothing.
                    choose_all: Locator = dialog.get_by_label("Choose all")
                    await choose_all.check()
                    await expect(dialog.get_by_role("button", name="Import 4")).to_be_visible()
                    await choose_all.uncheck()
                    await expect(dialog.get_by_role("button", name="Import", exact=True)).to_be_disabled()
                    await dialog.get_by_role("button", name="Cancel").click()
                    await expect(dialog).to_have_count(0)
                    await page.close()
                    continue
                # The server too, and Codex's skill of a name Claude Code's takes first.
                await server.locator("input").check()
                await dialog.get_by_role("button", name="Import 4").click()
                await expect(page.locator(".toast.ok", has_text="Imported 3 items.")).to_be_visible()
                await expect(page.locator(".toast.error")).to_contain_text(
                    "pdf-tools was not imported: Dot's skills folder already holds pdf-tools"
                )
                await expect(dialog).to_have_count(0)
                # What is left cannot be added: the button says so.
                await expect(button).to_be_disabled()
                await page.close()
        finally:
            await browser.close()
    copied: list[str] = sorted(path.name for path in skills_dir().iterdir() if not path.name.startswith("."))
    assert copied == ["csv-clean", "pdf-tools"]
    added: object = json.loads(added_servers_path().read_text(encoding="utf-8"))
    assert isinstance(added, dict) and [server["name"] for server in added["servers"]] == ["notion"]


async def test_an_import_names_each_item_once(served: str) -> None:
    item: dict[str, str] = {"kind": "skill", "agent": "Claude Code", "name": "pdf-tools"}
    async with httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        twice: httpx.Response = await client.post("/api/skills/elsewhere/import", json={"items": [item, item]})
        none: httpx.Response = await client.post("/api/skills/elsewhere/import", json={"items": []})
    assert (twice.status_code, none.status_code) == (422, 422), (twice.text, none.text)
    assert "an item is named more than once" in twice.text

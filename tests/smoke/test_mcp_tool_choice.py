"""The tools of an MCP server that offers many (items 426 and 524), for real: a server of 32 tools, served in this
process over streamable HTTP and added from the Connectors page. It is ready with every one of them; the page lists
what it offers, all of it ticked, and the server comes back with just the tools the owner ticks for the runs that
start next, or with all of them and no list when every one is ticked. A server set up in Dot's config, or one that
is not there, is refused."""

import asyncio
import json
import socket
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from mcp.server.mcpserver import MCPServer
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from starlette.types import ASGIApp

from dotagent.agent.manager import RunManager
from dotagent.config import McpServerConfig, load_config
from dotagent.core.errors import ConfigError
from dotagent.core.paths import connected_servers_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.mcp.connected import read_connected
from dotagent.mcp.types import McpTool
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "tool-choice-token"
FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
START_TIMEOUT_S: float = 15.0
READY_WITHIN_S: float = 30.0
_POLL_S: float = 0.2
# More than the 30 Dot once took from a server without a choice.
_TOOLS: int = 32


def _many_server() -> MCPServer:
    """An MCP server open to all, with many tools."""
    server: MCPServer = MCPServer("many")

    def tool(number: int) -> None:
        def run() -> str:
            return f"tool {number:02d} ran"

        server.add_tool(run, name=f"tool_{number:02d}", description=f"Does the {number:02d}th thing of the many.")

    for number in range(_TOOLS):
        tool(number)
    return server


@dataclass(frozen=True)
class _Served:
    url: str
    server: uvicorn.Server
    task: asyncio.Task[None]


async def _serve(app: ASGIApp, listener: socket.socket) -> _Served:
    port: int = listener.getsockname()[1]
    server: uvicorn.Server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    task: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    deadline: float = time.monotonic() + START_TIMEOUT_S
    while not server.started:
        if task.done():
            task.result()
            raise AssertionError("the HTTP server stopped before it started")
        assert time.monotonic() < deadline, "the HTTP server did not start in time"
        await asyncio.sleep(0.05)
    return _Served(url=f"http://127.0.0.1:{port}", server=server, task=task)


@dataclass(frozen=True)
class _World:
    runtime: Runtime
    dot: str
    many: str


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[_World]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n'
        f'[[mcp]]\nname = "fixture"\ncommand = {json.dumps(sys.executable)}\nargs = [{json.dumps(str(FIXTURE))}]\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(home / "logs"))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    many: _Served = await _serve(_many_server().streamable_http_app(), bind_listener("127.0.0.1", 0))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    stopping: asyncio.Event = asyncio.Event()
    listener: socket.socket = bind_listener("127.0.0.1", 0)
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
    dot: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(dot.serve(sockets=[listener]))
    while not dot.started:
        assert not serving.done(), "Dot's server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        yield _World(runtime=runtime, dot=f"http://127.0.0.1:{port}", many=many.url)
    finally:
        dot.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        many.server.should_exit = True
        await many.task
        patch.undo()


def _client(world: _World) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=world.dot, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60.0)


async def _view(client: httpx.AsyncClient, name: str) -> dict[str, object]:
    response: httpx.Response = await client.get("/api/mcp/servers")
    assert response.status_code == 200, response.text
    found: list[dict[str, object]] = [view for view in response.json()["servers"] if view["name"] == name]
    assert len(found) == 1, response.json()
    return found[0]


async def _settled(client: httpx.AsyncClient, name: str) -> dict[str, object]:
    async with asyncio.timeout(READY_WITHIN_S):
        while True:
            view: dict[str, object] = await _view(client, name)
            if view["state"] not in ("trying", "signing_in"):
                return view
            await asyncio.sleep(_POLL_S)


async def _added(world: _World, client: httpx.AsyncClient, name: str) -> dict[str, object]:
    response: httpx.Response = await client.post(
        "/api/mcp/servers", json={"service": None, "name": name, "address": f"{world.many}/mcp", "origin": world.dot}
    )
    assert response.status_code == 200, response.text
    return await _settled(client, name)


async def test_a_server_of_many_tools_is_ready_with_all_of_them_and_offers_just_the_chosen(world: _World) -> None:
    names: list[str] = [f"tool_{number:02d}" for number in range(_TOOLS)]
    async with _client(world) as client:
        ready: dict[str, object] = await _added(world, client, "many")
        # Ready with every tool it lists, and the page is told it may cut them down.
        assert (ready["state"], ready["origin"], ready["picks_tools"]) == ("ready", "connected", True), ready
        assert ready["tools"] == names, ready
        assert sorted(tool.remote_name for tool in world.runtime.mcp.tools if tool.server == "many") == names
        # What it offers, in its own order, and no allow list yet.
        offered: httpx.Response = await client.get("/api/mcp/servers/many/offered")
        assert offered.status_code == 200, offered.text
        listing: dict[str, object] = offered.json()
        assert "limit" not in listing and listing["allow"] == [], listing
        assert [tool["name"] for tool in listing["tools"]] == names  # type: ignore[attr-defined]
        assert listing["tools"][3] == {"name": "tool_03", "description": "Does the 03th thing of the many."}  # type: ignore[index]
        # A name the server does not list and none are refused, and nothing changes.
        unknown: httpx.Response = await client.put("/api/mcp/servers/many/allow", json={"tools": ["tool_01", "nope"]})
        assert unknown.status_code == 422 and "nope" in unknown.text, unknown.text
        none: httpx.Response = await client.put("/api/mcp/servers/many/allow", json={"tools": []})
        assert none.status_code == 422, none.text
        assert len([tool for tool in world.runtime.mcp.tools if tool.server == "many"]) == _TOOLS
        # The chosen two come back as the server's tools, in the server's order, whichever order they were sent in.
        chosen: httpx.Response = await client.put("/api/mcp/servers/many/allow", json={"tools": ["tool_03", "tool_01"]})
        assert chosen.status_code == 200, chosen.text
        cut: dict[str, object] = chosen.json()
        assert (cut["state"], cut["tools"], cut["picks_tools"]) == ("ready", ["tool_01", "tool_03"], True), cut
        assert sorted(tool.remote_name for tool in world.runtime.mcp.tools if tool.server == "many") == [
            "tool_01",
            "tool_03",
        ]
        # The choice is kept with the server, so a restart offers the same two.
        kept = {config.name: config for config in read_connected(connected_servers_path()).servers}
        assert kept["many"].allow == ["tool_01", "tool_03"]
        # Every one chosen keeps no list: the server offers all of its tools again, those it adds later too.
        everything: httpx.Response = await client.put("/api/mcp/servers/many/allow", json={"tools": names})
        assert everything.status_code == 200 and everything.json()["tools"] == names, everything.text
        kept = {config.name: config for config in read_connected(connected_servers_path()).servers}
        assert kept["many"].allow == []
        # The page's own servers only: one set up in Dot's config, and one that is not there, are refused.
        config_set: httpx.Response = await client.put("/api/mcp/servers/fixture/allow", json={"tools": ["add"]})
        assert config_set.status_code == 409, config_set.text
        assert (await client.get("/api/mcp/servers/fixture/offered")).status_code == 409
        assert (await client.get("/api/mcp/servers/nothing/offered")).status_code == 404
        # Removed, it leaves no record behind, and a try that failed before is not left to show again.
        removed: httpx.Response = await client.delete("/api/mcp/servers/many")
        assert removed.status_code == 200, removed.text
        assert all(view["name"] != "many" for view in removed.json()["servers"])
        assert all(config.name != "many" for config in read_connected(connected_servers_path()).servers)


async def test_a_choice_that_cannot_connect_is_not_kept_and_the_owner_may_choose_again(world: _World) -> None:
    """The server goes away between the list and the choice: the connection fails, the choice is not written down, and
    the server stays as it was, with its tools and its **Choose tools**."""
    async with _client(world) as client:
        ready: dict[str, object] = await _added(world, client, "many")
        assert (ready["state"], ready["picks_tools"]) == ("ready", True), ready
        listing: httpx.Response = await client.get("/api/mcp/servers/many/offered")
        assert listing.status_code == 200, listing.text
        real = world.runtime.mcp.connect

        async def refused(config: McpServerConfig) -> tuple[McpTool, ...]:
            raise ConfigError("the server is not answering", {"server": config.name})

        world.runtime.mcp.connect = refused  # type: ignore[method-assign]
        try:
            chosen: httpx.Response = await client.put("/api/mcp/servers/many/allow", json={"tools": ["tool_01"]})
        finally:
            world.runtime.mcp.connect = real  # type: ignore[method-assign]
        assert chosen.status_code >= 400, chosen.text
        # The refused choice is not on disk: the server keeps no allow list.
        written = {config.name: config for config in read_connected(connected_servers_path()).servers}
        assert "many" not in written or written["many"].allow == [], written.get("many")
        again: dict[str, object] = await _view(client, "many")
        assert (again["state"], again["picks_tools"], len(again["tools"])) == ("ready", True, _TOOLS), again  # type: ignore[arg-type]
        retry: httpx.Response = await client.put("/api/mcp/servers/many/allow", json={"tools": ["tool_02"]})
        assert retry.status_code == 200 and retry.json()["tools"] == ["tool_02"], retry.text
        await client.delete("/api/mcp/servers/many")


def _row(page: Page, name: str) -> Locator:
    return page.locator(f".plugin-server[data-server='{name}']")


async def test_the_plugins_page_lists_the_tools_all_ticked_and_connects_the_chosen(
    world: _World, tmp_path: Path
) -> None:
    port: int = int(world.dot.rpartition(":")[2])
    async with _client(world) as client:
        ready: dict[str, object] = await _added(world, client, "many")
        assert (ready["state"], ready["picks_tools"]) == ("ready", True), ready
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=world.runtime.config.browser.channel, headless=True)
        try:
            # The phone only looks and cancels; the wide page then saves, which leaves the server with the chosen two.
            for width, scheme in ((390, "dark"), (1280, "light")):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 860}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{world.dot}/#/customize/connectors")
                row: Locator = _row(page, "many")
                await expect(row.locator(".badge")).to_have_text("Ready")
                await expect(row).to_contain_text(f"{_TOOLS} actions: tool_00")
                # Only a server the page added offers the choice.
                await expect(_row(page, "fixture").get_by_role("button", name="Choose actions")).to_have_count(0)
                await row.get_by_role("button", name="Choose actions").click()
                dialog: Locator = page.locator("dialog[open]")
                await expect(dialog.get_by_role("heading")).to_have_text("Choose the actions of many")
                every: Locator = dialog.get_by_role("checkbox", name="All actions")
                boxes: Locator = dialog.locator(".import-list").get_by_role("checkbox")
                await expect(boxes).to_have_count(_TOOLS)
                # With no list yet, every tool is ticked, and so is All tools.
                await expect(every).to_be_checked()
                await expect(dialog).to_contain_text(f"{_TOOLS} of {_TOOLS}")
                await expect(dialog.get_by_role("button", name=f"Save {_TOOLS}")).to_be_enabled()
                await expect(dialog).to_contain_text("tool_03")
                await expect(dialog).to_contain_text("Does the 03th thing of the many.")
                # All tools unticks them all, and nothing ticked cannot be saved.
                await every.uncheck()
                await expect(dialog).to_contain_text(f"0 of {_TOOLS}")
                save: Locator = dialog.get_by_role("button", name="Save")
                await expect(save).to_be_disabled()
                await boxes.nth(5).check()
                await boxes.nth(_TOOLS - 1).check()
                await expect(every).not_to_be_checked()
                await expect(dialog.get_by_role("button", name="Save 2")).to_be_enabled()
                await page.screenshot(path=str(tmp_path / f"choose-tools-{width}-{scheme}.png"))
                # Cancelled, nothing is kept; saved, the server comes back with the two.
                await dialog.get_by_role("button", name="Cancel").click()
                await expect(page.locator("dialog[open]")).to_have_count(0)
                await expect(row).to_contain_text(f"{_TOOLS} actions: tool_00")
                if width == 390:
                    await page.close()
                    continue
                await row.get_by_role("button", name="Choose actions").click()
                await every.uncheck()
                await boxes.nth(5).check()
                await boxes.nth(_TOOLS - 1).check()
                await dialog.get_by_role("button", name="Save 2").click()
                await expect(row).to_contain_text("2 actions: tool_05, tool_31", timeout=READY_WITHIN_S * 1000)
                await expect(row.locator(".badge")).to_have_text("Ready")
                # A server that runs keeps the choice, to cut its tools down or take them all again.
                await expect(row.get_by_role("button", name="Choose actions")).to_have_count(1)
                await page.close()
        finally:
            await browser.close()
    assert sorted(tool.remote_name for tool in world.runtime.mcp.tools if tool.server == "many") == [
        "tool_05",
        "tool_31",
    ]

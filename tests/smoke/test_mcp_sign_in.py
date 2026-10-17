"""MCP servers on Customize › Plugins (items 279 and 316), for real: an MCP server that is its own authorization server, served in this
process (the MCP library's own, with a provider that says yes to every sign-in), and Dot's own server in front of a
runtime. Added by its address, the server asks for a sign-in: the page the add answers with is followed as a browser
would, to Dot's ``/oauth/callback``, and the server is then offered to runs with its tool, kept with its sign-in
(readable by the owner alone) and connected again at the next start, its token renewed once it ran out. A server whose
sign-in is gone is left out with words that send the owner to Customize › Plugins; Remove forgets the server and its sign-in.
A server open to all is added without a sign-in, and a bad name, a page of another address and a stray callback are
refused. The Plugins page itself, in a real headless Chrome, wide and light and on a phone and dark: the server of Dot's
config with its tools and where it is set up, one added by its address, signed in to on the page that opens, and
removed.

Nothing is mocked but macOS's answers on the page's other sections: real HTTP servers on free ports and the real client
library.
"""

import asyncio
import json
import os
import secrets
import socket
import stat
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.types import CallToolResult, TextContent
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from starlette.types import ASGIApp

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.connections import mac
from dotagent.core.paths import connected_servers_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.mcp.connected import read_connected
from dotagent.mcp.hub import McpHub, open_hub
from dotagent.mcp.signin import SIGN_IN_AGAIN, read_sign_in, sign_in_path
from dotagent.mcp.types import McpTool
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_connections
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "mcp-sign-in-token"
SCOPE: str = "tools"
# Tokens of the test's authorization server run out this soon, so that the test sees one renewed.
LIFETIME_S: int = 3
START_TIMEOUT_S: float = 15.0
READY_WITHIN_S: float = 30.0
_POLL_S: float = 0.1
FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
_PHONE_PX: int = 860


@dataclass
class _YesSayer:
    """An authorization server in memory that says yes to every sign-in, and counts the sign-ins and renewals."""

    clients: dict[str, OAuthClientInformationFull] = field(default_factory=dict)
    codes: dict[str, AuthorizationCode] = field(default_factory=dict)
    access: dict[str, AccessToken] = field(default_factory=dict)
    refresh: dict[str, RefreshToken] = field(default_factory=dict)
    authorized: int = 0
    renewed: int = 0

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        assert client.client_id is not None
        self.authorized += 1
        code: str = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self.codes[authorization_code.code]
        return self._issue(authorization_code.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        self.renewed += 1
        del self.refresh[refresh_token.token]
        return self._issue(refresh_token.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        found: AccessToken | None = self.access.get(token)
        if found is None or (found.expires_at is not None and found.expires_at < time.time()):
            return None
        return found

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access.pop(token.token, None)
        self.refresh.pop(token.token, None)

    def forget_everyone(self) -> None:
        """Every sign-in ends: the tokens given so far are good for nothing."""
        self.access.clear()
        self.refresh.clear()

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access: str = secrets.token_urlsafe(24)
        refresh: str = secrets.token_urlsafe(24)
        self.access[access] = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + LIFETIME_S,
            resource=resource,
        )
        self.refresh[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes, resource=resource)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=LIFETIME_S,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )


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


async def _stop(served: _Served) -> None:
    served.server.should_exit = True
    await served.task


def _signed_server(provider: _YesSayer, base: str) -> MCPServer:
    """An MCP server that is its own authorization server at ``base``, with one tool."""
    server: MCPServer = MCPServer(
        "signed",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=base,
            resource_server_url=f"{base}/mcp",
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            required_scopes=[SCOPE],
            validate_token_resource=True,
        ),
    )

    @server.tool()
    def whoami() -> str:
        """Who the caller is signed in as."""
        return "the owner, signed in"

    return server


def _open_server() -> MCPServer:
    """An MCP server open to all, with one tool."""
    server: MCPServer = MCPServer("open")

    @server.tool()
    def ping() -> str:
        """Answer pong."""
        return "pong"

    return server


@dataclass(frozen=True)
class _World:
    runtime: Runtime
    dot: str
    provider: _YesSayer
    signed: str
    open: str


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
    provider: _YesSayer = _YesSayer()
    signed_listener: socket.socket = bind_listener("127.0.0.1", 0)
    signed_base: str = f"http://127.0.0.1:{signed_listener.getsockname()[1]}"
    signed: _Served = await _serve(_signed_server(provider, signed_base).streamable_http_app(), signed_listener)
    opened: _Served = await _serve(_open_server().streamable_http_app(), bind_listener("127.0.0.1", 0))
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
        yield _World(
            runtime=runtime, dot=f"http://127.0.0.1:{port}", provider=provider, signed=signed.url, open=opened.url
        )
    finally:
        dot.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        await _stop(opened)
        await _stop(signed)
        patch.undo()


def _client(world: _World) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=world.dot, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60.0)


async def _server_view(client: httpx.AsyncClient, name: str) -> dict[str, object]:
    response: httpx.Response = await client.get("/api/mcp/servers")
    assert response.status_code == 200, response.text
    found: list[dict[str, object]] = [view for view in response.json()["servers"] if view["name"] == name]
    assert len(found) == 1, response.json()
    return found[0]


async def _ready(client: httpx.AsyncClient, name: str) -> dict[str, object]:
    async with asyncio.timeout(READY_WITHIN_S):
        while True:
            view: dict[str, object] = await _server_view(client, name)
            if view["state"] not in ("trying", "signing_in"):
                return view
            await asyncio.sleep(_POLL_S)


async def _called(hub: McpHub, server: str, tool_name: str) -> str:
    tool: McpTool = next(tool for tool in hub.tools if tool.server == server and tool.remote_name == tool_name)
    result: CallToolResult = await hub.call(tool, {})
    assert not result.is_error, result
    texts: list[str] = [block.text for block in result.content if isinstance(block, TextContent)]
    return "".join(texts)


async def test_a_server_that_asks_signs_in_in_the_browser_and_stays_signed_in(world: _World) -> None:
    async with _client(world) as client:
        added: httpx.Response = await client.post(
            "/api/mcp/servers",
            json={"service": None, "name": "signed", "address": f"{world.signed}/mcp", "origin": world.dot},
        )
        assert added.status_code == 200, added.text
        asking: dict[str, object] = added.json()
        assert (asking["state"], asking["origin"]) == ("signing_in", "connected")
        link: object = asking["link"]
        assert isinstance(link, str) and link.startswith(f"{world.signed}/authorize?")
        # The owner's browser: the authorization server's page says yes and sends it back to Dot.
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as browser:
            back: httpx.Response = await browser.get(link)
        assert back.status_code == 200, back.text
        assert str(back.url).startswith(f"{world.dot}/oauth/callback?")
        assert "Signed in to signed" in back.text
        ready: dict[str, object] = await _ready(client, "signed")
        assert (ready["state"], ready["tools"], ready["signs_in"], ready["error"]) == (
            "ready",
            ["whoami"],
            True,
            None,
        )
        # Offered to the runs that start now, and called through the hub.
        assert "mcp__signed__whoami" in world.runtime.registry.tools
        assert await _called(world.runtime.mcp, "signed", "whoami") == "the owner, signed in"
        assert world.provider.authorized == 1
    # Kept for the next start: the address, and the sign-in in a file the owner alone reads.
    (kept,) = read_connected(connected_servers_path()).servers
    assert (kept.name, kept.url, kept.oauth) == ("signed", f"{world.signed}/mcp", True)
    path: Path = sign_in_path("signed")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    stored = read_sign_in(path)
    assert stored is not None and stored.tokens is not None and stored.redirect_uri == f"{world.dot}/oauth/callback"
    # The next start, once the token ran out: it is renewed, and no one is asked to sign in again.
    await asyncio.sleep(LIFETIME_S + 1)
    renewed_before: int = world.provider.renewed
    hub: McpHub = await open_hub([kept], {})
    try:
        assert hub.failures == ()
        assert await _called(hub, "signed", "whoami") == "the owner, signed in"
    finally:
        await hub.close()
    assert world.provider.renewed > renewed_before
    assert world.provider.authorized == 1


async def test_a_server_whose_sign_in_is_gone_is_left_out_with_where_to_sign_in(world: _World) -> None:
    (kept,) = read_connected(connected_servers_path()).servers
    world.provider.forget_everyone()
    hub: McpHub = await open_hub([kept], {})
    try:
        (failure,) = hub.failures
    finally:
        await hub.close()
    assert (failure.server, failure.error) == ("signed", SIGN_IN_AGAIN)
    assert world.provider.authorized == 1


async def test_remove_forgets_the_server_and_its_sign_in(world: _World) -> None:
    async with _client(world) as client:
        removed: httpx.Response = await client.delete("/api/mcp/servers/signed")
        assert removed.status_code == 200, removed.text
        assert "signed" not in [view["name"] for view in removed.json()["servers"]]
    assert "mcp__signed__whoami" not in world.runtime.registry.tools
    assert "signed" not in world.runtime.mcp.connected()
    assert read_connected(connected_servers_path()).servers == ()
    assert not sign_in_path("signed").exists()


async def test_a_server_open_to_all_is_added_without_a_sign_in(world: _World) -> None:
    async with _client(world) as client:
        added: httpx.Response = await client.post(
            "/api/mcp/servers",
            json={"service": None, "name": "open", "address": f"{world.open}/mcp", "origin": world.dot},
        )
        assert added.status_code == 200, added.text
        view: dict[str, object] = added.json()
        if view["state"] == "trying":
            view = await _ready(client, "open")
        assert (view["state"], view["tools"], view["signs_in"]) == ("ready", ["ping"], False)
        assert await _called(world.runtime.mcp, "open", "ping") == "pong"
        assert not sign_in_path("open").exists()
        listed: httpx.Response = await client.get("/api/mcp/servers")
        services: dict[str, bool] = {service["id"]: service["added"] for service in listed.json()["services"]}
        assert services["notion"] is False and "deepwiki" in services


async def test_a_bad_name_an_unknown_page_and_a_stray_callback_are_refused(world: _World) -> None:
    async with _client(world) as client:
        bad: httpx.Response = await client.post(
            "/api/mcp/servers",
            json={"service": None, "name": "Not A Name", "address": f"{world.open}/mcp", "origin": world.dot},
        )
        assert bad.status_code == 422, bad.text
        assert "lowercase letters" in bad.text
        elsewhere: httpx.Response = await client.post(
            "/api/mcp/servers",
            json={"service": "notion", "name": None, "address": None, "origin": "https://evil.example"},
        )
        assert elsewhere.status_code == 422, elsewhere.text
    async with httpx.AsyncClient(base_url=world.dot, timeout=30.0) as browser:
        stray: httpx.Response = await browser.get("/oauth/callback", params={"state": "made-up", "code": "x"})
    assert stray.status_code == 400
    assert "Dot does not wait for this sign-in" in stray.text


def _server_row(page: Page, name: str) -> Locator:
    return page.locator(f".plugin-server[data-server='{name}']")


async def test_plugins_lists_adds_signs_in_to_and_removes_mcp_servers(
    world: _World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The page's other sections ask macOS about its applications: here every one is allowed, and none is asked.
    monkeypatch.setattr(mac, "automation_state", lambda bundle_id: "allowed")
    monkeypatch.setattr(mac, "installed_app", lambda bundle_id: Path("/Applications") / bundle_id)
    monkeypatch.setattr(routes_connections, "CLOUD_STORAGE", tmp_path / "CloudStorage")
    port: int = int(world.dot.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=world.runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(
                viewport={"width": 1280, "height": 860}, color_scheme="light", reduced_motion="reduce"
            )
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"{world.dot}/#/customize/plugins")
            fixture: Locator = _server_row(page, "fixture")
            await expect(fixture.locator(".badge")).to_have_text("Ready")
            await expect(fixture).to_contain_text("Set up in Dot's config.")
            await expect(fixture).to_contain_text("10 tools: ")
            await expect(fixture.get_by_role("button")).to_have_count(0)
            # The server added by the API before is the Plugins page's own: it can be removed.
            await expect(_server_row(page, "open").get_by_role("button", name="Remove")).to_be_visible()
            await expect(page.locator(".plugin-tile[data-plugin='notion'] .chip")).to_have_text("MCP · Sign in")
            await expect(page.locator(".plugin-tile[data-plugin='deepwiki'] .chip")).to_have_text("MCP · Open to all")
            # Added by its address: it asks for a sign-in, which happens on the page that opens, and comes back here.
            await page.get_by_label("Server name").fill("signed")
            await page.get_by_label("Server address or command").fill(f"{world.signed}/mcp")
            await page.locator(".connect-add").get_by_role("button", name="Add").click()
            signed: Locator = _server_row(page, "signed")
            await expect(signed.locator(".badge")).to_have_text("Waiting for your sign-in")
            async with page.context.expect_page() as opened:
                await signed.get_by_role("link", name="Open the sign-in page").click()
            sign_in: Page = await opened.value
            await sign_in.wait_for_load_state()
            await expect(sign_in.locator("h1")).to_have_text("Signed in to signed")
            await sign_in.close()
            await expect(signed.locator(".badge")).to_have_text("Ready", timeout=READY_WITHIN_S * 1000)
            await expect(signed).to_contain_text("1 tool: whoami")
            await expect(signed.get_by_role("button", name="Sign in again")).to_be_visible()
            assert await page.get_by_label("Server name").input_value() == ""
            await page.screenshot(path=str(tmp_path / "connect-mcp-1280-light.png"), full_page=True)
            # Removed, after a yes.
            await _server_row(page, "open").get_by_role("button", name="Remove").click()
            await page.get_by_role("button", name="Remove", exact=True).last.click()
            await expect(_server_row(page, "open")).to_have_count(0)
            assert "open" not in world.runtime.mcp.connected()
            # On a phone, a long address breaks rather than widen the page.
            phone: Page = await browser.new_page(
                viewport={"width": 390, "height": 860}, color_scheme="dark", reduced_motion="reduce"
            )
            await phone.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await phone.goto(ui_url("127.0.0.1", port, TOKEN))
            await phone.goto(f"{world.dot}/#/customize/plugins")
            await expect(_server_row(phone, "signed").locator(".badge")).to_have_text("Ready")
            wide: bool = await phone.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            assert not wide
            await phone.screenshot(path=str(tmp_path / "connect-mcp-390-dark.png"), full_page=True)
        finally:
            await browser.close()

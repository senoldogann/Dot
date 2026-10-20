"""The routes of the official sign-ins (item 422), on a daemon in a throw-away home with the clients in its
environment: the providers and their accounts, a sign-in started only from this Mac's own address, and the way back to
``/oauth/callback`` for a state nobody waits for. No provider is reached and no keychain of the owner is touched."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "oauth-routes-token"


@asynccontextmanager
async def _daemon() -> AsyncIterator[str]:
    """A daemon in a throw-away home: the address it serves on."""
    config: AppConfig = eval_config(load_default_config())
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
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


async def test_a_sign_in_starts_from_this_mac_and_a_state_nobody_waits_for_is_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOT_GOOGLE_CLIENT_ID", "client-1")
    monkeypatch.setenv("DOT_GOOGLE_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("DOT_MS_CLIENT_ID", "client-ms")
    async with (
        _daemon() as address,
        httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as client,
    ):
        providers = (await client.get("/api/accounts")).json()
        assert [(view["id"], view["configured"], view["accounts"]) for view in providers] == [
            ("google", True, []),
            ("microsoft", True, []),
        ]
        started = await client.post("/api/accounts/microsoft/start")
        assert started.status_code == 200, started.text
        query: dict[str, list[str]] = parse_qs(urlsplit(started.json()["link"]).query)
        port: str = address.rpartition(":")[2]
        assert query["redirect_uri"] == [f"http://localhost:{port}/oauth/callback"]
        assert query["client_id"] == ["client-ms"] and query["code_challenge_method"] == ["S256"]
        # A page reached through a tailnet name cannot start one: the sign-in comes back to this Mac.
        phone = await client.post("/api/accounts/google/start", headers={"Host": "mac.tail1234.ts.net"})
        assert phone.status_code in (421, 422), phone.text
        # The way back needs no token, and a state nobody waits for is said so, in a page of Dot's own look that shows
        # nothing the address carried.
        unknown = await client.get(
            "/oauth/callback",
            params={
                "state": "nobody",
                "code": "c",
                "error": "server_error",
                "error_description": "WORDS-OF-A-STRANGER",
            },
        )
        assert unknown.status_code == 400 and "This sign-in has ended" in unknown.text
        assert "WORDS-OF-A-STRANGER" not in unknown.text and "<style>" in unknown.text
        # The page carries its look, under a policy of its own that lets that one style apply (item 445).
        assert "style-src 'sha256-" in unknown.headers["content-security-policy"]
        # The browser that comes back for the sign-in started is given a page; its code is not traded here (no
        # provider is reached), so only that the page is the sign-in's own is checked. The plain No is not an error to
        # show; another error is, as the provider words it and escaped.
        declined = await client.get("/oauth/callback", params={"state": query["state"][0], "error": "access_denied"})
        assert declined.status_code == 200 and "Not signed in to Microsoft</h1>" in declined.text
        assert 'class="callback-reason"' not in declined.text
        again = parse_qs(urlsplit((await client.post("/api/accounts/microsoft/start")).json()["link"]).query)
        refused = await client.get(
            "/oauth/callback",
            params={"state": again["state"][0], "error": "server_error", "error_description": "<b>It broke</b>"},
        )
        assert refused.status_code == 200 and "server_error: &lt;b&gt;It broke&lt;/b&gt;" in refused.text

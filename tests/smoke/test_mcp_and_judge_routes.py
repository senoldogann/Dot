"""Four faults of the routes of MCP servers and of the judge (item 558), each shown through the API of a real in-process
server over a runtime in a throw-away home:

- a server added again under the name of one that is connected, at another address, is sent none of the tokens its
  predecessor was given (an address that the owner left alone keeps them);
- a key the owner put in a server's URL or in an argument of its command, or that the server printed as it failed, is
  given back by neither ``GET /api/mcp/servers`` nor ``GET /api/status``;
- the tools of a server are not chosen while Dot tries it, which would be written over when the try ends;
- the typed judge stays as it was when its choice cannot be written to the config file.

The servers are stand-ins of this Mac that answer every request with an error and note the ``Authorization`` they were
sent, or hold it. No network and no model service is used.
"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from mcp.shared.auth import OAuthToken

from dotagent.agent.manager import RunManager
from dotagent.config import McpServerConfig, load_config
from dotagent.core.paths import connected_servers_path
from dotagent.core.types import JsonObject
from dotagent.mcp.connected import keep_connected
from dotagent.mcp.signin import CALLBACK_PATH, FileTokenStorage, read_sign_in, sign_in_path
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_mcp
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "mcp-and-judge-routes-token"
PORT: int = 8765
ORIGIN: str = f"http://127.0.0.1:{PORT}"
JUDGE_KEY_ENV: str = "DOT_TEST_MCP_ROUTES_JUDGE_KEY"
# The keys of the servers of the config: one in the path of a URL and one in its query, one in an argument.
PATH_KEY: str = "k3Yx9Zq81LmNpR5tUv7W2aB"
QUERY_KEY: str = "q9Zx81LmNpR5tUv7W2aBkey"
ARGUMENT_KEY: str = "sk-live-0123456789abcdefghij"
OLD_TOKEN: str = "token-issued-for-the-old-address"
_AT_ONCE_S: float = 20.0


@dataclass(frozen=True)
class _World:
    client: httpx.AsyncClient
    runtime: Runtime
    home: Path


@dataclass(frozen=True)
class _StandIn:
    """A server of this Mac: its address, the ``Authorization`` of each request it got, and the gate that holds them."""

    address: str
    seen: list[str]
    release: threading.Event


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _stand_in(hold: bool) -> Iterator[_StandIn]:
    """A server that answers every request with an error once it is let to (at once, unless ``hold``)."""
    seen: list[str] = []
    release: threading.Event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            seen.append(self.headers.get("Authorization", ""))
            length: int = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            if hold:
                release.wait(60)
            body: bytes = json.dumps({"error": "this stand-in serves no tools"}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._answer()

        def do_POST(self) -> None:
            self._answer()

        def log_message(self, format: str, *args: object) -> None:
            return None

    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _StandIn(f"http://127.0.0.1:{server.server_address[1]}/mcp", seen, release)
    finally:
        release.set()
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[_World]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[judge]\napi_key_env = "{JUDGE_KEY_ENV}"\n\n'
        f'[[mcp]]\nname = "keyed"\nurl = "http://127.0.0.1:{_closed_port()}/{PATH_KEY}/mcp?api_key={QUERY_KEY}"\n\n'
        f'[[mcp]]\nname = "keyed-command"\ncommand = "/nonexistent/dot-tests/mcp-server"\n'
        f'args = ["--api-key", "{ARGUMENT_KEY}"]\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    try:
        patch.setenv("DOT_HOME", str(home))
        patch.setenv("DOT_LOG_DIR", str(home / "logs"))
        patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
        patch.setenv(JUDGE_KEY_ENV, "typesafe-key-of-this-test")
        runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
        try:
            manager: RunManager = RunManager(runtime)
            try:
                context: ServerContext = ServerContext(
                    runtime=runtime,
                    manager=manager,
                    scheduler=None,
                    token=TOKEN,
                    host="127.0.0.1",
                    port=PORT,
                    started_at=time.time(),
                    stopping=asyncio.Event(),
                )
                transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
                async with httpx.AsyncClient(
                    transport=transport, base_url=ORIGIN, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60.0
                ) as client:
                    yield _World(client, runtime, home)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
    finally:
        patch.undo()


async def _signed_in(name: str, address: str) -> None:
    """A server added on the Connectors page and signed in to: its record, and the token its sign-in gave."""
    keep_connected(connected_servers_path(), McpServerConfig(name=name, url=address, transport="http", oauth=True))
    storage: FileTokenStorage = FileTokenStorage(sign_in_path(name), f"{ORIGIN}{CALLBACK_PATH}")
    await storage.set_tokens(OAuthToken(access_token=OLD_TOKEN, token_type="Bearer"))


async def _take_away(world: _World, name: str) -> None:
    removed: httpx.Response = await world.client.delete(f"/api/mcp/servers/{name}")
    assert removed.status_code == 200, removed.text


async def test_a_server_added_again_at_another_address_is_sent_none_of_the_old_ones_tokens(world: _World) -> None:
    """The sign-in file is named for the server, and the library sends the tokens in it with the first request: the
    owner's token for one service went to another host, and stayed bound to its address."""
    with _stand_in(hold=False) as old, _stand_in(hold=False) as new:
        await _signed_in("foo", old.address)
        # The same address: the sign-in is the server's, and the first request carries it.
        same: httpx.Response = await world.client.post(
            "/api/mcp/servers", json={"service": None, "name": "foo", "address": old.address, "origin": ORIGIN}
        )
        assert same.status_code == 200, same.text
        assert old.seen and all(header == f"Bearer {OLD_TOKEN}" for header in old.seen), old.seen
        assert read_sign_in(sign_in_path("foo")) is not None
        # Another address: nothing of the old server's sign-in is sent to it, nor kept for it.
        elsewhere: httpx.Response = await world.client.post(
            "/api/mcp/servers", json={"service": None, "name": "foo", "address": new.address, "origin": ORIGIN}
        )
        assert elsewhere.status_code == 200, elsewhere.text
        assert new.seen, "the try did not reach the new address"
        assert all(OLD_TOKEN not in header for header in new.seen), new.seen
        assert read_sign_in(sign_in_path("foo")) is None
        await _take_away(world, "foo")


async def test_a_sign_in_left_behind_by_a_name_nobody_has_any_more_is_not_used_by_the_next_server_of_it(
    world: _World,
) -> None:
    with _stand_in(hold=False) as new:
        await FileTokenStorage(sign_in_path("gone"), f"{ORIGIN}{CALLBACK_PATH}").set_tokens(
            OAuthToken(access_token=OLD_TOKEN, token_type="Bearer")
        )
        added: httpx.Response = await world.client.post(
            "/api/mcp/servers", json={"service": None, "name": "gone", "address": new.address, "origin": ORIGIN}
        )
        assert added.status_code == 200, added.text
        assert new.seen and all(OLD_TOKEN not in header for header in new.seen), new.seen
        await _take_away(world, "gone")


async def test_a_key_in_a_servers_address_or_arguments_is_given_back_by_no_page_of_the_api(world: _World) -> None:
    # The premise: both servers of the config were left out as they started, and their config does hold the keys.
    assert {failure.server for failure in world.runtime.mcp.failures} == {"keyed", "keyed-command"}
    assert PATH_KEY in str(world.runtime.config.mcp[0].url) and ARGUMENT_KEY in world.runtime.config.mcp[1].args
    stored: str = json.dumps([failure.detail for failure in world.runtime.mcp.failures])
    listed: httpx.Response = await world.client.get("/api/mcp/servers")
    status: httpx.Response = await world.client.get("/api/status")
    for answer in (listed, status):
        assert answer.status_code == 200, answer.text
        for key in (PATH_KEY, QUERY_KEY, ARGUMENT_KEY):
            assert key not in answer.text and key not in stored, (key, answer.text, stored)
    addresses: dict[str, str] = {server["name"]: server["address"] for server in listed.json()["servers"]}
    assert addresses["keyed"].endswith("/***/mcp?…"), addresses
    assert addresses["keyed-command"] == "/nonexistent/dot-tests/mcp-server --api-key ***", addresses
    failures: dict[str, JsonObject] = {one["server"]: one for one in status.json()["mcp_failures"]}
    assert set(failures) == {"keyed", "keyed-command"}, failures
    detail = failures["keyed-command"]["detail"]
    assert isinstance(detail, dict) and detail["args"] == ["--api-key", "***"], failures


async def test_the_tools_of_a_server_are_not_chosen_while_dot_tries_it(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The try keeps the allow list it began with and writes it over the choice when it ends: before, the choice was
    taken and the try, dropped from the list of tries, went on to undo it."""
    monkeypatch.setattr(routes_mcp, "_ANSWER_WITHIN_S", 0.5)
    with _stand_in(hold=True) as slow:
        keep_connected(connected_servers_path(), McpServerConfig(name="slow", url=slow.address, transport="http"))
        started: httpx.Response = await world.client.post("/api/mcp/servers/slow/connect", json={"origin": ORIGIN})
        assert started.status_code == 200, started.text
        assert started.json()["state"] in ("trying", "signing_in"), started.text
        async with asyncio.timeout(_AT_ONCE_S):
            refused: httpx.Response = await world.client.put("/api/mcp/servers/slow/allow", json={"tools": ["x"]})
        assert refused.status_code == 409 and "trying this server already" in refused.text, refused.text
        slow.release.set()
        await _take_away(world, "slow")


async def test_the_judge_stays_as_it_was_when_its_choice_cannot_be_written(world: _World) -> None:
    """``PUT /judge`` switched the live judge before it wrote the config file, and a file that could not be changed
    refused the request with the judge switched all the same, until the next start."""
    config: Path = world.home / "config.toml"
    before: str = await asyncio.to_thread(config.read_text, encoding="utf-8")
    assert (await world.client.get("/api/judge")).json()["provider"] == "none"
    await asyncio.to_thread(config.write_text, "this is [not = valid toml\n", encoding="utf-8")
    try:
        refused: httpx.Response = await world.client.put("/api/judge", json={"provider": "typesafe"})
        assert refused.status_code >= 400, refused.text
        assert (await world.client.get("/api/judge")).json()["provider"] == "none"
        assert world.runtime.judge.provider == "none"
    finally:
        await asyncio.to_thread(config.write_text, before, encoding="utf-8")
    switched: httpx.Response = await world.client.put("/api/judge", json={"provider": "typesafe"})
    assert switched.status_code == 200, switched.text
    assert world.runtime.judge.provider == "typesafe"
    assert (await world.client.put("/api/judge", json={"provider": "none"})).status_code == 200

"""A served daemon and a paired phone for the tests of what the iPhone app asks of the daemon (items 1420 and after):
the real server on a free port, the real store in a folder of the test's own, a phone paired with a code as the app
pairs, and no network or model service. The same shape as ``test_phone_limits.py``, apart so that each test of the
app's routes borrows it."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.notify.leases import PhoneLeases
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.phones import phone_id_of

TOKEN: str = "phone-app-test-token"
TAILNET_NAME: str = "dot-mac.tailnet-test.ts.net"
MAC: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}


def own_home_of(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Dot home, log folder and Chrome folder of the test's own, which the process then uses."""
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def served(
    home: Path, leases: PhoneLeases | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, int, Runtime]]:
    """A daemon on ``home`` that a reverse proxy presents as ``TAILNET_NAME``: a client of its API (``/api``), its port
    and its runtime. ``leases`` are the activity leases it keeps, where a test moves the clock."""
    await asyncio.to_thread(
        (home / "config.toml").write_text,
        f'[browser]\nheadless = true\n\n[server]\nreach = ["{TAILNET_NAME}"]\n',
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
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
            leases=PhoneLeases() if leases is None else leases,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api", timeout=30.0) as client:
                yield client, port, runtime
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def pair_phone(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    """A phone paired with a code, as the app does: its id and the headers of a request made with its own key."""
    code: str = (await client.post("/phone/pairing", headers=MAC)).json()["code"]
    paired: httpx.Response = await client.post(
        "/phone/pair", json={"code": code}, headers={"Host": TAILNET_NAME, "Origin": f"https://{TAILNET_NAME}"}
    )
    key: str = str(paired.json()["token"])
    phone_id: str | None = phone_id_of(key)
    assert phone_id is not None
    return phone_id, {"Authorization": f"Bearer {key}"}

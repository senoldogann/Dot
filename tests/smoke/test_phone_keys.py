"""A paired phone manages the API keys (item 2054), on a served daemon: it saves, checks and removes a key; the key is
in no answer, log line or tell, only its last four characters; every change a phone makes is told to the owner, naming
the provider and the phone; a phone's changes and checks are limited (429), the Mac's token is not. No network and no
model service is used: the provider's check is a stand-in."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.doctor import CheckResult
from dotagent.events import EventBus
from dotagent.notify.tellkinds import TellKind
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_keys
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config

pytestmark = pytest.mark.asyncio

TOKEN: str = "phone-keys-test-token"
TAILNET_NAME: str = "dot-mac.tailnet-test.ts.net"
# A key of the test's own, in memory only.
KEY: str = "sk-or-phone-test-0123456789abcdefZq7c"
NAME: str = "OPENROUTER_API_KEY"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    monkeypatch.delenv(NAME, raising=False)
    ensure_layout()
    return home


@pytest.fixture
def tells(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """The tells the key routes send, title, text and route, as they are sent."""
    sent: list[tuple[str, str, str]] = []
    real = routes_keys.tell_owner

    async def recorded(bus: EventBus, title: str, text: str, url: str, kind: TellKind, subject: str) -> None:
        sent.append((title, text, url))
        await real(bus, title, text, url, kind, subject)

    monkeypatch.setattr(routes_keys, "tell_owner", recorded)
    return sent


@pytest.fixture
def stand_in_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider's check, without the provider: it refuses the key."""

    async def refused(config: object, provider: str) -> CheckResult:
        return CheckResult(name=f"{provider} api", ok=False, required=True, detail="401: the key was refused")

    monkeypatch.setattr(routes_keys, "check_provider_key", refused)


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[httpx.AsyncClient]:
    """A daemon on ``home`` that a reverse proxy presents as ``TAILNET_NAME``, and a client of its API."""
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
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api", timeout=30.0) as client:
                yield client
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _phone_headers(client: httpx.AsyncClient) -> dict[str, str]:
    code: str = (await client.post("/phone/pairing", headers={"Authorization": f"Bearer {TOKEN}"})).json()["code"]
    phone: dict[str, str] = {"Host": TAILNET_NAME, "Origin": f"https://{TAILNET_NAME}", "User-Agent": "DotNext iPhone"}
    paired: httpx.Response = await client.post("/phone/pair", json={"code": code}, headers=phone)
    assert paired.status_code == 200, paired.text
    return {**phone, "Authorization": f"Bearer {paired.json()['token']}"}


async def test_a_phone_saves_checks_and_removes_a_key_that_never_comes_back(
    own_home: Path, tells: list[tuple[str, str, str]], stand_in_check: None
) -> None:
    with capture_logs() as logs:
        async with _served(own_home) as client:
            phone: dict[str, str] = await _phone_headers(client)
            saved: httpx.Response = await client.post(f"/keys/{NAME}", json={"value": KEY}, headers=phone)
            assert saved.status_code == 200, saved.text
            assert saved.json()["tail"] == KEY[-4:] and saved.json()["source"] == "env_file"
            assert saved.json()["check"] is None
            # The check is kept as the key's last one, and the list says so.
            checked: httpx.Response = await client.post(f"/keys/{NAME}/check", headers=phone)
            assert checked.status_code == 200 and checked.json()["ok"] is False
            listed: httpx.Response = await client.get("/keys", headers=phone)
            row: dict[str, object] = next(key for key in listed.json()["keys"] if key["name"] == NAME)
            check = row["check"]
            assert isinstance(check, dict) and check["ok"] is False and "refused" in str(check["detail"])
            removed: httpx.Response = await client.delete(f"/keys/{NAME}", headers=phone)
            assert removed.status_code == 200 and removed.json()["source"] is None and removed.json()["check"] is None
            answers: str = saved.text + checked.text + listed.text + removed.text
            assert KEY not in answers and KEY[:-4] not in answers
    # Each change was told to the owner: the provider and the phone, nothing of the key.
    assert [title for title, _, _ in tells] == [
        "The OpenRouter key was saved from a phone",
        "The OpenRouter key was removed from a phone",
    ]
    assert all(url == "#/settings/keys" and "iPhone" in text for _, text, url in tells), tells
    assert not any(KEY[-8:] in title + text for title, text, _ in tells)
    logged: str = repr(logs)
    assert "key_saved" in logged and KEY not in logged and KEY[-8:] not in logged


async def test_a_phone_is_limited_and_the_macs_token_is_not(
    own_home: Path, tells: list[tuple[str, str, str]], stand_in_check: None
) -> None:
    async with _served(own_home) as client:
        phone: dict[str, str] = await _phone_headers(client)
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        first: httpx.Response = await client.post(f"/keys/{NAME}/check", headers=phone)
        assert first.status_code == 200
        again: httpx.Response = await client.post(f"/keys/{NAME}/check", headers=phone)
        assert again.status_code == 429 and again.json()["context"]["retry_after_s"] > 0
        assert (await client.post(f"/keys/{NAME}/check", headers=mac)).status_code == 200
        # A removal of a key Dot does not hold changes nothing and spends none of the phone's changes.
        assert (await client.delete(f"/keys/{NAME}", headers=phone)).status_code == 404
        for _ in range(routes_keys.PHONE_CHANGES):
            assert (await client.post(f"/keys/{NAME}", json={"value": KEY}, headers=phone)).status_code == 200
        past: httpx.Response = await client.post(f"/keys/{NAME}", json={"value": KEY}, headers=phone)
        assert past.status_code == 429, past.text
        assert (await client.delete(f"/keys/{NAME}", headers=phone)).status_code == 429
        # The Mac's token keeps every right it had, and its changes are not told: the owner made them on the Mac.
        assert (await client.delete(f"/keys/{NAME}", headers=mac)).status_code == 200
    assert len(tells) == routes_keys.PHONE_CHANGES


async def test_a_refused_key_is_not_echoed(own_home: Path) -> None:
    async with _served(own_home) as client:
        phone: dict[str, str] = await _phone_headers(client)
        spaced: str = "sk-or with a space 9f3k"
        refused: httpx.Response = await client.post(f"/keys/{NAME}", json={"value": spaced}, headers=phone)
        assert refused.status_code == 422 and "9f3k" not in refused.text


async def test_a_check_that_began_before_a_change_records_nothing(
    own_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check of the old key that ends after a new key was saved would show its verdict against the new one (item
    2054's security review)."""
    started: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()

    async def slow(config: object, provider: str) -> CheckResult:
        started.set()
        await release.wait()
        return CheckResult(name=f"{provider} api", ok=True, required=True, detail="took the old key")

    monkeypatch.setattr(routes_keys, "check_provider_key", slow)
    async with _served(own_home) as client:
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        checking: asyncio.Task[httpx.Response] = asyncio.create_task(client.post(f"/keys/{NAME}/check", headers=mac))
        await started.wait()
        assert (await client.post(f"/keys/{NAME}", json={"value": KEY}, headers=mac)).status_code == 200
        release.set()
        assert (await checking).status_code == 200
        listed: httpx.Response = await client.get("/keys", headers=mac)
        assert next(key for key in listed.json()["keys"] if key["name"] == NAME)["check"] is None

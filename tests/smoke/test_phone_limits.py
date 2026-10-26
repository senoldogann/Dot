"""What a phone's key may not do (item 678), on a served daemon: what is shown or connected on the Mac alone answers
the installation's token, not a phone's key, whatever Host header the phone's request writes (the eye of the vault
answered a phone's key with a loopback Host before); and a phone's key cannot widen for good what runs may do without
asking: add a connected app, switch the sandbox or the extra checker off, or add a rule that lets steps through. What
narrows stays open to it, and so does yolo, the owner's to use from wherever they are: a schedule, a loop, an agent or a
campaign kept in yolo. The Mac's token does it all. No network and no model service is used."""

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
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config

pytestmark = pytest.mark.asyncio

TOKEN: str = "phone-limits-test-token"
TAILNET_NAME: str = "dot-mac.tailnet-test.ts.net"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[httpx.AsyncClient, int, Runtime]]:
    """A daemon on ``home`` that a reverse proxy presents as ``TAILNET_NAME``: a client of its API, its port and its
    runtime."""
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
                yield client, port, runtime
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _phone_key(client: httpx.AsyncClient) -> str:
    code: str = (await client.post("/phone/pairing", headers={"Authorization": f"Bearer {TOKEN}"})).json()["code"]
    paired: httpx.Response = await client.post(
        "/phone/pair", json={"code": code}, headers={"Host": TAILNET_NAME, "Origin": f"https://{TAILNET_NAME}"}
    )
    return str(paired.json()["token"])


async def test_a_phone_key_is_not_this_mac_whatever_host_it_writes(own_home: Path) -> None:
    async with _served(own_home) as (client, port, _):
        key: str = await _phone_key(client)
        # The Host of this Mac's own address, as a client can write it through the tailnet's proxy.
        phone: dict[str, str] = {"Authorization": f"Bearer {key}", "Host": f"127.0.0.1:{port}"}
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        revealed: httpx.Response = await client.post(
            "/vault/items/no-such-item/reveal", json={"part": "password"}, headers=phone
        )
        assert revealed.status_code == 403 and "on the Mac itself" in revealed.json()["message"]
        # The Mac's token gets past the check: there is no such item.
        assert (
            await client.post("/vault/items/no-such-item/reveal", json={"part": "password"}, headers=mac)
        ).status_code == 404
        mac_only: list[tuple[str, dict[str, str]]] = [
            ("/vault/connect", {}),
            ("/accounts/google/start", {}),
            ("/voice/allow", {"pane": "microphone"}),
        ]
        for path, body in mac_only:
            answer: httpx.Response = await client.post(path, json=body, headers=phone)
            assert answer.status_code in (403, 422) and "Mac" in answer.json()["message"], (path, answer.text)


async def test_a_phone_key_cannot_widen_for_good_what_runs_do_unasked(own_home: Path) -> None:
    async with _served(own_home) as (client, _, _):
        key: str = await _phone_key(client)
        phone: dict[str, str] = {"Authorization": f"Bearer {key}"}
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        widening: list[tuple[str, str, dict[str, object]]] = [
            ("POST", "/mcp/servers", {"service": None, "name": "x", "address": "npx x", "origin": "http://a"}),
            ("PUT", "/shell/sandbox", {"sandbox": False}),
            ("POST", "/rules", {"behavior": "allow", "tool_glob": "shell", "match_regex": "", "description": ""}),
        ]
        for method, path, body in widening:
            refused: httpx.Response = await client.request(method, path, json=body, headers=phone)
            assert refused.status_code == 403 and "a phone cannot" in refused.json()["message"], (path, refused.text)
        narrowing: list[tuple[str, str, dict[str, object]]] = [
            ("POST", "/schedules", {"name": "n", "goal": "g", "cron": "0 9 * * *", "approvals": "ask"}),
            ("POST", "/rules", {"behavior": "ask", "tool_glob": "shell", "match_regex": "", "description": ""}),
            # yolo is the owner's to use from wherever they are (the owner's word on item 678): a schedule or an agent
            # in yolo is made from the phone as on the Mac.
            ("POST", "/schedules", {"name": "y", "goal": "g", "cron": "0 9 * * *", "approvals": "yolo"}),
            (
                "POST",
                "/agents",
                {"name": "a", "role": "", "instructions": "", "model": "", "effort": None, "approvals": "yolo"},
            ),
        ]
        for method, path, body in narrowing:
            allowed: httpx.Response = await client.request(method, path, json=body, headers=phone)
            assert allowed.status_code == 201, (path, allowed.text)
        rule: httpx.Response = await client.post(
            "/rules",
            json={"behavior": "allow", "tool_glob": "shell", "match_regex": "", "description": ""},
            headers=mac,
        )
        assert rule.status_code == 201
        rule_id: str = rule.json()["id"]
        await client.patch(f"/rules/{rule_id}", json={"enabled": False}, headers=mac)
        switched: httpx.Response = await client.patch(f"/rules/{rule_id}", json={"enabled": True}, headers=phone)
        assert switched.status_code == 403
        assert (await client.patch(f"/rules/{rule_id}", json={"enabled": True}, headers=mac)).status_code == 200


async def test_a_phone_key_cannot_loosen_the_rules_but_keeps_yolo_in_reach(own_home: Path) -> None:
    """The security review of item 678: a rule that asks deleted, switched off or passed by a move widens what runs do
    as an allow rule does, and a server another agent set up is started by Dot as one added on Connectors is. yolo is
    not held from a phone: its schedules, agents and campaigns are the owner's to keep from wherever they are."""
    async with _served(own_home) as (client, _, runtime):
        key: str = await _phone_key(client)
        phone: dict[str, str] = {"Authorization": f"Bearer {key}"}
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        asking: httpx.Response = await client.post(
            "/rules", json={"behavior": "ask", "tool_glob": "shell", "match_regex": "", "description": ""}, headers=mac
        )
        rule_id: str = asking.json()["id"]
        refused: list[httpx.Response] = [
            await client.patch(f"/rules/{rule_id}", json={"enabled": False}, headers=phone),
            await client.delete(f"/rules/{rule_id}", headers=phone),
            await client.post(f"/rules/{rule_id}/move", json={"direction": "down"}, headers=phone),
            await client.put(
                "/skills/elsewhere",
                json={"kind": "server", "agent": "claude", "name": "x", "added": True},
                headers=phone,
            ),
            await client.post(
                "/skills/elsewhere/import",
                json={"items": [{"kind": "server", "agent": "claude", "name": "x"}]},
                headers=phone,
            ),
        ]
        assert [answer.status_code for answer in refused] == [403] * 5, [answer.text for answer in refused]
        # yolo is the owner's to use from wherever they are (the owner's word on item 678): from the phone, a schedule
        # runs as an agent kept in yolo, one kept in yolo is switched on again, and a campaign and an agent kept in yolo
        # go on.
        agent: httpx.Response = await client.post(
            "/agents",
            json={"name": "Night", "role": "", "instructions": "", "model": "", "effort": None, "approvals": "yolo"},
            headers=phone,
        )
        assert agent.status_code == 201, agent.text
        agent_id: str = agent.json()["id"]
        through: httpx.Response = await client.post(
            "/schedules", json={"name": "n", "goal": "g", "cron": "0 9 * * *", "agent_id": agent_id}, headers=phone
        )
        assert through.status_code == 201, through.text
        kept: httpx.Response = await client.post(
            "/schedules",
            json={"name": "k", "goal": "g", "cron": "0 9 * * *", "approvals": "yolo", "enabled": False},
            headers=phone,
        )
        assert kept.status_code == 201, kept.text
        schedule_id: str = kept.json()["record"]["id"]
        assert (await client.post(f"/schedules/{schedule_id}/enable", headers=phone)).status_code == 200
        thread = await runtime.store.create_thread("campaign", "test")
        campaign = await runtime.store.create_campaign(
            "c", "goal", thread.id, "ollama:x", "medium", "standard", None, "yolo", (), None
        )
        await runtime.store.set_campaign_status(campaign.id, "stopped", None)
        assert (await client.post(f"/campaigns/{campaign.id}/resume", headers=phone)).status_code == 200
        assert (await client.post(f"/agents/{agent_id}/pause", headers=phone)).status_code == 200
        assert (await client.post(f"/agents/{agent_id}/resume", headers=phone)).status_code == 200
        # The extra checker is switched off on the Mac alone.
        assert (await client.put("/judge", json={"provider": "none"}, headers=phone)).status_code == 403
        assert (await client.delete(f"/rules/{rule_id}", headers=mac)).status_code == 200


async def test_a_phone_key_makes_the_loop_of_a_chat_in_yolo(own_home: Path) -> None:
    """``/loop`` from the phone makes the loop of a chat that runs in yolo, as on the Mac (the owner's word on item 678:
    they run their Dot from the phone when away from the Mac)."""
    async with _served(own_home) as (client, _, runtime):
        key: str = await _phone_key(client)
        phone: dict[str, str] = {"Authorization": f"Bearer {key}"}
        thread = await runtime.store.create_thread("Watch", "test")
        run = await runtime.store.create_run(
            thread.id,
            "watch",
            "task",
            "ollama:x",
            None,
            "test",
            "medium",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        made: httpx.Response = await client.post(
            f"/runs/{run.id}/loop", json={"every": "1h", "text": "Check the inbox"}, headers=phone
        )
        assert made.status_code == 201, made.text
        assert made.json()["options"]["approvals"] == "yolo"

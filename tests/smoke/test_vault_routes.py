"""Customize › Connectors › 1Password's routes (item 493), through a real in-process server and a stand-in for ``op``:
where 1Password stands; a pasted token that is none refused; Connect, which makes the vault "Shared with Dot" and a
read-only service account with the owner's ``op`` and keeps its token in Dot's .env, never answering it; Disconnect.
No network is used and no 1Password account."""

import asyncio
import json
import secrets
import stat
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.vault.onepassword import VAULT_NAME, kept_token

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "remove-me"
MADE: str = "ops_" + secrets.token_urlsafe(32)
STAND_IN: Path = Path(__file__).resolve().parent.parent / "fixtures" / "op_stand_in.py"


def _stand_in(tools: Path, home: Path) -> None:
    """An ``op`` in ``tools`` that the stand-in answers, signed in to an account of no vault, and the home's config."""
    state: JsonObject = {
        "accounts": [{"url": "my.1password.com", "email": "ada@example.com"}],
        "vaults": [],
        "token": None,
        "reads": [],
        "made_token": MADE,
        "items": {},
    }
    (tools / "state.json").write_text(json.dumps(state), encoding="utf-8")
    wrapper: Path = tools / "op"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STAND_IN}" "{tools / "state.json"}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[httpx.AsyncClient]:
    """A client of the API, on this Mac's own address, over a runtime whose PATH finds the stand-in ``op``: signed in
    to an account of no vault, which Connect gives one."""
    home: Path = tmp_path_factory.mktemp("home")
    tools: Path = tmp_path_factory.mktemp("op")
    _stand_in(tools, home)
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("PATH", f"{tools}:/usr/bin:/bin")
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(context)),
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            yield http
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def test_connect_makes_the_vault_and_keeps_its_token_unseen_and_disconnect_forgets_it(
    client: httpx.AsyncClient,
) -> None:
    before: JsonObject = (await client.get("/api/vault")).raise_for_status().json()
    assert before["connected"] is False and str(before["cli"]).endswith("/op"), before
    pasted: httpx.Response = await client.post("/api/vault/token", json={"token": "not-a-token-of-1password-at-all"})
    assert pasted.status_code == 422 and "starts with ops_" in pasted.text, pasted.text
    connected: httpx.Response = await client.post("/api/vault/connect", json={})
    assert connected.status_code == 200, connected.text
    assert [(vault["name"], vault["logins"]) for vault in connected.json()["vaults"]] == [(VAULT_NAME, 0)]
    assert MADE not in connected.text
    assert kept_token() == MADE
    gone: JsonObject = (await client.delete("/api/vault")).raise_for_status().json()
    assert gone["connected"] is False
    assert kept_token() is None

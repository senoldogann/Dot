"""A run asks for a secret on a card of its own (item 857): the owner gives it through the card's route, the chat's
next command reads it from its environment, and the value is in no event, approval, message, tool result, log or
file of the home but the chat's own. The plain decision of the card refuses a yes with no value; a decline tells the
run so. A real server on a scripted model; the value is made up and the command only counts and prints it."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import async_playwright, expect

from dotagent import secrets
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, EventRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio

TOKEN: str = "ask-secret-token"
VALUE: str = "made-up-cloud-token-0123456789-item-857"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 30.0
_ASK: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(
        ScriptedCall(
            tool="ask_secret",
            arguments={
                "name": "DEMO_TOKEN",
                "label": "Demo API token (read)",
                "why": "To read the visits. Make one at demo.example: Profile, then Tokens, then Create, read only.",
            },
        ),
    ),
)
# The command counts the value's characters and prints it: the count shows it reached the command, the print that what
# a command prints of it is masked.
_USE: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(ScriptedCall(tool="shell", arguments={"command": 'printf %s "$DEMO_TOKEN" | wc -c; echo "$DEMO_TOKEN"'}),),
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)


def _reads_of_the_folder(home: Path) -> list[ScriptedTurn]:
    """Runs of another chat that try to read the chats' secrets from their folder: one, a command then a file tool;
    the next, in yolo, a command."""
    folder: Path = home / "held-secrets"
    read: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": f"cat {folder}/*.json"}),)
    )
    return [
        read,
        ScriptedTurn(text="", calls=(ScriptedCall(tool="list_dir", arguments={"path": str(folder)}),)),
        _FINISH,
        read,
    ]


@pytest_asyncio.fixture
async def served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> AsyncIterator[tuple[RunManager, httpx.AsyncClient]]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    # A test that reads the folder takes its run's turns from its parameter; the others, the second run's card.
    second: list[ScriptedTurn] = _reads_of_the_folder(home) if getattr(request, "param", "") == "reads" else [_ASK]
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_ASK, _USE, _FINISH, *second, _FINISH]))
    manager: RunManager = RunManager(runtime)
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as client:
            yield manager, client
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)


async def _card(client: httpx.AsyncClient) -> dict[str, object]:
    async with asyncio.timeout(_WAIT_S):
        while True:
            found = (await client.get("/api/approvals", params={"status": "pending", "limit": 10})).json()
            if found:
                return found[0]
            await asyncio.sleep(0.05)


async def test_the_secret_reaches_the_chats_command_and_nothing_else(
    served: tuple[RunManager, httpx.AsyncClient], tmp_path: Path
) -> None:
    manager, client = served
    started = await client.post("/api/runs", json={"goal": "Read the visits of my site", "approvals": "ask"})
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    card: dict[str, object] = await _card(client)
    assert card["severity"] == "secret" and card["tool"] == "ask_secret"
    assert card["args"] == {
        "name": "DEMO_TOKEN",
        "label": "Demo API token (read)",
        "why": "To read the visits. Make one at demo.example: Profile, then Tokens, then Create, read only.",
    }
    # A yes with no value is refused: it would tell the run a secret was given.
    plain = await client.post(f"/api/approvals/{card['id']}/decision", json={"approve": True, "note": VALUE})
    assert plain.status_code == 422, plain.text
    given = await client.post(f"/api/approvals/{card['id']}/secret", json={"value": VALUE})
    assert given.status_code == 200, given.text
    assert (given.json()["status"], given.json()["note"]) == ("approved", "")
    # Once given, the card takes nothing more.
    again = await client.post(f"/api/approvals/{card['id']}/secret", json={"value": VALUE})
    assert again.status_code == 422, again.text
    async with asyncio.timeout(_WAIT_S):
        assert (await manager.wait(run_id)).status == "done"
    events: list[EventRecord] = await manager._runtime.store.list_events(run_id, 0, 500)
    results: dict[str, str] = {
        str(event.data.get("tool")): str(event.data.get("text", "")) for event in events if event.kind == "tool_result"
    }
    asked, used = results["ask_secret"], results["shell"]
    # The run reads the name to use, never the value; its command read the value, and printed it masked.
    assert "$DEMO_TOKEN" in asked
    assert str(len(VALUE)) in used
    for event in events:
        assert VALUE not in json.dumps(event.data), event.kind
    approvals: list[ApprovalRecord] = await manager._runtime.store.list_approvals(None, 50)
    assert all(VALUE not in record.model_dump_json() for record in approvals)
    # No file of the home but the chat's own holds it, the database and the logs among them.
    home: Path = tmp_path / "home"
    held: Path = home / "held-secrets" / f"{started.json()['thread_id']}.json"
    holding: list[Path] = await asyncio.to_thread(_files_holding, [home, tmp_path / "logs"], VALUE)
    assert holding == [held], holding


def _files_holding(folders: list[Path], value: str) -> list[Path]:
    return [
        file
        for folder in folders
        for file in sorted(folder.rglob("*"))
        if file.is_file() and value.encode() in file.read_bytes()
    ]


async def test_a_declined_card_tells_the_run_so(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    # The script's first run is the test above's: start one and give it what it asks, so this one is the second.
    first = await client.post("/api/runs", json={"goal": "Read the visits", "approvals": "ask"})
    card = await _card(client)
    await client.post(f"/api/approvals/{card['id']}/secret", json={"value": VALUE})
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(first.json()["id"])
    second = await client.post("/api/runs", json={"goal": "Read the visits again", "approvals": "ask"})
    card = await _card(client)
    declined = await client.post(f"/api/approvals/{card['id']}/decision", json={"approve": False, "note": ""})
    assert declined.status_code == 200, declined.text
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(second.json()["id"])
    events = await manager._runtime.store.list_events(second.json()["id"], 0, 200)
    (said,) = [
        str(event.data.get("text", ""))
        for event in events
        if event.kind == "tool_result" and event.data.get("tool") == "ask_secret"
    ]
    assert "did not give" in said and "Do not ask for it in a message" in said


@pytest.mark.parametrize("served", ["reads"], indirect=True)
async def test_a_run_of_another_chat_cannot_read_the_folder_the_secrets_are_held_in(
    served: tuple[RunManager, httpx.AsyncClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, client = served
    # Pasted with a line end, as a value copied from a page often is: it is held, and masked, without it.
    first = await client.post("/api/runs", json={"goal": "Read the visits", "approvals": "ask"})
    card = await _card(client)
    given = await client.post(f"/api/approvals/{card['id']}/secret", json={"value": f"  {VALUE}\n"})
    assert given.status_code == 200, given.text
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(first.json()["id"])
    first_events = await manager._runtime.store.list_events(first.json()["id"], 0, 200)
    (used,) = [
        str(event.data.get("text", ""))
        for event in first_events
        if event.kind == "tool_result" and event.data.get("tool") == "shell"
    ]
    assert str(len(VALUE)) in used and VALUE not in used
    other = await client.post("/api/runs", json={"goal": "Look around", "approvals": "ask"})
    assert other.json()["thread_id"] != first.json()["thread_id"]
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(other.json()["id"])
    events = await manager._runtime.store.list_events(other.json()["id"], 0, 200)
    results: dict[str, EventRecord] = {
        str(event.data.get("tool")): event for event in events if event.kind == "tool_result"
    }
    # Refused before it ran: the folder is guarded as the secrets file is.
    for tool in ("shell", "list_dir"):
        assert results[tool].data.get("ok") is False, results[tool].data
        assert "DEMO_TOKEN" not in str(results[tool].data.get("text", ""))
    # A yolo run may read any file, this one too: what it reads of the value is masked, in its history as well, in a
    # process whose masks lack it (a `dotx run` started before the value was given on the daemon's page).
    monkeypatch.setattr(secrets, "_filled", frozenset())
    loose = await client.post("/api/runs", json={"goal": "Look around again", "approvals": "yolo"})
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(loose.json()["id"])
    loose_events = await manager._runtime.store.list_events(loose.json()["id"], 0, 200)
    (read,) = [str(event.data.get("text", "")) for event in loose_events if event.kind == "tool_result"][:1]
    assert "DEMO_TOKEN" in read and "***" in read
    for event in [*events, *loose_events]:
        assert VALUE not in json.dumps(event.data), event.kind
    for run in (other, loose):
        history = await manager._runtime.store.load_messages(run.json()["thread_id"], 0)
        assert all(VALUE not in message.model_dump_json() for message in history)


@pytest.mark.browser
async def test_the_card_takes_the_secret_in_a_box_that_shows_no_letter_and_says_where_it_goes(
    served: tuple[RunManager, httpx.AsyncClient], tmp_path: Path
) -> None:
    manager, client = served
    started = await client.post("/api/runs", json={"goal": "Read the visits of my site", "approvals": "ask"})
    run_id: str = started.json()["id"]
    await _card(client)
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"http://127.0.0.1:{port}/#/runs/{run_id}")
            card = page.locator(".approval.secret")
            await expect(card).to_be_visible()
            await expect(card.locator(".secret-label")).to_have_text("Demo API token (read)")
            await expect(card.locator(".secret-why")).to_contain_text("Make one at demo.example")
            box = card.get_by_role("textbox", name="Demo API token (read)")
            await expect(box).to_have_attribute("type", "password")
            await expect(card.locator(".secret-foot")).to_contain_text("Kept out of the chat")
            await expect(card.get_by_role("button", name="Decline")).to_be_visible()
            await page.screenshot(path=str(tmp_path / "secret-card.png"))
            # Too short to mask: the card keeps it and sends nothing.
            await box.fill("short")
            await card.get_by_role("button", name="Save securely").click()
            await expect(box).to_be_focused()
            await box.fill(VALUE)
            await card.get_by_role("button", name="Save securely").click()
            # Answered, the card leaves the conversation, its box emptied, and the run goes on.
            await expect(page.locator(".approval.secret")).to_have_count(0)
            async with asyncio.timeout(_WAIT_S):
                assert (await manager.wait(run_id)).status == "done"
            # The page never shows the value, the command's output masked included.
            assert VALUE not in await page.content()
        finally:
            await browser.close()

"""The pages of teams (item 610) in a real Chrome, over a real daemon whose model is the scripted one: the Agents page
shows Your teams above Your agents, a team a card with its agents' faces (four, the rest counted) and its newest chat,
and the agents a grid of compact cards, two to four across a wide window and one on a phone; a team's page takes a
message that starts a run of Dot's in a chat with the team, which the sidebar and the chat's bar name, and the chat's
next message goes on in it; a team with no agent left takes none; a removed team leaves its agents and its chats, and
its page says it is gone; and the owner puts agents together as a team and changes it."""

import asyncio
import re
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
ANSWER: ScriptedTurn = ScriptedTurn(text="Handled.", calls=())
# What the browser measures of the agents' grid: the cards of its first row, the tallest card, and the columns.
_GRID: str = """(selector) => {
  const boxes = [...document.querySelectorAll(selector)].map((node) => node.getBoundingClientRect());
  const first = boxes[0];
  return {
    count: boxes.length,
    across: boxes.filter((box) => Math.abs(box.top - first.top) < 2).length,
    columns: new Set(boxes.map((box) => Math.round(box.left))).size,
    tallest: Math.max(...boxes.map((box) => box.height)),
    widest: Math.max(...boxes.map((box) => box.right)),
    sideways: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
}"""
# Whether a line of text stands on one line and is not cut by its own box but by an ellipsis.
_ONE_LINE: str = """(node) => {
  const style = getComputedStyle(node);
  return node.getBoundingClientRect().height < parseFloat(style.lineHeight) * 1.5;
}"""


class _Recorder:
    """The scripted model, keeping every request it is sent."""

    def __init__(self) -> None:
        self.script: ScriptedProvider = ScriptedProvider(_PROVIDER, [])
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self.script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self.script.list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


async def _agent(client: httpx.AsyncClient, name: str, role: str) -> JsonObject:
    made = await client.post(
        "/api/agents",
        json={
            "name": name,
            "role": role,
            "instructions": "",
            "model": "",
            "effort": None,
            "approvals": None,
            "tools": ["shell"],
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


async def _team(client: httpx.AsyncClient, name: str, purpose: str, agents: list[JsonObject]) -> JsonObject:
    made = await client.post(
        "/api/teams", json={"name": name, "purpose": purpose, "agents": [agent["id"] for agent in agents]}
    )
    assert made.status_code == 201, made.text
    return made.json()


async def _clear(client: httpx.AsyncClient) -> None:
    """Every chat, team and agent gone, so that the next test starts as a new home."""
    for chat in (await client.get("/api/threads")).json():
        await client.request("DELETE", "/api/threads", json={"ids": [chat["id"]]})
    for team in (await client.get("/api/teams")).json():
        assert (await client.delete(f"/api/teams/{team['record']['id']}")).status_code == 204
    for row in (await client.get("/api/agents")).json():
        if row["record"]["id"] != "dot":
            assert (await client.delete(f"/api/agents/{row['record']['id']}")).status_code == 204


async def _chat(client: httpx.AsyncClient, manager: RunManager, team: JsonObject, goal: str) -> JsonObject:
    """A message of the owner's to the team, the run it starts once it has ended."""
    MODEL.script = ScriptedProvider(_PROVIDER, [ANSWER])
    started = await client.post("/api/runs", json={"goal": goal, "mode": "chat", "team_id": team["id"]})
    assert started.status_code == 201, started.text
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(started.json()["id"])
    return started.json()


async def _open(browser: Browser, client: httpx.AsyncClient, route: str, width: int, phone: bool = False) -> Page:
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900},
        reduced_motion="reduce",
        is_mobile=phone,
        has_touch=phone,
    )
    page: Page = await context.new_page()
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#{route}")
    return page


async def _launch(runtime: Runtime) -> tuple[Any, Browser]:
    playwright = await async_playwright().start()
    return playwright, await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)


async def test_your_teams_stand_above_your_agents_and_the_agents_are_a_grid_two_to_four_across_and_one_on_a_phone(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    agents: list[JsonObject] = [
        await _agent(client, f"Agent {number}", "Finds the sources of a claim and writes down what each one says")
        for number in range(1, 9)
    ]
    desk: JsonObject = await _team(client, "Research desk", "Finds sources and checks them", agents[:6])
    pair: JsonObject = await _team(client, "Night pair", "Answers what came in at night", agents[6:])
    await _chat(client, manager, desk, "Check the claims in the launch post")
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, "/agents", 1280)
        teams: Locator = page.locator(".owned-team")
        await expect(teams).to_have_count(2)
        yours_teams: Locator = page.locator("section[aria-label='Your teams']")
        yours_agents: Locator = page.locator("section[aria-label='Your agents']")
        await expect(yours_teams.locator(".section-label")).to_contain_text("Your teams")
        above = await yours_teams.bounding_box()
        below = await yours_agents.bounding_box()
        assert above is not None and below is not None and above["y"] + above["height"] <= below["y"]
        # A card names the team and what it is for; the faces of six agents are four and the rest counted, those of two
        # are two; its newest chat is the title of its chat, and a team with none says so.
        first: Locator = page.locator(f".owned-team[data-owned-team='{desk['id']}']")
        second: Locator = page.locator(f".owned-team[data-owned-team='{pair['id']}']")
        await expect(first.locator(".owned-team-name")).to_have_text("Research desk")
        await expect(first.locator(".owned-team-purpose")).to_have_text("Finds sources and checks them")
        await expect(first.locator(".face-stack .avatar")).to_have_count(4)
        await expect(first.locator(".face-stack-more")).to_have_text("+2")
        await expect(first.locator(".owned-team-count")).to_have_text("6 agents")
        await expect(first.locator(".agent-card-chat-title")).to_have_text("Check the claims in the launch post")
        await expect(second.locator(".face-stack .avatar")).to_have_count(2)
        await expect(second.locator(".face-stack-more")).to_have_count(0)
        await expect(second.locator(".agent-card-chat-title")).to_have_text("No chats yet")
        # Your agents: Dot and the eight, as cards; two to four across, none tall, each name and role on one line.
        grid: dict[str, Any] = await page.evaluate(_GRID, ".agent-card")
        assert grid["count"] == 9 and 2 <= grid["across"] <= 4 and grid["columns"] == grid["across"], grid
        assert grid["tallest"] < 140 and grid["sideways"] <= 0, grid
        card: Locator = page.locator(".agent-card[data-agent]").nth(1)
        assert await card.locator(".agent-card-title").evaluate(_ONE_LINE)
        assert await card.locator(".agent-card-role").evaluate(_ONE_LINE)
        await page.screenshot(path=tmp_path / "agents-1280.png", full_page=True)
        await first.click()
        await expect(page).to_have_url(f"{page.url.split('#')[0]}#/teams/{desk['id']}")
        await page.context.close()

        phone: Page = await _open(browser, client, "/agents", 390, phone=True)
        await expect(phone.locator(".owned-team")).to_have_count(2)
        narrow: dict[str, Any] = await phone.evaluate(_GRID, ".agent-card")
        assert narrow["count"] == 9 and narrow["across"] == 1 and narrow["columns"] == 1, narrow
        assert narrow["widest"] <= 390 and narrow["sideways"] <= 0, narrow
        narrow_teams: dict[str, Any] = await phone.evaluate(_GRID, ".owned-team")
        assert narrow_teams["across"] == 1 and narrow_teams["widest"] <= 390, narrow_teams
        await phone.screenshot(path=tmp_path / "agents-390.png", full_page=True)
        await phone.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)


async def test_use_team_puts_the_team_under_your_teams_and_a_second_use_keeps_its_one_card(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, "/agents", 1280)
        await expect(page.locator(".owned-team")).to_have_count(0)
        await expect(page.locator("section[aria-label='Your teams']")).to_contain_text("No teams yet")
        gallery: Locator = page.locator(".team-card[data-team='content']")
        use: Locator = gallery.get_by_role("button", name="Use the team Content team")
        await use.click()
        await expect(page.locator(".toast.ok", has_text="Content team is under Your teams.")).to_be_visible()
        card: Locator = page.locator(".owned-team")
        await expect(card).to_have_count(1)
        await expect(card.locator(".owned-team-name")).to_have_text("Content team")
        await expect(card.locator(".face-stack .avatar")).to_have_count(2)
        await expect(gallery.locator(".team-added")).to_have_text("In your teams")
        await use.click()
        await expect(
            page.locator(".toast.ok", has_text="You have every agent of the Content team already.")
        ).to_be_visible()
        await expect(card).to_have_count(1)
        assert len((await client.get("/api/teams")).json()) == 1
        await page.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)


async def test_a_message_to_a_team_starts_a_run_in_a_chat_with_the_team_which_the_sidebar_and_the_bar_name(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds and checks sources")
    writer: JsonObject = await _agent(client, "Writer", "Writes the copy")
    desk: JsonObject = await _team(client, "Desk", "Writes a report with sources", [scout, writer])
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, f"/teams/{desk['id']}", 1280)
        await expect(page.get_by_role("heading", level=1)).to_have_text("Desk")
        await expect(page.locator(".team-who .home-name")).to_have_text("Desk")
        await expect(page.locator(".team-who .home-greeting")).to_have_text("Writes a report with sources")
        await expect(page.locator(".team-roster li")).to_have_text(["Scout", "Writer"])
        await expect(page.locator(".team-who .face-stack .avatar")).to_have_count(2)
        await expect(page).to_have_title("Desk · Agents · Dot")
        message: Locator = page.locator("textarea[aria-label='Message']")
        await expect(message).to_have_attribute("placeholder", "Message Desk")
        await expect(page.locator(".team-empty")).to_have_count(0)
        MODEL.script = ScriptedProvider(_PROVIDER, [ANSWER, ANSWER])
        await message.fill("Write the report on the launch")
        async with page.expect_response(
            lambda response: response.url.endswith("/api/runs") and response.request.method == "POST"
        ) as sent:
            await message.press("Enter")
        run: JsonObject = await (await sent.value).json()
        await expect(page).to_have_url(f"{page.url.split('#')[0]}#/runs/{run['id']}")
        # A run of Dot's, in a thread of the team, that was told it leads the team.
        assert run["agent_id"] is None
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(run["id"])
        detail: JsonObject = (await client.get(f"/api/runs/{run['id']}")).json()
        assert detail["team"] == {"id": desk["id"], "name": "Desk", "agents": [scout["id"], writer["id"]]}
        head: str = next(m.content for m in MODEL.requests[-1].messages if m.role == "system")
        assert head.startswith('You lead the team "Desk"') and "  - Scout: Finds and checks sources" in head, head[:300]
        # The chat's bar and the sidebar's row name the team and wear the faces of its agents.
        await expect(page.locator(".stage-title .stage-name")).to_have_text("Desk")
        await expect(page.locator(".stage-title .team-duo .avatar")).to_have_count(2)
        row: Locator = page.locator("#sidebar a.side-run", has_text="Write the report on the launch")
        await expect(row.locator(".side-chat-team")).to_have_text("Desk")
        await expect(row.locator(".team-duo .avatar")).to_have_count(2)
        await expect(page.locator(f"#panel-run-details a[href='#/teams/{desk['id']}']")).to_have_count(1)
        await page.screenshot(path=tmp_path / "team-chat-1280.png")
        # The chat's next message, typed in the chat's own box, is told the team too.
        await expect(page.locator(".bubble.live")).to_have_count(0)
        follow: Locator = page.locator(".composer textarea").first
        await follow.fill("Make it shorter")
        async with page.expect_response(
            lambda response: response.url.endswith("/api/runs") and response.request.method == "POST"
        ) as again:
            await follow.press("Enter")
        second: JsonObject = await (await again.value).json()
        assert second["thread_id"] == run["thread_id"] and second["agent_id"] is None
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(second["id"])
        later: str = next(m.content for m in MODEL.requests[-1].messages if m.role == "system")
        assert later.startswith('You lead the team "Desk"'), later[:200]
        # An agent the owner puts in the chat answers as itself: the row wears its face, as the chat's page does, even
        # when the page sees only the end of its run (its stream was away while it ran): the team's faces are not
        # kept for it.
        await page.context.set_offline(True)
        MODEL.script = ScriptedProvider(_PROVIDER, [ANSWER])
        asked = await client.post(
            "/api/runs",
            json={
                "goal": "Scout, one more source",
                "mode": "chat",
                "thread_id": run["thread_id"],
                "agent_id": scout["id"],
            },
        )
        assert asked.status_code == 201, asked.text
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(asked.json()["id"])
        await page.context.set_offline(False)
        await expect(row.locator(".team-duo")).to_have_count(0, timeout=15_000)
        await expect(row.locator(".avatar")).to_have_count(1)
        # The team's page lists the chat once, whatever the runs it holds.
        await page.goto(f"{page.url.split('#')[0]}#/teams/{desk['id']}")
        await expect(page.locator(".list-row[data-chat]")).to_have_count(1)
        await expect(page.locator(".list-row[data-chat] .row-title")).to_have_text("Write the report on the launch")
        await page.context.close()

        phone: Page = await _open(browser, client, f"/teams/{desk['id']}", 390, phone=True)
        await expect(phone.locator("textarea[aria-label='Message']")).to_be_visible()
        sideways: int = await phone.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert sideways <= 0
        await phone.screenshot(path=tmp_path / "team-390.png", full_page=True)
        await phone.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)


async def test_a_team_with_no_agent_left_says_so_and_takes_no_message_until_the_owner_gives_it_one(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    lone: JsonObject = await _agent(client, "Lone", "Works alone")
    other: JsonObject = await _agent(client, "Other", "Fills in")
    team: JsonObject = await _team(client, "Solo", "One agent", [lone])
    assert (await client.delete(f"/api/agents/{lone['id']}")).status_code == 204
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, f"/teams/{team['id']}", 1280)
        note: Locator = page.locator(".team-empty")
        await expect(note).to_contain_text("no agents left")
        await expect(page.locator("textarea[aria-label='Message']")).to_have_count(0)
        await expect(page.locator(".team-roster")).to_have_count(0)
        # The refusal is the daemon's too: no chat starts with a team that has no agent.
        refused = await client.post("/api/runs", json={"goal": "Anyone?", "mode": "chat", "team_id": team["id"]})
        assert refused.status_code == 422, refused.text
        await page.goto(f"{page.url.split('#')[0]}#/agents")
        await expect(page.locator(".owned-team .owned-team-count")).to_have_text("No agents left")
        await page.go_back()
        # Edit puts an agent of the owner's in, and the box comes back.
        await note.get_by_role("button", name="Edit").click()
        editor: Locator = page.locator(".team-editor")
        await expect(editor.get_by_role("button", name="Save changes")).to_be_disabled()
        await editor.locator(f".team-pick[data-agent='{other['id']}'] input").check()
        await editor.get_by_role("button", name="Save changes").click()
        await expect(page.locator("textarea[aria-label='Message']")).to_be_visible()
        await expect(page.locator(".team-roster li")).to_have_text(["Other"])
        await expect(page.locator(".team-empty")).to_have_count(0)
        await page.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)


async def test_a_team_page_that_is_open_while_the_team_changes_follows_it_at_the_next_run(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    lone: JsonObject = await _agent(client, "Lone", "Works alone")
    other: JsonObject = await _agent(client, "Other", "Fills in")
    team: JsonObject = await _team(client, "Solo", "One agent", [lone])
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, f"/teams/{team['id']}", 1280)
        message: Locator = page.locator("textarea[aria-label='Message']")
        await expect(message).to_be_visible()
        # The last agent goes, in another window: the page says so once something moves, and takes the box away.
        assert (await client.delete(f"/api/agents/{lone['id']}")).status_code == 204
        MODEL.script = ScriptedProvider(_PROVIDER, [ANSWER, ANSWER])
        started = await client.post("/api/runs", json={"goal": "Something else", "mode": "chat"})
        assert started.status_code == 201, started.text
        await expect(page.locator(".team-empty")).to_contain_text("no agents left")
        await expect(message).to_have_count(0)
        # An agent is put in, in another window: the next run brings the box back, and the agent's name.
        changed = await client.put(
            f"/api/teams/{team['id']}", json={"name": "Solo", "purpose": "One agent", "agents": [other["id"]]}
        )
        assert changed.status_code == 200, changed.text
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(started.json()["id"])
        again = await client.post("/api/runs", json={"goal": "And more", "mode": "chat"})
        assert again.status_code == 201, again.text
        await expect(message).to_be_visible()
        await expect(page.locator(".team-empty")).to_have_count(0)
        await expect(page.locator(".team-roster li")).to_have_text(["Other"])
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(again.json()["id"])
        await page.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)


async def test_a_removed_team_leaves_its_agents_and_its_chats_and_its_page_says_it_is_gone(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds sources")
    team: JsonObject = await _team(client, "Desk", "Writes", [scout])
    run: JsonObject = await _chat(client, manager, team, "Write the launch report")
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, f"/teams/{team['id']}", 1280)
        await page.get_by_role("button", name="Remove the team").click()
        dialog: Locator = page.locator("dialog.modal")
        await expect(dialog).to_contain_text("Its agents stay yours")
        await dialog.get_by_role("button", name="Remove").click()
        await expect(page).to_have_url(f"{page.url.split('#')[0]}#/agents")
        await expect(page.locator(".toast.ok", has_text="Desk removed")).to_be_visible()
        await expect(page.locator(".owned-team")).to_have_count(0)
        await expect(page.locator(f".agent-card[data-agent='{scout['id']}']")).to_have_count(1)
        assert (await client.get(f"/api/teams/{team['id']}")).status_code == 404
        # The chat stays in the list, as a chat with Dot: no team's name, no faces of its agents.
        row: Locator = page.locator("#sidebar a.side-run", has_text="Write the launch report")
        await expect(row).to_have_count(1)
        await expect(row.locator(".side-chat-team")).to_have_count(0)
        await expect(row.locator(".team-duo")).to_have_count(0)
        # The team's page, asked for again, says it is gone and where its parts are.
        await page.goto(f"{page.url.split('#')[0]}#/teams/{team['id']}")
        await expect(page.locator(".team-gone")).to_contain_text("This team is gone")
        await expect(page.locator("textarea[aria-label='Message']")).to_have_count(0)
        await page.goto(f"{page.url.split('#')[0]}#/runs/{run['id']}")
        await expect(page.locator(".stage-title .stage-name")).not_to_have_text("Desk")
        await expect(page.locator(".stage-title .team-duo")).to_have_count(0)
        await page.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)


async def test_agents_picked_on_their_cards_make_a_team_in_the_order_picked(
    runtime: Runtime, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """Item 867, the owner's ask: "kendimiz agents sayfasından ajanları seçerek bir takım oluşturabilelim". Select
    turns the cards of the owner's agents into boxes to tick (Dot's own is none), the bar under the grid counts them,
    and Make a team opens the form with them ticked, first, in the order picked; Cancel leaves the cards as links."""
    scout: JsonObject = await _agent(client, "Scout", "Finds sources")
    writer: JsonObject = await _agent(client, "Writer", "Writes copy")
    checker: JsonObject = await _agent(client, "Checker", "Checks facts")
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, "/agents", 1280)
        mine: Locator = page.get_by_role("region", name="Your agents")
        await mine.get_by_role("button", name="Select").click()
        bar: Locator = page.get_by_role("region", name="Agents picked for a team")
        await expect(bar).to_contain_text("Tick the agents of the team")
        await expect(bar.get_by_role("button", name="Make a team")).to_be_disabled()
        await expect(mine.get_by_role("checkbox")).to_have_count(3)
        await mine.locator(f".agent-card[data-agent='{checker['id']}']").click()
        await mine.locator(f".agent-card[data-agent='{scout['id']}']").click()
        await expect(mine.locator(f".agent-card[data-agent='{checker['id']}']")).to_have_attribute(
            "aria-checked", "true"
        )
        await expect(bar).to_contain_text("2 agents picked")
        # Picking stays on the page: no card led away.
        await expect(page).to_have_url(re.compile(r"#/agents$"))
        await page.screenshot(path=str(tmp_path / "picking.png"))
        await bar.get_by_role("button", name="Make a team").click()
        editor: Locator = page.locator(".team-editor")
        await expect(editor.locator(".team-pick .team-pick-name")).to_have_text(["Checker", "Scout", "Writer"])
        await expect(editor.locator(f".team-pick[data-agent='{checker['id']}'] input")).to_be_checked()
        await expect(editor.locator(f".team-pick[data-agent='{writer['id']}'] input")).not_to_be_checked()
        await editor.get_by_label("Name").fill("Fact desk")
        await editor.get_by_role("button", name="Create team").click()
        await expect(page.get_by_role("heading", level=1)).to_have_text("Fact desk")
        made: JsonObject = (await client.get("/api/teams")).json()[0]["record"]
        assert made["agents"] == [checker["id"], scout["id"]]
        # Back on the page, Select and Cancel leave the cards as links again.
        await page.goto(page.url.split("#")[0] + "#/agents")
        await mine.get_by_role("button", name="Select").click()
        await bar.get_by_role("button", name="Cancel").click()
        await expect(mine.get_by_role("checkbox")).to_have_count(0)
        await expect(bar).to_be_hidden()
        await page.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)


async def test_new_team_puts_the_owners_agents_together_and_edit_changes_its_name_purpose_and_agents(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    scout: JsonObject = await _agent(client, "Scout", "Finds sources")
    writer: JsonObject = await _agent(client, "Writer", "Writes copy")
    checker: JsonObject = await _agent(client, "Checker", "Checks facts")
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, "/agents", 1280)
        await page.get_by_role("button", name="New team").click()
        editor: Locator = page.locator(".team-editor")
        await expect(editor.get_by_role("button", name="Create team")).to_be_disabled()
        await editor.get_by_label("Name").fill("Night desk")
        await editor.get_by_label("Purpose").fill("Answers what came in at night")
        await editor.locator(f".team-pick[data-agent='{checker['id']}'] input").check()
        await editor.locator(f".team-pick[data-agent='{scout['id']}'] input").check()
        await editor.get_by_role("button", name="Create team").click()
        await expect(page.get_by_role("heading", level=1)).to_have_text("Night desk")
        made: JsonObject = (await client.get("/api/teams")).json()[0]["record"]
        assert (made["name"], made["purpose"], made["ready_team"]) == (
            "Night desk",
            "Answers what came in at night",
            None,
        )
        # The agents go in the order the form lists them, which is the order of the agents the owner has.
        assert made["agents"] == [scout["id"], checker["id"]]
        # Edit: the agents the team has are first, the others after them; one taken out, one put in, the name changed.
        await page.get_by_role("button", name="Edit the team").click()
        edit: Locator = page.locator(".team-editor")
        await expect(edit.locator(".team-pick")).to_have_count(3)
        await expect(edit.locator(".team-pick .team-pick-name")).to_have_text(["Scout", "Checker", "Writer"])
        await edit.get_by_label("Name").fill("Day desk")
        await edit.locator(f".team-pick[data-agent='{scout['id']}'] input").uncheck()
        await edit.locator(f".team-pick[data-agent='{writer['id']}'] input").check()
        await edit.get_by_role("button", name="Save changes").click()
        await expect(page.get_by_role("heading", level=1)).to_have_text("Day desk")
        await expect(page.locator(".team-roster li")).to_have_text(["Checker", "Writer"])
        changed: JsonObject = (await client.get(f"/api/teams/{made['id']}")).json()["record"]
        assert (changed["name"], changed["agents"]) == ("Day desk", [checker["id"], writer["id"]])
        # Cancel leaves the team as it is.
        await page.get_by_role("button", name="Edit the team").click()
        await page.locator(".team-editor").get_by_label("Name").fill("Never saved")
        await page.locator(".team-editor").get_by_role("button", name="Cancel").click()
        await expect(page.get_by_role("heading", level=1)).to_have_text("Day desk")
        assert (await client.get(f"/api/teams/{made['id']}")).json()["record"]["name"] == "Day desk"
        await page.context.close()
    finally:
        await browser.close()
        await playwright.stop()
        await _clear(client)

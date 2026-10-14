"""Agents (item 86) through the API and the real loop: an agent is made, a run is started for it on the scripted
model, the run wears the agent (its id, its prompt, its bounds), the Agents page and the agent's page show it, and the
agent's end leaves its chats to Dot. An agent learns for itself (item 94): its lesson and its learned skill reach its
own run and not Dot's, its page lists them, and the agent's end leaves them to Dot. No network and no model service:
the scripted model answers.
"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import Playbook, playbook_name, read_meta, write_playbook
from dotagent.agent.review import PlaybookDraft
from dotagent.config import load_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.memory.service import save_memory
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.team import CREATE_AGENT_TOOL, MESSAGE_AGENT_TOOL

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_SETTLE_MS: int = 700
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
LESSON: str = "Primary sources on the matter are listed in the ministry's own register first."
SKILL_KIND: str = "Check a claim against the ministry register"
SKILL_NAME: str = playbook_name(SKILL_KIND)
FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Two sources.", "success": True}),)
)


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


def _skill(agent_id: str) -> Playbook:
    """A learned skill of the agent, as a run of its would have written it."""
    return Playbook(
        name=SKILL_NAME,
        kind=SKILL_KIND,
        host="register.example.gov",
        draft=PlaybookDraft(
            kind=SKILL_KIND,
            description="Look a claim up in the ministry register and quote the entry.",
            when="A goal that asks to check a claim against the register.",
            steps=("Open the register's search.", "Search <claim> and quote the entry."),
            parameters=("<claim>: the claim to check",),
        ),
        run_id="run-earlier",
        agent_id=agent_id,
        goal="Check the claim",
        written_at=time.time(),
        acting_steps=4,
    )


def _system_prompt(request: ChatRequest) -> str:
    return next(message.content for message in request.messages if message.role == "system")


async def _finished(client: httpx.AsyncClient, manager: RunManager, body: dict[str, object]) -> str:
    """Start a run through the API with ``body`` and wait for its end; its id."""
    started = await client.post("/api/runs", json=body)
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)
    return run_id


def _agent(tools: list[str]) -> dict[str, object]:
    return {
        "name": "Scout",
        "role": "Finds and checks sources",
        "instructions": "Prefer primary sources. Say what you could not confirm.",
        "model": "",
        "effort": None,
        "approvals": "yolo",
        "tools": tools,
    }


@pytest.mark.browser
async def test_a_run_makes_a_lasting_teammate_and_learns_of_it_at_its_next_step(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    make: ScriptedTurn = ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool=CREATE_AGENT_TOOL,
                arguments={"name": "Pilot", "role": "Writes copy", "instructions": "Be brief.", "tools": ["shell"]},
            ),
        ),
    )
    MODEL.script = ScriptedProvider(_PROVIDER, [make, FINISH])
    run_id: str = await _finished(
        client, manager, {"goal": "Set up a copywriter for the recurring newsletter", "approvals": "yolo"}
    )
    detail = await client.get(f"/api/runs/{run_id}")
    assert detail.json()["run"]["status"] == "done", detail.text
    agents = (await client.get("/api/agents")).json()
    pilot = next(row["record"] for row in agents if row["record"]["name"] == "Pilot")
    assert (pilot["role"], pilot["tools"], pilot["made_by"]) == ("Writes copy", ["shell"], run_id)
    # The first step offered message_agent though the run could make a teammate before it had one; the second step's
    # request carries the note of the newcomer.
    first, second = MODEL.requests[-2], MODEL.requests[-1]
    assert {CREATE_AGENT_TOOL, MESSAGE_AGENT_TOOL} <= {spec.name for spec in first.tools}
    notes: list[str] = [
        message.content
        for message in second.messages
        if isinstance(message.content, str) and "joined the team" in message.content
    ]
    assert notes and "- Pilot: Writes copy" in notes[0], [m.content for m in second.messages][-3:]
    # A second run with the same name is refused and told whom to message instead.
    MODEL.script = ScriptedProvider(_PROVIDER, [make, FINISH])
    again: str = await _finished(client, manager, {"goal": "Set up a copywriter again", "approvals": "yolo"})
    refused = [
        message.content
        for message in MODEL.requests[-1].messages
        if isinstance(message.content, str) and "an agent has that name already" in message.content
    ]
    assert refused, again
    assert len([row for row in (await client.get("/api/agents")).json() if row["record"]["name"] == "Pilot"]) == 1

    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(viewport={"width": 1280, "height": 860})
            page_ui: Page = await context.new_page()
            await page_ui.goto(ui_url(host, port, TOKEN))
            await page_ui.wait_for_timeout(_SETTLE_MS)
            await page_ui.goto(f"http://{host}:{port}/#/agents")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            row = page_ui.locator(f".agent-row[data-agent='{pilot['id']}']")
            assert await row.locator(".agent-made").inner_text() == "Made by Dot"
            await page_ui.screenshot(path=str(tmp_path / "agents-made-by-dot.png"))
        finally:
            await browser.close()
    # The teammate goes the way any agent goes, so the next test starts with Dot alone.
    assert (await client.delete(f"/api/agents/{pilot['id']}")).status_code == 204


@pytest.mark.browser
async def test_an_agent_is_made_runs_in_its_own_voice_and_bounds_and_leaves_its_chats_to_dot(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    refused = await client.post("/api/agents", json=_agent(["no_such_tool"]))
    assert refused.status_code == 422, refused.text
    # The agent may read skills too, so its runs are offered its own learned skill under Skills (item 94).
    made = await client.post("/api/agents", json=_agent(["shell", "skill"]))
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    listed = await client.get("/api/agents")
    assert [row["record"]["id"] for row in listed.json()] == ["dot", agent_id]
    assert listed.json()[1]["latest"] is None
    for forbidden in (client.put("/api/agents/dot", json=_agent([])), client.delete("/api/agents/dot")):
        answer = await forbidden
        assert answer.status_code == 422, answer.text

    MODEL.script = ScriptedProvider(_PROVIDER, [FINISH])
    started = await client.post("/api/runs", json={"goal": "Find two sources on the matter", "agent_id": agent_id})
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    assert started.json()["agent_id"] == agent_id
    # The agent's approvals bound the run; its model is the configured one, since the agent names none.
    assert started.json()["approvals"] == "yolo"
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)
    detail = await client.get(f"/api/runs/{run_id}")
    assert detail.json()["run"]["status"] == "done", detail.text
    assert detail.json()["agent"]["name"] == "Scout"
    # The prompt opens in the agent's voice; the tools offered are the one it names and the core ones.
    request: ChatRequest = MODEL.requests[-1]
    system: str = next(message.content for message in request.messages if message.role == "system")
    assert system.startswith("You are Scout, an agent the owner made: Finds and checks sources."), system[:200]
    assert "Prefer primary sources." in system
    offered: set[str] = {spec.name for spec in request.tools}
    assert "shell" in offered and FINISH_TOOL in offered
    assert "fetch" not in offered, offered
    page = await client.get(f"/api/agents/{agent_id}")
    assert [run["id"] for run in page.json()["runs"]] == [run_id]
    # The run's row on the Activity page wears the agent's face.
    activity = await client.get("/api/activity")
    recent = [row for row in activity.json()["recent"] if row["id"] == run_id]
    assert [row["actor_id"] for row in recent] == [agent_id], activity.text

    # The agent learns for itself (item 94): a lesson of its run and a skill of its own reach its next run, in the
    # prompt's notes and under Skills, and not Dot's run; its page lists them, scored by its own runs.
    await save_memory(runtime.memory, LESSON, ("lesson",), run_id, agent_id)
    await asyncio.to_thread(write_playbook, playbooks_dir(), _skill(agent_id))
    MODEL.script = ScriptedProvider(_PROVIDER, [FINISH, FINISH])
    second_id: str = await _finished(client, manager, {"goal": "Find two sources on the matter", "agent_id": agent_id})
    own_prompt: str = _system_prompt(MODEL.requests[-1])
    assert LESSON in own_prompt and SKILL_NAME in own_prompt, own_prompt[-1500:]
    dots_id: str = await _finished(client, manager, {"goal": "Find two sources on the matter"})
    dots_prompt: str = _system_prompt(MODEL.requests[-1])
    assert LESSON not in dots_prompt and SKILL_NAME not in dots_prompt, dots_prompt[-1500:]
    page = await client.get(f"/api/agents/{agent_id}")
    learned = page.json()["learned"]
    assert [lesson["content"] for lesson in learned["lessons"]] == [LESSON]
    lesson_id: str = learned["lessons"][0]["id"]
    assert learned["scores"] == {lesson_id: {"handed": 1, "done": 1, "failed": 0}}
    assert [(skill["name"], skill["agent_id"], skill["shared"]) for skill in learned["skills"]] == [
        (SKILL_NAME, agent_id, False)
    ]
    assert [run["id"] for run in page.json()["runs"]] == [second_id, run_id]
    assert dots_id != second_id
    # The Memory page names the agent on the lesson; shared, the skill reaches Dot's run too.
    memories = await client.get("/api/memories")
    assert memories.json()["agents"] == {agent_id: "Scout"}
    assert [note["agent_id"] for note in memories.json()["items"] if note["id"] == lesson_id] == [agent_id]
    shared = await client.put(f"/api/skills/learned/{SKILL_NAME}", json={"shared": True})
    assert shared.status_code == 200 and shared.json()["items"][0]["shared"] is True, shared.text
    MODEL.script = ScriptedProvider(_PROVIDER, [FINISH])
    await _finished(client, manager, {"goal": "Find two sources on the matter"})
    assert SKILL_NAME in _system_prompt(MODEL.requests[-1]) and LESSON not in _system_prompt(MODEL.requests[-1])

    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    out: Path = tmp_path / "agents"
    await asyncio.to_thread(out.mkdir)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 860}, device_scale_factor=1
            )
            page_ui: Page = await context.new_page()
            await page_ui.goto(ui_url(host, port, TOKEN))
            await page_ui.wait_for_timeout(_SETTLE_MS)
            # The composer offers the agent, and the Agents page lists Dot and the agent with its newest chat.
            await page_ui.goto(f"http://{host}:{port}/#/runs")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            await page_ui.locator(".composer-pill[data-pill='agent']").click()
            options: list[str] = await page_ui.locator(
                ".composer-popover [role=listbox][aria-label='Agent'] .pop-option-text > span:first-child"
            ).all_inner_texts()
            assert options == ["Dot", "Scout"], options
            await page_ui.keyboard.press("Escape")
            await page_ui.goto(f"http://{host}:{port}/#/agents")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            rows_text: list[str] = await page_ui.locator(".agent-row").all_inner_texts()
            assert len(rows_text) == 2 and "Scout" in rows_text[1] and "Find two sources" in rows_text[1], rows_text
            await page_ui.screenshot(path=str(out / "agents.png"))
            await page_ui.goto(f"http://{host}:{port}/#/agents/{agent_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            assert await page_ui.locator(".agent-who .home-name").inner_text() == "Scout"
            assert (
                await page_ui.locator("textarea[aria-label='Message']").get_attribute("placeholder") == "Message Scout"
            )
            assert await page_ui.locator(".list-row[data-run]").count() == 2
            # What it learned: the lesson with its score, the skill with Share.
            assert await page_ui.locator(".agent-learned [data-lesson]").count() == 1
            learned_text: str = await page_ui.locator(".agent-learned").inner_text()
            assert LESSON in learned_text and SKILL_NAME in learned_text and "Handed to 1 run, 1 done" in learned_text
            await page_ui.screenshot(path=str(out / "agent.png"))
            # The run's page wears the agent's face and name in the bar.
            await page_ui.goto(f"http://{host}:{port}/#/runs/{run_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            assert await page_ui.locator(".stage-title .stage-name").inner_text() == "Scout"
            assert await page_ui.locator(".stage-title .avatar[data-hue][data-shape]").count() == 1
            # The agent is a fact of the panel's Details, a link to its page; the bar has no chips (item 120).
            await expect(page_ui.locator(f"#panel-run-details a[href='#/agents/{agent_id}']")).to_have_text("Scout")
            await expect(page_ui.locator(".stagebar .chip")).to_have_count(0)
            # A look given to the agent (item 102) is worn in the bar, in its row and on its page.
            dressed = await client.put(
                f"/api/agents/{agent_id}/look", json={"shape": "droplet", "expression": "curious", "colour": "violet"}
            )
            assert dressed.status_code == 200, dressed.text
            assert dressed.json()["look"] == {"shape": "droplet", "expression": "curious", "colour": "violet"}
            await page_ui.goto(f"http://{host}:{port}/#/agents")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            worn = page_ui.locator(f".agent-row[data-agent='{agent_id}'] .avatar")
            assert await worn.get_attribute("data-colour") == "violet"
            assert await worn.get_attribute("data-shape") == "droplet"
            assert await worn.get_attribute("data-hue") is None
            await page_ui.goto(f"http://{host}:{port}/#/runs/{run_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            assert await page_ui.locator(".stage-title .avatar[data-colour='violet']").count() == 1
            # The editor's Look field: a moving preview and the shapes, expressions and colours as faces.
            await page_ui.goto(f"http://{host}:{port}/#/agents/{agent_id}")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            await page_ui.locator(".stage-tools").get_by_role("button", name="Edit", exact=True).click()
            await expect(page_ui.locator(".look-picker .look-option")).to_have_count(8 + 16 + 12)
            await expect(
                page_ui.locator(".look-preview .character[data-still='false'][data-shape='droplet']")
            ).to_have_count(1)
            assert await page_ui.locator(".look-picker input[value='violet']").is_checked()
            await page_ui.screenshot(path=str(out / "editor.png"), full_page=True)
            # Dot's own look is a card of Settings › General.
            await page_ui.goto(f"http://{host}:{port}/#/settings/general")
            await page_ui.wait_for_timeout(_SETTLE_MS)
            dot_look = page_ui.locator('section[aria-label="Dot\'s look"]')
            await dot_look.scroll_into_view_if_needed()
            await expect(dot_look.locator(".look-preview .character[data-colour='amber']")).to_have_count(1)
            await dot_look.screenshot(path=str(out / "dot-look.png"))
            await page_ui.screenshot(path=str(out / "run.png"))
            await context.close()
        finally:
            await browser.close()

    gone = await client.delete(f"/api/agents/{agent_id}")
    assert gone.status_code == 204, gone.text
    assert (await client.get(f"/api/runs/{run_id}")).json()["run"]["agent_id"] is None
    assert (await client.get(f"/api/agents/{agent_id}")).status_code == 404
    # Its lesson and its skill stay, as Dot's.
    assert (await runtime.store.get_memory(lesson_id)).agent_id is None
    assert read_meta(playbooks_dir() / SKILL_NAME).agent_id is None

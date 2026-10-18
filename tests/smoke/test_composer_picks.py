"""The composer's picks (item 318). An MCP server picked for a message is on from its run's first step, and without the
pick its group is off (a grouped run, the fixture MCP server); a picked skill and Plan first follow the owner's words in
the run's first message, while the run's goal stays the owner's words; a queued message keeps its picks; picks a run
cannot start with are refused, and nothing starts; Save skill writes the skill of a run that ended done after fewer
acting steps than a skill needs on its own, and refuses a run with none. In the browser, wide and light, and on a phone
and dark: the "+" menu picks plugins, skills and Plan first as chips, the slash menu too, a chip goes with its ×, the
picks go with the message and stand under it, and on a phone the chips wrap inside the box.

No network and no model service is used: the scripted model plays the runs and answers as the review model too; the
homes are throw-away directories.
"""

import asyncio
import re
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, ConsoleMessage, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.agent.picks import PICKS_EVENT
from dotagent.agent.playbooks import playbook_name
from dotagent.agent.review import PLAYBOOK_TOOL
from dotagent.config import AppConfig, McpServerConfig, ToolSurface, load_default_config
from dotagent.core.paths import playbooks_dir
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import NO_PICKS, EventRecord, QueuedMessage, RunPicks, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
SERVER: str = "fixture"
GROUP: str = f"mcp__{SERVER}"
SKILL_NAME: str = "weekly-report"
SKILL_STEPS: str = "MARKER-weekly-report-steps: one line a project, the late ones first."
SKILL: str = (
    f'---\nname: {SKILL_NAME}\ndescription: "Write the weekly report as the owner likes it."\n---\n'
    f"# The weekly report\n\n{SKILL_STEPS}\n"
)
GOAL: str = "Add two and three, then tell me the sum."
KIND: str = "Add two numbers"
# A source of the owner's own, as test_learned_skills uses: an eval's run gives no skill.
SOURCE: str = "test"
# One of Zen's free models, whose run is OpenCode's own agent (item 283); no OpenCode is started, since it is refused.
OPENCODE_MODEL: str = "opencode:big-pickle"
TOKEN: str = "composer-picks-test-token"
WAIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_MAX_EVENTS: int = 10_000
# The browser's window height; the "+" menu must fit in it.
_HEIGHT: int = 900


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


class Played(FrozenModel):
    """A run that is over: its record, the requests of its steps, its tool_groups events and its picks events."""

    record: RunRecord
    steps: tuple[ChatRequest, ...]
    groups: tuple[JsonObject, ...]
    picks: tuple[JsonObject, ...]


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


DONE: ScriptedTurn = _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The sum is 5.", "success": True}))
WRITE: ScriptedTurn = _turn(ScriptedCall(tool="write_file", arguments={"path": "sum.txt", "content": "5\n"}))
# The skill writer's answer, as the review model gives it.
SKILL_WRITTEN: ScriptedTurn = _turn(
    ScriptedCall(
        tool=PLAYBOOK_TOOL,
        arguments={
            "kind": KIND,
            "description": "Add two numbers and keep the sum in a file.",
            "when": "A goal that asks for a sum to keep.",
            "steps": ["Write the sum of <a> and <b> to <file> with write_file."],
            "parameters": ["<a>, <b>: the numbers", "<file>: the file's path"],
        },
    )
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config(surface: ToolSurface) -> AppConfig:
    """An eval run's configuration with its tools offered as ``surface`` says, the fixture MCP server, learned skills on
    (after more acting steps than any run here takes) and the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(
                update={"tool_surface": surface, "playbooks": True, "skill_min_steps": 10}
            ),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            "mcp": [McpServerConfig(name=SERVER, command=sys.executable, args=[str(FIXTURE)])],
        }
    )


def _write_skill(home: Path) -> None:
    folder: Path = home / "skills" / SKILL_NAME
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], config: AppConfig, turns: Sequence[ScriptedTurn]
) -> AsyncIterator[tuple[Runtime, RunManager, RecordingProvider]]:
    """The daemon's runtime and run manager in the home of the environment, whose model plays ``turns``."""
    runtime: Runtime = await open_runtime(config, login_env)
    try:
        model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, list(turns)))
        await runtime.router.register(_PROVIDER, model)
        manager: RunManager = RunManager(runtime)
        try:
            yield runtime, manager, model
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


def _context(runtime: Runtime, manager: RunManager, port: int, stopping: asyncio.Event) -> ServerContext:
    return ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )


@asynccontextmanager
async def _client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
    """The daemon's API, in this process."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(_context(runtime, manager, 8765, asyncio.Event()))),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


@asynccontextmanager
async def _served(runtime: Runtime, manager: RunManager) -> AsyncIterator[str]:
    """The daemon served on a free port of this Mac, at the address it yields."""
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    server: DotServer = DotServer(serving_config(create_app(_context(runtime, manager, port, stopping))), stopping)
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


def _kind(events: Sequence[EventRecord], kind: str) -> tuple[JsonObject, ...]:
    return tuple(event.data for event in events if event.kind == kind)


async def _ended(runtime: Runtime, manager: RunManager, model: RecordingProvider, run_id: str, asked: int) -> Played:
    """The run ``run_id`` once it is over, with the requests its model was sent after the first ``asked``."""
    async with asyncio.timeout(WAIT_S):
        record: RunRecord = await manager.wait(run_id)
    events: list[EventRecord] = await runtime.store.list_events(run_id, 0, _MAX_EVENTS)
    return Played(
        record=record,
        steps=tuple(request for request in model.requests[asked:] if request.session_key == record.thread_id),
        groups=_kind(events, "tool_groups"),
        picks=_kind(events, PICKS_EVENT),
    )


async def _play(
    runtime: Runtime, manager: RunManager, model: RecordingProvider, picks: RunPicks, source: str, approvals: str
) -> Played:
    """``GOAL`` as a task run in a chat of its own, sent with ``picks``, once it is over."""
    asked: int = len(model.requests)
    run: RunRecord = await manager.start_picked(
        GOAL, "task", SCRIPTED_MODEL, None, source, None, None, "standard", None, approvals, False, (), picks
    )
    return await _ended(runtime, manager, model, run.id, asked)


def _named(event: JsonObject, key: str) -> list[str]:
    value: object = event[key]
    assert isinstance(value, list), event
    return [str(name) for name in value]


def _opening(played: Played) -> str:
    """The owner's message as the run's first request gave it to the model."""
    content: object = next(message for message in played.steps[0].messages if message.role == "user").content
    assert isinstance(content, str), content
    return content


async def test_a_picked_server_is_on_from_the_first_step_and_without_the_pick_its_group_is_off(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, _config("grouped"), [DONE, DONE]) as (runtime, manager, model):
            picked: Played = await _play(
                runtime, manager, model, RunPicks(plugins=(SERVER,), skills=(), plan_first=False), EVAL_SOURCE, "ask"
            )
            plain: Played = await _play(runtime, manager, model, NO_PICKS, EVAL_SOURCE, "ask")
    assert (picked.record.status, plain.record.status) == ("done", "done")
    # The server's group is the run's either way: with the pick it is on from step 0, without it off.
    assert GROUP in _named(picked.groups[0], "on"), picked.groups[0]
    assert GROUP in _named(plain.groups[0], "off"), plain.groups[0]
    assert f"{GROUP}__add" in [spec.name for spec in picked.steps[0].tools]
    assert not [spec.name for spec in plain.steps[0].tools if spec.name.startswith(f"{GROUP}__")]
    # The first message is the owner's words, then the pick; the run's goal stays the owner's words.
    assert _opening(picked) == (
        f"{GOAL}\n\n[Picked in the composer] The owner picked the MCP servers {SERVER} for this message: use their "
        f"tools ({GROUP}__…) for it where they fit."
    )
    assert _opening(plain) == GOAL
    assert picked.record.goal == GOAL
    assert list(picked.picks) == [{"plugins": [SERVER], "skills": [], "plan_first": False}]
    assert plain.picks == ()


async def test_a_picked_skill_and_plan_first_follow_the_owners_words_in_the_first_message(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        _write_skill(home)
        async with _daemon(login_env, _config("full"), [DONE]) as (runtime, manager, model):
            played: Played = await _play(
                runtime, manager, model, RunPicks(plugins=(), skills=(SKILL_NAME,), plan_first=True), UI_SOURCE, "ask"
            )
    assert played.record.status == "done"
    assert played.record.goal == GOAL
    words, plan, skills, skill = _opening(played).split("\n\n", 3)
    assert words == GOAL
    assert plan == (
        "[Picked in the composer] Plan first: before you change anything, write your plan with update_plan, then ask "
        "the owner with ask_user whether to go ahead, and act only once they agree."
    )
    assert skills == "[Picked in the composer] The owner picked the skills below for this message."
    # The skill as the skill tool gives it: what it is and where, then its instructions inside their fence.
    assert skill.startswith(f"Skill {SKILL_NAME} (home), folder "), skill
    assert f'name="{SKILL_NAME}">' in skill and SKILL_STEPS in skill, skill
    assert list(played.picks) == [{"plugins": [], "skills": [SKILL_NAME], "plan_first": True}]


async def _following(runtime: Runtime, run: RunRecord) -> RunRecord:
    """The run that followed ``run`` in its chat, once there is one."""
    deadline: float = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        newest: list[RunRecord] = await runtime.store.list_runs(1, run.thread_id)
        if newest and newest[0].id != run.id:
            return newest[0]
        await asyncio.sleep(0.05)
    raise AssertionError(f"no run followed {run.id} in {WAIT_S} s")


async def test_a_queued_message_keeps_its_picks(login_env: dict[str, str]) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, _config("grouped"), [DONE, DONE]) as (runtime, manager, model):
            first: Played = await _play(runtime, manager, model, NO_PICKS, EVAL_SOURCE, "ask")
            asked: int = len(model.requests)
            await manager.queue(
                first.record.thread_id,
                GOAL,
                (),
                "task",
                SCRIPTED_MODEL,
                None,
                "standard",
                None,
                RunPicks(plugins=(SERVER,), skills=(), plan_first=False),
                "ask",
                False,
                None,
                EVAL_SOURCE,
            )
            # With no run at work in the chat, the message starts at once as its next run, with the server on.
            following: RunRecord = await _following(runtime, first.record)
            played: Played = await _ended(runtime, manager, model, following.id, asked)
    assert GROUP in _named(first.groups[0], "off"), first.groups[0]
    assert played.record.status == "done"
    assert GROUP in _named(played.groups[0], "on"), played.groups[0]
    assert list(played.picks) == [{"plugins": [SERVER], "skills": [], "plan_first": False}]


async def test_picks_a_run_cannot_start_with_are_refused_and_nothing_starts(login_env: dict[str, str]) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _daemon(login_env, _config("full"), [DONE]) as (runtime, manager, model),
            _client(runtime, manager) as client,
        ):
            bounded: httpx.Response = await client.post(
                "/api/agents",
                json={
                    "name": "Note keeper",
                    "role": "Keeps notes",
                    "instructions": "",
                    "model": SCRIPTED_MODEL,
                    "effort": None,
                    "approvals": None,
                    "tools": ["memory_save"],
                },
            )
            assert bounded.status_code == 201, bounded.text
            refused: dict[str, tuple[JsonObject, str]] = {
                "a server that is not connected": (
                    {"plugins": ["nowhere"]},
                    "the MCP server nowhere is not connected, or none of its tools is open to this run (the servers "
                    f"it can use: {SERVER})",
                ),
                "a skill not offered": ({"skills": ["nope"]}, "no skill offered to this run is called nope"),
                "Plan first in yolo": (
                    {"plan_first": True, "approvals": "yolo"},
                    "Plan first asks the owner before the run acts, and this run cannot ask (a yolo run is not asked)",
                ),
                "Plan first in a chat": (
                    {"plan_first": True, "mode": "chat"},
                    "Plan first is for task and research runs: a chat changes nothing",
                ),
                "a server outside an agent's bounds": (
                    {"plugins": [SERVER], "agent_id": bounded.json()["id"]},
                    f"the MCP server {SERVER} is not connected, or none of its tools is open to this run (the servers "
                    "it can use: none)",
                ),
                "picks with Keep going": (
                    {"plugins": [SERVER], "standing": True},
                    "plugins, skills and Plan first go with a message, not with a campaign (standing)",
                ),
                "picks on one of OpenCode's models": (
                    {"plugins": [SERVER], "model": OPENCODE_MODEL},
                    f"{OPENCODE_MODEL} works as OpenCode's own agent, which takes no plugins, skills or Plan first",
                ),
            }
            for case, (fields, says) in refused.items():
                answer: httpx.Response = await client.post(
                    "/api/runs", json={"goal": GOAL, "model": SCRIPTED_MODEL, **fields}
                )
                assert answer.status_code == 422, (case, answer.text)
                assert says in answer.text, (case, answer.text)
            assert await runtime.store.list_runs(10, None) == []
            # Picks the run can start with start it.
            started: httpx.Response = await client.post(
                "/api/runs", json={"goal": GOAL, "model": SCRIPTED_MODEL, "plugins": [SERVER]}
            )
            assert started.status_code == 201, started.text
            played: Played = await _ended(runtime, manager, model, started.json()["id"], 0)
            # A queued message with picks is not steered into a run at work, which could not take them: it stays.
            queued: QueuedMessage = await runtime.store.add_queued(
                played.record.thread_id,
                GOAL,
                (),
                "task",
                None,
                None,
                "standard",
                None,
                RunPicks(plugins=(SERVER,), skills=(), plan_first=False),
                None,
                None,
                None,
                SOURCE,
            )
            steered: httpx.Response = await client.post(f"/api/queue/{queued.id}/steer")
            left: list[QueuedMessage] = await runtime.store.list_queued(played.record.thread_id)
    assert played.record.status == "done"
    assert list(played.picks) == [{"plugins": [SERVER], "skills": [], "plan_first": False}]
    assert steered.status_code == 422, steered.text
    assert "starts a run of its own, since the run at work cannot take them" in steered.text, steered.text
    assert [message.id for message in left] == [queued.id]


async def test_save_skill_writes_the_skill_of_a_run_that_ended_done_and_refuses_one_with_no_acting_step(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _daemon(login_env, _config("full"), [WRITE, DONE, SKILL_WRITTEN, DONE]) as (runtime, manager, model),
            _client(runtime, manager) as client,
        ):
            # One acting step, fewer than a skill needs on its own (skill_min_steps): only the owner's word saves it.
            acted: Played = await _play(runtime, manager, model, NO_PICKS, SOURCE, "ask")
            await manager.distilled(acted.record.id)
            assert not playbooks_dir().exists() or not any(playbooks_dir().iterdir())
            saved: httpx.Response = await client.post(f"/api/runs/{acted.record.id}/skill")
            assert saved.status_code == 201, saved.text
            assert saved.json() == {"name": playbook_name(KIND), "updated": False}
            assert (playbooks_dir() / playbook_name(KIND) / "SKILL.md").is_file()
            idle: Played = await _play(runtime, manager, model, NO_PICKS, SOURCE, "ask")
            await manager.distilled(idle.record.id)
            refused: httpx.Response = await client.post(f"/api/runs/{idle.record.id}/skill")
            assert refused.status_code == 422, refused.text
            assert "it took no acting step, so it did nothing a skill could repeat" in refused.text
    assert (acted.record.status, idle.record.status) == ("done", "done")


def _collect_refusals(refused: list[str]) -> Callable[[ConsoleMessage], None]:
    """A console listener that keeps what the page's Content-Security-Policy refused, and the page's errors."""

    def listen(message: ConsoleMessage) -> None:
        if "Content Security Policy" in message.text or message.type == "error":
            refused.append(message.text)

    return listen


async def _home_page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": _HEIGHT}, color_scheme=scheme, reduced_motion="reduce"
    )
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/runs")
    return page


async def _fits(page: Page, inner: Locator, outer: Locator) -> None:
    """Each of ``inner`` lies within ``outer``, and the page does not scroll sideways."""
    box: dict[str, float] | None = await outer.bounding_box()
    assert box is not None
    for at in range(await inner.count()):
        part: dict[str, float] | None = await inner.nth(at).bounding_box()
        assert part is not None
        assert box["x"] <= part["x"] and part["x"] + part["width"] <= box["x"] + box["width"] + 0.5, (part, box)
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.browser
async def test_the_plus_menu_picks_plugins_skills_and_plan_first_as_chips_that_go_with_the_message(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    passes: tuple[tuple[int, Literal["light", "dark"], tuple[str, ...]], ...] = (
        (1280, "light", (SKILL_NAME,)),
        (1280, "dark", (SKILL_NAME,)),
        (390, "light", (SKILL_NAME, "excel", "onedrive", "word")),
        (390, "dark", (SKILL_NAME, "excel", "onedrive", "word")),
    )
    with scratch_home() as home, eval_environment(home, {}):
        _write_skill(home)
        async with (
            _daemon(login_env, _config("full"), [DONE] * len(passes)) as (runtime, manager, model),
            _served(runtime, manager) as address,
            async_playwright() as playwright,
        ):
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme, skills in passes:
                    refused: list[str] = []
                    page: Page = await _home_page(browser, address, width, scheme)
                    page.on("console", _collect_refusals(refused))
                    composer: Locator = page.locator(".composer")
                    goal: Locator = page.locator("textarea[aria-label='Goal']")
                    chips: Locator = composer.locator(".pick-list .pick-chip")
                    await expect(chips).to_have_count(0)
                    # The "+" opens Add files, goal mode, Plan first, then the servers and the skills to pick.
                    await composer.get_by_role("button", name="Add", exact=True).click()
                    menu: Locator = composer.locator(".composer-popover[aria-label='Add']")
                    await expect(menu.get_by_role("button", name=re.compile("^Add files"))).to_be_visible()
                    await expect(menu.get_by_role("switch", name="Goal mode")).to_be_visible()
                    plugin: Locator = (
                        menu.get_by_role("listbox", name="Plugins").get_by_role("option").filter(has_text=SERVER)
                    )
                    await expect(plugin).to_contain_text("10 tools")
                    await plugin.click()
                    await expect(plugin).to_have_attribute("aria-selected", "true")
                    await menu.get_by_role("switch", name="Plan first").click()
                    await expect(menu.get_by_role("switch", name="Plan first")).to_have_attribute(
                        "aria-checked", "true"
                    )
                    for name in skills:
                        await (
                            menu.get_by_role("listbox", name="Skills")
                            .get_by_role("option")
                            .filter(has_text=re.compile(f"^{name}"))
                            .click()
                        )
                    await expect(chips).to_have_count(2 + len(skills))
                    await expect(composer.locator(".pick-chip[data-kind='plugin']")).to_have_attribute(
                        "data-name", SERVER
                    )
                    menu_box: dict[str, float] | None = await menu.bounding_box()
                    assert menu_box is not None
                    assert menu_box["x"] >= 0 and menu_box["x"] + menu_box["width"] <= width, menu_box
                    assert menu_box["y"] >= 0 and menu_box["y"] + menu_box["height"] <= _HEIGHT, menu_box
                    await page.screenshot(path=str(tmp_path / f"plus-menu-{width}-{scheme}.png"), animations="disabled")
                    await page.keyboard.press("Escape")
                    await expect(menu).to_be_hidden()
                    # A chip goes with its ×; the slash menu picks a skill as the same chip, and nothing is typed.
                    await composer.get_by_role("button", name=f"Remove {SKILL_NAME}").click()
                    await expect(chips).to_have_count(1 + len(skills))
                    await goal.click()
                    await goal.type("/weekly")
                    await expect(composer.locator(".slash-menu [role=option]")).to_have_count(1)
                    await goal.press("Enter")
                    await expect(goal).to_have_value("")
                    await expect(chips).to_have_count(2 + len(skills))
                    await _fits(page, chips, composer)
                    await page.screenshot(path=str(tmp_path / f"chips-{width}-{scheme}.png"), animations="disabled")
                    # The picks go with the message and stand under it; the composer they left holds none.
                    await goal.fill(GOAL)
                    await goal.press("Enter")
                    await page.wait_for_url(re.compile(r"#/runs/[^/]+$"))
                    run_id: str = page.url.rpartition("/")[2]
                    sent: Locator = page.locator(".user-message .pick-list.sent .pick-chip")
                    await expect(sent).to_have_count(2 + len(skills))
                    await expect(sent.first).to_have_attribute("data-kind", "plan")
                    await _fits(page, sent, page.locator(".user-message").first)
                    await page.screenshot(path=str(tmp_path / f"sent-{width}-{scheme}.png"), animations="disabled")
                    assert (await _ended(runtime, manager, model, run_id, 0)).record.status == "done"
                    events: list[EventRecord] = await runtime.store.list_events(run_id, 0, _MAX_EVENTS)
                    assert list(_kind(events, PICKS_EVENT)) == [
                        {"plugins": [SERVER], "skills": [*skills[1:], SKILL_NAME], "plan_first": True}
                    ]
                    # The chips come back with the page, from the run's events.
                    await page.reload()
                    await expect(sent).to_have_count(2 + len(skills))
                    await expect(sent.first).to_have_attribute("data-kind", "plan")
                    await page.goto(f"{address}/#/runs")
                    await expect(page.locator("textarea[aria-label='Goal']")).to_have_value("")
                    await expect(page.locator(".composer .pick-list .pick-chip")).to_have_count(0)
                    assert refused == [], refused
                    await page.close()
            finally:
                await browser.close()


async def test_an_at_word_picks_a_server_or_a_skill_by_its_name(login_env: dict[str, str], tmp_path: Path) -> None:
    """`@` at the start of a word opens the menu of the picks alone (item 367): the MCP servers by name with the number
    of their tools, then the skills and the servers' tools, filtered by the letters after it; Enter makes the chip and
    takes the at word away, and an `@` inside a word opens nothing. Wide and light, and on a phone and dark."""
    with scratch_home() as home, eval_environment(home, {}):
        _write_skill(home)
        async with (
            _daemon(login_env, _config("full"), []) as (runtime, manager, _model),
            _served(runtime, manager) as address,
            async_playwright() as playwright,
        ):
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                passes: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
                for width, scheme in passes:
                    refused: list[str] = []
                    page: Page = await _home_page(browser, address, width, scheme)
                    page.on("console", _collect_refusals(refused))
                    composer: Locator = page.locator(".composer")
                    goal: Locator = page.locator("textarea[aria-label='Goal']")
                    chips: Locator = composer.locator(".pick-list .pick-chip")
                    menu: Locator = composer.locator(".slash-menu")
                    await goal.click()
                    await goal.type("Look it up @")
                    await expect(menu).to_be_visible()
                    await expect(menu.locator(".slash-group").first).to_have_text("MCP servers")
                    server: Locator = menu.get_by_role("option").filter(has_text=SERVER).first
                    await expect(server.locator(".slash-hint")).to_have_text("10 tools")
                    await expect(menu.locator(".slash-group", has_text="Skills")).to_have_count(1)
                    await expect(menu.locator(".slash-group", has_text="Commands")).to_have_count(0)
                    box: dict[str, float] | None = await menu.bounding_box()
                    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width, box
                    await page.screenshot(path=str(tmp_path / f"at-menu-{width}-{scheme}.png"), animations="disabled")
                    await goal.type(SERVER[:3])
                    await expect(menu.get_by_role("option").first).to_contain_text(SERVER)
                    await goal.press("Enter")
                    await expect(menu).to_be_hidden()
                    await expect(goal).to_have_value("Look it up ")
                    await expect(composer.locator(".pick-chip[data-kind='plugin']")).to_have_attribute(
                        "data-name", SERVER
                    )
                    await goal.type(f"@{SKILL_NAME[:6]}")
                    await expect(menu.get_by_role("option")).to_have_count(1)
                    await goal.press("Enter")
                    await expect(goal).to_have_value("Look it up ")
                    await expect(chips).to_have_count(2)
                    # An at inside a word, as in an address, is part of the words.
                    await goal.type("owner@example.org")
                    await expect(menu).to_be_hidden()
                    await expect(goal).to_have_value("Look it up owner@example.org")
                    await _fits(page, chips, composer)
                    await page.screenshot(path=str(tmp_path / f"at-chips-{width}-{scheme}.png"), animations="disabled")
                    assert refused == [], refused
                    await page.close()
            finally:
                await browser.close()

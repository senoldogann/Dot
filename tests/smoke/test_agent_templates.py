"""Templates and agent files (item 315, after Grok's): each of the templates becomes an agent in one click, with
the template's role, instructions, tools and face; an agent's page saves it as a file that holds its profile and
nothing else, and refuses to while its words hold what looks like a key; Import in another home makes the same agent,
and refuses a file that names tools that home lacks, saying which. Two real daemons, one home after the other, in
Chrome, wide and on a phone."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Download, Locator, Page, async_playwright, expect

from dotagent.agent import templates as gallery
from dotagent.agent.manager import RunManager
from dotagent.agent.templates import TEAMS, TEMPLATES, AgentTeam
from dotagent.config import load_default_config
from dotagent.core.paths import env_file_path
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import AgentRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "MARKER-agent-templates-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Secrets no file of an agent may carry: a key known by its form, and one this home holds in its .env, whatever its
# form.
FORMED_KEY: str = "MARKER- sk-0123456789abcdefghij"
HELD_SECRET: str = "MARKER-held-by-this-home-0123"
# What makes an agent the same agent in another home.
PROFILE: tuple[str, ...] = ("name", "role", "instructions", "model", "effort", "approvals", "tools", "look")
STRANGE_TOOL: str = "made_up_tool"
# The tools that would reach Mail, Messages or a terminal on their own.
DRIVERS: frozenset[str] = frozenset({"shell", "terminal_open", "terminal_send", "apple_script", "desktop_act"})
_AGENT_URL: re.Pattern[str] = re.compile(r"#/agents/[^/]+$")
# The titles of the Import and Export buttons, their names on a phone, where their labels hide.
IMPORT_TITLE: str = "Make an agent from a file exported from Dot"
EXPORT_TITLE: str = "Save the agent as a file, to import in another home"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str], *, desktop: bool = True) -> AsyncIterator[tuple[Runtime, str]]:
    """The daemon in the home of the environment, served at the address it yields. ``desktop`` says whether the Mac's
    own tools are registered, which ``[desktop] enabled`` in the configuration turns off; the eval configuration leaves
    them off (425)."""
    config = eval_config(load_default_config())
    config = config.model_copy(update={"desktop": config.desktop.model_copy(update={"enabled": desktop})})
    runtime: Runtime = await open_runtime(config, login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
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
            yield runtime, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _open(browser: Browser, address: str, route: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#{route}")
    return page


async def _singles(page: Page) -> None:
    """Show the single agents of the ready-made ones, which the page shows after its teams (item 509)."""
    await (
        page.get_by_role("group", name="Ready-made agents")
        .get_by_role("button", name=re.compile("^Single agents"))
        .click()
    )


def _hold_secret() -> None:
    """Give the home of the environment a secret in its .env, as an API key the owner saved."""
    env_file_path().write_text(f"DOT_TEST_SECRET={HELD_SECRET}\n", encoding="utf-8")


def _profile(record: AgentRecord) -> JsonObject:
    dumped: JsonObject = record.model_dump(mode="json")
    return {field: dumped[field] for field in PROFILE}


async def _import(page: Page, file: Path) -> None:
    """Import ``file`` with the Agents page's Import button."""
    async with page.expect_file_chooser() as choosing:
        await page.get_by_title(IMPORT_TITLE).click()
    await (await choosing.value).set_files(file)


def _toast(page: Page, kind: Literal["ok", "error"], words: str) -> Locator:
    """The toast of ``kind`` that says ``words``, among the ones still shown."""
    return page.locator(f".toast.{kind}", has_text=words)


async def _overflow(page: Page) -> int:
    """How far the page reaches past the window's right edge, in pixels."""
    reach: int = await page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    return reach


@pytest.mark.browser
async def test_a_home_that_cannot_list_its_tools_says_so_on_the_cards_and_marks_nothing_it_does_not_know(
    login_env: dict[str, str],
) -> None:
    """When ``GET /api/tools`` fails the cards cannot tell which tools this home lacks. They say that, above the
    templates, instead of listing every name as if it were available (item 425)."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, desktop=False) as (runtime, address), async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.route("**/api/tools", lambda route: route.fulfill(status=500, body="no"))
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/agents")
                await expect(page.locator(".template-card")).to_have_count(len(TEMPLATES))
                await _singles(page)
                notice = page.locator("[data-templates-tools-unknown]")
                await expect(notice).to_contain_text("could not be listed")
                # No card claims a tool is missing, and the notice is the one place that says why.
                chief = page.locator('.template-card[data-template="chief-of-staff"]')
                await chief.locator(".template-tools > summary").click()
                await expect(chief.locator(".template-tool-chips .chip.missing")).to_have_count(0)
                await page.close()
            finally:
                await browser.close()


async def test_each_template_becomes_an_agent_in_one_click_and_its_file_makes_the_same_agent_in_another_home(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    exported: Path = tmp_path / "exported.dot-agent.json"
    with scratch_home() as home, eval_environment(home, {}):
        await asyncio.to_thread(_hold_secret)
        async with _daemon(login_env) as (runtime, address), async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _open(browser, address, "/agents", 1280, "light")
                cards: Locator = page.locator(".template-card")
                await expect(cards).to_have_count(len(TEMPLATES))
                await _singles(page)
                await expect(cards.locator(".template-name")).to_have_text([template.name for template in TEMPLATES])
                await expect(cards.locator(".template-role")).to_have_text([template.role for template in TEMPLATES])
                await expect(cards.locator(".avatar")).to_have_count(len(TEMPLATES))
                # Every card names the tools its agent may use (item 425): the count on the card, the names a click
                # below it, so that no click makes an agent with every tool by surprise.
                await expect(cards.locator(".template-tools > summary")).to_have_text(
                    [f"Tools ({len(template.tools)})" for template in TEMPLATES]
                )
                first: Locator = cards.first
                await first.locator(".template-tools > summary").click()
                await expect(first.locator(".template-tool-chips code")).to_have_text(list(TEMPLATES[0].tools))
                # Every tool the fourteen name is a tool this home has, so no chip is marked missing.
                await expect(cards.locator(".template-tool-chips .chip.missing")).to_have_count(0)
                await page.screenshot(path=tmp_path / "templates-1280-light.png", full_page=True)
                phone: Page = await _open(browser, address, "/agents", 390, "dark")
                await expect(phone.locator(".template-card")).to_have_count(len(TEMPLATES))
                await _singles(phone)
                # One card a row on a phone, inside the window.
                lefts: set[float] = set()
                for index in range(len(TEMPLATES)):
                    box = await phone.locator(".template-card").nth(index).bounding_box()
                    assert box is not None
                    lefts.add(box["x"])
                    assert box["x"] + box["width"] <= 390
                assert len(lefts) == 1
                assert await _overflow(phone) <= 0
                await phone.screenshot(path=tmp_path / "templates-390-dark.png", full_page=True)
                await phone.close()

                # Each template becomes an agent with one click, which opens the agent's page.
                made: dict[str, str] = {}
                for template in TEMPLATES:
                    await page.goto(f"{address}/#/agents")
                    await page.get_by_role("button", name=f"Use the template {template.name}").click()
                    await expect(page).to_have_url(_AGENT_URL)
                    await expect(page.locator(".agent-who .home-name")).to_have_text(template.name)
                    made[template.id] = page.url.rpartition("/")[2]
                agents: list[AgentRecord] = list(await runtime.store.list_agents())
                assert len(agents) == len(TEMPLATES)
                for template in TEMPLATES:
                    record: AgentRecord = await runtime.store.get_agent(made[template.id])
                    assert _profile(record) == {
                        "name": template.name,
                        "role": template.role,
                        "instructions": template.instructions,
                        "model": "",
                        "effort": None,
                        "approvals": None,
                        "tools": list(template.tools),
                        "look": template.look.model_dump(mode="json"),
                    }
                # Every template names the tools its agent may use (item 425), and drafts what it writes to a person
                # (item 314): none starts with every tool, and one that works on the web and in files alone names no
                # tool that drives Mail, Messages or a terminal.
                assert all(template.tools for template in TEMPLATES), "a template names its tools"
                assert all("draft_message" in template.instructions for template in TEMPLATES)
                assert all("draft_message" in template.tools for template in TEMPLATES)
                assert [template.id for template in TEMPLATES if DRIVERS & set(template.tools)] == [
                    "expense-manager",
                    "bug-reproduction",
                    "account-health",
                    "chief-of-staff",
                    "reviewer",
                ]
                outbound: AgentRecord = await runtime.store.get_agent(made["sales-outbound"])

                # The owner sets its bounds, then saves it as a file from its page.
                async with httpx.AsyncClient(
                    base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=WAIT_S
                ) as client:
                    changed: httpx.Response = await client.put(
                        f"/api/agents/{outbound.id}",
                        json={**_profile(outbound), "model": SCRIPTED_MODEL, "effort": "high", "approvals": "ask"},
                    )
                    assert changed.status_code == 200, changed.text
                    # Every template's agent goes out as a file.
                    for template in TEMPLATES:
                        went: httpx.Response = await client.get(f"/api/agents/{made[template.id]}/export")
                        assert went.status_code == 200, (template.id, went.text)
                    # One whose instructions hold a secret does not, and the refusal does not repeat it.
                    secret_agents: dict[str, str] = {}
                    for name, secret in (("Keyed", FORMED_KEY), ("Held", HELD_SECRET)):
                        made_one: httpx.Response = await client.post(
                            "/api/agents",
                            json={
                                "name": name,
                                "role": "",
                                "instructions": f"Use the key {secret} for the CRM.",
                                "model": "",
                                "effort": None,
                                "approvals": None,
                            },
                        )
                        assert made_one.status_code == 201, made_one.text
                        secret_agents[name] = made_one.json()["id"]
                        refused: httpx.Response = await client.get(f"/api/agents/{secret_agents[name]}/export")
                        assert refused.status_code == 422, name
                        assert "looks like a secret" in refused.text and secret not in refused.text, name
                    assert (await client.get("/api/agents/dot/export")).status_code == 422
                outbound = await runtime.store.get_agent(outbound.id)
                await page.goto(f"{address}/#/agents/{outbound.id}")
                await expect(page.locator(".agent-who .home-name")).to_have_text("Sales Outbound")
                async with page.expect_download() as downloading:
                    await page.get_by_title(EXPORT_TITLE).click()
                download: Download = await downloading.value
                assert download.suggested_filename == "sales-outbound.dot-agent.json"
                await download.save_as(exported)
                await expect(
                    _toast(page, "ok", "Sales Outbound is saved as sales-outbound.dot-agent.json.")
                ).to_be_visible()
                text: str = await asyncio.to_thread(exported.read_text, encoding="utf-8")
                file: JsonObject = json.loads(text)
                # The file holds the agent's profile and nothing of this home: no id, chats, memory, or secret.
                assert set(file) == {"format", "version", *PROFILE}
                assert (file["format"], file["version"]) == ("dot-agent", 1)
                assert {field: file[field] for field in PROFILE} == _profile(outbound)
                assert outbound.id not in text and TOKEN not in text and HELD_SECRET not in text

                # An agent whose words hold what looks like a secret is not saved as a file: the page says why.
                downloads: list[Download] = []

                def kept(download: Download) -> None:
                    downloads.append(download)

                page.on("download", kept)
                await page.goto(f"{address}/#/agents/{secret_agents['Keyed']}")
                await expect(page.locator(".agent-who .home-name")).to_have_text("Keyed")
                await page.get_by_title(EXPORT_TITLE).click()
                await expect(_toast(page, "error", "looks like a secret; take it out")).to_be_visible()
                await page.wait_for_timeout(500)
                assert downloads == []
                await page.screenshot(path=tmp_path / "export-refused-1280-light.png")
            finally:
                await browser.close()
    profile: JsonObject = {field: file[field] for field in PROFILE}

    # A copy that names a tool no home has, and a file that is not an agent's.
    stranger: Path = tmp_path / "stranger.dot-agent.json"
    tools: list[str] = [*outbound.tools, STRANGE_TOOL]
    await asyncio.to_thread(stranger.write_text, json.dumps({**file, "tools": tools}), encoding="utf-8")
    broken: Path = tmp_path / "broken.json"
    await asyncio.to_thread(broken.write_text, "not an agent", encoding="utf-8")

    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, desktop=False) as (runtime, address), async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                phone = await _open(browser, address, "/agents", 390, "dark")
                await expect(phone.locator(".template-card")).to_have_count(len(TEMPLATES))
                await _singles(phone)
                # A tool this home does not have is marked on the card before the click that would be refused (item
                # 425): here the Mac's own tools are off, so the AppleScript three templates name is struck through.
                chief: Locator = phone.locator('.template-card[data-template="chief-of-staff"]')
                await chief.locator(".template-tools > summary").click()
                await expect(chief.locator(".template-tool-chips .chip.missing")).to_have_text(["apple_script"])
                await expect(phone.locator(".template-card").first.locator(".chip.missing")).to_have_count(0)
                await _import(phone, broken)
                await expect(_toast(phone, "error", "broken.json is not an agent file")).to_be_visible()
                await _import(phone, stranger)
                await expect(
                    _toast(phone, "error", f"an agent names tools that do not exist here: {STRANGE_TOOL}")
                ).to_be_visible()
                assert list(await runtime.store.list_agents()) == []
                # The file from the other home makes the same agent here, and opens its page.
                await _import(phone, exported)
                await expect(phone).to_have_url(_AGENT_URL)
                await expect(phone.locator(".agent-who .home-name")).to_have_text("Sales Outbound")
                await expect(_toast(phone, "ok", "Sales Outbound is imported.")).to_be_visible()
                (imported,) = await runtime.store.list_agents()
                assert _profile(imported) == profile
                assert imported.id == phone.url.rpartition("/")[2]
                assert imported.made_by is None
                # The bar of the agent's page keeps Edit, Export and Delete inside a phone's width.
                tools_bar: Locator = phone.locator(".stage-tools")
                await expect(tools_bar.get_by_title(EXPORT_TITLE)).to_be_visible()
                bar = await tools_bar.bounding_box()
                assert bar is not None and bar["x"] + bar["width"] <= 390
                assert await _overflow(phone) <= 0
                await phone.screenshot(path=tmp_path / "imported-390-dark.png")
            finally:
                await browser.close()


@pytest.mark.browser
async def test_a_template_schedule_is_added_switched_off_and_read_and_switched_on_on_the_agents_page(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Item 464: a template that suggests a schedule offers to add it with the agent, and one that suggests none does
    not; ticked, the agent is made and its schedule added switched off, as the agent's, with the template's time and
    goal, and the agent's page lists it with its goal in full and its switch, which switches it on. Wide and light,
    and on a phone and dark."""
    template = next(one for one in TEMPLATES if one.id == "chief-of-staff")
    plain = next(one for one in TEMPLATES if one.cron == "")
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _daemon(login_env) as (runtime, address),
            httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http,
            async_playwright() as playwright,
        ):
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in ((1280, "light"), (390, "dark")):
                    page: Page = await _open(browser, address, "/agents", width, scheme)
                    await _singles(page)
                    card: Locator = page.locator(f".template-card[data-template='{template.id}']")
                    await expect(card).to_be_visible()
                    no_offer: Locator = page.locator(f".template-card[data-template='{plain.id}'] .template-also")
                    await expect(no_offer).to_have_count(0)
                    also: Locator = card.get_by_role("switch", name="Also add this schedule")
                    await also.click()
                    await expect(also).to_have_attribute("aria-checked", "true")
                    await card.screenshot(path=tmp_path / f"template-card-{width}-{scheme}.png")
                    await card.get_by_role("button", name=f"Use the template {template.name}").click()
                    await expect(page).to_have_url(_AGENT_URL)
                    agent_id: str = page.url.rpartition("/")[2]
                    row: Locator = page.locator("[data-section='schedules'] .list-row")
                    await expect(row).to_have_count(1)
                    await expect(row.locator(".agent-schedule-goal")).to_have_text(template.task)
                    switch: Locator = row.get_by_role("switch")
                    await expect(switch).to_have_attribute("aria-checked", "false")
                    await expect(row.locator(".badge")).to_have_text("switched off")
                    views: list[JsonObject] = (await http.get("/api/schedules")).raise_for_status().json()
                    kept: list[tuple[object, ...]] = [
                        (record["name"], record["cron"], record["goal"], record["enabled"], record["next_run_at"])
                        for record in (view["record"] for view in views)
                        if record["options"]["agent_id"] == agent_id
                    ]
                    assert kept == [(template.name, template.cron, template.task, False, None)], kept
                    assert await _overflow(page) <= 0
                    await page.screenshot(path=tmp_path / f"agent-schedule-{width}-{scheme}.png", full_page=True)
                    # Read, it is switched on from the agent's page, and waits for its next firing.
                    await switch.click()
                    await expect(switch).to_have_attribute("aria-checked", "true")
                    await expect(row.locator(".row-meta")).to_contain_text("Next ")
                    await page.close()
            finally:
                await browser.close()


def _names(team: AgentTeam) -> list[str]:
    return [next(template.name for template in TEMPLATES if template.id == member) for member in team.members]


async def test_a_team_makes_the_agents_the_owner_lacks_and_keeps_those_the_owner_has(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 504: Use team makes the agents of the team's templates, each as its template's Use makes one; an agent of
    the owner's with a member's name, in another case, is kept as it is; used again, the team makes none; a team that
    is not there is a 404; and a team one of whose agents this home cannot make makes none of them."""
    team: AgentTeam = next(each for each in TEAMS if each.id == "engineering")
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _daemon(login_env) as (_runtime, address),
            httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http,
        ):
            listed: list[JsonObject] = (await http.get("/api/agent-teams")).raise_for_status().json()
            assert [each["id"] for each in listed] == [each.id for each in TEAMS]
            mine: JsonObject = (
                (
                    await http.post(
                        "/api/agents",
                        json={
                            "name": "REVIEWER",
                            "role": "Mine",
                            "instructions": "",
                            "model": "",
                            "effort": None,
                            "approvals": None,
                        },
                    )
                )
                .raise_for_status()
                .json()
            )
            used = await http.post(f"/api/agent-teams/{team.id}/use")
            assert used.status_code == 201, used.text
            body: JsonObject = used.json()
            made: list[JsonObject] = body["made"]
            assert [record["name"] for record in made] == [name for name in _names(team) if name != "Reviewer"]
            assert [record["id"] for record in body["kept"]] == [mine["id"]]
            for record in made:
                template = next(each for each in TEMPLATES if each.name == record["name"])
                assert (record["role"], record["instructions"], tuple(record["tools"])) == (
                    template.role,
                    template.instructions,
                    template.tools,
                )
                assert record["look"] == template.look.model_dump(mode="json")
            again: JsonObject = (await http.post(f"/api/agent-teams/{team.id}/use")).raise_for_status().json()
            assert again["made"] == []
            assert [record["id"] for record in again["kept"]] == [made[0]["id"], mine["id"], made[1]["id"]]
            assert (await http.post("/api/agent-teams/no-such-team/use")).status_code == 404
            # A team whose second agent names a tool this home lacks makes none, not its first.
            strange = TEMPLATES[0].model_copy(
                update={"id": "strange", "name": "Strange", "tools": (*TEMPLATES[0].tools, STRANGE_TOOL)}
            )
            monkeypatch.setattr(gallery, "TEMPLATES", (*TEMPLATES, strange))
            monkeypatch.setattr(
                gallery,
                "TEAMS",
                (*TEAMS, AgentTeam(id="strange", name="Strange team", purpose="-", members=("copy-editor", "strange"))),
            )
            refused = await http.post("/api/agent-teams/strange/use")
            assert refused.status_code == 422, refused.text
            assert STRANGE_TOOL in refused.text
            names: list[str] = [each["record"]["name"] for each in (await http.get("/api/agents")).json()]
            assert "Copy Editor" not in names and "Strange" not in names


@pytest.mark.browser
async def test_a_team_card_makes_its_agents_in_one_click_with_their_schedules_switched_off(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Item 504: the Agents page offers the teams above the templates, each card with its name, what it does and the
    faces and names of its agents; Use team, its schedules asked for, makes the agents and adds the schedules of those
    that suggest one, switched off, and the toast names them; used again, it says the owner has them already. Wide and
    light, and on a phone and dark, inside the window."""
    team: AgentTeam = next(each for each in TEAMS if each.id == "growth")
    names: list[str] = _names(team)
    scheduled: list[str] = [
        template.name for template in TEMPLATES if template.id in team.members and template.cron != ""
    ]
    assert scheduled, "the team should suggest a schedule"
    with scratch_home() as home, eval_environment(home, {}):
        async with (
            _daemon(login_env) as (runtime, address),
            httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http,
            async_playwright() as playwright,
        ):
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _open(browser, address, "/agents", 1280, "light")
                cards: Locator = page.locator(".team-card")
                await expect(cards).to_have_count(len(TEAMS))
                await expect(cards.locator(".template-name")).to_have_text([each.name for each in TEAMS])
                await expect(cards.locator(".template-role")).to_have_text([each.purpose for each in TEAMS])
                # The templates' gallery is as it was: a card a template.
                await expect(page.locator(".template-card")).to_have_count(len(TEMPLATES))
                card: Locator = page.locator(f".team-card[data-team='{team.id}']")
                await expect(card.locator(".team-members > li")).to_have_text(names)
                await expect(card.locator(".avatar")).to_have_count(len(names))
                also: Locator = card.get_by_role("switch", name="Also add their schedules")
                await also.click()
                await expect(also).to_have_attribute("aria-checked", "true")
                await card.screenshot(path=tmp_path / "team-card-1280-light.png")
                use: Locator = card.get_by_role("button", name=f"Use the team {team.name}")
                await use.click()
                await expect(_toast(page, "ok", f"{team.name}: ")).to_be_visible()
                await expect(_toast(page, "ok", "Their schedules are switched off")).to_be_visible()
                summaries: list[JsonObject] = (await http.get("/api/agents")).raise_for_status().json()
                assert [each["record"]["name"] for each in summaries[1:]] == names
                views: list[JsonObject] = (await http.get("/api/schedules")).raise_for_status().json()
                assert sorted((view["record"]["name"], view["record"]["enabled"]) for view in views) == sorted(
                    (name, False) for name in scheduled
                )
                await use.click()
                await expect(_toast(page, "ok", f"You have every agent of the {team.name} already.")).to_be_visible()
                assert len((await http.get("/api/agents")).raise_for_status().json()) == 1 + len(names)
                await page.screenshot(path=tmp_path / "teams-1280-light.png", full_page=True)
                await page.close()
                phone: Page = await _open(browser, address, "/agents", 390, "dark")
                await expect(phone.locator(".team-card")).to_have_count(len(TEAMS))
                for index in range(len(TEAMS)):
                    box = await phone.locator(".team-card").nth(index).bounding_box()
                    assert box is not None
                    assert box["x"] + box["width"] <= 390
                assert await _overflow(phone) <= 0
                await phone.screenshot(path=tmp_path / "teams-390-dark.png", full_page=True)
                await phone.close()
            finally:
                await browser.close()

"""Templates and agent files (item 315, after Grok's): each of the eight templates becomes an agent in one click, with
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

from dotagent.agent.manager import RunManager
from dotagent.agent.templates import TEMPLATES
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
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, str]]:
    """The daemon in the home of the environment, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
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
                await expect(cards.locator(".template-name")).to_have_text([template.name for template in TEMPLATES])
                await expect(cards.locator(".template-role")).to_have_text([template.role for template in TEMPLATES])
                await expect(cards.locator(".avatar")).to_have_count(len(TEMPLATES))
                await page.screenshot(path=tmp_path / "templates-1280-light.png", full_page=True)
                phone: Page = await _open(browser, address, "/agents", 390, "dark")
                await expect(phone.locator(".template-card")).to_have_count(len(TEMPLATES))
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
                # Every template drafts what it writes to a person (item 314); one that works on the web and in files
                # has no tool that drives Mail, Messages or a terminal.
                assert all("draft_message" in template.instructions for template in TEMPLATES)
                bounded: list[str] = [template.id for template in TEMPLATES if template.tools]
                assert bounded == ["sales-outbound", "talent-scout", "paid-media", "product-performance"]
                for template in TEMPLATES:
                    if template.tools:
                        assert "draft_message" in template.tools, template.id
                        assert not DRIVERS & set(template.tools), template.id
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
        async with _daemon(login_env) as (runtime, address), async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                phone = await _open(browser, address, "/agents", 390, "dark")
                await expect(phone.locator(".template-card")).to_have_count(len(TEMPLATES))
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

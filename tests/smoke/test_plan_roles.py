"""A model of the owner's Claude plan in Dot's own work (item 1032): it may be the review model, every question of
which is a single one the plan answers (:func:`dotagent.claude_code.ask.question_of`); not the fast model, which takes a
run's steps, nor the vision model, which reads pictures, nor the model that looks for work on Dot's own initiative,
whose tools Claude Code's door does not give. Settings › Models chooses them through the daemon's routes.

The ``claude`` Dot finds is a script that does nothing, and Claude Code's state file says an account is signed in (no
secret in it): no call reaches a model."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from model_menu import expect_chosen, model_box, offered, why_not
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "plan-roles-token"


def _lay_out(root: Path) -> tuple[Path, Path, Path]:
    """Dot's home with its config, the ``claude`` Dot finds (a script that does nothing) and Claude Code's state folder,
    whose state file says which account is signed in (no secret in it)."""
    home: Path = root / "home"
    home.mkdir()
    (home / "config.toml").write_text("[browser]\nheadless = true", encoding="utf-8")
    program: Path = root / "bin" / "claude"
    program.parent.mkdir()
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    program.chmod(0o755)
    state: Path = root / "claude-state"
    state.mkdir()
    (state / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "owner@example.org", "organizationName": "Owner"}}),
        encoding="utf-8",
    )
    return home, program, state


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[Runtime, str]]:
    home, program, state = _lay_out(tmp_path_factory.mktemp("plan-roles"))
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(home.parent / "chrome"))
    patch.setenv("DOT_CLAUDE", str(program))
    patch.setenv("CLAUDE_CONFIG_DIR", str(state))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
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
    try:
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        yield runtime, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def test_a_model_of_the_claude_plan_may_review_but_not_take_steps_read_pictures_or_look_for_work(
    served: tuple[Runtime, str],
) -> None:
    runtime, address = served
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        before: tuple[str, str, str] = (runtime.roles.fast(), runtime.roles.review(), runtime.roles.vision())
        for role in ("fast", "vision"):
            refused: httpx.Response = await http.post(
                "/api/models/roles", json={"role": role, "model": "claude_code:opus"}
            )
            assert refused.status_code == 422 and "answers only as Claude Code" in refused.text, refused.text
        assert (runtime.roles.fast(), runtime.roles.review(), runtime.roles.vision()) == before
        chosen: httpx.Response = await http.post(
            "/api/models/roles", json={"role": "review", "model": "claude_code:opus"}
        )
        assert chosen.status_code == 200, chosen.text
        assert runtime.roles.review() == "claude_code:opus"
        assert load_config(config_path()).models.review == "claude_code:opus"
        # The look for work is a run that keeps Dot's list and reads the owner's mail: a run on the plan has those tools
        # since item 1063, so a model of the plan may look.
        taken_look: httpx.Response = await http.put("/api/initiative/look-model", json={"model": "claude_code:sonnet"})
        assert taken_look.status_code == 200, taken_look.text
        assert runtime.roles.look() == "claude_code:sonnet"


async def test_settings_choose_the_model_of_each_role_of_the_learning_from_its_next_question(
    served: tuple[Runtime, str],
) -> None:
    """Item 1033: the roles of the improvement loop are chosen on Settings › Models as the others are, live and kept in
    the config file; "" plays a role on the review model again."""
    runtime, address = served
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        start: httpx.Response = await http.get("/api/models/learning")
        assert start.status_code == 200, start.text
        assert start.json()["proposer"] == "" and start.json()["review"] == runtime.roles.review(), start.text
        chosen: httpx.Response = await http.put(
            "/api/models/learning", json={"role": "proposer", "model": "claude_code:opus"}
        )
        assert chosen.status_code == 200 and chosen.json()["proposer"] == "claude_code:opus", chosen.text
        assert runtime.roles.learning_model("proposer") == "claude_code:opus"
        assert load_config(config_path()).harness.proposer == "claude_code:opus"
        refused: httpx.Response = await http.put(
            "/api/models/learning", json={"role": "composer", "model": "opencode:big"}
        )
        assert refused.status_code == 422 and "cannot play a role of the learning" in refused.text, refused.text
        assert runtime.roles.learning("composer") == ""
        back: httpx.Response = await http.put("/api/models/learning", json={"role": "proposer", "model": ""})
        assert back.status_code == 200 and back.json()["proposer"] == "", back.text
        assert runtime.roles.learning_model("proposer") == runtime.roles.review()
        assert load_config(config_path()).harness.proposer == ""


@pytest.mark.browser
async def test_the_models_page_puts_every_model_of_dots_own_work_in_one_place(
    served: tuple[Runtime, str], tmp_path: Path
) -> None:
    """Item 1033: Settings › Models holds the roles, the night's learning and Self-repair, and what the schedules and
    the agents run on; a role offers only the models that can do its work; one press puts the review model on the
    owner's Claude plan."""
    runtime, address = served
    # One schedule that names its model, one that runs as an agent naming none (it runs on the agent's), and the agent.
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        agent: httpx.Response = await http.post(
            "/api/agents",
            json={
                "name": "Scout",
                "role": "Finds sources",
                "instructions": "Prefer primary sources.",
                "model": "claude_code:sonnet",
                "effort": None,
                "approvals": None,
            },
        )
        assert agent.status_code == 201, agent.text
        for body in (
            {
                "name": "Backup",
                "goal": "Back up.",
                "cron": "0 15 * * *",
                "mode": "research",
                "model": "claude_code:opus",
            },
            {"name": "Scan", "goal": "Scan.", "cron": "0 9 * * *", "mode": "research", "agent_id": agent.json()["id"]},
        ):
            made: httpx.Response = await http.post("/api/schedules", json=body)
            assert made.status_code == 201, made.text
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/settings/models")
                await expect(model_box(page, "Review model")).to_be_visible(timeout=15_000)
                # The plan's models stand on every box's rail (item 1044): offered where they can play the role, and
                # where they cannot, their view says why.
                fast: dict[str, list[str]] = await offered(page, "Fast model")
                assert fast.get("claude_code") == [], fast
                assert "only as their own agent program" in await why_not(page, "Fast model", "claude_code")
                assert "claude_code:opus" in (await offered(page, "Review model"))["claude_code"]
                assert "claude_code:haiku" in (await offered(page, "Default model"))["claude_code"]
                await expect(model_box(page, "Proposer")).to_contain_text("Review model")
                await expect(page.get_by_role("combobox", name="Self-repair model")).to_be_visible()
                await expect(page.locator(".models-usage")).to_contain_text("Schedules: 1 on Sonnet 5.5, 1 on Opus 5.5")
                await expect(page.locator(".models-usage")).to_contain_text("Agents: 1 on Sonnet 5.5")
                over: object = await page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                assert isinstance(over, int | float) and over <= 1, f"{width}: {over} px past the edge"
                await page.screenshot(path=str(tmp_path / f"models-{width}.png"), full_page=True)
                if width == 1280:
                    await page.get_by_role("button", name="Use my Claude plan").click()
                    await expect(page.locator(".toast.ok")).to_contain_text("Claude plan")
                    assert runtime.roles.review() == "claude_code:opus"
                    assert runtime.roles.learning_model("proposer") == "claude_code:opus"
                    await expect_chosen(page, "Review model", "claude_code:opus")
                await page.close()
        finally:
            await browser.close()

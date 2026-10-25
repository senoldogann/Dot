"""Customize › Skills (item 317) end to end, in a scratch home on the scripted model, served by a real server and seen
in a real headless Chrome. Personal holds the owner's own skills (seven, so the section shows six and See 1 more),
one imported from Claude Code and the learned ones; System the skills that come with Dot and one of a plugin. The
search finds one skill by its name; the cards stand two a row on a wide page and one on a phone; the settings' old
Skills address leads here. Save from a chat writes the skill of a chat that ended done after one acting step, through
the review model; Share offers an agent's learned skill to every run; Drop removes a learned skill and Remove an
imported one, which is then found again on this Mac."""

import asyncio
import json
import re
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, FloatRect, Locator, Page, Route, async_playwright, expect
from test_composer_picks import DONE, GOAL, KIND, SKILL_WRITTEN, SOURCE, TOKEN, WRITE, _daemon, _served

from dotagent.agent.playbooks import Playbook, playbook_name, write_playbook
from dotagent.agent.review import PlaybookDraft
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.server.links import ui_url
from dotagent.store.models import NO_PICKS, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_OWN: tuple[str, ...] = tuple(f"own-skill-{number}" for number in range(1, 8))
_PLUGIN: str = "acme"
_PLUGIN_SKILL: str = "acme-brief"
_FOUND: str = "pdf-tools"
# A kind as long as the review model may write one (60 characters at most): its chip keeps to the card (review A2).
_SHARED_KIND: str = "Prepare the weekly status report from the project tracker"
_SHARED: str = playbook_name(_SHARED_KIND)
_WAIT_S: float = 60.0
_IDLE_GOAL: str = "Say hello in one word."
# How long the skills Dot offers take to read in one look, and how soon a card shows an action's change.
_SLOW_PALETTE_S: float = 3.0
_AT_ONCE_MS: int = 1500


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _skill(folder: Path, name: str, description: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nFollow the steps.\n", encoding="utf-8"
    )


def _config() -> AppConfig:
    """An eval run's configuration with learned skills on (after more acting steps than the run here takes), the
    scripted model as the review model too, and the skills of the Mac's other agents looked for."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"playbooks": True, "skill_min_steps": 10}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            "skills": config.skills.model_copy(update={"elsewhere": True}),
        }
    )


def _home_skills(home: Path) -> None:
    """The owner's seven skills, a plugin's skill, and an agent's learned skill that is its own until shared."""
    for number, name in enumerate(_OWN, start=1):
        _skill(home / "skills", name, f"The owner's own skill number {number}.")
    plugin: Path = home / "plugins" / _PLUGIN
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": _PLUGIN}), encoding="utf-8")
    _skill(plugin / "skills", _PLUGIN_SKILL, "Write a brief for Acme's weekly call.")
    draft: PlaybookDraft = PlaybookDraft(
        kind=_SHARED_KIND,
        description="Write the agent's weekly report.",
        when="A goal that asks for the weekly report.",
        steps=["Gather the week's work.", "Write the report."],
        parameters=[],
    )
    playbooks_dir().mkdir(parents=True, exist_ok=True)
    write_playbook(
        playbooks_dir(),
        Playbook(
            name=_SHARED,
            kind=draft.kind,
            host="none",
            draft=draft,
            run_id="run_agent",
            agent_id="agent_test",
            goal="Write the weekly report.",
            written_at=time.time(),
            acting_steps=2,
            tainted=False,
        ),
    )


def _card(page: Page, name: str) -> Locator:
    return page.locator(f".skill-card[data-skill='{name}']")


def _section(page: Page, section: str) -> Locator:
    return page.locator(f"section.plugin-section[data-section='{section}']")


async def _box(locator: Locator) -> FloatRect:
    box: FloatRect | None = await locator.bounding_box()
    assert box is not None, locator
    return box


async def test_skills_are_in_their_tabs_and_are_saved_shared_and_removed_from_the_page(
    login_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skill(tmp_path / ".claude" / "skills", _FOUND, "Fill and merge PDF forms.")
    with scratch_home() as home, eval_environment(home, {}):
        _home_skills(home)
        async with _daemon(login_env, _config(), [WRITE, DONE, DONE, SKILL_WRITTEN]) as (runtime, manager, _model):
            monkeypatch.setenv("HOME", str(tmp_path))
            run: RunRecord = await manager.start_picked(
                GOAL, "task", SCRIPTED_MODEL, None, SOURCE, None, None, "standard", None, "ask", False, (), NO_PICKS
            )
            ended: RunRecord = await manager.wait(run.id)
            await manager.distilled(run.id)
            assert ended.status == "done"
            # A chat that answered without acting: the server gives it no skill, and says why.
            idle: RunRecord = await manager.start_picked(
                _IDLE_GOAL,
                "task",
                SCRIPTED_MODEL,
                None,
                SOURCE,
                None,
                None,
                "standard",
                None,
                "ask",
                False,
                (),
                NO_PICKS,
            )
            assert (await manager.wait(idle.id)).status == "done"
            await manager.distilled(idle.id)
            async with _served(runtime, manager) as served:
                async with httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
                    imported: httpx.Response = await client.put(
                        "/api/skills/elsewhere",
                        json={"kind": "skill", "agent": "Claude Code", "name": _FOUND, "added": True},
                    )
                    assert imported.status_code == 200, imported.text
                port: int = int(served.rpartition(":")[2])
                async with async_playwright() as playwright:
                    browser: Browser = await playwright.chromium.launch(
                        channel=runtime.config.browser.channel, headless=True
                    )
                    try:
                        page: Page = await browser.new_page(
                            viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
                        )
                        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                        await page.goto(ui_url("127.0.0.1", port, TOKEN))
                        # The settings' old address leads to Customize › Skills.
                        await page.goto(f"{served}/#/settings/skills")
                        await expect(page).to_have_url(f"{served}/#/customize/skills")
                        await expect(page.locator(".customize-tabs a[aria-current='page']")).to_have_text("Skills")
                        await expect(page.get_by_role("button", name=re.compile(r"^Personal \d+$"))).to_have_text(
                            "Personal 9"
                        )
                        # Personal: the owner's own, six shown and the seventh a click away; the imported one; the
                        # agent's learned one, its own until shared.
                        own: Locator = _section(page, "own")
                        await expect(own.locator(".skill-card")).to_have_count(6)
                        await own.get_by_role("button", name="See 1 more").click()
                        await expect(own.locator(".skill-card")).to_have_count(7)
                        await expect(_card(page, _FOUND)).to_contain_text("Imported from Claude Code")
                        await expect(_section(page, "imported").locator(".skill-card")).to_have_count(1)
                        shared: Locator = _card(page, _SHARED)
                        await expect(shared.locator(".skill-have")).to_have_text("Learned")
                        # Two cards a row on a wide page, measured once what the Mac's other agents set up is read
                        # and drawn (the page paints again then).
                        await expect(page.get_by_text("Found on this Mac")).to_be_visible()
                        first: FloatRect = await _box(own.locator(".skill-card").nth(0))
                        second: FloatRect = await _box(own.locator(".skill-card").nth(1))
                        assert first["y"] == second["y"] and second["x"] > first["x"], (first, second)
                        # The search finds one skill by its name.
                        search: Locator = page.get_by_role("searchbox", name="Search skills")
                        await search.press_sequentially("own-skill-7")
                        await expect(search).to_be_focused()
                        await expect(search).to_have_value("own-skill-7")
                        await expect(page.locator(".skill-card")).to_have_count(1)
                        await expect(_card(page, "own-skill-7")).to_be_visible()
                        await search.fill("")
                        # System: the skills that come with Dot, and the plugin's with its name.
                        await page.get_by_role("button", name=re.compile(r"^System \d+$")).click()
                        await expect(_section(page, "builtin").locator(".skill-card")).to_have_count(3)
                        await expect(_card(page, _PLUGIN_SKILL)).to_contain_text(_PLUGIN)
                        await expect(_card(page, _SHARED)).to_have_count(0)
                        await page.screenshot(path=str(tmp_path / "skills-system-1280-light.png"), full_page=True)
                        await page.get_by_role("button", name=re.compile(r"^Personal \d+$")).click()

                        # Share offers the agent's skill to every run, and the card says so at once, before the
                        # skills Dot offers are read again (review A5: it waited for them).
                        async def slow(route: Route) -> None:
                            await asyncio.sleep(_SLOW_PALETTE_S)
                            await route.continue_()

                        await page.route("**/api/palette", slow)
                        await shared.get_by_role("button", name="Share").click()
                        await expect(page.locator(".toast.ok", has_text="is offered to every task")).to_be_visible()
                        await expect(shared.get_by_role("button", name="Keep to the agent")).to_be_visible(
                            timeout=_AT_ONCE_MS
                        )
                        await page.unroute("**/api/palette")
                        # Save from a chat: the chat that ended done, its skill written by the review model.
                        await page.get_by_role("button", name="Save from a chat").click()
                        dialog: Locator = page.locator("dialog.chat-skill-dialog")
                        # A chat with no acting step is refused in the dialog, which stays for another choice
                        # (review A4: it closed first and the refusal came as a toast).
                        await dialog.locator(".chat-skill-row", has_text=_IDLE_GOAL).click()
                        await expect(dialog.locator(".chat-skill-problem")).to_contain_text(
                            "it took no acting step, so it did nothing a skill could repeat"
                        )
                        await expect(dialog).to_be_visible()
                        await dialog.locator(".chat-skill-row", has_text=GOAL).click()
                        name: str = playbook_name(KIND)
                        await expect(page.locator(".toast.ok", has_text=f"Saved the skill {name}.")).to_be_visible()
                        saved: Locator = _card(page, name)
                        await expect(saved.locator(".skill-have")).to_have_text("Learned")
                        assert (playbooks_dir() / name / "SKILL.md").is_file()
                        await page.screenshot(path=str(tmp_path / "skills-personal-1280-light.png"), full_page=True)
                        # Drop removes the learned skill; Remove the imported one, found again on this Mac.
                        await saved.get_by_role("button", name="Drop").click()
                        await page.locator("dialog.modal").get_by_role("button", name="Drop").click()
                        await expect(saved).to_have_count(0)
                        assert not (playbooks_dir() / name).exists()
                        await _card(page, _FOUND).get_by_role("button", name="Remove").click()
                        await page.locator("dialog.modal").get_by_role("button", name="Remove").click()
                        await expect(_section(page, "imported")).to_have_count(0)
                        await expect(page.locator(".customize-body")).not_to_contain_text("null")
                        await expect(page.get_by_role("button", name="Import from other apps")).to_be_enabled()
                        await page.close()
                        # On a phone, dark: one card a row, nothing wider than the screen.
                        phone: Page = await browser.new_page(
                            viewport={"width": 390, "height": 844},
                            color_scheme="dark",
                            reduced_motion="reduce",
                            has_touch=True,
                        )
                        await phone.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                        await phone.goto(ui_url("127.0.0.1", port, TOKEN))
                        await phone.goto(f"{served}/#/customize/skills")
                        cards: Locator = _section(phone, "own").locator(".skill-card")
                        await expect(cards).to_have_count(6)
                        await expect(phone.get_by_text("Found on this Mac")).to_be_visible()
                        lefts: set[float] = {(await _box(cards.nth(index)))["x"] for index in range(2)}
                        assert len(lefts) == 1, lefts
                        wide: int = await phone.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
                        assert wide <= 0, wide
                        await phone.screenshot(path=str(tmp_path / "skills-390-dark.png"), full_page=True)
                        await phone.close()
                    finally:
                        await browser.close()


async def test_a_skills_page_that_could_not_be_read_offers_try_again_as_the_other_tabs_do(
    login_env: dict[str, str],
) -> None:
    """Item 559: Skills said "This could not be loaded" with no way to read again but to leave the page and come back,
    for its skills and for the part that lists what other apps set up; Plugins and Connectors have Try again. Each read
    fails once here, as a daemon whose store is busy fails it, and the next read answers."""
    with scratch_home() as home, eval_environment(home, {}):
        _home_skills(home)
        async with _daemon(login_env, _config(), []) as (runtime, manager, _model), _served(runtime, manager) as served:
            failing: dict[str, int] = {"learned": 1, "elsewhere": 1}

            async def busy_once(route: Route) -> None:
                read: str = route.request.url.rsplit("/", 1)[-1]
                if failing[read] > 0:
                    failing[read] -= 1
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"message": "the store is busy", "context": {}}',
                    )
                    return
                await route.continue_()

            async with async_playwright() as playwright:
                browser: Browser = await playwright.chromium.launch(
                    channel=runtime.config.browser.channel, headless=True
                )
                try:
                    page: Page = await browser.new_page(
                        viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
                    )
                    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await page.route("**/api/skills/learned", busy_once)
                    await page.route("**/api/skills/elsewhere", busy_once)
                    await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
                    await page.goto(f"{served}/#/customize/skills")
                    # The skills could not be read: the page says so, and reads them again on Try again.
                    again: Locator = page.get_by_role("button", name="Try again")
                    await expect(page.get_by_text("Dot ran into a problem and could not finish.").first).to_be_visible()
                    await expect(page.locator(".error-details").first).to_contain_text("the store is busy")
                    await expect(again).to_have_count(1)
                    await again.click()
                    await expect(_card(page, "own-skill-1")).to_be_visible()
                    # What other apps set up could not be read either: its part says so, and reads it again.
                    await expect(page.get_by_text("Dot ran into a problem and could not finish.").first).to_be_visible()
                    await expect(page.locator(".error-details").first).to_contain_text("the store is busy")
                    await expect(again).to_have_count(1)
                    await again.click()
                    await expect(page.get_by_text("the store is busy")).to_have_count(0)
                    await expect(again).to_have_count(0)
                    await expect(_card(page, "own-skill-1")).to_be_visible()
                    await page.close()
                finally:
                    await browser.close()

"""The life of the learned skills on Customize › Skills in a real browser (item 585): a skill no run read for the days
the settings name is shown as resting, last in its section, and Wake offers it again; Pin keeps one from resting; the
merge the nightly reflection proposed waits as a card that shows everything it would write (the name, the description,
the kind of work, when it fits, the steps and the parameters, each against the first skill), and the owner's yes writes
exactly that as a new version of the first skill and puts the other to rest, kept whole; a skill that was rewritten
lists its versions in a dialog, each with how it was made, its mark and the steps it changed, and Restore puts one back
as a new version after a question; and all of it fits a phone. No model is called: the home is a scratch directory."""

import asyncio
import time
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from test_composer_picks import TOKEN, _daemon, _served

from dotagent.agent.playbooks import (
    Playbook,
    playbook_name,
    read_meta,
    save_meta,
    update_playbook,
    write_playbook,
)
from dotagent.agent.review import PlaybookDraft
from dotagent.agent.skill_history import SkillText, skill_text
from dotagent.agent.skill_proposals import SkillProposal, save_proposal
from dotagent.config import AppConfig, load_default_config
from dotagent.core.ids import new_id
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.server.links import ui_url
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

DAY_S: float = 86400.0
STEADY_KIND: str = "Prepare the weekly status report"
DUSTY_KIND: str = "Fill the old expense form"
REWRITTEN_KIND: str = "Manage a cold email campaign"
FIRST_KIND: str = "Write the weekly report"
SECOND_KIND: str = "Draft the weekly status"
STEADY: str = playbook_name(STEADY_KIND)
DUSTY: str = playbook_name(DUSTY_KIND)
REWRITTEN: str = playbook_name(REWRITTEN_KIND)
FIRST: str = playbook_name(FIRST_KIND)
SECOND: str = playbook_name(SECOND_KIND)
# What the merge proposes to write, in every field: the card shows each of them against the first skill's own.
MERGED: PlaybookDraft = PlaybookDraft(
    kind="Report the week",
    description="Report the week from the tracker and mail it to the team.",
    when="A goal that asks for the weekly report, and what must be at hand before it starts.",
    steps=["Open the tracker.", "Write and send it.", "Count the replies."],
    parameters=["<list>: the contacts", "<week>: the week to report"],
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """An eval run's configuration with learned skills on and the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"playbooks": True}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


def _draft(kind: str, *steps: str) -> PlaybookDraft:
    return PlaybookDraft(
        kind=kind,
        description=f"{kind}: what is done and where.",
        when=f"A goal that asks for {kind.lower()}.",
        steps=list(steps),
        parameters=["<list>: the contacts"],
    )


def _playbook(kind: str, run: str, *steps: str, tainted: bool = False) -> Playbook:
    return Playbook(
        name=playbook_name(kind),
        kind=kind,
        host="mail.example.com",
        draft=_draft(kind, *steps),
        run_id=run,
        agent_id=None,
        goal=f"Goal of {run}",
        written_at=time.time(),
        acting_steps=len(steps),
        tainted=tainted,
    )


def _seed() -> str:
    """The skills of the page, in the scratch home: one awake, one no run read for 70 days, one a later run rewrote (its
    steps carry the mark of content third parties can author) and two that say the same, with the merge waiting for the
    owner's yes. Returns the id of the proposal."""
    root: Path = playbooks_dir()
    write_playbook(root, _playbook(STEADY_KIND, "run-1", "Gather the week's work.", "Write the report."))
    write_playbook(root, _playbook(DUSTY_KIND, "run-2", "Open the form.", "Fill it in."))
    long_ago: float = time.time() - 70 * DAY_S
    save_meta(root / DUSTY, read_meta(root / DUSTY).model_copy(update={"written_at": long_ago, "updated_at": long_ago}))
    write_playbook(root, _playbook(REWRITTEN_KIND, "run-3", "Open the inbox.", "Send the first mail."))
    update_playbook(
        root, REWRITTEN, _playbook(REWRITTEN_KIND, "run-4", "Open the inbox.", "Send the mails.", tainted=True)
    )
    write_playbook(root, _playbook(FIRST_KIND, "run-5", "Open the tracker.", "Write it."))
    write_playbook(root, _playbook(SECOND_KIND, "run-6", "Open the tracker.", "Draft it.", "Send it."))
    proposal: SkillProposal = SkillProposal(
        id=new_id("skp"),
        kind="merge",
        names=(FIRST, SECOND),
        draft=MERGED,
        reason="the same procedure",
        words=None,
        bases={FIRST: 1, SECOND: 1},
        tainted=False,
        created_at=time.time(),
    )
    save_proposal(root, proposal)
    return proposal.id


def _card(page: Page, name: str) -> Locator:
    return page.locator(f".skill-card[data-skill='{name}']")


def _section(page: Page, section: str) -> Locator:
    return page.locator(f"section.plugin-section[data-section='{section}']")


def _written(name: str) -> SkillText:
    """What the SKILL.md of a learned skill says of the work now."""
    file: Path = playbooks_dir() / name / "SKILL.md"
    return skill_text(file.read_text(encoding="utf-8"), file)


async def _open(browser: Browser, served: str, width: int) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme="light", reduced_motion="reduce"
    )
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
    await page.goto(f"{served}/#/customize/skills")
    return page


async def test_the_skills_page_shows_what_rests_and_what_waits_and_lists_the_versions(
    login_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with scratch_home() as home, eval_environment(home, {}):
        proposal_id: str = _seed()
        async with (
            _daemon(login_env, _config(), []) as (runtime, manager, _model),
            _served(runtime, manager) as served,
            async_playwright() as playwright,
        ):
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _open(browser, served, 1280)
                # The merge the reflection proposed waits above the skills, with the steps it would write.
                waiting: Locator = _section(page, "proposals").locator(".proposal-card")
                await expect(waiting).to_have_count(1)
                await expect(waiting.locator(".skill-name")).to_have_text(f"Merge {FIRST} and {SECOND}")
                await expect(waiting).to_contain_text("Why: the same procedure")
                steps: Locator = waiting.locator("[data-field='steps']")
                await expect(steps.locator(".version-step.kept")).to_have_text(["Open the tracker."])
                await expect(steps.locator(".version-step.dropped")).to_have_text(["-Dropped: Write it."])
                await expect(steps.locator(".version-step.added")).to_have_text(
                    ["+Added: Write and send it.", "+Added: Count the replies."]
                )
                await expect(waiting).to_contain_text("3 steps: 2 new, 1 dropped, 1 kept")
                # Everything else the yes writes is on the card too, each against what the first skill says now: the
                # description is a line of every run's instructions, and the card showed it before it is written.
                await expect(waiting.locator(".proposal-field")).to_have_count(6)
                await expect(waiting.locator(".proposal-field-label")).to_have_text(
                    ["Name", "Description", "Kind of work", "When", "Steps", "Parameters"]
                )
                await expect(waiting.locator("[data-field='name']")).to_contain_text(FIRST)
                for field, dropped, added in (
                    ("description", [f"{FIRST_KIND}: what is done and where."], [MERGED.description]),
                    ("kind", [FIRST_KIND], [MERGED.kind]),
                    ("when", [f"A goal that asks for {FIRST_KIND.lower()}."], [MERGED.when]),
                    ("parameters", [], [MERGED.parameters[1]]),
                ):
                    block: Locator = waiting.locator(f"[data-field='{field}']")
                    await expect(block).to_have_attribute("data-changed", "true")
                    await expect(block.locator(".version-step.dropped")).to_have_text(
                        [f"-Dropped: {x}" for x in dropped]
                    )
                    await expect(block.locator(".version-step.added")).to_have_text([f"+Added: {x}" for x in added])
                await expect(waiting.locator("[data-field='parameters'] .version-step.kept")).to_have_text(
                    [MERGED.parameters[0]]
                )
                await expect(waiting.locator(".proposal-effects li")).to_have_count(2)
                await expect(waiting.locator(".proposal-effects")).to_contain_text(f"{SECOND} rests, kept whole")
                await waiting.scroll_into_view_if_needed()
                await waiting.screenshot(path=str(tmp_path / "skills-proposal-1280.png"))
                # A skill no run read for 70 days rests, after the awake ones; the others are awake.
                await expect(_card(page, DUSTY).locator(".skill-have")).to_have_text("Resting")
                await expect(_card(page, DUSTY)).to_contain_text("No task read it for 60 days")
                await expect(_card(page, STEADY).locator(".skill-have")).to_have_text("Learned")
                learned: Locator = _section(page, "learned").locator(".skill-card")
                names: list[str | None] = [await card.get_attribute("data-skill") for card in await learned.all()]
                assert names.index(DUSTY) == len(names) - 1, names
                await expect(_card(page, DUSTY).get_by_role("button", name="Wake")).to_be_visible()
                # Pin keeps a skill from resting, and takes its rest from the pin.
                await _card(page, DUSTY).get_by_role("button", name="Pin").click()
                await expect(page.locator(".toast.ok", has_text="is pinned: it never rests")).to_be_visible()
                await expect(_card(page, DUSTY).locator(".skill-have")).to_have_text("Pinned")
                await expect(_card(page, DUSTY).get_by_role("button", name="Unpin")).to_have_attribute(
                    "aria-pressed", "true"
                )
                assert read_meta(playbooks_dir() / DUSTY).pinned
                await _card(page, DUSTY).get_by_role("button", name="Unpin").click()
                await expect(_card(page, DUSTY).locator(".skill-have")).to_have_text("Learned")
                assert not read_meta(playbooks_dir() / DUSTY).pinned

                # Yes writes what the card showed, as a new version of the first, and the other rests whole.
                await waiting.get_by_role("button", name="Merge").click()
                await expect(page.locator(".toast.ok", has_text=f"Merged {FIRST} and {SECOND}.")).to_be_visible()
                await expect(_section(page, "proposals")).to_have_count(0)
                await expect(_card(page, FIRST).get_by_role("button", name="Versions (2)")).to_be_visible()
                await expect(_card(page, SECOND).locator(".skill-have")).to_have_text("Resting")
                await expect(_card(page, SECOND)).to_contain_text(f"Merged into {FIRST}")
                assert (playbooks_dir() / FIRST / ".versions" / "1.md").is_file()
                # What the yes wrote is what the card showed, field by field.
                written: SkillText = await asyncio.to_thread(_written, FIRST)
                assert (written.description, written.kind, written.when) == (
                    MERGED.description,
                    MERGED.kind,
                    MERGED.when,
                )
                assert (written.steps, written.parameters) == (tuple(MERGED.steps), tuple(MERGED.parameters))
                assert (playbooks_dir() / SECOND / "SKILL.md").is_file()
                assert not (playbooks_dir() / ".proposals" / f"{proposal_id}.json").exists()
                # Wake makes the one a merge put aside its own skill again.
                await _card(page, SECOND).get_by_role("button", name="Wake").click()
                await expect(_card(page, SECOND).locator(".skill-have")).to_have_text("Learned")
                assert read_meta(playbooks_dir() / SECOND).merged_into is None

                # The versions of the skill a later run rewrote, the newest first, with what each changed.
                await _card(page, REWRITTEN).get_by_role("button", name="Versions (2)").click()
                dialog: Locator = page.locator("dialog.versions-dialog")
                await expect(dialog.get_by_role("heading", name=f"Versions of {REWRITTEN}")).to_be_visible()
                blocks: Locator = dialog.locator(".version-block")
                await expect(blocks).to_have_count(2)
                await expect(blocks.nth(0)).to_have_attribute("data-version", "2")
                await expect(blocks.nth(0)).to_have_attribute("data-current", "true")
                await expect(blocks.nth(0).locator(".badge.b-ok")).to_have_text("Current")
                await expect(blocks.nth(0).locator(".badge.b-warn")).to_contain_text("From outside content")
                await expect(blocks.nth(0)).to_contain_text("Rewritten by a later task")
                await expect(blocks.nth(0).locator(".version-step.dropped")).to_have_text(
                    ["-Dropped: Send the first mail."]
                )
                await expect(blocks.nth(0).locator(".version-step.added")).to_have_text(["+Added: Send the mails."])
                await expect(blocks.nth(1)).to_contain_text("Learned from a task")
                await expect(blocks.nth(1).locator(".badge.b-warn")).to_have_count(0)
                await expect(blocks.nth(0).get_by_role("button", name="Restore")).to_have_count(0)
                await dialog.screenshot(path=str(tmp_path / "skills-versions-1280.png"))
                # Restore asks first, then puts version 1 back as version 3, with the mark it had (none).
                await blocks.nth(1).get_by_role("button", name="Restore").click()
                question: Locator = page.locator("dialog.modal", has_text="Put version 1")
                await expect(question).to_contain_text("Its steps become version 3")
                await question.get_by_role("button", name="Put it back").click()
                await expect(page.locator(".toast.ok", has_text="Put version 1")).to_be_visible()
                await expect(blocks).to_have_count(3)
                await expect(blocks.nth(0)).to_have_attribute("data-version", "3")
                await expect(blocks.nth(0)).to_contain_text("Restored from version 1")
                await expect(blocks.nth(0).locator(".badge.b-warn")).to_have_count(0)
                assert read_meta(playbooks_dir() / REWRITTEN).version == 3
                await dialog.get_by_role("button", name="Close").click()
                await expect(dialog).to_have_count(0)
                await expect(_card(page, REWRITTEN).get_by_role("button", name="Versions (3)")).to_be_visible()
                await page.screenshot(path=str(tmp_path / "skills-life-1280.png"))
                await page.close()
            finally:
                await browser.close()


async def _overrun(page: Page) -> list[str]:
    """The cards, steps and versions of the page whose words run past their own box, by name."""
    return await page.evaluate(
        "[...document.querySelectorAll('.skill-card, .version-block, .version-step, .proposal-field')]"
        ".filter((box) => box.scrollWidth > box.clientWidth + 1)"
        ".map((box) => box.dataset.skill ?? box.dataset.version ?? box.textContent.slice(0, 40))"
    )


async def test_the_life_of_the_skills_fits_a_phone(
    login_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with scratch_home() as home, eval_environment(home, {}):
        _seed()
        async with (
            _daemon(login_env, _config(), []) as (runtime, manager, _model),
            _served(runtime, manager) as served,
            async_playwright() as playwright,
        ):
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width in (390, 320):
                    page: Page = await _open(browser, served, width)
                    try:
                        waiting: Locator = _section(page, "proposals").locator(".proposal-card")
                        await expect(waiting).to_have_count(1)
                        # All six fields of the yes are on the card, and the longest line of them (the description,
                        # which no word of is cut) wraps inside it.
                        await expect(waiting.locator(".proposal-field")).to_have_count(6)
                        await expect(waiting.locator("[data-field='description'] .version-step.added")).to_contain_text(
                            MERGED.description
                        )
                        await expect(_card(page, DUSTY).locator(".skill-have")).to_have_text("Resting")
                        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
                        assert await _overrun(page) == [], width
                        for button in (
                            waiting.get_by_role("button", name="Merge"),
                            waiting.get_by_role("button", name="No"),
                            _card(page, DUSTY).get_by_role("button", name="Wake"),
                            _card(page, REWRITTEN).get_by_role("button", name="Versions (2)"),
                        ):
                            box = await button.bounding_box()
                            assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width, (
                                width,
                                box,
                            )
                        await page.screenshot(path=str(tmp_path / f"skills-life-{width}.png"), full_page=True)
                        await waiting.screenshot(path=str(tmp_path / f"skills-proposal-{width}.png"))
                        await _card(page, REWRITTEN).get_by_role("button", name="Versions (2)").click()
                        dialog: Locator = page.locator("dialog.versions-dialog")
                        await expect(dialog.locator(".version-block")).to_have_count(2)
                        frame = await dialog.bounding_box()
                        assert frame is not None and frame["x"] >= 0 and frame["x"] + frame["width"] <= width, (
                            width,
                            frame,
                        )
                        restore = await dialog.get_by_role("button", name="Restore").bounding_box()
                        assert restore is not None and restore["x"] + restore["width"] <= width, (width, restore)
                        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
                        assert await _overrun(page) == [], width
                        await dialog.screenshot(path=str(tmp_path / f"skills-versions-{width}.png"))
                    finally:
                        await page.close()
            finally:
                await browser.close()

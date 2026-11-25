"""A skill Dot could not offer to a task is on the page of skills (item 1405), in a scratch home on the scripted
model, served by a real server and seen in a real headless Chrome. The owner's folder and the plugin of the audit's
case each hold a skill whose frontmatter is not closed: ``GET
/api/skills/left-out`` lists each with its file, the catalog's reason and where Dot looked for it, and Settings ›
Skills says so at the foot of the tab each belongs on (Personal, System), a closed fold with the reason and the file
in it, readable on a phone 390 px wide and on a wide page."""

import json
import re
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from test_composer_picks import TOKEN, _client, _daemon, _served

from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.server.links import ui_url
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_REASON: str = "the frontmatter of SKILL.md is not closed by a --- line"
_GOOD: str = "---\nname: good-skill\ndescription: A skill that loads.\n---\nFollow the steps.\n"
_PLUGIN: str = "twilio-developer-kit"
_PHONE: dict[str, int] = {"width": 390, "height": 844}
_WIDE: dict[str, int] = {"width": 1280, "height": 900}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"skills": config.skills.model_copy(update={"elsewhere": False})})


def _write(folder: Path, name: str, text: str) -> None:
    (folder / name).mkdir(parents=True)
    (folder / name / "SKILL.md").write_text(text, encoding="utf-8")


def _home_skills(home: Path) -> None:
    """A skill that loads and one whose frontmatter is not closed in the owner's folder; a plugin with one such skill
    (the audit's twilio-agent-connect opened with a title, which loads since item 1723) and one that loads."""
    _write(home / "skills", "good-skill", _GOOD)
    _write(home / "skills", "open-frontmatter", "---\nname: open-frontmatter\nSteps.\n")
    plugin: Path = home / "plugins" / _PLUGIN
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": _PLUGIN}), encoding="utf-8")
    _write(plugin / "skills", "twilio-agent-connect", "---\nname: twilio-agent-connect\ndescription: x\n")
    _write(plugin / "skills", "twilio-call", _GOOD.replace("good-skill", "twilio-call"))


async def test_the_route_lists_each_skill_left_out_with_its_file_its_reason_and_its_source(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        _home_skills(home)
        async with (
            _daemon(login_env, _config(), []) as (runtime, manager, _model),
            _client(runtime, manager) as client,
        ):
            answer: httpx.Response = await client.get("/api/skills/left-out")
    assert answer.status_code == 200, answer.text
    items: list[dict[str, object]] = answer.json()["items"]
    listed: dict[str, tuple[object, object, object]] = {
        str(Path(str(item["path"])).parent.name): (item["reason"], item["source"], item["shadowed"]) for item in items
    }
    assert listed == {
        "open-frontmatter": (_REASON, "home", False),
        "twilio-agent-connect": (_REASON, f"plugin {_PLUGIN}", False),
    }, items
    assert all(str(item["path"]).endswith("/SKILL.md") for item in items)


async def _fold(page: Page, tab: str) -> Locator:
    """The closed fold of what was left out on ``tab``, opened: its toggle and then its rows."""
    toggle: Locator = page.locator(".fold-toggle", has_text="Skills Dot could not use")
    await expect(toggle).to_be_visible()
    await expect(toggle).to_have_attribute("aria-expanded", "false")
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-expanded", "true")
    return page.locator(f"#fold-left-out-{tab}")


async def _fits(page: Page, rows: Locator) -> None:
    """Nothing of the page or of a row runs past the window's right edge."""
    width: int = await page.evaluate("document.documentElement.clientWidth")
    assert await page.evaluate("document.documentElement.scrollWidth") <= width
    for index in range(await rows.count()):
        box = await rows.nth(index).bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box


@pytest.mark.browser
async def test_the_page_says_what_was_left_out_on_the_tab_it_belongs_to_at_1280_and_390(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with scratch_home() as home, eval_environment(home, {}):
        _home_skills(home)
        async with (
            _daemon(login_env, _config(), []) as (runtime, manager, _model),
            _served(runtime, manager) as served,
            async_playwright() as playwright,
        ):
            port: int = int(served.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for size in (_WIDE, _PHONE):
                    page: Page = await browser.new_page(viewport=size, reduced_motion="reduce")  # type: ignore[arg-type]
                    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    await page.goto(f"{served}/#/settings/skills")
                    # Personal: the owner's skill with no frontmatter, said in a sentence and in a closed fold.
                    await expect(page.locator("[data-left-out-hint]")).to_have_text(
                        "Dot could not read 1 skill, so no task is offered it. "
                        "Mend the file and the next task reads it again."
                    )
                    fold: Locator = await _fold(page, "personal")
                    rows: Locator = fold.locator(".list-row")
                    await expect(rows).to_have_count(1)
                    await expect(rows.first.locator(".row-title")).to_have_text("open-frontmatter")
                    await expect(rows.first.locator(".row-text")).to_have_text(_REASON)
                    await expect(rows.first.locator(".row-meta")).to_contain_text(
                        str(home / "skills" / "open-frontmatter" / "SKILL.md")
                    )
                    await expect(page.get_by_text("twilio-agent-connect")).to_have_count(0)
                    await _fits(page, rows)
                    # System: the plugin's skill whose file does not start with the --- line.
                    await page.get_by_role("button", name=re.compile(r"^System \d+$")).click()
                    system: Locator = await _fold(page, "system")
                    plugin_rows: Locator = system.locator(".list-row")
                    await expect(plugin_rows).to_have_count(1)
                    await expect(plugin_rows.first.locator(".row-title")).to_have_text("twilio-agent-connect")
                    await expect(plugin_rows.first.locator(".row-text")).to_have_text(_REASON)
                    await expect(page.get_by_text("open-frontmatter")).to_have_count(0)
                    await _fits(page, plugin_rows)
                    await page.close()
            finally:
                await browser.close()

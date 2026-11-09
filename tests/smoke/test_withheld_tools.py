"""What a run is not given as it starts (items 1063 and 1068), one rule for Dot's own loop and for a run on the owner's
Claude plan: the browser's tools while Settings › Browser keeps every run off it, the skill tool with no skill offered,
consulting with no helper that can answer, and the rest of `withheld_tools`. Both paths call it; this checks what it
gives for a home with the browser off, no skill and no helper. No model is called."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.loop import run_helpers, run_skills, withheld_tools
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.skills.types import SKILL_TOOL
from dotagent.store.models import RunRecord
from dotagent.tools.consult import CONSULT_TOOL
from dotagent.tools.groups import BROWSER_GROUP, group_of

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nenabled = false\nheadless = true\n\n[skills]\nelsewhere = false\n\n"
        f'[models]\nmain = "{SCRIPTED_MODEL}"\nhelpers = []\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def test_the_browser_off_no_skill_and_no_helper_withhold_their_tools(runtime: Runtime) -> None:
    thread = await runtime.store.create_thread("Withheld", "ui")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Look.",
        "task",
        SCRIPTED_MODEL,
        None,
        "ui",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
    )
    offered: frozenset[str] = frozenset(runtime.registry.tools)
    skills = await run_skills(runtime, run)
    helpers: tuple[str, ...] = await run_helpers(runtime, SCRIPTED_MODEL)
    withheld: frozenset[str] = await withheld_tools(runtime, run, [], offered, False, bool(skills.skills), helpers)
    browser: set[str] = {name for name in offered if group_of(name) == BROWSER_GROUP}
    assert browser and browser <= withheld
    assert (SKILL_TOOL in withheld) == (not skills.skills)
    assert (CONSULT_TOOL in withheld) == (not helpers)
    # What a run keeps whatever Dot holds.
    assert "read_file" not in withheld and "shell" not in withheld

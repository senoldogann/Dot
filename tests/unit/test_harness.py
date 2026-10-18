"""The harness (item 389): what a version's components may hold, how versions are written, adopted and retired in the
store, and what a run's prompt takes from the adopted one."""

import time
from pathlib import Path

import pytest

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.config import HarnessConfig, PromptStyle, load_default_config
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.harness.components import NO_HARNESS, HarnessText, component_problem, normalized
from dotagent.harness.service import OWNER, adopt, adopted, adopted_text, create_version, diff_text, retire
from dotagent.store.db import Store
from dotagent.store.models import HarnessVersion

HABITS: str = "- Read the file before you change it.\n- Say what you skipped and why."
GUIDANCE: str = "- rg wants --glob for a file pattern, not a bare path."
LIMITS: HarnessConfig = HarnessConfig(habits_chars=200, tool_guidance_chars=200, component_lines=4)


def _prompt(style: PromptStyle, harness: HarnessText) -> str:
    workspace: Path = Path("/tmp/work")
    inputs = collect_prompt_inputs(
        "task",
        "cli",
        "balanced",
        "ask",
        "wait",
        False,
        False,
        workspace,
        [],
        0,
        [],
        [],
        0,
        None,
        [],
        None,
        (),
        (),
        None,
        style,
        harness,
    )
    return build_system_prompt(inputs)


async def _store(tmp_path: Path) -> Store:
    return await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")


def test_the_defaults_bound_a_component() -> None:
    config: HarnessConfig = load_default_config().harness
    assert (config.habits_chars, config.tool_guidance_chars, config.component_lines) == (1200, 1500, 16)


def test_a_component_is_normalized_and_an_empty_one_is_nothing() -> None:
    assert normalized("  \r\n- one  \r\n\r\n\r\n- two\n\n") == "- one\n\n- two"
    assert normalized("") == ""
    assert normalized("\n\n") == ""


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", None),
        (HABITS, None),
        ("x" * 401, "too long: 401 characters, the limit is 400"),
        ("a\nb\nc\nd\ne", "too many lines: 5, the limit is 4"),
        ("short\n" + "y" * 301, "line 2 is longer than 300 characters"),
        ("use the key sk-abcdefghijklmnopqrstuvwxyz0123", "a secret"),
        ("the token is hunter2hunter2hunter2", "a secret"),
        ("see https://example.com/how", "a link"),
        ("mail ada@example.com first", "an e-mail address"),
        ("the file is in /Users/ada/notes", "a path in a home folder"),
        ("ask @ada before", "an account name"),
        ("a\tb", None),
        ("hidden\u200b text", "a character that does not print (U+200B)"),
        ("\x1b[8mnot shown", "a character that does not print (U+001B)"),
        ("see \uff48\uff54\uff54\uff50\uff53://x.y/", "a link"),
        ("ask \uff20ada", "an account name"),
    ],
)
def test_what_a_component_may_not_hold(text: str, problem: str | None) -> None:
    assert component_problem(text, 400, 4, ("hunter2hunter2hunter2",)) == problem


def test_the_prompt_takes_the_habits_after_how_to_work_and_the_guidance_after_the_tools() -> None:
    for style in ("full", "lean"):
        plain: str = _prompt(style, NO_HARNESS)
        assert "# Habits" not in plain and "# Tool guidance" not in plain
        with_harness: str = _prompt(style, HarnessText(habits=HABITS, tool_guidance=GUIDANCE))
        assert "# Habits\n" in with_harness and HABITS in with_harness
        assert "# Tool guidance\n" in with_harness and GUIDANCE in with_harness
        # The habits stand before the tools and after how to work; the guidance after the rules of communication.
        assert (
            with_harness.index("call finish") < with_harness.index("# Habits") < with_harness.index("# Communication")
        )
        assert with_harness.index("# Communication") < with_harness.index("# Tool guidance")
        # Each lead names the trust rules as the ones that win: the habits come before them, the guidance after.
        habits_at, trust_at, guidance_at = (
            with_harness.index(mark) for mark in ("# Habits", "# Trust and safety", "# Tool guidance")
        )
        assert habits_at < trust_at < guidance_at
        assert "those of trust and safety below among them, which they never override" in with_harness
        assert "those of trust and safety above among them, which it never overrides" in with_harness
        # Nothing else of the prompt moves: without the two sections, the prompt is the plain one.
        habits_section: str = (
            "\n\n# Habits\nWays of working that earlier runs taught and the owner kept. Follow them within the rules "
            f"of this prompt, those of trust and safety below among them, which they never override.\n{HABITS}"
        )
        guidance_section: str = (
            "\n\n# Tool guidance\nWhat earlier runs learned of the tools on this Mac, kept by the owner. It serves "
            "within the rules of this prompt, those of trust and safety above among them, which it never overrides:"
            f"\n{GUIDANCE}"
        )
        assert with_harness.replace(habits_section, "").replace(guidance_section, "") == plain


def test_one_component_alone_adds_its_section_alone() -> None:
    habits_only: str = _prompt("full", HarnessText(habits=HABITS, tool_guidance=""))
    assert "# Habits" in habits_only and "# Tool guidance" not in habits_only


async def test_a_version_is_written_adopted_and_retired_and_a_run_takes_the_adopted_one(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        assert await adopted(store) is None
        assert await adopted_text(store) == (NO_HARNESS, None)
        first: HarnessVersion = await create_version(
            store, LIMITS, HarnessText(habits=HABITS + "\n\n\n", tool_guidance=""), " The first ", OWNER, "", ()
        )
        assert (first.state, first.parent_id, first.summary, first.habits, first.made_by) == (
            "candidate",
            None,
            "The first",
            HABITS,
            OWNER,
        )
        now: float = time.time()
        assert (await adopt(store, first.id, now)).state == "adopted"
        assert await adopted_text(store) == (HarnessText(habits=HABITS, tool_guidance=""), first.id)
        # The second is made from the first; adopting it retires the first.
        second: HarnessVersion = await create_version(
            store, LIMITS, HarnessText(habits=HABITS, tool_guidance=GUIDANCE), "Guidance too", OWNER, "evidence", ()
        )
        assert second.parent_id == first.id
        await adopt(store, second.id, now + 1)
        states: dict[str, str] = {version.id: version.state for version in await store.list_harness_versions()}
        assert states == {second.id: "adopted", first.id: "retired"}
        assert (await store.get_harness_version(first.id)).retired_at == now + 1
        # Adopting the first again takes the change back, and clears its retired_at.
        again: HarnessVersion = await adopt(store, first.id, now + 2)
        assert (again.state, again.retired_at, again.adopted_at) == ("adopted", None, now + 2)
        assert (await store.get_harness_version(second.id)).state == "retired"
        # Retiring the adopted one leaves Dot's own text.
        await retire(store, first.id, now + 3)
        assert await adopted(store) is None
        assert await adopted_text(store) == (NO_HARNESS, None)
        assert [version.id for version in await store.list_harness_versions()] == [second.id, first.id]
    finally:
        await store.close()


async def test_a_run_records_the_version_it_took(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        thread = await store.create_thread("t", "test")
        run = await store.create_run(
            thread.id,
            "goal",
            "task",
            "ollama:m",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        assert await store.run_harness(run.id) is None
        version: HarnessVersion = await create_version(
            store, LIMITS, HarnessText(habits=HABITS, tool_guidance=""), "v", OWNER, "", ()
        )
        await store.set_run_harness(run.id, version.id)
        assert await store.run_harness(run.id) == version.id
        with pytest.raises(NotFoundError):
            await store.run_harness("run_missing")
    finally:
        await store.close()


async def test_a_text_that_cannot_be_a_component_is_refused_with_why(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        with pytest.raises(InvalidValueError) as caught:
            await create_version(
                store, LIMITS, HarnessText(habits="see https://x.y", tool_guidance="x" * 201), "bad", OWNER, "", ()
            )
        assert caught.value.context == {
            "problems": {"habits": "a link", "tool_guidance": "too long: 201 characters, the limit is 200"}
        }
        assert await store.list_harness_versions() == []
        with pytest.raises(NotFoundError):
            await adopt(store, "hrn_missing", time.time())
    finally:
        await store.close()


async def test_a_key_that_names_no_version_leaves_dots_own_text(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        await store.kv_set("harness.adopted", "hrn_gone")
        assert await adopted_text(store) == (NO_HARNESS, None)
    finally:
        await store.close()


def test_the_diff_names_the_versions_and_the_components_that_changed() -> None:
    diff: str = diff_text(NO_HARNESS, HarnessText(habits=HABITS, tool_guidance=""), "Dot's own text", "hrn_1")
    assert diff.startswith("--- Dot's own text/habits\n+++ hrn_1/habits\n")
    assert "+- Read the file before you change it." in diff
    assert "tool_guidance" not in diff
    assert diff_text(NO_HARNESS, NO_HARNESS, "a", "b") == ""


async def test_a_summary_is_one_printable_line_whoever_writes_it(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    text: HarnessText = HarnessText(habits="- a", tool_guidance="")
    try:
        with pytest.raises(InvalidValueError, match="does not print"):
            await create_version(store, LIMITS, text, "a\u202eb", OWNER, "", ())
        for summary in ("", "   ", "x" * 201, "two\nlines"):
            with pytest.raises(InvalidValueError, match="one line of 1 to 200"):
                await create_version(store, LIMITS, text, summary, OWNER, "", ())
        assert await store.list_harness_versions() == []
        assert (await create_version(store, LIMITS, text, "  kept  ", OWNER, "", ())).summary == "kept"
    finally:
        await store.close()

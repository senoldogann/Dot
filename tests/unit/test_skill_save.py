"""``skill_save`` (item 893): how the owner told a run to do a kind of work, written as a taught skill every later run
reads; never from a run that read content others can write, nor in a chat Dot opened on its own, and with no secret
Dot holds in it. Told again, the skill is rewritten as a new version and the one before is kept."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dotagent.agent.playbooks import VERSIONS_DIR, list_playbooks, retires, save_said_skill
from dotagent.agent.review import PlaybookDraft
from dotagent.core.errors import InvalidValueError
from dotagent.proactive import INITIATIVE_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools import skill_save as skill_save_module
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.groups import group_of
from dotagent.tools.skill_save import SKILL_SAVE_TOOL, build_skill_save_tool

_HELD: str = "hk_live_9d8c7b6a5f4e3d2c1b0a"
_ARGS: dict[str, Any] = {
    "kind": "weekly invoice check",
    "description": "Check the invoice portal for new bills and list them for the owner.",
    "steps": ["Open <portal> and sign in with the saved login.", f"Use the key {_HELD} for the export."],
}


class _Store:
    def __init__(self, opened: str | None, chat_tainted: bool) -> None:
        self._opened = opened
        self._chat_tainted = chat_tainted

    async def opened_by(self, _thread_id: str) -> str | None:
        return self._opened

    async def chat_ever_tainted(self, _thread_id: str) -> bool:
        return self._chat_tainted


def _context(
    tainted: bool = False,
    opened: str | None = None,
    agent_id: str | None = None,
    ever: bool = False,
    chat_tainted: bool = False,
) -> ToolContext:
    return ToolContext(
        runtime=cast(Any, SimpleNamespace(store=_Store(opened, chat_tainted))),
        browser=cast(Any, None),
        run_id="run_test",
        agent_id=agent_id,
        thread_id="thread_test",
        mode="chat",
        approvals="auto",
        questions="leave",
        workspace=Path("/tmp"),
        gate=cast(Any, None),
        cancel=asyncio.Event(),
        output_chars=4000,
        facts=cast(Any, lambda: None),
        tainted=lambda: tainted,
        ever_tainted=lambda: ever or tainted,
        user_text=lambda: f"from now on, list the bills by due date; the key is {_HELD}",
    )


async def _call(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    prepared = await build_skill_save_tool().prepare(arguments, ctx)
    return await prepared.run(ctx)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(skill_save_module, "playbooks_dir", lambda: tmp_path)
    monkeypatch.setattr(skill_save_module, "known_secrets", lambda filled: (_HELD,))
    return tmp_path


def test_the_owners_steps_are_saved_as_a_taught_skill_with_no_secret_in_it(root: Path) -> None:
    result: ToolResult = asyncio.run(_call(_ARGS, _context(agent_id="agt_books")))
    assert not result.is_error, result.text
    assert "taught-weekly-invoice-check" in result.text
    text: str = (root / "taught-weekly-invoice-check" / "SKILL.md").read_text(encoding="utf-8")
    assert "in their own words in a chat" in text and "showing the task once" not in text
    assert "Open <portal> and sign in" in text
    assert _HELD not in text
    [entry] = list_playbooks(root)
    assert (entry.meta.taught, entry.meta.said, entry.meta.tainted, entry.meta.agent_id) == (
        True,
        True,
        False,
        "agt_books",
    )
    # The owner's to keep: failures never retire it.
    assert retires(entry.meta.model_copy(update={"failing": 99})) is False


def test_told_again_the_skill_is_rewritten_as_a_new_version_and_the_old_one_kept(root: Path) -> None:
    asyncio.run(_call(_ARGS, _context()))
    changed: dict[str, Any] = {**_ARGS, "steps": ["Open <portal>.", "List the bills by due date."]}
    result: ToolResult = asyncio.run(_call({**changed, "replace": "taught-weekly-invoice-check"}, _context()))
    assert not result.is_error, result.text
    folder: Path = root / "taught-weekly-invoice-check"
    assert "List the bills by due date." in (folder / "SKILL.md").read_text(encoding="utf-8")
    [entry] = list_playbooks(root)
    assert entry.meta.version == 2
    assert entry.meta.versions[-1].change == "revised"
    assert (entry.meta.versions[-1].words or "").startswith("from now on, list the bills by due date")
    assert _HELD not in (entry.meta.versions[-1].words or "")
    assert entry.meta.said
    assert (folder / VERSIONS_DIR / "1.md").is_file()


def test_a_run_that_read_content_others_can_write_saves_no_skill(root: Path) -> None:
    result: ToolResult = asyncio.run(_call(_ARGS, _context(tainted=True)))
    assert result.is_error and "no skill was saved" in result.text
    assert list_playbooks(root) == ()


def test_a_run_whose_taint_the_owners_next_message_cleared_saves_no_skill_either(root: Path) -> None:
    """The pre-push review: the owner's "go on" clears the run's taint, not the page's words in its context."""
    result: ToolResult = asyncio.run(_call(_ARGS, _context(tainted=False, ever=True)))
    assert result.is_error and "no skill was saved" in result.text
    assert list_playbooks(root) == ()


def test_a_chat_an_earlier_run_of_which_read_such_content_saves_no_skill(root: Path) -> None:
    """The pre-push review: the owner's next message starts a clean run, and the page's words are in the chat."""
    result: ToolResult = asyncio.run(_call(_ARGS, _context(chat_tainted=True)))
    assert result.is_error and "no skill was saved" in result.text
    assert list_playbooks(root) == ()


async def test_the_store_says_a_chat_read_such_content_once_any_of_its_runs_did(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        chat: ThreadRecord = await store.create_thread("t", "test")
        other: ThreadRecord = await store.create_thread("u", "test")
        run: RunRecord = await store.create_run(
            chat.id, "goal", "chat", "ollama:m", None, "test", None, "standard", None, "ask", False, None, None, None
        )
        later: RunRecord = await store.create_run(
            chat.id, "go on", "chat", "ollama:m", None, "test", None, "standard", None, "ask", False, None, None, None
        )
        await store.add_event(run.id, "tool_result", {"tool": "web_fetch", "ok": True, "tainted": False})
        assert not await store.chat_ever_tainted(chat.id)
        await store.add_event(run.id, "tool_result", {"tool": "web_fetch", "ok": True, "tainted": True})
        # The owner's next message clears the run's taint, not what it read.
        await store.add_event(later.id, "steer", {"text": "go on", "tainted": False})
        assert await store.chat_ever_tainted(chat.id)
        assert not await store.chat_ever_tainted(other.id)
    finally:
        await store.close()


def test_two_saves_of_one_kind_take_two_names(root: Path) -> None:
    draft: PlaybookDraft = PlaybookDraft(kind="weekly invoice check", description="d", when="w", steps=["s"])
    assert save_said_skill(root, draft, None, 1.7e9) == "taught-weekly-invoice-check"
    assert save_said_skill(root, draft, None, 1.7e9) == "taught-weekly-invoice-check-2"


def test_a_chat_dot_opened_on_its_own_saves_no_skill(root: Path) -> None:
    result: ToolResult = asyncio.run(_call(_ARGS, _context(opened=INITIATIVE_SOURCE)))
    assert result.is_error and "opened on its own" in result.text
    assert list_playbooks(root) == ()


def test_only_a_said_skill_of_the_same_agent_is_rewritten(root: Path) -> None:
    draft: PlaybookDraft = PlaybookDraft(
        kind="post a weekly digest", description="d", when="w", steps=["s"], parameters=[]
    )
    name: str = save_said_skill(root, draft, "agt_one", 1.7e9)
    with pytest.raises(InvalidValueError):
        asyncio.run(_call({**_ARGS, "replace": name}, _context(agent_id="agt_two")))
    with pytest.raises(InvalidValueError):
        asyncio.run(_call({**_ARGS, "replace": name}, _context(agent_id=None)))


def test_it_is_a_tool_of_its_own_group_that_writes_like_a_note() -> None:
    assert group_of(SKILL_SAVE_TOOL) == "skills"
    prepared = asyncio.run(build_skill_save_tool().prepare(_ARGS, _context()))
    assert (prepared.action.kind, prepared.action.risk) == ("memory_write", "low")

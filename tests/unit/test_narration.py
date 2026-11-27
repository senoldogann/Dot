"""The run told as a conversation (item 1976): which calls get a line of their own, what a line names, and that the line
is an event beside the call, made from the run's newest lines in the store."""

import asyncio
from pathlib import Path

from dotagent.agent.narration import NARRATION_EVENT, Narration, SameCall, narration_of, opencode_call, tell_call
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import EventRecord

KEY: str = "sk-ant-api03-" + "a" * 90


def _line(tool: str, args: dict[str, str]) -> Narration:
    return Narration(call_id="c0", tool=tool, args=dict(args))


def test_a_call_that_turns_the_run_to_other_work_is_told_and_one_that_goes_on_with_it_is_not() -> None:
    first: Narration | None = narration_of("c1", "read_file", {"path": "/a/b/README.md"}, [], ())
    assert first == Narration(call_id="c1", tool="read_file", args={"path": "README.md"})
    # More reading, of another file: the same work.
    assert narration_of("c2", "read_file", {"path": "/a/b/notes.md"}, [first], ()) is None
    # A command after it is other work, and a read after that goes back to work one of the newest lines told.
    command: Narration | None = narration_of("c3", "shell", {"command": "swift test"}, [first], ())
    assert command == Narration(call_id="c3", tool="shell", args={})
    assert narration_of("c4", "list_dir", {"path": "/a"}, [first, command], ()) is None


def test_a_search_of_other_words_and_a_page_on_another_site_are_news_and_more_of_the_same_site_is_not() -> None:
    search: Narration = _line("web_search", {"query": "gistsmith contact"})
    assert narration_of("c1", "web_search", {"query": "gistsmith contact"}, [search], ()) is None
    assert narration_of("c2", "web_search", {"query": "gistsmith pricing"}, [search], ()) is not None
    page: Narration | None = narration_of(
        "c3", "browser_open", {"url": "https://www.gistsmith.com/contact?x=1"}, [], ()
    )
    assert page == Narration(call_id="c3", tool="browser_open", args={"url": "https://www.gistsmith.com"})
    assert narration_of("c4", "browser_act", {"ref": "e12"}, [page], ()) is None
    assert narration_of("c5", "web_fetch", {"url": "https://www.gistsmith.com/about"}, [page], ()) is None
    assert narration_of("c6", "web_fetch", {"url": "https://example.com/"}, [page], ()) is not None


def test_a_folder_with_no_name_of_its_own_is_not_named() -> None:
    assert narration_of("c1", "list_dir", {"path": "."}, [], ()) == Narration(call_id="c1", tool="list_dir", args={})


def test_the_runs_own_bookkeeping_is_never_told() -> None:
    for tool in ("finish", "update_plan", "mark_plan_step", "use_tools", "ask_user", "react"):
        assert narration_of("c1", tool, {}, [], ()) is None


def test_a_line_names_no_command_and_masks_a_secret_in_what_it_names() -> None:
    assert narration_of("c1", "shell", {"command": f"curl -H 'x-api-key: {KEY}' https://x"}, [], ()) == Narration(
        call_id="c1", tool="shell", args={}
    )
    held: str = "hunter2-very-secret"
    line: Narration | None = narration_of("c2", "web_search", {"query": f"login {held}"}, [], (held,))
    assert line is not None
    assert held not in str(line.args)


async def test_the_line_is_an_event_beside_the_call_read_against_the_runs_newest_lines(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        bus: EventBus = EventBus(store)
        await tell_call(bus, store, "run_a", "c1", "web_search", {"query": "gistsmith"}, ())
        await tell_call(bus, store, "run_a", "c2", "web_search", {"query": "gistsmith"}, ())
        await tell_call(bus, store, "run_a", "c3", "read_file", {"path": "x.md"}, ())
        # Another run's lines are its own.
        await tell_call(bus, store, "run_b", "c4", "read_file", {"path": "x.md"}, ())
        lines: list[EventRecord] = await store.events_of_kind("run_a", NARRATION_EVENT, 10)
        assert [event.data for event in lines] == [
            {"call_id": "c1", "tool": "web_search", "args": {"query": "gistsmith"}},
            {"call_id": "c3", "tool": "read_file", "args": {"path": "x.md"}},
        ]
        assert len(await store.events_of_kind("run_b", NARRATION_EVENT, 10)) == 1
    finally:
        await store.close()


async def test_the_calls_of_one_step_that_run_at_once_are_one_line(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        bus: EventBus = EventBus(store)
        await asyncio.gather(
            *(tell_call(bus, store, "run_a", f"c{n}", "read_file", {"path": f"{n}.md"}, ()) for n in range(3))
        )
        assert len(await store.events_of_kind("run_a", NARRATION_EVENT, 10)) == 1
    finally:
        await store.close()


def test_opencodes_own_tools_are_told_as_the_dot_tool_that_does_the_same() -> None:
    assert opencode_call("read", {"filePath": "/w/notes.md"}) == SameCall(
        tool="read_file", args={"path": "/w/notes.md"}
    )
    assert opencode_call("bash", {"command": "ls"}) == SameCall(tool="shell", args={})
    assert opencode_call("webfetch", {"url": "https://a.org/x"}) == SameCall(
        tool="web_fetch", args={"url": "https://a.org/x"}
    )
    assert opencode_call("todowrite", {}) is None
    same: SameCall | None = opencode_call("read", {"filePath": "/w/notes.md"})
    assert same is not None
    assert narration_of("c1", same.tool, same.args, [], ()) == Narration(
        call_id="c1", tool="read_file", args={"path": "notes.md"}
    )

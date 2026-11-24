"""The steps of a run that acted with a tool the undo journal does not hold (item 1404): undo covers `write_file`,
`edit_file` and `write_notes` only, so a run's summary says how many steps ran with a shell, the browser, the screen or
a message, by what its `tool_result` events record, and nothing of what they did. A fixture run with a helper."""

from pathlib import Path

from dotagent.server.actions import outside_steps
from dotagent.server.views import OutsideSteps
from dotagent.store.db import Store
from dotagent.store.models import RanStep, RunRecord

_ALLOWED: dict[str, object] = {
    "verdict": "allow",
    "source": "mode",
    "reason": "the mode lets it through",
    "rule_id": None,
    "answered_by": "mode",
    "preset": "balanced",
}


def _step(kind: str, *flags: str, steps: int = 1) -> RanStep:
    return RanStep(kind=kind, flags=flags, steps=steps)


def test_steps_are_counted_by_the_kind_of_tool_and_only_the_kinds_that_have_any() -> None:
    ran: list[RanStep] = [
        _step("shell"),
        _step("shell"),
        _step("browser_click", steps=4),
        _step("browser_input"),
        _step("desktop_input"),
        _step("app_control"),
        _step("mcp"),
    ]
    assert outside_steps(ran) == [
        OutsideSteps(category="shell", steps=2),
        OutsideSteps(category="browser", steps=5),
        OutsideSteps(category="desktop", steps=2),
        OutsideSteps(category="app", steps=1),
    ]


def test_a_step_that_sends_a_message_is_a_message_whatever_tool_it_went_through() -> None:
    ran: list[RanStep] = [_step("mcp", "external_send", "message_send"), _step("shell", "message_send"), _step("mcp")]
    assert outside_steps(ran) == [OutsideSteps(category="message", steps=2), OutsideSteps(category="app", steps=1)]


def test_what_only_reads_or_is_in_the_journal_or_is_dots_own_is_not_counted() -> None:
    ran: list[RanStep] = [
        _step("read"),
        _step("write"),
        _step("notes_write"),
        _step("network"),
        _step("browser_read"),
        _step("browser_navigate"),
        _step("desktop_observe"),
        _step("memory_read"),
        _step("memory_write"),
        _step("mcp_read"),
        _step("schedule"),
        _step("delegate"),
        _step("meta"),
        _step(""),
    ]
    assert outside_steps(ran) == []
    assert outside_steps([]) == []


async def _result(store: Store, run: RunRecord, tool: str, kind: str, **more: object) -> None:
    data: dict[str, object] = {
        "call_id": "c",
        "tool": tool,
        "ok": True,
        "kind": kind,
        "flags": [],
        "decision": _ALLOWED,
        **more,
    }
    await store.add_event(run.id, "tool_result", data)  # type: ignore[arg-type]


async def test_the_store_reads_the_steps_the_gate_let_run_of_a_run_and_of_the_helper_it_delegated_to(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread = await store.create_thread("A task", "ui")
        run: RunRecord = await store.create_run(
            thread.id, "goal", "task", "ollama:m", None, "ui", None, "standard", None, "ask", False, None, None, None
        )
        helper: RunRecord = await store.create_run(
            thread.id, "part", "task", "ollama:m", run.id, "ui", None, "standard", None, "ask", False, None, None, None
        )
        other_thread = await store.create_thread("Another task", "ui")
        elsewhere: RunRecord = await store.create_run(
            other_thread.id, "x", "task", "ollama:m", None, "ui", None, "standard", None, "ask", False, None, None, None
        )
        await _result(store, run, "shell", "shell")
        await _result(store, run, "read_file", "read")
        await _result(store, run, "browser_act", "browser_click", steps=3)
        await _result(store, run, "write_file", "write")
        # A call the gate refused carries no decision: it never ran, and is not counted.
        await store.add_event(
            run.id,
            "tool_result",
            {"call_id": "r", "tool": "shell", "ok": False, "kind": "shell", "flags": ["destructive"], "decision": None},
        )
        await _result(store, helper, "shell", "shell")
        await _result(store, helper, "send_email", "mcp", flags=["external_send", "message_send"])
        # A result recorded before steps and flags were kept still counts as one step.
        await store.add_event(
            helper.id,
            "tool_result",
            {"call_id": "o", "tool": "desktop_act", "kind": "desktop_input", "decision": _ALLOWED},
        )
        await _result(store, elsewhere, "shell", "shell")
        ran: list[RanStep] = await store.ran_steps(run.id)
        assert [(step.kind, step.flags, step.steps) for step in ran] == [
            ("shell", (), 1),
            ("read", (), 1),
            ("browser_click", (), 3),
            ("write", (), 1),
            ("shell", (), 1),
            ("mcp", ("external_send", "message_send"), 1),
            ("desktop_input", (), 1),
        ]
        assert outside_steps(ran) == [
            OutsideSteps(category="shell", steps=2),
            OutsideSteps(category="browser", steps=3),
            OutsideSteps(category="desktop", steps=1),
            OutsideSteps(category="message", steps=1),
        ]
        # The helper's own view counts its own steps; a run of another chat is never counted.
        assert [step.kind for step in await store.ran_steps(helper.id)] == ["shell", "mcp", "desktop_input"]
        assert await store.ran_steps("run_missing") == []
    finally:
        await store.close()

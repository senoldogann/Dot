"""The context of a chat's earlier turns (item 650): the tool calls of the turns before a run that succeeded, oldest
first, with their arguments cut for the panel; a memory change keeps what its result says; the run's own calls, a
failed call, a rewound turn and a delegated run's calls are not among them."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from dotagent.server.chatcontext import ChatContext, _steps, chat_context
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ToolStep

LONG_ADDRESS: str = "https://example.org/search?q=" + "a" * 600


def _run(connection: sqlite3.Connection, run_id: str, created: float, parent: str | None = None) -> None:
    connection.execute(
        "INSERT INTO runs(id, thread_id, goal, mode, model, status, created_at, finished_at, result, steps,"
        " input_tokens, output_tokens, parent_run_id, source) VALUES(?, 'thr_1', 'g', 'chat', 'm', 'done', ?, ?, '', 1,"
        " 0, 0, ?, 'chat')",
        (run_id, created, created + 1, parent),
    )


def _call(
    connection: sqlite3.Connection,
    run_id: str,
    call_id: str,
    tool: str,
    args: dict[str, object],
    ok: bool,
    kind: str = "read",
    text: str = "",
    summary: str = "",
) -> None:
    connection.execute(
        "INSERT INTO events(run_id, ts, kind, data) VALUES(?, 0, 'tool_call', ?)",
        (run_id, json.dumps({"call_id": call_id, "tool": tool, "args": args})),
    )
    connection.execute(
        "INSERT INTO events(run_id, ts, kind, data) VALUES(?, 0, 'tool_result', ?)",
        (
            run_id,
            json.dumps({"call_id": call_id, "tool": tool, "ok": ok, "kind": kind, "text": text, "summary": summary}),
        ),
    )


async def test_a_run_gets_the_calls_its_chats_earlier_turns_made_and_not_its_own(tmp_path: Path) -> None:
    path: Path = tmp_path / "dot.sqlite3"
    await (await Store.open(path, tmp_path / "screenshots")).close()
    with closing(sqlite3.connect(path)) as connection:
        _run(connection, "run_1", 1.0)
        _call(connection, "run_1", "c1", "web_search", {"query": "dot agent " + "x" * 1000}, True)
        _call(connection, "run_1", "c2", "web_fetch", {"url": "https://example.org/missing"}, False)
        _call(
            connection,
            "run_1",
            "c3",
            "memory_save",
            {"content": "The user prefers short answers."},
            True,
            "memory_write",
            "Saved: [mem_a] The user prefers short answers.",
            "Save: The user…",
        )
        _run(connection, "run_1_helper", 1.5, parent="run_1")
        _call(connection, "run_1_helper", "h1", "web_fetch", {"url": "https://example.org/helper"}, True)
        # A turn a rewind took out of the chat (item 282): none of its calls.
        _run(connection, "run_rewound", 1.8)
        connection.execute("UPDATE runs SET rewound_at = 1.9 WHERE id = 'run_rewound'")
        _call(connection, "run_rewound", "r1", "web_fetch", {"url": "https://example.org/rewound"}, True)
        _run(connection, "run_2", 2.0)
        _call(connection, "run_2", "d0", "web_fetch", {"url": LONG_ADDRESS}, True)
        _call(connection, "run_2", "d1", "skill", {"name": "pdf"}, True, "read", "the skill's whole text")
        _run(connection, "run_3", 3.0)
        _call(connection, "run_3", "e1", "web_fetch", {"url": "https://example.org/own"}, True)
        connection.commit()
    store: Store = await Store.open(path, tmp_path / "screenshots")
    try:
        third: RunRecord = await store.get_run("run_3")
        seen: ChatContext = await chat_context(store, third)
        assert [(step.tool, step.kind) for step in seen.steps] == [
            ("web_search", "read"),
            ("memory_save", "memory_write"),
            ("web_fetch", "read"),
            ("skill", "read"),
        ]
        search, memory, page, skill = seen.steps
        # An address is kept whole, as the panel's row opens it.
        assert page.args["url"] == LONG_ADDRESS
        assert search.args["query"] == ("dot agent " + "x" * 1000)[:300]
        assert memory.text == "Saved: [mem_a] The user prefers short answers." and memory.summary == "Save: The user…"
        # A result that changed no note keeps none of its text.
        assert skill.text == "" and skill.args == {"name": "pdf"}
        # The first turn has no turn before it, and a delegated run's page is its own.
        assert (await chat_context(store, await store.get_run("run_1"))).steps == ()
        assert (await chat_context(store, await store.get_run("run_1_helper"))).steps == ()
    finally:
        await store.close()


def test_a_call_and_a_result_with_no_call_id_pair_with_nothing() -> None:
    blank: dict[str, object] = {"run_id": "run_1", "call_id": "", "tool": "web_fetch", "text": "", "summary": ""}
    rows: list[ToolStep] = [
        ToolStep(**blank, kind="tool_call", args={"url": "https://example.org"}, ok=False, result_kind=""),
        ToolStep(**blank, kind="tool_result", args={}, ok=True, result_kind="read"),
    ]
    assert _steps(rows) == []

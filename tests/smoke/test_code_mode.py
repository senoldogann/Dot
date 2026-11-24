"""Code mode, whole (items 1501 to 1503): a scripted model calls ``code_run``, whose code calls three of Dot's real
tools and loops once, in a real runtime with its real gate, policy, store, events, undo journal and sandbox.

What it shows that the unit tests (stand-in gate and tools) do not: the owner's approvals reach the owner for the code
and for each change the code makes, a step the owner refuses is refused in the code, the writes are in the undo
journal, the two events of each call say who made it, the model reads what the code printed and nothing else, and a
research run has no ``code_run``. macOS only: the code runs in the kernel's sandbox.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord
from dotagent.undo.journal import read_journal, run_journal

pytestmark = [pytest.mark.macos, pytest.mark.asyncio]

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider

# Three tools, a loop and a filter: it writes two files, lists the folder, reads both back, and writes what it found.
CODE: str = """\
from dot_tools.servers.dot import list_dir, read_file, write_file

names = ["alpha.txt", "beta.txt"]
for name in names:
    write_file(path=f"data/{name}", content=(name + " says hello\\n") * 3)
listing = list_dir(path="data")
total = 0
for name in names:
    total += len(read_file(path=f"data/{name}").splitlines())
write_file(path="data/summary.txt", content=f"{total} lines in {len(names)} files\\n")
print(total, "lines in", len(names), "files; the folder lists", listing.count("txt"), "files")
"""


def _call(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


def _code(code: str = CODE) -> ScriptedTurn:
    return _call("code_run", code=code, title="Write, list and read back two files")


_FINISH: ScriptedTurn = _call("finish", answer="The files are written and counted.", success=True)


class Played:
    """A run that ended, and what it left: its events, the messages of its chat, its undo journal and a snapshot of its
    workspace (the folder is removed as the run's home is): the files by their paths from it, a folder as ``None``."""

    def __init__(
        self,
        run: RunRecord,
        events: list[EventRecord],
        messages: list[str],
        journal: list[str],
        snapshot: dict[str, bytes | None],
    ) -> None:
        self.run: RunRecord = run
        self.events: list[EventRecord] = events
        self.messages: list[str] = messages
        self.journal: list[str] = journal
        self._snapshot: dict[str, bytes | None] = snapshot

    def of(self, kind: str) -> list[EventRecord]:
        return [event for event in self.events if event.kind == kind]

    def inner(self, kind: str) -> list[EventRecord]:
        """The events of ``kind`` of the calls a code made."""
        return [event for event in self.of(kind) if event.data.get("via") == "code"]

    def text(self, *parts: str) -> str:
        data: bytes | None = self._snapshot["/".join(parts)]
        assert data is not None, f"{'/'.join(parts)} is a folder"
        return data.decode("utf-8")

    def has(self, *parts: str) -> bool:
        return "/".join(parts) in self._snapshot

    def names(self, *parts: str) -> list[str]:
        prefix: str = "/".join(parts) + "/"
        return sorted(
            {path.removeprefix(prefix).split("/", 1)[0] for path in self._snapshot if path.startswith(prefix)}
        )


def _snapshot(workspace: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(workspace)): path.read_bytes() if path.is_file() else None
        for path in sorted(workspace.rglob("*"))
    }


async def _answering(runtime: Runtime, run_id: str, decide: Callable[[str], bool], asked: list[str]) -> None:
    """Answer each question the run puts to the owner, by what it says it is about, until the run ends."""
    answered: set[str] = set()
    while True:
        for approval in await runtime.store.list_approvals(None, 50):
            if approval.run_id == run_id and approval.status == "pending" and approval.id not in answered:
                answered.add(approval.id)
                asked.append(approval.summary)
                await runtime.approvals.resolve(approval.id, decide(approval.summary), UI_SOURCE, "")
        await asyncio.sleep(0.05)


async def _play(
    turns: list[ScriptedTurn],
    *,
    approvals: str,
    mode: str = "task",
    decide: Callable[[str], bool] = lambda summary: True,
    asked: list[str] | None = None,
) -> Played:
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(update={"shell": base.shell.model_copy(update={"sandbox": False})})
    questions: list[str] = [] if asked is None else asked
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, {})
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
            run: RunRecord = await manager.start(
                "Count the lines of two files.", mode, SCRIPTED_MODEL, None, "ui", None, None, "standard", None,
                approvals, False, (),
            )  # fmt: skip
            owner: asyncio.Task[None] = asyncio.create_task(_answering(runtime, run.id, decide, questions))
            try:
                ended: RunRecord = await asyncio.wait_for(manager.wait(run.id), timeout=180)
            finally:
                owner.cancel()
            events: list[EventRecord] = await runtime.store.list_events(run.id, 0, 10_000)
            thread = await runtime.store.load_messages(run.thread_id, 0)
            messages: list[str] = [message.content for message in thread if message.role == "tool"]
            journal: list[str] = [str(entry.path) for entry in read_journal(run_journal(runtime.undo_dir, run.id))]
            return Played(ended, events, messages, journal, _snapshot(workspace_dir()))
        finally:
            await manager.shutdown()
            await close_runtime(runtime)


async def test_a_scripted_model_writes_code_that_calls_three_tools_and_loops_once() -> None:
    played: Played = await _play([_code(), _FINISH], approvals="ask")
    assert (played.run.status, played.run.result) == ("done", "The files are written and counted.")
    # The model read what the code printed, and one result for its one call: the calls of the code are not its messages.
    assert len(played.messages) == 2 and played.messages[0].startswith("8 lines in 2 files; the folder lists 2 files")
    assert played.messages[0].splitlines()[-1].startswith("[6 tool calls, ")
    # The two events of each call, whose are the code's and whose the model's.
    code_call: EventRecord = next(event for event in played.of("tool_call") if event.data["tool"] == "code_run")
    assert "via" not in code_call.data
    nested: list[EventRecord] = played.inner("tool_call")
    assert [event.data["tool"] for event in nested] == [
        "write_file", "write_file", "list_dir", "read_file", "read_file", "write_file",
    ]  # fmt: skip
    assert {event.data["parent_call_id"] for event in nested} == {code_call.data["call_id"]}
    assert [event.data["call_id"] for event in nested] == [f"{code_call.data['call_id']}.{n}" for n in range(1, 7)]
    assert [event.data["ok"] for event in played.inner("tool_result")] == [True] * 6
    outer: EventRecord = next(event for event in played.of("tool_result") if event.data["tool"] == "code_run")
    assert (outer.data["kind"], outer.data["risk"], outer.data["ok"]) == ("shell", "medium", True)
    # What the code did is on the disk and in the undo journal, where a write of the model's own would be.
    assert played.text("data", "summary.txt") == "8 lines in 2 files\n"
    assert played.names("data") == ["alpha.txt", "beta.txt", "summary.txt"]
    assert sorted(Path(path).name for path in played.journal) == ["alpha.txt", "beta.txt", "summary.txt"]
    # The folders of the run's code are in its workspace, its code is kept, and nothing of it is the owner's.
    where: tuple[str, ...] = ("code", played.run.id, "tools")
    assert played.text(*where, "snippets", "1.py") == CODE
    assert played.has(*where, "dot_tools", "servers", "dot", "read_file.py")
    assert not played.has(*where, "dot_tools", "servers", "dot", "code_run.py")


async def test_the_owner_is_asked_for_the_code_and_for_each_change_it_makes_and_a_no_is_a_refusal_in_the_code() -> None:
    asked: list[str] = []
    # They say yes to the code and to the two files, and no to the summary: the third write of the code.
    played: Played = await _play(
        [_code(), _FINISH], approvals="supervised", decide=lambda summary: "summary.txt" not in summary, asked=asked
    )
    assert played.run.status == "done"
    assert asked[0].startswith("Run code: from dot_tools.servers.dot import list_dir, read_file, write_file")
    writes: list[str] = [summary for summary in asked if summary.startswith("Write")]
    assert any("alpha.txt" in summary for summary in writes) and any("summary.txt" in summary for summary in writes)
    # A question about a step of the code is the owner's as any other: it names the step, not the code.
    assert all("dot_tools" not in summary for summary in writes)
    result: str = played.messages[0]
    assert result.startswith("The code failed (exit 1)") and "ToolRefused" in result
    assert "Not allowed:" in result or "not allowed" in result.lower()
    assert not played.has("data", "summary.txt") and played.has("data", "alpha.txt")
    refused: list[EventRecord] = [event for event in played.inner("tool_result") if not event.data["ok"]]
    assert [event.data["tool"] for event in refused] == ["write_file"]


async def test_a_research_run_has_no_code_run_and_so_no_code_to_write() -> None:
    played: Played = await _play(
        [_code(), _call("finish", answer="No code was run.", success=True)], approvals="ask", mode="research"
    )
    assert played.messages[0].startswith("Tool 'code_run' is not available in research mode")
    assert not played.has("data") and not played.has("code")


async def test_the_model_reads_only_what_the_code_prints_however_much_the_tools_gave_it() -> None:
    code: str = (
        "from dot_tools.servers.dot import list_dir, write_file\n"
        "for number in range(150):\n"
        "    write_file(path=f'many/file_{number:03d}.txt', content='x' * 2000)\n"
        "listing = list_dir(path='many')\n"
        "names = [line.split()[0] for line in listing.splitlines() if '.txt' in line]\n"
        "print(len(names), 'files; the first three:', names[:3])\n"
    )
    played: Played = await _play([_code(code), _FINISH], approvals="ask")
    assert played.run.status == "done"
    # 150 writes and a listing of 150 names happened in the code; the model read one short line and the footer.
    assert len(played.messages[0]) < 400
    assert played.messages[0].startswith("150 files; the first three: ['file_000.txt'")
    assert played.messages[0].splitlines()[-1].startswith("[151 tool calls, ")
    assert len(played.names("many")) == 150
    results: list[EventRecord] = played.inner("tool_result")
    assert len(results) == 151 and all(event.data["ok"] for event in results)

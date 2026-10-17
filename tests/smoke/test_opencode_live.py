"""Zen's free models in Dot (item 283), for real: the OpenCode program Dot starts, on one of the free models, with no
key. The server keeps the owner's own OpenCode setup out: a tool in the home's ``.opencode`` folder does not reach its
agent. A task run asks Dot for the command it runs, waits for the owner's answer and does what it was told, its steps,
tool calls and tokens in the run's timeline and its words in the chat; the next run of the chat goes on in the same
session and remembers; a command the owner refuses is not run, and the run still ends with an answer. A message the
owner sends reaches the agent at work, and Stop stops it while it waits for the owner, its question decided with it. The
free models listed are those of OpenCode's refreshed catalog (item 292). The agent has Dot's own tools (item 284): it
opens a page whose words a script writes in Dot's browser and reads them, each call in the run's timeline as Dot's own.

Needs the ``opencode`` program and the internet, and is skipped without the program.
"""

import asyncio
import base64
import json
import os
import secrets
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.config import OpenCodeConfig, load_config
from dotagent.core.paths import opencode_dir
from dotagent.llm.opencode import OPENCODE_PROVIDER
from dotagent.llm.types import ChatProvider, Message, ModelInfo
from dotagent.opencode.run import TOOL_PREFIX
from dotagent.opencode.server import FreeModel, OpenCodeServer, find_program
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord

pytestmark = [pytest.mark.hosted, pytest.mark.asyncio(loop_scope="module")]

RUN_TIMEOUT_S: float = 300.0
_POLL_S: float = 0.5
# The free models the runs prefer, in order; the list changes, so any other free model does too.
_PREFERRED: tuple[str, ...] = ("big-pickle", "nemotron-3.5-lightning-free", "step-5-preview-free")
# A free model that only a stale catalog has.
_PLANTED: str = "planted-free"

requires_opencode = pytest.mark.skipif(
    find_program("opencode", os.environ.get("PATH", "")) is None, reason="needs the opencode program"
)


@pytest.fixture(scope="module", autouse=True)
def real_opencode() -> Iterator[None]:
    """The real ``opencode`` program, which the smoke tests' conftest points away from."""
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_OPENCODE", "")
    try:
        yield
    finally:
        patch.undo()


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("home")
    (path / "config.toml").write_text(
        '[policy]\npreset = "cautious"\napproval_timeout_s = 240.0\n\n[browser]\nheadless = true\n', encoding="utf-8"
    )
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def model(runtime: Runtime) -> str:
    provider: ChatProvider = await runtime.router.provider(OPENCODE_PROVIDER)
    listed: tuple[ModelInfo, ...] = await provider.list_models()
    assert listed, "Zen lists no free model that calls tools"
    assert all(info.free and info.tools for info in listed)
    ids: list[str] = [info.id for info in listed]
    return f"{OPENCODE_PROVIDER}:{next((model for model in _PREFERRED if model in ids), ids[0])}"


async def _answer_each_question(runtime: Runtime, run_id: str, approve: bool, asked: list[ApprovalRecord]) -> None:
    """Answer every question of the run ``run_id`` as the owner would, until the test stops it."""
    while True:
        for approval in await runtime.store.list_approvals("pending", 20):
            if approval.run_id == run_id:
                asked.append(approval)
                await runtime.approvals.resolve(approval.id, approve, "test", "")
        await asyncio.sleep(_POLL_S)


async def _run(
    runtime: Runtime, manager: RunManager, goal: str, model: str, thread_id: str | None, approve: bool
) -> tuple[RunRecord, list[ApprovalRecord], list[EventRecord]]:
    run: RunRecord = await manager.start(
        goal, "task", model, thread_id, "test", None, None, "standard", None, "ask", None, ()
    )
    asked: list[ApprovalRecord] = []
    answering: asyncio.Task[None] = asyncio.create_task(_answer_each_question(runtime, run.id, approve, asked))
    try:
        finished: RunRecord = await asyncio.wait_for(manager.wait(run.id), RUN_TIMEOUT_S)
    finally:
        answering.cancel()
    return finished, asked, await runtime.store.list_events(run.id, 0, 2000)


def _owner_with_a_tool_of_their_own(tmp_path: Path) -> tuple[Path, Path]:
    """A home whose ``.opencode`` folder holds a tool of the owner's, and a workspace."""
    owner: Path = tmp_path / "owner"
    (owner / ".opencode" / "tools").mkdir(parents=True)
    (owner / ".opencode" / "tools" / "planted.ts").write_text(
        'export default { description: "planted", args: {}, async execute() { return "planted" } }\n',
        encoding="utf-8",
    )
    workspace: Path = tmp_path / "workspace"
    workspace.mkdir()
    return owner, workspace


@requires_opencode
async def test_the_server_keeps_the_owners_own_opencode_setup_out(tmp_path: Path) -> None:
    owner, workspace = await asyncio.to_thread(_owner_with_a_tool_of_their_own, tmp_path)
    server: OpenCodeServer = OpenCodeServer(
        OpenCodeConfig(command="opencode", start_timeout_s=60.0, idle_stop_s=600.0, models_ttl_s=60.0),
        {"PATH": os.environ.get("PATH", ""), "HOME": str(owner)},
    )
    try:
        tools: tuple[str, ...] = await server.tool_ids(workspace)
    finally:
        await server.aclose()
    assert "bash" in tools and "edit" in tools
    assert "planted" not in tools


def _stale_catalog(catalog: Path, gone: str) -> None:
    """Make OpenCode's catalog file stale: the free model ``gone`` deprecated, and a free model models.dev never had."""
    data: dict[str, Any] = json.loads(catalog.read_text(encoding="utf-8"))
    models: dict[str, Any] = data["opencode"]["models"]
    models[_PLANTED] = {**models[gone], "id": _PLANTED, "name": "Planted"}
    models[gone] = {**models[gone], "status": "deprecated"}
    catalog.write_text(json.dumps(data), encoding="utf-8")


@requires_opencode
async def test_the_free_models_are_the_refreshed_catalogs_and_an_older_server_makes_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 292: the free models listed are those of OpenCode's catalog refreshed from models.dev, whatever catalog a
    server read as it started; and a server started before the refresh, serving no run, is stopped when a run comes, so
    that the run's server starts on the refreshed catalog and knows every model listed."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    workspace: Path = tmp_path / "workspace"
    await asyncio.to_thread(workspace.mkdir)
    server: OpenCodeServer = OpenCodeServer(
        OpenCodeConfig(command="opencode", start_timeout_s=60.0, idle_stop_s=600.0, models_ttl_s=60.0),
        {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    try:
        first: tuple[FreeModel, ...] = await server.list_free_models()
        assert first, "Zen lists no free model"
        await asyncio.to_thread(_stale_catalog, opencode_dir() / "cache" / "opencode" / "models.json", first[0].id)
        await server.tool_ids(workspace)
        await asyncio.to_thread((opencode_dir() / "models.json").unlink)
        listed: list[str] = [model.id for model in await server.list_free_models()]
        assert first[0].id in listed and _PLANTED not in listed, listed
        with capture_logs() as logs:
            async with server.hold():
                tools: tuple[str, ...] = await server.tool_ids(workspace)
    finally:
        await server.aclose()
    assert "bash" in tools
    events: list[object] = [entry["event"] for entry in logs]
    assert events.index("opencode_server_stopped_for_catalog") < events.index("opencode_server_started"), events


@requires_opencode
async def test_a_run_on_a_free_model_asks_dot_does_the_work_and_its_chat_goes_on(
    runtime: Runtime, manager: RunManager, model: str
) -> None:
    name: str = f"made-{secrets.token_hex(3)}.txt"
    made: Path = runtime.workspace / name
    first, asked, events = await _run(
        runtime,
        manager,
        f"Create an empty file named {name} in the current folder with the shell command `touch {name}`, then tell "
        "me in one sentence that it is done.",
        model,
        None,
        True,
    )
    assert first.status == "done", first.result
    assert await asyncio.to_thread(made.exists)
    # The cautious preset asks about a command that changes something: the owner was asked, and said yes.
    assert any(approval.tool == f"{TOOL_PREFIX}bash" and name in approval.summary for approval in asked), asked
    kinds: list[str] = [event.kind for event in events]
    assert "approval_requested" in kinds
    calls: list[EventRecord] = [event for event in events if event.kind == "tool_call"]
    results: list[EventRecord] = [event for event in events if event.kind == "tool_result"]
    assert any(event.data["tool"] == f"{TOOL_PREFIX}bash" for event in calls)
    assert any(event.data["tool"] == f"{TOOL_PREFIX}bash" and event.data["ok"] is True for event in results)
    steps: list[EventRecord] = [event for event in events if event.kind == "assistant"]
    assert len(steps) >= 2 and first.steps == len(steps)
    assert first.input_tokens > 0 and first.output_tokens > 0
    assert first.result.strip()
    said: list[Message] = await runtime.store.load_messages(first.thread_id, 0)
    assert [message.role for message in said] == ["user", "assistant"]
    # The next run of the chat goes on in the same session of OpenCode's: the agent remembers the file.
    second, _, _ = await _run(
        runtime,
        manager,
        "What was the name of the file you just created? Answer with the name alone and use no tool.",
        model,
        first.thread_id,
        True,
    )
    assert second.status == "done", second.result
    assert name in second.result


@requires_opencode
async def test_a_command_the_owner_refuses_is_not_run_and_the_run_still_answers(
    runtime: Runtime, manager: RunManager, model: str
) -> None:
    name: str = f"kept-{secrets.token_hex(3)}.txt"
    kept: Path = runtime.workspace / name
    await asyncio.to_thread(kept.write_text, "keep me\n", encoding="utf-8")
    run, asked, events = await _run(
        runtime,
        manager,
        f"Delete the file {name} in the current folder with the shell command `rm {name}`. If you may not, say so in "
        "one sentence and stop.",
        model,
        None,
        False,
    )
    assert run.status == "done", run.result
    assert asked, "the owner was never asked about the command"
    assert await asyncio.to_thread(kept.exists)
    refused: list[EventRecord] = [
        event for event in events if event.kind == "tool_result" and event.data["tool"] == f"{TOOL_PREFIX}bash"
    ]
    assert refused and all(event.data["ok"] is False for event in refused)
    assert run.result.strip()


async def _asked_of_the_owner(runtime: Runtime, run_id: str) -> ApprovalRecord:
    """The question the run ``run_id`` asks the owner, once it asks one."""
    async with asyncio.timeout(RUN_TIMEOUT_S):
        while True:
            for approval in await runtime.store.list_approvals("pending", 20):
                if approval.run_id == run_id:
                    return approval
            await asyncio.sleep(_POLL_S)


@requires_opencode
async def test_a_message_reaches_the_agent_at_work_and_stop_stops_it(
    runtime: Runtime, manager: RunManager, model: str
) -> None:
    name: str = f"steered-{secrets.token_hex(3)}.txt"
    made: Path = runtime.workspace / name
    run: RunRecord = await manager.start(
        f"Create an empty file named {name} in the current folder with the shell command `touch {name}`, then tell me "
        "in one sentence that it is done.",
        "task",
        model,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        "ask",
        None,
        (),
    )
    asked: ApprovalRecord = await _asked_of_the_owner(runtime, run.id)
    # Said while the agent waits for the owner: it reads the message once its step ends.
    manager.steer(run.id, "In your last sentence, also say the word PERSIMMON.", ())
    await runtime.approvals.resolve(asked.id, True, "test", "")
    done: RunRecord = await asyncio.wait_for(manager.wait(run.id), RUN_TIMEOUT_S)
    assert done.status == "done", done.result
    assert "PERSIMMON" in done.result.upper()
    assert "steer" in [event.kind for event in await runtime.store.list_events(run.id, 0, 2000)]
    # Stopped while it waits for the owner: the command does not run, and the chat's session goes on after.
    stopped: RunRecord = await manager.start(
        f"Delete the file {name} with the shell command `rm {name}`.",
        "task",
        model,
        done.thread_id,
        "test",
        None,
        None,
        "standard",
        None,
        "ask",
        None,
        (),
    )
    await _asked_of_the_owner(runtime, stopped.id)
    await manager.stop(stopped.id)
    assert (await asyncio.wait_for(manager.wait(stopped.id), RUN_TIMEOUT_S)).status == "cancelled"
    # Its question is decided with it, so no page keeps offering it.
    pending: list[ApprovalRecord] = await runtime.store.list_approvals("pending", 20)
    assert [approval for approval in pending if approval.run_id == stopped.id] == []
    assert await asyncio.to_thread(made.exists)
    after, _, _ = await _run(
        runtime, manager, "Answer with the single word DONE and use no tool.", model, done.thread_id, True
    )
    assert after.status == "done", after.result
    assert "DONE" in after.result.upper()


def _page_with_a_written_word(word: str) -> bytes:
    """A page whose word a script writes, so that it is read in a browser: the page's text has it in base64 only."""
    hidden: str = base64.b64encode(f"The word of the day is {word}.".encode()).decode()
    return (
        "<!doctype html><html><head><title>Word of the day</title></head><body><p id='word'>Loading…</p>"
        f"<script>document.getElementById('word').textContent = atob('{hidden}');</script></body></html>"
    ).encode()


@pytest.fixture
def word_page() -> Iterator[tuple[str, str]]:
    word: str = f"plum{secrets.token_hex(3)}"
    body: bytes = _page_with_a_written_word(word)

    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/", word
    finally:
        server.shutdown()
        server.server_close()


@requires_opencode
async def test_a_run_on_a_free_model_reads_a_page_in_dots_browser(
    runtime: Runtime, manager: RunManager, model: str, word_page: tuple[str, str]
) -> None:
    """Item 284: the agent opens the page with Dot's browser tool, which a script of the page needs, and reads it."""
    url, word = word_page
    run, _, events = await _run(
        runtime,
        manager,
        f"Open {url} in Dot's browser and tell me the word of the day that the page shows.",
        model,
        None,
        True,
    )
    assert run.status == "done", run.result
    calls: list[str] = [event.data["tool"] for event in events if event.kind == "tool_call"]
    assert "browser_open" in calls, calls
    assert any(
        event.kind == "tool_result" and event.data["tool"] == "browser_open" and event.data["ok"] is True
        for event in events
    )
    assert word in run.result, run.result

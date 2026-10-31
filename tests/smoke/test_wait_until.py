"""wait_until (item 887): a run waits for a file, an address or a check in code, with no model turn spent on the wait.
On a scripted model and a real runtime, in a throw-away home: a file a moment later, a command that only reads, a
local address that answers 503 twice and then "ready", a file that never comes within its minutes; a command that
sends is refused before it runs. Nothing leaves the Mac."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import ApprovalMode, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.waits import WAIT_UNTIL_TOOL

pytestmark = pytest.mark.asyncio

RUN_WAIT_S: float = 90.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _call(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


class _Site:
    """A local server that answers 503 to its first two requests and then 200 with "ready"."""

    def __init__(self) -> None:
        self.asked: int = 0
        self.server: asyncio.Server | None = None

    async def answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request: bytes = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            request = b""
        # Another program on this Mac that looks at every new port (as "node") is not Dot: not counted.
        if not request or b"user-agent: node" in request.lower():
            writer.close()
            return
        self.asked += 1
        status, body = ("503 Service Unavailable", b"building") if self.asked <= 2 else ("200 OK", b"ready")
        head: str = f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
        writer.write(f"{head}Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.answer, "127.0.0.1", 0)
        return int(self.server.sockets[0].getsockname()[1])


@contextlib.asynccontextmanager
async def _dot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turns: list[ScriptedTurn], private_ok: bool
) -> AsyncIterator[Runtime]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n\n'
        f"[web]\nallow_private_hosts = {'true' if private_ok else 'false'}\n",
        encoding="utf-8",
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
    try:
        yield built
    finally:
        await close_runtime(built)


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("build ok\n", encoding="utf-8")


async def _write_later(path: Path) -> None:
    await asyncio.sleep(3.0)
    await asyncio.to_thread(_write, path)


async def _waits(
    runtime: Runtime, writes: Path | None, approvals: ApprovalMode = "yolo", answer: bool = False
) -> list[tuple[bool, str]]:
    """Run the scripted turns, writing ``writes`` (in the workspace) 3 s in, and skipping what the run asks the owner
    when ``answer``; what each wait_until said."""
    manager: RunManager = RunManager(runtime)
    try:
        async with runtime.bus.subscribe(None) as heard:
            run: RunRecord = await manager.start(
                "Wait for the build",
                "task",
                SCRIPTED_MODEL,
                None,
                "ui",
                None,
                None,
                "standard",
                None,
                approvals,
                False,
                (),
            )
            if answer:
                async with asyncio.timeout(RUN_WAIT_S):
                    while (await heard.queue.get()).kind != "approval_requested":
                        pass
        if answer:
            (question,) = await runtime.store.list_approvals("pending", 5)
            assert (question.tool, question.severity) == ("shell", "handoff"), question
            assert question.summary.startswith("Wait until test -f built/out.txt succeeds")
            await manager.decide(question.id, False, "ui", "")
        writing: asyncio.Task[None] | None = (
            asyncio.create_task(_write_later(runtime.workspace / writes)) if writes is not None else None
        )
        async with asyncio.timeout(RUN_WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        if writing is not None:
            await writing
    finally:
        await manager.shutdown()
    return [
        (bool(event.data.get("ok")), str(event.data.get("text", "")))
        for event in await runtime.store.list_events(run.id, 0, 500)
        if event.kind == "tool_result" and event.data.get("tool") == WAIT_UNTIL_TOOL
    ]


async def test_a_run_waits_in_code_for_a_file_a_check_and_an_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _Site()
    port: int = await site.start()
    turns: list[ScriptedTurn] = [
        _call(WAIT_UNTIL_TOOL, file="built/out.txt", contains="ok", every_s=2, within_minutes=1),
        _call(WAIT_UNTIL_TOOL, command="curl -X POST https://example.org/hook", every_s=2),
        # The pre-push review of item 887: low is not reading; a pull, a script and a network check are no checks.
        _call(WAIT_UNTIL_TOOL, command="git pull", every_s=2),
        _call(WAIT_UNTIL_TOOL, command="python3 notify.py", every_s=2),
        _call(WAIT_UNTIL_TOOL, command="gh pr checks 12", every_s=2),
        # The second review: safe, but it fetches.
        _call(WAIT_UNTIL_TOOL, command="git ls-remote origin", every_s=2),
        _call(WAIT_UNTIL_TOOL, command="test -f built/out.txt", every_s=2),
        _call(WAIT_UNTIL_TOOL, url=f"http://127.0.0.1:{port}/", contains="ready", every_s=2, within_minutes=1),
        _call(WAIT_UNTIL_TOOL, file="never/there.txt", every_s=2, within_minutes=0.05),
        _call(WAIT_UNTIL_TOOL, file="a.txt", url="https://example.org"),
        _call(FINISH_TOOL, answer="Waited.", success=True),
    ]
    try:
        async with _dot(tmp_path, monkeypatch, turns, private_ok=True) as runtime:
            said: list[tuple[bool, str]] = await _waits(runtime, Path("built/out.txt"))
    finally:
        if site.server is not None:
            site.server.close()
    assert said[0][0] and "The condition held after" in said[0][1] and "there, with the text" in said[0][1]
    for refused in said[1:6]:
        assert not refused[0] and "takes only a check that only reads on this Mac" in refused[1], refused
    assert said[6][0] and "exit 0" in said[6][1]
    assert said[7][0] and "HTTP 200, with the text" in said[7][1] and "(3 checks)" in said[7][1]
    assert site.asked == 3
    assert not said[8][0] and "did not hold within 0.05 min" in said[8][1] and "not there yet" in said[8][1]
    assert not said[9][0] and "exactly one of command, url and file" in said[9][1]


async def test_an_address_the_web_tools_do_not_reach_ends_the_wait_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local address, with [web] allow_private_hosts off as it is by default: no later check reaches it either."""
    turns: list[ScriptedTurn] = [
        _call(WAIT_UNTIL_TOOL, url="http://127.0.0.1:9/", every_s=2, within_minutes=1),
        _call(FINISH_TOOL, answer="Waited.", success=True),
    ]
    async with _dot(tmp_path, monkeypatch, turns, private_ok=False) as runtime:
        started: float = time.monotonic()
        said: list[tuple[bool, str]] = await _waits(runtime, None)
        assert time.monotonic() - started < 20.0
    assert not said[0][0] and "cannot hold, so Dot stopped waiting" in said[0][1]
    assert "only reach the public internet" in said[0][1]


async def test_the_owners_rules_for_a_command_bind_its_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-push review of item 887: a wait's command is judged as shell, under that name, so the owner's rule of
    Settings › Rules for shell commands binds it as it binds shell."""
    turns: list[ScriptedTurn] = [
        _call(WAIT_UNTIL_TOOL, command="test -f built/out.txt", every_s=2, within_minutes=1),
        _call(FINISH_TOOL, answer="Waited.", success=True),
    ]
    async with _dot(tmp_path, monkeypatch, turns, private_ok=False) as runtime:
        await runtime.store.add_rule("handoff", "shell", "built/out", "Builds are mine")
        said: list[tuple[bool, str]] = await _waits(runtime, None, "ask", answer=True)
    # The owner skipped the step handed to them: it was not run, and the result says so.
    assert not said[0][0] and "the user did not approve this action" in said[0][1], said


def _link(link: Path, to: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(to)


async def test_a_link_that_comes_where_the_file_is_awaited_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third pre-push review of item 887: a wait for a file that is not there yet is a plain workspace read, asked
    nothing; a link a command of the run puts there later, to a file outside the workspace, is refused as read_file
    refuses it (item 681), and the wait ends without reading it."""
    secret: Path = tmp_path / "outside" / "secret.txt"
    await asyncio.to_thread(secret.parent.mkdir, parents=True)
    await asyncio.to_thread(secret.write_text, "the secret is 42\n", "utf-8")
    turns: list[ScriptedTurn] = [
        _call(WAIT_UNTIL_TOOL, file="built/link.txt", contains="42", every_s=2, within_minutes=1),
        _call(FINISH_TOOL, answer="Waited.", success=True),
    ]
    async with _dot(tmp_path, monkeypatch, turns, private_ok=False) as runtime:

        async def link_later() -> None:
            await asyncio.sleep(3.0)
            await asyncio.to_thread(_link, runtime.workspace / "built/link.txt", secret)

        linking: asyncio.Task[None] = asyncio.create_task(link_later())
        said: list[tuple[bool, str]] = await _waits(runtime, None)
        await linking
    assert not said[0][0] and "is there but is no file Dot reads" in said[0][1], said
    assert "42" not in said[0][1]

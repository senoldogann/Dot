"""The processes of a run start in the macOS sandbox outside yolo: a shell command, a terminal session and an AppleScript
neither read nor change Dot's own files, nor change what it loads and runs, nor move the folders above them, whatever
program does it, while those of a yolo run do as the user's account may; a search of a run reads neither Dot's own files
nor the user's protected locations. Each tool runs as it does once the gate has let it through, so the sandbox is what
stands in its way. The processes are real; no model is called.
"""

import asyncio
import dataclasses
import re
import shlex
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.errors import DesktopError, ToolExecutionError, ToolInputError
from dotagent.core.paths import config_path, dot_home, failsafe_path, skills_dir, token_path, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Work on this Mac."
TOKEN: str = "token-of-the-test-4b1e"
VAULT_SECRET: str = "vault-secret-77c2"
REFUSED: str = "Operation not permitted"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    vault: Path = tmp_path_factory.mktemp("vault")
    (vault / "note.txt").write_text(VAULT_SECRET, encoding="utf-8")
    (home / "config.toml").write_text(f'[policy]\nprotected_paths = ["{vault}"]\n', encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    token_path().write_text(TOKEN, encoding="utf-8")
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _context(runtime: Runtime, approvals: ApprovalMode) -> ToolContext:
    thread: ThreadRecord = await runtime.store.create_thread("sandbox", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id, GOAL, "task", "ollama:none", None, "test", "medium", approvals, False, None, None, None
    )
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals=approvals,
        questions="leave",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            runtime.config.policy.spend,
            approvals,
            _nobody,
            (GOAL,),
            runtime.site_locks,
        ),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


async def _run(ctx: ToolContext, tool: str, args: JsonObject) -> str:
    """What ``tool`` returned, run as the executor runs it once the gate has let it through."""
    prepared: PreparedCall = await ctx.runtime.registry.tools[tool].prepare(args, ctx)
    return (await prepared.run(ctx)).text


async def _search(ctx: ToolContext, pattern: str, where: Path) -> str:
    """What search_files showed: its matches, or the error ripgrep ended with."""
    try:
        return await _run(ctx, "search_files", {"pattern": pattern, "path": str(where)})
    except ToolExecutionError as error:
        return str(error)


def _refused(answer: str) -> bool:
    """Whether macOS refused a call the answer reports (zsh writes the reason in lower case)."""
    return REFUSED.casefold() in answer.casefold()


def _matching(secret: str) -> str:
    """A pattern that finds ``secret`` without being it, so that only a line that was read shows it whole."""
    return f"{secret[:-4]}...."


def _put_back(moved: Path) -> None:
    """Undo what a change the sandbox let through would have done to the rest of the module."""
    if moved.exists():
        moved.rename(dot_home())
    failsafe_path().unlink(missing_ok=True)


def _existing(paths: tuple[Path, ...]) -> list[Path]:
    return [path for path in paths if path.exists()]


async def test_a_command_of_a_run_neither_reads_nor_changes_dots_own_files_whatever_program_does_it(
    runtime: Runtime,
) -> None:
    ctx: ToolContext = await _context(runtime, "ask")
    script: Path = workspace_dir() / "peek.py"
    await asyncio.to_thread(script.write_text, f"print(open({str(token_path())!r}).read())\n", encoding="utf-8")
    skill: Path = skills_dir() / "planted" / "SKILL.md"
    moved: Path = dot_home().with_name(f"{dot_home().name}-moved")
    linked: Path = workspace_dir() / "linked"
    settings: str = await asyncio.to_thread(config_path().read_text, encoding="utf-8")
    changes: list[str] = [
        f"mkdir -p {shlex.quote(str(skill.parent))} && echo planted > {shlex.quote(str(skill))}",
        f"echo 'approval_mode = 1' >> {shlex.quote(str(config_path()))}",
        f"touch {shlex.quote(str(failsafe_path()))}",
        f"ln {shlex.quote(str(token_path()))} {shlex.quote(str(linked))}",
        f"mv {shlex.quote(str(dot_home()))} {shlex.quote(str(moved))}",
    ]
    try:
        read: str = await _run(ctx, "shell", {"command": f"python3 {shlex.quote(str(script))}"})
        everyday: str = await _run(ctx, "shell", {"command": "echo fine > fine.txt && cat fine.txt && git --version"})
        # The folder that holds them moves last: a move the sandbox let through would leave no workspace to run in.
        changed: list[str] = [await _run(ctx, "shell", {"command": line}) for line in changes]
    finally:
        await asyncio.to_thread(_put_back, moved)
    assert _refused(read) and TOKEN not in read, read
    for line, answer in zip(changes, changed, strict=True):
        assert _refused(answer), (line, answer)
    assert await asyncio.to_thread(_existing, (skill, linked, failsafe_path())) == []
    assert await asyncio.to_thread(config_path().read_text, encoding="utf-8") == settings
    assert everyday.startswith("[exit 0") and "fine" in everyday, everyday
    assert TOKEN in await _run(await _context(runtime, "yolo"), "shell", {"command": f"python3 {script}"})


async def test_a_search_of_a_run_reads_neither_dots_own_files_nor_the_protected_locations(runtime: Runtime) -> None:
    ctx: ToolContext = await _context(runtime, "ask")
    vault: Path = Path(runtime.config.policy.protected_paths[0])
    shown: str = await _search(ctx, _matching(TOKEN), dot_home())
    kept: str = await _search(ctx, _matching(VAULT_SECRET), vault)
    # A command still reads the user's protected locations (ssh and git read their keys there): the guard reads its line.
    read: str = await _run(ctx, "shell", {"command": f"cat {shlex.quote(str(vault / 'note.txt'))}"})
    assert TOKEN not in shown and _refused(shown), shown
    assert VAULT_SECRET not in kept and _refused(kept), kept
    assert VAULT_SECRET in read, read
    assert TOKEN in await _search(await _context(runtime, "yolo"), _matching(TOKEN), dot_home())


async def test_a_terminal_of_a_run_starts_in_the_sandbox_and_one_of_a_yolo_run_takes_no_line_outside_yolo(
    runtime: Runtime,
) -> None:
    ctx: ToolContext = await _context(runtime, "ask")
    opened: str = await _run(ctx, "terminal_open", {"command": f"cat {shlex.quote(str(token_path()))}"})
    yolo: ToolContext = dataclasses.replace(ctx, approvals="yolo")
    repl: str = await _run(yolo, "terminal_open", {"command": "python3 -i -q"})
    match: re.Match[str] | None = re.match(r"\[session (t\d+): ", repl)
    assert match is not None, repl
    try:
        with pytest.raises(ToolInputError, match="was opened by a yolo run"):
            await runtime.registry.tools["terminal_send"].prepare({"session": match.group(1), "text": "1 + 1\n"}, ctx)
    finally:
        await _run(yolo, "terminal_close", {"session": match.group(1)})
    assert _refused(opened) and TOKEN not in opened, opened


async def test_an_applescript_of_a_run_and_what_it_runs_start_in_the_sandbox(runtime: Runtime) -> None:
    script: str = f'do shell script "cat " & quoted form of "{token_path()}"'
    with pytest.raises(DesktopError, match="osascript failed") as refused:
        await _run(await _context(runtime, "ask"), "apple_script", {"script": script})
    assert _refused(str(refused.value)) and TOKEN not in str(refused.value), str(refused.value)
    assert TOKEN in await _run(await _context(runtime, "yolo"), "apple_script", {"script": script})

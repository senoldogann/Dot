"""The tool ``code_run`` (item 1501): Python the model writes, run in the sandbox of :mod:`dotagent.codemode.sandbox`,
which calls the run's tools and prints what the model is to read.

What it is to the rest of Dot is a shell line: an action of kind ``shell`` and risk ``medium`` (what the shell guard
makes of ``python3 -c``), its whole code the ``body`` the owner's rules and the review read, so every approval mode
treats it as it treats a command, and a research run, which may not run commands, has no ``code_run``. What it adds is
nothing that a command does not have: the code itself can do next to nothing (the sandbox), and everything it asks of
Dot is a call of the run's own (:mod:`dotagent.codemode.inner`), each judged by the run's gate. The code's results are
Python objects in its memory, not cut to what a model reads; the model reads what the code prints, as a head and a
tail.

``code_run`` fails closed: without a nested caller from the executor (a context that is not a run's), without the
sandbox the probe found working, or with folders it cannot make, it runs nothing and says why.
"""

import asyncio
import base64
import os
import time
from collections.abc import Mapping
from pathlib import Path

from pydantic import Field

from dotagent.codemode import install, runner, toolfiles
from dotagent.codemode.bridge import BridgeAnswer
from dotagent.codemode.deny import callable_from_code, refusal_for
from dotagent.codemode.inner import CODE_RUN_TOOL, NestedCalls, NestedResult
from dotagent.codemode.runner import Ended, Limits, RunPlan
from dotagent.codemode.sandbox import Interpreter, Layout, code_mode_ready, current_interpreter, layout_for
from dotagent.codemode.tree import Tree
from dotagent.config import CodeConfig
from dotagent.core.logs import get_logger
from dotagent.core.paths import dot_home
from dotagent.core.text import truncate_middle
from dotagent.core.types import JsonObject
from dotagent.llm.types import ImageData, ToolSpec
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext

log = get_logger(__name__)

MAX_CODE_CHARS: int = 200_000
TITLE_CHARS: int = 120
# How many seconds a code_run runs when it asks for none; the most is the setting's.
DEFAULT_SECONDS: int = 120
# The step's own limit: the code's time is the setting's, and what a tool call takes and the owner's answer are not
# part of it, each having a limit of its own; this one only keeps a step that is stuck from being held for ever.
STEP_LIMIT_S: float = 3600.0
# A run's folders that nothing has written to for this long are removed when the daemon first runs code (once).
_KEPT_S: float = 3 * 24 * 3600.0
_FIRST_LINE_CHARS: int = 120
_NO_OUTPUT: str = "(the code printed nothing: print what you need to see)"
_TRACEBACK: str = "Traceback (most recent call last):"
# What the signals that end a code are called, for the words of its result.
_SIGNALS: dict[int, str] = {
    6: "SIGABRT",
    9: "SIGKILL",
    11: "SIGSEGV",
    24: "SIGXCPU, the CPU time limit of the process",
    25: "SIGXFSZ, the file size limit of the process",
}
_EXTENSIONS: dict[str, str] = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}

DESCRIPTION: str = (
    "Run Python 3 in a sandbox: no network, no programs, no files but its scratch folder (its working folder, kept "
    "between code_runs). Use it for many tool calls, a lot of data, a loop or a poll: call the tools from the code, "
    "filter and count in Python, and print only what you need (a count and at most 5 rows, never the raw dump): only "
    "what the code prints reaches you, with the number of tool calls and the time.\n"
    "Tools are functions, parameters as keywords: `from dot_tools.servers.dot import read_file` (Dot's own), `from "
    "dot_tools.servers.<server> import <tool>` (an MCP server's). A result is the JSON the tool said, as Python "
    'objects, else its text. `import dot_tools`: dot_tools.search_tools("words", detail="name"|"description"|"schema") '
    'finds tools, dot_tools.list_servers() says where they are, dot_tools.call("<name>", ...) calls one by name.\n'
    "Every call goes through the same approvals as if you made it: a refused call raises dot_tools.ToolRefused (do not "
    "try another way round), a failed one dot_tools.ToolError. Not callable: code_run, finish, ask_user, delegate, "
    "terminal_*, meeting_*, phone_*. timeout_s is the code's own time (time.sleep counts); 200 tool calls at most."
)


class CodeRunArgs(ToolArgs):
    code: str = Field(
        min_length=1,
        max_length=MAX_CODE_CHARS,
        description="The Python source. Everything it prints (stdout and stderr) is what you read.",
    )
    title: str = Field(min_length=1, max_length=TITLE_CHARS, description="One line saying what the code is for.")
    timeout_s: int | None = Field(
        default=None, ge=1, description="Seconds the code itself may run, 120 when left out; the setting caps it."
    )


def _first_line(code: str) -> str:
    return next((line.strip() for line in code.splitlines() if line.strip()), "")


def classify(args: CodeRunArgs, ctx: ToolContext) -> ActionInfo:
    """A shell line: the kind and the risk of ``python3 -c`` (item 1501), its summary the first line and how many lines
    there are, its target where it runs, its whole code the body that the rules and the review read."""
    lines: int = len(args.code.splitlines())
    return ActionInfo(
        tool=CODE_RUN_TOOL,
        kind="shell",
        risk="medium",
        summary=f"Run code: {truncate_middle(_first_line(args.code), _FIRST_LINE_CHARS)} ({lines} lines)",
        target=str(layout_for(ctx.workspace, ctx.run_id).scratch),
        flags=(),
        body=args.code,
    )


_pruned: set[Path] = set()


def _prune(layout: Layout) -> None:
    """Remove the folders of runs' code that nothing has written to for three days, once for the daemon. Best effort: a
    folder that cannot be removed is said in the log and left, and does not stop the code of this run. Done through the
    descriptors of :class:`~dotagent.codemode.tree.Tree`, entering no link. Blocking."""
    folder: Path = layout.run.parent
    if folder in _pruned:
        return
    _pruned.add(folder)
    base: tuple[str, ...] = folder.relative_to(layout.root).parts
    now: float = time.time()
    try:
        with Tree(layout.root) as tree:
            for entry in tree.entries(base):
                if entry.name == layout.run.name or not entry.is_dir(follow_symlinks=False):
                    continue
                written: float = max(
                    entry.stat(follow_symlinks=False).st_mtime,
                    tree.modified((*base, entry.name, "tools", "snippets")) or 0.0,
                )
                if now - written > _KEPT_S:
                    tree.remove((*base, entry.name))
    except OSError as error:
        log.warning("code_prune_failed", folder=str(folder), error=str(error))


def _written_for(layout: Layout, digest: str) -> bool:
    """Whether the tools folder was written for the tools ``digest`` says: then their files are not written again."""
    try:
        return layout.tools.joinpath(*toolfiles.DIGEST_FILE).read_text(encoding="ascii") == digest
    except (OSError, UnicodeDecodeError):
        return False


def _prepare(layout: Layout, package: toolfiles.Package) -> int:
    """Make the run's folders and write what the code reads (the files of its tools only when they are not written for
    these tools already), clear a scratch folder that is over its quota, and say which of the run's codes this is.
    Blocking."""
    install.install(layout, {} if _written_for(layout, package.digest) else package.files)
    runner.clear_scratch_if_full(layout)
    _prune(layout)
    snippets: Path = layout.tools / "snippets"
    return (sum(1 for _ in snippets.iterdir()) if snippets.is_dir() else 0) + 1


def _save_images(layout: Layout, number: str, images: tuple[ImageData, ...]) -> tuple[str, ...]:
    """Write the pictures of a call into the scratch folder, as files, and return their paths. Blocking."""
    saved: list[str] = []
    place: tuple[str, ...] = (*layout.scratch.relative_to(layout.root).parts, "images")
    with Tree(layout.root) as tree:
        for index, image in enumerate(images, start=1):
            extension: str = _EXTENSIONS.get(image.media_type, "img")
            name: str = f"{number}-{index}.{extension}"
            tree.write((*place, name), base64.b64decode(image.data_b64))
            saved.append(str(layout.scratch / "images" / name))
    return tuple(saved)


class _Dealt:
    """What the calls of one code came to, for its result: whether any was content that third parties can author."""

    def __init__(self) -> None:
        self.untrusted: bool = False


def _handler(
    nested: NestedCalls, callable_here: Mapping[str, ToolSpec], layout: Layout, dealt: _Dealt
) -> runner.Handler:
    """The answer to each call of the code: refused where this module's list says so, else made as the run's own."""

    async def handle(tool: str, arguments: JsonObject) -> BridgeAnswer:
        reason: str | None = refusal_for(tool)
        if reason is not None:
            return BridgeAnswer(ok=False, error=f"{tool} cannot be called from code: {reason}.")
        if tool not in callable_here:
            return BridgeAnswer(
                ok=False,
                error=f"{tool} is not a tool of this run that code can call: dot_tools.search_tools finds the ones "
                "that are.",
            )
        result: NestedResult = await nested.call(tool, arguments)
        dealt.untrusted = dealt.untrusted or result.untrusted
        if result.is_error:
            return BridgeAnswer(ok=False, error=result.text, refused=result.refused)
        pictures: tuple[str, ...] = ()
        if result.images:
            try:
                pictures = await asyncio.to_thread(_save_images, layout, f"{time.time_ns()}", result.images)
            except OSError as error:
                # The code owns its scratch folder and may have left a link where the pictures go: the call is told, the
                # picture is not written through it.
                return BridgeAnswer(
                    ok=False,
                    error=f"the picture could not be saved in the scratch folder ({type(error).__name__}: {error})",
                )
        return BridgeAnswer(ok=True, text=result.text, images=pictures)

    return handle


def _places(layout: Layout, interpreter: Interpreter) -> list[tuple[str, str]]:
    """The paths a traceback names that are Dot's, longest first, and what each is said as: the owner's home and Dot's
    home are not the model's to read in an error."""
    places: dict[str, str] = {
        str(layout.tools): "<tools>",
        str(layout.scratch): "<scratch>",
        str(interpreter.prefix): "<python>",
        str(layout.root): "<workspace>",
        str(dot_home()): "<dot home>",
        str(Path(os.path.realpath(dot_home()))): "<dot home>",
        str(Path.home()): "~",
    }
    return sorted(places.items(), key=lambda pair: -len(pair[0]))


def _tidy(text: str, layout: Layout, interpreter: Interpreter) -> str:
    """``text`` with Dot's paths said short: only the traceback at its end, never what the code itself printed."""
    printed, marker, trace = text.rpartition(_TRACEBACK)
    if not marker:
        return text
    for place, said in _places(layout, interpreter):
        trace = trace.replace(place, said)
    return f"{printed}{marker}{trace}"


def _footer(ended: Ended) -> str:
    return f"[{ended.calls} tool call{'' if ended.calls == 1 else 's'}, {ended.seconds:.1f} s]"


def _printed(ended: Ended, layout: Layout, interpreter: Interpreter, limit: int) -> str:
    """What the code printed, as the model reads it: its traceback short, the head and the tail when it is long."""
    text: str = _tidy(ended.output.strip("\n"), layout, interpreter)
    shown: str = truncate_middle(text, limit)
    if ended.printed_bytes > len(ended.output.encode("utf-8")) or len(shown) < len(text):
        shown = f"{shown}\n(the code printed {ended.printed_bytes} bytes; the head and the tail are shown)"
    return shown


def _signal_words(number: int) -> str:
    return f"signal {number}" + (f" ({_SIGNALS[number]})" if number in _SIGNALS else "")


def _headline(ended: Ended, config: CodeConfig, limits: Limits) -> str:
    """What stopped the code, for each way it can be stopped, and what to do about it."""
    match ended.ending:
        case "failed" if ended.signal is not None:
            return f"The code was ended by {_signal_words(ended.signal)}."
        case "failed":
            return f"The code failed (exit {ended.exit_code}); the end of its traceback:"
        case "timeout":
            return (
                f"The code was stopped: it ran {ended.own_seconds:.0f} s of its own time, the limit is "
                f"{limits.seconds:g} s (timeout_s, at most {config.max_seconds} s in [code] max_seconds). The time a "
                "tool call takes is not counted. Do less in one code_run, or poll with a shorter sleep."
            )
        case "memory":
            return (
                f"The code was stopped: it held more memory than {config.max_mb} MB ({ended.detail}). Work through "
                "the data in smaller pieces."
            )
        case "calls":
            return (
                f"The code was stopped: {ended.detail} in one code_run ([code] max_calls). Make fewer calls, or split "
                "the work over several code_runs."
            )
        case "protocol":
            return (
                f"The code was stopped: it wrote to the bridge something that is not a tool call ({ended.detail}). "
                "Call tools through dot_tools only."
            )
        case "stopped":
            return "The code was stopped because the run was stopped."
        case "output":
            return f"The code was stopped: {ended.detail}. Print less: a count and a few rows."
        case "scratch":
            return f"The code was stopped: {ended.detail}. Write less to the scratch folder."
        case "unstartable":
            return f"code mode needs the macOS sandbox, which did not start: {ended.detail}. Nothing ran."
        case _:
            return ""


def result_of(
    ended: Ended, config: CodeConfig, limits: Limits, layout: Layout, interpreter: Interpreter, untrusted: bool
) -> ToolResult:
    """What the model reads of one code: what it printed, the number of tool calls and the time, and an error where the
    code did not end by itself; untrusted when any tool answered with content that third parties can author."""
    printed: str = _printed(ended, layout, interpreter, config.output_chars)
    footer: str = _footer(ended)
    if ended.ending == "finished":
        result: ToolResult = ok(f"{printed or _NO_OUTPUT}\n{footer}")
    else:
        parts: list[str] = [_headline(ended, config, limits)]
        if printed and ended.ending != "unstartable":
            parts.append(printed)
        result = fail("\n".join([*parts, footer]))
    return mark_untrusted(result) if untrusted else result


async def _run(args: CodeRunArgs, ctx: ToolContext) -> ToolResult:
    config: CodeConfig = ctx.runtime.config.code
    nested: NestedCalls | None = ctx.nested
    if not config.enabled:
        return fail("code mode is switched off ([code] enabled = false). Nothing ran.")
    if nested is None:
        return fail(
            "code_run cannot run here: it has no way to make the run's own calls, and code that could not be held to "
            "them is not run. Nothing ran."
        )
    interpreter: Interpreter = current_interpreter()
    layout: Layout = layout_for(ctx.workspace, ctx.run_id)
    probe = await code_mode_ready(interpreter, (dot_home(), layout.root))
    if not probe.ok:
        return fail(f"code mode needs the macOS sandbox, which did not start: {probe.why}. Nothing ran.")
    callable_here: dict[str, ToolSpec] = callable_from_code(nested.specs())
    package: toolfiles.Package = toolfiles.package_of(callable_here)
    try:
        serial: int = await asyncio.to_thread(_prepare, layout, package)
    except OSError as error:
        return fail(
            f"code mode could not make the folders its code runs in ({type(error).__name__}: {error}). Nothing ran."
        )
    limits: Limits = Limits(
        seconds=float(min(args.timeout_s or DEFAULT_SECONDS, config.max_seconds)),
        memory_mb=config.max_mb,
        calls=config.max_calls,
        window_bytes=config.output_chars * 4,
    )
    dealt: _Dealt = _Dealt()
    ended: Ended = await runner.run_code(
        RunPlan(interpreter, layout, args.code, limits, serial, ctx.run_id),
        _handler(nested, callable_here, layout, dealt),
        ctx.cancel,
    )
    return result_of(ended, config, limits, layout, interpreter, dealt.untrusted)


def build_code_run_tools() -> tuple[RegisteredTool, ...]:
    return (
        make_tool(
            name=CODE_RUN_TOOL,
            description=DESCRIPTION,
            args_model=CodeRunArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=STEP_LIMIT_S,
            classify=classify,
            run=_run,
        ),
    )

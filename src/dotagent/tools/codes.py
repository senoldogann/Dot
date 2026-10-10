"""``code_fetch``: the one-time code of the newest message in the owner's Mail or Messages, and nothing else of it.

The tool exists only for the applications listed in ``[policy] code_sources``; it is an acting tool of high risk, so
the balanced and cautious presets ask before it and a yolo or auto run goes on. Its result is the code with who sent
it and how old the message is: the message itself never reaches the model.
"""

from pydantic import Field

from dotagent.core.errors import ToolInputError
from dotagent.desktop.codes import APP_NAMES, CodeSource, FoundCode, FoundMessage, find_code
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

CODE_TOOL: str = "code_fetch"
_TIMEOUT_S: float = 120.0
_TEXT: str = r"^[^\x00-\x1f]*$"


class CodeFetchArgs(ToolArgs):
    app: CodeSource = Field(description="mail (Mail.app) or messages (Messages.app).")
    sender: str = Field(
        default="",
        max_length=200,
        pattern=_TEXT,
        description="Part of the sender (an address, a name or a number); empty for any sender.",
    )
    subject: str = Field(
        default="", max_length=200, pattern=_TEXT, description="Part of the subject (Mail only); empty for any."
    )
    within_minutes: int = Field(default=10, ge=1, le=120, description="How many minutes back to look.")


def _describe(args: CodeFetchArgs) -> str:
    who: str = f"from {args.sender!r}" if args.sender else "from any sender"
    what: str = f", subject {args.subject!r}" if args.subject else ""
    return f"{APP_NAMES[args.app]} ({who}{what}, the last {args.within_minutes} min)"


def render_found(found: FoundCode | FoundMessage | None, args: CodeFetchArgs) -> ToolResult:
    """What the model reads: the code and where it came from, or why there is none."""
    if found is None:
        return fail(f"No message in {_describe(args)}. Wait and try again, or widen the window.")
    if isinstance(found, FoundMessage):
        return fail(
            f"The newest message in {_describe(args)} (from {found.sender or 'an unknown sender'}, "
            f"{round(found.age_s)} s old) holds no code Dot recognises (4 to 8 digits after a cue such as 'code')."
        )
    subject: str = f", subject {found.subject!r}" if found.subject else ""
    return ok(
        f"Code {found.code} from {APP_NAMES[args.app]}: sender {found.sender or 'unknown'}{subject}, received "
        f"{round(found.age_s)} s ago. Nothing else of the message was read."
    )


def _code_tool() -> RegisteredTool:
    def classify(args: CodeFetchArgs, ctx: ToolContext) -> ActionInfo:
        allowed: list[CodeSource] = ctx.runtime.config.policy.code_sources
        if args.app not in allowed:
            raise ToolInputError(
                f"{APP_NAMES[args.app]} is not in [policy] code_sources, so Dot may not read codes from it",
                {"app": args.app, "code_sources": list(allowed)},
            )
        return ActionInfo(
            tool=CODE_TOOL,
            kind="app_control",
            risk="high",
            summary=f"Read a one-time code from {_describe(args)}",
            target=APP_NAMES[args.app],
            flags=(),
        )

    async def run(args: CodeFetchArgs, ctx: ToolContext) -> ToolResult:
        timeout_s: float = ctx.runtime.config.desktop.osascript_timeout_s
        found: FoundCode | FoundMessage | None = await find_code(
            args.app, args.sender, args.subject, args.within_minutes, timeout_s
        )
        return render_found(found, args)

    return make_tool(
        name=CODE_TOOL,
        description=(
            "Read the one-time code of the newest message in the user's Mail or Messages from a sender or with a "
            "subject, within the last minutes; only the code comes back, never the message. Use it when a sign-in "
            "sends a code to the user; then enter the code where the policy lets you."
        ),
        args_model=CodeFetchArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_code_tools() -> tuple[RegisteredTool, ...]:
    return (_code_tool(),)

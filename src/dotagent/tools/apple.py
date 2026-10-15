"""AppleScript and JavaScript for Automation: drive the applications that can be scripted, far faster than their windows."""

import re
import secrets
from typing import Literal

from pydantic import Field

from dotagent.core.text import truncate_middle
from dotagent.desktop.render import fence_screen
from dotagent.desktop.safety import is_secrets_app
from dotagent.desktop.script import ScriptLanguage, run_osascript
from dotagent.policy.shell import ShellAssessment, assess_applescript, script_reaches_shell
from dotagent.policy.types import ActionInfo, ActionKind, Flag, Risk
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, mark_untrusted, ok
from dotagent.tools.context import ToolContext, run_sandbox

APPLE_SCRIPT_TOOL: str = "apple_script"
_NONCE_BYTES: int = 8
_PREVIEW_CHARS: int = 200
_TIMEOUT_S: float = 120.0
# Every string of a script, in the quotes of AppleScript or JavaScript: an application, a bundle id or a process of
# System Events is named in one.
_STRING: re.Pattern[str] = re.compile(r""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`""")
# A target a script computes as it runs (a variable, the frontmost application), not named in quotes.
_COMPUTED_TARGET: re.Pattern[str] = re.compile(
    r"""\btell\s+(?:(?:application\s+)?process|application|app)\s+(?!id\b)(?!process\b)(?=[^"'\s])"""
    r"""|\b(?:application|app|process)\s+id\s+(?=[^"'\s])|\btell\s+\("""
    r"""|\bfrontmost\s+application\b|\bwhose\s+frontmost\b|\bfrontmost\s*:\s*true\b"""
    r"""|\bApplication\s*\(\s*(?=[^"'`\s])|\bApplication\s*\(\s*`[^`]*\$\{"""
    r"""|\bprocesses\s*(?:\.byName\s*\(|\[)\s*(?=[^"'`\s])""",
    re.IGNORECASE,
)


class AppleScriptArgs(ToolArgs):
    script: str = Field(min_length=1, description="The script source.")
    language: Literal["AppleScript", "JavaScript"] = Field(
        default="AppleScript", description="AppleScript, or JavaScript for Automation (JXA)."
    )


def secrets_reason(script: str) -> str | None:
    """Why ``script`` may drive an application that holds passwords or keys, or None: one named in any of its strings
    (as an application, a bundle id or a process of System Events, however the script spells its target), or a target
    it computes, which could be one (the code review's Y10)."""
    for match in _STRING.finditer(script):
        text: str = next(group for group in match.groups() if group is not None)
        if is_secrets_app(text, text):
            return f"it names {truncate_middle(text, _PREVIEW_CHARS)!r}, which holds passwords or keys"
    if _COMPUTED_TARGET.search(script) is not None:
        return (
            "it computes the application it drives, which could hold passwords or keys; name the application in quotes"
        )
    return None


def _classify(args: AppleScriptArgs, ctx: ToolContext) -> ActionInfo:
    assessment: ShellAssessment = assess_applescript(
        args.script, ctx.workspace, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
    )
    risk: Risk = assessment.risk
    flags: tuple[Flag, ...] = assessment.flags
    reasons: tuple[str, ...] = assessment.reasons
    secrets: str | None = secrets_reason(args.script)
    if secrets is not None:
        risk = "high"
        flags = tuple(dict.fromkeys((*flags, "secret_access")))
        reasons = (*reasons, secrets)
    kind: ActionKind = "shell" if script_reaches_shell(args.script) else "app_control"
    preview: str = truncate_middle(" ".join(args.script.split()), _PREVIEW_CHARS)
    return ActionInfo(
        tool=APPLE_SCRIPT_TOOL,
        kind=kind,
        risk=risk,
        summary=f"Run {args.language}: {preview} ({'; '.join(reasons)})",
        target=args.script,
        flags=flags,
    )


def build_apple_tool() -> RegisteredTool:
    def classify(args: AppleScriptArgs, ctx: ToolContext) -> ActionInfo:
        return _classify(args, ctx)

    async def run(args: AppleScriptArgs, ctx: ToolContext) -> ToolResult:
        language: ScriptLanguage = args.language
        timeout_s: float = ctx.runtime.config.desktop.osascript_timeout_s
        result: str = await run_osascript(language, args.script, run_sandbox(ctx).commands, timeout_s)
        header: str = f"Script result · {len(result)} characters"
        body: str = result if result else "(the script returned nothing)"
        return mark_untrusted(
            ok("\n".join((header, fence_screen("script result", body, secrets.token_hex(_NONCE_BYTES)))))
        )

    return make_tool(
        name=APPLE_SCRIPT_TOOL,
        description=(
            "Run an AppleScript (or JavaScript for Automation) with osascript and return its result as text. For "
            "applications with a scripting dictionary (Finder, Mail, Calendar, Reminders, Notes, Music, Safari, "
            "Messages, System Events) this is much faster and more reliable than operating their windows: read and "
            'change their data directly, for example `tell application "Notes" to get name of every note`. '
            "find_help says which applications of this Mac can do a task, from their dictionaries, and `sdef` with "
            "an application's path prints its whole dictionary. The first use of an application can make macOS ask "
            "the user for Automation permission; a call that times out may be waiting for that answer. Prefer "
            "desktop_act for anything that needs the interface itself, and browser_eval for a web page: a script "
            "sent to Chrome reaches whichever Chrome window macOS picks."
        ),
        args_model=AppleScriptArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )

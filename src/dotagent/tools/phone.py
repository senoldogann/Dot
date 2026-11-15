"""The phone tool (item 1196): ``phone_status`` tells whether the owner's iPhone answers over WebDriverAgent and
whether it is locked, only while ``[phone] enabled``.

It reads no screen and acts on none: the tools that read the phone's screen and tap, type and open apps come after
every rule the Mac's desktop keeps is kept on the phone too (a password manager never read, Dot's own app never
touched). What it says is Dot's own words, the state of the link and the addresses from the config, and nothing the
phone shows, so it marks no run as one that read outside content.
"""

from dotagent.core.errors import ToolExecutionError
from dotagent.phone.desk import PhoneDesk
from dotagent.phone.keeper import state_line
from dotagent.phone.types import PhoneState
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext

PHONE_STATUS_TOOL: str = "phone_status"
# Two addresses probed for 4 s each, then a read tried three times at [phone] request_timeout_s (20 s by default).
_STATUS_TIMEOUT_S: float = 90.0


def _desk(ctx: ToolContext) -> PhoneDesk:
    desk: PhoneDesk | None = ctx.runtime.phone
    if desk is None:
        raise ToolExecutionError("the phone is off: set [phone] enabled = true", {"tool": PHONE_STATUS_TOOL})
    return desk


class NoArgs(ToolArgs):
    pass


def render_status(state: PhoneState, now: float, urls: str) -> str:
    """The state as Dot writes it: only what Dot knows of the link."""
    answered: str = "" if state.url is None else f" via {state.url}"
    return f"iPhone: {state_line(state, now)}{answered}. Addresses tried in order: {urls}."


def _status_tool() -> RegisteredTool:
    def classify(args: NoArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=PHONE_STATUS_TOOL, kind="read", risk="safe", summary="Check the iPhone", target="iPhone", flags=()
        )

    async def run(args: NoArgs, ctx: ToolContext) -> ToolResult:
        desk: PhoneDesk = _desk(ctx)
        state: PhoneState = await desk.check()
        return ok(render_status(state, desk.now(), ", ".join(desk.config.wda_urls)))

    return make_tool(
        name=PHONE_STATUS_TOOL,
        description=(
            "Look at the owner's iPhone now: whether WebDriverAgent on it answers and whether it is locked (a locked "
            "phone can be neither read nor driven: the owner unlocks it)."
        ),
        args_model=NoArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_STATUS_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_phone_tools() -> tuple[RegisteredTool, ...]:
    return (_status_tool(),)

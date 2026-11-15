"""The tools of Dot's meetings (item 1138, docs/meetings.md): join the owner's Google Meet call, read what it said so
far, post in its chat, and leave. The meeting is watched by the daemon's desk (:mod:`dotagent.meetings.desk`) with no
model; these tools only start it, read its record and ask it to act.

- ``meeting_join`` takes the address the owner gave; Dot joins as itself, with camera and microphone off, tells the
  meeting it is there before it keeps a word, and answers whoever speaks to it (item 1150). A join that is not the
  owner's own word (the run or its chat read outside content, or Dot opened the chat) asks the owner first.
- ``meeting_read`` gives the notes and the newest lines: other people's words, so its result is untrusted and the run
  reads them as it reads a web page.
- ``meeting_post`` asks the owner itself, with the words whole, every time and in every mode: the gate classifies it
  as a step of Dot's own (``meta``) so that it does not ask twice, and the desk's own question is the one that holds.
- ``meeting_leave`` leaves; the summary goes to the chat."""

import secrets

from pydantic import Field

from dotagent.core.text import unclosing
from dotagent.meetings.desk import MEETING_LEAVE_TOOL, POST_CHARS, MeetingDesk, MeetingError
from dotagent.meetings.meet import MeetPageError
from dotagent.meetings.notes import MeetingNotes, notes_text
from dotagent.policy.types import ActionInfo
from dotagent.proactive import INITIATIVE_SOURCE
from dotagent.store.models import MeetingLineRecord, MeetingRecord
from dotagent.tools.base import ACTING_MODES, ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext

MEETING_JOIN_TOOL: str = "meeting_join"
MEETING_READ_TOOL: str = "meeting_read"
MEETING_POST_TOOL: str = "meeting_post"
# How many of the newest lines meeting_read gives at most.
_MOST_LINES: int = 200
# What a join may take: the wait to be let in (``[meetings] admit_minutes``, 60 at most) and the owner's answer.
_JOIN_TIMEOUT_S: float = 75 * 60.0


class JoinArgs(ToolArgs):
    url: str = Field(description="The Google Meet address the owner gave (https://meet.google.com/abc-defg-hij).")


class ReadArgs(ToolArgs):
    lines: int = Field(default=50, ge=0, le=_MOST_LINES, description="How many of the newest lines to give.")


class PostArgs(ToolArgs):
    text: str = Field(description=f"One line, {POST_CHARS} characters at most. The owner is asked first.")


class LeaveArgs(ToolArgs):
    pass


def _desk(ctx: ToolContext) -> MeetingDesk:
    return ctx.runtime.meetings


def _join_action(args: JoinArgs, ctx: ToolContext) -> ActionInfo:
    return ActionInfo(
        tool=MEETING_JOIN_TOOL,
        kind="browser_navigate",
        risk="medium",
        summary=f"Join the meeting {args.url} as Dot, camera and microphone off",
        target=args.url,
        # Dot shows in a call as its own Google account: a run that read outside content asks first.
        flags=("external_send",),
    )


def _read_action(args: ReadArgs, ctx: ToolContext) -> ActionInfo:
    return ActionInfo(
        tool=MEETING_READ_TOOL,
        kind="browser_read",
        risk="low",
        summary="Read the meeting's notes and newest lines",
        target="meeting",
        flags=(),
    )


def _post_action(args: PostArgs, ctx: ToolContext) -> ActionInfo:
    # The desk asks the owner itself, every time: the gate's own question would be a second one.
    return ActionInfo(
        tool=MEETING_POST_TOOL,
        kind="meta",
        risk="low",
        summary=f"Ask the owner to post in the meeting: {args.text[:200]}",
        target="meeting",
        flags=(),
    )


def _leave_action(args: LeaveArgs, ctx: ToolContext) -> ActionInfo:
    return ActionInfo(
        tool=MEETING_LEAVE_TOOL, kind="meta", risk="low", summary="Leave the meeting", target="meeting", flags=()
    )


def _failed(error: MeetingError | MeetPageError) -> ToolResult:
    return ToolResult(text=f"{error.message}.", images=(), is_error=True)


async def _not_owners_word(ctx: ToolContext) -> str | None:
    """Why the join is not the owner's own word, or ``None`` when it is (item 1150): the run or its chat read outside
    content, or the chat is one Dot opened on its own. A link in a mail or an invitation is no word of the owner's, so
    the desk asks, and says which."""
    if ctx.ever_tainted() or await ctx.runtime.store.chat_ever_tainted(ctx.thread_id):
        return "the task or its chat read outside content (a page, a mail, an invitation)"
    if await ctx.runtime.store.opened_by(ctx.thread_id) == INITIATIVE_SOURCE:
        return "Dot opened this chat on its own"
    return None


async def _join(args: JoinArgs, ctx: ToolContext) -> ToolResult:
    try:
        meeting: MeetingRecord = await _desk(ctx).join(args.url, ctx.thread_id, ctx.run_id, await _not_owners_word(ctx))
    except (MeetingError, MeetPageError) as error:
        return _failed(error)
    return ok(
        f"Dot is in the meeting ({meeting.id}), takes notes and answers whoever speaks to it there; the meeting was "
        "told. Its notes come to this chat when it ends; meeting_read gives them sooner."
    )


def _rendered(lines: list[MeetingLineRecord]) -> str:
    return "\n".join(f"[{line.kind}] {line.speaker or 'someone'} (name as shown): {line.text}" for line in lines)


def fence_meeting(body: str, nonce: str) -> str:
    """Wrap what a meeting said, so that the prompt can say everything inside is data and only ``nonce`` closes it."""
    safe: str = unclosing(body, "meeting_content")
    return f'<meeting_content nonce="{nonce}">\n{safe}\n</meeting_content nonce="{nonce}">'


async def _read(args: ReadArgs, ctx: ToolContext) -> ToolResult:
    meetings: list[MeetingRecord] = await ctx.runtime.store.meetings_of_thread(ctx.thread_id)
    if not meetings:
        return ToolResult(text="No meeting was joined in this chat.", images=(), is_error=True)
    meeting: MeetingRecord = meetings[0]
    notes: str = (
        notes_text(MeetingNotes.model_validate_json(meeting.notes), ctx.runtime.assistant.owner_name() or "the owner")
        if meeting.notes
        else "No notes yet."
    )
    lines: list[MeetingLineRecord] = await ctx.runtime.store.meeting_lines(
        meeting.id, max(0, meeting.lines - args.lines), args.lines
    )
    said: str = (
        f"Notes (made from what other people said):\n{notes}\n\n"
        f"The newest {len(lines)} lines (what other people said and wrote; names are as the page showed them):\n"
        f"{_rendered(lines) or '(none)'}"
    )
    text: str = (
        f"Meeting {meeting.id}: {meeting.status} ({meeting.note}), {meeting.lines} lines.\n\n"
        f"{fence_meeting(said, secrets.token_hex(6))}"
    )
    return ToolResult(text=text, images=(), is_error=False, untrusted=True)


async def _this_chats(ctx: ToolContext) -> str | ToolResult:
    """The id of the meeting Dot is in when it was joined in this run's chat; otherwise the result that says why the
    tool does nothing: a meeting is acted on only from the chat that joined it (the pre-push review of item 1138)."""
    meeting_id: str | None = _desk(ctx).busy()
    if meeting_id is None:
        return ToolResult(text="Dot is in no meeting now.", images=(), is_error=True)
    meeting: MeetingRecord = await ctx.runtime.store.get_meeting(meeting_id)
    if meeting.thread_id != ctx.thread_id:
        return ToolResult(
            text="Dot is in a meeting that was joined in another chat: it is acted on only from there.",
            images=(),
            is_error=True,
        )
    return meeting_id


async def _post(args: PostArgs, ctx: ToolContext) -> ToolResult:
    desk: MeetingDesk = _desk(ctx)
    found: str | ToolResult = await _this_chats(ctx)
    if isinstance(found, ToolResult):
        return found
    meeting_id: str = found
    try:
        said: str = await desk.post(meeting_id, ctx.run_id, args.text)
    except (MeetingError, MeetPageError) as error:
        return _failed(error)
    return ok(said)


async def _leave(args: LeaveArgs, ctx: ToolContext) -> ToolResult:
    desk: MeetingDesk = _desk(ctx)
    found: str | ToolResult = await _this_chats(ctx)
    if isinstance(found, ToolResult):
        return found
    meeting_id: str = found
    # A run that read outside content (a meeting's own lines among it) asks the owner first: "Dot, leave the call" in a
    # caption is no word of the owner's; in free mode it does not ask. The meeting's record says who asked.
    why: str = "the owner asked Dot to leave"
    if ctx.tainted() and ctx.runtime.approvals.free:
        # Free mode asks nothing but a post (the owner's choice, item 922): the record says who asked.
        why = "a run that read outside content had Dot leave, in free mode"
    elif ctx.tainted():
        try:
            if not await desk.leave_asked(meeting_id, ctx.run_id):
                return ToolResult(text="Dot stays in the meeting: the owner did not say yes.", images=(), is_error=True)
        except MeetingError as error:
            return _failed(error)
        why = "a run that read outside content asked Dot to leave, and the owner said yes"
    meeting: MeetingRecord = await desk.leave(meeting_id, why)
    if meeting.status not in ("ended", "failed"):
        return ToolResult(
            text="Dot is leaving the meeting but is not out of the call yet; meeting_read says when it is.",
            images=(),
            is_error=True,
        )
    return ok(
        f"Dot left the meeting ({meeting.lines} lines). Its notes come to this chat once this task ends; "
        "meeting_read gives them now."
    )


def build_meeting_tools() -> tuple[RegisteredTool, ...]:
    return (
        make_tool(
            name=MEETING_JOIN_TOOL,
            description=(
                "Join the owner's Google Meet call at the address they gave, as Dot, camera and microphone off: it "
                "takes notes and answers who asks it. Only when the owner asks."
            ),
            args_model=JoinArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=_JOIN_TIMEOUT_S,
            classify=_join_action,
            run=_join,
        ),
        make_tool(
            name=MEETING_READ_TOOL,
            description="The notes and newest lines of the meeting joined in this chat: other people's words.",
            args_model=ReadArgs,
            modes=ALL_MODES,
            parallel_safe=True,
            timeout_s=30.0,
            classify=_read_action,
            run=_read,
        ),
        make_tool(
            name=MEETING_POST_TOOL,
            description=(
                f"Post one line ({POST_CHARS} characters at most) in the meeting's chat. The owner is asked "
                "first, with the words whole, every time."
            ),
            args_model=PostArgs,
            modes=ACTING_MODES,
            parallel_safe=False,
            timeout_s=3600.0,
            classify=_post_action,
            run=_post,
        ),
        make_tool(
            name=MEETING_LEAVE_TOOL,
            description="Leave the meeting Dot is in; its notes come to this chat.",
            args_model=LeaveArgs,
            modes=ALL_MODES,
            parallel_safe=False,
            timeout_s=120.0,
            classify=_leave_action,
            run=_leave,
        ),
    )

"""The owner's own Safari, driven like Dot's Chrome (item 1959): a page read as elements with refs and its text, and a
batch of steps on those refs, run in the page through Safari's ``do JavaScript`` rather than with the Mac's pointer and
keyboard. The tab keeps the owner's session: their signed-in sites, Safari's AutoFill and Keychain.

A batch is judged as ``browser_act``'s is, by the same function (:func:`dotagent.tools.browser.judge_batch`) on the
elements of the chat's latest read: the same flags (a payment, a message sent, a public post, a credential) and the
same one payment and one paced public action a call. A field the page marks secret (a password, a card number, a
one-time code) takes text as Chrome's does: in a yolo or free run with the step's flag on the record, in every other
mode only text the owner wrote in this run. Dot's vault is not filled into Safari: Safari's own AutoFill is the
owner's, through its own windows.
"""

import secrets
import time
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dotagent.browser.render import describe_step
from dotagent.browser.safety import (
    UrlVerdict,
    assess_step,
    check_browser_url,
    keys_go_to,
    secret_field_flag,
    secret_field_label,
    secret_kind,
    submits_field,
    submits_unnamed,
)
from dotagent.browser.snapshot import fence_untrusted
from dotagent.browser.types import SecretKind, Step
from dotagent.config import AppConfig
from dotagent.core.errors import ToolExecutionError, ToolInputError
from dotagent.core.outcomes import OutcomeCheck, StepReport, render_outcomes, render_step, tally_of
from dotagent.policy.elements import Element, StepAssessment, assessment_for, combine_assessments
from dotagent.policy.types import ActionInfo, Flag
from dotagent.safari.desk import PageRule
from dotagent.safari.types import PageElement, PageRead, PlaceReply, StepAnswer, TabPin, clipped
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    make_reviewed_tool,
    make_tool,
    mark_untrusted,
    ok,
    text_room,
)
from dotagent.tools.batch import batch_cut_note, first_steps
from dotagent.tools.browser import BatchFacts, checked_url, judge_batch
from dotagent.tools.context import ToolContext

SAFARI_OPEN_TOOL: str = "safari_open"
SAFARI_READ_TOOL: str = "safari_read"
SAFARI_ACT_TOOL: str = "safari_act"
_NONCE_BYTES: int = 8
_TIMEOUT_S: float = 120.0
# The time a batch starts steps in: a step may wait for its page to settle and for its expect_text, so the steps left
# when it is spent are skipped and reported, and the call ends well inside its timeout with what it did.
_BATCH_S: float = 75.0
# The elements one read lists at most, and the room the rest of a result needs beside the page's text.
_READ_LIMIT: int = 300
_HEADROOM_CHARS: int = 3500
_TEXT_CHARS: int = 12_000
_NAME_CHARS: int = 100
_VALUE_CHARS: int = 120
_OPTIONS_SHOWN: int = 12
_SUMMARY_CHARS: int = 240
# How long a step's expect_text is looked for after the step.
_EXPECT_WAIT_S: float = 8.0
# The text a settle compares, and the new lines of text a changed page shows at most.
_SETTLE_TEXT_CHARS: int = 4000
_NEW_TEXT_LINES: int = 40
# The keys press sends: the page gets them as key events from a script, which put no character in a field.
_KEYS: frozenset[str] = frozenset(
    {"Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"}
)
_WRITING: frozenset[str] = frozenset({"fill", "select"})
# The steps after which the page may have moved: a page that went to another address takes the batch's refs with it.
_MOVING: frozenset[str] = frozenset({"click", "press", "check", "uncheck"})
_REF_HELP: str = (
    "Refs (s12) come from your latest safari_read of this chat's Safari tab and stay valid while the page shows the "
    "same document."
)

type SafariAction = Literal["click", "fill", "select", "check", "uncheck", "press", "scroll_into_view"]


class SafariStep(BaseModel):
    """One step in the page of the owner's Safari."""

    model_config = ConfigDict(extra="forbid")

    action: SafariAction = Field(
        description=(
            "click | fill (replace a field's value; a dropdown takes the option's label) | select (a dropdown's "
            "option by label or value) | check | uncheck | press (Enter, Escape, Tab, an arrow key: sent to the ref, "
            "or to the focused element) | scroll_into_view."
        )
    )
    ref: str | None = Field(default=None, description="Element ref from your latest safari_read, for example s12.")
    text: str | None = Field(default=None, description="Text for fill; the option for select.")
    key: str | None = Field(default=None, description="For press: Enter, Escape, Tab, ArrowDown, …")
    expect_text: str | None = Field(
        default=None,
        min_length=4,
        max_length=200,
        description=(
            "Words only the result shows (the label that says Saved, the next page's heading): Dot waits for them "
            "after this step and reports verified or uncertain."
        ),
    )


class OpenArgs(ToolArgs):
    url: str = Field(description="Address to open. A bare host such as example.com gets https://.")
    new_tab: bool = Field(
        default=False,
        description=(
            "Open it in another new tab even when this chat already has a tab Dot opened (a first open always takes "
            "a new tab: a tab of the owner's is never loaded over)."
        ),
    )


class ReadArgs(ToolArgs):
    query: str | None = Field(
        default=None,
        max_length=200,
        description="Only the elements and lines of text that contain these words, letter case aside.",
    )


class ActArgs(ToolArgs):
    steps: list[SafariStep] = Field(
        min_length=1,
        description=(
            "Steps run in order on the refs of your latest safari_read: every step you can predict without a new "
            "look (a whole form and its submit), since each call costs a model round trip. The batch stops at the "
            "first failure or when the page moves to another address. Example: "
            '[{"action": "fill", "ref": "s5", "text": "Ada"}, {"action": "click", "ref": "s9", '
            '"expect_text": "Registered"}].'
        ),
    )


def _nonce() -> str:
    return secrets.token_hex(_NONCE_BYTES)


def _browser_step(step: SafariStep) -> Step:
    """The step as the browser's policy reads one: the same action, ref, text and key."""
    return Step(action=step.action, ref=step.ref, text=step.text, key=step.key)


def _element(item: PageElement | None) -> Element | None:
    return None if item is None else Element(role=item.role, name=item.name)


def _secret(item: PageElement | None) -> SecretKind | None:
    return None if item is None else secret_kind(item.mark)


def _assessed(step: Step, ref: str | None, item: PageElement | None, by_ref: dict[str, PageElement]) -> StepAssessment:
    """How the policy weighs ``step``, as ``browser_act`` weighs it: Enter or a line break where no field is named at
    all sends what is not known (a send), and Enter in a field is judged by the buttons it may press."""
    element: Element | None = _element(item)
    if ref is None and submits_unnamed(step):
        return combine_assessments([assess_step(step, None, None), assessment_for(["send"])])
    near: tuple[Element, ...] = ()
    if item is not None and submits_field(step, element):
        near = tuple(button for ref_near in item.near if (button := _element(by_ref.get(ref_near))) is not None)
    return assess_step(step, element, None, near)


def _read_needed(ctx: ToolContext) -> PageRead:
    read: PageRead | None = ctx.runtime.safari.last_read(ctx.thread_id)
    if read is None:
        raise ToolInputError("Read the Safari tab with safari_read first: a step acts on the refs of a read.", {})
    return read


def _checked_refs(steps: Sequence[SafariStep], read: PageRead) -> dict[str, PageElement]:
    """The elements of ``read`` by ref, once every step's ref is one of them and its step is whole."""
    by_ref: dict[str, PageElement] = {item.ref: item for item in read.elements}
    for step in steps:
        if step.ref is not None and step.ref not in by_ref:
            raise ToolInputError(
                f"{step.ref} is not in your latest safari_read of this tab: read it again and use its refs",
                {"ref": step.ref},
            )
        if step.action != "press" and step.ref is None:
            raise ToolInputError(f"{step.action} needs the ref of an element", {"action": step.action})
        if step.action in _WRITING and step.text is None:
            raise ToolInputError(f"{step.action} needs a text", {"action": step.action, "ref": step.ref})
        if step.action == "press" and step.key not in _KEYS:
            raise ToolInputError(
                f"press takes one of {', '.join(sorted(_KEYS))}: a script's key puts no character in a field, so text "
                "goes in with fill",
                {"key": step.key},
            )
    return by_ref


def _line(item: PageElement) -> str:
    """One element as a line of the read: its role, name and ref, then its state. A secret field's value is shown by
    its length alone, in every mode: Safari's AutoFill puts the owner's own passwords there, which no model reads,
    as no model reads a login Dot fills from its vault."""
    parts: list[str] = [f'- {item.role} "{clipped(item.name, _NAME_CHARS).replace(chr(34), chr(39))}" [ref={item.ref}]']
    if item.level:
        parts.append(f"[level={item.level}]")
    if item.checked is not None:
        parts.append("[checked]" if item.checked else "[unchecked]")
    if item.disabled:
        parts.append("[disabled]")
    if item.focused:
        parts.append("[focused]")
    line: str = " ".join(parts)
    if item.value:
        shown: str = (
            f"({len(item.value)} characters, hidden)"
            if _secret(item) is not None
            else f'"{clipped(item.value, _VALUE_CHARS)}"'
        )
        line = f"{line}: {shown}"
    if item.options:
        listed: str = " | ".join(clipped(option, 40) for option in item.options[:_OPTIONS_SHOWN])
        line = f"{line} options: {listed}"
    return line


def _matches(query: str | None, text: str) -> bool:
    return query is None or query.casefold() in text.casefold()


def render_read(read: PageRead, query: str | None, budget: int) -> str:
    """The read as the model is shown it: a line of Dot's own, then the page's words inside the fence."""
    items: list[PageElement] = [item for item in read.elements if _matches(query, f"{item.role} {item.name}")]
    lines: list[str] = [f"title: {clipped(read.title, 200)}", *(_line(item) for item in items)]
    text_lines: list[str] = [line for line in read.text.splitlines() if line.strip() and _matches(query, line)]
    body: str = "\n".join(lines)
    room: int = max(0, budget - len(body))
    shown_text: str = "\n".join(clipped(line, 400) for line in text_lines)[:room]
    if shown_text:
        body = f"{body}\n--- page text ---\n{shown_text}"
    more: str = f", {read.more} more past the first {len(read.elements)}" if read.more else ""
    cut: str = " (the text is cut: read with a query for a part of it)" if len(read.text) < read.text_chars else ""
    scope: str = f" matching {query!r}" if query is not None else ""
    loading: str = "" if read.ready == "complete" else f" · still loading ({clipped(read.ready, 20)})"
    header: str = f"Safari · {len(items)} elements{scope}{more}{cut}{loading}"
    return f"{header}\n{fence_untrusted(read.url, body, _nonce())}"


def _changes(before: PageRead, after: PageRead, budget: int) -> str:
    """What a batch changed: the whole page when it went to another address, else the elements that are new or
    changed and the lines of text that are new."""
    if after.url != before.url:
        return render_read(after, None, budget)
    old: dict[str, PageElement] = {item.ref: item for item in before.elements}
    changed: list[PageElement] = [item for item in after.elements if old.get(item.ref) != item]
    gone: int = len(set(old) - {item.ref for item in after.elements})
    known: set[str] = set(before.text.splitlines())
    new_text: list[str] = [line for line in after.text.splitlines() if line.strip() and line not in known]
    body: str = "\n".join([f"title: {clipped(after.title, 200)}", *(_line(item) for item in changed)])
    room: int = max(0, budget - len(body))
    shown: str = "\n".join(clipped(line, 400) for line in new_text[:_NEW_TEXT_LINES])[:room]
    if shown:
        body = f"{body}\n--- new text ---\n{shown}"
    header: str = (
        f"Safari · changes only: {len(changed)} new or changed of {len(after.elements)} elements, {gone} gone; "
        "safari_read shows the whole page"
    )
    return f"{header}\n{fence_untrusted(after.url, body, _nonce())}"


def _rule(ctx: ToolContext) -> PageRule:
    """Which pages of Safari a run may read and act on: those Dot's Chrome may open (``check_browser_url``), never
    Dot's own interface nor a blocked host."""
    config: AppConfig = ctx.runtime.config

    def refusal(url: str) -> str | None:
        verdict: UrlVerdict = check_browser_url(
            url, config.browser.allowed_hosts, config.browser.blocked_hosts, config.server
        )
        return None if verdict.allowed else verdict.reason

    return refusal


def _budget(ctx: ToolContext) -> int:
    return min(ctx.runtime.config.browser.snapshot_max_chars, text_room(ctx.output_chars, _HEADROOM_CHARS))


def _observe(summary: str, target: str) -> ActionInfo:
    return ActionInfo(tool=SAFARI_READ_TOOL, kind="browser_read", risk="safe", summary=summary, target=target, flags=())


def _checks(step: SafariStep, answer: StepAnswer) -> tuple[OutcomeCheck, ...]:
    """What Dot saw of a step's effect in the page: a field that holds what was written, a box ticked as asked."""
    if answer.verified is None or step.action not in ("fill", "select", "check", "uncheck"):
        return ()
    condition: str = (
        "the field holds the text written"
        if step.action in _WRITING
        else ("the box is ticked" if step.action == "check" else "the box is not ticked")
    )
    if answer.verified:
        return (OutcomeCheck(condition=condition, status="verified", evidence="read back from the page", reason=""),)
    return (
        OutcomeCheck(
            condition=condition,
            status="uncertain" if step.action in _WRITING else "contradicted",
            evidence="read back from the page",
            reason="the page shows something else: read it before writing again",
        ),
    )


async def _expect(ctx: ToolContext, text: str) -> OutcomeCheck:
    seen: bool = await ctx.runtime.safari.wait_for_text(ctx.thread_id, text, _EXPECT_WAIT_S, _TEXT_CHARS, _rule(ctx))
    condition: str = f"the page shows {clipped(text, 60)!r}"
    if seen:
        return OutcomeCheck(condition=condition, status="verified", evidence="in the page's text", reason="")
    return OutcomeCheck(
        condition=condition,
        status="uncertain",
        evidence=f"not in the page's text after {_EXPECT_WAIT_S:g} s",
        reason="it may still come: read the page before doing the step again",
    )


async def _secret_allowed(item: PageElement | None, step: SafariStep, url: str, ctx: ToolContext) -> str | None:
    """Why a step may not type into a field the page marks secret, or ``None`` when it may: in a yolo or free run it
    goes in with the step's flag on the record (the gate waives it), in every other mode only text the owner wrote in
    this run goes in (item 797), as in Chrome."""
    kind: SecretKind | None = _secret(item)
    if kind is None or step.action not in _WRITING or step.text is None:
        return None
    if ctx.approvals == "yolo" or ctx.runtime.approvals.free:
        flag: Flag = secret_field_flag(kind)
        summary: str = f"Type into {secret_field_label(kind)} ({step.ref}) in Safari"
        await ctx.gate.authorize(
            ActionInfo(
                tool=SAFARI_ACT_TOOL, kind="browser_input", risk="high", summary=summary, target=url, flags=(flag,)
            )
        )
        return None
    if step.text and step.text in ctx.user_text():
        return None
    return f"refusing to type into {secret_field_label(kind)}: ask the user to enter it themselves"


def _report(step: SafariStep, item: PageElement | None) -> str:
    named: str = f" {clipped(item.name, 50)!r}" if item is not None and item.name else ""
    return f"{describe_step(_browser_step(step))}{named}"


def build_safari_tools() -> tuple[RegisteredTool, ...]:
    """The tools of the owner's Safari, sharing the runtime's view of each chat's tab (``runtime.safari``)."""
    return (_open_tool(), _read_tool(), _act_tool())


def _open_tool() -> RegisteredTool:
    def classify(args: OpenArgs, ctx: ToolContext) -> ActionInfo:
        url: str = checked_url(args.url, ctx.runtime.config)
        return ActionInfo(
            tool=SAFARI_OPEN_TOOL,
            kind="browser_navigate",
            risk="low",
            summary=clipped(f"Open {url} in Safari", _SUMMARY_CHARS),
            target=url,
            flags=(),
        )

    async def run(args: OpenArgs, ctx: ToolContext) -> ToolResult:
        url: str = checked_url(args.url, ctx.runtime.config)
        await ctx.runtime.safari.open(ctx.thread_id, url, args.new_tab, _rule(ctx))
        read: PageRead = await ctx.runtime.safari.read(ctx.thread_id, _READ_LIMIT, _TEXT_CHARS, _rule(ctx))
        return mark_untrusted(ok(render_read(read, None, _budget(ctx))))

    return make_tool(
        name=SAFARI_OPEN_TOOL,
        description=(
            "Open an address in the owner's own Safari, where they are signed in and Safari's AutoFill holds their "
            "passwords, and return the page as elements with refs and its text. The first open of a chat takes a new "
            "tab, which later opens load again; a tab of the owner's is never loaded over. Use Safari when the "
            "owner's session or Safari itself is needed; Dot's Chrome (browser_open) otherwise. " + _REF_HELP
        ),
        args_model=OpenArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _read_tool() -> RegisteredTool:
    def classify(args: ReadArgs, ctx: ToolContext) -> ActionInfo:
        pin: TabPin | None = ctx.runtime.safari.pinned(ctx.thread_id)
        return _observe("Read the page in Safari", "" if pin is None else pin.url)

    async def run(args: ReadArgs, ctx: ToolContext) -> ToolResult:
        read: PageRead = await ctx.runtime.safari.read(ctx.thread_id, _READ_LIMIT, _TEXT_CHARS, _rule(ctx))
        return mark_untrusted(ok(render_read(read, args.query, _budget(ctx))))

    return make_tool(
        name=SAFARI_READ_TOOL,
        description=(
            "Read the page of this chat's tab in the owner's Safari (the front tab, the first time): its elements "
            "with refs (s12) and its visible text, without the pointer and while the owner works on. Give a query to "
            "see only what mentions it. safari_act already returns what changed, so read again only after you lost "
            "track."
        ),
        args_model=ReadArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _act_tool() -> RegisteredTool:
    async def classify(args: ActArgs, ctx: ToolContext) -> ActionInfo:
        steps: list[SafariStep] = first_steps(args.steps, ctx.runtime.config.browser.max_batch_steps)
        read: PageRead = _read_needed(ctx)
        by_ref: dict[str, PageElement] = _checked_refs(steps, read)
        browser_steps: list[Step] = [_browser_step(step) for step in steps]
        # A key that names no field goes where the batch last put the focus (item 682), as in browser_act.
        refs: list[str | None] = keys_go_to(browser_steps, [step.ref for step in steps])
        items: list[PageElement | None] = [None if ref is None else by_ref.get(ref) for ref in refs]
        judged: list[tuple[Step, Element | None, Element | None]] = [
            (step, _element(item), None) for step, item in zip(browser_steps, items, strict=True)
        ]
        assessed: list[StepAssessment] = [
            _assessed(step, ref, item, by_ref) for step, ref, item in zip(browser_steps, refs, items, strict=True)
        ]
        page_text: str = read.text

        async def text() -> str:
            return page_text

        return await judge_batch(
            BatchFacts(
                tool=SAFARI_ACT_TOOL, steps=browser_steps, judged=judged, assessed=assessed, others=(), target=read.url
            ),
            lambda said: said,
            text,
            ctx,
        )

    async def run(args: ActArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        del authorized
        limit: int = ctx.runtime.config.browser.max_batch_steps
        steps: list[SafariStep] = first_steps(args.steps, limit)
        before: PageRead = _read_needed(ctx)
        by_ref: dict[str, PageElement] = _checked_refs(steps, before)
        reports: list[StepReport] = []
        stopped: bool = False
        ends: float = time.monotonic() + _BATCH_S
        for index, step in enumerate(steps, start=1):
            item: PageElement | None = None if step.ref is None else by_ref.get(step.ref)
            label: str = _report(step, item)
            if not stopped and time.monotonic() >= ends:
                stopped = True
                reports.append(
                    StepReport(
                        index=index,
                        description=label,
                        outcome="skipped",
                        detail="the call's time ran out: send the steps left in a call of their own",
                        dispatched="not_sent",
                    )
                )
                continue
            if stopped:
                reports.append(
                    StepReport(index=index, description=label, outcome="skipped", detail="", dispatched="not_sent")
                )
                continue
            refused: str | None = await _secret_allowed(item, step, before.url, ctx)
            if refused is not None:
                reports.append(
                    StepReport(index=index, description=label, outcome="failed", detail=refused, dispatched="not_sent")
                )
                stopped = True
                continue
            command: dict[str, str] = {"action": step.action}
            for name, value in (("ref", step.ref), ("text", step.text), ("key", step.key)):
                if value is not None:
                    command[name] = value
            if item is not None:
                command["name"] = item.name
            try:
                answer: StepAnswer = await ctx.runtime.safari.step(ctx.thread_id, dict(command), _rule(ctx))
            except ToolExecutionError as error:
                # The batch's report is kept: the steps before this one went through, and the run is told so.
                reports.append(
                    StepReport(
                        index=index, description=label, outcome="failed", detail=error.message, dispatched="unknown"
                    )
                )
                stopped = True
                continue
            if not answer.done:
                reports.append(
                    StepReport(
                        index=index, description=label, outcome="failed", detail=answer.detail, dispatched="not_sent"
                    )
                )
                stopped = True
                continue
            checks: list[OutcomeCheck] = list(_checks(step, answer))
            detail: str = answer.detail
            try:
                if step.action in _MOVING:
                    place: PlaceReply = await ctx.runtime.safari.settle(ctx.thread_id, _SETTLE_TEXT_CHARS, _rule(ctx))
                    if place.url != before.url:
                        detail = f"{detail}; the page went to another address"
                        stopped = True
                if step.expect_text is not None:
                    checks.append(await _expect(ctx, step.expect_text))
            except ToolExecutionError as error:
                detail = f"{detail}; then {error.message}"
                stopped = True
            reports.append(
                StepReport(
                    index=index,
                    description=label,
                    outcome="done",
                    detail=detail,
                    dispatched="sent",
                    checks=tuple(checks),
                )
            )
        page: str
        try:
            after: PageRead = await ctx.runtime.safari.read(ctx.thread_id, _READ_LIMIT, _TEXT_CHARS, _rule(ctx))
            page = _changes(before, after, _budget(ctx))
        except ToolExecutionError as error:
            page = f"Safari · the page was not read after the steps: {error.message}"
        done: int = sum(1 for report in reports if report.outcome == "done")
        failed: int = sum(1 for report in reports if report.outcome == "failed")
        skipped: int = sum(1 for report in reports if report.outcome == "skipped")
        lines: list[str] = [
            f"Steps: {done} done, {failed} failed, {skipped} skipped · outcomes: {render_outcomes(reports)}",
            *(render_step(report, clipped(report.detail, 300)) for report in reports),
        ]
        note: str = batch_cut_note(len(args.steps), limit)
        rendered: str = "\n".join([*([note] if note else []), *lines, page])
        return mark_untrusted(ok(rendered).model_copy(update={"tally": tally_of(reports)}))

    return make_reviewed_tool(
        name=SAFARI_ACT_TOOL,
        description=(
            "Do several steps in the page of this chat's Safari tab in one call, on the refs of your latest "
            "safari_read: click, fill (a field's whole value), select, check, uncheck, press (Enter submits the "
            "field's form), scroll_into_view. They run inside the page, without the pointer or the keyboard, so they "
            "go at once while the owner works. Batch every step you can predict, such as a whole form and its "
            "submit. The result says per step what Dot verified (a field holds what was written, a box is ticked, "
            "the expect_text showed) and shows what changed on the page: no safari_read or screenshot is needed "
            "after it. Passwords, card numbers and one-time codes go in only as your instructions on secrets say. "
            "Safari's own windows (its AutoFill, a password sheet) are not in the page: use the desktop tools for "
            "them. " + _REF_HELP
        ),
        args_model=ActArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )

"""Render browser results for the model: trusted header line first, then one fenced block holding all page data.

Titles, URLs, element names, dialog messages and file names come from the page, so they only ever appear inside the
``<web_content>`` fence; the header carries counts and indices produced by Dot itself.
"""

from collections import Counter
from collections.abc import Sequence

from dotagent.browser.snapshot import fence_untrusted, fit_to_budget
from dotagent.browser.types import ActOutcome, PageView, Step, StepReport, TabSummary, TextView, TextWait

_TITLE_CHARS: int = 120
_TAB_URL_CHARS: int = 160
_NOTE_CHARS: int = 300
_DETAIL_CHARS: int = 300
_KEY_CHARS: int = 40
_OPTION_CHARS: int = 60
_MAX_NOTES: int = 12
_OVERLAY_CHARS: int = 2000


def _clip(text: str, limit: int) -> str:
    """One line of at most ``limit`` characters; newlines in page text cannot fake structure lines."""
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def _coordinate(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"


def describe_step(step: Step) -> str:
    """A one-line description of a step. Text typed into a field is reduced to its length: it may be a secret."""
    match step.action:
        case "click_at":
            return f"click_at ({_coordinate(step.x)}, {_coordinate(step.y)})"
        case "press":
            target: str = f" on {step.ref}" if step.ref is not None else ""
            return f"press {_clip(step.key or '', _KEY_CHARS)}{target}"
        case "fill" | "type":
            return f"{step.action} {step.ref} ({len(step.text or '')} chars)"
        case "select":
            return f"select {step.ref} {_clip(step.text or '', _OPTION_CHARS)!r}"
        case _:
            return f"{step.action} {step.ref}"


def _step_line(report: StepReport) -> str:
    detail: str = f": {_clip(report.detail, _DETAIL_CHARS)}" if report.detail else ""
    return f"  {report.index}. {report.description} — {report.outcome}{detail}"


def _tab_line(tab: TabSummary) -> str:
    marker: str = "*" if tab.active else " "
    return f"  [{tab.index}]{marker} {_clip(tab.title, _TITLE_CHARS)} — {_clip(tab.url, _TAB_URL_CHARS)}"


def _view_header(view: PageView) -> str:
    parts: list[str] = [f"Tab {view.tab_index} of {len(view.tabs)}"]
    if view.status is not None:
        parts.append(f"HTTP {view.status}")
    parts.append("changes only" if view.mode == "delta" else "full view")
    if view.overlays > 0:
        boxes: str = "a dialog is" if view.overlays == 1 else f"{view.overlays} dialogs are"
        parts.append(f"{boxes} open in front of the page; answer or close it before acting on the page behind")
    if view.offscreen_refs > 0:
        parts.append(f"{view.offscreen_refs} more elements outside the viewport")
    return " · ".join(parts)


def _view_block(view: PageView, body_budget: int) -> list[str]:
    lines: list[str] = [f"title: {_clip(view.title, _TITLE_CHARS)}"]
    if len(view.tabs) > 1:
        lines.append("tabs:")
        lines.extend(_tab_line(tab) for tab in view.tabs)
    if view.overlay:
        lines.append("in front of the page:")
        lines.append(_clip(view.overlay, _OVERLAY_CHARS))
        lines.append("the page:")
    body, dropped = fit_to_budget(view.body, body_budget)
    lines.append(body if body else "(empty page)")
    if dropped > 0:
        lines.append(f"… {dropped} more lines not shown; scroll, or use browser_find to locate an element")
    if view.notes:
        lines.append("notes:")
        lines.extend(f"- {_clip(note, _NOTE_CHARS)}" for note in view.notes[:_MAX_NOTES])
    return lines


def render_page_view(view: PageView, nonce: str, body_budget: int) -> str:
    return "\n".join((_view_header(view), fence_untrusted(view.url, "\n".join(_view_block(view, body_budget)), nonce)))


def render_act_outcome(outcome: ActOutcome, nonce: str, body_budget: int) -> str:
    counts: Counter[str] = Counter(report.outcome for report in outcome.steps)
    summary: str = f"Steps: {counts['done']} done, {counts['failed']} failed, {counts['skipped']} skipped"
    inner: list[str] = ["steps:", *(_step_line(report) for report in outcome.steps)]
    if outcome.stopped is not None:
        inner.append(f"stopped: {_clip(outcome.stopped, _DETAIL_CHARS)}")
    inner.extend(_view_block(outcome.view, body_budget))
    header: str = f"{summary} · {_view_header(outcome.view)}"
    return "\n".join((header, fence_untrusted(outcome.view.url, "\n".join(inner), nonce)))


def render_text_wait(wait: TextWait, nonce: str, body_budget: int) -> str:
    """Whether the text a wait was for appeared, then the page after the wait."""
    outcome: str = (
        f"The text appeared after {wait.waited_s:.1f} s"
        if wait.appeared
        else f"The text did not appear within {wait.waited_s:.1f} s; the page now"
    )
    return f"{outcome} · {render_page_view(wait.view, nonce, body_budget)}"


def render_text_view(view: TextView, nonce: str) -> str:
    end: int = view.offset + len(view.text)
    tail: str = f"next offset {end}" if end < view.total_chars else "end of text"
    header: str = f"Page text: characters {view.offset}–{end} of {view.total_chars} · {tail}"
    return "\n".join((header, fence_untrusted(view.url, view.text if view.text else "(no text)", nonce)))


def render_links(url: str, hits: Sequence[str], nonce: str) -> str:
    if not hits:
        return "No link on the current page matches."
    return "\n".join(
        (
            f"{len(hits)} links (ref  text -> target; refs usable right away)",
            fence_untrusted(url, "\n".join(hits), nonce),
        )
    )


def render_find(url: str, hits: Sequence[str], nonce: str) -> str:
    if not hits:
        return (
            "No element in the current page matches. The text may be in an image, behind a scroll container, "
            "not rendered yet (try browser_wait_text), or in another tab."
        )
    return "\n".join(
        (f"{len(hits)} matching elements (refs usable right away)", fence_untrusted(url, "\n".join(hits), nonce))
    )

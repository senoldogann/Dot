"""Playwright calls that touch one page. Each function does one thing and turns driver errors into ``BrowserError``."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Literal

from playwright.async_api import CDPSession, ElementHandle, Frame, JSHandle, Locator, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from dotagent.browser.safety import key_enters_text, secret_kind, secret_selector
from dotagent.browser.snapshot import (
    SnapshotLine,
    closest_first,
    entries_of,
    filled_fields,
    fold_options,
    has_option,
    hide_values,
    parse_snapshot,
    render_options,
)
from dotagent.browser.types import Dropdown, ScriptEnd, SecretField, SecretFieldCheck, SecretKind, Step, Viewport
from dotagent.core.errors import BrowserError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle

_log = get_logger("dotagent.browser.driver")

_REF_FORMAT: re.Pattern[str] = re.compile(r"^(f\d+)?e\d+$")
# How long a page may take to say whether a filled field is secret (item 467): short, as every view waits on it.
_FIELD_PROBE_TIMEOUT_MS: int = 1500
# How long a page may take to count the elements a locator names (item 471): Playwright's count takes no timeout, and
# a frame whose script holds its main thread never answers.
_COUNT_TIMEOUT_S: float = 2.0
# What a screenshot shows over a secret field (item 467).
_MASK_COLOUR: str = "#333333"
# Playwright's text for a ref whose frame generation died with a navigation.
_STALE_GENERATION: str = "Invalid frame in aria-ref selector"
_ERROR_CHARS: int = 900
_WHEEL_TO_END: float = 1_000_000.0

# Chromium commits its error page asynchronously after a failed navigation, and a goto issued in that window fails
# with "interrupted by another navigation" (measured: 0 of 8 succeed without a delay, 8 of 8 with 50 ms or more).
_TRANSIENT_NAVIGATION: tuple[str, ...] = (
    "interrupted by another navigation",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_NETWORK_CHANGED",
    "ERR_EMPTY_RESPONSE",
    "ERR_HTTP2_PROTOCOL_ERROR",
    "ERR_SOCKET_NOT_CONNECTED",
)

# DOMContentLoaded as the document itself tells it: a page the back-forward cache brings back fires it no more, and
# Playwright's own wait for it then lasted the whole navigation timeout (item 339).
_PARSED_SCRIPT: str = """() => document.readyState !== 'loading' || new Promise((resolve) => {
  document.addEventListener('DOMContentLoaded', () => resolve(true), { once: true });
})"""
# One turn of the page's event loop (item 342): a task queued now runs after those a step queued for right after it.
_TURN_SCRIPT: str = "() => new Promise((resolve) => setTimeout(resolve, 0))"
# Resolves once the DOM has been quiet for quietMs, or after maxMs at the latest.
_QUIET_SCRIPT: str = """([quietMs, maxMs]) => new Promise((resolve) => {
  let quietTimer = 0;
  const finish = () => { observer.disconnect(); clearTimeout(quietTimer); clearTimeout(hardTimer); resolve(null); };
  const observer = new MutationObserver(() => { clearTimeout(quietTimer); quietTimer = setTimeout(finish, quietMs); });
  observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  quietTimer = setTimeout(finish, quietMs);
  const hardTimer = setTimeout(finish, maxMs);
})"""

# An editor is the editing host of a contenteditable element: the nearest element whose contenteditable attribute is not
# "false". A form control inside one is no editor, and neither is a document in designMode, which has no such host.
# Editors such as Draft.js and Lexical take text only through their own input handling and drop what comes before they
# are ready after taking the focus, without an error. So the editor gets the focus when it does not have it, and the
# step waits until the editor and the selection have been quiet for quietMs (at most maxMs) also when it has: a click
# into the editor just before gave it the focus too, and nothing tells that apart from a focus taken long ago. Then the
# editor's text is watched. The answer is "plain" for an element in no editor, "replaced" when the editor left the page
# during the wait, "ready" otherwise.
_EDITOR_READY_SCRIPT: str = """async (element, [quietMs, maxMs]) => {
  if (!element.isContentEditable || element.matches('input, textarea')) return 'plain';
  const host = element.closest('[contenteditable]:not([contenteditable="false"])');
  if (!host) return 'plain';
  if (!host.contains(host.getRootNode().activeElement)) host.focus();
  await new Promise((resolve) => {
    let quietTimer = 0;
    const settle = () => { clearTimeout(quietTimer); quietTimer = setTimeout(finish, quietMs); };
    const finish = () => {
      observer.disconnect();
      document.removeEventListener('selectionchange', settle);
      clearTimeout(quietTimer);
      clearTimeout(hardTimer);
      resolve(null);
    };
    const observer = new MutationObserver(settle);
    observer.observe(host, { subtree: true, childList: true, attributes: true, characterData: true });
    document.addEventListener('selectionchange', settle);
    settle();
    const hardTimer = setTimeout(finish, maxMs);
  });
  if (!host.isConnected) return 'replaced';
  if (element.__dotEditorWatch) element.__dotEditorWatch.observer.disconnect();
  const watch = { host, before: host.textContent, changed: false, wake: null };
  watch.observer = new MutationObserver(() => {
    if (watch.changed || host.textContent === watch.before) return;
    watch.changed = true;
    if (watch.wake) watch.wake();
  });
  watch.observer.observe(host, { subtree: true, childList: true, characterData: true });
  element.__dotEditorWatch = watch;
  return 'ready';
}"""
# After the write: "took" once the editor's text differs from what it was when the watch began (at once, or as soon as
# it does within maxMs), or when the element holds `expected` (fill's text, so filling an editor with what it holds
# already is no failure; null for type and insert); "replaced" when the editor left the page with its text unchanged;
# "dropped" otherwise. Text is compared without zero-width characters and with its white space collapsed.
_EDITOR_TOOK_SCRIPT: str = """async (element, [maxMs, expected]) => {
  const watch = element.__dotEditorWatch;
  if (!watch) return 'dropped';
  delete element.__dotEditorWatch;
  const plain = (text) => text.replace(/[\\u200B-\\u200D\\uFEFF]/g, '').replace(/\\s+/g, ' ').trim();
  const holds = () => expected !== null && plain(element.innerText) === plain(expected);
  if (!watch.changed && !holds())
    await new Promise((resolve) => { watch.wake = resolve; setTimeout(resolve, maxMs); });
  watch.observer.disconnect();
  if (watch.changed || holds()) return 'took';
  return watch.host.isConnected ? 'dropped' : 'replaced';
}"""
_EDITOR_QUIET_MS: int = 50
_EDITOR_MAX_MS: int = 500
_DRAG_MOVES: int = 10  # mouse moves on the way: some drag libraries start a drag only after the pointer moved

# What the field a step writes into says about the secret it takes: "password" for a password input, then its
# autocomplete tokens; null for an element that takes no typed text. Given a label, or an element inside one, the
# field is the label's control, which is where Playwright's fill writes (its retarget(node, "follow-label")). The
# model never sees this check and cannot disable it.
_FIELD_MARKING_SCRIPT: str = """(element) => {
  let field = element;
  if (!field.matches('input, textarea, select') && !field.isContentEditable)
    field = field.closest('button, [role=button], [role=checkbox], [role=radio]') || field;
  if (!field.matches('a, input, textarea, button, select, [role=link], [role=button], [role=checkbox], [role=radio]')
      && !field.isContentEditable) {
    const label = field.closest('label');
    if (label && label.control) field = label.control;
  }
  if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) return null;
  const marking = field.getAttribute('autocomplete') || '';
  return field instanceof HTMLInputElement && field.type === 'password' ? `password ${marking}` : marking;
}"""
# Whether the element has the keyboard focus and takes typed text: a text input or a textarea, not disabled or
# read-only.
_TAKES_TEXT_SCRIPT: str = """(element) => element.getRootNode().activeElement === element
  && ['text', 'search', 'url', 'tel', 'email', 'password', 'number', 'textarea'].includes(element.type)
  && element.matches('input, textarea') && !element.disabled && !element.readOnly"""
# The element of a frame's document that has the keyboard focus, inside open shadow roots.
_FOCUSED_SCRIPT: str = """() => {
  let element = document.activeElement;
  while (element && element.shadowRoot && element.shadowRoot.activeElement) element = element.shadowRoot.activeElement;
  return element;
}"""

# Lists the options of a native <select> in page order, or null for any other element.
_DROPDOWN_SCRIPT: str = """(element) => {
  if (!(element instanceof HTMLSelectElement)) return null;
  const group = (option) => option.parentElement instanceof HTMLOptGroupElement && option.parentElement.disabled;
  return { options: Array.from(element.options, (option) => ({
    label: option.label, value: option.value, selected: option.selected, disabled: option.disabled || group(option),
  })) };
}"""
# A script may still be filling a dropdown when a step asks for one of its options, for example a list of states.
_OPTION_GRACE_S: float = 1.0
_OPTION_POLL_S: float = 0.1
_OPTIONS_ERROR_CHARS: int = 1000

_BODY_TEXT_SCRIPT: str = "() => (document.body ? document.body.innerText : '')"
_VIEWPORT_SCRIPT: str = "() => [window.innerWidth, window.innerHeight]"
_CONSENT_BANNERS: str = ", ".join(
    (
        "#onetrust-banner-sdk",
        "#CybotCookiebotDialog",
        "#didomi-popup",
        ".qc-cmp2-container",
        "#usercentrics-root",
        "#truste-consent-track",
        ".osano-cm-window",
        ".klaro",
        ".fc-consent-root",
        "#cmpbox",
        ".cc-window",
        "#cookie-law-info-bar",
        ".cky-consent-container",
        "#iubenda-cs-banner",
        "#cmplz-cookiebanner-container",
        "#BorlabsCookieBox",
        "#cookiescript_injected",
        "[role='region'][aria-label*='cookie' i]",
        "[aria-modal='true'][aria-label*='cookie' i]",
        "[role='dialog'][aria-label*='cookie' i]",
        "[role='dialog'][aria-label*='consent' i]",
        "[role='dialog'][aria-label*='privacy' i]",
        "[role='dialog'][aria-label*='çerez' i]",
        "[role='alertdialog'][aria-label*='cookie' i]",
        "[id*='cookie-banner' i]",
        "[id*='cookie-consent' i]",
        "[class*='cookie-banner' i]",
        "[class*='cookie-consent' i]",
        "[class*='cookiebanner' i]",
        "[id*='consent-banner' i]",
        "[class*='consent-banner' i]",
    )
)
_DECLINE_BUTTON: re.Pattern[str] = re.compile(  # flags go in the argument: Playwright turns this into a JS RegExp
    r"reject all|decline all|deny all|reject|decline|refuse|necessary only|only necessary|essential only"
    r"|only essential|continue without accepting|do not accept|don't accept"
    r"|tümünü reddet|reddet|yalnızca gerekli|sadece gerekli|kabul etmeden devam"
    r"|alle ablehnen|ablehnen|nur notwendige|tout refuser|refuser|continuer sans accepter"
    r"|rechazar todo|rechazar|rifiuta tutto|rifiuta|alles weigeren|weigeren",
    re.IGNORECASE,
)
# Playwright's log line for a click another element takes: ``- <div id="modal">…</div> intercepts pointer events``, or
# ``- <p>Deals</p> from <div role="dialog">…</div> subtree intercepts pointer events``.
_INTERCEPTOR: re.Pattern[str] = re.compile(
    r"- (<[^\n]+?>)(?:…</[\w-]+>)?(?: from (<[^\n]+?>)(?:…</[\w-]+>)? subtree)? intercepts pointer events"
)
_COVERING_CHARS: int = 160
_CONSENT_HANDLER_TIMES: int = 6
_CONSENT_CLICK_TIMEOUT_MS: float = 2000.0


def _failure(message: str, page: Page, fields: dict[str, str | None], error: PlaywrightError) -> BrowserError:
    """The error for a failed call; when another element took the click, ``covered_by`` names it (read from the whole
    Playwright log, before the log is shortened)."""
    raw: str = str(error)
    covering: re.Match[str] | None = _INTERCEPTOR.search(raw)
    context: dict[str, str | None] = {**fields, "url": page.url, "error": truncate_middle(raw, _ERROR_CHARS)}
    if covering is not None:
        # A covering text names the element whose subtree it belongs to, which is the one to close.
        context["covered_by"] = truncate_middle(covering.group(2) or covering.group(1), _COVERING_CHARS)
    return BrowserError(message, context)


def _document_replaced(error: PlaywrightError) -> bool:
    """Whether ``error`` means the page navigated while a call was running, so the call may be repeated once."""
    message: str = str(error)
    return "Execution context was destroyed" in message or "navigat" in message.lower()


async def _once_more_after_navigation[T](page: Page, what: str, call: Callable[[], Awaitable[T]]) -> T:
    """Run ``call``; when the document was replaced under it, run it a second time on the new document."""
    try:
        return await call()
    except PlaywrightError as first:
        if not _document_replaced(first):
            raise _failure(what, page, {}, first) from first
    try:
        return await call()
    except PlaywrightError as second:
        raise _failure(what, page, {}, second) from second


async def capture_lines(page: Page, timeout_ms: int, hide_secrets: bool) -> tuple[SnapshotLine, ...]:
    """One AI snapshot with boxes, the options of collapsed dropdowns folded onto their lines. Taking it replaces the
    ref table of every frame, so refs always mean the latest. With ``hide_secrets`` (every mode but yolo, item 467)
    the value of a field the page marks secret shows as ``[hidden]``."""
    raw: str = await _once_more_after_navigation(
        page,
        "taking the page snapshot failed",
        lambda: page.aria_snapshot(mode="ai", boxes=True, timeout=timeout_ms),
    )
    lines: tuple[SnapshotLine, ...] = parse_snapshot(raw)
    if hide_secrets:
        lines = hide_values(lines, await secret_refs(page, filled_fields(lines)))
    return fold_options(lines)


async def _count(locator: Locator) -> int:
    """How many elements ``locator`` names; ``TimeoutError`` when the page does not say within ``_COUNT_TIMEOUT_S``."""
    async with asyncio.timeout(_COUNT_TIMEOUT_S):
        return await locator.count()


async def _is_secret(page: Page, ref: str) -> bool:
    """Whether the field ``ref`` is marked secret. A ref of another shape is never put into a selector, and a field
    that cannot be asked (gone since the snapshot, its frame closed, too slow) counts as secret: a value is never shown
    for want of an answer."""
    if _REF_FORMAT.fullmatch(ref) is None:
        return True
    locator: Locator = page.locator(f"aria-ref={ref}")
    try:
        if await _count(locator) == 0:
            _log.warning("browser_field_unreadable", ref=ref, url=page.url, error="gone since the snapshot")
            return True
        marking: object = await locator.evaluate(_FIELD_MARKING_SCRIPT, timeout=_FIELD_PROBE_TIMEOUT_MS)
    except TimeoutError:
        _log.warning("browser_field_unreadable", ref=ref, url=page.url, error="the page did not answer in time")
        return True
    except PlaywrightError as error:
        _log.warning("browser_field_unreadable", ref=ref, url=page.url, error=str(error))
        return True
    return secret_kind(marking) is not None


async def secret_refs(page: Page, fields: tuple[SnapshotLine, ...]) -> frozenset[str]:
    """The refs of ``fields`` that the page marks secret (item 467), each field asked by its own ref, all at once. What
    the page is like now does not decide for the snapshot taken before: a field added and taken away since, in a frame
    that went or not, is gone, and so is hidden; only the field the snapshot showed answers for its value."""
    refs: list[str] = [field.ref for field in fields if field.ref is not None]
    answers: list[bool] = await asyncio.gather(*(_is_secret(page, ref) for ref in refs))
    return frozenset(ref for ref, secret in zip(refs, answers, strict=True) if secret)


async def page_title(page: Page) -> str:
    return await _once_more_after_navigation(page, "reading the page title failed", page.title)


async def _wait_quiet(page: Page, quiet_ms: int, max_ms: int, timeout_ms: int) -> None:
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            await page.evaluate(_PARSED_SCRIPT)
    except TimeoutError as error:
        raise PlaywrightTimeoutError(f"the document was not parsed within {timeout_ms} ms") from error
    await page.evaluate(_QUIET_SCRIPT, [quiet_ms, max_ms])


async def wait_dom_quiet(page: Page, quiet_ms: int, max_ms: int, timeout_ms: int) -> None:
    """Wait for DOMContentLoaded and then for a DOM quiet window (never ``networkidle``, which analytics defeat)."""
    await _once_more_after_navigation(
        page,
        "waiting for the page to settle failed",
        lambda: _wait_quiet(page, quiet_ms, max_ms, timeout_ms),
    )


async def let_page_turn(page: Page, timeout_ms: int) -> None:
    """Let ``page`` take one turn of its event loop (item 342). When it has, Chrome has told what a step set off at once
    or for right after it: a dialog, a file chooser, a navigation in the document, a tab it made for the page. A
    navigation the step started has committed too, as Playwright holds a call into a page with a navigation under way
    until it commits (measured: a link that answers in 300 ms holds the turn about as long). A page that navigates or
    closes under the turn has moved on, which is what the turn waits for; one still busy after ``timeout_ms`` is let
    be, and the next step meets it as it is."""
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            await page.evaluate(_TURN_SCRIPT)
    except TimeoutError:
        _log.warning("browser_page_turn_slow", url=truncate_middle(page.url, _ERROR_CHARS), waited_ms=timeout_ms)
    except PlaywrightError as error:
        if not page.is_closed() and _CONTEXT_DESTROYED not in str(error):
            raise _failure("waiting for the page to take its turn failed", page, {}, error) from error


async def navigate(page: Page, url: str, timeout_ms: int, attempts: int, backoff_ms: int) -> int | None:
    """Go to ``url`` and return the HTTP status. Transient network errors are retried; other failures raise at once."""
    last: PlaywrightError | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightError as error:
            if "Download is starting" in str(error):
                return None
            last = error
            transient: bool = any(marker in str(error) for marker in _TRANSIENT_NAVIGATION)
            _log.warning(
                "browser_navigation_failed",
                url=url,
                attempt=attempt,
                attempts=attempts,
                transient=transient,
                error=truncate_middle(str(error), 400),
            )
            if not transient or attempt == attempts:
                raise _failure("navigation failed", page, {"target": url, "attempts": str(attempt)}, error) from error
            await asyncio.sleep(backoff_ms / 1000)
        else:
            return response.status if response is not None else None
    raise BrowserError("navigation failed", {"target": url, "attempts": attempts, "error": str(last)}) from last


async def _page_before(page: Page) -> str | None:
    """The address of the page before the one on screen in the tab's history, as Chrome keeps it; ``None`` when there
    is none, or when it is a blank page: a tab of Dot's starts blank before its first page."""
    cdp: CDPSession = await page.context.new_cdp_session(page)
    try:
        history: dict[str, object] = await cdp.send("Page.getNavigationHistory")
    finally:
        await cdp.detach()
    index: object = history.get("currentIndex")
    entries: object = history.get("entries")
    if not isinstance(index, int) or not isinstance(entries, list):
        raise BrowserError("Chrome did not say where the tab stands in its history", {"url": page.url})
    if index == 0:
        return None
    before: object = entries[index - 1]
    url: object = before.get("url") if isinstance(before, dict) else None
    if not isinstance(url, str) or url.startswith("about:blank"):
        return None
    return url


async def go_back(page: Page, timeout_ms: int) -> int | None:
    """Go back one page in the tab and return the status of the page gone back to; ``None`` when Chrome brought it back
    from its back-forward cache, with no response of its own. Done once the page is committed, as the caller settles it:
    a page the cache brings back fires no load and no DOMContentLoaded again, so waiting for one held each Back for the
    whole navigation timeout in a Chrome that keeps the cache (the owner's, item 339), and then failed it."""
    try:
        if await _page_before(page) is None:
            raise BrowserError("there is no earlier page in this tab's history", {"url": page.url})
        response = await page.go_back(wait_until="commit", timeout=timeout_ms)
    except PlaywrightError as error:
        raise _failure("going back failed", page, {}, error) from error
    return None if response is None else response.status


async def live_locator(page: Page, ref: str, action: str) -> Locator:
    """Resolve a ref against the latest snapshot; a dead ref fails in milliseconds instead of at the action timeout."""
    if _REF_FORMAT.fullmatch(ref) is None:  # refs come from model output: never splice free text into a selector
        raise BrowserError(
            "the ref has the wrong format; use a ref from the latest snapshot, for example e12",
            {"action": action, "ref": ref, "url": page.url},
        )
    locator: Locator = page.locator(f"aria-ref={ref}")
    try:
        match_count: int = await _count(locator)
    except TimeoutError as error:
        raise BrowserError(
            "the page did not answer in time (a script may be holding it); wait, then take a new snapshot",
            {"action": action, "ref": ref, "url": page.url, "waited_s": _COUNT_TIMEOUT_S},
        ) from error
    except PlaywrightError as error:
        if _STALE_GENERATION in str(error):
            raise BrowserError(
                "the page navigated since the snapshot; take a new snapshot and use its refs",
                {"action": action, "ref": ref, "url": page.url},
            ) from error
        raise _failure(f"{action} failed", page, {"ref": ref}, error) from error
    if match_count == 0:
        raise BrowserError(
            "the ref is not in the latest snapshot (the element changed or the page re-rendered); take a new snapshot",
            {"action": action, "ref": ref, "url": page.url},
        )
    return locator


async def _dropdown_of(page: Page, locator: Locator, ref: str, timeout_ms: int) -> Dropdown | None:
    try:
        raw: object = await locator.evaluate(_DROPDOWN_SCRIPT, timeout=timeout_ms)
    except PlaywrightError as error:
        raise _failure("reading the options failed", page, {"ref": ref}, error) from error
    return None if raw is None else Dropdown.model_validate(raw)


def _ref_of(step: Step) -> str:
    if step.ref is None:
        raise BrowserError("the step needs a ref", {"action": step.action})
    return step.ref


def _to_ref_of(step: Step) -> str:
    if step.to_ref is None:
        raise BrowserError("the step needs a to_ref", {"action": step.action})
    return step.to_ref


def _text_of(step: Step) -> str:
    if step.text is None:
        raise BrowserError("the step needs text", {"action": step.action})
    return step.text


def _key_of(step: Step) -> str:
    if step.key is None:
        raise BrowserError("the step needs a key", {"action": step.action})
    return step.key


async def _check_secret_field(page: Page, marking: object, ref: str | None, check: SecretFieldCheck) -> None:
    """Hand a field that takes a secret, by its ``marking`` (:data:`_FIELD_MARKING_SCRIPT`), to ``check`` before any
    text goes into it."""
    kind: SecretKind | None = secret_kind(marking)
    if kind is not None:
        await check(SecretField(ref=ref, kind=kind, url=page.url))


async def _focused_marking(page: Page) -> object:
    """The marking of the element that has the keyboard focus, followed from the page into the frame that holds it:
    the keys of ``type`` and ``press`` go there, whatever element their ref names."""
    frame: Frame = page.main_frame
    while True:
        handle: JSHandle = await frame.evaluate_handle(_FOCUSED_SCRIPT)
        try:
            element: ElementHandle | None = handle.as_element()
            if element is None:
                return None
            inner: Frame | None = await element.content_frame()
            if inner is None:
                return await element.evaluate(_FIELD_MARKING_SCRIPT)
        finally:
            await handle.dispose()
        frame = inner


async def _dropdown_offering(page: Page, locator: Locator, ref: str, wanted: str, timeout_ms: int) -> Dropdown | None:
    """The dropdown as soon as it offers ``wanted`` or, failing that, as it is after a short wait; ``None`` when the
    element is not a native ``<select>``."""
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    deadline: float = loop.time() + _OPTION_GRACE_S
    while True:
        dropdown: Dropdown | None = await _dropdown_of(page, locator, ref, timeout_ms)
        if dropdown is None or has_option(dropdown, wanted) or loop.time() >= deadline:
            return dropdown
        await asyncio.sleep(_OPTION_POLL_S)


async def _select_option(page: Page, locator: Locator, ref: str, wanted: str, timeout_ms: int) -> None:
    """Choose an option by value or label. Playwright waits the whole action timeout for an option that is not there;
    a native dropdown without it answers after a second at most, with the options it has."""
    dropdown: Dropdown | None = await _dropdown_offering(page, locator, ref, wanted, timeout_ms)
    if dropdown is not None and not has_option(dropdown, wanted):
        raise BrowserError(
            "the dropdown has no option with that value or label",
            {
                "action": "select",
                "ref": ref,
                "url": page.url,
                "wanted": wanted,
                "error": render_options(entries_of(closest_first(dropdown.options, wanted)), _OPTIONS_ERROR_CHARS),
            },
        )
    await locator.select_option(wanted, timeout=timeout_ms)


async def _into_editor(
    page: Page,
    locator: Locator,
    ref: str,
    timeout_ms: int,
    secret_field: SecretFieldCheck,
    expected: str | None,
    write: Callable[[], Awaitable[None]],
) -> bool:
    """Run ``write`` into the editor the element is in (:data:`_EDITOR_READY_SCRIPT`): once the editor has the focus and
    is ready, the field that has the focus goes to ``secret_field``; after the write, a step the editor did not take
    fails instead of reporting done (:data:`_EDITOR_TOOK_SCRIPT`, with ``expected`` as there). ``False``, with nothing
    done, when the element is in no editor."""
    element: ElementHandle = await locator.element_handle(timeout=timeout_ms)
    try:
        state: object = await element.evaluate(_EDITOR_READY_SCRIPT, [_EDITOR_QUIET_MS, _EDITOR_MAX_MS])
        if state == "plain":
            return False
        if state == "replaced":
            raise BrowserError(
                "the editor was replaced when it took the focus; take a new snapshot", {"ref": ref, "url": page.url}
            )
        await _check_secret_field(page, await _focused_marking(page), ref, secret_field)
        await write()
        try:
            took: object = await element.evaluate(_EDITOR_TOOK_SCRIPT, [_EDITOR_MAX_MS, expected])
        except PlaywrightError as error:
            if not _document_replaced(error):
                raise
            return True  # the write made the page navigate, as Enter in a search box does
    finally:
        await element.dispose()
    if took == "dropped":
        raise BrowserError(
            "the editor did not take the text: its text did not change. Look at the page, then try the step again",
            {"ref": ref, "url": page.url},
        )
    if took == "replaced":
        raise BrowserError(
            "the editor was replaced while the text went in, so it is not known whether it took the text: look at the "
            "page before writing again",
            {"ref": ref, "url": page.url},
        )
    return True


async def run_step(page: Page, step: Step, timeout_ms: int, secret_field: SecretFieldCheck) -> None:
    """Perform one step against the latest snapshot. A field that takes a secret goes to ``secret_field`` first, which
    raises to refuse the step: the field ``fill`` writes into, the one ``drag`` drops onto, and the one that has the
    keyboard focus when ``type``, ``insert``, or ``press`` with a key that puts text in, is about to send its text."""
    if step.action == "click_at":
        if step.x is None or step.y is None:
            raise BrowserError("click_at needs x and y", {"url": page.url})
        try:
            await page.mouse.click(step.x, step.y)
        except PlaywrightError as error:
            raise _failure("click_at failed", page, {"x": str(step.x), "y": str(step.y)}, error) from error
        return
    if step.action == "press" and step.ref is None:
        key: str = _key_of(step)
        try:
            if key_enters_text(key):
                await _check_secret_field(page, await _focused_marking(page), None, secret_field)
            await page.keyboard.press(key)
        except PlaywrightError as error:
            raise _failure("press failed", page, {"key": step.key}, error) from error
        return
    ref: str = _ref_of(step)
    locator: Locator = await live_locator(page, ref, step.action)
    try:
        match step.action:
            case "click":
                await locator.click(timeout=timeout_ms)
            case "double_click":
                await locator.dblclick(timeout=timeout_ms)
            case "right_click":
                await locator.click(button="right", timeout=timeout_ms)
            case "hover":
                await locator.hover(timeout=timeout_ms)
            case "fill":
                await _check_secret_field(page, await locator.evaluate(_FIELD_MARKING_SCRIPT), ref, secret_field)
                if not await _into_editor(
                    page,
                    locator,
                    ref,
                    timeout_ms,
                    secret_field,
                    _text_of(step),
                    lambda: locator.fill(_text_of(step), timeout=timeout_ms),
                ):
                    await locator.fill(_text_of(step), timeout=timeout_ms)
            case "type":
                if not await _into_editor(
                    page, locator, ref, timeout_ms, secret_field, None, lambda: page.keyboard.type(_text_of(step))
                ):
                    await locator.focus(timeout=timeout_ms)
                    await _check_secret_field(page, await _focused_marking(page), ref, secret_field)
                    await locator.press_sequentially(_text_of(step), timeout=timeout_ms)
            case "insert":
                if not await _into_editor(
                    page,
                    locator,
                    ref,
                    timeout_ms,
                    secret_field,
                    None,
                    lambda: page.keyboard.insert_text(_text_of(step)),
                ):
                    await locator.focus(timeout=timeout_ms)
                    await _check_secret_field(page, await _focused_marking(page), ref, secret_field)
                    if not await locator.evaluate(_TAKES_TEXT_SCRIPT, timeout=timeout_ms):
                        raise BrowserError(
                            "insert needs a text field or an editor that takes the focus; this element takes no text",
                            {"ref": ref, "url": page.url},
                        )
                    await page.keyboard.insert_text(_text_of(step))
            case "drag":
                target: Locator = await live_locator(page, _to_ref_of(step), "drag")
                await _check_secret_field(
                    page, await target.evaluate(_FIELD_MARKING_SCRIPT, timeout=timeout_ms), step.to_ref, secret_field
                )
                try:
                    await locator.drag_to(target, steps=_DRAG_MOVES, timeout=timeout_ms)
                except BaseException:
                    await page.mouse.up()  # a drag that fails or is cancelled after the press leaves the button down
                    raise
            case "select":
                await _select_option(page, locator, ref, _text_of(step), timeout_ms)
            case "press":
                if key_enters_text(_key_of(step)):
                    await locator.focus(timeout=timeout_ms)
                    await _check_secret_field(page, await _focused_marking(page), ref, secret_field)
                await locator.press(_key_of(step), timeout=timeout_ms)
            case "check":
                await locator.check(timeout=timeout_ms)
            case "uncheck":
                await locator.uncheck(timeout=timeout_ms)
            case "scroll_into_view":
                await locator.scroll_into_view_if_needed(timeout=timeout_ms)
            case "click_at":
                raise BrowserError("click_at is handled before refs are resolved", {"url": page.url})
    except PlaywrightError as error:
        raise _failure(f"{step.action} failed", page, {"ref": ref}, error) from error


async def scroll_page(
    page: Page,
    direction: Literal["down", "up", "top", "bottom"],
    pixels: int,
    ref: str | None,
    viewport_width: int,
    viewport_height: int,
    timeout_ms: int,
) -> None:
    """Scroll with the mouse wheel over the page centre, or over the element ``ref`` when it is a scroll container."""
    try:
        if ref is None:
            await page.mouse.move(viewport_width / 2, viewport_height / 2)
        else:
            locator: Locator = await live_locator(page, ref, "scroll")
            await locator.hover(timeout=timeout_ms)
        match direction:
            case "down":
                await page.mouse.wheel(0, pixels)
            case "up":
                await page.mouse.wheel(0, -pixels)
            case "bottom":
                await page.mouse.wheel(0, _WHEEL_TO_END)
            case "top":
                await page.mouse.wheel(0, -_WHEEL_TO_END)
    except PlaywrightError as error:
        raise _failure("scrolling failed", page, {"direction": direction, "ref": ref}, error) from error


async def screenshot_jpeg(page: Page, quality: int, timeout_ms: int, hide_secrets: bool) -> bytes:
    """Viewport screenshot in CSS pixels (so a pixel in the picture is a pixel for ``click_at``, also on Retina). With
    ``hide_secrets`` (every mode but yolo, item 467) the fields the page marks secret are covered, in every frame."""
    mask: list[Locator] = (
        [frame.locator(secret_selector()) for frame in page.frames if not frame.is_detached()] if hide_secrets else []
    )
    try:
        return await page.screenshot(
            type="jpeg",
            quality=quality,
            animations="disabled",
            caret="initial",
            scale="css",
            timeout=timeout_ms,
            mask=mask,
            mask_color=_MASK_COLOUR,
        )
    except PlaywrightError as error:
        raise _failure("taking the screenshot failed", page, {}, error) from error


async def visible_text(page: Page, ref: str | None, timeout_ms: int) -> str:
    """The rendered text of the whole page, or of the element ``ref``."""
    try:
        if ref is None:
            text: object = await page.evaluate(_BODY_TEXT_SCRIPT)
            return text if isinstance(text, str) else ""
        locator: Locator = await live_locator(page, ref, "read")
        return await locator.inner_text(timeout=timeout_ms)
    except PlaywrightError as error:
        raise _failure("reading the page text failed", page, {"ref": ref}, error) from error


# How Playwright begins the error of a script, and the start of a line of its call stack.
_EVALUATE_PREFIX: str = "Page.evaluate: "
_STACK_LINE: str = "    at "
# What Playwright says when the page navigated while a script ran (only this: a script's own error may say "navigator").
_CONTEXT_DESTROYED: str = "Execution context was destroyed"


def _script_error(raw: str) -> str:
    """The message a script threw, without Playwright's prefix and the lines of the call stack (Playwright's own frames
    and the anonymous ones of the script)."""
    lines: list[str] = raw.removeprefix(_EVALUATE_PREFIX).splitlines()
    return "\n".join(line for line in lines if not line.startswith(_STACK_LINE)).strip()


async def run_script(page: Page, script: str, timeout_s: float) -> tuple[ScriptEnd, str]:
    """Run ``script`` in the page's own JavaScript world (item 263) and wait at most ``timeout_s`` for its value:
    Playwright calls a function and evaluates anything else, and waits for a promise either gives. Returns how it ended
    and the JSON of its value (``null`` for ``undefined``; a value JSON cannot hold, a DOM node, comes as Playwright
    writes it) or the message it threw. A script still running when the wait ends goes on in the page."""
    try:
        async with asyncio.timeout(timeout_s):
            value: object = await page.evaluate(script)
    except TimeoutError:
        return "timeout", ""
    except PlaywrightError as error:
        if page.is_closed():
            raise _failure("the tab closed while the script ran", page, {}, error) from error
        if _CONTEXT_DESTROYED in str(error):
            return "navigated", ""
        return "error", _script_error(str(error))
    return "value", json.dumps(value, ensure_ascii=False, default=str)


async def measure_viewport(page: Page) -> Viewport:
    """The page area of ``page`` that is on screen. A browser Dot did not size itself (the open Chrome of the user)
    has the size of its window, which only the page can tell."""
    try:
        reported: object = await page.evaluate(_VIEWPORT_SCRIPT)
    except PlaywrightError as error:
        raise _failure("measuring the viewport failed", page, {}, error) from error
    if (
        not isinstance(reported, list)
        or len(reported) != 2
        or not all(isinstance(side, int | float) and not isinstance(side, bool) and side > 0 for side in reported)
    ):
        raise BrowserError("the page reported no viewport size", {"url": page.url, "reported": repr(reported)[:200]})
    return Viewport(width=int(reported[0]), height=int(reported[1]))


async def keep_focused(page: Page) -> None:
    """Make ``page`` behave as if its window had focus, as Playwright does in a browser it started itself. Without it a
    tab of the Chrome of the user gets no focus events while another window is in front, and widgets that wait for them
    (autocomplete boxes, editors) stay dead."""
    try:
        session: CDPSession = await page.context.new_cdp_session(page)
        await session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
    except PlaywrightError as error:
        raise _failure("keeping the page focused failed", page, {}, error) from error


async def bring_to_front(page: Page) -> None:
    """Make ``page`` the visible tab of its window (used when the model switches tabs)."""
    try:
        await page.bring_to_front()
    except PlaywrightError as error:
        raise _failure("switching to the tab failed", page, {}, error) from error


async def wait_for_text(page: Page, text: str, timeout_ms: int) -> bool:
    """Whether ``text`` became visible on the page within ``timeout_ms`` (in any of its matches: an earlier hidden one,
    such as a template or a closed menu, does not hold the wait)."""
    try:
        await page.get_by_text(text).filter(visible=True).first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return False
    except PlaywrightError as error:
        raise _failure("waiting for the text failed", page, {"text": text}, error) from error
    return True


async def upload_files(page: Page, ref: str, paths: list[str], timeout_ms: int) -> None:
    """Click the element ``ref`` and hand the files to the file chooser it opens."""
    trigger: Locator = await live_locator(page, ref, "upload")
    try:
        async with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
            await trigger.click(timeout=timeout_ms)
        chooser = await chooser_info.value
        await chooser.set_files(paths)
    except PlaywrightError as error:
        raise _failure("uploading the files failed", page, {"ref": ref}, error) from error


async def _banner_gone(banner: Locator, url: str) -> bool:
    """Whether the banner left the page; one that cannot be counted, or not in time, is taken as still there, with why
    in the log."""
    try:
        return await _count(banner) == 0
    except TimeoutError:
        _log.warning("consent_banner_uncounted", url=truncate_middle(url, _ERROR_CHARS), error="no answer in time")
        return False
    except PlaywrightError as error:
        _log.warning(
            "consent_banner_uncounted",
            url=truncate_middle(url, _ERROR_CHARS),
            error=truncate_middle(str(error), _ERROR_CHARS),
        )
        return False


async def install_consent_handler(page: Page) -> None:
    """Decline cookie banners whenever one blocks an action; banners without a decline button are left alone. A click
    that fails because the banner went away under it (it closed, or the page did) is the outcome the handler is after;
    any other failure of the click is logged with the page's address. Neither reaches Playwright, which would print
    the error to stderr and count the use all the same."""
    banner: Locator = page.locator(_CONSENT_BANNERS).filter(has=page.get_by_role("button", name=_DECLINE_BUTTON)).first

    async def decline() -> None:
        try:
            await banner.get_by_role("button", name=_DECLINE_BUTTON).first.click(timeout=_CONSENT_CLICK_TIMEOUT_MS)
        except PlaywrightError as error:
            if page.is_closed() or await _banner_gone(banner, page.url):
                return
            _log.warning(
                "consent_decline_failed",
                url=truncate_middle(page.url, _ERROR_CHARS),
                error=truncate_middle(str(error), _ERROR_CHARS),
            )

    await page.add_locator_handler(banner, decline, no_wait_after=True, times=_CONSENT_HANDLER_TIMES)

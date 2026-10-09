"""Playwright calls that touch one page. Each function does one thing and turns driver errors into ``BrowserError``."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Literal

from playwright.async_api import CDPSession, ElementHandle, Frame, JSHandle, Locator, Page
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.safety import key_enters_text, secret_kind
from dotagent.browser.snapshot import (
    SnapshotLine,
    closest_first,
    entries_of,
    fold_options,
    has_option,
    parse_snapshot,
    render_options,
)
from dotagent.browser.types import Dropdown, SecretField, SecretFieldCheck, SecretKind, Step, Viewport
from dotagent.core.errors import BrowserError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle

_log = get_logger("dotagent.browser.driver")

_REF_FORMAT: re.Pattern[str] = re.compile(r"^(f\d+)?e\d+$")
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

# Resolves once the DOM has been quiet for quietMs, or after maxMs at the latest.
_QUIET_SCRIPT: str = """([quietMs, maxMs]) => new Promise((resolve) => {
  let quietTimer = 0;
  const finish = () => { observer.disconnect(); clearTimeout(quietTimer); clearTimeout(hardTimer); resolve(null); };
  const observer = new MutationObserver(() => { clearTimeout(quietTimer); quietTimer = setTimeout(finish, quietMs); });
  observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  quietTimer = setTimeout(finish, quietMs);
  const hardTimer = setTimeout(finish, maxMs);
})"""

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


async def capture_lines(page: Page, timeout_ms: int) -> tuple[SnapshotLine, ...]:
    """One AI snapshot with boxes, the options of collapsed dropdowns folded onto their lines. Taking it replaces the
    ref table of every frame, so refs always mean the latest."""
    raw: str = await _once_more_after_navigation(
        page,
        "taking the page snapshot failed",
        lambda: page.aria_snapshot(mode="ai", boxes=True, timeout=timeout_ms),
    )
    return fold_options(parse_snapshot(raw))


async def page_title(page: Page) -> str:
    return await _once_more_after_navigation(page, "reading the page title failed", page.title)


async def _wait_quiet(page: Page, quiet_ms: int, max_ms: int, timeout_ms: int) -> None:
    await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    await page.evaluate(_QUIET_SCRIPT, [quiet_ms, max_ms])


async def wait_dom_quiet(page: Page, quiet_ms: int, max_ms: int, timeout_ms: int) -> None:
    """Wait for DOMContentLoaded and then for a DOM quiet window (never ``networkidle``, which analytics defeat)."""
    await _once_more_after_navigation(
        page,
        "waiting for the page to settle failed",
        lambda: _wait_quiet(page, quiet_ms, max_ms, timeout_ms),
    )


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


async def go_back(page: Page, timeout_ms: int) -> int | None:
    try:
        response = await page.go_back(wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightError as error:
        raise _failure("going back failed", page, {}, error) from error
    if response is None:
        raise BrowserError("there is no earlier page in this tab's history", {"url": page.url})
    return response.status


async def live_locator(page: Page, ref: str, action: str) -> Locator:
    """Resolve a ref against the latest snapshot; a dead ref fails in milliseconds instead of at the action timeout."""
    if _REF_FORMAT.fullmatch(ref) is None:  # refs come from model output: never splice free text into a selector
        raise BrowserError(
            "the ref has the wrong format; use a ref from the latest snapshot, for example e12",
            {"action": action, "ref": ref, "url": page.url},
        )
    locator: Locator = page.locator(f"aria-ref={ref}")
    try:
        match_count: int = await locator.count()
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


async def run_step(page: Page, step: Step, timeout_ms: int, secret_field: SecretFieldCheck) -> None:
    """Perform one step against the latest snapshot. A field that takes a secret goes to ``secret_field`` first, which
    raises to refuse the step: the field ``fill`` writes into, and the one that has the keyboard focus when ``type``,
    or ``press`` with a key that puts text in, is about to send its keys."""
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
                await locator.fill(_text_of(step), timeout=timeout_ms)
            case "type":
                await locator.focus(timeout=timeout_ms)
                await _check_secret_field(page, await _focused_marking(page), ref, secret_field)
                await locator.press_sequentially(_text_of(step), timeout=timeout_ms)
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


async def screenshot_jpeg(page: Page, quality: int, timeout_ms: int) -> bytes:
    """Viewport screenshot in CSS pixels (so a pixel in the picture is a pixel for ``click_at``, also on Retina)."""
    try:
        return await page.screenshot(
            type="jpeg", quality=quality, animations="disabled", caret="initial", scale="css", timeout=timeout_ms
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


async def wait_for_text(page: Page, text: str, timeout_ms: int) -> None:
    try:
        await page.get_by_text(text).first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightError as error:
        raise _failure("the text did not appear", page, {"text": text}, error) from error


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


async def install_consent_handler(page: Page) -> None:
    """Decline cookie banners whenever one blocks an action; banners without a decline button are left alone."""
    banner: Locator = page.locator(_CONSENT_BANNERS).filter(has=page.get_by_role("button", name=_DECLINE_BUTTON)).first

    async def decline() -> None:
        await banner.get_by_role("button", name=_DECLINE_BUTTON).first.click(timeout=_CONSENT_CLICK_TIMEOUT_MS)

    await page.add_locator_handler(banner, decline, no_wait_after=True, times=_CONSENT_HANDLER_TIMES)

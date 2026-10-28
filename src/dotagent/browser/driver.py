"""Playwright calls that touch one page. Each function does one thing and turns driver errors into ``BrowserError``."""

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from playwright.async_api import CDPSession, Cookie, ElementHandle, Frame, JSHandle, Locator, Page
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
from dotagent.browser.types import (
    Dropdown,
    HeldValue,
    ScriptEnd,
    SecretField,
    SecretFieldCheck,
    SecretKind,
    Step,
    VaultFieldKind,
    Viewport,
)
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
# The shortest cookie value that the value of a script is searched for (item 533).
_MIN_HIDDEN_VALUE_CHARS: int = 8
# A cookie value that is a word of lower case letters (a consent: ``dismissed``) is no session.
_PLAIN_WORD: re.Pattern[str] = re.compile(r"[a-z][a-z_-]*")
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

# The element a step writes into when it is given ``element``: a label, or an element inside one (the AI snapshot gives
# a div, a span or a b in a label a ref of its own), is its label's control, which is where Playwright's fill writes
# (its retarget(node, "follow-label")). It is one definition: the marking of the field, the control a login is filled
# into (:func:`fill_login_field`) and everything that is asked of it are made on the element this gives (item 621; a
# check that read the element a ref names while the fill wrote the control let a password into a form that sends by
# GET).
_CONTROL_OF: str = """const controlOf = (element) => {
  let field = element;
  if (!field.matches('input, textarea, select') && !field.isContentEditable)
    field = field.closest('button, [role=button], [role=checkbox], [role=radio]') || field;
  if (!field.matches('a, input, textarea, button, select, [role=link], [role=button], [role=checkbox], [role=radio]')
      && !field.isContentEditable) {
    const label = field.closest('label');
    if (label && label.control) field = label.control;
  }
  return field;
};"""
_CONTROL_SCRIPT: str = "(element) => {\n" + _CONTROL_OF + "\n  return controlOf(element);\n}"
# What the field a step writes into says about the secret it takes: "password" for a password input, then its
# autocomplete tokens; null for an element that takes no typed text and no choice (a dropdown takes one: a card's
# expiry month and year are often dropdowns, item 554). The model never sees this check and cannot disable it. A field
# Dot filled a value into says "off" in its own autocomplete (:data:`_MARK_FIELD`, item 621) and keeps what the page
# wrote there in ``data-dot-autocomplete``, which is what counts as its marking.
_FIELD_MARKING_SCRIPT: str = (
    "(element) => {\n"
    + _CONTROL_OF
    + """
  const field = controlOf(element);
  const takesValue = field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement
    || field instanceof HTMLSelectElement;
  if (!takesValue) return null;
  const filled = field.hasAttribute('data-dot-vault') ? ' dot-vault' : '';
  const written = field.hasAttribute('data-dot-autocomplete')
    ? field.getAttribute('data-dot-autocomplete') : field.getAttribute('autocomplete');
  const marking = (written || '') + filled;
  return field instanceof HTMLInputElement && field.type === 'password' ? `password ${marking}` : marking;
}"""
)
# What the marking of a field says when Dot filled a login into it (item 621): the value of such a field shows as
# hidden in every view of the page, in every mode.
_VAULT_MARK: str = "dot-vault"
# The secrets each part of a login may go into (item 493): a password into a password field alone, so that it shows in
# no text; a one-time code into a field marked for one or a plain one; a username into anything but a password field.
_PASSWORD_KINDS: frozenset[SecretKind] = frozenset({"password", "current-password", "new-password"})
# The form a field is in, made to send by POST before a password or a code goes into the field (item 775; item 493
# refused the field instead, and a sign-in page whose script sends its form, which names no method, got no login):
# - a form whose method is GET (``form.method`` is what a browser makes of the attribute: GET for none, and for a word
#   no browser knows) gets ``method="post"``, and a control of it that names ``formmethod`` GET loses it; a dialog form
#   sends nothing and is left alone;
# - a browser reads the method once the ``submit`` event has run its listeners, so the window's first and last
#   listeners put the form back to POST, undoing a script of the page that set GET again; a form in a shadow root,
#   whose ``submit`` never reaches the window, gets them on its root;
# - ``form.submit()`` fires no event: the form gets an own ``submit`` that sets POST first, unless a control named
#   ``submit`` hides the method (the page cannot call it either).
# A control named ``method``, ``elements`` or ``setAttribute`` hides the form's own of that name, so they are read from
# the prototypes. It is asked of the control (:data:`_CONTROL_OF`) and of no other element: a div has no form. It
# returns whether the form would have sent by GET. A page whose script sends the form itself is not changed: the guard
# of Dot's Chrome refuses a fetch or a frame of the page whose address carries a password or a one-time code (item 621).
_POSTS_SCRIPT: str = """(field) => {
  const form = field.form;
  if (!form) return false;
  const own = (name) => Object.getOwnPropertyDescriptor(HTMLFormElement.prototype, name).get;
  const methodOf = own('method');
  const elementsOf = own('elements');
  const { hasAttribute, setAttribute, removeAttribute } = Element.prototype;
  const byGet = (target) => methodOf.call(target) === 'get'
    || [...elementsOf.call(target)].some((control) => hasAttribute.call(control, 'formmethod')
      && control.formMethod === 'get');
  const post = (target) => {
    if (methodOf.call(target) === 'get') setAttribute.call(target, 'method', 'post');
    for (const control of elementsOf.call(target))
      if (hasAttribute.call(control, 'formmethod') && control.formMethod === 'get')
        removeAttribute.call(control, 'formmethod');
  };
  const was = byGet(form);
  post(form);
  if (!hasAttribute.call(form, 'data-dot-posts')) {
    setAttribute.call(form, 'data-dot-posts', '');
    const atSubmit = (event) => { if (event.target === form) post(form); };
    const root = Node.prototype.getRootNode.call(form);
    for (const at of root instanceof ShadowRoot ? [window, root] : [window]) {
      at.addEventListener('submit', atSubmit, true);
      at.addEventListener('submit', atSubmit, false);
    }
    const native = HTMLFormElement.prototype.submit;
    if (form.submit === native) {
      const submit = function () { post(form); native.call(form); };
      Object.defineProperty(form, 'submit', { configurable: true, value: submit });
    }
  }
  return was;
}"""
# The mark of a field a login was filled into (items 493 and 621), which every screenshot covers, in every mode: the
# field shows the username, and a password once the page shows it.
_FILLED_SELECTOR: str = "[data-dot-vault]"
# What Dot does to a field it fills a value into, before the value goes in, and again to a field of a later document
# that holds a value it filled (:func:`remark_held`):
# - the field is marked (``data-dot-vault``), so that every view of the page shows its value as hidden and every
#   screenshot covers it;
# - its autocomplete says "off", and what the page wrote there moves to ``data-dot-autocomplete``. Chrome keeps no value
#   of such a field in the state it saves for a page: not to give it back when the page is gone back to or forward to,
#   not in the files of its session on disk, not among the entries it makes of a form that was sent (measured for an
#   input and for a dropdown).
_MARK_FIELD: str = """const markField = (element) => {
  if (!element.hasAttribute('data-dot-autocomplete'))
    element.setAttribute('data-dot-autocomplete', element.getAttribute('autocomplete') || '');
  element.setAttribute('autocomplete', 'off');
  element.setAttribute('data-dot-vault', '');
};"""
_PREPARE_SCRIPT: str = f"(element) => {{ {_MARK_FIELD} markField(element); }}"
_REMARK_SCRIPT: str = (
    f"(elements, indexes) => {{ {_MARK_FIELD} for (const index of indexes) markField(elements[index]); }}"
)
# Every field of a document, in the open shadow roots it holds too, in page order, up to a limit; and what each holds:
# its value and, for a dropdown, the label of the option it shows (a text longer than a password may be is left out,
# so that a page with a long text in a box sends none of it). Given the address the frame had when Dot read it, it
# returns before it touches a field when the document is another (``null``): a frame that moves between Dot's reading
# of its address and this script (item 621, N1) is a frame Dot has not looked at.
_FIELDS_LIMIT: int = 2000
_ALL_FIELDS_SCRIPT: str = f"""(expected) => {{
  if (document.URL !== expected) return null;
  const found = [];
  const walk = (root) => {{
    for (const element of root.querySelectorAll('input, textarea, select')) {{
      if (found.length < {_FIELDS_LIMIT}) found.push(element);
    }}
    for (const host of root.querySelectorAll('*')) {{
      if (host.shadowRoot && found.length < {_FIELDS_LIMIT}) walk(host.shadowRoot);
    }}
  }};
  walk(document);
  return found;
}}"""
_MOST_HELD_CHARS: int = 600
_FIELD_VALUES_SCRIPT: str = f"""(elements) => elements.map((element) => {{
  const short = (text) => (text.length <= {_MOST_HELD_CHARS} ? text : '');
  return element instanceof HTMLSelectElement
    ? [short(element.value), element.selectedOptions.length ? short(element.selectedOptions[0].label) : '']
    : [short(element.value)];
}})"""
# The shortest value (letters and digits only) that a field is marked for holding: a month is two digits and a number
# in any quantity field would be taken for it.
_HELD_FROM_CHARS: int = 3
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


# How many documents a read goes through when each is replaced under it (item 752): a sign-in hops through several
# pages by itself (entra.microsoft.com, login.microsoftonline.com, its error page, back), and a read that met the second
# hop failed the whole call. How long it waits for each new document to be parsed before it reads again.
_NAVIGATION_TRIES: int = 5
_NEW_DOCUMENT_WAIT_MS: int = 5000


async def _again_after_navigation[T](page: Page, what: str, call: Callable[[], Awaitable[T]]) -> T:
    """Run ``call``; while the document is replaced under it (a page that sends itself on, a sign-in's chain of
    redirects), wait for the new document to be parsed and run it again there, :data:`_NAVIGATION_TRIES` times at
    most. Any other failure, or the last, fails at once."""
    for attempt in range(1, _NAVIGATION_TRIES + 1):
        try:
            return await call()
        except PlaywrightError as error:
            if attempt == _NAVIGATION_TRIES or not _document_replaced(error) or page.is_closed():
                raise _failure(what, page, {"attempts": str(attempt)}, error) from error
            _log.info("browser_document_replaced", what=what, attempt=attempt, url=truncate_middle(page.url, 200))
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_load_state("domcontentloaded", timeout=_NEW_DOCUMENT_WAIT_MS)
    raise AssertionError("unreachable")


# The part of a page a snapshot falls back to when the whole is too heavy for it (item 743): the biggest element around
# the middle of the window that holds at most this many elements, found by sampling the window on a grid.
_PARTIAL_BUDGET: int = 3000
_PARTIAL_ROOT_SCRIPT: str = """(budget) => {
  const count = (node) => node.getElementsByTagName('*').length;
  const chunks = new Map();
  for (let column = 0; column < 6; column++) {
    for (let row = 0; row < 4; row++) {
      let node = document.elementFromPoint(innerWidth * (2 * column + 1) / 12, innerHeight * (2 * row + 1) / 8);
      while (node && node.parentElement && count(node.parentElement) <= budget) node = node.parentElement;
      if (node) chunks.set(node, (chunks.get(node) || 0) + 1);
    }
  }
  let best = null;
  for (const [node, seen] of chunks) {
    const better = best === null || seen > chunks.get(best)
      || (seen === chunks.get(best) && count(node) > count(best));
    if (better) best = node;
  }
  if (best === null || best === document.documentElement || best === document.body) return null;
  const path = [];
  for (let node = best; node && node !== document.documentElement; node = node.parentElement) {
    const siblings = node.parentElement ? Array.from(node.parentElement.children) : [];
    path.unshift(node.tagName.toLowerCase() + ':nth-child(' + (siblings.indexOf(node) + 1) + ')');
  }
  return {selector: 'html > ' + path.join(' > '), elements: count(best) + 1, tag: best.tagName.toLowerCase()};
}"""


class PageLines(NamedTuple):
    """What :func:`capture_page` read: the lines of the snapshot and, when the page was too heavy for a snapshot of the
    whole and only a part of it was read, a sentence that says which (empty for a whole one)."""

    lines: tuple[SnapshotLine, ...]
    partial: str = ""


async def _partial_snapshot(page: Page, timeout_ms: int) -> tuple[str, str] | None:
    """The AI snapshot of the part of the page around the middle of the window (:data:`_PARTIAL_ROOT_SCRIPT`) and the
    sentence that says it is a part; ``None`` when no part smaller than the page can be named or it cannot be read
    either. Taking it replaces the ref table as a whole snapshot does, so its refs are the ones that work."""
    try:
        # The page that was too slow for the whole may be too busy for this too: it gets the same time, no more.
        async with asyncio.timeout(timeout_ms / 1000):
            root: object = await page.evaluate(_PARTIAL_ROOT_SCRIPT, _PARTIAL_BUDGET)
        if not isinstance(root, dict) or not isinstance(root.get("selector"), str):
            return None
        raw: str = await page.locator(root["selector"]).aria_snapshot(mode="ai", boxes=True, timeout=timeout_ms)
    except (PlaywrightError, TimeoutError) as error:
        _log.warning(
            "browser_partial_snapshot_failed",
            url=page.url,
            error=truncate_middle(str(error) or "timeout", _ERROR_CHARS),
        )
        return None
    note: str = (
        f"Partial view: the page was too heavy to read whole in {timeout_ms / 1000:g} s, so this holds only a part of "
        f"it, a <{root.get('tag')}> of {root.get('elements')} elements around the middle of the window. Its refs "
        "work; for the rest use browser_find or browser_read_text, or scroll and look again."
    )
    return raw, note


async def capture_page(
    page: Page, timeout_ms: int, hide_secrets: bool, vault_filled: bool = False, held: Collection[HeldValue] = ()
) -> PageLines:
    """One AI snapshot with boxes, the options of collapsed dropdowns folded onto their lines. Taking it replaces the
    ref table of every frame, so refs always mean the latest. A page too heavy to be read within ``timeout_ms`` gives
    a snapshot of a part of it instead (item 743), and says so. With ``hide_secrets`` (every mode but yolo, item 467)
    the value of a field the page marks secret shows as ``[hidden]``; once a login was filled into the tab
    (``vault_filled``, item 621) so does the value of a field Dot filled it into, in every mode, and so does a field
    of a later document of the same origin that holds one of the values ``held`` (:func:`remark_held`)."""
    partial: str = ""
    try:
        raw: str = await _again_after_navigation(
            page,
            "taking the page snapshot failed",
            lambda: page.aria_snapshot(mode="ai", boxes=True, timeout=timeout_ms),
        )
    except BrowserError as failed:
        part: tuple[str, str] | None = (
            await _partial_snapshot(page, timeout_ms) if "Timeout" in str(failed.context.get("error", "")) else None
        )
        if part is None:
            raise
        raw, partial = part
    lines: tuple[SnapshotLine, ...] = parse_snapshot(raw)
    if vault_filled and held:
        await remark_held(page, held)
    if hide_secrets or vault_filled:
        lines = hide_values(lines, await secret_refs(page, filled_fields(lines), hide_secrets))
    return PageLines(fold_options(lines), partial)


async def _count(locator: Locator) -> int:
    """How many elements ``locator`` names; ``TimeoutError`` when the page does not say within ``_COUNT_TIMEOUT_S``."""
    async with asyncio.timeout(_COUNT_TIMEOUT_S):
        return await locator.count()


async def _is_secret(page: Page, ref: str, every_secret: bool = True) -> bool:
    """Whether the field ``ref`` is marked secret, or (item 621) is one Dot filled a login into. With
    ``every_secret`` false it is the second alone that counts. A ref of another shape is never put into a selector, and
    a field that cannot be asked (gone since the snapshot, its frame closed, too slow) counts as secret: a value is
    never shown for want of an answer."""
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
    if isinstance(marking, str) and _VAULT_MARK in marking.split():
        return True
    return every_secret and secret_kind(marking) is not None


async def secret_refs(page: Page, fields: tuple[SnapshotLine, ...], every_secret: bool = True) -> frozenset[str]:
    """The refs of ``fields`` that the page marks secret (item 467) and those Dot filled a login into (item 621), each
    field asked by its own ref, all at once; with ``every_secret`` false, the second alone. What the page is
    like now does not decide for the snapshot taken before: a field added and taken away since, in a frame that went or
    not, is gone, and so is hidden; only the field the snapshot showed answers for its value."""
    refs: list[str] = [field.ref for field in fields if field.ref is not None]
    answers: list[bool] = await asyncio.gather(*(_is_secret(page, ref, every_secret) for ref in refs))
    return frozenset(ref for ref, secret in zip(refs, answers, strict=True) if secret)


def _held_core(text: str) -> str:
    """``text`` as its letters and digits alone, in one case: how a page writes a number, an expiry or a code (``4242
    4242``, ``08 / 39``) does not make it another value."""
    return "".join(char for char in text.casefold() if char.isalnum())


def _holding(values: object, wanted: frozenset[str]) -> list[int]:
    """The places in ``values`` (what each field of a document holds, :data:`_FIELD_VALUES_SCRIPT`) of a field that
    holds one of ``wanted``."""
    if not isinstance(values, list):
        return []
    return [
        index
        for index, candidates in enumerate(values)
        if isinstance(candidates, list)
        and any(isinstance(candidate, str) and _held_core(candidate) in wanted for candidate in candidates)
    ]


_BLANK_DOCUMENTS: frozenset[str] = frozenset({"", "about:blank", "about:srcdoc"})


def document_of(frame: Frame) -> str | None:
    """The address of the document ``frame`` shows, for telling which site it is of: its own; for a blank one
    (``about:blank`` and ``about:srcdoc`` are made by a script of the frame they lie in, and are of its site) that of
    the frame it lies in, and so on up; for a blob (``blob:https://site/id``) the address it was made at. ``None`` when
    no document can be named: a blank main frame (a tab just opened, whose opener is not known here) or a frame that is
    gone. Nothing is done by what a value is to a document of which no site is named."""
    current: Frame | None = frame
    while current is not None:
        address: str = current.url.removeprefix("blob:")
        if address not in _BLANK_DOCUMENTS:
            return address
        current = current.parent_frame
    return None


async def remark_held(page: Page, held: Collection[HeldValue]) -> None:
    """Mark again every field that holds a value Dot filled into the tab, in each document of the origin the value was
    filled into (item 621): the page and its frames, a frame of another origin never looked into. A tab that goes back
    or forward is in a new document, whose fields Chrome, or the page's own script, may fill with the value again,
    without Dot's mark: a field that holds one of the values (their letters and digits alone compared, none shorter
    than :data:`_HELD_FROM_CHARS`) is marked as it was filled, so that every view hides its value and every screenshot
    covers it. What is compared stays in Dot: the values never go into the page, and no script runs in a document that
    was not given them: by which of its fields Dot marked, a page that never held a value would read it. The script is
    told the address the frame had when Dot read it and touches no field of a document that is another by then (a frame
    that moves between the reading and the script is one Dot has not looked at). A frame that does not answer in time
    is left and the log says so; the view asks each of its fields about its mark all the same, and a field that cannot
    be asked counts as secret."""
    work: list[Awaitable[None]] = []
    for frame in page.frames:
        if frame.is_detached():
            continue
        expected: str = frame.url
        document: str | None = document_of(frame)
        if document is None:
            continue
        wanted: frozenset[str] = frozenset(
            core
            for core in (_held_core(one.value) for one in held if one.covers(document))
            if len(core) >= _HELD_FROM_CHARS
        )
        if wanted:
            work.append(_remark_frame(frame, expected, wanted))
    await asyncio.gather(*work)


async def _remark_frame(frame: Frame, expected: str, wanted: frozenset[str]) -> None:
    """:func:`remark_held` in one frame, which has :data:`_FIELD_PROBE_TIMEOUT_MS` twice over to answer. ``expected``
    is the address the frame had when Dot read it: the script returns before it touches a field if the document is
    another."""
    try:
        async with asyncio.timeout(2 * _FIELD_PROBE_TIMEOUT_MS / 1000):
            fields: JSHandle = await frame.evaluate_handle(_ALL_FIELDS_SCRIPT, expected)
            try:
                if await fields.evaluate("(found) => found === null"):
                    _log.debug("browser_frame_moved", url=truncate_middle(expected, _ERROR_CHARS))
                    return
                found: list[int] = _holding(await fields.evaluate(_FIELD_VALUES_SCRIPT), wanted)
                if found:
                    await fields.evaluate(_REMARK_SCRIPT, found)
            finally:
                await fields.dispose()
    except (PlaywrightError, TimeoutError) as error:
        _log.warning(
            "browser_fields_not_checked",
            url=truncate_middle(frame.url, _ERROR_CHARS),
            error=truncate_middle(str(error), _ERROR_CHARS),
        )


async def page_title(page: Page) -> str:
    return await _again_after_navigation(page, "reading the page title failed", page.title)


async def _wait_quiet(page: Page, quiet_ms: int, max_ms: int, timeout_ms: int) -> None:
    """The document parsed, then quiet for ``quiet_ms`` (at most ``max_ms``). A tab whose page answers nothing at all
    within ``timeout_ms`` is told apart from one still parsing (item 753): a page stuck in a script, or a Chrome that
    stopped answering, was reported as a document not yet parsed. A page busy for a while (a heavy app starting on a
    loaded Mac) has the whole ``timeout_ms`` to answer, as before; the parse has what is left of it."""
    started: float = time.monotonic()
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            await page.evaluate("1")
    except TimeoutError as error:
        raise PlaywrightTimeoutError(
            f"the tab did not answer within {timeout_ms / 1000:g} s: its page is stuck, or Chrome is overloaded. Close "
            "this tab (browser_close_tab) and open the page in a new one"
        ) from error
    try:
        async with asyncio.timeout(max(timeout_ms / 1000 - (time.monotonic() - started), 0.001)):
            await page.evaluate(_PARSED_SCRIPT)
    except TimeoutError as error:
        raise PlaywrightTimeoutError(f"the document was not parsed within {timeout_ms} ms") from error
    try:
        # The script ends itself after max_ms; the bound is for a page that stops answering while it waits.
        async with asyncio.timeout((max_ms + timeout_ms) / 1000):
            await page.evaluate(_QUIET_SCRIPT, [quiet_ms, max_ms])
    except TimeoutError as error:
        raise PlaywrightTimeoutError(
            f"the tab stopped answering while the page settled ({max_ms} ms at most)"
        ) from error


async def wait_dom_quiet(page: Page, quiet_ms: int, max_ms: int, timeout_ms: int) -> None:
    """Wait for DOMContentLoaded and then for a DOM quiet window (never ``networkidle``, which analytics defeat)."""
    await _again_after_navigation(
        page,
        "waiting for the page to settle failed",
        lambda: _wait_quiet(page, quiet_ms, max_ms, timeout_ms),
    )


# What a step that should change the page did to it (item 739): how many changes the page's documents have had since
# Dot first asked (a MutationObserver of each frame's document), the alerts and live regions it shows, and the fields
# it marks invalid with what it says of each. Read in every frame, as a control may be in one.
_PULSE_SCRIPT: str = """() => {
  const w = window;
  if (!w.__dotPulse) {
    w.__dotPulse = { changes: 0 };
    try {
      new MutationObserver((list) => { w.__dotPulse.changes += list.length; })
        .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
      // What changes a page with no mutation: a box ticked, a field typed in, a <details> opened.
      for (const kind of ["input", "change", "toggle"]) {
        document.addEventListener(kind, () => { w.__dotPulse.changes += 1; }, true);
      }
    } catch (error) { w.__dotPulse.changes = -1; }
  }
  const shown = (el) => { const box = el.getBoundingClientRect(); return box.width > 0 && box.height > 0; };
  const words = (el) => (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 160);
  const alerts = [...document.querySelectorAll("[role=alert], [aria-live=assertive], [aria-live=polite]")]
    .filter(shown).map(words).filter(Boolean).slice(0, 8);
  const invalid = [...document.querySelectorAll(
    "[aria-invalid=true], input:invalid, select:invalid, textarea:invalid"
  )].filter(shown).slice(0, 8).map((el) => {
    const label = (el.labels && el.labels[0] ? words(el.labels[0]) : "") || el.getAttribute("aria-label")
      || el.getAttribute("name") || el.getAttribute("placeholder") || el.tagName.toLowerCase();
    return `${label.slice(0, 60)}: ${(el.validationMessage || "invalid").slice(0, 120)}`;
  });
  return { changes: w.__dotPulse.changes, alerts, invalid };
}"""
_PULSE_FRAMES: int = 10
_PULSE_TIMEOUT_S: float = 1.0


@dataclass(frozen=True, slots=True)
class Pulse:
    """The page as :data:`_PULSE_SCRIPT` reads it: the changes its documents had (``None`` when one could not be
    read, which then counts as a change), and what it shows of alerts and invalid fields."""

    changes: int | None
    alerts: tuple[str, ...]
    invalid: tuple[str, ...]


async def page_pulse(page: Page) -> Pulse:
    """What :data:`_PULSE_SCRIPT` reads of ``page``'s first :data:`_PULSE_FRAMES` frames, summed (item 739)."""
    frames: list[Frame] = [frame for frame in page.frames if not frame.is_detached()][:_PULSE_FRAMES]

    async def one(frame: Frame) -> object:
        try:
            async with asyncio.timeout(_PULSE_TIMEOUT_S):
                return await frame.evaluate(_PULSE_SCRIPT)
        except (TimeoutError, PlaywrightError):
            return None

    answers: list[object] = list(await asyncio.gather(*(one(frame) for frame in frames)))
    changes: int | None = 0
    alerts: list[str] = []
    invalid: list[str] = []
    for answer in answers:
        if not isinstance(answer, dict) or not isinstance(answer.get("changes"), int) or answer["changes"] < 0:
            changes = None
            continue
        if changes is not None:
            changes += answer["changes"]
        alerts.extend(str(one) for one in answer.get("alerts") or [] if isinstance(one, str))
        invalid.extend(str(one) for one in answer.get("invalid") or [] if isinstance(one, str))
    return Pulse(changes=changes, alerts=tuple(dict.fromkeys(alerts)), invalid=tuple(dict.fromkeys(invalid)))


# Whether the element a click named now has the focus as a field that takes input (a text field, a box, a dropdown,
# an edited element), itself, inside it or as the field of its label: a change no element of the page shows.
_HOLDS_FIELD_FOCUS_SCRIPT: str = """(el) => {
  const focused = el.ownerDocument.activeElement;
  if (!focused || !(focused === el || el.contains(focused) || el.control === focused)) return false;
  return focused.isContentEditable || focused.matches(
    "input:not([type=button]):not([type=submit]):not([type=reset]):not([type=image]), textarea, select"
  );
}"""


async def holds_field_focus(page: Page, ref: str) -> bool:
    """Whether the element ``ref`` names took the focus as a field (:data:`_HOLDS_FIELD_FOCUS_SCRIPT`, item 739): a
    click that focused a text field or opened a dropdown changed the page. ``False`` when that cannot be read."""
    try:
        target: Locator = await live_locator(page, ref, "click")
        async with asyncio.timeout(_PULSE_TIMEOUT_S):
            return await target.evaluate(_HOLDS_FIELD_FOCUS_SCRIPT) is True
    except (TimeoutError, PlaywrightError, BrowserError):
        return False


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


# Why a value is not typed into an element that is no form field: only an input, a textarea or a dropdown is hidden in
# every view and every picture of the page, and has the autocomplete that keeps Chrome from storing what is in it (an
# editable element that is none of those, a ``contenteditable`` or a ``role=textbox``, is neither). A ref that names
# something that is not the control of a form field is refused for every part of a login, the username too.
_NOT_A_FORM_FIELD: str = (
    "the element {ref} is not a form field (an input, a textarea or a dropdown): Dot types the login's {kind} only "
    "into one, since only a form field is hidden in what the page shows of it"
)


def _takes(kind: VaultFieldKind, field: SecretKind | None) -> bool:
    """Whether a field marked ``field`` may take the part ``kind`` of a login."""
    if kind == "password":
        return field in _PASSWORD_KINDS
    if kind == "otp":
        return field is None or field == "one-time-code"
    return field not in _PASSWORD_KINDS


async def fill_login_field(
    page: Page,
    ref: str,
    kind: VaultFieldKind,
    value: str,
    fills_on: Callable[[str], bool],
    typing: Callable[[str], None],
    timeout_ms: int,
) -> None:
    """Fill the field ``ref`` with ``value``, the part ``kind`` of a login of the password manager (item 493) or of
    Dot's vault (item 621), once the field is one that part may go into and the document that holds it is one of the
    login's sites (``fills_on`` of the address of its frame): every part into a form field alone, a password into a
    password field, nothing into a frame of another site. ``ref`` may name an element inside the label of the field;
    the field is the control of that label (:data:`_CONTROL_OF`), found once, and every check, the mark and the fill
    are of it. The frame and its address are the browser's: a script of the frame can make the element itself name
    another document. ``typing(document)`` runs once the field passed, just before the value goes into the element
    that passed, with the address of the document that holds it: the value is scoped to that document's origin.
    Neither the value nor Playwright's log of the call is in what it raises."""
    locator: Locator = await live_locator(page, ref, "fill")
    try:
        named: ElementHandle = await locator.element_handle(timeout=timeout_ms)
    except PlaywrightError as error:
        raise BrowserError(
            "the field could not be read before the login was filled", {"ref": ref, "url": page.url}
        ) from error
    control: JSHandle | None = None
    try:
        try:
            # The element a ref names is not always the field: a div, a span or a b in a label is given a ref of its
            # own, and Playwright's fill writes the label's control. The control is resolved once, and the frame, the
            # marking, the form, the mark and the fill are all of that one element (item 621: the form was read off
            # the element the ref named, a div that has none, while the fill wrote the input in its label).
            control = await named.evaluate_handle(_CONTROL_SCRIPT)
            element: ElementHandle | None = control.as_element()
            if element is None:
                raise BrowserError(_NOT_A_FORM_FIELD.format(ref=ref, kind=kind), {"ref": ref, "url": page.url})
            frame: Frame | None = await element.owner_frame()
            marking: object = await element.evaluate(_FIELD_MARKING_SCRIPT)
        except PlaywrightError as error:
            raise BrowserError(
                "the field could not be read before the login was filled", {"ref": ref, "url": page.url}
            ) from error
        if frame is None or not fills_on(frame.url):
            raise BrowserError(
                "the field is in a frame of another site: Dot fills a login only on its own site",
                {
                    "ref": ref,
                    "url": page.url,
                    "frame": None if frame is None else truncate_middle(frame.url, _ERROR_CHARS),
                },
            )
        if not isinstance(marking, str):
            raise BrowserError(_NOT_A_FORM_FIELD.format(ref=ref, kind=kind), {"ref": ref, "url": page.url})
        field: SecretKind | None = secret_kind(marking)
        if not _takes(kind, field):
            raise BrowserError(
                f"the field {ref} does not take the login's {kind}: a password goes only into a password field, so "
                "that it shows in no text",
                {"ref": ref, "url": page.url, "part": kind, "field": field},
            )
        if kind != "username":
            # An address is written into the page's views, its history and logs, and shortened where it is long: the
            # form sends by POST before the value goes in, or nothing goes in.
            try:
                by_get: object = await element.evaluate(_POSTS_SCRIPT)
            except PlaywrightError as error:
                raise BrowserError(
                    f"the form of the field {ref} could not be made to send by POST: Dot fills a login's {kind} only "
                    "into a form that keeps it out of the address",
                    {"ref": ref, "url": page.url, "part": kind},
                ) from error
            if by_get is True:
                _log.info("vault_form_posts", url=truncate_middle(frame.url, _ERROR_CHARS), part=kind)
        typing(frame.url)
        try:
            await element.evaluate(_PREPARE_SCRIPT)
            await element.fill(value, timeout=timeout_ms)
        except PlaywrightError:
            # Playwright's log of the call is left out, here and in the chain of the error: it may name what was typed.
            raise BrowserError(
                "the field did not take the login", {"ref": ref, "url": page.url, "part": kind}
            ) from None
    finally:
        if control is not None:
            await control.dispose()
        await named.dispose()


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
                await _check_secret_field(page, await locator.evaluate(_FIELD_MARKING_SCRIPT), ref, secret_field)
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


async def screenshot_jpeg(
    page: Page, quality: int, timeout_ms: int, hide_secrets: bool, held: Collection[HeldValue] = ()
) -> bytes:
    """Viewport screenshot in CSS pixels (so a pixel in the picture is a pixel for ``click_at``, also on Retina). The
    fields a login of the password manager or of Dot's vault was filled into are covered in every mode (items 493 and
    621), a field of a later document that holds one of the values ``held`` among them (:func:`remark_held`), and
    with ``hide_secrets`` (every mode but yolo, item 467) the fields the page marks secret too, in every frame."""
    if held:
        await remark_held(page, held)
    covered: str = f"{secret_selector()}, {_FILLED_SELECTOR}" if hide_secrets else _FILLED_SELECTOR
    mask: list[Locator] = [frame.locator(covered) for frame in page.frames if not frame.is_detached()]
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


# How Playwright begins the error of a script (in the page, or in a frame of it given refs), and the start of a
# line of its call stack.
_EVALUATE_PREFIXES: tuple[str, ...] = ("Page.evaluate: ", "Frame.evaluate: ")
_STACK_LINE: str = "    at "
# What Playwright says when the page navigated while a script ran (only this: a script's own error may say "navigator").
_CONTEXT_DESTROYED: str = "Execution context was destroyed"


def _script_error(raw: str) -> str:
    """The message a script threw, without Playwright's prefix and the lines of the call stack (Playwright's own frames
    and the anonymous ones of the script)."""
    for prefix in _EVALUATE_PREFIXES:
        raw = raw.removeprefix(prefix)
    lines: list[str] = raw.splitlines()
    return "\n".join(line for line in lines if not line.startswith(_STACK_LINE)).strip()


async def _elements_of(page: Page, refs: Sequence[str]) -> tuple[Frame, list[ElementHandle]]:
    """The elements the refs ``refs`` of the latest snapshot stand for (:func:`live_locator`), and the frame they share:
    a script runs in one document, so the elements of two frames cannot be given to one script."""
    handles: list[ElementHandle] = []
    frames: list[Frame] = []
    for ref in refs:
        locator: Locator = await live_locator(page, ref, "script")
        try:
            # Both ask the page, which a script still running from before may hold: as long as a count waits.
            async with asyncio.timeout(_COUNT_TIMEOUT_S):
                handle: ElementHandle | None = await locator.element_handle(timeout=_COUNT_TIMEOUT_S * 1000)
                frame: Frame | None = None if handle is None else await handle.owner_frame()
        except TimeoutError as error:
            raise BrowserError(
                "the page did not answer in time (a script may be holding it); wait, then take a new snapshot",
                {"action": "script", "ref": ref, "url": page.url, "waited_s": _COUNT_TIMEOUT_S},
            ) from error
        except PlaywrightError as error:
            raise _failure("finding the element for the script failed", page, {"ref": ref}, error) from error
        if handle is None or frame is None:
            raise BrowserError(
                "the ref is not in the page any more (it re-rendered); take a new snapshot",
                {"action": "script", "ref": ref, "url": page.url},
            )
        handles.append(handle)
        frames.append(frame)
    if any(frame != frames[0] for frame in frames):
        raise BrowserError(
            "the refs are in different frames of the page: give one script the elements of one frame",
            {"action": "script", "refs": ", ".join(refs), "url": page.url},
        )
    return frames[0], handles


async def run_script(page: Page, script: str, timeout_s: float, refs: Sequence[str] = ()) -> tuple[ScriptEnd, str]:
    """Run ``script`` in the page's own JavaScript world (item 263) and wait at most ``timeout_s`` for its value:
    Playwright calls a function and evaluates anything else, and waits for a promise either gives. With ``refs`` (item
    738), refs of the latest snapshot, the script is a function called with their elements, in that order, in the frame
    they are in: the element a click of a step did not move (a button that listens for other events) is reached
    as it is, not found again by a selector. Returns how it ended and the JSON of its value (``null`` for ``undefined``;
    a value JSON cannot hold, a DOM node, comes as Playwright writes it) or the message it threw. A script still
    running when the wait ends goes on in the page. The elements are found before the wait starts: a wait that ends
    while they are found would say a script runs that never began."""
    elements: tuple[Frame, list[ElementHandle]] | None = await _elements_of(page, refs) if refs else None
    try:
        async with asyncio.timeout(timeout_s):
            if elements is not None:
                frame, handles = elements
                # The function is assigned, then called: a statement its own ";" ends and a comment after it ends
                # nowhere else, alone or together (the review of every module: ";" then "// …" was a SyntaxError).
                called: str = (
                    f"(elements) => {{ let __dotScript;\n__dotScript =\n{script}\n;\n"
                    "return __dotScript(...elements); }"
                )
                value: object = await frame.evaluate(called, handles)
            else:
                value = await page.evaluate(script)
    except TimeoutError:
        return "timeout", ""
    except PlaywrightError as error:
        if page.is_closed():
            raise _failure("the tab closed while the script ran", page, {}, error) from error
        if _CONTEXT_DESTROYED in str(error):
            return "navigated", ""
        return "error", _script_error(str(error))
    return "value", json.dumps(value, ensure_ascii=False, default=str)


async def holds_secret_field(page: Page) -> bool:
    """Whether ``page`` holds, in any of its frames, a field that the page marks as taking a secret (item 533): a
    password, a one-time code, a card (:func:`secret_selector`). A page that does not say in time holds one, as a field
    that cannot be asked counts as secret."""
    selector: str = secret_selector()
    try:
        counts: list[int] = await asyncio.gather(
            *(_count(frame.locator(selector)) for frame in page.frames if not frame.is_detached())
        )
    except (TimeoutError, PlaywrightError):
        return True
    return any(counts)


def _applies_to(domain: str, host: str) -> bool:
    """Whether a cookie of ``domain`` (``.example.com`` or ``example.com``) is sent to ``host``, on any path: a script
    of a page can read the ones of a narrower path through a frame of that path."""
    kept: str = domain.lstrip(".").casefold()
    return host.casefold() == kept or host.casefold().endswith(f".{kept}")


async def cookie_secrets(page: Page, url: str) -> tuple[str, ...]:
    """The values of the cookies of ``url``'s site, on any of its paths, that a script may read and that are long enough
    to be a session (item 533): a script that reads ``document.cookie`` hands the model a session. A value shorter than
    :data:`_MIN_HIDDEN_VALUE_CHARS` (``en``, ``1``) is no session, and neither is a plain word (``dismissed``,
    ``accepted``: a cookie of consent), which would be hidden wherever it stands in the chat."""
    host: str = urlsplit(url).hostname or ""
    try:
        cookies: list[Cookie] = await page.context.cookies()
    except PlaywrightError as error:
        raise _failure(
            "reading the cookies, to hide them from what the script gave, failed", page, {}, error
        ) from error
    values: list[str] = [cookie["value"] for cookie in cookies if _applies_to(cookie["domain"], host)]
    return tuple(
        dict.fromkeys(
            value for value in values if len(value) >= _MIN_HIDDEN_VALUE_CHARS and _PLAIN_WORD.fullmatch(value) is None
        )
    )


async def measure_viewport(page: Page) -> Viewport:
    """The page area of ``page`` that is on screen. A browser Dot did not size itself (the open Chrome of the user)
    has the size of its window, which only the page can tell."""
    reported: object = await _again_after_navigation(
        page, "measuring the viewport failed", lambda: page.evaluate(_VIEWPORT_SCRIPT)
    )
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


# The dialogs of a page that end a wait for a text when one opens during it (item 698): a cookie banner is not one,
# since the consent handler declines it when it is in the way.
_MODALS: str = f":is([role='dialog'], [role='alertdialog'], dialog[open], [aria-modal='true']):not({_CONSENT_BANNERS})"
TextWaitEnd = Literal["text", "dialog", "timeout"]


async def no_dialog_open(page: Page) -> bool:
    """Whether no dialog stands in front of ``page`` (:data:`_MODALS`): one that opens then ends a wait for a text."""
    try:
        return await _count(page.locator(_MODALS).filter(visible=True)) == 0
    except TimeoutError as error:
        raise BrowserError("the page did not say in time what it shows", {"url": page.url}) from error
    except PlaywrightError as error:
        raise _failure("looking for a dialog failed", page, {}, error) from error


async def wait_for_text_or_dialog(
    page: Page, text: str, timeout_ms: int, watch_dialogs: bool | None = None
) -> TextWaitEnd:
    """What ended a wait of at most ``timeout_ms`` for ``text`` to become visible, in any of its matches (an earlier
    hidden one, such as a template or a closed menu, does not hold the wait): the text, a dialog that opened in front
    of the page while none was open when the wait began (item 698; the text would not come until it is answered), or
    the time. A dialog that brings the text with it is the text. One wait in the page, for either, so nothing is left
    waiting behind it. ``watch_dialogs`` says whether no dialog was open when a wait made of several began (item 774);
    ``None`` looks now."""
    wanted: Locator = page.get_by_text(text).filter(visible=True)
    dialogs: Locator = page.locator(_MODALS).filter(visible=True)
    try:
        if watch_dialogs is None:
            watch_dialogs = await _count(dialogs) == 0
        target: Locator = wanted.or_(dialogs) if watch_dialogs else wanted
        await target.first.wait_for(state="visible", timeout=timeout_ms)
        if not watch_dialogs or await _count(wanted) > 0:
            return "text"
    except PlaywrightTimeoutError:
        return "timeout"
    except TimeoutError as error:
        raise BrowserError("the page did not say in time what it shows", {"text": text, "url": page.url}) from error
    except PlaywrightError as error:
        raise _failure("waiting for the text failed", page, {"text": text}, error) from error
    return "dialog"


# The file field that a control named by label or by ``aria-controls`` hands its files to, which a custom "choose file"
# button, a label drawn as a button or a dropzone that wraps nothing are made of (item 745): the field of the label the
# element is in (its ``for``, or the field inside it) or the one an id in its ``aria-controls`` names.
# Item 754: or the file field a click at the element's middle reaches, a field made see-through and laid over the button
# it looks like (the one a person's click there opens); ids and points are looked up in the element's own root, a
# shadow root's for a control of a web component.
_CONTROLLED_FIELD_SCRIPT: str = """(element) => {
  const field = (node) => (node instanceof HTMLInputElement && node.type === 'file' ? node : null);
  const root = element.getRootNode();
  const label = element.closest('label');
  const labelled = label && label.control ? field(label.control) : null;
  if (labelled) return labelled;
  for (const id of (element.getAttribute('aria-controls') || '').split(/\\s+/)) {
    const controlled = id && root.getElementById ? field(root.getElementById(id)) : null;
    if (controlled) return controlled;
  }
  const box = element.getBoundingClientRect();
  if (box.width > 0 && box.height > 0 && root.elementFromPoint) {
    const over = field(root.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
    if (over) return over;
  }
  return null;
}"""


async def _file_field_of(trigger: Locator) -> Locator | ElementHandle | None:
    """The file field the files go into for the element ``ref`` names (item 704): the element itself when it is one,
    else the one file field inside it (a label or a box that wraps a hidden field), else the field its label or its
    ``aria-controls`` names (item 745). ``None`` otherwise, and when it holds several: then the element is clicked, and
    the file chooser it opens takes the files. A field next to the element is not taken: it may belong to another
    control than the one the run named."""
    try:
        async with asyncio.timeout(_COUNT_TIMEOUT_S):
            if await trigger.evaluate("e => e instanceof HTMLInputElement && e.type === 'file'"):
                return trigger
        inside: Locator = trigger.locator("input[type=file]")
        if await _count(inside) == 1:
            return inside
        async with asyncio.timeout(_COUNT_TIMEOUT_S):
            controlled: JSHandle = await trigger.evaluate_handle(_CONTROLLED_FIELD_SCRIPT)
        element: ElementHandle | None = controlled.as_element()
        if element is None:
            await controlled.dispose()
        return element
    except TimeoutError:
        return None


async def _only_file_field(page: Page, document: object) -> Locator | None:
    """The file field of the page when it has exactly one: then no other control can be the one the files are for. Only
    while the page is still the document ``document`` marked (:data:`_DOCUMENT_MARK_SCRIPT`): a click that sent a form
    or followed a link has put another page there, perhaps of another site, which the upload was not allowed for."""
    try:
        async with asyncio.timeout(_COUNT_TIMEOUT_S):
            if document is None or await page.evaluate(_DOCUMENT_MARK_SCRIPT) != document:
                return None
        every: Locator = page.locator("input[type=file]")
        return every if await _count(every) == 1 else None
    except TimeoutError:
        return None


# A mark of the document a page holds: the same while it holds that document, another once it holds a new one.
_DOCUMENT_MARK_SCRIPT: str = "() => (window.__dotDocument = window.__dotDocument || crypto.randomUUID())"


async def _document_mark(page: Page) -> object:
    """The mark of the document ``page`` holds (:data:`_DOCUMENT_MARK_SCRIPT`), ``None`` when it cannot be read."""
    try:
        async with asyncio.timeout(_COUNT_TIMEOUT_S):
            return await page.evaluate(_DOCUMENT_MARK_SCRIPT)
    except (TimeoutError, PlaywrightError):
        return None


async def _chooser_files(page: Page, trigger: Locator, paths: list[str], timeout_ms: int) -> None:
    """Click ``trigger`` and hand the files to the file chooser it opens. A click that waits for the element to hold
    still and does not get it within ``timeout_ms`` is made once more without that wait, which a custom button that a
    script keeps redrawing never passes (item 745), but only when the element itself is what lies at its middle
    (:data:`_ON_TOP_SCRIPT`): a forced click lands on whatever is there, and a layer over the button (a bar that sends a
    form, a cookie banner) is never clicked in its place."""
    try:
        async with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
            await trigger.click(timeout=timeout_ms)
    except PlaywrightTimeoutError as error:
        if "filechooser" in str(error):
            raise  # the click went through and opened no chooser: a second click would not
        try:
            async with asyncio.timeout(_COUNT_TIMEOUT_S):
                on_top: object = await trigger.evaluate(_ON_TOP_SCRIPT)
        except TimeoutError:
            on_top = False
        if on_top is not True:
            raise  # something else lies over it, and a forced click would land on that
        async with page.expect_file_chooser(timeout=_FORCED_CLICK_TIMEOUT_MS * 2) as chooser_info:
            await trigger.click(timeout=_FORCED_CLICK_TIMEOUT_MS, force=True)
    chooser = await chooser_info.value
    await chooser.set_files(paths)


_FORCED_CLICK_TIMEOUT_MS: int = 2000
# Whether the element is what a click at its middle reaches: itself or an element inside it is the topmost there.
_ON_TOP_SCRIPT: str = """(el) => {
  const box = el.getBoundingClientRect();
  const hit = el.getRootNode().elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
  return !!hit && (hit === el || el.contains(hit));
}"""


async def upload_files(page: Page, ref: str, paths: list[str], timeout_ms: int) -> None:
    """Hand the files to the file field of the element ``ref`` (:func:`_file_field_of`): straight into it, without a
    click, since sites hide the field off the screen or under an icon and a click on it waits for nothing; else click
    the element and hand them to the file chooser it opens (:func:`_chooser_files`); when that opens none, the only
    file field of the page takes them (item 745)."""
    trigger: Locator = await live_locator(page, ref, "upload")
    try:
        field: Locator | ElementHandle | None = await _file_field_of(trigger)
        if isinstance(field, ElementHandle):
            try:
                await field.set_input_files(paths, timeout=timeout_ms)
            finally:
                await field.dispose()
            return
        if field is not None:
            await field.set_input_files(paths, timeout=timeout_ms)
            return
        document: object = await _document_mark(page)
        try:
            await _chooser_files(page, trigger, paths, timeout_ms)
        except PlaywrightError as chooser_error:
            only: Locator | None = await _only_file_field(page, document)
            if only is None:
                raise
            _log.info("browser_upload_by_only_field", url=page.url, error=truncate_middle(str(chooser_error), 300))
            await only.set_input_files(paths, timeout=timeout_ms)
    except PlaywrightError as error:
        raise _failure("uploading the files failed", page, {"ref": ref}, error) from error


async def _banner_uncounted(banner: Locator) -> str | None:
    """``None`` when the banner left the page; else why it is taken as still there: it is there, or it could not be
    counted, or not in time."""
    try:
        return None if await _count(banner) == 0 else ""
    except TimeoutError:
        return "the page did not answer the count in time"
    except PlaywrightError as error:
        return truncate_middle(str(error), _ERROR_CHARS)


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
            if page.is_closed():
                return
            uncounted: str | None = await _banner_uncounted(banner)
            if uncounted is None:
                return
            _log.warning(
                "consent_decline_failed",
                url=truncate_middle(page.url, _ERROR_CHARS),
                error=truncate_middle(str(error), _ERROR_CHARS),
                count_error=uncounted or None,
            )

    await page.add_locator_handler(banner, decline, no_wait_after=True, times=_CONSENT_HANDLER_TIMES)

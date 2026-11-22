"""The human check in front of a page, found and, where a press can pass it, passed (item 1288).

A captcha used to cost a screenshot to find it, a guess at a pixel to press it and a sleep to see what happened, and a
"Press & Hold" check (Fiverr's, and others') cost all of that and failed anyway: no click holds a button down. This
module finds the check itself, in the page or in any frame of it (hCaptcha, reCAPTCHA, Cloudflare Turnstile, or a
site's own "Verify you are human" button), presses it and reads its state again a few times a second until it clears.
What is left is the part that needs eyes - an image grid or a drag puzzle - and that comes back as the widget's own
picture at twice its size, with the box to act in.

The boxes are in CSS pixels of the main frame's viewport, the coordinates ``page.mouse`` and ``browser_act click_at``
take.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page

from dotagent.browser.types import CheckBox, HumanCheckOutcome
from dotagent.core.images import crop_scaled
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle

_log = get_logger("dotagent.browser.human_check")

# How long one probe of one frame may take before the frame is counted as unreadable (a frame that is going away
# while it is read).
_PROBE_TIMEOUT_S: float = 2.0
# How often the state is read again while a check is pressed or held: fast enough to catch a widget that says it is
# done in under a second, slow enough to leave the page's own script the thread it runs on.
_POLL_S: float = 0.12
# The longest a Press & Hold button is held before it is let go whatever it says (Fiverr's asks for about 2 s).
_HOLD_MOST_S: float = 6.0
# A widget that is gone for this long has passed: the frame of a reCAPTCHA that is still checking comes and goes.
_GONE_SETTLED_S: float = 0.45
# How much larger than its CSS size a challenge is drawn for the model to read its tiles.
_CROP_SCALE: float = 2.0
# The largest edge of the crop: a 4K page's challenge does not need more.
_CROP_MOST_PX: int = 1024
# A click on a widget iframe whose inside cannot be read goes where its checkbox sits: the left of the anchor.
_CHECKBOX_INSET_X: float = 30.0
# How far a check may sit from where it was read and still be the same check in a hold that is under way.
_MOVED_PX: float = 12.0
# How far apart two reads of a check may be for it to count as standing still, and how many more reads a check that was
# found moving gets to stop before it is given up (a widget with an entrance animation stops within a few).
_STILL_PX: float = 2.0
_SETTLE_READS: int = 6
# How far the box of the element under a pixel may differ from the check's box and still be the check's own element.
_BOX_SLACK_PX: float = 6.0
# What a note names of the element that covers a check: enough to know it, not a page's whole text.
_NAME_CHARS: int = 60
# A frame whose address says it is a widget of someone else's: one that cannot be read makes the page unreadable.
_WIDGET_URL: re.Pattern[str] = re.compile(
    r"hcaptcha\.com|recaptcha|challenges\.cloudflare\.com|turnstile", re.IGNORECASE
)

# What is under a pixel of a frame: the element the check is drawn by (the first of the stack whose box is the check's)
# with nothing above it that is not part of it (a label around a checkbox is). Reads the DOM only.
_UNDER: str = r"""
([x, y, left, top, width, height, slack]) => {
  const name = (el) => (el.tagName || '').toLowerCase() + (el.id ? '#' + el.id : '') + ' ' +
    ((el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30));
  const stack = document.elementsFromPoint(x, y);
  const near = (r) => Math.abs(r.left - left) <= slack && Math.abs(r.top - top) <= slack &&
    Math.abs(r.width - width) <= slack && Math.abs(r.height - height) <= slack;
  for (let i = 0; i < stack.length; i++) {
    const el = stack[i];
    if (!near(el.getBoundingClientRect())) continue;
    for (let j = 0; j < i; j++) {
      const above = stack[j];
      if (!el.contains(above) && !(above.tagName === 'LABEL' && above.contains(el))) return name(above);
    }
    return '';
  }
  return stack.length ? name(stack[0]) : 'nothing';
}
"""

# What is on top at a pixel of the document an <iframe> sits in: the <iframe> itself, or what covers it.
_FRAME_ON_TOP: str = r"""
(frame, [x, y]) => {
  const top = frame.ownerDocument.elementsFromPoint(x, y)[0];
  if (top === frame) return '';
  return top ? (top.tagName || '').toLowerCase() + (top.id ? '#' + top.id : '') : 'nothing';
}
"""

# The probe that finds the checks of one frame. It reads the DOM only: what is visible, where it is, what it says and
# whether it already passed. Nothing is pressed here.
_PROBE: str = r"""
() => {
  const out = [];
  const seen = new Set();
  // The most a page's text may hold to be read as one body, and the most nodes of a page looked at for a check of its
  // own: a page past either is not scanned whole (its first and its last nodes are, where a modal sits).
  const BODY_CAP = 20000;
  const NODE_CAP = 3000;
  const vw = window.innerWidth || 1280;
  const vh = window.innerHeight || 800;
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return [r.left, r.top, r.width, r.height];
  };
  // The words of an element, read from textContent first: a node whose text is longer than ``cap`` is no label of a
  // check, and innerText (which makes the browser lay the page out) is never asked of it (item 1295).
  const words = (el, cap = 600) => {
    if (!el) return '';
    if ((el.textContent || '').length > cap) return '';
    return ((el.innerText || el.textContent || '') + '').replace(/\s+/g, ' ').trim();
  };
  const shown = (el) => {
    if (!el || el.nodeType !== 1) return false;
    try {
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) < 0.05) return false;
      const r = el.getBoundingClientRect();
      return r.width >= 4 && r.height >= 4 && r.right > 0 && r.bottom > 0 && r.left < vw + 60 && r.top < vh + 240;
    } catch (e) {
      return false;
    }
  };
  const push = (kind, role, el, text, state, cols, rows) => {
    if (!shown(el)) return;
    const box = rectOf(el);
    const key = kind + '|' + role + '|' + Math.round(box[0]) + ',' + Math.round(box[1]) + ',' +
      Math.round(box[2]) + ',' + Math.round(box[3]);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({kind, role, box, text: (text || '').slice(0, 200), state: state || '',
              cols: cols || 0, rows: rows || 0});
  };
  const kindOf = (url) => {
    if (!url) return '';
    if (/hcaptcha\.com/i.test(url)) return 'hcaptcha';
    if (/recaptcha/i.test(url) || /google\.com\/recaptcha/i.test(url)) return 'recaptcha';
    if (/challenges\.cloudflare\.com|turnstile/i.test(url)) return 'turnstile';
    return '';
  };
  const here = kindOf(location.href);
  const regex = (patterns) => new RegExp(patterns.join('|'), 'i');
  // A hold's own words, and the word a widget says while it is being held ("Holding..."). A bare "hold" is not one:
  // a "Holds" tab or "Hold Shift to select several" is no button to press.
  const HOLD = regex([
    'press\\s*(?:&|and|\\u0026)\\s*hold',
    'hold\\s*(?:to|and)\\s*(?:confirm|verify|continue)',
    '\\bholding\\b',
    'bas[ıi]l[ıi]\\s*tut',
  ]);
  const CHECKBOX = regex([
    "i(?:'|’)?m not a robot",
    'i am not a robot',
    'i am (?:a )?human',
    'verify (?:that )?you are (?:a )?human',
    'confirm you are (?:a )?human',
    'are you a human',
    'human verification',
  ]);
  const WAIT = regex([
    'checking (?:your browser|the site connection|if the site connection is secure)',
    'just a moment',
    'verifying you are human',
    'needs to review the security',
    'bir dakika lütfen',
  ]);
  const PROMPT = regex([
    'select all (?:the )?(?:images|squares)',
    'click (?:each|on) (?:the )?(?:images|squares|tiles)',
    'please click',
    'click the images',
    'drag (?:the|this) (?:shape|piece|object)',
    'use the arrows',
    'verify there are no',
  ]);
  const VERIFY = regex(['^verify$', '^next$', '^submit$', '^skip$', '^done$', '^continue$', '^confirm$',
                        '^i am human$', "^i'm human$", '^doğrula$']);
  const DONE = regex(['\\bsuccess\\b', '\\bverified\\b', '\\bpassed\\b', '\\bdoğrulandı\\b', '\\bbaşarılı\\b']);
  // "unsuccessful", "not verified" and "doğrulama başarısız" say the opposite of DONE with a word DONE matches.
  const NOT_DONE = regex(['unsuccessful', 'not verified', 'unverified', 'not passed', 'failed', 'başarısız',
                          'doğrulanmadı']);
  const clickable = (el) => el.closest('button, [role=button], a, label, input[type=checkbox]');
  const WIDGET_PROMPT = ['.rc-imageselect-desc-no-canonical', '.rc-imageselect-desc', '.challenge-example',
    '[id*=challenge-example]', '[class*=challenge-example]', '[class*=prompt-text]',
    '[class*=challenge-prompt]'].join(',');
  const PRESSABLE = ['button', '[role=button]', 'input[type=checkbox]'].join(',');
  const CANDIDATES = ['button', '[role=button]', 'label', 'input[type=checkbox]', 'span', 'div', 'p', 'h1',
                      'h2', 'h3', 'strong', 'b'].join(',');

  // A widget iframe: the check lives inside it, in another document.
  for (const el of document.querySelectorAll('iframe')) {
    const src = el.src || el.getAttribute('src') || '';
    const title = el.title || el.getAttribute('aria-label') || '';
    let kind = kindOf(src) || kindOf(title);
    if (!kind && /captcha|human check|verify you are human|are you a robot/i.test(title)) kind = 'human_check';
    if (!kind) continue;
    push(kind, 'widget', el, title || src.slice(0, 120), '', 0, 0);
  }

  // A page that sets the token the site sends is past its check, whatever the widget still shows.
  const tokens = ['textarea[name="g-recaptcha-response"]', 'textarea[name="h-captcha-response"]',
                  'input[name="cf-turnstile-response"]', 'input[name="h-captcha-response"]'];
  for (const selector of tokens) {
    const field = document.querySelector(selector);
    if (field && typeof field.value === 'string' && field.value.length >= 20) {
      out.push({kind: 'token', role: 'token', box: [0, 0, 1, 1], text: selector, state: 'set', cols: 0, rows: 0});
      break;
    }
  }

  // Inside a widget's own document: its checkbox, its challenge, the button that sends one, and its answer. A small
  // embedded document that holds a checkbox of its own or talks of a robot is a widget too, whatever its address.
  const embedded = window.top !== window;
  const bodyShort = !!document.body && (document.body.textContent || '').length <= BODY_CAP;
  const said = bodyShort ? words(document.body, BODY_CAP) : '';
  const anchor = document.querySelector('#recaptcha-anchor, #checkbox, [role=checkbox]');
  const promptEl = document.querySelector(WIDGET_PROMPT);
  const widget = here || (embedded && (!!anchor || (bodyShort && said.length < 600 &&
    (CHECKBOX.test(said) || !!promptEl))));
  const kind = here || 'human_check';
  if (widget) {
    const box = anchor || document.querySelector('input[type=checkbox]');
    if (box) {
      const checked = box.getAttribute('aria-checked') === 'true' || box.checked === true ||
        box.classList.contains('checked') || !!document.querySelector('.recaptcha-checkbox-checked');
      push(kind, 'checkbox', box, words(box).slice(0, 80), checked ? 'checked' : '', 0, 0);
    }
    if (said && said.length < 400 && DONE.test(said) && !NOT_DONE.test(said)) {
      out.push({kind, role: 'widget', box: [0, 0, vw, vh], text: said.slice(0, 120), state: 'set',
                cols: 0, rows: 0});
    }
    const grid = document.querySelector(['table[class*=rc-imageselect-table]', '.rc-imageselect-table',
      '[class*=image-wrapper]', '[class*=challenge-container]', '[class*=challenge-grid]', '[class*=task-grid]',
      '[class*=grid-container]'].join(','));
    const prompt = promptEl ? words(promptEl).slice(0, 200) : '';
    if (grid || (prompt && PROMPT.test(prompt))) {
      let cols = 0, rows = 0;
      const name = grid ? (grid.className || '') + '' : '';
      const size = name.match(/(?:^|\D)(\d)(\d)(?:\D|$)/);
      if (size) { cols = Number(size[1]); rows = Number(size[2]); }
      push(kind, 'challenge', grid || promptEl, prompt || words(grid).slice(0, 120), '', cols, rows);
    }
    for (const el of document.querySelectorAll(['button', '[role=button]', '.button', '.btn',
                                                'input[type=submit]'].join(','))) {
      if (VERIFY.test(words(el))) push(kind, 'verify', el, words(el), '', 0, 0);
    }
  }

  // A check drawn by the site itself: a word on something that can be pressed. A paragraph about a captcha beside a
  // link is no check, so only a pressable target counts.
  if (!widget) {
    const all = Array.from(document.querySelectorAll(CANDIDATES));
    const head = Math.floor(NODE_CAP / 3);
    const nodes = all.length > NODE_CAP ? all.slice(0, head).concat(all.slice(all.length - (NODE_CAP - head))) : all;
    for (const el of nodes) {
      const text = words(el);
      if (!text || text.length > 120) continue;
      let role = '';
      if (HOLD.test(text)) role = 'hold';
      else if (CHECKBOX.test(text)) role = 'checkbox';
      else if (WAIT.test(text) && bodyShort && said.length < 600) role = 'wait';
      if (!role) continue;
      // The control pressed is the one the words are in or the one they are drawn on. A hold is often a bar beside its
      // own words, so a pressable drawn beside them is taken only when it says nothing of its own or says the same
      // words: a "Pay 49 \u20ac" button beside the line is never pressed (item 1252).
      const beside = role === 'hold' && el.parentElement ? el.parentElement.querySelector(PRESSABLE) : null;
      const quiet = beside !== null && (!words(beside) || HOLD.test(words(beside)) || CHECKBOX.test(words(beside)));
      const target = role === 'wait' ? el : clickable(el) || (quiet ? beside : null);
      if (!target) continue;
      push('human_check', role, target, text, '', 0, 0);
    }
  }
  return {vw, vh, findings: out};
}
"""

# Put to before any press: what the pixel it would land on is judged by (item 1252).
PressGuard = Callable[[float, float], Awaitable[None]]

_HOLD_WORDS: re.Pattern[str] = re.compile(r"press\s*(?:&|and)\s*hold|hold", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    """One part of a human check as the page draws it now."""

    kind: str
    role: str
    box: CheckBox
    text: str = ""
    state: str = ""
    cols: int = 0
    rows: int = 0
    # The frame whose probe found it and where that frame's viewport sits in the main frame's: what a press is checked
    # against (item 1295). Not part of what a check is, so they do not take part in comparing two.
    frame: Frame | None = field(default=None, compare=False, repr=False)
    offset: tuple[float, float] = field(default=(0.0, 0.0), compare=False, repr=False)

    @property
    def passed(self) -> bool:
        return self.state in ("checked", "set")


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _box_of(value: object) -> CheckBox | None:
    if not isinstance(value, list | tuple) or len(value) < 4:
        return None
    left, top, width, height = (_as_float(part) for part in value[:4])
    if width < 1 or height < 1:
        return None
    return CheckBox(x=left, y=top, width=width, height=height)


def _finding(item: object, offset_x: float, offset_y: float, frame: Frame | None = None) -> Check | None:
    if not isinstance(item, dict):
        return None
    box = _box_of(item.get("box"))
    role = item.get("role")
    if box is None or not isinstance(role, str):
        return None
    text = item.get("text")
    state = item.get("state")
    kind = item.get("kind")
    return Check(
        kind=kind if isinstance(kind, str) else "",
        role=role,
        box=CheckBox(x=box.x + offset_x, y=box.y + offset_y, width=box.width, height=box.height),
        text=text if isinstance(text, str) else "",
        state=state if isinstance(state, str) else "",
        cols=int(_as_float(item.get("cols"))),
        rows=int(_as_float(item.get("rows"))),
        frame=frame,
        offset=(offset_x, offset_y),
    )


async def _offset_of(frame: Frame) -> tuple[float, float] | None:
    """Where a frame's own viewport sits in the main frame's, from the box of its ``<iframe>`` element; ``None`` for a
    frame that is not drawn. A frame that is going away raises, and the caller counts that as unreadable."""
    if frame.parent_frame is None:
        return (0.0, 0.0)
    element = await frame.frame_element()
    box = await element.bounding_box()
    if box is None:
        return None
    return (box["x"], box["y"])


@dataclass(frozen=True)
class Reading:
    """One read of the page: its viewport, the checks in it, and whether a frame that matters could not be read.

    ``unreadable`` is set when the main frame, a frame that held a check a moment ago or a frame whose address is a
    captcha widget's did not answer in time or went away while it was read. What such a read lacks is not a check that
    passed: an empty ``checks`` with ``unreadable`` set says nothing (item 1295)."""

    viewport: dict[str, float]
    checks: tuple[Check, ...]
    unreadable: bool = False


def _matters(frame: Frame, page: Page, watched: set[Frame]) -> bool:
    return frame is page.main_frame or frame in watched or _WIDGET_URL.search(frame.url) is not None


async def find_checks(page: Page, watched: set[Frame] | None = None) -> Reading:
    """The checks in front of the page now, and the page's viewport in CSS pixels.

    ``watched`` holds the frames that showed a check earlier in the same call: it is added to here, and a frame in it
    that cannot be read makes the reading ``unreadable`` as the main frame does."""
    held: set[Frame] = watched if watched is not None else set()
    readings: list[Check] = []
    viewport: dict[str, float] = {"width": 1280.0, "height": 800.0}
    unreadable: bool = False
    for frame in page.frames:
        try:
            raw: object = await asyncio.wait_for(frame.evaluate(_PROBE), _PROBE_TIMEOUT_S)
            offset: tuple[float, float] | None = await _offset_of(frame)
        except (PlaywrightError, TimeoutError) as error:
            if _matters(frame, page, held):
                unreadable = True
                _log.info(
                    "human_check_frame_unreadable",
                    frame=truncate_middle(frame.url, _NAME_CHARS * 2),
                    error=truncate_middle(str(error), _NAME_CHARS * 2),
                )
            continue
        if not isinstance(raw, dict):
            unreadable = unreadable or _matters(frame, page, held)
            continue
        if offset is None:
            continue
        if frame is page.main_frame:
            viewport = {"width": _as_float(raw.get("vw")) or 1280.0, "height": _as_float(raw.get("vh")) or 800.0}
        findings = raw.get("findings")
        for item in findings if isinstance(findings, list) else []:
            check = _finding(item, *offset, frame=frame)
            if check is not None:
                readings.append(check)
                held.add(frame)
    return Reading(viewport, tuple(readings), unreadable)


def _settled(reading: Reading) -> bool:
    """Whether the read shows nothing of a check left, and was whole: an unreadable page is not a cleared one."""
    return _cleared(reading.checks) and not reading.unreadable


# The checks a widget of someone else draws: on a page that has one, the page's own words wait their turn.
_WIDGET_KINDS: tuple[str, ...] = ("hcaptcha", "recaptcha", "turnstile")


def _front(checks: tuple[Check, ...]) -> Check | None:
    """What stands in front of the page: a challenge that opened, a button to hold, a checkbox to press, a page that
    is still checking, or a check that already passed. A widget someone else draws comes before a page's own words: a
    "Press & Hold" somewhere on a page is no reason to hold it while the real check waits."""
    for kinded in (True, False):
        for role in ("challenge", "hold", "checkbox", "wait"):
            for check in checks:
                if check.role != role or (role == "checkbox" and check.passed):
                    continue
                if (check.kind in _WIDGET_KINDS) is kinded:
                    return check
    for check in checks:
        if check.passed:
            return check
    for check in checks:
        if check.role == "widget":
            return check
    return None


def _passed(checks: tuple[Check, ...]) -> bool:
    return any(check.passed for check in checks)


def _cleared(checks: tuple[Check, ...]) -> bool:
    """Whether nothing of the check is left on the page."""
    return not any(
        check.role in ("challenge", "hold", "wait", "widget") or (check.role == "checkbox" and not check.passed)
        for check in checks
    )


async def _poll(page: Page, until: float, watched: set[Frame]) -> Reading:
    """Read the page again every ``_POLL_S`` until it says the check passed or ended, or ``until`` comes. A read that
    could not see a frame that matters is read again, never taken for an end."""
    reading: Reading = await find_checks(page, watched)
    while True:
        if _passed(reading.checks):
            return reading
        if _settled(reading):
            # A widget that is gone for a blink is a frame being rebuilt, not a check that passed.
            await asyncio.sleep(min(_GONE_SETTLED_S, max(0.0, until - time.monotonic())))
            reading = await find_checks(page, watched)
            if _passed(reading.checks) or _settled(reading):
                return reading
            continue
        left: float = until - time.monotonic()
        if left <= 0:
            return reading
        await asyncio.sleep(min(_POLL_S, left))
        reading = await find_checks(page, watched)


async def wait_cleared(page: Page, timeout_s: float) -> bool:
    """Wait for a check that is already working (Turnstile's, Cloudflare's) to clear itself."""
    reading: Reading = await _poll(page, time.monotonic() + timeout_s, set())
    return _passed(reading.checks) or _settled(reading)


async def _crop(page: Page, box: CheckBox, viewport: dict[str, float]) -> tuple[bytes, CheckBox] | None:
    """The check's own picture, twice its size, and the box it came from."""
    width: float = viewport.get("width") or 1280.0
    height: float = viewport.get("height") or 800.0
    inside: CheckBox | None = box.clipped(width, height)
    if inside is None:
        return None
    try:
        data: bytes = await page.screenshot(type="jpeg", quality=80)
    except PlaywrightError:
        return None
    scale: float = min(_CROP_SCALE, _CROP_MOST_PX / max(inside.width, inside.height, 1.0))
    return (
        crop_scaled(data, int(width), int(height), (inside.x, inside.y, inside.width, inside.height), scale, 80),
        inside,
    )


def _still(one: CheckBox, other: CheckBox) -> bool:
    return abs(one.x - other.x) <= _STILL_PX and abs(one.y - other.y) <= _STILL_PX


def _near(one: CheckBox, other: CheckBox) -> bool:
    return abs(one.x - other.x) < _MOVED_PX and abs(one.y - other.y) < _MOVED_PX


@dataclass(frozen=True)
class _Press:
    """A check as it stands just before it is pressed, and the pixel it is pressed at."""

    check: Check
    point: tuple[float, float]


def _press_point(check: Check) -> tuple[float, float]:
    """Where a check is pressed: the middle of a checkbox or a button, the checkbox's place at the left of a widget
    whose inside could not be read."""
    if check.role == "widget":
        return (check.box.x + min(_CHECKBOX_INSET_X, check.box.width / 2), check.box.y + check.box.height / 2)
    return check.box.center


def _counterpart(checks: tuple[Check, ...], front: Check) -> Check | None:
    """The check now that is the one read before: of its kind and role, the nearest to where it was."""
    same = [one for one in checks if one.kind == front.kind and one.role == front.role]
    if not same:
        return None
    cx, cy = front.box.center
    return min(same, key=lambda one: (one.box.center[0] - cx) ** 2 + (one.box.center[1] - cy) ** 2)


async def _covered_by(check: Check, point: tuple[float, float]) -> str:
    """What stands over ``point`` instead of the check, named; ``""`` when the pixel is the check's own.

    The frame the check lives in is asked what is on top there (``document.elementsFromPoint``), and so is every
    document its ``<iframe>`` sits in up to the page's, where the ``<iframe>`` itself must be on top: a page that draws
    something over a widget it cannot read inside is seen from outside. What cannot be read is not the check's."""
    frame: Frame | None = check.frame
    if frame is None:
        return "an unknown element"
    x, y = point
    try:
        left, top = check.box.x - check.offset[0], check.box.y - check.offset[1]
        covering: object = await asyncio.wait_for(
            frame.evaluate(
                _UNDER,
                [x - check.offset[0], y - check.offset[1], left, top, check.box.width, check.box.height, _BOX_SLACK_PX],
            ),
            _PROBE_TIMEOUT_S,
        )
        if covering != "":
            return str(covering)[:_NAME_CHARS] or "an unknown element"
        while frame.parent_frame is not None:
            parent: Frame = frame.parent_frame
            parent_offset: tuple[float, float] | None = await _offset_of(parent)
            if parent_offset is None:
                return "a frame that is not drawn"
            element = await frame.frame_element()
            over: object = await asyncio.wait_for(
                element.evaluate(_FRAME_ON_TOP, [x - parent_offset[0], y - parent_offset[1]]), _PROBE_TIMEOUT_S
            )
            if over != "":
                return str(over)[:_NAME_CHARS] or "an unknown element"
            frame = parent
    except (PlaywrightError, TimeoutError) as error:
        _log.info("human_check_pixel_unreadable", error=truncate_middle(str(error), _NAME_CHARS * 2))
        return "something that could not be read"
    return ""


async def _confirm_press(page: Page, front: Check, watched: set[Frame]) -> tuple[_Press | None, Reading, str]:
    """Read the page again just before a press (item 1295): the check must be where it was read, or settled in a new
    place, and be the element under the pixel. Returns what to press, or ``None`` with the reading and why not.

    A widget that is still sliding in is read again until it stands still (``_SETTLE_READS`` reads at most) and is
    pressed where it stops, not where it was first read; a page whose check stands still costs this one read."""
    reading: Reading = await find_checks(page, watched)
    if _passed(reading.checks):
        return (None, reading, "it passed before it was pressed")
    now: Check | None = _counterpart(reading.checks, front)
    reads: int = 0
    while now is not None and not _still(now.box, front.box):
        # It is not where it was read: wait for it to stop, and press it where it stops.
        reads += 1
        if reads > _SETTLE_READS:
            return (None, reading, "it kept moving, so nothing was pressed")
        await asyncio.sleep(_POLL_S)
        reading = await find_checks(page, watched)
        if _passed(reading.checks):
            return (None, reading, "it passed before it was pressed")
        before: Check = now
        now = _counterpart(reading.checks, front)
        if now is not None and _still(now.box, before.box):
            break
    if now is None:
        return (None, reading, "it was gone before it was pressed")
    point: tuple[float, float] = _press_point(now)
    covering: str = await _covered_by(now, point)
    if covering:
        return (None, reading, f"{covering!r} is over it, so nothing was pressed")
    return (_Press(now, point), reading, "")


async def _hold(
    page: Page, press: _Press, timeout_s: float, guard: PressGuard | None, watched: set[Frame]
) -> tuple[float, str, Reading]:
    """Press the button, hold it until the page says it took it, and let go. Returns how long it was held, what told
    the hold to end, and the reading after it."""
    check: Check = press.check
    x, y = press.point
    limit: float = min(timeout_s, _HOLD_MOST_S)
    started: float = time.monotonic()
    note: str = "it was held as long as it may be"
    jiggled: bool = False
    if guard is not None:
        await guard(x, y)
    await page.mouse.move(x, y)
    await page.mouse.down()
    try:
        while time.monotonic() - started < limit:
            await asyncio.sleep(_POLL_S)
            if not jiggled and time.monotonic() - started > 0.35:
                # A widget that watches the pointer for a move while it is down sees one (a still mouse is no mouse).
                await page.mouse.move(x + 1, y + 1)
                jiggled = True
            reading: Reading = await find_checks(page, watched)
            now = next((one for one in reading.checks if one.role == "hold" and _near(one.box, check.box)), None)
            if now is None:
                if reading.unreadable:
                    # The page did not answer: the button is not known to be gone, so the hold goes on.
                    continue
                note = "the button went away"
                break
            if now.passed or not _HOLD_WORDS.search(now.text):
                note = "the button stopped asking to be held"
                break
    finally:
        held: float = time.monotonic() - started
        await page.mouse.up()
    return (held, note, await _poll(page, time.monotonic() + timeout_s, watched))


def _with_crop(outcome: HumanCheckOutcome, crop: tuple[bytes, CheckBox] | None) -> HumanCheckOutcome:
    return outcome if crop is None else outcome.model_copy(update={"crop": crop[0], "crop_box": crop[1]})


def _verify_box(checks: tuple[Check, ...]) -> CheckBox | None:
    return next((one.box for one in checks if one.role == "verify"), None)


def _challenge(front: Check, checks: tuple[Check, ...], started: float, did: tuple[str, ...] = ()) -> HumanCheckOutcome:
    return HumanCheckOutcome(
        result="challenge",
        kind=front.kind,
        role="challenge",
        box=front.box,
        text=front.text,
        seconds=time.monotonic() - started,
        did=did,
        cols=front.cols,
        rows=front.rows,
        verify=_verify_box(checks),
    )


_UNREADABLE_NOTE: str = "could not read the page, look at it"


def _unreadable(front: Check | None, started: float, did: tuple[str, ...], note: str = "") -> HumanCheckOutcome:
    """A page that did not answer is not a page that passed: the run is told to look at it (item 1295)."""
    return HumanCheckOutcome(
        result="unfinished",
        kind=front.kind if front is not None else "",
        role=front.role if front is not None else "",
        box=front.box if front is not None else None,
        text=front.text if front is not None else "",
        seconds=time.monotonic() - started,
        did=did,
        note=f"{note}; then the page could not be read, look at it" if note else _UNREADABLE_NOTE,
        unreadable=True,
    )


async def _read_after(
    page: Page,
    check: Check,
    reading: Reading,
    started: float,
    did: tuple[str, ...],
    passed_note: str,
    unfinished_note: str,
) -> HumanCheckOutcome:
    """What a read after a press (or after a press that was not made because the page had changed) comes to: a
    challenge that opened, a check that passed or cleared on a read that saw the whole page, a page that could not be
    read, or a check that is still there."""
    after: Check | None = _front(reading.checks)
    if after is not None and after.role == "challenge":
        return _with_crop(
            _challenge(after, reading.checks, started, did=did), await _crop(page, after.box, reading.viewport)
        )
    if (after is not None and after.passed) or _settled(reading):
        return HumanCheckOutcome(
            result="passed",
            kind=check.kind,
            role=check.role,
            box=check.box,
            text=check.text,
            seconds=time.monotonic() - started,
            did=did,
            note=passed_note,
        )
    if reading.unreadable:
        return _unreadable(after or check, started, did, "" if did else unfinished_note)
    shown: Check = after or check
    outcome = HumanCheckOutcome(
        result="unfinished",
        kind=shown.kind,
        role=shown.role,
        box=shown.box,
        text=shown.text,
        seconds=time.monotonic() - started,
        did=did,
        note=unfinished_note,
    )
    return _with_crop(outcome, await _crop(page, shown.box, reading.viewport))


async def pass_human_check(page: Page, timeout_s: float, guard: PressGuard | None = None) -> HumanCheckOutcome:
    """Find the human check in front of the page and pass the part of it a press can pass.

    Every press is put to ``guard`` first, with the pixel it would land on: the tool that calls this weighs that
    pixel by the elements a snapshot shows under it, as ``click_at`` is weighed (item 1252). Just before it, the page is
    read again and the pixel is checked to be the check's own element (item 1295).

    A page that could not be read is never a page that passed: after a press it is ``unfinished`` and the run is told
    to look at it."""
    started: float = time.monotonic()
    watched: set[Frame] = set()
    reading: Reading = await find_checks(page, watched)
    front: Check | None = _front(reading.checks)
    if front is None:
        if reading.unreadable:
            return _unreadable(None, started, ())
        return HumanCheckOutcome(result="none", kind="", role="", box=None, text="", seconds=time.monotonic() - started)
    if front.passed:
        return HumanCheckOutcome(
            result="passed",
            kind=front.kind,
            role=front.role,
            box=front.box,
            text=front.text,
            seconds=time.monotonic() - started,
            note="the page already says it passed",
        )
    if front.role == "challenge":
        return _with_crop(_challenge(front, reading.checks, started), await _crop(page, front.box, reading.viewport))
    if front.role == "wait":
        waited: bool = await wait_cleared(page, timeout_s)
        return HumanCheckOutcome(
            result="passed" if waited else "unfinished",
            kind=front.kind,
            role="wait",
            box=front.box,
            text=front.text,
            seconds=time.monotonic() - started,
            did=("waited for the page's own check to clear",),
            note="the page went on" if waited else "the page is still checking",
        )
    # A button to hold, a checkbox, or a widget whose inside could not be read: the page is read once more, and the
    # press goes to where the check stands now if it is the element under that pixel.
    press, latest, why = await _confirm_press(page, front, watched)
    if press is None:
        return await _read_after(page, front, latest, started, (), why, why)
    check: Check = press.check
    if front.role == "hold":
        held, note, held_after = await _hold(page, press, timeout_s, guard, watched)
        did: tuple[str, ...] = (f"held it {held:.1f} s at {check.box.words()}",)
        if _passed(held_after.checks) or _settled(held_after):
            return HumanCheckOutcome(
                result="passed",
                kind=check.kind,
                role="hold",
                box=check.box,
                text=check.text,
                seconds=time.monotonic() - started,
                did=did,
                note=note,
            )
        if held_after.unreadable:
            return _unreadable(check, started, did, note)
        return HumanCheckOutcome(
            result="unfinished",
            kind=check.kind,
            role="hold",
            box=check.box,
            text=check.text,
            seconds=time.monotonic() - started,
            did=did,
            note=f"{note} and the check is still there",
        )
    point: tuple[float, float] = press.point
    if guard is not None:
        await guard(point[0], point[1])
    await page.mouse.click(point[0], point[1])
    pressed = f"pressed the check at {point[0]:.0f},{point[1]:.0f}"
    return await _read_after(
        page,
        check,
        await _poll(page, time.monotonic() + timeout_s, watched),
        started,
        (pressed,),
        "it cleared",
        "it did not clear",
    )

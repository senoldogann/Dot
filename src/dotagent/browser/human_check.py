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
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page

from dotagent.browser.types import CheckBox, HumanCheckOutcome
from dotagent.core.images import crop_scaled

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

# The probe that finds the checks of one frame. It reads the DOM only: what is visible, where it is, what it says and
# whether it already passed. Nothing is pressed here.
_PROBE: str = r"""
() => {
  const out = [];
  const seen = new Set();
  const vw = window.innerWidth || 1280;
  const vh = window.innerHeight || 800;
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return [r.left, r.top, r.width, r.height];
  };
  const words = (el) => ((el.innerText || el.textContent || '') + '').replace(/\s+/g, ' ').trim();
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
  const said = words(document.body);
  const anchor = document.querySelector('#recaptcha-anchor, #checkbox, [role=checkbox]');
  const promptEl = document.querySelector(WIDGET_PROMPT);
  const widget = here || (embedded && (!!anchor || (said.length < 600 && (CHECKBOX.test(said) || !!promptEl))));
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
    for (const el of document.querySelectorAll(CANDIDATES)) {
      const text = words(el);
      if (!text || text.length > 120) continue;
      let role = '';
      if (HOLD.test(text)) role = 'hold';
      else if (CHECKBOX.test(text)) role = 'checkbox';
      else if (WAIT.test(text) && words(document.body).length < 600) role = 'wait';
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


def _finding(item: object, offset_x: float, offset_y: float) -> Check | None:
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
    )


async def _offset_of(frame: Frame) -> tuple[float, float] | None:
    """Where a frame's own viewport sits in the main frame's, from the box of its ``<iframe>`` element."""
    if frame.parent_frame is None:
        return (0.0, 0.0)
    try:
        element = await frame.frame_element()
        box = await element.bounding_box()
    except (PlaywrightError, TimeoutError):
        return None
    if box is None:
        return None
    return (box["x"], box["y"])


async def find_checks(page: Page) -> tuple[dict[str, float], tuple[Check, ...]]:
    """The checks in front of the page now, and the page's viewport in CSS pixels."""
    readings: list[Check] = []
    viewport: dict[str, float] = {"width": 1280.0, "height": 800.0}
    for frame in page.frames:
        try:
            raw: object = await asyncio.wait_for(frame.evaluate(_PROBE), _PROBE_TIMEOUT_S)
        except (PlaywrightError, TimeoutError):
            continue
        if not isinstance(raw, dict):
            continue
        offset: tuple[float, float] | None = await _offset_of(frame)
        if offset is None:
            continue
        if frame is page.main_frame:
            viewport = {"width": _as_float(raw.get("vw")) or 1280.0, "height": _as_float(raw.get("vh")) or 800.0}
        findings = raw.get("findings")
        for item in findings if isinstance(findings, list) else []:
            check = _finding(item, *offset)
            if check is not None:
                readings.append(check)
    return viewport, tuple(readings)


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


async def _poll(page: Page, until: float) -> tuple[dict[str, float], tuple[Check, ...]]:
    """Read the page again every ``_POLL_S`` until it says the check passed or ended, or ``until`` comes."""
    viewport, checks = await find_checks(page)
    while True:
        if _passed(checks):
            return viewport, checks
        if _cleared(checks):
            # A widget that is gone for a blink is a frame being rebuilt, not a check that passed.
            await asyncio.sleep(min(_GONE_SETTLED_S, max(0.0, until - time.monotonic())))
            viewport, checks = await find_checks(page)
            if _passed(checks) or _cleared(checks):
                return viewport, checks
            continue
        left: float = until - time.monotonic()
        if left <= 0:
            return viewport, checks
        await asyncio.sleep(min(_POLL_S, left))
        viewport, checks = await find_checks(page)


async def wait_cleared(page: Page, timeout_s: float) -> bool:
    """Wait for a check that is already working (Turnstile's, Cloudflare's) to clear itself."""
    _, checks = await _poll(page, time.monotonic() + timeout_s)
    return _passed(checks) or _cleared(checks)


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


def _near(one: CheckBox, other: CheckBox) -> bool:
    return abs(one.x - other.x) < 12 and abs(one.y - other.y) < 12


async def _hold(
    page: Page, check: Check, timeout_s: float, guard: PressGuard | None
) -> tuple[float, str, dict[str, float], tuple[Check, ...]]:
    """Press the button, hold it until the page says it took it, and let go. Returns how long it was held, what told
    the hold to end, and the reading after it."""
    x, y = check.box.center
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
            _, checks = await find_checks(page)
            now = next((one for one in checks if one.role == "hold" and _near(one.box, check.box)), None)
            if now is None:
                note = "the button went away"
                break
            if now.passed or not _HOLD_WORDS.search(now.text):
                note = "the button stopped asking to be held"
                break
    finally:
        held: float = time.monotonic() - started
        await page.mouse.up()
    viewport, checks = await _poll(page, time.monotonic() + timeout_s)
    return (held, note, viewport, checks)


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


async def pass_human_check(page: Page, timeout_s: float, guard: PressGuard | None = None) -> HumanCheckOutcome:
    """Find the human check in front of the page and pass the part of it a press can pass.

    Every press is put to ``guard`` first, with the pixel it would land on: the tool that calls this weighs that
    pixel by the elements a snapshot shows under it, as ``click_at`` is weighed (item 1252)."""
    started: float = time.monotonic()
    viewport, checks = await find_checks(page)
    front: Check | None = _front(checks)
    if front is None:
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
        return _with_crop(_challenge(front, checks, started), await _crop(page, front.box, viewport))
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
    if front.role == "hold":
        held, note, _, held_after = await _hold(page, front, timeout_s, guard)
        cleared: bool = _passed(held_after) or _cleared(held_after)
        return HumanCheckOutcome(
            result="passed" if cleared else "unfinished",
            kind=front.kind,
            role="hold",
            box=front.box,
            text=front.text,
            seconds=time.monotonic() - started,
            did=(f"held it {held:.1f} s at {front.box.words()}",),
            note=note if cleared else f"{note} and the check is still there",
        )
    # A checkbox, or a widget whose inside could not be read: one press, then the state read again until it says.
    point: tuple[float, float]
    if front.role == "checkbox":
        point = front.box.center
    else:
        point = (front.box.x + min(_CHECKBOX_INSET_X, front.box.width / 2), front.box.y + front.box.height / 2)
    if guard is not None:
        await guard(point[0], point[1])
    await page.mouse.click(point[0], point[1])
    viewport, checks = await _poll(page, time.monotonic() + timeout_s)
    after: Check | None = _front(checks)
    pressed = f"pressed the check at {point[0]:.0f},{point[1]:.0f}"
    if after is not None and after.role == "challenge":
        outcome = _challenge(after, checks, started, did=(pressed,))
        return _with_crop(outcome, await _crop(page, after.box, viewport))
    if after is None or after.passed or _cleared(checks):
        return HumanCheckOutcome(
            result="passed",
            kind=front.kind,
            role=front.role,
            box=front.box,
            text=front.text,
            seconds=time.monotonic() - started,
            did=(pressed,),
            note="it cleared",
        )
    outcome = HumanCheckOutcome(
        result="unfinished",
        kind=after.kind,
        role=after.role,
        box=after.box,
        text=after.text,
        seconds=time.monotonic() - started,
        did=(pressed,),
        note="it did not clear",
    )
    return _with_crop(outcome, await _crop(page, after.box, viewport))

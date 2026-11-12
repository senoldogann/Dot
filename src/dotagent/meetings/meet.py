"""Google Meet's page, and nothing else knows it (item 1138).

Meet is a web page with no API for a guest, so Dot drives it like a person: it turns the microphone and camera off,
asks to join, turns captions on and reads them, and types in the chat. Everything that depends on how Meet draws
itself is in ``SELECTORS``, ``_STATE_TEXT`` and ``_WATCH_SCRIPT`` here, so a change of Meet's layout is mended in
this one file. A failure says what was looked for; there is no retry loop and no guess at another layout.
"""

import json
import re
from typing import Final

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from dotagent.core.errors import DotError
from dotagent.meetings.types import CaptionSeen, ChatSeen, MeetState, PageBatch

MEET_HOST: Final[str] = "meet.google.com"
_CODE: Final[re.Pattern[str]] = re.compile(r"[a-z]{3}-[a-z]{4}-[a-z]{3}")
_SHORT_MS: Final[int] = 3000
_STEP_MS: Final[int] = 10000

# Every selector is an aria label or role Meet draws, gathered 2026-11-12 from two open-source Meet bots, Attendee and
# TranscripTonic (neither is a contract: Meet may change them without a word).
SELECTORS: Final[dict[str, str]] = {
    # Pre-join and in-call microphone and camera toggles: the label names what a click does.
    "mic_off": '[aria-label="Turn off microphone"]',
    "mic_on": '[aria-label="Turn on microphone"]',
    "camera_off": '[aria-label="Turn off camera"]',
    "camera_on": '[aria-label="Turn on camera"]',
    # The guest's name field on the pre-join screen of a call joined with no account.
    "name_field": 'input[type="text"][aria-label="Your name"]',
    # In the call: the button that leaves it is there only while Dot is in.
    "leave": 'button[aria-label="Leave call"]',
    # Captions: the toggle, its other face when they are on, and the region their blocks are drawn in.
    "captions_turn_on": 'button[aria-label="Turn on captions"]',
    "captions_turn_off": 'button[aria-label="Turn off captions"]',
    "captions_region": 'div[role="region"][tabindex="0"]',
    # Chat: the button that opens the panel, the message box (Enter sends) and the live region of the messages.
    "chat_open": 'button[aria-label="Chat with everyone"]',
    "chat_input": 'textarea[aria-label="Send a message"]',
    "chat_region": 'div[aria-live="polite"]',
}

# The texts Meet shows for each state, from the same two bots. ``classify_page`` reads them in this order.
_STATE_TEXT: Final[dict[str, tuple[str, ...]]] = {
    "ended": ("You left the meeting", "The call has ended"),
    "removed": ("You've been removed from the meeting", "You’ve been removed from the meeting"),
    "refused": (
        "You can't join this video call",
        "You can’t join this video call",
        "Someone in the call denied your request",
    ),
    "waiting": ("Asking to be let in", "Waiting for the host to join"),
}
_JOIN_BUTTON: Final[re.Pattern[str]] = re.compile(r"^(Join now|Ask to join)$")

# The one script that reads Meet's captions and chat. It watches the page for changes, and keeps keyed snapshots of
# every caption block (Meet rewrites a block's words as the speaker goes on) and every chat message in a buffer on
# ``window``, at most ``cap`` entries of each, counting what it had to drop. A block's key is a data attribute the
# script puts on the element the first time it sees it. The block layout (a speaker element, then a text element) and
# the chat layout (a sender, then the words) are read in ``blockOf`` only.
_WATCH_SCRIPT: Final[str] = """
(spec) => {
  if (window.__dotMeet) { return; }
  const state = { caps: [], chat: [], dropped: 0, last: {}, chatKeys: {}, next: 1, timer: null };
  window.__dotMeet = state;
  const blockOf = (el) => {
    const kids = Array.from(el.children);
    if (kids.length >= 2) {
      return { who: (kids[0].innerText || '').trim(), text: (kids[kids.length - 1].innerText || '').trim() };
    }
    const lines = (el.innerText || '').split('\\n').map((s) => s.trim()).filter((s) => s);
    return { who: lines.length > 1 ? lines[0] : '', text: lines.slice(1).join(' ') };
  };
  const keyOf = (el) => {
    if (!el.dataset.dotKey) { el.dataset.dotKey = 'b' + state.next++; }
    return el.dataset.dotKey;
  };
  const push = (list, item) => {
    if (list.length >= spec.cap) { list.shift(); state.dropped += 1; }
    list.push(item);
  };
  const scan = () => {
    state.timer = null;
    const now = Date.now() / 1000;
    const region = document.querySelector(spec.captions);
    if (region) {
      for (const el of Array.from(region.children)) {
        const b = blockOf(el);
        const key = keyOf(el);
        const sig = b.who + '\\u0000' + b.text;
        if (state.last[key] === sig) { continue; }
        state.last[key] = sig;
        push(state.caps, { key, speaker: b.who, text: b.text, at: now });
      }
    }
    for (const live of Array.from(document.querySelectorAll(spec.chat))) {
      if (region && region.contains(live)) { continue; }
      for (const el of Array.from(live.children)) {
        const key = keyOf(el);
        if (state.chatKeys[key]) { continue; }
        const b = blockOf(el);
        if (!b.text) { continue; }
        state.chatKeys[key] = true;
        push(state.chat, { key, sender: b.who, text: b.text, at: now });
      }
    }
  };
  const later = () => { if (state.timer === null) { state.timer = setTimeout(scan, 30); } };
  new MutationObserver(later).observe(document.body, { subtree: true, childList: true, characterData: true });
  scan();
}
"""

_DRAIN_SCRIPT: Final[str] = """
() => {
  const s = window.__dotMeet;
  if (!s) { return null; }
  const out = { captions: s.caps, chat: s.chat, dropped: s.dropped };
  s.caps = []; s.chat = []; s.dropped = 0;
  return out;
}
"""

_READ_SCRIPT: Final[str] = """
(spec) => ({
  text: document.body ? document.body.innerText : '',
  headings: Array.from(document.querySelectorAll('h1')).map((h) => h.innerText),
  leave: document.querySelector(spec.leave) !== null,
})
"""

_BUFFER_CAP: Final[int] = 500


class MeetPageError(DotError):
    """Meet's page was not what Dot expected: the message says what was looked for and where."""


def meeting_address(url: str) -> str:
    """The meeting's address, ``https://meet.google.com/<code>``, with any query or fragment dropped.

    Only that host and a code like ``abc-defg-hij`` are accepted: a meeting tab never opens another address.
    """
    match: re.Match[str] | None = re.fullmatch(
        rf"https://{re.escape(MEET_HOST)}/({_CODE.pattern})(?:[?#].*)?", url.strip()
    )
    if match is None:
        raise MeetPageError(f"A meeting address is https://{MEET_HOST}/abc-defg-hij; this is not one.", {"url": url})
    return f"https://{MEET_HOST}/{match.group(1)}"


def classify_page(text: str, headings: tuple[str, ...], has_leave: bool, has_join: bool) -> MeetState:
    """The call's state from what the page shows: its text, its h1s, whether Leave call and a join button are there."""
    if any(phrase in text for phrase in _STATE_TEXT["ended"]):
        return "ended"
    if any(phrase in text for phrase in _STATE_TEXT["removed"]):
        return "removed"
    if any(phrase in text for phrase in _STATE_TEXT["refused"]):
        return "refused"
    if has_leave:
        return "in_call"
    if any("Sign in" in heading for heading in headings):
        return "sign_in"
    if any(phrase in text for phrase in _STATE_TEXT["waiting"]):
        return "waiting"
    if has_join:
        return "joining"
    return "unknown"


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _entries(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


class MeetPage:
    """An adapter over one Playwright page that shows a Meet call."""

    def __init__(self, page: Page) -> None:
        self._page: Page = page

    async def open(self, url: str) -> None:
        """Go to ``url``. The host is not checked here (a test serves a stand-in from 127.0.0.1): the desk passes
        the address through ``meeting_address`` before it calls this."""
        await self._page.goto(url, wait_until="domcontentloaded")

    async def _off(self, label: str, off_now: str, on_now: str) -> None:
        """Make ``label`` (microphone or camera) off, and check that its toggle now offers to turn it on."""
        if await self._page.locator(on_now).first.is_visible():
            return
        button: Locator = self._page.locator(off_now).first
        try:
            await button.click(timeout=_SHORT_MS)
            await self._page.locator(on_now).first.wait_for(state="visible", timeout=_SHORT_MS)
        except PlaywrightError as error:
            raise MeetPageError(
                f"Dot could not turn the {label} off before joining, so it did not ask to join.",
                {"url": self._page.url, "looked_for": [off_now, on_now], "error": str(error)},
            ) from error

    async def prepare(self, name: str) -> MeetState:
        """Turn the microphone and the camera off (verified), fill the guest name when the field is there, and press
        Join now or Ask to join. Returns the state the page is in after that."""
        await self._off("microphone", SELECTORS["mic_off"], SELECTORS["mic_on"])
        await self._off("camera", SELECTORS["camera_off"], SELECTORS["camera_on"])
        field: Locator = self._page.locator(SELECTORS["name_field"]).first
        if await field.is_visible():
            await field.fill(name)
        join: Locator = self._page.get_by_role("button", name=_JOIN_BUTTON)
        try:
            await join.first.click(timeout=_STEP_MS)
        except PlaywrightError as error:
            raise MeetPageError(
                "Dot found no Join now or Ask to join button on the page.",
                {"url": self._page.url, "state": await self.state(), "error": str(error)},
            ) from error
        return await self.state()

    async def state(self) -> MeetState:
        """What the page shows now."""
        raw: object = await self._page.evaluate(_READ_SCRIPT, {"leave": SELECTORS["leave"]})
        if not isinstance(raw, dict):
            return "unknown"
        headings: object = raw.get("headings")
        heads: tuple[str, ...] = tuple(_text(h) for h in headings) if isinstance(headings, list) else ()
        join_visible: bool = await self._page.get_by_role("button", name=_JOIN_BUTTON).count() > 0
        return classify_page(_text(raw.get("text")), heads, raw.get("leave") is True, join_visible)

    async def captions_on(self) -> bool:
        """Whether the captions are on: Meet then offers Turn off captions."""
        return await self._page.locator(SELECTORS["captions_turn_off"]).first.is_visible()

    async def turn_captions_on(self) -> None:
        """Turn the captions on when they are off, and check that the page says they are on."""
        if await self.captions_on():
            return
        try:
            await self._page.locator(SELECTORS["captions_turn_on"]).first.click(timeout=_STEP_MS)
            await self._page.locator(SELECTORS["captions_turn_off"]).first.wait_for(state="visible", timeout=_STEP_MS)
        except PlaywrightError as error:
            raise MeetPageError(
                "Dot could not turn the captions on.",
                {"url": self._page.url, "looked_for": [SELECTORS["captions_turn_on"]], "error": str(error)},
            ) from error

    async def watch(self) -> None:
        """Install the page script that keeps the captions and the chat in a bounded buffer. Once per page."""
        await self._page.evaluate(
            _WATCH_SCRIPT,
            {"captions": SELECTORS["captions_region"], "chat": SELECTORS["chat_region"], "cap": _BUFFER_CAP},
        )

    async def drain(self) -> PageBatch:
        """Return what the page buffered since the last drain, and empty the buffer."""
        raw: object = await self._page.evaluate(_DRAIN_SCRIPT)
        if not isinstance(raw, dict):
            raise MeetPageError(
                "The page has no watcher: watch() was not called, or the page was reloaded.",
                {"url": self._page.url},
            )
        captions: tuple[CaptionSeen, ...] = tuple(
            CaptionSeen(
                key=_text(e.get("key")),
                speaker=_text(e.get("speaker")),
                text=_text(e.get("text")),
                at=_number(e.get("at")),
            )
            for e in _entries(raw.get("captions"))
        )
        chat: tuple[ChatSeen, ...] = tuple(
            ChatSeen(
                key=_text(e.get("key")),
                sender=_text(e.get("sender")),
                text=_text(e.get("text")),
                at=_number(e.get("at")),
            )
            for e in _entries(raw.get("chat"))
        )
        return PageBatch(captions=captions, chat=chat, dropped=int(_number(raw.get("dropped"))))

    async def post(self, text: str) -> None:
        """Type ``text`` in the chat, send it with Enter, and check that it appears in the chat. Opens the chat
        first when it is closed. Raises when the message does not show up."""
        box: Locator = self._page.locator(SELECTORS["chat_input"]).first
        try:
            if not await box.is_visible():
                await self._page.locator(SELECTORS["chat_open"]).first.click(timeout=_STEP_MS)
            await box.fill(text, timeout=_STEP_MS)
            await box.press("Enter")
            await self._page.wait_for_function(
                "([sel, words]) => Array.from(document.querySelectorAll(sel)).some((e) => e.innerText.includes(words))",
                arg=[SELECTORS["chat_region"], text],
                timeout=_STEP_MS,
            )
        except PlaywrightError as error:
            raise MeetPageError(
                "Dot typed a message in the meeting's chat but could not see it appear there.",
                {
                    "url": self._page.url,
                    "looked_for": [SELECTORS["chat_open"], SELECTORS["chat_input"], SELECTORS["chat_region"]],
                    "text": json.dumps(text[:80], ensure_ascii=False),
                    "error": str(error),
                },
            ) from error

    async def leave(self) -> None:
        """Press Leave call."""
        try:
            await self._page.locator(SELECTORS["leave"]).first.click(timeout=_STEP_MS)
        except PlaywrightError as error:
            raise MeetPageError(
                "Dot found no Leave call button, so it may not be in the call.",
                {"url": self._page.url, "looked_for": [SELECTORS["leave"]], "error": str(error)},
            ) from error

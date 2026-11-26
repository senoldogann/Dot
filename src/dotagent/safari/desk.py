"""The owner's Safari, reached through AppleScript (item 1959): which tab a chat works in, opening an address there, and
the page script run in that tab with ``do JavaScript``. Safari's own windows, its signed-in sites and its AutoFill are
the owner's: Dot drives the tab they are in and never another session.

Two settings are the owner's to turn on once, never Dot's: macOS's Automation permission for Dot to send Apple events to
Safari, and Safari's "Allow JavaScript from Apple Events" (Settings › Developer, shown once "Show features for web
developers" is ticked under Settings › Advanced). Without either, a call says which and where, and nothing is changed.
"""

import asyncio
import json
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.core.errors import DesktopError, ToolExecutionError
from dotagent.core.logs import get_logger
from dotagent.core.types import JsonObject
from dotagent.desktop.script import run_osascript
from dotagent.safari.page_script import page_command
from dotagent.safari.types import PageRead, PlaceReply, ReadReply, StepAnswer, StepReply, TabPin

_log = get_logger(__name__)

# How long one call of osascript to Safari may take: a page's script runs at once, and Safari answers within a second
# unless a dialog of its own holds it.
_CALL_TIMEOUT_S: float = 20.0
# The chats whose tab Dot keeps in mind: the oldest is forgotten past this, and its next step reads the front tab.
_KEPT_CHATS: int = 64
# How often a wait reads the tab, and how long a step's page is given to settle once it acted.
_POLL_S: float = 0.25
_SETTLE_S: float = 3.0
_LOAD_S: float = 20.0
# The error numbers osascript gives that are not the page script's setting.
_NOT_RUNNING: int = -600
_NOT_ALLOWED: int = -1743
# What Safari 26 answers ``do JavaScript`` while "Allow JavaScript from Apple Events" is off, measured on the owner's
# Mac on 2026-11-26: "Error: You must enable 'Allow JavaScript from Apple Events' in the Developer section of Safari
# Settings to use 'do JavaScript'. (8)", in English whatever the Mac's language.
_JAVASCRIPT_OFF: int = 8
_JAVASCRIPT_OFF_WORDS: str = "Allow JavaScript from Apple Events"
# How many times a read follows a tab that went to another page before it gives up.
_MOVES: int = 3
# Whether Dot may read and act on a page of Safari, by its address: ``None`` when it may, else why not (Dot's own
# interface, a blocked host, a host outside ``[browser] allowed_hosts``: the rule of Dot's Chrome).
type PageRule = Callable[[str], str | None]
# The error number at the end of osascript's message.
_ERROR_NUMBER: re.Pattern[str] = re.compile(r"\((-?\d{1,6})\)\s*$")

# What the owner turns on, once, for Dot to read and act in Safari's pages; Dot never turns it on itself.
JAVASCRIPT_SETTING: str = (
    "Safari did not run Dot's script in the page. The owner turns this on once (Dot never does): in Safari, "
    "Settings… › Advanced › tick “Show features for web developers”, then Settings… › Developer › tick “Allow "
    "JavaScript from Apple Events”. Tell the owner exactly that, in one line, and until then drive Safari with "
    "desktop_act and desktop_snapshot, or Dot's Chrome with browser_open."
)

# The script that reaches Safari: ``front`` names the tab a chat starts in; ``open`` loads an address in a tab Dot
# opened for the chat while it still shows what Dot last saw there, else in a new tab, never over a tab of the
# owner's; ``js`` runs a page script in the chat's tab while it still shows the address Dot last saw there.
_SAFARI: str = """
function run(argv) {
  const safari = Application("Safari");
  const op = argv[0];
  const place = (w, t) => JSON.stringify({ window: w.id(), tab: t.index(), url: String(t.url() || "") });
  const windowOf = (id) => {
    if (!id) return null;
    try { const w = safari.windows.byId(Number(id)); w.id(); return w; } catch (error) { return null; }
  };
  const frontWindow = () => {
    for (const w of safari.windows()) {
      try { w.currentTab(); return w; } catch (error) { continue; }
    }
    return null;
  };
  if (op === "front") {
    if (!safari.running()) return JSON.stringify({ running: false });
    const w = frontWindow();
    return w ? place(w, w.currentTab()) : JSON.stringify({ running: true });
  }
  if (op === "open") {
    const url = argv[1];
    const own = windowOf(argv[3]);
    const index = Number(argv[4]);
    // The chat's own tab is loaded again only while it still shows what Dot last saw in it; else a new tab.
    if (argv[2] === "same" && own && index >= 1 && index <= own.tabs.length) {
      const tab = own.tabs[index - 1];
      if (String(tab.url() || "") === argv[5]) {
        tab.url = url;
        return place(own, tab);
      }
    }
    const w = own || frontWindow();
    if (!w) {
      safari.Document().make();
      const made = frontWindow();
      made.currentTab().url = url;
      return place(made, made.currentTab());
    }
    const tab = safari.Tab({ url: url });
    w.tabs.push(tab);
    w.currentTab = tab;
    return place(w, w.currentTab());
  }
  if (op === "js") {
    const w = windowOf(argv[1]);
    if (!w) return JSON.stringify({ gone: true });
    const index = Number(argv[2]);
    if (index < 1 || index > w.tabs.length) return JSON.stringify({ gone: true });
    const tab = w.tabs[index - 1];
    const url = String(tab.url() || "");
    if (argv[3] !== "" && url !== argv[3]) return JSON.stringify({ moved: true, url: url });
    const out = safari.doJavaScript(argv[4], { in: tab });
    return JSON.stringify({ url: url, out: out === undefined || out === null ? "" : String(out) });
  }
  return JSON.stringify({ unknown: op });
}
"""


class _Answer(BaseModel):
    """Safari's answer to the script above, before the page's own answer inside it is read."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    running: bool = True
    window: int | None = None
    tab: int | None = None
    url: str = ""
    gone: bool = False
    moved: bool = False
    out: str | None = None


@dataclass
class _Chat:
    """What Dot keeps of a chat's Safari: the tab it works in, and the latest read of that tab."""

    pin: TabPin
    read: PageRead | None


class SafariDesk:
    """The owner's Safari, one tab per chat. Not safe for two calls of one chat at once; the tools that use it are not
    run in parallel."""

    def __init__(self) -> None:
        self._chats: OrderedDict[str, _Chat] = OrderedDict()

    def last_read(self, thread_id: str) -> PageRead | None:
        """The chat's latest read of its tab, which a batch is judged by before it runs."""
        chat: _Chat | None = self._chats.get(thread_id)
        return None if chat is None else chat.read

    def pinned(self, thread_id: str) -> TabPin | None:
        chat: _Chat | None = self._chats.get(thread_id)
        return None if chat is None else chat.pin

    def keep_read(self, thread_id: str, pin: TabPin, read: PageRead) -> None:
        """Keep ``read`` as the chat's latest read of the tab ``pin``, which its next batch is judged by."""
        self._keep(thread_id, _Chat(pin=pin, read=read))

    def _keep(self, thread_id: str, chat: _Chat) -> None:
        self._chats[thread_id] = chat
        self._chats.move_to_end(thread_id)
        while len(self._chats) > _KEPT_CHATS:
            self._chats.popitem(last=False)

    async def _call(self, argv: tuple[str, ...]) -> _Answer:
        try:
            said: str = await run_osascript("JavaScript", _SAFARI, argv, (), _CALL_TIMEOUT_S, None, ("Safari",))
        except DesktopError as error:
            raise explained(error, argv[0]) from error
        try:
            return _Answer.model_validate_json(said)
        except ValidationError as error:
            raise ToolExecutionError("Safari's answer could not be read", {"op": argv[0]}) from error

    async def front(self, thread_id: str) -> TabPin:
        """The chat's tab: the one it worked in, else the front window's current tab, which it keeps from now on."""
        pin: TabPin | None = self.pinned(thread_id)
        if pin is not None:
            return pin
        answer: _Answer = await self._call(("front",))
        if not answer.running:
            raise ToolExecutionError("Safari is not running: open a page with safari_open first", {})
        if answer.window is None or answer.tab is None:
            raise ToolExecutionError("Safari has no window: open a page with safari_open first", {})
        pin = TabPin(window=answer.window, tab=answer.tab, url=answer.url)
        self._keep(thread_id, _Chat(pin=pin, read=None))
        return pin

    async def open(self, thread_id: str, url: str, new_tab: bool, rule: PageRule) -> TabPin:
        """Load ``url`` (an address the rules let Dot open) and keep its tab for the chat: the tab Dot opened for the
        chat before, while it still shows the page Dot last saw there; otherwise, and with ``new_tab``, a new tab. A
        tab the owner had open (the front tab a read started in) is never loaded over."""
        pin: TabPin | None = self.pinned(thread_id)
        reuse: bool = pin is not None and pin.opened and not new_tab
        argv: tuple[str, ...] = (
            "open",
            url,
            "same" if reuse else "new",
            "" if pin is None else str(pin.window),
            "" if pin is None else str(pin.tab),
            "" if pin is None else pin.url,
        )
        answer: _Answer = await self._call(argv)
        if answer.window is None or answer.tab is None:
            raise ToolExecutionError("Safari did not say which tab it opened", {})
        opened: TabPin = TabPin(window=answer.window, tab=answer.tab, url=url, opened=True)
        self._keep(thread_id, _Chat(pin=opened, read=None))
        await self._loaded(thread_id, url, rule)
        return self.pinned(thread_id) or opened

    async def _page(self, thread_id: str, command: JsonObject, rule: PageRule, strict: bool) -> tuple[str, str]:
        """The page script's answer to ``command`` in the chat's tab, and the address Safari gave for the tab. The
        script runs only in a page whose address ``rule`` lets Dot read: Dot's own interface and a blocked host are
        never read nor acted on, as in Chrome, since the owner may be signed in there and a run could answer its own
        questions. ``strict`` (a step) runs it only where Dot last read; otherwise a tab that went to another page is
        judged by its new address, and read there when the rule lets it."""
        pin: TabPin = await self.front(thread_id)
        expected: str = pin.url
        for _attempt in range(_MOVES):
            refusal: str | None = rule(expected) if expected else "the tab shows no page"
            if refusal is not None:
                raise ToolExecutionError(
                    f"Dot does not read or act on the page of this Safari tab ({refusal}): open another with "
                    "safari_open",
                    {},
                )
            answer: _Answer = await self._call(("js", str(pin.window), str(pin.tab), expected, page_command(command)))
            if answer.gone:
                self._chats.pop(thread_id, None)
                raise ToolExecutionError(
                    "the Safari tab this chat worked in is closed: safari_read reads the front tab again", {}
                )
            if answer.moved and strict:
                raise ToolExecutionError(
                    "the Safari tab shows another page than the one you last read: safari_read it again before acting",
                    {},
                )
            if answer.moved:
                expected = answer.url
                continue
            if answer.out is None or answer.out == "":
                raise ToolExecutionError(JAVASCRIPT_SETTING, {})
            self._keep(thread_id, _Chat(pin=pin.model_copy(update={"url": answer.url}), read=self.last_read(thread_id)))
            return answer.out, answer.url
        raise ToolExecutionError("the Safari tab kept going to other pages: read it again", {})

    async def read(self, thread_id: str, limit: int, text_chars: int, rule: PageRule) -> PageRead:
        """The elements and the visible text of the chat's tab, kept as the read its next batch is judged by."""
        said, address = await self._page(
            thread_id, {"op": "read", "limit": limit, "text_chars": text_chars}, rule, False
        )
        reply: ReadReply = parsed(ReadReply, said)
        if not reply.ok or reply.page is None:
            raise ToolExecutionError("the page did not let Dot read it: its own script stood in the way", {})
        _own_address_allowed(reply.page.url, rule)
        pin: TabPin = await self.front(thread_id)
        self.keep_read(thread_id, pin.model_copy(update={"url": address}), reply.page)
        return reply.page

    async def step(self, thread_id: str, command: JsonObject, rule: PageRule) -> StepAnswer:
        """One step of a batch in the chat's tab, which still shows the page it was read on."""
        said, _ = await self._page(thread_id, {"op": "step", "step": command}, rule, True)
        reply: StepReply = parsed(StepReply, said)
        if not reply.ok or reply.step is None:
            raise ToolExecutionError("the page did not take the step: its own script stood in the way", {})
        return reply.step

    async def _where(self, thread_id: str, text_chars: int, rule: PageRule) -> tuple[PlaceReply, str]:
        said, address = await self._page(thread_id, {"op": "where", "text_chars": text_chars}, rule, False)
        reply: PlaceReply = parsed(PlaceReply, said)
        if not reply.ok:
            raise ToolExecutionError("the page did not say where it is: its own script stood in the way", {})
        _own_address_allowed(reply.url, rule)
        return reply, address

    async def place(self, thread_id: str, text_chars: int, rule: PageRule) -> PlaceReply:
        """Where the chat's tab is now: its document's address, whether it loaded, the start of its text."""
        reply, _ = await self._where(thread_id, text_chars, rule)
        return reply

    async def _loaded(self, thread_id: str, url: str, rule: PageRule) -> None:
        """Wait until the chat's tab shows the document of the address Safari gives for it (not the one it showed
        before the load) and that document finished loading, at most :data:`_LOAD_S`; a page that never finishes is
        read as it stands."""
        ends: float = time.monotonic() + _LOAD_S
        while time.monotonic() < ends:
            place, address = await self._where(thread_id, 0, rule)
            if place.ready == "complete" and _without_fragment(place.url) == _without_fragment(address):
                return
            await asyncio.sleep(_POLL_S)
        _log.info("safari_page_still_loading", url=url[:200], waited_s=_LOAD_S)

    async def settle(self, thread_id: str, text_chars: int, rule: PageRule) -> PlaceReply:
        """Wait for the tab to settle after a step: a page that loads is waited for, one whose address and text stay
        as they were for two reads in a row is taken as it is, at most :data:`_SETTLE_S`."""
        ends: float = time.monotonic() + _SETTLE_S
        before: PlaceReply = await self.place(thread_id, text_chars, rule)
        while time.monotonic() < ends:
            await asyncio.sleep(_POLL_S)
            now: PlaceReply = await self.place(thread_id, text_chars, rule)
            if now.ready == "complete" and (now.url, now.text) == (before.url, before.text):
                return now
            before = now
        return before

    async def wait_for_text(self, thread_id: str, text: str, timeout_s: float, text_chars: int, rule: PageRule) -> bool:
        """Whether ``text`` shows in the tab's visible text within ``timeout_s``, letter case aside."""
        wanted: str = text.casefold()
        ends: float = time.monotonic() + timeout_s
        while True:
            place: PlaceReply = await self.place(thread_id, text_chars, rule)
            if wanted in place.text.casefold():
                return True
            if time.monotonic() >= ends:
                return False
            await asyncio.sleep(_POLL_S)


def _without_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def _own_address_allowed(url: str, rule: PageRule) -> None:
    """Refuse what a document says of itself when its own address is one Dot does not read: the tab's address is
    judged before the script runs, and a document still loading may be the one the tab showed before."""
    refusal: str | None = rule(url)
    if refusal is not None:
        raise ToolExecutionError(
            f"Dot does not read or act on the page of this Safari tab ({refusal}): open another with safari_open",
            {},
        )


def parsed[Reply: BaseModel](model: type[Reply], said: str) -> Reply:
    """The page script's answer as ``model``; one that is not (a page that broke the script) is an error."""
    try:
        return model.model_validate(json.loads(said))
    except (ValueError, ValidationError) as error:
        # What the page answered is its own words: it is not repeated to the run, which reads errors unfenced.
        raise ToolExecutionError("the page answered Dot's script with something it could not read", {}) from error


def explained(error: DesktopError, op: str) -> ToolExecutionError:
    """What a failed call to Safari means for the run: Dot not allowed to script Safari, Safari not running, or, for a
    page script, the setting that lets it run off. macOS writes its own messages in the owner's language, so the number
    at the end is what is read."""
    stderr: str = str(error.context.get("stderr", ""))
    found: re.Match[str] | None = _ERROR_NUMBER.search(stderr.strip())
    number: int | None = None if found is None else int(found.group(1))
    if number == _NOT_ALLOWED:
        return ToolExecutionError(
            "macOS does not let Dot control Safari: the owner allows Dot under System Settings › Privacy & Security › "
            "Automation › Safari. Tell them so in one line; do not retry until then.",
            {"op": op},
        )
    if number == _NOT_RUNNING:
        return ToolExecutionError("Safari is not running: open a page with safari_open first", {"op": op})
    if op == "js" and (number == _JAVASCRIPT_OFF or _JAVASCRIPT_OFF_WORDS in stderr):
        return ToolExecutionError(JAVASCRIPT_SETTING, {})
    # What osascript said may hold a page's words (an error a page made Dot's script throw): only its number is told.
    return ToolExecutionError("Safari refused the call", {"op": op, "error_number": number})

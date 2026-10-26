"""Pure browser safety rules: which URLs may be opened and how much damage a step can do.

The page is untrusted: an element's name decides how a click or a keystroke is classified, and the policy engine
then decides whether the user must be asked, whether the step is refused, or whether it is handed to the user.
"""

import ipaddress
import re
import socket
from collections.abc import Collection, Sequence
from typing import get_args
from urllib.parse import unquote, urlsplit

from dotagent.agent.pages import PAGES_PREFIX
from dotagent.browser.snapshot import SnapshotLine, parse_snapshot
from dotagent.browser.types import SecretField, SecretKind, Step, StepAction
from dotagent.config import ServerConfig
from dotagent.core.errors import BrowserError
from dotagent.core.types import FrozenModel
from dotagent.mcp.signin import CALLBACK_PATH
from dotagent.policy.elements import (
    Element,
    StepAssessment,
    asks_to_pay,
    assessment_for,
    clicking_reasons,
    looks_like_key,
    naming_reasons,
    quiet_assessment,
    typing_reasons,
)
from dotagent.policy.types import ApprovalMode, Flag

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
# Addresses a page makes itself, whose data never leaves the page: no server answers them.
_PAGE_OWN_SCHEMES: frozenset[str] = frozenset({"blob", "data"})
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}
_LOOPBACK_NAME: str = "localhost"
# The path of a page a run showed (item 601): the store's ids are "page_" and URL-safe letters.
_SHOWN_PAGE_PATH: re.Pattern[str] = re.compile(rf"{re.escape(PAGES_PREFIX)}page_[A-Za-z0-9_-]+")
_MAX_URL_CHARS: int = 2000
_SCHEME: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_HOST_LIKE: re.Pattern[str] = re.compile(r"^(localhost|[\w-]+(\.[\w-]+)+|\d{1,3}(\.\d{1,3}){3})(:\d+)?([/?#].*)?$")
_UNSAFE_FILE_CHARS: re.Pattern[str] = re.compile(r"[^\w.\- ()]+")
_MAX_FILE_NAME_CHARS: int = 120
_TYPING_ACTIONS: frozenset[StepAction] = frozenset({"fill", "type", "press", "select", "insert"})
# The keys that press the control that has the focus, as a click on it does (the review of item 561): Enter submits a
# form or presses a button, Space presses a button or ticks a box.
_ACTIVATING_KEYS: frozenset[str] = frozenset({"enter", "numpadenter", "space", " "})
_CLICKING_ACTIONS: frozenset[StepAction] = frozenset({"click", "double_click", "right_click", "click_at"})
_QUIET_ACTIONS: frozenset[StepAction] = frozenset({"hover", "scroll_into_view"})
# The controls whose name says what dragging them does, as a click on them would (a dragged list item is named for what
# it is, not for what the drag does).
_CONTROL_ROLES: frozenset[str] = frozenset({"button", "link", "menuitem", "tab", "option", "checkbox", "switch"})
# The steps that put the focus on what they name, those that leave it where it was, as keys go where the focus is
# (:func:`keys_go_to`, item 682); any other step leaves it where Dot cannot tell.
_FOCUSING_ACTIONS: frozenset[StepAction] = frozenset(
    {"click", "double_click", "right_click", "fill", "type", "insert", "select", "check", "uncheck", "press"}
)
_FOCUS_KEEPING_ACTIONS: frozenset[StepAction] = frozenset({"hover", "scroll_into_view"})
# The fields Enter submits from, and the keys that do (item 682).
_TEXT_FIELD_ROLES: frozenset[str] = frozenset({"textbox", "searchbox", "combobox", "spinbutton"})
# The other controls of a form that Enter submits it from (a checkbox, a radio button), as a text field does.
_ENTER_SUBMITS_ROLES: frozenset[str] = frozenset({"checkbox", "radio"})
_SUBMITTING_KEYS: frozenset[str] = frozenset({"enter", "numpadenter"})
_SECRET_KINDS: dict[str, SecretKind] = {kind: kind for kind in get_args(SecretKind)}
# Keys that move, delete, submit or modify rather than put a character in (Playwright's key names, lower case).
_KEYS_WITHOUT_TEXT: frozenset[str] = frozenset(
    {
        "enter",
        "numpadenter",
        "tab",
        "escape",
        "backspace",
        "delete",
        "insert",
        "home",
        "end",
        "pageup",
        "pagedown",
        "arrowup",
        "arrowdown",
        "arrowleft",
        "arrowright",
        "shift",
        "control",
        "alt",
        "meta",
        "controlormeta",
        "capslock",
        "numlock",
        "scrolllock",
        "contextmenu",
        "pause",
        "printscreen",
    }
)
_FUNCTION_KEY: re.Pattern[str] = re.compile(r"^f\d{1,2}$")
_SHORTCUT_MODIFIERS: frozenset[str] = frozenset({"control", "meta", "controlormeta"})


class UrlVerdict(FrozenModel):
    allowed: bool
    reason: str


def normalize_url(raw: str) -> str:
    """Turn what the model wrote into an absolute URL; a bare host such as ``example.com`` gets ``https://``."""
    text: str = raw.strip()
    if _SCHEME.match(text) is not None and not re.match(r"^[\w-]+(\.[\w-]+)*:\d+", text):
        return text
    if _HOST_LIKE.match(text) is not None:
        scheme: str = "http" if text.startswith(("localhost", "127.0.0.1")) else "https"
        return f"{scheme}://{text}"
    return text


def host_matches(host: str, entries: Sequence[str]) -> bool:
    """Whether ``host`` is one of ``entries`` or a subdomain of one. Both are read the way a browser reads a host
    (:func:`browser_host`), so that an entry the owner wrote in Unicode, in punycode, with the dot of an absolute name
    or as a numeric address names the host a browser goes to by any of its spellings (item 554). An entry that cannot be
    read as a host names none."""
    name: str | None = _plain_host(host)
    if name is None:
        return False
    return any(
        name == entry or name.endswith(f".{entry}")
        for entry in (_plain_host(written.lstrip(".")) for written in entries)
        if entry
    )


def _plain_host(host: str) -> str | None:
    """``host`` as :func:`browser_host` reads it, without the dot that ends the name of an absolute host."""
    name: str | None = browser_host(host)
    return None if name is None else name.removesuffix(".")


def browser_host(host: str) -> str | None:
    """``host`` as a browser reads it, so the checks judge the host the request goes to (the code review's Y1): its
    percent escapes decoded first (``127%2e0%2e0%2e1``), a name with the other dots of Unicode (``127。0。0。1``) and
    its full-width letters read as IDNA does, lower case, and an IPv4 address in any spelling (``2852039166``,
    ``0x7f.1``, ``127.1``) as its four numbers. None for a host IDNA cannot read, as one whose escapes are no UTF-8."""
    decoded: str = unquote(host)
    try:
        name: str = decoded.lower() if decoded.isascii() else decoded.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    try:
        # A browser reads an IPv4 address with one dot at its end as the address.
        return str(ipaddress.IPv4Address(socket.inet_aton(name.removesuffix("."))))
    except OSError:
        return name


def _backslashed(url: str) -> bool:
    """Whether ``url`` has a backslash before its query, which a browser takes for ``/`` where Python's parser does
    not."""
    return "\\" in url.partition("#")[0].partition("?")[0]


def check_url(url: str, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str]) -> UrlVerdict:
    """Whether the browser may open ``url``: web pages only, never a blocked host, only allowed hosts when set. The host
    is judged as a browser reads it (:func:`browser_host`), and an address a browser reads otherwise than this check is
    refused: one with a backslash before its query (:func:`_backslashed`)."""
    if url == "about:blank":
        return UrlVerdict(allowed=True, reason="blank page")
    if _backslashed(url):
        return UrlVerdict(allowed=False, reason="the URL holds a backslash, which a browser reads as /")
    try:
        parts = urlsplit(url)
        written: str | None = parts.hostname
    except ValueError as exc:
        return UrlVerdict(allowed=False, reason=f"malformed URL ({exc})")
    if parts.scheme not in _ALLOWED_SCHEMES:
        return UrlVerdict(allowed=False, reason=f"scheme {parts.scheme!r} is not allowed; only http and https open")
    if not written:
        return UrlVerdict(allowed=False, reason="the URL has no host")
    host: str | None = browser_host(written)
    if host is None:
        return UrlVerdict(allowed=False, reason=f"host {written!r} cannot be read as a browser reads it")
    if host_matches(host, blocked_hosts):
        return UrlVerdict(allowed=False, reason=f"host {host!r} is on the blocked list (browser.blocked_hosts)")
    if allowed_hosts and not host_matches(host, allowed_hosts):
        return UrlVerdict(allowed=False, reason=f"host {host!r} is not in browser.allowed_hosts")
    return UrlVerdict(allowed=True, reason="allowed")


def _is_loopback(host: str) -> bool:
    """Whether ``host`` names this Mac: ``localhost`` or a name under it, or a loopback or unspecified address in any
    spelling a browser reads (``127.1``, ``2130706433``, ``0x7f.0.0.1``, ``::1``, ``::ffff:127.0.0.1``)."""
    if host_matches(host, (_LOOPBACK_NAME,)):
        return True
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback or address.ipv4_mapped.is_unspecified
    return address.is_loopback or address.is_unspecified


def forbidden_target(url: str, blocked_hosts: Sequence[str], server: ServerConfig) -> UrlVerdict:
    """Whether the browser may send a request to ``url`` at all, for a page it opens or a resource a page loads alike:
    a web address that is neither a blocked host nor Dot's own interface (this Mac on the daemon's port, or a name of
    ``[server] reach``). The owner's Chrome may be signed in there, and a run that opened it could answer its own
    questions. ``browser.allowed_hosts`` is not read here: a page of an allowed host loads its scripts and images from
    wherever it has them (:func:`check_browser_url` reads it for the pages themselves)."""
    verdict: UrlVerdict = check_url(url, (), blocked_hosts)
    if not verdict.allowed or url == "about:blank":
        return verdict
    parts = urlsplit(url)
    try:
        port: int | None = parts.port
    except ValueError as exc:
        return UrlVerdict(allowed=False, reason=f"malformed URL ({exc})")
    # A browser reads one dot at the end of a host as none: 127.0.0.1. is 127.0.0.1, the address the token lives at.
    host: str = (browser_host(parts.hostname or "") or "").removesuffix(".")
    reached: int = port if port is not None else _DEFAULT_PORTS[parts.scheme]
    if host_matches(host, server.reach) or (reached == server.port and _is_loopback(host)):
        return UrlVerdict(allowed=False, reason="it is Dot's own interface, which a run never opens")
    return verdict


def shown_page(url: str, server: ServerConfig) -> bool:
    """Whether ``url`` is a page a run showed (item 601): ``/pages/<id>`` exactly, on this Mac at the daemon's port, the
    address show_page gives the run so that it may look at what it showed. It is served without the token to whoever
    names its id, which cannot be guessed, and sandboxed wherever it opens (``PAGE_HEADERS``): its origin is opaque, so
    its scripts reach neither the API nor the UI, and a navigation from it to the interface is refused. Nothing else
    of the interface passes, nor an address a browser reads otherwise than Python (:func:`_backslashed`)."""
    if _backslashed(url):
        return False
    try:
        parts = urlsplit(url)
        port: int | None = parts.port
    except ValueError:
        return False
    if parts.scheme not in _ALLOWED_SCHEMES or parts.query or not _SHOWN_PAGE_PATH.fullmatch(parts.path):
        return False
    host: str = (browser_host(parts.hostname or "") or "").removesuffix(".")
    reached: int = port if port is not None else _DEFAULT_PORTS[parts.scheme]
    return reached == server.port and _is_loopback(host)


def check_browser_url(
    url: str, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str], server: ServerConfig
) -> UrlVerdict:
    """What the browser may open as a page: a page a run showed (:func:`shown_page`), or no :func:`forbidden_target`
    and a host of ``allowed_hosts`` when that is set."""
    if shown_page(url, server):
        return UrlVerdict(allowed=True, reason="a page a run showed")
    verdict: UrlVerdict = forbidden_target(url, blocked_hosts, server)
    if not verdict.allowed or url == "about:blank" or not allowed_hosts:
        return verdict
    host: str = urlsplit(url).hostname or ""
    if not host_matches(host, allowed_hosts):
        return UrlVerdict(allowed=False, reason=f"host {host!r} is not in browser.allowed_hosts")
    return verdict


def sign_in_return(url: str, server: ServerConfig) -> bool:
    """Whether ``url`` is the way back of a sign-in to Dot (item 448): the daemon's ``/oauth/callback`` (exactly, as
    Chrome read it) on this Mac at the daemon's port, where Google, Microsoft and the MCP services send the browser with
    the code of a sign-in the owner started. The sign-in may open in Dot's own Chrome (macOS hands an address to the
    Chrome that runs), whose guard refuses Dot's interface; the way back is let through as a page, and nothing else of
    the interface. :func:`check_browser_url` still refuses it, so a run whose tab lands there has the tab reset. A run
    gains nothing by it: the daemon takes only a state it handed out itself, once, for ten minutes, and the page
    carries no token. An address a browser reads otherwise than Python (:func:`_backslashed`) is not it."""
    if _backslashed(url):
        return False
    try:
        parts = urlsplit(url)
        port: int | None = parts.port
    except ValueError:
        return False
    if parts.scheme not in _ALLOWED_SCHEMES or parts.path != CALLBACK_PATH:
        return False
    host: str = (browser_host(parts.hostname or "") or "").removesuffix(".")
    reached: int = port if port is not None else _DEFAULT_PORTS[parts.scheme]
    return reached == server.port and _is_loopback(host)


def request_verdict(
    url: str, navigation: bool, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str], server: ServerConfig
) -> UrlVerdict:
    """Whether Chrome may send one request it is about to send, judged on the address as Chrome read it (so a spelling
    that reads differently here cannot slip by): a navigation of a frame is a page and must pass
    :func:`check_browser_url`, but for the way back of a sign-in (:func:`sign_in_return`), which opens as a page alone;
    any other request (a script, an image, a fetch, a worker) must not be a :func:`forbidden_target`. What a page made
    itself (a ``blob:`` or ``data:`` address) reaches no server and is let through."""
    if navigation:
        if sign_in_return(url, server):
            return UrlVerdict(allowed=True, reason="the way back of a sign-in to Dot")
        return check_browser_url(url, allowed_hosts, blocked_hosts, server)
    try:
        scheme: str = urlsplit(url).scheme
    except ValueError as exc:
        return UrlVerdict(allowed=False, reason=f"malformed URL ({exc})")
    if scheme in _PAGE_OWN_SCHEMES:
        return UrlVerdict(allowed=True, reason="the page's own data")
    return forbidden_target(url, blocked_hosts, server)


def url_flags(url: str) -> tuple[Flag, ...]:
    """A very long URL or one that embeds a key can carry data out of the machine."""
    if len(url) > _MAX_URL_CHARS or looks_like_key(url):
        return ("exfiltration",)
    return ()


def parse_element(line: str) -> Element | None:
    """Role and accessible name from a snapshot line, or ``None`` when the line is not an element. An element with no
    name of its own is named by the text it shows inline (item 682: ``- generic [ref=e5] [cursor=pointer]: Pay now`` is
    what a click on it presses). A text field's inline text is its value, not its name, and never names it."""
    parsed: tuple[SnapshotLine, ...] = parse_snapshot(line)
    if not parsed or not parsed[0].role:
        return None
    shown: str = "" if parsed[0].role in _TEXT_FIELD_ROLES else (parsed[0].inline or "").strip()
    return Element(role=parsed[0].role, name=parsed[0].name or shown)


def _activates(step: Step) -> bool:
    """Whether ``step`` is a key that presses the control it is given (:data:`_ACTIVATING_KEYS`), with or without a
    modifier, as a click on it does."""
    if step.action != "press" or not step.key:
        return False
    key: str = step.key if step.key == " " else step.key.rsplit("+", 1)[-1]
    return key.lower() in _ACTIVATING_KEYS


def keys_go_to(steps: Sequence[Step], refs: Sequence[str | None]) -> list[str | None]:
    """The ref each step of a batch acts on: its own, or, for a key or a text that names none, where the focus is
    then (item 682). The focus is the ref of the last step that puts it on what it names (:data:`_FOCUSING_ACTIONS`:
    a fill, a type, a click, a choice); a step that does not move it (a hover, a scroll into view, a key that names
    no field) leaves it as it was; a step that moves it where Dot cannot tell (a click at a point, a Tab, any other
    step that names nothing) leaves it unknown, ``None``, and a key after that names no field. One class of the
    steps that place the focus, rather than one step at a time (the code review of this item took six rounds over
    them)."""
    focus: str | None = None
    went: list[str | None] = []
    for step, ref in zip(steps, refs, strict=True):
        went.append(focus if ref is None and step.action in _TYPING_ACTIONS else ref)
        if _moves_focus(step):
            focus = None
        elif step.action in _FOCUSING_ACTIONS and ref is not None:
            focus = ref
        elif step.action in _FOCUS_KEEPING_ACTIONS:
            pass
        elif ref is None and step.action in _TYPING_ACTIONS:
            # A key or a text that names no field goes where the focus is and leaves it there.
            pass
        else:
            focus = None
    return went


def _moves_focus(step: Step) -> bool:
    """Whether a key step moves the focus away: Tab, with or without a modifier."""
    return step.action == "press" and bool(step.key) and str(step.key).rsplit("+", 1)[-1].lower() == "tab"


def submits_unnamed(step: Step) -> bool:
    """Whether ``step`` presses Enter (with or without a modifier) or types a line break where no field is named, in
    the batch or by the step: what it sends is not known, so it is judged as a send (item 682)."""
    if step.action == "press" and step.key:
        return step.key.rsplit("+", 1)[-1].lower() in _SUBMITTING_KEYS
    return step.action in ("type", "insert") and step.text is not None and ("\n" in step.text or "\r" in step.text)


def submits_field(step: Step, element: Element | None) -> bool:
    """Whether ``step`` sends what a text field holds: Enter (with or without a modifier) pressed in it, or a line
    break typed into it, which a page takes as the press of its form's button or of the Send beside a composer (item
    682)."""
    if element is None or element.role not in _TEXT_FIELD_ROLES | _ENTER_SUBMITS_ROLES:
        return False
    if step.action == "press" and step.key:
        return step.key.rsplit("+", 1)[-1].lower() in _SUBMITTING_KEYS
    if element.role in _ENTER_SUBMITS_ROLES:
        return False
    return step.action in ("type", "insert") and step.text is not None and ("\n" in step.text or "\r" in step.text)


def assess_step(
    step: Step, element: Element | None, target: Element | None, near: Sequence[Element] = ()
) -> StepAssessment:
    """Risk and flags of one step, judged from the action and the element it targets. A click is judged by its
    element's name whatever its role (item 682: a page's ``div`` that pays is a ``generic`` with a pointer). A drag's
    element is judged as a click on it would be; the element it drops onto is more often a list or a region than a
    button, and its name says what the drop does, so it is judged by its name, and as a field that takes typed text.
    Enter or a line break in a text field is judged by the buttons ``near`` it, which it may press
    (:func:`submits_field`), and by the field's own name."""
    reasons: list[str] = []
    if step.action in _TYPING_ACTIONS:
        reasons = typing_reasons(element, step.text)
        # Enter or Space on a button presses it: judged by its name, as a click on it is (the review of item 561).
        if _activates(step):
            reasons = list(dict.fromkeys(reasons + naming_reasons(element)))
        if submits_field(step, element):
            pressed: list[str] = [reason for button in near for reason in naming_reasons(button)]
            reasons = list(dict.fromkeys(reasons + naming_reasons(element) + pressed))
    elif step.action in _CLICKING_ACTIONS or step.action in ("check", "uncheck"):
        reasons = naming_reasons(element)
    elif step.action == "drag":
        # What is dragged is named for what it is, not for what the drag does: a control dragged is judged as a click.
        dragged: list[str] = clicking_reasons(element, _CONTROL_ROLES)
        reasons = list(dict.fromkeys(dragged + naming_reasons(target) + typing_reasons(target, None)))
    if step.action in _QUIET_ACTIONS and not reasons:
        return quiet_assessment()
    return assessment_for(reasons)


def unexplained_clicks(steps: Sequence[tuple[Step, Element | None]]) -> tuple[Element, ...]:
    """The controls the clicks of a batch land on whose names none of the patterns knows: the clicks the typed judge is
    asked about (:mod:`dotagent.agent.judge`), each control once."""
    return tuple(
        dict.fromkeys(
            element
            for step, element in steps
            if (step.action in _CLICKING_ACTIONS or step.action in ("check", "uncheck"))
            and element is not None
            and element.name
            and not naming_reasons(element)
        )
    )


def payment_steps(steps: Sequence[tuple[Step, Element | None, StepAssessment]], judged: Collection[Element]) -> int:
    """How many steps of a batch pay (item 561): a click, a check, a drag or a key that presses its control
    (:func:`_activates`) or Enter in a field that presses one near it (:func:`submits_field`, item 682), whose name
    asks to pay (``financial``), or that the typed judge found a payment (``judged``: the controls it found so). Typing
    into a card field is flagged as money too, but moves none by itself."""
    return sum(
        1
        for step, element, part in steps
        if (step.action not in _TYPING_ACTIONS or _activates(step) or submits_field(step, element))
        and ("financial" in part.flags or (element is not None and element in judged))
    )


def secret_kind(marking: object) -> SecretKind | None:
    """The secret a field takes, from what the page says about the field (``password`` for a password input, then its
    autocomplete tokens): a card secret when one is named, since money outranks the rest, so a masked card code is a
    card field; otherwise the first one named. ``None`` when it takes none."""
    if not isinstance(marking, str):
        return None
    kinds: list[SecretKind] = [_SECRET_KINDS[token] for token in marking.lower().split() if token in _SECRET_KINDS]
    return next((kind for kind in kinds if kind.startswith("cc-")), kinds[0] if kinds else None)


def hides_secrets(approvals: ApprovalMode) -> bool:
    """Whether a run's views of a page hide the values of the fields the page marks secret (item 467): in every mode
    but yolo, in which a run sees all it reads and types what a page or a mail gives it (item 443)."""
    return approvals != "yolo"


def secret_selector() -> str:
    """The CSS selector of the fields :func:`secret_kind` takes for secret (item 467), as the driver's field marking
    reads them: password inputs, and the inputs, text areas and dropdowns whose autocomplete names a secret among its
    tokens (``section-pay shipping cc-number`` too), whatever their case. A dropdown is one since a card's expiry month
    and year are often dropdowns (item 554)."""
    return ", ".join(
        [
            'input[type="password" i]',
            *(f'{tag}[autocomplete~="{kind}" i]' for kind in _SECRET_KINDS for tag in ("input", "textarea", "select")),
        ]
    )


def secret_field_label(kind: SecretKind) -> str:
    return f"a {kind} field"


def key_enters_text(key: str) -> bool:
    """Whether pressing ``key`` (Playwright's syntax: ``1``, ``Shift+A``, ``Digit1``, ``Control+V``) can put text into
    the focused field: a character key without Control or Meta, or a paste (Control+V, Meta+V, Shift+Insert)."""
    *modifiers, name = key.lower().split("+")
    held: set[str] = set(modifiers)
    if held & _SHORTCUT_MODIFIERS:
        return name == "v"
    # Shift+Insert pastes, as Control+V does (item 533).
    if name == "insert" and "shift" in held:
        return True
    return name not in _KEYS_WITHOUT_TEXT and _FUNCTION_KEY.match(name) is None


def secret_field_flag(kind: SecretKind) -> Flag:
    """A card field is about money; every other secret is a credential."""
    return "financial" if kind.startswith("cc-") else "credential"


async def refuse_secret_field(field: SecretField) -> None:
    """The secret-field check of every mode but yolo: no text goes into a field that takes a secret."""
    raise BrowserError(
        f"refusing to type into {secret_field_label(field.kind)}: ask the user to enter it themselves",
        {"action": "type", "ref": field.ref, "url": field.url},
    )


def dialog_is_financial(message: str) -> bool:
    """A confirm() that asks to pay or purchase must never be accepted on the model's behalf."""
    return asks_to_pay(message)


def safe_download_name(suggested: str) -> str:
    """A file name that stays inside the downloads folder: the leaf only, no hidden files, no odd characters."""
    leaf: str = suggested.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned: str = _UNSAFE_FILE_CHARS.sub("_", leaf).lstrip(".").strip()
    return cleaned[:_MAX_FILE_NAME_CHARS] or "download"

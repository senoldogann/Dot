"""Pure browser safety rules: which URLs may be opened and how much damage a step can do.

The page is untrusted: an element's name decides how a click or a keystroke is classified, and the policy engine
then decides whether the user must be asked, whether the step is refused, or whether it is handed to the user.
"""

import ipaddress
import re
import socket
from collections.abc import Sequence
from typing import get_args
from urllib.parse import unquote, urlsplit

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
from dotagent.policy.types import Flag

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
# Addresses a page makes itself, whose data never leaves the page: no server answers them.
_PAGE_OWN_SCHEMES: frozenset[str] = frozenset({"blob", "data"})
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}
_LOOPBACK_NAME: str = "localhost"
_MAX_URL_CHARS: int = 2000
_SCHEME: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_HOST_LIKE: re.Pattern[str] = re.compile(r"^(localhost|[\w-]+(\.[\w-]+)+|\d{1,3}(\.\d{1,3}){3})(:\d+)?([/?#].*)?$")
_UNSAFE_FILE_CHARS: re.Pattern[str] = re.compile(r"[^\w.\- ()]+")
_MAX_FILE_NAME_CHARS: int = 120
_TYPING_ACTIONS: frozenset[StepAction] = frozenset({"fill", "type", "press", "select", "insert"})
_CLICKING_ACTIONS: frozenset[StepAction] = frozenset({"click", "double_click", "right_click", "click_at"})
_QUIET_ACTIONS: frozenset[StepAction] = frozenset({"hover", "scroll_into_view"})
_CLICKABLE_ROLES: frozenset[str] = frozenset({"button", "link", "menuitem", "tab", "option", "checkbox", "switch"})
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
    """Whether ``host`` is one of ``entries`` or a subdomain of one."""
    lowered: str = host.lower().rstrip(".")
    return any(lowered == entry or lowered.endswith(f".{entry}") for entry in (e.lower().lstrip(".") for e in entries))


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


def check_browser_url(
    url: str, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str], server: ServerConfig
) -> UrlVerdict:
    """What the browser may open as a page: no :func:`forbidden_target`, and a host of ``allowed_hosts`` when that is
    set."""
    verdict: UrlVerdict = forbidden_target(url, blocked_hosts, server)
    if not verdict.allowed or url == "about:blank" or not allowed_hosts:
        return verdict
    host: str = urlsplit(url).hostname or ""
    if not host_matches(host, allowed_hosts):
        return UrlVerdict(allowed=False, reason=f"host {host!r} is not in browser.allowed_hosts")
    return verdict


def sign_in_return(url: str, server: ServerConfig) -> bool:
    """Whether ``url`` is the way back of a sign-in to Dot (item 445): the daemon's ``/oauth/callback`` (exactly, as
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
    """Role and accessible name from a snapshot line, or ``None`` when the line is not an element."""
    parsed: tuple[SnapshotLine, ...] = parse_snapshot(line)
    if not parsed or not parsed[0].role:
        return None
    return Element(role=parsed[0].role, name=parsed[0].name)


def assess_step(step: Step, element: Element | None, target: Element | None) -> StepAssessment:
    """Risk and flags of one step, judged from the action and the element it targets. A drag's element is judged as a
    click on it would be; the element it drops onto is more often a list or a region than a button, and its name says
    what the drop does, so it is judged by its name whatever its role, and as a field that takes typed text."""
    reasons: list[str] = []
    if step.action in _TYPING_ACTIONS:
        reasons = typing_reasons(element, step.text)
    elif step.action in _CLICKING_ACTIONS or step.action in ("check", "uncheck"):
        reasons = clicking_reasons(element, _CLICKABLE_ROLES)
    elif step.action == "drag":
        reasons = list(
            dict.fromkeys(
                clicking_reasons(element, _CLICKABLE_ROLES) + naming_reasons(target) + typing_reasons(target, None)
            )
        )
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
            and element.role in _CLICKABLE_ROLES
            and not clicking_reasons(element, _CLICKABLE_ROLES)
        )
    )


def secret_kind(marking: object) -> SecretKind | None:
    """The secret a field takes, from what the page says about the field (``password`` for a password input, then its
    autocomplete tokens): a card secret when one is named, since money outranks the rest, so a masked card code is a
    card field; otherwise the first one named. ``None`` when it takes none."""
    if not isinstance(marking, str):
        return None
    kinds: list[SecretKind] = [_SECRET_KINDS[token] for token in marking.lower().split() if token in _SECRET_KINDS]
    return next((kind for kind in kinds if kind.startswith("cc-")), kinds[0] if kinds else None)


def secret_field_label(kind: SecretKind) -> str:
    return f"a {kind} field"


def key_enters_text(key: str) -> bool:
    """Whether pressing ``key`` (Playwright's syntax: ``1``, ``Shift+A``, ``Digit1``, ``Control+V``) can put text into
    the focused field: a character key without Control or Meta, or a paste."""
    *modifiers, name = key.lower().split("+")
    held: set[str] = set(modifiers)
    if held & _SHORTCUT_MODIFIERS:
        return name == "v"
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

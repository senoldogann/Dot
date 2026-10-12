"""Pure browser safety rules: which URLs may be opened and how much damage a step can do.

The page is untrusted: an element's name decides how a click or a keystroke is classified, and the policy engine
then decides whether the user must be asked, whether the step is refused, or whether it is handed to the user.
"""

import ipaddress
import re
import socket
from collections.abc import Sequence
from typing import get_args
from urllib.parse import urlsplit

from dotagent.browser.snapshot import SnapshotLine, parse_snapshot
from dotagent.browser.types import SecretField, SecretKind, Step, StepAction
from dotagent.config import ServerConfig
from dotagent.core.errors import BrowserError
from dotagent.core.types import FrozenModel
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


def check_url(url: str, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str]) -> UrlVerdict:
    """Whether the browser may open ``url``: web pages only, never a blocked host, only allowed hosts when set."""
    if url == "about:blank":
        return UrlVerdict(allowed=True, reason="blank page")
    try:
        parts = urlsplit(url)
        host: str | None = parts.hostname
    except ValueError as exc:
        return UrlVerdict(allowed=False, reason=f"malformed URL ({exc})")
    if parts.scheme not in _ALLOWED_SCHEMES:
        return UrlVerdict(allowed=False, reason=f"scheme {parts.scheme!r} is not allowed; only http and https open")
    if not host:
        return UrlVerdict(allowed=False, reason="the URL has no host")
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


def check_browser_url(
    url: str, allowed_hosts: Sequence[str], blocked_hosts: Sequence[str], server: ServerConfig
) -> UrlVerdict:
    """What :func:`check_url` allows, save Dot's own interface: this Mac on the daemon's port, or a name of ``[server]
    reach``. The owner's Chrome may be signed in there, and a run that opened it could answer its own questions."""
    verdict: UrlVerdict = check_url(url, allowed_hosts, blocked_hosts)
    if not verdict.allowed or url == "about:blank":
        return verdict
    parts = urlsplit(url)
    try:
        port: int | None = parts.port
    except ValueError as exc:
        return UrlVerdict(allowed=False, reason=f"malformed URL ({exc})")
    host: str = parts.hostname or ""
    reached: int = port if port is not None else _DEFAULT_PORTS[parts.scheme]
    if host_matches(host, server.reach) or (reached == server.port and _is_loopback(host)):
        return UrlVerdict(allowed=False, reason="it is Dot's own interface, which a run never opens")
    return verdict


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
    """The secret-field check of the ask and auto modes: no text goes into a field that takes a secret."""
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

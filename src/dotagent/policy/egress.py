"""What a step sends out of the Mac, and whether it carries a secret there (item 525).

A run that read content third parties can author may have been told by it to send a secret away. Such a step is
refused in every approval mode, yolo too, whatever its preset or the owner's rules say: no task the owner gives needs
a run that read a stranger's words to send their keys anywhere, and the owner can do that step themselves.

:func:`secret_to_unknown_host` says whether those words name a host Dot does not already send keys to. The gate
refuses a carried secret only once the run has read outside content, and the refusal says the step would send a
secret out of the Mac, whichever host it names. A run that read nothing of the sort may still send a key where it
was told.
"""

import ipaddress
import re
from collections.abc import Iterator, Sequence
from urllib.parse import urlsplit

from pydantic import JsonValue

from dotagent.core.text import KNOWN_SECRET_MIN_CHARS
from dotagent.core.types import JsonObject
from dotagent.policy.elements import looks_like_key
from dotagent.policy.types import ActionInfo, ActionKind, Flag

# Hosts Dot's own clients already send the owner's keys to, and the clouds a key of that shape is for. A subdomain of
# one counts. The loopback is always one, so a test and a service on this Mac are not an unknown host. The owner's
# ``[browser] allowed_hosts`` are added by the gate when that list is set; an empty list does not mean every host.
KEY_HOSTS: tuple[str, ...] = (
    "localhost",
    "github.com",
    "githubusercontent.com",
    "openai.com",
    "anthropic.com",
    "openrouter.ai",
    "opencode.ai",
    "ollama.com",
    "coinbase.com",
    "search.brave.com",
    "tavily.com",
    "exa.ai",
    "google.serper.dev",
    "googleapis.com",
    "accounts.google.com",
    "microsoftonline.com",
    "microsoft.com",
    "apple.com",
    "typesafe.ai",
    "amazonaws.com",
    "core.windows.net",
)
_URL: re.Pattern[str] = re.compile(r"https?://[^\s<>'\"`]+", re.IGNORECASE)

# The kinds of step that hand their words to someone beyond the Mac: a request, a page's address or field, a message,
# an MCP server's tool, whose server may be anywhere, and what runs code or types into an application (a command line,
# an AppleScript, keystrokes, a script in a page: :data:`_SENDING_TOOLS`), which may reach the network whatever flags
# its words raised: a plain `curl` of an address that holds a secret raises none.
_SENDING_KINDS: frozenset[ActionKind] = frozenset(
    {
        "network",
        "browser_navigate",
        "browser_input",
        "message",
        "mcp",
        "mcp_read",
        "shell",
        "app_control",
        "desktop_input",
    }
)
_SENDING_FLAGS: frozenset[Flag] = frozenset(
    {"external_send", "message_send", "upload", "public_post", "social", "exfiltration"}
)
# A tool whose words are code that runs in a page: a script may fetch any address as a command line may, yet it is
# classified as a click of medium risk, since no pattern tells what its text does (item 263). Its kind is not added to
# the list above: a plain click's summary and target (a label, the page's address) would raise false refusals (item
# 561).
_SENDING_TOOLS: frozenset[str] = frozenset({"browser_eval"})


def sends_out(info: ActionInfo) -> bool:
    """Whether the step hands what its arguments say to someone beyond the Mac."""
    return (
        info.kind in _SENDING_KINDS or info.tool in _SENDING_TOOLS or any(flag in _SENDING_FLAGS for flag in info.flags)
    )


def _texts(value: JsonValue) -> Iterator[str]:
    """Every string in ``value``, its keys too, as the tool reads them (not escaped as JSON)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _texts(item)


def carries_secret(info: ActionInfo, arguments: JsonObject, known: Sequence[str]) -> bool:
    """Whether a step that sends out (:func:`sends_out`) carries a secret in its words (its target, its summary and
    every string of the call's ``arguments``): one of ``known``, the secrets Dot holds
    (:func:`dotagent.secrets.known_secrets`), word for word, or text in the form of a key or a private key. A command
    that reads a secret from a file or a variable as it runs carries none in its words."""
    if not sends_out(info):
        return False
    text: str = "\n".join((info.target, info.summary, *_texts(arguments)))
    if looks_like_key(text):
        return True
    return any(value in text for value in known if len(value) >= KNOWN_SECRET_MIN_CHARS)


def _host_of(url: str) -> str | None:
    """The host ``url`` names, lower case, or ``None`` when it names none."""
    try:
        host: str | None = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower().rstrip(".")


def _loopback(host: str) -> bool:
    """Whether ``host`` is this machine: ``localhost`` or a loopback address."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _known_host(host: str, known_hosts: Sequence[str]) -> bool:
    """Whether ``host`` is the loopback or one of ``known_hosts`` or a subdomain of one."""
    if _loopback(host):
        return True
    for entry in known_hosts:
        item: str = entry.lower().strip().strip(".").rstrip(".")
        if item and (host == item or host.endswith(f".{item}")):
            return True
    return False


def _destination_hosts(info: ActionInfo, arguments: JsonObject) -> tuple[str, ...]:
    """The hosts the step's words name in an ``http`` or ``https`` address: its target, its summary, its body and
    every string of ``arguments``."""
    text: str = "\n".join((info.target, info.summary, info.body, *_texts(arguments)))
    hosts: list[str] = []
    for raw in _URL.findall(text):
        host: str | None = _host_of(raw.rstrip(".,;)]}"))
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def secret_to_unknown_host(
    info: ActionInfo, arguments: JsonObject, known: Sequence[str], known_hosts: Sequence[str]
) -> bool:
    """Whether a step that sends out and carries a secret (:func:`carries_secret`) names a host that is not the
    loopback and not one of ``known_hosts`` (nor a subdomain of one). A step that names no host is not one: a token
    on a ``gh`` line has nowhere in its words to refuse. One that names a known host and an unknown one is."""
    if not carries_secret(info, arguments, known):
        return False
    hosts: tuple[str, ...] = _destination_hosts(info, arguments)
    return bool(hosts) and any(not _known_host(host, known_hosts) for host in hosts)

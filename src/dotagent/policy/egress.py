"""What a step sends out of the Mac, and whether it carries a secret there (item 525).

A run that read content third parties can author may have been told by it to send a secret away. Such a step is
refused in every approval mode, yolo too, whatever its preset or the owner's rules say: no task the owner gives needs
a run that read a stranger's words to send their keys anywhere, and the owner can do that step themselves.
"""

from collections.abc import Iterator, Sequence

from pydantic import JsonValue

from dotagent.core.text import KNOWN_SECRET_MIN_CHARS
from dotagent.core.types import JsonObject
from dotagent.policy.elements import looks_like_key
from dotagent.policy.types import ActionInfo, ActionKind, Flag

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

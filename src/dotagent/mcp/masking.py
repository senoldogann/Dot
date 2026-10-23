"""What of an MCP server's address, arguments and failure is shown or logged (item 558). The owner may put a key in the
path or the query of a URL, or in an argument of the command, and a server may print its token as it fails to start:
the Connectors page, the status and the log say where a server is and why it failed without them."""

import re
import shlex
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import JsonValue

from dotagent.core.text import redact_known
from dotagent.secrets import known_secrets

# A segment of a URL's path that reads as a key rather than a word: 20 characters or more of those keys are written in,
# with a digit among them (``/mcp`` and ``/streamable-http`` are words, ``/k3Yx9...`` is a key).
_KEY_SEGMENT: re.Pattern[str] = re.compile(r"(?=[A-Za-z0-9_.~-]*\d)[A-Za-z0-9_.~-]{20,}")


def shown_url(url: str) -> str:
    """``url`` as it may be shown: without the user and password, the query and the fragment, and with the segments of
    its path that read as a key masked."""
    parts = urlsplit(url)
    path: str = "/".join("***" if _KEY_SEGMENT.fullmatch(segment) else segment for segment in parts.path.split("/"))
    shown: str = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{path}"
    return f"{shown}?…" if parts.query else shown


def shown_command(command: str, arguments: list[str]) -> str:
    """A command line as it may be shown: an option that names a secret (``--api-key sk-...``) and a key of a known
    form are masked."""
    return redact_known(shlex.join([command, *arguments]), known_secrets())


def _hidden(value: JsonValue, url: str | None, known: tuple[str, ...]) -> JsonValue:
    """``value`` with its strings masked: the server's own address as :func:`shown_url` shows it wherever it is
    repeated, then the secrets Dot holds and those that have a form of their own."""
    if isinstance(value, str):
        return redact_known(value if url is None else value.replace(url, shown_url(url)), known)
    if isinstance(value, list):
        return [_hidden(item, url, known) for item in value]
    if isinstance(value, dict):
        return {key: _hidden(item, url, known) for key, item in value.items()}
    return value


def masked_context(context: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """What an error of an MCP server tells (its address and arguments, the causes httpx gives with the whole URL, the
    tail of what the server printed), with a key in any of them masked."""
    known: tuple[str, ...] = known_secrets()
    address: JsonValue = context.get("url")
    url: str | None = address if isinstance(address, str) else None
    arguments: JsonValue = context.get("args")
    masked: dict[str, JsonValue] = {}
    for key, value in context.items():
        if key == "url" and url is not None:
            masked[key] = shown_url(url)
        elif key == "args" and isinstance(arguments, list) and all(isinstance(arg, str) for arg in arguments):
            # An option and its value are told apart by the text they make together: --api-key sk-...
            words: str = redact_known(shlex.join(str(arg) for arg in arguments), known)
            try:
                masked[key] = list(shlex.split(words))
            except ValueError:
                masked[key] = [words]
        else:
            masked[key] = _hidden(value, url, known)
    return masked

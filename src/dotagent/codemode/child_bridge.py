"""The code's one way out of the sandbox (item 1500): a call over two pipes the daemon handed it.

Standard library only: the daemon copies this file into the run's tools folder as ``dot_tools/_bridge.py``. A call
writes one line of JSON to the request pipe, ``{"id", "tool", "args"}``, and reads one line from the reply pipe,
``{"id", "ok", ...}``: ``text`` and ``images`` (the paths of pictures the daemon saved in the scratch folder) when the
tool answered, ``error`` and ``refused`` when it did not. What the code prints never goes there: it is the
process's own output.

The daemon holds no trust in what comes down the request pipe: a frame that is not exactly that line ends the code. The
code can call only what the daemon lets through, and each call is judged by the run's own gate, as a call of the model's
is; nothing here decides anything.
"""

import datetime
import json
import os
import threading
from typing import Any, TextIO


class ToolError(Exception):
    """A call that failed: the tool's own words, or the daemon's when the call could not be made."""


class ToolRefused(ToolError):
    """A call the run's rules, its approvals or the owner refused: the message is the gate's words. Do not try it again
    another way: it is what the run was told not to do."""


class BridgeError(Exception):
    """The bridge itself broke: the daemon is gone or said something that is not a reply. The code cannot go on."""


_lock: threading.Lock = threading.Lock()
_requests: TextIO | None = None
_replies: TextIO | None = None
_sent: int = 0
# The most a line of the bridge may be, a little under what the daemon reads (32 MiB), so that a call too large to send
# is the code's own error and not the end of the code.
_MOST_BYTES: int = 32 * 1024 * 1024 - 4096


def attach(request_fd: int, reply_fd: int) -> None:
    """Take the two descriptors the daemon handed over."""
    global _requests, _replies
    _requests = os.fdopen(request_fd, "w", encoding="utf-8", newline="\n")
    _replies = os.fdopen(reply_fd, "r", encoding="utf-8", newline="\n")


def _parsed(text: str) -> object:
    """What a tool said as the code gets it: the JSON it holds when it is an object or a list, else the text."""
    stripped: str = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(stripped)
        except (ValueError, RecursionError):
            return text
    return text


def _plain(value: object) -> object:
    """What a value the code gives a tool that is not JSON is sent as: a path as its text, a date as its ISO form, a
    set as a sorted list. Anything else cannot be sent, and the call says so."""
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value, key=repr)
    raise TypeError(f"{type(value).__name__} cannot be sent to a tool")


def _frame(number: int, tool: str, arguments: dict[str, Any]) -> str:
    """The line of call ``number``, or a :class:`ToolError` that says why the arguments cannot be sent: the call was not
    made, and the numbering of the calls is as it was."""
    try:
        text: str = json.dumps(
            {"id": number, "tool": tool, "args": arguments},
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_plain,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ToolError(f"the arguments of {tool} cannot be sent: {error}") from None
    if len(text) > _MOST_BYTES:
        raise ToolError(f"the arguments of {tool} are {len(text)} bytes: the most one call can carry is {_MOST_BYTES}")
    return text


def call(tool: str, arguments: dict[str, Any]) -> object:
    """Make one call of Dot's tool ``tool`` and return what it answered. Calls of a thread go one at a time."""
    global _sent
    if _requests is None or _replies is None:
        raise BridgeError("the bridge is not attached")
    with _lock:
        # Numbered only once the line exists: a call that is not made does not take a number the daemon waits for.
        frame: str = _frame(_sent + 1, tool, arguments)
        _sent += 1
        number: int = _sent
        try:
            _requests.write(frame + "\n")
            _requests.flush()
            line: str = _replies.readline()
        except (OSError, ValueError) as error:
            raise BridgeError(f"the bridge closed: {error}") from None
    if not line.endswith("\n"):
        raise BridgeError("the daemon closed the bridge: the run was stopped, or this code ran out of its time")
    try:
        reply: Any = json.loads(line)
    except ValueError:
        raise BridgeError("the daemon sent a line that is not JSON") from None
    if not isinstance(reply, dict) or reply.get("id") != number or not isinstance(reply.get("ok"), bool):
        raise BridgeError("the daemon sent a reply that is not the answer to this call")
    if reply["ok"]:
        text: Any = reply.get("text")
        pictures: Any = reply.get("images", [])
        if not isinstance(text, str) or not isinstance(pictures, list):
            raise BridgeError("the daemon sent an answer without its text")
        if pictures:
            return text + "".join(f"\n[image saved: {path}]" for path in pictures)
        return _parsed(text)
    words: Any = reply.get("error")
    message: str = words if isinstance(words, str) else "the call failed"
    raise (ToolRefused if reply.get("refused") is True else ToolError)(message)

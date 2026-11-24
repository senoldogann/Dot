"""The daemon's end of the bridge (item 1500): the frames of the code's calls and the replies it gets.

What comes down the request pipe is text a program wrote, and a program a model wrote and a page may have shaped: it is
read as exactly one thing, a line of JSON that is an object with the keys ``id`` (the number of the call, one more than
the one before), ``tool`` (a name) and ``args`` (an object), and nothing else. A line longer than
:data:`MAX_FRAME_BYTES`, one that is no JSON, one with another key, a key of another type or a number out of its turn
is a *malformed frame*: it ends the code (:class:`MalformedFrame`), whatever it was after. Nothing here decides what a
call may do: the gate of the run does, call by call.
"""

import json
from dataclasses import dataclass

from pydantic import Field, ValidationError

from dotagent.core.types import FrozenModel, JsonObject

# The most one line of the bridge may be, both ways: a request carries a call's arguments (a file written whole), a
# reply a tool's whole result, which code mode does not cut to the size of a model's result.
MAX_FRAME_BYTES: int = 32 * 1024 * 1024
MAX_TOOL_NAME_CHARS: int = 200


class MalformedFrame(Exception):
    """A line of the request pipe that is not a call as the bridge defines one: ``reason`` says what is wrong."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason: str = reason


class BridgeRequest(FrozenModel):
    """One call of the code: its number, the tool it names and the arguments it gives."""

    id: int
    tool: str = Field(min_length=1, max_length=MAX_TOOL_NAME_CHARS)
    args: JsonObject


@dataclass(frozen=True)
class BridgeAnswer:
    """What a call came to, as the reply tells it: a tool's ``text`` and the ``images`` it gave (paths of files in the
    scratch folder), or an ``error``, which ``refused`` says the run's rules, its approvals or the owner gave."""

    ok: bool
    text: str = ""
    images: tuple[str, ...] = ()
    error: str = ""
    refused: bool = False


def read_request(line: bytes, expected: int) -> BridgeRequest:
    """The call that ``line`` holds, which must be call number ``expected``; a line that is not exactly that raises
    :class:`MalformedFrame`."""
    if not line.endswith(b"\n"):
        raise MalformedFrame("a frame that does not end with a newline")
    if len(line) > MAX_FRAME_BYTES:
        raise MalformedFrame(f"a frame of more than {MAX_FRAME_BYTES} bytes")
    try:
        request: BridgeRequest = BridgeRequest.model_validate_json(line, strict=True)
    except ValidationError as error:
        first = error.errors()[0]
        where: str = ".".join(str(part) for part in first["loc"]) or "the frame"
        raise MalformedFrame(f"a frame that is not a call ({where}: {first['msg']})") from None
    if request.id != expected:
        raise MalformedFrame(f"a call numbered {request.id} where {expected} was next")
    return request


def reply_frame(number: int, answer: BridgeAnswer) -> bytes:
    """The line the code reads as the answer to call ``number``: ASCII only, so no character of a result can end it."""
    body: dict[str, object] = (
        {"id": number, "ok": True, "text": answer.text, "images": list(answer.images)}
        if answer.ok
        else {"id": number, "ok": False, "error": answer.error, "refused": answer.refused}
    )
    return (json.dumps(body, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")

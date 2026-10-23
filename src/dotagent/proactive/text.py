"""Words that go into the instructions of a run or into a question to a model, from what Dot keeps and from what others
wrote: one line each, masked, cut short and quoted, so that no line can pass for an instruction or close the fence it
stands in."""

import json

from dotagent.core.text import redact_secrets


def one_line(text: str, limit: int) -> str:
    """``text`` with its secrets masked, as one line of at most ``limit`` characters (cut with an ellipsis)."""
    flat: str = " ".join(redact_secrets(text).split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def quoted(text: str, limit: int) -> str:
    """``text`` as :func:`one_line` makes it, written as a JSON string whose ``<`` is escaped: a line of another's
    writing is shown as a quotation, and none of it can close a fence such as ``</dot_state>``."""
    return json.dumps(one_line(text, limit), ensure_ascii=False).replace("<", "\\u003c")

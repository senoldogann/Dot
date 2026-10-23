"""Telling the owner something Dot has to say on its own (items 570 and 571): the morning brief and the watchers both do
it through :func:`tell_owner`, and nothing else in Dot tells the owner a thing that happened by a way of its own.

A tell is an event of Dot itself (``owner_told``, live only, like the other notices of ``notify/push.py``), so every
way to reach the owner listens to the one bus: the web push sends it to the phone (``notice_message`` of
``notify/push.py``), the web UI shows it as a desktop notification, and the router of the channels sends it into the
owner's messaging apps (``channels/router.py``, item 582). A way added later listens for the same event and needs no
change to the callers."""

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.events import SYSTEM_RUN_ID, EventBus

# The live event of Dot itself that carries one tell.
OWNER_TOLD_EVENT: str = "owner_told"
# What a tell may hold: a title, a few lines of text (a phone shows far less), and the route inside Dot's pages that a
# tap opens.
TITLE_CHARS: int = 80
TEXT_CHARS: int = 400
ROUTE_PREFIX: str = "#/"
_ROUTE_CHARS: int = 200


class OwnerTold(FrozenModel):
    """What ``owner_told`` carries: the title and the text of the tell, and the route (``#/runs/<id>``) that opens what
    it is about."""

    title: str
    text: str
    url: str


def _clipped(text: str, limit: int) -> str:
    """``text`` as one line of at most ``limit`` characters: what a third party wrote cannot fake a second line."""
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def told(title: str, text: str, url: str) -> OwnerTold:
    """The tell for ``title``, ``text`` and ``url``, as it is sent. ``url`` is a route inside Dot's own pages: a tell
    that opened any other address would let whoever wrote the words of one send the owner somewhere else."""
    if not url.startswith(ROUTE_PREFIX) or len(url) > _ROUTE_CHARS or any(char.isspace() for char in url):
        raise InvalidValueError(
            "a tell opens a route of Dot's own pages (#/runs/<id>)", {"url": _clipped(url, _ROUTE_CHARS)}
        )
    return OwnerTold(title=_clipped(title, TITLE_CHARS), text=_clipped(text, TEXT_CHARS), url=url)


async def tell_owner(bus: EventBus, title: str, text: str, url: str) -> None:
    """Tell the owner: ``title`` and ``text`` where they are told (their phone, their desktop), and ``url``, the route
    of what it is about, opened by a tap. Raises :class:`InvalidValueError` for a route that is not one of Dot's
    pages."""
    await bus.emit(SYSTEM_RUN_ID, OWNER_TOLD_EVENT, told(title, text, url).model_dump(), False)

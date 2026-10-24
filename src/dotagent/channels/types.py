"""What the channels, their router and their service share: the names of the channels and what a channel is."""

from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from dotagent.channels.pictures import Picture

# The channels there are; a channel added later is named here and in TITLES.
type ChannelId = Literal["imessage"]

# The name of each channel as the owner reads it: also the title of its chat in Dot.
TITLES: dict[ChannelId, str] = {"imessage": "iMessage"}

# The message that stops the run at work in the channel's chat. The router runs it, and a channel runs it even when it
# cannot tell who wrote it: it only reduces what Dot does.
STOP_COMMAND: str = "/stop"
# The plain words that stop it as well (item 611), the owner writing in English or in Turkish. Few and exact: a message
# that is only one of them, in any case, but not a sentence that holds one.
STOP_WORDS: frozenset[str] = frozenset({STOP_COMMAND, "stop", "dur", "durdur"})


def is_stop(text: str) -> bool:
    """Whether ``text`` is a message that only says stop: ``/stop``, ``stop``, ``dur`` or ``durdur``, in any case and
    with a closing dot or exclamation mark. A sentence that holds one of them (``please stop``, ``stop it``) is not."""
    return text.strip().rstrip(".!").strip().casefold() in STOP_WORDS


# What a channel calls with a message of the owner it accepted (see :class:`~dotagent.channels.intake.Intake`): the
# router posts it into the channel's chat.
type Receive = Callable[[str], Awaitable[None]]


class Channel(Protocol):
    """Something that receives the owner's messages and sends Dot's."""

    @property
    def id(self) -> ChannelId: ...

    @property
    def limit(self) -> int:
        """The most characters of one message Dot sends on the channel, its own marks included."""
        ...

    def paired(self) -> bool:
        """Whether the channel knows who to answer: the owner's handle, the owner's chat."""
        ...

    async def run(self, receive: Receive) -> None:
        """Take the owner's messages and call ``receive`` with each one that may be a command, until cancelled."""
        ...

    async def send(self, text: str) -> None:
        """Send ``text`` to the owner. Raises :class:`~dotagent.core.errors.ChannelError` when it did not go."""
        ...

    async def send_picture(self, picture: Picture) -> None:
        """Send ``picture``, a file :class:`~dotagent.channels.pictures.Pictures` judged may go, to the owner as a
        message of its own. Raises :class:`~dotagent.core.errors.ChannelError` when it did not go (item 617)."""
        ...

    async def start_from_now(self) -> None:
        """Forget what came in while the channel was off: what arrives after this is the first it reads."""
        ...

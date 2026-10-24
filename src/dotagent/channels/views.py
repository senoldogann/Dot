"""What Settings › Channels shows and sends (item 572): where each channel stands, and the requests that pair, switch
and remove."""

from typing import Literal

from pydantic import Field, StrictBool

from dotagent.channels.imessage import Access, Needs, Verifying
from dotagent.channels.seen import Seen
from dotagent.connections.mac import AutomationState
from dotagent.core.types import FrozenModel

# What macOS says of Dot's right to control Messages, or that it was not asked (the Messages app is not running, or
# macOS did not answer).
type AutomationAnswer = AutomationState | Literal["unknown"]


class ImessageView(FrozenModel):
    """The iMessage channel: whether it is ``on``, the ``handles`` paired, a code that waits to be typed back
    (``verifying``), whether the program that runs Dot may read Messages' database (``database``) and control Messages
    (``automation``), what keeps the channel from working now (``problem``, and ``needs``, the permission it is for),
    the newest run of its chat (``chat_run_id``, which the page links to), the name of the program macOS holds
    responsible for Dot (``program``), which is what to switch on in Privacy & Security, what the channel sees of
    Messages' database (``seen``, item 595): the newest row, the cursor, the two files, the last command and error, and
    the address the links of its messages open at (``links``, item 611), ``None`` when ``[server] reach`` names none and
    no link is sent."""

    on: bool
    handles: tuple[str, ...]
    verifying: Verifying | None
    database: Access
    automation: AutomationAnswer
    problem: str | None
    needs: Needs | None
    chat_run_id: str | None
    program: str | None
    seen: Seen
    links: str | None


class ChannelsView(FrozenModel):
    """Every channel, and whether a run started from one tells what it does while it works (``progress``, item 618)."""

    imessage: ImessageView
    progress: bool


class SwitchRequest(FrozenModel):
    """Switch a channel on or off."""

    on: StrictBool


class HandleRequest(FrozenModel):
    """A phone number (with its country code) or an Apple ID address, as the owner typed it."""

    handle: str = Field(min_length=1, max_length=120)


class CodeRequest(FrozenModel):
    """The code the owner read on their iPhone, as they typed it."""

    code: str = Field(min_length=1, max_length=24)

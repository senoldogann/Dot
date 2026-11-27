"""What the page script of the owner's Safari answers (item 1959), as models: the page is untrusted, so its answer is
validated here before Dot reads a field of it."""

from pydantic import BaseModel, ConfigDict, Field

# The longest a name, a value or an option the page gives is kept: past it, the rest is the page's to say again.
_NAME_CHARS: int = 160
_VALUE_CHARS: int = 2000


class _PageModel(BaseModel):
    """A part of the page's answer: extra keys a page could slip in are ignored, and nothing of it is changed."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class PageElement(_PageModel):
    """One element of a page the run can act on, or a heading: its ref, role and name as the page states them, the
    tag it is, what marks a field as a secret one (``password`` for a password input, else its autocomplete tokens),
    and its state."""

    ref: str = Field(pattern=r"^s\d{1,9}$")
    role: str = Field(max_length=40)
    name: str = Field(max_length=_NAME_CHARS)
    tag: str = Field(max_length=40)
    mark: str = Field(max_length=200)
    value: str | None = None
    checked: bool | None = None
    disabled: bool = False
    focused: bool = False
    level: int | None = None
    options: tuple[str, ...] = ()
    # The refs of the buttons Enter in this field may press: its form's, else those of its nearest small ancestors.
    near: tuple[str, ...] = ()


class PageRead(_PageModel):
    """A read of the tab: its address and title, whether it finished loading, its elements in document order
    (``more`` past the read's limit), and the start of its visible text with the length of the whole."""

    url: str
    title: str
    ready: str
    elements: tuple[PageElement, ...]
    more: int = Field(ge=0)
    text: str
    text_chars: int = Field(ge=0)


class StepAnswer(_PageModel):
    """What one step did in the page: done (sent), verified when the page shows its effect (the value is in the field,
    the box is ticked), and what happened in a few words."""

    done: bool
    verified: bool | None = None
    detail: str = Field(max_length=400)


class ReadReply(_PageModel):
    ok: bool
    page: PageRead | None = None
    error: str | None = None


class StepReply(_PageModel):
    ok: bool
    step: StepAnswer | None = None
    url: str | None = None
    error: str | None = None


class PlaceReply(_PageModel):
    """Where the tab is: its address, whether its document finished loading, and the start of its visible text."""

    ok: bool
    url: str = ""
    ready: str = ""
    text: str = ""
    error: str | None = None


class TabPin(_PageModel):
    """The tab a chat's Safari steps go to: Safari's id of its window and its place among the window's tabs, and the
    address Dot last saw in it. A step goes there only while the tab still shows that address, so that a tab the owner
    moved or opened elsewhere is never acted on with refs of another page."""

    window: int
    tab: int = Field(ge=1)
    url: str
    # Whether Dot opened the tab for the chat (safari_open), and may load another page in it; the owner's own tab,
    # where a read started, is never loaded over.
    opened: bool = False


def clipped(text: str, limit: int) -> str:
    """``text`` on one line, at most ``limit`` characters: what a page gave is shown so, never as lines of its own."""
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


VALUE_CHARS: int = _VALUE_CHARS

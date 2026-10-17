"""How the pages of Dot's browser look (item 354, after T3 Code's Settings › Browser): the size of a page of Dot's own
Chrome, which fills its window or has a size of its own, and the colour scheme every page is told to prefer."""

from typing import Literal

from pydantic import Field

from dotagent.config import PageAppearance
from dotagent.core.types import FrozenModel


class PageLook(FrozenModel):
    """``fit_window``: a page of Dot's own Chrome fills its window, else it is ``viewport_width`` by
    ``viewport_height`` (from a small phone's to a 4K screen's, as ``[browser]`` allows); ``appearance``: the colour
    scheme pages are told to prefer."""

    fit_window: bool
    viewport_width: int = Field(ge=320, le=3840)
    viewport_height: int = Field(ge=240, le=2160)
    appearance: PageAppearance


def color_scheme(appearance: PageAppearance) -> Literal["light", "dark", "null"]:
    """Playwright's word for ``appearance``: "null" tells the pages nothing, so they follow the Mac."""
    match appearance:
        case "system":
            return "null"
        case "light" | "dark":
            return appearance

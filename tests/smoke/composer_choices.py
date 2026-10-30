"""The run's choices in the box (item 833): the box is one line until the owner asks for them, and the "+" menu's
**Model and options** shows them on the bar under the words, which the browser keeps. A test that reads the pills, the
context meter or that bar shows them first, as the owner would, from a page's first load."""

from playwright.async_api import BrowserContext, Page

# What the "+" menu keeps once the choices are shown (composer.js `CHOICES_KEY`).
SHOWN: str = "localStorage.setItem('dot.composer.choices', 'shown')"


async def show_choices(target: BrowserContext | Page) -> None:
    """The run's choices shown in every box of `target`: every page of a context, or one page."""
    await target.add_init_script(SHOWN)

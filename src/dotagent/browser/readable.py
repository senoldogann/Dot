"""How many cookies Chrome reads from a profile of Dot's (items 529 and 917): the check that ends an import of the
sign-ins of the owner's Chrome, from the command line and from Settings › Browser alike. The cookies of an imported
profile are encrypted with the key Chrome keeps in the Keychain, so this is when macOS may ask whether Chrome may use
it."""

from pathlib import Path

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.config import BrowserConfig, ServerConfig
from dotagent.core.paths import downloads_dir


async def readable_cookies(config: BrowserConfig, server: ServerConfig, profile: Path) -> int:
    """Open the profile in a hidden Chrome of its own, whatever ``[browser] mode`` says, and count the cookies that
    Chrome can read. It takes no DevTools connections: the port is the daemon's Chrome's."""
    session: BrowserSession = BrowserSession(
        config.model_copy(update={"headless": True, "mode": "own", "debug_port": 0}),
        server,
        profile,
        downloads_dir(),
        no_runs_at_work,
    )
    try:
        return await session.stored_cookie_count()
    finally:
        await session.aclose()

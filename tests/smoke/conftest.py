"""What every smoke test shares: the ``opencode`` program Dot would start for Zen's free models (item 283) is one that
does not exist, so that no test starts the owner's OpenCode or reaches Zen by listing models; the live tests of
OpenCode point it back at the real one. Tailscale's command line and ``caffeinate`` (item 271) are ones that do not
exist too, so that no test reads or changes the owner's tailnet or keeps the Mac awake, and so is the ``open`` that
Settings › Phone opens the App Store or Tailscale with (item 421); the tests of Settings › Phone point them at
stand-ins."""

import os

import pytest

MISSING_OPENCODE: str = "/nonexistent/dot-tests/opencode"
MISSING_TAILSCALE: str = "/nonexistent/dot-tests/tailscale"
MISSING_CAFFEINATE: str = "/nonexistent/dot-tests/caffeinate"
MISSING_OPEN: str = "/nonexistent/dot-tests/open"
# The keychain the accounts of the official sign-ins keep their tokens in (item 422): one that does not exist, so that
# no test reads or writes the owner's login keychain through them; the tests of the sign-ins make one of their own.
MISSING_KEYCHAIN: str = "/nonexistent/dot-tests/accounts.keychain-db"


def pytest_configure(config: pytest.Config) -> None:
    os.environ["DOT_OPENCODE"] = MISSING_OPENCODE
    os.environ["DOT_TAILSCALE"] = MISSING_TAILSCALE
    os.environ["DOT_CAFFEINATE"] = MISSING_CAFFEINATE
    os.environ["DOT_OPEN"] = MISSING_OPEN
    os.environ["DOT_KEYCHAIN"] = MISSING_KEYCHAIN

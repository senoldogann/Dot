"""What every smoke test shares: the ``opencode`` program Dot would start for Zen's free models (item 283) is one that
does not exist, so that no test starts the owner's OpenCode or reaches Zen by listing models; the live tests of
OpenCode point it back at the real one. Tailscale's command line (item 271) is one that does not exist too, so that no
test reads or changes the owner's tailnet; the tests of Settings › Phone point it at a stand-in."""

import os

import pytest

MISSING_OPENCODE: str = "/nonexistent/dot-tests/opencode"
MISSING_TAILSCALE: str = "/nonexistent/dot-tests/tailscale"


def pytest_configure(config: pytest.Config) -> None:
    os.environ["DOT_OPENCODE"] = MISSING_OPENCODE
    os.environ["DOT_TAILSCALE"] = MISSING_TAILSCALE

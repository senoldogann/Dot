"""What every smoke test shares: the ``opencode`` program Dot would start for Zen's free models (item 283) is one that
does not exist, so that no test starts the owner's OpenCode or reaches Zen by listing models; the live tests of
OpenCode point it back at the real one."""

import os

import pytest

MISSING_OPENCODE: str = "/nonexistent/dot-tests/opencode"


def pytest_configure(config: pytest.Config) -> None:
    os.environ["DOT_OPENCODE"] = MISSING_OPENCODE

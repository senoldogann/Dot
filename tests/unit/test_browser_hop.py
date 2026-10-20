"""The answer to a request Chrome holds for a verdict (``BrowserSession._answer_hop``): Chrome refusing it with "Invalid
InterceptionId" means the page cancelled or finished the request first, which is ordinary traffic and no warning; any
other refusal is one, and a page that closed has nothing to say. The verdict itself is not touched."""

from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError
from structlog.testing import capture_logs

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.config import load_default_config


class _Cdp:
    """A CDP session that records what it was sent and answers with ``error``."""

    def __init__(self, error: PlaywrightError) -> None:
        self.error: PlaywrightError = error
        self.sent: list[str] = []

    async def send(self, method: str, params: dict[str, object]) -> None:
        self.sent.append(method)
        raise self.error


class _Page:
    def __init__(self, closed: bool) -> None:
        self.closed: bool = closed
        self.url: str = "https://example.com/"

    def is_closed(self) -> bool:
        return self.closed


def _warnings(logs: list[dict[str, object]]) -> list[str]:
    return [str(entry["event"]) for entry in logs if entry["log_level"] == "warning"]


async def _answer(tmp_path: Path, error: str, closed: bool, allowed: bool) -> tuple[list[str], list[str]]:
    config = load_default_config()
    session = BrowserSession(
        config.browser, config.server, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    cdp = _Cdp(PlaywrightError(error))
    with capture_logs() as logs:
        await session._answer_hop(cdp, _Page(closed), "id-1", "https://example.com/a.js", allowed)  # type: ignore[arg-type]
    return cdp.sent, _warnings(logs)


@pytest.mark.parametrize(
    ("allowed", "method"), [(True, "Fetch.continueRequest"), (False, "Fetch.failRequest")], ids=["let on", "failed"]
)
async def test_a_request_the_page_cancelled_first_is_no_warning(tmp_path: Path, allowed: bool, method: str) -> None:
    gone: str = f"CDPSession.send: Protocol error ({method}): Invalid InterceptionId."
    assert await _answer(tmp_path, gone, False, allowed) == ([method], [])


async def test_another_refusal_is_a_warning_and_a_closed_page_has_nothing_to_say(tmp_path: Path) -> None:
    other: str = "CDPSession.send: Protocol error (Fetch.continueRequest): Target closed"
    assert await _answer(tmp_path, other, False, True) == (["Fetch.continueRequest"], ["browser_hop_unanswered"])
    assert await _answer(tmp_path, other, True, True) == (["Fetch.continueRequest"], [])

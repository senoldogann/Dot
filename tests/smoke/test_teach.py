"""Teaching Dot a task end to end (item 423): a real Chrome on a page served here, the recorder the session puts into
it, the steps the owner's own clicks and typing make, a secret field's value left out of them, and the skill the review
model's scripted stand-in writes and the daemon saves.

The writer is a scripted one: no model service is called and no network is reached.
"""

import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import Browser, async_playwright

from dotagent import teach as teach_module
from dotagent.agent.playbooks import list_playbooks, retires
from dotagent.agent.review import PlaybookDraft
from dotagent.browser.teach import TEACH_BINDING, TEACH_SCRIPT, Recording
from dotagent.teach import draft_taught_skill, save_taught_skill

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PAGE: str = """<!doctype html><html><body>
<a href="/next">Invoices</a>
<label for="email">Email</label><input id="email" name="email">
<label for="password">Password</label><input id="password" name="password" type="password">
<label for="plan">Plan</label><select id="plan"><option>Monthly</option><option>Yearly</option></select>
<label for="search">Search</label><input id="search" name="search">
<iframe id="ads" src="/frame" title="an advert"></iframe>
</body></html>"""

_FRAME: str = """<!doctype html><html><body>
<a href="#" id="pay">Pay now</a>
</body></html>"""


def _headless_settings(tmp_path: Path) -> tuple[Path, Path]:
    """A config whose browser has no window, and the empty profile folder it runs on: kept out of the async test, where
    the writing of a file would block the loop."""
    settings: Path = tmp_path / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    profile: Path = tmp_path / "profile"
    profile.mkdir()
    return settings, profile


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = (_FRAME if self.path.startswith("/frame") else _PAGE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
async def browser() -> Iterator[Browser]:
    async with async_playwright() as playwright:
        found = await playwright.chromium.launch(headless=True)
        try:
            yield found
        finally:
            await found.close()


async def test_the_steps_the_owner_takes_are_recorded_and_the_skill_is_saved(
    browser: Browser, site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = await browser.new_page()
    recording = Recording(thread_id="thread-teach", page=page, tab_index=1)
    await page.expose_binding(TEACH_BINDING, recording.take)
    await page.add_init_script(TEACH_SCRIPT)
    await page.goto(site)
    await page.evaluate(TEACH_SCRIPT)

    await page.fill("#email", "owner@example.com")
    await page.fill("#password", "hunter2-a-secret")
    # Enter on a password field is a step too, and one that carries neither the name nor the value.
    await page.press("#password", "Enter")
    await page.select_option("#plan", "Yearly")
    await page.fill("#search", "coffee")
    await page.press("#search", "Enter")
    await page.goto(f"{site}next?code=oauth-code&state=xyz#access_token=leaked")
    await page.evaluate(TEACH_SCRIPT)
    await page.click("text=Invoices")
    await page.wait_for_load_state()
    await page.evaluate(TEACH_SCRIPT)

    # A frame of the page is not the owner's page: what its own script reports is not a step of theirs.
    await page.frame_locator("#ads").locator("#pay").click()

    lines: list[str] = [step.line for step in recording.steps]
    assert lines[0].startswith("Opened http://127.0.0.1:")
    assert 'Filled "Email"' in lines
    assert 'Chose "Plan"' in lines
    assert 'Pressed Enter in "Search"' in lines
    assert 'Clicked "Invoices"' in lines
    # The secret field's value and its name are in no line, and the value is nowhere in the recording.
    assert "Filled a secret field" in lines
    assert "Pressed Enter in a secret field" in lines
    shown: str = recording.digest()
    assert "hunter2" not in shown and "Password" not in shown
    # The page the owner moved to is a step of its own: the script runs again in every document the tab loads.
    assert sum(1 for line in lines if line.startswith("Opened ")) == 3
    # An address is a page's, never a value of it: no query, no fragment, so no code and no token in the recording.
    opened: str = "\n".join(line for line in lines if line.startswith("Opened "))
    assert "oauth-code" not in opened and "access_token" not in opened and "state=" not in opened
    # Neither an advert's page nor its click is the owner's step.
    assert "/frame" not in "\n".join(lines)
    assert 'Clicked "Pay now"' not in lines
    await page.close()

    # The review model's stand-in writes the skill of the recording; the daemon saves it as the owner's.
    async def writes(_request: object) -> PlaybookDraft:
        return PlaybookDraft(
            kind="pay invoices",
            description="Pay the month's invoices from the invoices page.",
            when="At the start of a month, signed in to the site.",
            steps=["Open the invoices page.", "Download the invoice of the month as a PDF."],
            parameters=[],
        )

    monkeypatch.setattr(teach_module, "playbook_writer_for", lambda *args, **kwargs: writes)
    monkeypatch.setattr(teach_module, "playbooks_dir", lambda: tmp_path)
    # The draft masks the key files' secrets, and those live in the owner's home: this test gets a home of its own.
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    runtime = SimpleNamespace(
        router=None,
        roles=SimpleNamespace(review=lambda: "test:review"),
        config=SimpleNamespace(agent=SimpleNamespace(model_call_timeout_s=1.0)),
    )
    skill = await draft_taught_skill(runtime, recording, "Pay this month's invoices")
    assert skill.name == "taught-pay-invoices"
    assert skill.shown == tuple(lines)
    name = await asyncio.to_thread(save_taught_skill, skill, 1.7e9)
    text: str = (tmp_path / name / "SKILL.md").read_text(encoding="utf-8")
    assert "Taught by the owner on 2023-11-14" in text
    entries = list_playbooks(tmp_path)
    assert [entry.meta.taught for entry in entries] == [True]
    assert retires(entries[0].meta.model_copy(update={"failing": 99})) is False


async def test_a_browser_without_a_window_refuses_to_record(tmp_path: Path) -> None:
    """Item 423: a task is shown in a window the owner works in; a headless browser has none, so the recording is
    refused before anything is put into a page, and the message says what to turn off."""
    from dotagent.browser.session import BrowserSession, no_runs_at_work
    from dotagent.config import load_config, load_default_config
    from dotagent.core.errors import BrowserError

    settings, profile = _headless_settings(tmp_path)
    config = load_config(settings).browser
    session: BrowserSession = BrowserSession(config, load_default_config().server, profile, tmp_path, no_runs_at_work)
    try:
        with pytest.raises(BrowserError, match="without a window"):
            await session.start_teaching("thread-teach")
        assert session.teaching is None
    finally:
        await session.aclose()

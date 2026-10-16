"""A draft a run writes is a card the owner sends, changed or not, or discards (item 314, after Grok's): nothing goes
out before Send, and then Mail or Messages gets what the card holds; a send the application refuses says why and can
be sent again; a second press, here or on another screen, sends nothing; the phone hears of the draft. A real daemon
whose scripted model calls draft_message, in Chrome, wide and on a phone; ``osascript`` is a stand-in that writes down
what it was given, so that no email or message leaves the Mac."""

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.core.types import JsonObject
from dotagent.desktop import script as osascript
from dotagent.drafts.service import (
    DRAFT_CREATED_EVENT,
    DRAFT_DECIDED_EVENT,
    STOPPED_WHILE_SENDING,
    fail_unsettled_drafts,
)
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.notify.push import PushMessage, push_message
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import DraftRecord, EventRecord, RunRecord
from dotagent.tools.drafts import DRAFT_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "drafts-test-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
REPORT: str = "Hi Ayşe,\n\nThe weekly report is attached.\n\nDot"
REPORT_NOW: str = "Hi Ayşe,\n\nThe weekly report is attached; the numbers are up.\n\nDot"
LATE: str = "Running 10 minutes late."
LUNCH: str = "Lunch on Friday?"
# Words the stand-in of osascript refuses, as Mail refuses a send when its account is offline.
REFUSED: str = "OFFLINE"
OFFLINE_SAYS: str = "execution error: Mail got an error: the account is offline. (-1728)"
_STAND_IN: str = f"""#!{sys.executable} -I
import json
import sys

with open({{log!r}}, "a", encoding="utf-8") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if any({REFUSED!r} in argument for argument in sys.argv[1:]):
    sys.stderr.write({OFFLINE_SAYS!r} + "\\n")
    sys.exit(1)
"""
_DRAFTS: tuple[JsonObject, ...] = (
    {"channel": "mail", "to": ["ayse@example.com"], "subject": "Weekly report", "body": REPORT},
    {"channel": "message", "to": ["+90 555 111 22 33"], "body": LATE},
    {"channel": "mail", "to": ["mert@example.com"], "subject": "Lunch", "body": LUNCH},
    {"channel": "mail", "to": ["deniz@example.com"], "subject": "Invoice", "body": f"The invoice is paid. {REFUSED}"},
    {"channel": "mail", "to": ["ece@example.com"], "subject": "Thanks", "body": "Thanks for today."},
    {"channel": "message", "to": ["+90 555 444 55 66"], "body": "See you tomorrow."},
)


def _turns() -> list[ScriptedTurn]:
    """One draft a turn, so that the cards come in this order; then the run ends."""
    return [
        *(ScriptedTurn(text="", calls=(ScriptedCall(tool=DRAFT_TOOL, arguments=draft),)) for draft in _DRAFTS),
        ScriptedTurn(
            text="",
            calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Six drafts wait for you.", "success": True}),),
        ),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, whose model plays ``_turns``, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _turns()))
        manager: RunManager = RunManager(runtime)
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _ended(runtime: Runtime, run_id: str) -> RunRecord:
    deadline: float = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        run: RunRecord = await runtime.store.get_run(run_id)
        if run.status in ("done", "failed", "stopped"):
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run did not end in {WAIT_S} s")


async def _open(browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


def _stand_in(path: Path, log: Path) -> Path:
    """Write the stand-in of osascript at ``path``, writing down what it is given in ``log``."""
    path.write_text(_STAND_IN.replace("{log!r}", repr(str(log))), encoding="utf-8")
    path.chmod(0o755)
    return path


def _handed(log: Path) -> list[list[str]]:
    """What the stand-in of osascript was given, a call a line, with the script and its arguments."""
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.browser
async def test_a_draft_goes_out_only_with_the_owners_send_and_with_what_the_card_holds(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log: Path = tmp_path / "osascript.jsonl"
    monkeypatch.setattr(osascript, "_OSASCRIPT", str(_stand_in(tmp_path / "osascript", log)))
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            run: RunRecord = await manager.start(
                "Write my emails", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, None, False, ()
            )
            assert (await _ended(runtime, run.id)).status == "done"
            events: list[EventRecord] = await runtime.store.list_events(run.id, 0, 1000)
            made: list[DraftRecord] = [
                DraftRecord.model_validate(event.data) for event in events if event.kind == DRAFT_CREATED_EVENT
            ]
            assert [draft.status for draft in made] == ["waiting"] * 6
            report, late, lunch, invoice, thanks, bye = made
            # The phone hears of each draft, in the words of the desktop notification.
            told: PushMessage | None = push_message(
                next(event for event in events if event.kind == DRAFT_CREATED_EVENT), run
            )
            assert told is not None
            assert (told.title, told.body, told.route) == (
                "Dot drafted an email",
                "To ayse@example.com: Weekly report",
                f"#/runs/{run.id}",
            )
            assert _handed(log) == [], "a draft went out before the owner pressed Send"
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await _open(browser, address, run.id, 1280, "light")
                cards: Locator = page.locator(".timeline .draft-card")
                await expect(cards).to_have_count(6)
                await expect(cards.locator(".draft-head .badge")).to_have_text(["Draft"] * 6)
                # Six wait: each has a tick, and the bar after the last sends the ticked ones.
                await expect(cards.locator(".draft-pick")).to_have_count(6)
                bar: Locator = page.locator(".timeline .draft-bar")
                await expect(bar).to_contain_text("6 drafts wait")
                await expect(bar.get_by_role("button", name="Send ticked")).to_be_disabled()
                # No empty part of a card is written out as a word ("null").
                await expect(cards.filter(has_text="null")).to_have_count(0)
                first: Locator = cards.nth(0)
                await expect(first.locator(".draft-fields")).to_contain_text("ayse@example.com")
                await expect(first.locator(".draft-fields")).to_contain_text("Weekly report")
                await expect(first.locator(".draft-body")).to_have_text(REPORT)
                await page.screenshot(path=str(tmp_path / "drafts-1280-light.png"), animations="disabled")
                # Changed, then sent: Mail gets the words of the card, the copy the owner added too.
                await first.get_by_role("button", name="Edit").click()
                await first.get_by_label("Cc").fill("mert@example.com")
                await first.get_by_label("Body").fill(REPORT_NOW)
                await page.screenshot(path=str(tmp_path / "draft-edit-1280-light.png"), animations="disabled")
                await first.get_by_role("button", name="Send email").click()
                await expect(first.locator(".draft-head .badge")).to_have_text("Sent")
                await expect(first.locator(".draft-body")).to_have_text(REPORT_NOW)
                await expect(first.get_by_role("button")).to_have_count(0)
                handed: list[list[str]] = _handed(log)
                assert len(handed) == 1
                assert handed[0][:3] == ["-l", "AppleScript", "-e"]
                assert 'tell application "Mail"' in handed[0][3]
                # The values come after --, so that one that starts with a dash is not read as an option.
                assert handed[0][4] == "--"
                assert handed[0][5:] == ["Weekly report", REPORT_NOW, "1", "ayse@example.com", "mert@example.com"]
                # Discarded: nothing goes out.
                third: Locator = cards.nth(2)
                await third.get_by_role("button", name="Discard").click()
                await page.get_by_role("dialog").get_by_role("button", name="Discard").click()
                await expect(third.locator(".draft-head .badge")).to_have_text("Discarded")
                await expect(third.get_by_role("button")).to_have_count(0)
                assert len(_handed(log)) == 1
                # Refused by the application: the card says why and keeps Send; changed, it goes.
                fourth: Locator = cards.nth(3)
                await fourth.get_by_role("button", name="Send email").click()
                await expect(fourth.locator(".draft-head .badge")).to_have_text("Not sent")
                await expect(fourth.locator(".draft-error")).to_contain_text(OFFLINE_SAYS)
                await fourth.get_by_role("button", name="Edit").click()
                await fourth.get_by_label("Body").fill("The invoice is paid.")
                await fourth.get_by_role("button", name="Send again").click()
                await expect(fourth.locator(".draft-head .badge")).to_have_text("Sent")
                assert [call[5:] for call in _handed(log)[1:]] == [
                    ["Invoice", f"The invoice is paid. {REFUSED}", "1", "deniz@example.com"],
                    ["Invoice", "The invoice is paid.", "1", "deniz@example.com"],
                ]
                # On a phone: the message goes to Messages, and the page open on the Mac shows it sent, the run long
                # over.
                phone: Page = await _open(browser, address, run.id, 390, "dark")
                second: Locator = phone.locator(".timeline .draft-card").nth(1)
                await expect(second.locator(".draft-fields")).to_contain_text("+90 555 111 22 33")
                await second.scroll_into_view_if_needed()
                await phone.screenshot(path=str(tmp_path / "draft-390-dark.png"), animations="disabled")
                await second.get_by_role("button", name="Send message").click()
                await expect(second.locator(".draft-head .badge")).to_have_text("Sent")
                last: list[str] = _handed(log)[-1]
                assert 'tell application "Messages"' in last[3]
                assert last[5:] == [LATE, "+90 555 111 22 33"]
                await expect(cards.nth(1).locator(".draft-head .badge")).to_have_text("Sent")
                await phone.close()
                await page.close()
                # From the history, as each draft ended; the two that wait, ticked, go out together, each with the
                # words its card holds.
                page = await _open(browser, address, run.id, 1280, "dark")
                cards = page.locator(".timeline .draft-card")
                await expect(cards.locator(".draft-head .badge")).to_have_text(
                    ["Sent", "Sent", "Discarded", "Sent", "Draft", "Draft"]
                )
                await cards.nth(4).locator(".draft-pick").check()
                await cards.nth(5).locator(".draft-pick").check()
                bar = page.locator(".timeline .draft-bar")
                await expect(bar).to_contain_text("2 of 2 drafts ticked")
                await bar.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / "drafts-ticked-1280-dark.png"), animations="disabled")
                await bar.get_by_role("button", name="Send 2").click()
                await expect(cards.locator(".draft-head .badge")).to_have_text(
                    ["Sent", "Sent", "Discarded", "Sent", "Sent", "Sent"]
                )
                await expect(bar).to_have_count(0)
                assert [call[5:] for call in _handed(log)[-2:]] == [
                    ["Thanks", "Thanks for today.", "1", "ece@example.com"],
                    ["See you tomorrow.", "+90 555 444 55 66"],
                ]
                await expect(cards.get_by_role("button")).to_have_count(0)
                await expect(cards.filter(has_text="null")).to_have_count(0)
                await page.screenshot(path=str(tmp_path / "drafts-ended-1280-dark.png"), animations="disabled")
                await page.close()
            finally:
                await browser.close()
            # A second press, here or on another screen, sends nothing.
            async with httpx.AsyncClient(
                base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=WAIT_S
            ) as client:
                again: httpx.Response = await client.post(
                    f"/api/drafts/{report.id}/send",
                    json={"to": ["ayse@example.com"], "cc": [], "subject": "Weekly report", "body": REPORT},
                )
                assert again.status_code == 409, again.text
                gone: httpx.Response = await client.post(f"/api/drafts/{lunch.id}/discard")
                assert gone.status_code == 409, gone.text
            assert len(_handed(log)) == 6
            assert [
                (await runtime.store.get_draft(draft.id)).status
                for draft in (report, late, lunch, invoice, thanks, bye)
            ] == ["sent", "sent", "discarded", "sent", "sent", "sent"]


async def test_a_draft_a_stopped_daemon_was_sending_is_not_sent_again(login_env: dict[str, str]) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            draft: DraftRecord = await runtime.store.add_draft("run_x", "mail", ["a@example.com"], [], "Hi", "Hello.")
            await runtime.store.claim_draft(draft.id, ["a@example.com"], [], "Hi", "Hello.")
            assert await fail_unsettled_drafts(runtime.store, runtime.bus) == 1
            failed: DraftRecord = await runtime.store.get_draft(draft.id)
            assert (failed.status, failed.error) == ("failed", STOPPED_WHILE_SENDING)
            told: list[EventRecord] = await runtime.store.list_events("run_x", 0, 10)
            assert [event.kind for event in told] == [DRAFT_DECIDED_EVENT]
            assert await fail_unsettled_drafts(runtime.store, runtime.bus) == 0
        finally:
            await close_runtime(runtime)

"""How a draft is handed to Mail and to Messages when the application does not take all of it (item 558): a mail that
Mail's ``send`` answers false to is not "sent", and a message to several people that fails partway is "Not sent" with
the names of those it had reached, which the owner takes off before Send again. The scripts are compiled, never run (a
run would send for real); ``osascript`` itself is a stand-in written here that fails for one recipient."""

import shutil
import subprocess
from pathlib import Path

import pytest

from dotagent.core.errors import DesktopError
from dotagent.drafts import service
from dotagent.drafts.scripts import SCRIPTS, send_calls
from dotagent.drafts.service import DraftWords, create_draft, send_draft
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import DraftChannel, DraftRecord

_OSACOMPILE: str = "/usr/bin/osacompile"
_FIRST: str = "+90 555 000 00 01"
_SECOND: str = "+90 555 000 00 02"
_THIRD: str = "+90 555 000 00 03"
_NO_PARTICIPANT: str = "execution error: Messages got an error: Can't get participant. (-1728)"


@pytest.mark.skipif(shutil.which(_OSACOMPILE) is None, reason="needs macOS's AppleScript compiler")
@pytest.mark.parametrize("channel", ["mail", "message"])
def test_the_scripts_compile(channel: DraftChannel, tmp_path: Path) -> None:
    source: Path = tmp_path / f"{channel}.applescript"
    source.write_text(SCRIPTS[channel], encoding="utf-8")
    compiled: subprocess.CompletedProcess[str] = subprocess.run(
        [_OSACOMPILE, "-o", str(tmp_path / f"{channel}.scpt"), str(source)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr


def test_a_send_mail_answers_false_to_is_an_error_of_the_script() -> None:
    """``send`` of an outgoing message answers whether it sent; false raised no error, so osascript ended well and the
    card said the draft was sent."""
    assert 'if not (send theMessage) then error "Mail did not send the message"' in SCRIPTS["mail"]
    assert "\n    send theMessage\n" not in SCRIPTS["mail"]


async def _draft(store: Store, bus: EventBus, channel: DraftChannel, to: list[str]) -> DraftRecord:
    return await create_draft(store, bus, "run_x", channel, to, [], "Hello" if channel == "mail" else "", "The words.")


async def test_a_message_that_fails_partway_names_those_it_reached_and_a_retry_leaves_them_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "shots")
    try:
        bus: EventBus = EventBus(store)
        calls: list[tuple[str, ...]] = []
        broken: list[str] = [_SECOND]

        async def osascript(
            language: str, script: str, arguments: tuple[str, ...], confinement: tuple[str, ...], timeout_s: float
        ) -> str:
            calls.append(arguments)
            if arguments[-1] in broken:
                raise DesktopError("osascript failed", {"stderr": _NO_PARTICIPANT})
            return ""

        monkeypatch.setattr(service, "run_osascript", osascript)
        draft: DraftRecord = await _draft(store, bus, "message", [_FIRST, _SECOND, _THIRD])
        words: DraftWords = DraftWords(to=draft.to, cc=(), subject="", body=draft.body)

        failed: DraftRecord = await send_draft(store, bus, 30.0, draft.id, words)

        assert failed.status == "failed" and failed.error is not None
        assert _NO_PARTICIPANT in failed.error and f"already sent to {_FIRST}," in failed.error, failed.error
        assert _SECOND not in failed.error.partition("already sent to")[2]
        # One run for each recipient, in order, and none after the one that failed.
        assert calls == [("The words.", _FIRST), ("The words.", _SECOND)]

        # The owner takes the first off, and the retry reaches the other two alone.
        broken.clear()
        calls.clear()
        retry: DraftRecord = await send_draft(
            store, bus, 30.0, draft.id, DraftWords(to=(_SECOND, _THIRD), cc=(), subject="", body=draft.body)
        )
        assert retry.status == "sent" and retry.error is None
        assert calls == [("The words.", _SECOND), ("The words.", _THIRD)]
    finally:
        await store.close()


async def test_an_email_to_several_people_is_one_run_of_its_script_and_one_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "shots")
    try:
        bus: EventBus = EventBus(store)
        calls: list[tuple[str, ...]] = []

        async def osascript(
            language: str, script: str, arguments: tuple[str, ...], confinement: tuple[str, ...], timeout_s: float
        ) -> str:
            calls.append(arguments)
            raise DesktopError("osascript failed", {"stderr": "execution error: Mail did not send the message (1)"})

        monkeypatch.setattr(service, "run_osascript", osascript)
        draft: DraftRecord = await _draft(store, bus, "mail", ["ayse@example.com", "mert@example.com"])
        failed: DraftRecord = await send_draft(
            store, bus, 30.0, draft.id, DraftWords(to=draft.to, cc=(), subject=draft.subject, body=draft.body)
        )
        assert len(calls) == 1 and calls == [send_calls(draft)[0]]
        assert failed.status == "failed" and failed.error is not None
        assert "Mail did not send the message" in failed.error and "already sent" not in failed.error
    finally:
        await store.close()

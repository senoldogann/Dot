"""Item 301: what the runs at work at the same time hold, what a run may not do to another's, and what its note says;
and the tabs of a chat with a run at work, which the browser's idle reaper keeps (review G5)."""

from pathlib import Path

import pytest

from dotagent.agent import peers as peers_module
from dotagent.agent.peers import Peers, PeerView, peers_note
from dotagent.browser.pages import ThreadTabs
from dotagent.browser.session import threads_to_release
from dotagent.policy.types import ActionInfo, ActionKind

_WORKSPACE: Path = Path("/Users/someone/Library/Application Support/Dot/workspace")
_SHARED: str = str(_WORKSPACE / "report.md")


def _action(kind: ActionKind, target: str) -> ActionInfo:
    return ActionInfo(tool="t", kind=kind, risk="low", summary="s", target=target, flags=())


def _two_runs() -> Peers:
    peers: Peers = Peers()
    peers.begin("run-a", "thread-a", "Write the weekly report from the sales sheet")
    peers.begin("run-b", "thread-b", "Tidy the workspace")
    return peers


def test_a_file_another_run_changed_is_changed_only_after_it_is_read_again() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "write_file", _action("write", _SHARED))
    refused: str | None = peers.conflict("run-b", "write_file", _action("write", _SHARED))
    assert refused is not None and "you have not read it" in refused and "weekly report" in refused
    peers.saw("run-b", "read_file", _action("read", _SHARED))
    assert peers.conflict("run-b", "edit_file", _action("write", _SHARED)) is None
    # A change of the other run after that read is refused again.
    peers.saw("run-a", "edit_file", _action("write", _SHARED))
    later: str | None = peers.conflict("run-b", "edit_file", _action("write", _SHARED))
    assert later is not None and "you last read it at" in later
    # The run that changed it, another file, and a run that ended hold nothing against it.
    assert peers.conflict("run-a", "write_file", _action("write", _SHARED)) is None
    assert peers.conflict("run-b", "write_file", _action("write", str(_WORKSPACE / "other.md"))) is None
    peers.end("run-a")
    assert peers.conflict("run-b", "write_file", _action("write", _SHARED)) is None


def test_an_app_another_run_drives_is_its_own_for_a_while(monkeypatch: pytest.MonkeyPatch) -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "desktop_act", _action("desktop_input", "Mail"))
    refused: str | None = peers.conflict("run-b", "desktop_act", _action("desktop_input", "Mail"))
    assert refused is not None and "Mail is in use" in refused
    # Reading the app, or driving another one, steps on nothing.
    assert peers.conflict("run-b", "desktop_snapshot", _action("desktop_observe", "Mail")) is None
    assert peers.conflict("run-b", "desktop_act", _action("desktop_input", "Notes")) is None
    monkeypatch.setattr(peers_module, "APP_HOLD_S", 0.0)
    assert peers.conflict("run-b", "desktop_act", _action("desktop_input", "Mail")) is None


def test_runs_of_one_chat_are_not_each_others_peers() -> None:
    peers: Peers = Peers()
    peers.begin("run-a", "thread-a", "First")
    peers.begin("run-a2", "thread-a", "Resumed")
    peers.saw("run-a", "write_file", _action("write", _SHARED))
    assert peers.conflict("run-a2", "write_file", _action("write", _SHARED)) is None
    assert peers.others("run-a2") == ()


def test_the_note_names_each_other_run_and_what_it_holds() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "browser_open", _action("browser_navigate", "https://mail.example.com/inbox"))
    peers.saw("run-a", "desktop_act", _action("desktop_input", "Numbers"))
    peers.saw("run-a", "write_file", _action("write", _SHARED))
    views: tuple[PeerView, ...] = peers.others("run-b")
    assert views == (
        PeerView(
            goal="Write the weekly report from the sales sheet",
            hosts=("mail.example.com",),
            apps=("Numbers",),
            changed=(_SHARED,),
        ),
    )
    note: str = peers_note(views, _WORKSPACE)
    assert note.startswith("[system note] Other runs of Dot are at work")
    assert (
        "“Write the weekly report from the sales sheet”: pages on mail.example.com; drives Numbers; changed report.md"
        in note
    )
    assert peers_note(peers.others("nobody"), _WORKSPACE).count("\n- ") == 2
    assert peers_note((), _WORKSPACE) == ""


async def test_the_tabs_of_a_chat_with_a_run_at_work_outlive_their_idle_time() -> None:
    """Of the chats whose tabs idled past the cutoff, the reaper releases the one with no run at work; the one whose run
    waits (for an answer, for a long command) and the one in use now keep theirs."""
    peers: Peers = _two_runs()
    peers.end("run-b")
    busy: ThreadTabs = ThreadTabs(last_used=10.0)
    await busy.lock.acquire()
    threads: dict[str, ThreadTabs] = {
        "thread-a": ThreadTabs(last_used=10.0),
        "thread-b": ThreadTabs(last_used=10.0),
        "thread-c": busy,
        "thread-d": ThreadTabs(last_used=99.0),
    }
    assert peers.threads() == frozenset({"thread-a"})
    assert threads_to_release(threads, 50.0, peers.threads()) == ["thread-b"]

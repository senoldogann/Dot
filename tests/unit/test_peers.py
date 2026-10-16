"""Item 301: what the runs at work at the same time hold, what a run may not do to another's, and what its note says;
and the tabs of a chat with a run at work, which the browser's idle reaper keeps (review G5). Item 305: an application
a script names, a batch that moves into another run's application, the footprint taken before a call runs, the case
of an application's name, and a browser that is not switched under a run at work."""

from pathlib import Path

import pytest

from dotagent.agent import peers as peers_module
from dotagent.agent.peers import Peers, PeerView, peers_note
from dotagent.browser.pages import ThreadTabs
from dotagent.browser.session import threads_in_use, threads_to_release
from dotagent.policy.shell import ShellAssessment, assess_applescript, assess_shell
from dotagent.policy.types import ActionInfo, ActionKind
from dotagent.tools.apple import APPLE_SCRIPT_TOOL, script_apps

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
    # The run that changed it and another file hold nothing against it.
    assert peers.conflict("run-a", "write_file", _action("write", _SHARED)) is None
    assert peers.conflict("run-b", "write_file", _action("write", str(_WORKSPACE / "other.md"))) is None
    # Once the run that changed it ended, its change still wants a reading (item 305), and holds nothing after one.
    peers.end("run-a")
    assert peers.conflict("run-b", "write_file", _action("write", _SHARED)) is not None
    peers.saw("run-b", "read_file", _action("read", _SHARED))
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


def test_a_script_names_the_applications_it_drives() -> None:
    assert script_apps('tell application "Notes" to make new note') == ("Notes",)
    assert script_apps(
        'tell application "System Events" to tell process "Safari" to click button 1\n'
        'tell app "Mail" to activate\ntell application "mail" to quit'
    ) == ("Safari", "Mail")
    assert script_apps("const numbers = Application('Numbers'); Application(\"Finder\").activate()") == (
        "Numbers",
        "Finder",
    )
    assert script_apps('do shell script "ls"') == ()
    assert script_apps('tell application id "com.apple.Notes" to activate') == ()


def test_an_app_another_run_drives_is_refused_to_a_script_and_a_script_holds_its_apps() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "desktop_act", _action("desktop_input", "Mail"))
    script: str = 'tell application "Notes" to activate\ntell application "Mail" to quit'
    refused: str | None = peers.conflict("run-b", APPLE_SCRIPT_TOOL, _action("app_control", script))
    assert refused is not None and refused.startswith("Nothing was done: Mail is in use by another run of Dot")
    assert (
        peers.take("run-b", APPLE_SCRIPT_TOOL, _action("app_control", 'tell application "Numbers" to activate')) is None
    )
    held: str | None = peers.conflict("run-a", "desktop_act", _action("desktop_input", "Numbers"))
    assert held is not None and "Tidy the workspace" in held


def test_an_application_is_one_whatever_the_case_of_its_name() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "desktop_open_app", _action("app_control", "notes"))
    refused: str | None = peers.conflict("run-b", "desktop_act", _action("desktop_input", "Notes"))
    assert refused is not None and "Notes is in use" in refused
    peers.saw("run-a", "desktop_act", _action("desktop_input", "Notes"))
    assert peers.others("run-b")[0].apps == ("Notes",)


def test_a_batch_that_moves_into_another_runs_app_stops_there_and_holds_each_app_it_moves_into() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "desktop_act", _action("desktop_input", "Mail"))
    assert peers.take_app("run-b", "Notes") is None
    held: str | None = peers.take_app("run-b", "Mail")
    assert held is not None and held.startswith("Mail is in use by another run of Dot")
    # The app it moved into is its own now, the one it was refused is not.
    assert peers.conflict("run-a", "desktop_act", _action("desktop_input", "Notes")) is not None
    assert peers.others("run-a")[0].apps == ("Notes",)


def test_the_footprint_is_taken_before_the_call_runs_so_a_second_run_finds_it() -> None:
    """Two runs that pass the first check while each waits for the user's approval: the one that takes the file first
    holds it, the other is refused when it is approved, and a write that fails halfway keeps its footprint."""
    peers: Peers = _two_runs()
    write: ActionInfo = _action("write", _SHARED)
    assert peers.conflict("run-a", "write_file", write) is None
    assert peers.conflict("run-b", "write_file", write) is None
    assert peers.take("run-b", "write_file", write) is None
    refused: str | None = peers.take("run-a", "write_file", write)
    assert refused is not None and "Tidy the workspace" in refused and "you have not read it" in refused
    # Run b's write never got to saw (it failed), and its footprint stands all the same.
    assert peers.others("run-a")[0].changed == (_SHARED,)
    # A read takes nothing before it runs: only one that went through counts as read.
    assert peers.take("run-a", "read_file", _action("read", _SHARED)) is None
    assert peers.conflict("run-a", "write_file", write) is not None


def test_a_chat_with_a_run_at_work_keeps_the_browser_from_switching_between_its_calls() -> None:
    threads: dict[str, ThreadTabs] = {"thread-a": ThreadTabs(last_used=1.0), "thread-b": ThreadTabs(last_used=1.0)}
    assert threads_in_use(threads, frozenset()) == []
    assert threads_in_use(threads, frozenset({"thread-b", "thread-z"})) == ["thread-b"]


async def test_a_chat_in_the_middle_of_a_step_keeps_the_browser_from_switching() -> None:
    busy: ThreadTabs = ThreadTabs(last_used=1.0)
    await busy.lock.acquire()
    assert threads_in_use({"thread-a": ThreadTabs(last_used=1.0), "thread-c": busy}, frozenset()) == ["thread-c"]


def test_the_files_a_run_changed_stay_changed_for_the_runs_at_work_with_it_after_it_ended() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-b", "write_file", _action("write", _SHARED))
    peers.end("run-b")
    refused: str | None = peers.conflict("run-a", "write_file", _action("write", _SHARED))
    assert refused is not None and "Tidy the workspace" in refused and "ended since" in refused
    peers.saw("run-a", "read_file", _action("read", _SHARED))
    assert peers.conflict("run-a", "write_file", _action("write", _SHARED)) is None
    # A run that starts after it ended found the file as it was left.
    peers.begin("run-c", "thread-c", "Later")
    assert peers.conflict("run-c", "write_file", _action("write", _SHARED)) is None


def _changes(line: str, workspace: Path) -> tuple[str, ...]:
    assessment: ShellAssessment = assess_shell(line, workspace, workspace, (), ())
    return assessment.changes


def test_a_command_line_names_the_files_it_changes_or_removes(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    assert _changes("sort data.csv > sorted.csv && echo done >> log.txt 2>/dev/null", tmp_path) == (
        str(tmp_path / "sorted.csv"),
        str(tmp_path / "log.txt"),
    )
    assert _changes("rm -rf build old.txt", tmp_path) == (str(tmp_path / "build"), str(tmp_path / "old.txt"))
    assert _changes("cp a.txt b.txt out/", tmp_path) == (
        str(tmp_path / "out" / "a.txt"),
        str(tmp_path / "out" / "b.txt"),
    )
    assert _changes("mv draft.md final.md", tmp_path) == (str(tmp_path / "draft.md"), str(tmp_path / "final.md"))
    assert _changes("sed -i '' 's/a/b/' notes.md", tmp_path) == (str(tmp_path / "notes.md"),)
    assert _changes("cat notes.md | tee copy.md", tmp_path) == (str(tmp_path / "copy.md"),)
    # A folder made, a file touched or read, and a path only the running line knows change nothing it can tell.
    assert _changes("mkdir -p out && touch out/x && cat notes.md && rm -rf $(pwd)/tmp", tmp_path) == ()


def test_the_files_a_command_names_take_part_in_no_judgement(tmp_path: Path) -> None:
    """The guard judges a line as it did before it named the files: a write into the workspace stays as mild."""
    assessment: ShellAssessment = assess_shell("echo hi > notes.md", tmp_path, tmp_path, (), ())
    assert (assessment.risk, assessment.flags, assessment.reasons) == ("safe", (), ("read-only command",))
    removal: ShellAssessment = assess_shell("rm -rf build", tmp_path, tmp_path, (), ())
    assert (removal.risk, removal.reasons) == ("low", ("removes files inside the workspace or temp",))


def test_a_command_that_changes_a_file_another_run_changed_is_refused_until_it_is_read() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "write_file", _action("write", _SHARED))
    line: ActionInfo = _action("shell", "sed -i '' s/a/b/ report.md").model_copy(update={"changes": (_SHARED,)})
    refused: str | None = peers.take("run-b", "shell", line)
    assert refused is not None and refused.startswith("Nothing was run:") and "you have not read it" in refused
    peers.saw("run-b", "read_file", _action("read", _SHARED))
    assert peers.take("run-b", "shell", line) is None
    # The line changed it: the other run must read it again before it changes it.
    assert peers.conflict("run-a", "write_file", _action("write", _SHARED)) is not None


def test_a_script_that_drives_a_free_app_and_changes_a_file_another_run_changed_is_refused(tmp_path: Path) -> None:
    peers: Peers = _two_runs()
    shared: str = str(tmp_path / "report.md")
    peers.saw("run-a", "write_file", _action("write", shared))
    script: str = 'tell application "Finder" to activate\ndo shell script "rm report.md"'
    assessment: ShellAssessment = assess_applescript(script, tmp_path, tmp_path, (), ())
    assert assessment.changes == (shared,)
    # A script that reaches the shell is a "shell" action of apple_script (tools/apple.py).
    action: ActionInfo = _action("shell", script).model_copy(update={"changes": assessment.changes})
    refused: str | None = peers.take("run-b", APPLE_SCRIPT_TOOL, action)
    assert refused is not None and refused.startswith("Nothing was run:") and "you have not read it" in refused
    # Refused, it holds neither the application nor the file.
    assert peers.others("run-a")[0].apps == () and peers.others("run-a")[0].changed == ()


def test_a_folder_a_command_removes_holds_every_file_another_run_changed_in_it() -> None:
    peers: Peers = _two_runs()
    peers.saw("run-a", "write_file", _action("write", _SHARED))
    removal: ActionInfo = _action("shell", "rm -rf workspace").model_copy(update={"changes": (str(_WORKSPACE),)})
    refused: str | None = peers.conflict("run-b", "shell", removal)
    assert refused is not None and f"{_SHARED} was changed" in refused
    # A folder that only begins the same way holds nothing of it.
    other: ActionInfo = _action("shell", "rm -rf work").model_copy(update={"changes": (f"{_WORKSPACE}-old",)})
    assert peers.conflict("run-b", "shell", other) is None

"""The programs that run a command line by an option or a command of their own (item 1302): ``sqlite3``'s ``.shell``,
vim's shell escapes, emacs's ``shell-command`` and ``tar``'s compress program are judged as the same line under
``bash -c`` is, and the same programs without such an option keep the verdict they had. Every line is judged, never
run, as it would run in the workspace."""

from pathlib import Path

import pytest

from dotagent.policy import paths
from dotagent.policy.engine import decide
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Decision

_LINE: str = "rm -rf ~/Documents"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder: Path = (tmp_path / "home").resolve(strict=False)
    (folder / "workspace").mkdir(parents=True)
    (folder / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setattr(paths, "_is_temp", lambda path: False)
    return folder


def _assess(line: str, home: Path) -> ShellAssessment:
    workspace: Path = home / "workspace"
    return assess_shell(line, workspace, workspace, (home / ".ssh",), ())


def _verdict(line: str, home: Path) -> Decision:
    assessment: ShellAssessment = _assess(line, home)
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags
    )
    return decide(info, "task", False, "balanced", [], None)


# The command line is the same under ``bash -c``, which is high; each of these hands it to a program that runs it.
_CARRIED: list[str] = [
    f"bash -c '{_LINE}'",
    # sqlite3: a dot command in an argument, in a -cmd value, in a statement's second line, on the input.
    f"sqlite3 app.db '.shell {_LINE}'",
    f"sqlite3 app.db '.system {_LINE}'",
    f"sqlite3 app.db -cmd '.shell {_LINE}'",
    f"sqlite3 -cmd '.shell {_LINE}' app.db 'select 1'",
    f"sqlite3 app.db 'select 1;\n.shell {_LINE}'",
    f"sqlite3 app.db '.SHELL {_LINE}'",
    f"sqlite3 app.db \".output '|{_LINE}'\"",
    f"sqlite3 app.db \".import '|{_LINE}' t\"",
    f"sqlite3 app.db \".read '|{_LINE}'\"",
    f"sqlite3 app.db <<'EOF'\n.shell {_LINE}\nEOF",
    f"echo '.shell {_LINE}' | sqlite3 app.db",
    # vim and its kin: Ex commands of -c, --cmd and +, with their colon and their bang.
    f"vim -c '!{_LINE}' x",
    f"vim -es -c '!{_LINE}' -c q x",
    f"vim -c ':!{_LINE}' x",
    f"vim -c '  : !{_LINE}' x",
    f"vim +'!{_LINE}' x",
    f"vim '+!{_LINE}' x",
    f"vim '+:!{_LINE}' x",
    f"nvim --headless -c '!{_LINE}' -c q",
    f"nvim --cmd '!{_LINE}'",
    f"vi -c 'r !{_LINE}' x",
    f"vim -c 'w !{_LINE}' x",
    f"vim -c '%!{_LINE}' x",
    f"vim -c 'set nu | !{_LINE}' x",
    f"view -c 'silent !{_LINE}' x",
    # emacs: Lisp that starts a program.
    f"emacs --batch --eval '(shell-command \"{_LINE}\")'",
    f"emacs -batch -eval '(shell-command \"{_LINE}\")'",
    f"emacs --batch --eval='(shell-command \"{_LINE}\")'",
    f"emacs --batch --eval '(shell-command-to-string \"{_LINE}\")'",
    f"emacs --batch --eval '(async-shell-command \"{_LINE}\")'",
    f"emacsclient --eval '(shell-command \"{_LINE}\")'",
    'emacs --batch --eval \'(call-process "rm" nil nil nil "-rf" "/Users/x/Documents")\'',
    'emacs --batch --eval \'(start-process "p" nil "sh" "-c" cmd)\'',
    "emacs --batch --eval '(shell-command cmd)'",
    # tar and its kin: the program that compresses, the command that takes each file, the action of a checkpoint.
    f"tar --use-compress-program='{_LINE}' -cf x.tar a",
    f"tar --use-compress-program '{_LINE}' -cf x.tar a",
    f"tar -I '{_LINE}' -cf x.tar a",
    f"tar -I'{_LINE}' -cf x.tar a",
    f"tar -cf x.tar --to-command='{_LINE}' a",
    f"tar -xf x.tar --to-command='{_LINE}'",
    f"tar -cf x.tar --checkpoint=1 --checkpoint-action=exec='{_LINE}' a",
    f"tar -cf x.tar --checkpoint=1 --checkpoint-action exec='{_LINE}' a",
    f"tar -cf x.tar --new-volume-script='{_LINE}' a",
    f"gtar -I '{_LINE}' -cf x.tar a",
    f"bsdtar --use-compress-program='{_LINE}' -cf x.tar a",
    f"/usr/bin/tar -I '{_LINE}' -cf x.tar a",
]


@pytest.mark.parametrize("line", _CARRIED)
def test_a_command_line_a_program_is_handed_is_judged_as_it_is_under_bash(line: str, home: Path) -> None:
    assessment: ShellAssessment = _assess(line, home)
    assert assessment.risk == "high", assessment.reasons
    assert "destructive" in assessment.flags or "outside_workspace" in assessment.flags, assessment.flags
    assert _verdict(line, home).verdict == "ask"


# The same programs and options with a command that harms nothing, and the programs without such an option.
_ORDINARY: list[str] = [
    "sqlite3 app.db 'select * from users'",
    "sqlite3 app.db '.tables'",
    "sqlite3 app.db '.schema users'",
    "sqlite3 -header -csv app.db 'select 1'",
    "sqlite3 app.db '.dump'",
    "sqlite3 app.db '.mode csv'",
    "sqlite3 app.db \"select '.shell rm -rf ~/Documents'\"",
    "sqlite3 app.db '.shell ls'",
    "sqlite3 app.db '.shell git status'",
    "sqlite3 app.db <<'EOF'\nselect 1;\nEOF",
    "echo 'select 1' | sqlite3 app.db",
    "vim notes.txt",
    "vim",
    "nvim README.md",
    "vi file.txt",
    "vim -R file.txt",
    "vim +10 file.txt",
    "vim +/foo file.txt",
    "vim -u NONE file.txt",
    "vim -c 'set nu' file.txt",
    "vim -c 'wq' file.txt",
    "vim -c 'w!' file.txt",
    "vim -c 'q!' file.txt",
    "vim -c '%s/a/b/g' file.txt",
    "vim -c 'g!/foo/d' file.txt",
    "vim -c '!ls' file.txt",
    "vim -c '!rm -rf build' file.txt",
    "emacs notes.txt",
    "emacs -nw file.txt",
    "emacs --version",
    "emacs --batch --eval '(message \"hi\")'",
    "emacs --batch --eval '(shell-command \"ls\")'",
    "emacs --batch -l init.el",
    "tar -czf out.tgz src",
    "tar czf out.tgz src",
    "tar -xzf in.tgz",
    "tar xf in.tar -C out",
    "tar -tf in.tar",
    "tar -cf out.tar --exclude=.git .",
    "tar --create --file=out.tar src",
    "tar -I zstd -cf x.tzst a",
    "tar -I 'pigz -p 4' -cf x.tgz a",
    "tar --use-compress-program=pigz -cf x.tgz a",
    "tar --checkpoint=100 --checkpoint-action=dot -cf x.tar a",
    "tar -cf x.tar --checkpoint=1 --checkpoint-action=exec=ls a",
    "gtar -xzf in.tgz",
    "bsdtar -xf in.zip",
]


@pytest.mark.parametrize("line", _ORDINARY)
def test_the_same_programs_without_a_harmful_line_keep_their_verdict(line: str, home: Path) -> None:
    assessment: ShellAssessment = _assess(line, home)
    assert assessment.risk in ("safe", "low"), assessment.reasons
    assert _verdict(line, home).verdict == "allow", assessment.reasons

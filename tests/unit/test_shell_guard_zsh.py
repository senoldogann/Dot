"""The shell guard checked against zsh itself (item 1250; the code review of 2026-11-19, K1-01 to K1-06 and TS-04).

Each line runs in a real ``zsh -f`` in a temporary folder whose ``HOME`` is that folder and whose ``PATH`` holds only
recorder programs: ``rm`` and ``cat`` there write the folder they ran in and the words they were given to a log and do
nothing else, and the shells a line may start are links to the system's shells, which find the same recorders. No real
``rm`` or ``cat`` is reachable, and every path a line names lies in the temporary folder. A recorded ``rm`` of a path
outside the workspace, or a recorded ``cat`` of a file under the protected folder, is a step the guard must not let
through: the default preset asks or refuses it. Ordinary lines keep the verdict they had."""

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from dotagent.policy import paths
from dotagent.policy.engine import auto_review, decide, preset_verdict, route_for
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Preset, Verdict

pytestmark = pytest.mark.skipif(not Path("/bin/zsh").exists(), reason="zsh is macOS's own shell")

_RECORDER: str = """#!/bin/sh
printf '%s\\037' "$PWD" "${0##*/}" "$@" >> "$DOT_RECORD"
printf '\\036' >> "$DOT_RECORD"
"""
_RECORDED: tuple[str, ...] = ("rm", "cat")
# The shells and interpreters a line may start, linked from the recorders' folder so that they run as on the Mac and
# find the recorders on their PATH.
_LINKED: tuple[str, ...] = ("/bin/sh", "/bin/bash", "/bin/tcsh", "/bin/csh")

# The report's lines, word for word but for the temporary home, and variants of each root cause.
_LINES: list[str] = [
    # K1-01: an append joins the old value and the new.
    "K=~/.s; K+=sh; cat $K/id_rsa",
    "HOME+=/.ssh; cat $HOME/id_rsa",
    "D=~/Docu; D+=ments; rm -rf $D",
    # K1-02: a comment or a here-document inside a substitution holds a ")".
    "echo $(true # )'\n)\nrm -rf ~/Documents\n# '",
    "echo $(cat <<EOF\n)'\nEOF\n)\nrm -rf ~/Documents\n# '",
    # K1-03: zsh's two-operand cd, and CDPATH.
    "cd workspace .ssh && cat id_rsa",
    "pushd workspace .ssh >/dev/null && cat id_rsa",
    "CDPATH=~; cd .ssh && cat id_rsa",
    # K1-04: a C shell, and zsh's "-" precommand modifier.
    "tcsh -c 'rm -rf ~/Documents'",
    "csh -c 'rm -rf ~/Documents'",
    "true; - rm -rf ~/Documents",
    # K1-05: braces in the command's place.
    "''{rm,-rf,~/Documents}",
    "command {rm,-rf,~/Documents}",
    "noglob {rm,-rf,~/Documents}",
    "''{cat,~/.ssh/id_rsa}",
    # K1-06: an alias's value runs when its name does (zsh 5.9 parses a -c string whole, so a later line of it does
    # not see an alias it defines, but ``eval`` does).
    "alias x='rm -rf ~/Documents'; eval x",
    "alias -g Z='~/.ssh/id_rsa'; eval 'cat Z'",
    # The security review of item 1250: zsh's cdpath array, its chdir, a line's own HOME behind "~", zsh's alias and
    # function tables, and a quote or an escape that ends a variable's name; each with its twin.
    "cdpath=(~); cd .ssh && cat id_rsa",
    "cdpath+=(~); cd .ssh && cat id_rsa",
    "chdir ..; rm -rf Documents",
    "chdir ..; cat .ssh/id_rsa",
    "chdir workspace .ssh && cat id_rsa",
    "cd ..; cat .ssh/id_rsa",
    "HOME+=/.s; HOME+=sh; cat ~/id_rsa",
    "HOME+=/.ssh; cat ~/id_rsa",
    "HOME=$HOME/.ssh; cat ~/id_rsa",
    "aliases[x]='rm -rf ~/Documents'; eval x",
    "functions[x]='rm -rf ~/Documents'; x",
    "galiases[Z]='~/.ssh/id_rsa'; eval 'cat Z'",
    "K=~/.s; K=$K'sh'; cat $K/id_rsa",
    'K=~/.s; K="$K"sh; cat $K/id_rsa',
    "K=~/.s; K=$K\\sh; cat $K/id_rsa",
    "K=~/.s; K=${K}sh; cat $K/id_rsa",
]
# Lines that do nothing the guard must stop, read as they were before.
_ORDINARY: list[str] = [
    "cat notes.txt",
    "rm -f build.log",
    "cd sub && cat a.txt",
    "alias ll='ls -l'",
    "bash -c 'cat notes.txt'",
    "K=no; K+=tes.txt; cat $K",
]


@dataclass(frozen=True)
class _Home:
    home: Path
    workspace: Path
    protected: Path
    bin: Path
    record: Path


@dataclass(frozen=True)
class _Run:
    cwd: Path
    program: str
    args: tuple[str, ...]


@pytest.fixture
def zsh_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Home:
    home: Path = (tmp_path / "home").resolve(strict=False)
    workspace: Path = home / "workspace"
    (workspace / "sub").mkdir(parents=True)
    (workspace / "notes.txt").write_text("notes\n")
    (workspace / "sub" / "a.txt").write_text("a\n")
    protected: Path = home / ".ssh"
    protected.mkdir()
    (protected / "id_rsa").write_text("not a key\n")
    (home / "Documents").mkdir()
    (home / "Documents" / "kept.txt").write_text("kept\n")
    folder: Path = tmp_path / "bin"
    folder.mkdir()
    for name in _RECORDED:
        recorder: Path = folder / name
        recorder.write_text(_RECORDER)
        recorder.chmod(recorder.stat().st_mode | stat.S_IXUSR)
    for shell in _LINKED:
        if Path(shell).exists():
            (folder / Path(shell).name).symlink_to(shell)
    # The guard reads ``~`` and ``$HOME`` from this environment, as the daemon does from its own; the temporary home
    # stands for the owner's, which is no temporary folder.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(paths, "_is_temp", lambda path: False)
    monkeypatch.delenv("CDPATH", raising=False)
    return _Home(home=home, workspace=workspace, protected=protected, bin=folder, record=tmp_path / "record")


def _run_in_zsh(line: str, where: _Home) -> list[_Run]:
    """What ``line`` starts of the recorders when zsh runs it in the workspace."""
    where.record.write_text("")
    environment: dict[str, str] = {"HOME": str(where.home), "PATH": str(where.bin), "DOT_RECORD": str(where.record)}
    subprocess.run(
        ["/bin/zsh", "-f", "-c", line],
        cwd=where.workspace,
        env=environment,
        capture_output=True,
        timeout=20,
        check=False,
    )
    runs: list[_Run] = []
    for entry in where.record.read_text().split("\x1e"):
        fields: list[str] = entry.split("\x1f")[:-1]
        if len(fields) >= 2:
            runs.append(_Run(cwd=Path(fields[0]), program=fields[1], args=tuple(fields[2:])))
    return runs


def _harmful(run: _Run, where: _Home) -> bool:
    """Whether ``run`` deletes outside the workspace or reads under the protected folder."""
    paths: list[Path] = [Path(os.path.normpath(run.cwd / arg)) for arg in run.args if not arg.startswith("-")]
    if run.program == "rm":
        return any(not path.is_relative_to(where.workspace) for path in paths)
    if run.program == "cat":
        return any(path.is_relative_to(where.protected) for path in paths)
    return False


def _verdict(line: str, where: _Home, preset: Preset) -> Verdict:
    assessment: ShellAssessment = assess_shell(line, where.workspace, where.workspace, (where.protected,), ())
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags
    )
    refused = auto_review(info)
    return refused.verdict if refused is not None else preset_verdict(preset, info)[0]


@pytest.mark.parametrize("line", _LINES)
def test_what_zsh_runs_of_a_line_the_guard_reads_too(line: str, zsh_home: _Home) -> None:
    runs: list[_Run] = _run_in_zsh(line, zsh_home)
    assert any(_harmful(run, zsh_home) for run in runs), f"zsh ran no harmful recorder for {line!r}: {runs}"
    for preset in ("balanced", "aggressive"):
        assert _verdict(line, zsh_home, preset) != "allow", f"{preset} lets {line!r} through"
    assert (zsh_home.home / "Documents" / "kept.txt").exists()


@pytest.mark.parametrize("line", _ORDINARY)
def test_an_ordinary_line_zsh_runs_harmlessly_stays_allowed(line: str, zsh_home: _Home) -> None:
    runs: list[_Run] = _run_in_zsh(line, zsh_home)
    assert not any(_harmful(run, zsh_home) for run in runs)
    assert _verdict(line, zsh_home, "balanced") == "allow"


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("perl5.34 -e 'print 1'", "perl runs inline code"),
        ("python3.13 -c 'print(1)'", "python3 runs inline code"),
        ("npx -c 'rm -rf ~/Documents'", "removes files outside the workspace"),
        ("npm exec --call='rm -rf ~/Documents'", "removes files outside the workspace"),
        ("docker exec box sh -c 'rm -rf ~/Documents'", "removes files outside the workspace"),
    ],
)
def test_a_program_that_runs_a_line_is_read_by_that_line(line: str, reason: str, zsh_home: _Home) -> None:
    """Programs that start a line without zsh's help, read by the line they are given (K1-04)."""
    assessment: ShellAssessment = assess_shell(line, zsh_home.workspace, zsh_home.workspace, (zsh_home.protected,), ())
    assert reason in assessment.reasons


@pytest.mark.parametrize("line", _LINES)
def test_a_yolo_run_asks_nothing_new_for_what_the_guard_now_reads(line: str, zsh_home: _Home) -> None:
    """The guard asks in the presets that ask; a yolo run waives its question as before (item 443)."""
    assessment: ShellAssessment = assess_shell(line, zsh_home.workspace, zsh_home.workspace, (zsh_home.protected,), ())
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags
    )
    assert route_for(decide(info, "task", False, "balanced", [], None), "yolo") == "waived"


@pytest.mark.parametrize("line", ["gcc -c main.c", "ffmpeg -i a.mp4 -c copy b.mp4", "docker run -e A=b img"])
def test_a_one_word_value_of_an_unknown_program_is_no_line(line: str, zsh_home: _Home) -> None:
    assert _verdict(line, zsh_home, "balanced") == "allow"

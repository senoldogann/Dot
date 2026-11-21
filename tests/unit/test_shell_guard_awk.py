"""awk programs whose output target is worked out while they run (item 1303): a ``>``, ``>>`` or ``|`` target of a
``print`` that is not one string literal asks like any other write the guard cannot place, a pipe into a shell that
reads its input asks, and a program that prints to standard output, reads files or writes to a literal path keeps its
verdict. Every line is judged, never run, as it would run in the workspace."""

from pathlib import Path

import pytest

from dotagent.policy import paths
from dotagent.policy.engine import decide
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Decision


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


# A target that is not one string literal: a variable, a field, an environment value, a concatenation, a call.
_COMPUTED: list[str] = [
    """awk 'BEGIN{printf "x" > ENVIRON["HOME"] "/.zshrc"}'""",
    """awk 'BEGIN{printf "x" > ENVIRON["HOME"] "/.zshrc"}' f""",
    """gawk 'BEGIN { print "x" >> ENVIRON["HOME"] "/.zshrc" }'""",
    """mawk 'BEGIN { print "x" > (ENVIRON["HOME"] "/.zshrc") }'""",
    """nawk 'BEGIN { print "x" > ENVIRON["OUT"] }'""",
    """awk '{print > ENVIRON["OUT"]}' f""",
    "awk '{print > $1}' f",
    """awk '{print > $1 ".txt"}' f""",
    """awk '{print $1 > "out/" $2 ".txt"}' f""",
    """awk '{print > FILENAME ".out"}' f""",
    "awk '{print >> f}' f",
    "awk -v f=x '{print > f}' f",
    "awk -v f=x -e '{print > f}' g",
    "awk '{print $1 >> FILENAME}' f",
    """awk '{print > (FILENAME ".bak")}' f""",
    """awk '{printf "%s\\n", $1 > "x" NR}' f""",
    """awk '{printf("%s\\n", $1) > d "/o.txt"}' f""",
    """awk '{if ($1 > 3) print $1 > $2; else print "no"}' f""",
    """awk '{print "a"; print "b" > "out" ; print "c" > $1}' f""",
    "awk '{print $1 > 5}' f",
    # A pipe into a program whose name is worked out, or into a shell that runs the text awk prints.
    "awk '{print | cmd}' f",
    """awk '{print | "sort " $1}' f""",
    """awk '{print | "tee " $1}' f""",
    """awk '{print | "sh"}' f""",
    """awk '{print | "bash"}' f""",
    """awk '{print | "bash -s"}' f""",
    """awk '{print | "/bin/sh"}' f""",
    """awk '{print | "python3 -"}' f""",
    # The same pipes through a wrapper: the program is the one the wrapper runs.
    """awk '{print | "env sh"}' f""",
    """awk '{print | "exec bash"}' f""",
    """awk '{print | "nohup sh"}' f""",
    """awk '{print | "env python3 -"}' f""",
    """awk '{print | "env -S \\"sh -s\\""}' f""",
    """awk '{print | "timeout 5 sh"}' f""",
    """awk '{print | "nice -n 5 env FOO=1 bash"}' f""",
    # A literal target the guard already judged.
    """awk 'BEGIN{printf "x" > "/Users/x/.zshrc"}'""",
    """awk '{print | "rm -rf ~/Documents"}' f""",
    """awk 'BEGIN { "rm -rf ~/Documents" | getline x }'""",
]


@pytest.mark.parametrize("line", _COMPUTED)
def test_an_awk_target_that_is_worked_out_while_it_runs_is_asked_about(line: str, home: Path) -> None:
    assessment: ShellAssessment = _assess(line, home)
    assert assessment.risk in ("medium", "high"), assessment.reasons
    assert _verdict(line, home).verdict == "ask", assessment.reasons


# Programs that print to standard output, read files, compare, or write to a literal path, and the pipes they had.
_ORDINARY: list[str] = [
    "awk '{print $1}' file.txt",
    "awk -F: '{print $1}' /etc/passwd",
    "awk 'BEGIN{print 1}'",
    "awk '{sum += $2} END {print sum}' data.txt",
    "awk 'NR>1 {print $1, $3}' data.txt",
    """awk '{ getline line < "other.txt"; print line }' f""",
    "awk '{ getline line < file }' f",
    """awk '{print > "out.txt"}' f""",
    """awk '{print >> "out.txt"}' f""",
    """awk '{print > ("out.txt")}' f""",
    """awk '{print $1, $2 > "out.txt"}' f""",
    """awk '{printf("%s\\n", $1) > "out.txt"}' f""",
    """awk '{ print > "a" ; print > "b" }' f""",
    """awk '{if ($1 > 3) print $1 > "big.txt"; else print $1 > "small.txt"}' f""",
    """awk '{print > "out.txt"; close("out.txt")}' f""",
    """awk '{print "a" "b" > "c"}' f""",
    """awk '{print > "/dev/stderr"}' f""",
    """awk '{print $1, $2 > "/dev/stderr"}' f""",
    """awk '{print > "/dev/stdout"}' f""",
    """awk 'BEGIN { print "a" > "/dev/null" }'""",
    "awk '/foo/ {n++} END {print n}' f",
    "awk '$1 > 5 && $2 < 3' f",
    "awk '$2 > 3 || $1 < 2' f",
    "awk 'length($0) > 80' f",
    "awk 'NR % 2 == 0 && $3 >= 10 { print }' f",
    "awk '$1 > 3 { print $2 }' f",
    "awk '{ if ($1 > 5) print $1 }' f",
    "awk 'BEGIN { x = 1 > 0 }'",
    "awk '{ print ($1 > 3) }' f",
    """awk '{ print ($1 > 3) ? "a" : "b" }' f""",
    "awk '{ a[$1] > 1 }' f",
    "awk '{ print (a > b) ? $1 : $2 }' f",
    "awk -v x=1 '{print $x}' f",
    """awk '{printf "%s\\n", $1}' f""",
    """awk 'BEGIN{FS=","; OFS="\\t"} {print $1,$2}' f""",
    "gawk '{print $1}' f",
    "mawk '{print $1}' f",
    "awk -f prog.awk f",
    """awk '{print | "sort"}' f""",
    """awk '{print | "sort -n"}' f""",
    """awk '{print | "cat 1>&2"}' f""",
    """awk '{print | "tee out.txt"}' f""",
    """awk '{print | "sh script.sh"}' f""",
    """awk '{print | "python3 script.py"}' f""",
    """awk '{print | "env sh script.sh"}' f""",
    """awk '{print | "env FOO=1 sort"}' f""",
    """awk '{print | "nohup sort -n"}' f""",
    """awk '{print | "exec python3 script.py"}' f""",
    """awk '{print | "sh -c \\"echo hi\\""}' f""",
    """awk '{ "date" | getline d; print d }' f""",
    """awk 'BEGIN { while (("ls" | getline f) > 0) print f }'""",
]


@pytest.mark.parametrize("line", _ORDINARY)
def test_an_awk_program_with_a_literal_or_no_target_keeps_its_verdict(line: str, home: Path) -> None:
    assessment: ShellAssessment = _assess(line, home)
    assert assessment.risk in ("safe", "low"), assessment.reasons
    assert _verdict(line, home).verdict == "allow", assessment.reasons


def test_a_pipe_into_a_shell_is_read_through_the_wrappers_in_front_of_it(home: Path) -> None:
    """The check read the first word of the command only, so ``print | "env sh"`` ran the text awk printed while
    ``print | "sh"`` asked (item 1303)."""
    for command in ("sh", "env sh", "exec bash", "nohup sh", "env python3 -", "env -S 'sh -s'", "timeout 5 sh"):
        line: str = f"""awk '{{print | "{command}"}}' f"""
        assert _verdict(line, home).verdict == "ask", line

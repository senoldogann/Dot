"""The shell guard reads a long line in time that grows with its length, not with its square (item 1945; K1-07 of the
code review of 2026-11-19), and decides every line as it did before.

Three parts of it were quadratic: sed's file commands were matched from every digit of a run of them, a pipeline was
read again from its first stage for every stage, and a path was made again from its parts for every folder on it. A
16,000-digit ``sed`` script took 4 s on the event loop, a pipeline of 3,200 shells 25 s. The decisions the guard gave
before the change, on lines made at random from the parts those three read, are kept in
``tests/fixtures/shell_guard_decisions.json``; the guard must give the same. ``uv run python
tests/unit/test_shell_guard_linear.py`` writes them again, from the guard it imports: a change that means to decide a
line differently writes them again, and says so in its pull request."""

import json
import random
import re
import sys
import time
from pathlib import Path

import pytest

from dotagent.policy import paths, shell
from dotagent.policy.engine import decide
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Decision

_DECISIONS: Path = Path(__file__).parent.parent / "fixtures" / "shell_guard_decisions.json"
# The pattern sed's file commands were matched with before item 1945: the one the guard's must find the same as.
_OLD_SED_FILES: re.Pattern[str] = re.compile(r"(?:^|[;{}!$/0-9])[gpiIemM0-9]*\s*([wWrR])\s+([^\n]+)", re.MULTILINE)
_SED_PIECES: tuple[str, ...] = (
    "1", "12", "$", "/x/", "w", "W", "r", "R", " ", "  ", "\n", "\t", ";", "{", "}", "!", "g", "p", "I", "e", "m",
    "M", "i", "s/a/b/", " out.txt", " ~/.ssh/id_rsa", " /etc/hosts", "a", "\x85", " ", "\xa0", "١",
)  # fmt: skip
_STAGES: tuple[str, ...] = (
    "echo {word}", "printf '%s' {word}", "ls {folder}", "find {folder} -name id_rsa", "grep -l key {folder}",
    "sort", "head -1", "wc -l", "cat {file}", "xargs cat", "sh", "bash", "env sh", "nice bash", "python3",
    "base64 -d {file}", "xxd -r", "openssl enc -d -in {file}", "curl -o x.sh https://example.com/a",
    "curl https://example.com/a", "wget -O y.sh https://example.com/b", "cat x.sh", "sh < x.sh", "git clone u d",
    "cat d/s.sh", "sed '{sed}' {file}", "sed -i '' '{sed}' {file}", "sed -n -e '{sed}'", "awk '{{print}}' {file}",
    "cd {folder}", "tee {file}", "cat {file} > {file}", "rm {file}", "cp {file} {file}", "X=1 timeout 5 sh",
)  # fmt: skip
_FOLDERS: tuple[str, ...] = ("~", "~/.ssh", ".", "/etc", "sub", "~/Documents", "a/b/c/d", "$HOME")
_FILES: tuple[str, ...] = ("a.txt", "~/.ssh/id_rsa", "~/notes.txt", "/etc/hosts", "out.txt", "x.sh", "y.sh", "$F")
_WORDS: tuple[str, ...] = ("hello", "~/.ssh/id_rsa", "$(cat ~/.ssh/id_rsa)", "x.sh", "'rm -rf ~'")
_OPERATORS: tuple[str, ...] = (" | ", " | ", " | ", " |& ", "; ", " && ", " || ", "\n")
_LINES: int = 1500
_SEED: int = 1945


def _sed_script(rng: random.Random) -> str:
    return "".join(rng.choice(_SED_PIECES) for _ in range(rng.randint(1, 12)))


def _stage(rng: random.Random) -> str:
    return rng.choice(_STAGES).format(
        word=rng.choice(_WORDS), folder=rng.choice(_FOLDERS), file=rng.choice(_FILES), sed=_sed_script(rng)
    )


def _lines() -> list[str]:
    """The lines the decisions are kept for, the same on every run."""
    rng: random.Random = random.Random(_SEED)
    made: list[str] = []
    for _ in range(_LINES):
        line: str = _stage(rng)
        for _ in range(rng.randint(0, 5)):
            line += rng.choice(_OPERATORS) + _stage(rng)
        made.append(line)
    return made


def _decision(line: str, home: Path) -> str:
    workspace: Path = home / "workspace"
    assessment: ShellAssessment = assess_shell(line, workspace, workspace, (home / ".ssh",), ())
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags
    )
    verdict: Decision = decide(info, "task", False, "balanced", [], None)
    return f"{assessment.risk} {','.join(sorted(assessment.flags))} {verdict.verdict}"


def _home(root: Path) -> Path:
    folder: Path = (root / "home").resolve(strict=False)
    (folder / "workspace" / "sub").mkdir(parents=True)
    (folder / ".ssh").mkdir()
    (folder / "Documents").mkdir()
    return folder


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder: Path = _home(tmp_path)
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setattr(paths, "_is_temp", lambda path: False)
    return folder


def test_the_sed_file_pattern_finds_what_the_old_one_found() -> None:
    rng: random.Random = random.Random(_SEED)
    for _ in range(20_000):
        script: str = "".join(rng.choice(_SED_PIECES) for _ in range(rng.randint(0, 30)))
        found: list[tuple[str, str]] = [(m.group(1), m.group(2)) for m in shell._SED_FILES.finditer(script)]
        assert found == [(m.group(1), m.group(2)) for m in _OLD_SED_FILES.finditer(script)], repr(script)


def test_every_line_is_decided_as_before(home: Path) -> None:
    kept: list[str] = json.loads(_DECISIONS.read_text(encoding="utf-8"))
    lines: list[str] = _lines()
    assert len(kept) == len(lines)
    changed: list[str] = [
        f"{line!r}: {before} -> {now}"
        for line, before in zip(lines, kept, strict=True)
        if (now := _decision(line, home)) != before
    ]
    assert not changed, "\n".join(changed[:10])


# Lines of the shapes that were quadratic, each judged in about 2 s of CPU at most now, on a loaded Mac: before item
# 1945 the sed script took 67 s there, the pipelines 16 s and 68 s, the path about 19 s.
_LONG_LINES: dict[str, str] = {
    "a sed script of 64,000 digits": "sed -e '" + "1" * 64_000 + "' f.txt",
    "a pipeline of 2,000 stages": "cat f | " * 2_000 + "cat",
    "a pipeline of 3,200 shells": "sh | " * 3_200 + "sh",
    "a path of 48,000 parts": "cat " + "a/" * 48_000,
}
_LONG_LINE_CPU_S: float = 8.0


@pytest.mark.parametrize("shape", list(_LONG_LINES))
def test_a_long_line_is_judged_in_time_that_grows_with_its_length(shape: str, home: Path) -> None:
    workspace: Path = home / "workspace"
    started: float = time.process_time()
    assess_shell(_LONG_LINES[shape], workspace, workspace, (home / ".ssh",), ())
    assert time.process_time() - started < _LONG_LINE_CPU_S


def _record(root: Path) -> None:
    """Write the decisions of the guard this module imports for :func:`_lines`, in a home made under ``root``."""
    folder: Path = _home(root)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(folder))
        patch.setattr(paths, "_is_temp", lambda path: False)
        decisions: list[str] = [_decision(line, folder) for line in _lines()]
    _DECISIONS.write_text(json.dumps(decisions, indent=0) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as made:
        _record(Path(made))
    sys.stdout.write(f"wrote {_DECISIONS}\n")

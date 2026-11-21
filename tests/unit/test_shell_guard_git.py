"""What git runs or writes beyond its own repository (item 1301): a file git runs as code (a repository's ``config`` and
``hooks``) is asked about when a file tool writes it, and the options that make git write a file or run a command line
are judged as a redirect and as that line. A shell line that writes there by a redirect or a copy is not judged
differently from any workspace write (not covered, see roadmap/1301.md). Every line is judged, never run, as it would
run in the workspace; ``home`` stands for the owner's home folder and holds the protected ``.ssh``."""

from pathlib import Path

import pytest

from dotagent.policy import paths
from dotagent.policy.engine import decide
from dotagent.policy.paths import CHANGES_GIT_CODE, runs_as_git_code
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Decision
from dotagent.tools.files import write_action


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder: Path = (tmp_path / "home").resolve(strict=False)
    (folder / "workspace" / "proj" / ".git" / "hooks").mkdir(parents=True)
    (folder / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setattr(paths, "_is_temp", lambda path: False)
    return folder


def _assess(line: str, home: Path) -> ShellAssessment:
    workspace: Path = home / "workspace"
    return assess_shell(line, workspace, workspace, (home / ".ssh",), ())


def _decision(line: str, home: Path, preset: str = "balanced") -> Decision:
    assessment: ShellAssessment = _assess(line, home)
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags
    )
    return decide(info, "task", False, preset, [], None)  # type: ignore[arg-type]


# What does not run as code, or what git itself writes: the verdict these lines had stays.
_UNCHANGED: list[str] = [
    "echo hi > proj/.git/HEAD",
    "echo hi > proj/.git/refs/heads/config",
    "echo hi > proj/.git/info/exclude",
    "echo hi > proj/.gitignore",
    "echo '*.png filter=lfs' > proj/.gitattributes",
    "echo hi > proj/config",
    "echo hi > proj/hooks.txt",
    "echo hi > proj/hooks/pre-commit",
    "cat proj/.git/config",
    "cp -r src backup",
    "cp -t out a b",
    "cp a.txt proj/",
    "cp -r proj/src proj-backup/",
    "cp .gitignore proj/",
    "cp config.json proj/",
    "mv a.txt proj/",
    "mv .gitignore proj/",
    "mv hooks proj/",
    "mv config old-config",
    "rsync -a src/ out/",
    "install -m 755 tool bin/",
    "ditto src out",
    "ln -s src link",
    "unzip x.zip -d out",
    "tar xf x.tar -C out",
    "tar -xzf x.tgz",
    "cp ex proj/.git/info/exclude",
    "cp ex proj/.git/info/",
    "git init r && echo x > r/config",
    "git init --bare r && echo x > r/HEAD",
    "git init --bare r && echo x > r/description",
    "git clone https://example.com/a.git && echo x > a/config",
    "cp proj/.git/hooks/pre-commit.sample backup.txt",
    "touch proj/.git/hooks/pre-commit",
    "git config user.name Senol",
    "git init",
    "git init proj",
    "git commit -m x",
    "git status",
    "git diff --output=out.patch",
    "git archive -o out.tar HEAD",
    "git format-patch -o patches HEAD~1",
    "git bundle create out.bundle HEAD",
    "git checkout-index --prefix=out/ -a",
    "git diff --stat",
    "git log --oneline -5",
]


@pytest.mark.parametrize("line", _UNCHANGED)
def test_a_line_that_runs_no_file_of_git_keeps_its_verdict(line: str, home: Path) -> None:
    decision: Decision = _decision(line, home)
    assert decision.verdict == "allow", (decision, _assess(line, home).reasons)
    assert "self_change" not in _assess(line, home).flags


# Git given a place outside the workspace to write, or a command line to run: judged as a redirect to it is, and as
# the same line under ``bash -c`` is.
_GIT_ASKS: list[str] = [
    "git diff --output=~/out.patch",
    "git log --output=~/out.txt",
    "git show --output ~/out.txt HEAD",
    "git archive -o ~/.zshrc HEAD",
    "git archive --output=~/.zshrc HEAD",
    "git format-patch -o ~/Desktop HEAD~1",
    "git bundle create ~/out.bundle HEAD",
    "git bundle create ../out.bundle HEAD",
    "git checkout-index --prefix=~/out/ -a",
    "git rebase --exec 'rm -rf ~/Documents' HEAD~2",
    "git rebase -x 'rm -rf ~/Documents' HEAD~2",
    "git rebase --exec='rm -rf ~/Documents' HEAD~2",
    "git rebase -i -x 'rm -rf ~/Documents' HEAD~2",
    "git bisect run sh -c 'rm -rf ~/Documents'",
    "git bisect run rm -rf ~/Documents",
    "git submodule foreach 'rm -rf ~/Documents'",
    "git submodule foreach --recursive 'rm -rf ~/Documents'",
    "git submodule foreach rm -rf ~/Documents",
    "git filter-branch --tree-filter 'rm -rf ~/Documents' HEAD",
    "git filter-branch --index-filter 'rm -rf ~/Documents' HEAD",
    "git filter-branch --msg-filter 'rm -rf ~/Documents' HEAD",
    "git filter-branch --env-filter='rm -rf ~/Documents' HEAD",
    "git grep -O'rm -rf ~/Documents' foo",
    "git grep --open-files-in-pager='rm -rf ~/Documents' foo",
    "git difftool -x 'rm -rf ~/Documents'",
    "git difftool --extcmd='rm -rf ~/Documents'",
    "git fetch --upload-pack='rm -rf ~/Documents' origin",
    "git push --receive-pack='rm -rf ~/Documents' origin main",
    "git -C proj rebase --exec 'rm -rf ~/Documents' HEAD~2",
]


@pytest.mark.parametrize("line", _GIT_ASKS)
def test_git_given_a_place_to_write_or_a_line_to_run_is_judged_by_it(line: str, home: Path) -> None:
    decision: Decision = _decision(line, home)
    assert decision.verdict == "ask", (decision, _assess(line, home).reasons)


def test_git_told_to_write_in_a_protected_folder_is_refused(home: Path) -> None:
    assert _decision("git diff --output=~/.ssh/out", home).verdict == "deny"


# The same options, harmless: the workspace, a command that changes nothing.
_GIT_ALLOWS: list[str] = [
    "git diff --output=out.patch",
    "git show --output=out.txt HEAD",
    "git rebase -i HEAD~3",
    "git rebase --exec make HEAD~2",
    "git rebase --exec 'make test' HEAD~2",
    "git rebase --continue",
    "git bisect start",
    "git bisect good",
    "git bisect run ./test.sh",
    "git bisect run make test",
    "git submodule foreach git pull",
    "git submodule foreach --recursive 'git status'",
    "git filter-branch --index-filter 'git rm --cached --ignore-unmatch x' HEAD",
    "git filter-branch --subdirectory-filter sub HEAD",
    "git grep -n foo",
    "git grep -O foo",
    "git grep -Oless foo",
    "git difftool -t vimdiff",
    "git fetch origin",
]


@pytest.mark.parametrize("line", _GIT_ALLOWS)
def test_the_same_git_options_with_a_harmless_value_stay_allowed(line: str, home: Path) -> None:
    decision: Decision = _decision(line, home)
    assert decision.verdict == "allow", (decision, _assess(line, home).reasons)


@pytest.mark.parametrize(
    ("relative", "runs"),
    [
        ("proj/.git/config", True),
        ("proj/.git/hooks/pre-commit", True),
        ("proj/.git/hooks", True),
        ("proj/.GIT/HOOKS/pre-commit", True),
        ("proj/.git/modules/sub/config", True),
        ("proj/.g*/config", True),
        ("proj/.gi?/config", True),
        ("proj/.gi?/hooks/pre-commit", True),
        ("proj/.[g]it/config", True),
        ("proj/*/config", True),
        ("proj/.git/ho*/pre-commit", True),
        ("proj/.git/hooks/*", True),
        ("proj/.git/modules/sub/hooks/pre-push", True),
        ("proj/.git/config.worktree", True),
        ("proj/.git/worktrees/w/config.worktree", True),
        ("proj/.git/HEAD", False),
        ("proj/.git/index", False),
        ("proj/.git/info/exclude", False),
        ("proj/.git/refs/heads/hooks", False),
        ("proj/.git/refs/heads/config", False),
        ("proj/.gitmodules", False),
        ("proj/.github/config", False),
        ("proj/.git/objects/ab/hooks", False),
        ("proj/.git", False),
        ("proj/.gitignore", False),
        ("proj/.gitattributes", False),
        ("proj/config", False),
        ("proj/hooks/pre-commit", False),
    ],
)
def test_the_file_tools_ask_for_what_git_runs_and_for_nothing_else_in_a_repository(
    relative: str, runs: bool, home: Path
) -> None:
    workspace: Path = home / "workspace"
    path: Path = workspace / relative
    assert runs_as_git_code(path) is runs
    info: ActionInfo = write_action("write_file", path, workspace, (home / ".ssh",), (), "Write")
    decision: Decision = decide(info, "task", False, "balanced", [], None)
    if runs:
        assert (info.risk, info.flags) == ("high", ("self_change",))
        assert CHANGES_GIT_CODE in info.summary
        assert (decision.verdict, decision.source) == ("ask", "own_files")
    else:
        assert (info.risk, info.flags) == ("low", ())
        assert decision.verdict == "allow"


def test_a_repository_outside_the_workspace_is_asked_about_as_a_change_of_what_git_runs(home: Path) -> None:
    path: Path = home / "Desktop" / "other" / ".git" / "hooks" / "pre-commit"
    info: ActionInfo = write_action("edit_file", path, home / "workspace", (home / ".ssh",), (), "Edit")
    assert (info.risk, info.flags) == ("high", ("self_change",))
    protected: ActionInfo = write_action(
        "edit_file", home / ".ssh" / "x" / ".git" / "config", home / "workspace", (home / ".ssh",), (), "Edit"
    )
    assert protected.flags == ("secret_access",)


def test_a_bare_repository_holds_its_config_and_hooks_as_a_dot_git_folder_does(home: Path) -> None:
    workspace: Path = home / "workspace"
    bare: Path = workspace / "repo.git"
    (bare / "hooks").mkdir(parents=True)
    (bare / "objects").mkdir()
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    for relative in ("repo.git/hooks/post-receive", "repo.git/config", "repo.git/hooks"):
        info: ActionInfo = write_action("write_file", workspace / relative, workspace, (home / ".ssh",), (), "Write")
        assert (info.risk, info.flags) == ("high", ("self_change",)), relative
    (workspace / "settings" / "hooks").mkdir(parents=True)
    for relative in ("settings/hooks/a.txt", "settings/config", "repo.git/HEAD", "repo.git/description"):
        info = write_action("write_file", workspace / relative, workspace, (home / ".ssh",), (), "Write")
        assert (info.risk, info.flags) == ("low", ()), relative

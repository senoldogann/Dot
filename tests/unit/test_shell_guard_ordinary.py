"""The ordinary developer commands keep the verdict the shell guard gave them before items 1301 to 1303 (git's
output and command options, the programs that run a command line, awk's computed targets): the guard reads the options
that write a file or hand over a command line, not the programs that carry them. Every line here is judged, never run,
as it would run in the workspace, and is allowed in the balanced preset with the risk it had."""

from pathlib import Path

import pytest

from dotagent.policy import paths
from dotagent.policy.engine import decide
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Verdict

# (line, the risk the guard gave it on commit 688f63322).
_ORDINARY: list[tuple[str, str]] = [
    ("git status", "safe"),
    ("git status --short", "safe"),
    ("git diff", "safe"),
    ("git diff --stat HEAD~1", "safe"),
    ("git diff --name-only", "safe"),
    ("git log", "safe"),
    ("git log --oneline -20", "safe"),
    ("git log --pretty=format:%h", "safe"),
    ("git show HEAD", "safe"),
    ("git show HEAD:README.md", "safe"),
    ("git add -A", "low"),
    ("git commit -m 'fix: x'", "low"),
    ("git commit --amend --no-edit", "low"),
    ("git push", "medium"),
    ("git push origin main", "medium"),
    ("git rebase -i HEAD~3", "low"),
    ("git rebase main", "low"),
    ("git rebase --continue", "low"),
    ("git rebase --abort", "low"),
    ("git bisect start", "low"),
    ("git bisect good", "low"),
    ("git bisect bad", "low"),
    ("git bisect reset", "low"),
    ("git checkout -b feature", "low"),
    ("git switch main", "low"),
    ("git stash", "safe"),
    ("git stash pop", "safe"),
    ("git branch -a", "safe"),
    ("git fetch origin", "safe"),
    ("git pull --rebase", "low"),
    ("git config user.name 'Senol'", "safe"),
    ("git config --get user.email", "safe"),
    ("git init", "low"),
    ("git clone https://github.com/a/b.git", "low"),
    ("git grep -n foo", "safe"),
    ("git grep -i -e foo", "safe"),
    ("git archive HEAD", "low"),
    ("git format-patch -1", "low"),
    ("git bundle verify x.bundle", "low"),
    ("git submodule status", "low"),
    ("git submodule update --init", "low"),
    ("git filter-branch --help", "low"),
    ("git blame README.md", "safe"),
    ("git tag v1", "safe"),
    ("git cherry-pick abc123", "low"),
    ("git worktree list", "low"),
    ("sqlite3 app.db 'select * from users'", "low"),
    ("sqlite3 app.db '.tables'", "low"),
    ("sqlite3 app.db '.schema users'", "low"),
    ("sqlite3 -header -csv app.db 'select 1'", "low"),
    ("sqlite3 app.db '.dump'", "low"),
    ("sqlite3 :memory: 'select 1'", "low"),
    ("sqlite3 app.db .mode csv", "low"),
    ("vim notes.txt", "low"),
    ("vim", "low"),
    ("nvim README.md", "low"),
    ("vi file.txt", "low"),
    ("vim -R file.txt", "low"),
    ("vim +10 file.txt", "low"),
    ("vim -u NONE file.txt", "low"),
    ("emacs notes.txt", "low"),
    ("emacs -nw file.txt", "low"),
    ("emacs --version", "low"),
    ("tar -czf out.tgz src", "low"),
    ("tar czf out.tgz src", "low"),
    ("tar -xzf in.tgz", "low"),
    ("tar xf in.tar -C out", "low"),
    ("tar -tf in.tar", "low"),
    ("tar -cf out.tar --exclude=.git .", "low"),
    ("gtar -xzf in.tgz", "low"),
    ("bsdtar -xf in.zip", "low"),
    ("tar --create --file=out.tar src", "low"),
    ("awk '{print $1}' file.txt", "safe"),
    ("awk -F: '{print $1}' /etc/passwd", "safe"),
    ("awk 'BEGIN{print 1}'", "safe"),
    ("awk '{sum += $2} END {print sum}' data.txt", "safe"),
    ("awk 'NR>1 {print $1, $3}' data.txt", "safe"),
    ("awk '{ getline line < \"other.txt\"; print line }' f", "safe"),
    ("awk '{print > \"out.txt\"}' f", "safe"),
    ("awk '{print >> \"out.txt\"}' f", "safe"),
    ("awk '/foo/ {n++} END {print n}' f", "safe"),
    ("awk '{print | \"sort\"}' f", "safe"),
    ("gawk '{print $1}' f", "safe"),
    ("mawk '{print $1}' f", "safe"),
    ("awk -v x=1 '{print $x}' f", "safe"),
    ("awk '{printf \"%s\\n\", $1}' f", "safe"),
    ('awk \'BEGIN{FS=","; OFS="\\t"} {print $1,$2}\' f', "safe"),
    ("awk '$1 > 5 && $2 < 3' f", "safe"),
    ("awk '{print > \"/dev/stderr\"}' f", "safe"),
    ("awk '{print | \"cat 1>&2\"}' f", "safe"),
    ("jq . data.json", "safe"),
    ("jq '.a.b' data.json", "safe"),
    ("jq -r '.[] | .name' data.json", "safe"),
    ("jq -c '.' < data.json", "safe"),
    ("printenv HOME", "safe"),
    ("printenv PATH", "safe"),
    ("printenv USER", "safe"),
    ("rsync -a src/ out/", "medium"),
    ("rsync -a src/ out", "medium"),
    ("rsync -a --delete src/. out/", "medium"),
    ("ditto src out", "low"),
    ("cp -R src/ out", "low"),
    ("cp -R src/. out/", "low"),
    ("cp -r src backup/", "low"),
    ("tar -cf out.tar src", "low"),
]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder: Path = (tmp_path / "home").resolve(strict=False)
    (folder / "workspace").mkdir(parents=True)
    (folder / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setattr(paths, "_is_temp", lambda path: False)
    return folder


@pytest.mark.parametrize(("line", "risk"), _ORDINARY)
def test_an_ordinary_developer_command_keeps_its_verdict(line: str, risk: str, home: Path) -> None:
    workspace: Path = home / "workspace"
    assessment: ShellAssessment = assess_shell(line, workspace, workspace, (home / ".ssh",), ())
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags
    )
    verdict: Verdict = decide(info, "task", False, "balanced", [], None).verdict
    assert (assessment.risk, verdict) == (risk, "allow"), assessment.reasons

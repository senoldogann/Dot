"""The release names the shell guard reads (item 850): a deploy to production, a pull request merged, a push that names
the main branch carry ``release``, which asks the owner in a chat Dot opened on its own initiative and changes nothing
elsewhere; their look-alikes that build, preview or read do not carry it. Nothing here runs: each line is only
judged."""

from pathlib import Path

import pytest

from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo
from dotagent.tools.shell import ShellArgs, _classify_shell

RELEASES: tuple[str, ...] = (
    # The line of 2026-10-30 that put a local build live (item 850), through the runner it went through.
    "cd web && npx wrangler pages deploy dist --project-name=ainotepad --branch=main",
    "wrangler deploy",
    "wrangler publish",
    "wrangler versions deploy",
    "bunx wrangler pages publish dist",
    "vercel --prod",
    "vercel deploy --prod",
    "vercel deploy --target production",
    "vercel promote https://site-abc.vercel.app",
    "netlify deploy --prod --dir dist",
    "netlify deploy -p",
    "firebase deploy --only hosting",
    "fly deploy",
    "flyctl deploy --remote-only",
    "gh pr merge 100 --squash",
    "gh pr merge --auto --rebase",
    "git push origin main",
    "git push origin HEAD:main",
    "git push origin feature:refs/heads/master",
    "git -C repo push --force origin main",
    "git push --all origin",
    "git push --mirror backup",
    # A runner's pinned version is the same tool (the independent review of item 850).
    "npx wrangler@3 deploy",
    "npx wrangler@latest pages deploy dist",
    "bunx wrangler@4 deploy",
    "npx vercel@latest --prod",
    "pnpm dlx vercel@latest --prod",
)
NOT_RELEASES: tuple[str, ...] = (
    "wrangler dev",
    "wrangler pages dev dist",
    "npx wrangler whoami",
    "vercel",
    "vercel deploy",
    "vercel build",
    "netlify deploy --dir dist",
    "netlify status",
    "firebase emulators:start",
    "fly status",
    "gh pr view 100",
    "gh pr create --title Fix --body Body",
    "git push origin feature",
    "git push origin main:feature",
    "git push",
    "git commit -m 'merge to main'",
    "npm run build:local",
)


def _info(line: str, workspace: Path) -> ActionInfo:
    return _classify_shell(ShellArgs(command=line), workspace, (), ())


@pytest.mark.parametrize("line", RELEASES)
def test_a_line_that_puts_a_change_live_carries_release(line: str, tmp_path: Path) -> None:
    assert "release" in _info(line, tmp_path).flags, line


@pytest.mark.parametrize("line", NOT_RELEASES)
def test_its_look_alikes_carry_no_release(line: str, tmp_path: Path) -> None:
    assert "release" not in _info(line, tmp_path).flags, line


def test_a_push_to_main_keeps_its_upload_too(tmp_path: Path) -> None:
    """``release`` is added to what the line was judged as: a push still asks as an upload once the run read outside
    content (item 725)."""
    info: ActionInfo = _info("git push origin main", tmp_path)
    assert {"release", "upload"} <= set(info.flags)


@pytest.mark.parametrize("preset", ["aggressive", "balanced", "cautious"])
def test_release_alone_asks_nothing_outside_a_chat_of_the_initiative(preset: str, tmp_path: Path) -> None:
    """What every preset made of ``wrangler pages deploy`` before item 850 it makes now: the flag is for the gate of a
    chat Dot opened on its own initiative alone."""
    line: str = "wrangler pages deploy dist"
    info: ActionInfo = _info(line, tmp_path)
    without: ActionInfo = info.model_copy(update={"flags": tuple(flag for flag in info.flags if flag != "release")})
    assert decide(info, "task", False, preset, [], None) == decide(without, "task", False, preset, [], None)


def test_the_card_knows_the_initiatives_question_by_its_words() -> None:
    """The approval card offers no rule for the question of a chat of Dot's initiative, which no rule answers, and finds
    it by the words its summary ends with (approvals.js): they must be the engine's (the pre-push review of item 850:
    the flag alone took the offer from the owner's own chats, where a rule does apply)."""
    from dotagent.policy.engine import _INITIATIVE_REASON

    script: str = (Path(__file__).resolve().parents[2] / "src/dotagent/server/static/js/approvals.js").read_text()
    words: str = script.split('const INITIATIVE_WORDS = "', 1)[1].split('"', 1)[0]
    assert _INITIATIVE_REASON.startswith(words), (words, _INITIATIVE_REASON)
    assert '"release"' not in script.split("const GUARDED_FLAGS", 1)[1].split("]);", 1)[0]

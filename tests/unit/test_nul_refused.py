"""A NUL character in what a step names is refused for what the step gave, never a crash (item 727, the outside
review of 2026-10-27): an address names no host, a path no file and a command line no command, and an OpenCode run
that is asked such a step refuses it and goes on."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.browser.safety import UrlVerdict, browser_host, check_url
from dotagent.core.errors import ToolInputError
from dotagent.opencode.events import PermissionAsked
from dotagent.opencode.run import _Follow
from dotagent.policy.paths import expand_path
from dotagent.tools.shell import ShellArgs, _classify_shell


def test_an_address_with_a_nul_names_no_host() -> None:
    assert browser_host("a\x00.com") is None
    verdict: UrlVerdict = check_url("http://a%00.com/", [], [])
    assert not verdict.allowed and "cannot be read" in verdict.reason


def test_a_path_or_a_command_line_with_a_nul_is_refused_for_what_it_gave(tmp_path: Path) -> None:
    with pytest.raises(ToolInputError, match="a path cannot hold a NUL character"):
        expand_path("notes\x00.md", tmp_path)
    with pytest.raises(ToolInputError, match="a command line cannot hold a NUL character"):
        _classify_shell(ShellArgs(command="echo \x00"), tmp_path, (), ())
    with pytest.raises(ToolInputError, match="a path cannot hold a NUL character"):
        _classify_shell(ShellArgs(command="ls", cwd="a\x00b"), tmp_path, (), ())


class _Server:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, str]] = []

    async def reply(self, request_id: str, directory: Path, answer: str, message: str | None = None) -> None:
        self.replies.append((request_id, answer, message or ""))


@pytest.mark.parametrize(
    ("permission", "metadata"),
    [("bash", {"command": "echo \x00"}), ("edit", {"filepath": "notes\x00.md"})],
)
def test_an_opencode_step_with_a_nul_is_refused_and_the_run_goes_on(
    permission: str, metadata: dict[str, str], tmp_path: Path
) -> None:
    """The review's finding: the step's ValueError failed the whole OpenCode run instead of refusing that step."""
    server: _Server = _Server()
    runtime = SimpleNamespace(protected=(), dot_paths=())
    follow: _Follow = _Follow(
        agent=SimpleNamespace(runtime=runtime),  # type: ignore[arg-type]
        server=server,  # type: ignore[arg-type]
        session_id="ses_1",
        directory=tmp_path,
        model="opencode/free",
        variant=None,
        vision=False,
        queue=asyncio.Queue(),
        door="dot_1a2b3c4d_",
    )
    event: PermissionAsked = PermissionAsked(
        request_id="per_1", permission=permission, patterns=(), metadata=dict(metadata), call_id="call_1"
    )
    asyncio.run(follow.decide(event))
    assert server.replies == [("per_1", "reject", server.replies[0][2])]
    assert "cannot hold a NUL character" in server.replies[0][2]

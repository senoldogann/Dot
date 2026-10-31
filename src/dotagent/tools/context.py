"""What every tool receives while it runs."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from dotagent.browser.session import BrowserSession
from dotagent.policy.sandbox import UNSANDBOXED, Sandbox
from dotagent.policy.types import ActionInfo, ApprovalMode
from dotagent.store.models import RunMode, RunRecord
from dotagent.web.refused import RefusedHosts

if TYPE_CHECKING:
    from dotagent.agent.verify import RunFacts
    from dotagent.runtime import Runtime


class Gate(Protocol):
    """Authorises one concrete action; raises when the policy or the user refuses. Returns the note the user wrote
    when approving it, or an empty string. What the user writes while a tool runs (the answer to a question) is passed
    on with ``add_instruction``."""

    async def authorize(self, info: ActionInfo) -> str: ...

    def add_instruction(self, text: str) -> None: ...


def _untainted() -> bool:
    """A context outside a run of the loop, which has read nothing third parties can author."""
    return False


def _no_user_text() -> str:
    """A context outside a run of the loop, with no user message whose words a tool may trust."""
    return ""


@dataclass(frozen=True)
class DelegateOutcome:
    """A delegated run that has ended, and whether it read content that third parties can author."""

    run: RunRecord
    tainted: bool


# What ``ask_user`` does in a run (``agent.progress.question_mode``): ``leave`` every question for the user, ``wait``
# for every answer, or ``choose`` (item 508): wait for an answer the run cannot go on without, and leave the others,
# whose answers reach the run as the user's messages while it works.
AskMode = Literal["leave", "wait", "choose"]


class Spawner(Protocol):
    """Starts runs on behalf of a running one."""

    @property
    def answers_reach_runs(self) -> bool:
        """Whether the user's answer to a question a run left reaches the run while it works (item 508): true in the
        daemon, false in a ``dotx`` command, whose runs no other process steers."""
        ...

    async def delegate(
        self,
        parent_run_id: str,
        goal: str,
        mode: RunMode,
        model: str | None,
        agent_id: str | None,
        opening: str | None = None,
    ) -> DelegateOutcome:
        """Run ``goal`` as a run of its own on ``model`` (``None``: the parent's model), as a run of the agent
        ``agent_id`` names when one is (item 87: its instructions, its tools and its bounds), wait until it ends and
        report how it ended. ``opening`` is the first message its model reads in place of the goal, when it is more
        than the goal (item 831: a group chat's conversation); the goal stays what its row and its page name."""
        ...


@dataclass(frozen=True)
class ToolContext:
    """``output_chars`` is the most one tool result of the run may carry; a tool that pages its output sizes each page
    to fit it, so nothing is cut from the middle of a page. ``approvals`` is the run's approval mode, which decides
    what happens at a field that takes a secret and where the run's processes start (:func:`run_sandbox`);
    ``questions`` is what ``ask_user`` does in the run. ``facts``
    gives what the run's calls did so far, as the harness measured it, for a tool that has a claim checked.
    ``tainted`` says whether the run has read content third parties can author as it stands; a tool that writes
    something lasting reads it when it runs, as the run may have read such content after the context was made."""

    runtime: "Runtime"
    # The browser session this run works in: the daemon's, or the one of the mode its campaign asked for.
    browser: BrowserSession
    run_id: str
    # The agent whose run it is (item 94), ``None`` for Dot's: what the memory tools search as.
    agent_id: str | None
    thread_id: str
    mode: RunMode
    approvals: ApprovalMode
    questions: AskMode
    workspace: Path
    gate: Gate
    cancel: asyncio.Event
    output_chars: int
    facts: Callable[[], "RunFacts"]
    spawner: Spawner | None = None
    # Whether the run has read content third parties can author as it stands (the gate's ``tainted``), read when a tool
    # that writes something lasting asks: the memory tools mark the notes they save with it.
    tainted: Callable[[], bool] = _untainted
    # Whether the run has read such content at any time, which the owner's next message does not clear (the gate's
    # ``ever_tainted``): what a tool writes that every later run follows as the owner's word reads it (item 893).
    ever_tainted: Callable[[], bool] = _untainted
    # What the user wrote in this run, the goal and every instruction since, read when a tool must tell a secret the
    # user gave from one a page or a tool's output carries (item 797).
    user_text: Callable[[], str] = _no_user_text
    # The names of the tools the run was given, which bound the agents it makes (item 307); ``None`` outside a run of
    # the loop, which makes no agent.
    run_tools: frozenset[str] | None = None
    # The hosts that refused this run's plain requests (item 98); the next fetch of one goes to the browser first.
    refused_hosts: RefusedHosts = field(default_factory=RefusedHosts)


def run_sandbox(ctx: ToolContext) -> Sandbox:
    """Where the processes of the run start: in the sandbox of the runtime while it is on, or as they are in a yolo run,
    whose lines the guard lets change Dot's own files as well."""
    return UNSANDBOXED if ctx.approvals == "yolo" else ctx.runtime.sandbox.current()

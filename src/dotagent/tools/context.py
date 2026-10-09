"""What every tool receives while it runs."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from dotagent.policy.types import ActionInfo, ApprovalMode, QuestionMode
from dotagent.store.models import RunMode, RunRecord

if TYPE_CHECKING:
    from dotagent.agent.verify import RunFacts
    from dotagent.runtime import Runtime


class Gate(Protocol):
    """Authorises one concrete action; raises when the policy or the user refuses. Returns the note the user wrote
    when approving it, or an empty string. What the user writes while a tool runs (the answer to a question) is passed
    on with ``add_instruction``."""

    async def authorize(self, info: ActionInfo) -> str: ...

    def add_instruction(self, text: str) -> None: ...


@dataclass(frozen=True)
class DelegateOutcome:
    """A delegated run that has ended, and whether it read content that third parties can author."""

    run: RunRecord
    tainted: bool


class Spawner(Protocol):
    """Starts runs on behalf of a running one."""

    async def delegate(self, parent_run_id: str, goal: str, mode: RunMode) -> DelegateOutcome:
        """Run ``goal`` as a run of its own on the parent's model, wait until it ends and report how it ended."""
        ...


@dataclass(frozen=True)
class ToolContext:
    """``output_chars`` is the most one tool result of the run may carry; a tool that pages its output sizes each page
    to fit it, so nothing is cut from the middle of a page. ``approvals`` is the run's approval mode, which decides
    what happens at a field that takes a secret; ``questions`` is what ``ask_user`` does in the run. ``facts``
    gives what the run's calls did so far, as the harness measured it, for a tool that has a claim checked."""

    runtime: "Runtime"
    run_id: str
    thread_id: str
    mode: RunMode
    approvals: ApprovalMode
    questions: QuestionMode
    workspace: Path
    gate: Gate
    cancel: asyncio.Event
    output_chars: int
    facts: Callable[[], "RunFacts"]
    spawner: Spawner | None = None

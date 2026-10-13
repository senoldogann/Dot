"""A ``dotx run`` of the scripted model on a chat, for the smoke test of the runs of commands on the pages of the
daemon: the run goes through :func:`dotagent.cli_run.follow_run`, and leaves its chat's queue to the daemon, as the
command's runs do.

Usage: ``python dot_cli_steps.py <goal> <approvals> <thread id> <turns>``, with ``DOT_HOME`` set to a throw-away home,
``<thread id>`` empty for a new chat and ``<turns>`` a JSON list of ``ScriptedTurn``. On the eval configuration, it
starts a run whose model plays those turns, prints ``run <id>``, then ``call <tool>`` as each call starts and ``asked
<approval id>`` as each question is asked, and ``ended <status>`` once the run is over. Its terminal is interactive: a
question waits for a line on the standard input, as at a terminal, so the caller keeps that open.
"""

import asyncio
import contextlib
import os
import sys

from pydantic import TypeAdapter

from dotagent.agent.manager import RunManager
from dotagent.cli_run import follow_run
from dotagent.cli_ui import TerminalUi, UiOptions
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_OPTIONS: UiOptions = UiOptions(interactive=True, stream_text=False, show_thinking=False, verbose=False)
_TURNS: TypeAdapter[list[ScriptedTurn]] = TypeAdapter(list[ScriptedTurn])
_APPROVALS: TypeAdapter[ApprovalMode] = TypeAdapter(ApprovalMode)


def _say(line: str) -> None:
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


async def _say_steps(subscription: Subscription) -> None:
    while True:
        event: EventRecord = await subscription.queue.get()
        if event.kind == "tool_call":
            _say(f"call {event.data['tool']}")
        elif event.kind == "approval_requested":
            _say(f"asked {event.data['approval_id']}")


async def main(goal: str, approvals: ApprovalMode, thread_id: str | None, turns: list[ScriptedTurn]) -> None:
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), dict(os.environ))
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        manager: RunManager = RunManager(runtime)
        manager.leave_queues()

        async def launch() -> RunRecord:
            run: RunRecord = await manager.start(
                goal, "task", SCRIPTED_MODEL, thread_id, "cli", None, None, approvals, False, ()
            )
            _say(f"run {run.id}")
            return run

        async with runtime.bus.subscribe(None) as subscription:
            saying: asyncio.Task[None] = asyncio.create_task(_say_steps(subscription))
            try:
                finished: RunRecord = await follow_run(runtime, manager, TerminalUi(runtime, _OPTIONS), launch)
            finally:
                saying.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await saying
        await manager.shutdown()
        _say(f"ended {finished.status}")
    finally:
        await close_runtime(runtime)


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1],
            _APPROVALS.validate_python(sys.argv[2]),
            sys.argv[3] or None,
            _TURNS.validate_json(sys.argv[4]),
        )
    )

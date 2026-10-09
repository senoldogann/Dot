"""A ``dotx run`` of the scripted model, for the smoke test of how it ends when it is told to: the run goes through
:func:`dotagent.cli.follow_run`, as the command's runs do.

Usage: ``python dot_cli_run.py <goal> <turn>``, with ``DOT_HOME`` set to a throw-away home and ``<turn>`` a
``ScriptedTurn`` as JSON. On the eval configuration, it starts a yolo run of ``dotx run`` whose model plays that one
turn, prints ``run <id>``, then ``call <tool>`` as each call starts, and ``ended <status>`` once the run is over.
"""

import asyncio
import contextlib
import os
import sys

from dotagent.agent.manager import RunManager
from dotagent.cli import follow_run
from dotagent.cli_ui import TerminalUi, UiOptions
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_OPTIONS: UiOptions = UiOptions(interactive=False, stream_text=False, show_thinking=False, verbose=False)


def _say(line: str) -> None:
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


async def _say_calls(subscription: Subscription) -> None:
    while True:
        event: EventRecord = await subscription.queue.get()
        if event.kind == "tool_call":
            _say(f"call {event.data['tool']}")


async def main(goal: str, turn: ScriptedTurn) -> None:
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), dict(os.environ))
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [turn]))
        manager: RunManager = RunManager(runtime)

        async def launch() -> RunRecord:
            run: RunRecord = await manager.start(
                goal, "task", SCRIPTED_MODEL, None, "cli", None, None, "yolo", False, ()
            )
            _say(f"run {run.id}")
            return run

        async with runtime.bus.subscribe(None) as subscription:
            saying: asyncio.Task[None] = asyncio.create_task(_say_calls(subscription))
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
    asyncio.run(main(sys.argv[1], ScriptedTurn.model_validate_json(sys.argv[2])))

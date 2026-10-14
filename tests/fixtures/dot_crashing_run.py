"""A Dot run that this process starts and never sees to its end, for the resume smoke tests: they kill the process
while the run works, as a crash of the daemon would end it.

Usage: ``python dot_crashing_run.py <goal> <turn>``, with ``DOT_HOME`` set to a throw-away home and ``<turn>`` a
``ScriptedTurn`` as JSON. On the eval configuration, it starts a run of the web UI in yolo mode whose model plays that
one turn, prints ``run <id>`` and then ``call <tool>`` as each call starts, and waits to be killed.
"""

import asyncio
import os
import sys

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _say(line: str) -> None:
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


async def main(goal: str, turn: ScriptedTurn) -> None:
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), dict(os.environ))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [turn]))
    manager: RunManager = RunManager(runtime)
    async with runtime.bus.subscribe(None) as subscription:
        run: RunRecord = await manager.start(
            goal, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", "yolo", False, ()
        )
        _say(f"run {run.id}")
        while True:
            event: EventRecord = await subscription.queue.get()
            if event.run_id == run.id and event.kind == "tool_call":
                _say(f"call {event.data['tool']}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], ScriptedTurn.model_validate_json(sys.argv[2])))

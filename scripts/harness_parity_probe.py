"""Three fixed tasks through a model of the owner's Claude plan (``claude_code:<model>``), measured as the audit of
2026-11-24 measured runs (items 1691 to 1694): for each, how it ended, its steps, whether it called ``finish``, whether
it kept Dot's plan, its calls of Dot's tools, those that failed and those refused as "Invalid arguments", the goal
checks and their decisions, its tokens and minutes.

It is a live measure: it calls the model on the owner's plan through the installed ``claude`` program, and the web
task reaches the internet. It runs in a throwaway Dot home of its own (a temporary folder: its own store, workspace,
logs and Chrome profile), never the owner's Dot or its daemon, at effort low, in goal mode with ``claude_code:haiku``
as the review model, approvals ``yolo`` (nobody watches it), and prints one JSON object. Run it on ``main`` and on a
branch to compare:

    uv run python scripts/harness_parity_probe.py claude_code:sonnet > after.json

Usage: uv run python scripts/harness_parity_probe.py <model> [review_model]
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# The tasks: a file made and checked, a fact to keep in memory, a lookup on the web. Each asks for a little of what
# the audit found missing (verification, the plan, memory_save, the web) and none needs the owner.
TASKS: tuple[str, ...] = (
    "Create parity/fib.txt in the workspace with the first 15 Fibonacci numbers, one per line, starting 0 1, then "
    "check the file and tell me their sum.",
    "Remember for later that the codename of my test project is Kestrel, and tell me how you saved it.",
    "Look up the latest stable release of Python on python.org and write its version number to parity/python.txt.",
)
_INVALID: str = "Invalid arguments for "
_EVENTS_READ: int = 5000
_RUN_LIMIT_S: float = 900.0


@dataclass(frozen=True)
class Measured:
    """One task's run, as the audit counted runs."""

    task: str
    status: str
    steps: int
    finished: bool
    planned: bool
    tool_calls: int
    tool_errors: int
    invalid_arguments: int
    goal_checks: list[str]
    tokens: int
    minutes: float
    result: str


def _config(home: Path, review: str) -> str:
    """The throwaway home's config: goal mode on, ``review`` as the review model, no embedding service, a headless
    browser."""
    return (
        '[browser]\nheadless = true\n\n[agent]\ngoal_mode = true\n\n[models]\nreview = "'
        + review
        + '"\n\n[memory]\nembed_enabled = false\n'
    )


async def _probe(model: str, review: str, home: Path) -> list[Measured]:
    # Imported once the throwaway home is set, so that nothing of Dot's reads the owner's.
    from dotagent.agent.manager import RunManager
    from dotagent.config import load_config
    from dotagent.evals import EVAL_SOURCE
    from dotagent.runtime import Runtime, build_runtime, close_runtime
    from dotagent.store.models import EventRecord, RunRecord

    (home / "config.toml").write_text(_config(home, review), encoding="utf-8")
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    measured: list[Measured] = []
    try:
        for task in TASKS:
            began: float = time.monotonic()
            started: RunRecord = await manager.start(
                task, "task", model, None, EVAL_SOURCE, None, "low", "standard", None, "yolo", True, ()
            )
            async with asyncio.timeout(_RUN_LIMIT_S):
                ended: RunRecord = await manager.wait(started.id)
            events: list[EventRecord] = await runtime.store.list_events(ended.id, 0, _EVENTS_READ)
            results: list[EventRecord] = [event for event in events if event.kind == "tool_result"]
            measured.append(
                Measured(
                    task=task,
                    status=ended.status,
                    steps=ended.steps,
                    finished=any(event.data.get("tool") == "finish" and event.data.get("ok") for event in results),
                    planned=any(event.kind == "plan" for event in events),
                    tool_calls=len(results),
                    tool_errors=sum(1 for event in results if event.data.get("ok") is False),
                    invalid_arguments=sum(
                        1 for event in results if str(event.data.get("text") or "").startswith(_INVALID)
                    ),
                    goal_checks=[str(event.data.get("decision")) for event in events if event.kind == "goal_check"],
                    tokens=ended.input_tokens + ended.output_tokens,
                    minutes=round((time.monotonic() - began) / 60.0, 2),
                    result=ended.result[:400],
                )
            )
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
    return measured


def main() -> None:
    if len(sys.argv) not in (2, 3) or not sys.argv[1].startswith("claude_code:"):
        sys.exit("usage: uv run python scripts/harness_parity_probe.py claude_code:<model> [review_model]")
    model: str = sys.argv[1]
    review: str = sys.argv[2] if len(sys.argv) == 3 else "claude_code:haiku"
    with tempfile.TemporaryDirectory(prefix="dot-parity-") as root:
        base: Path = Path(os.path.realpath(root))
        for name, folder in (("DOT_HOME", "home"), ("DOT_LOG_DIR", "logs"), ("DOT_CHROME_DIR", "chrome")):
            (base / folder).mkdir()
            os.environ[name] = str(base / folder)
        measured: list[Measured] = asyncio.run(_probe(model, review, base / "home"))
    print(json.dumps({"model": model, "review": review, "runs": [asdict(one) for one in measured]}, indent=2))


if __name__ == "__main__":
    main()

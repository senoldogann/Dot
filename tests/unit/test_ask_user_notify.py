"""A question a run leaves on the chat tells the user it is there (item 797): the notification is best-effort and the
question itself is still kept when it fails."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from dotagent.agent.verify import new_facts
from dotagent.runtime import Runtime
from dotagent.tools.context import ToolContext
from dotagent.tools.meta import ASK_USER_TOOL, build_meta_tools


def _context(runtime: Any) -> ToolContext:
    return ToolContext(
        runtime=cast(Runtime, runtime),
        browser=cast(Any, None),
        run_id="run-1",
        agent_id=None,
        thread_id="thread-1",
        mode="task",
        approvals="yolo",
        questions="leave",
        workspace=Path(),
        gate=cast(Any, SimpleNamespace(add_instruction=lambda text: None)),
        cancel=asyncio.Event(),
        output_chars=4_000,
        facts=new_facts,
    )


async def test_a_left_question_notifies_the_user() -> None:
    notifications: list[tuple[str, str]] = []
    left: list[tuple[object, ...]] = []

    async def leave(*args: object, **kwargs: object) -> None:
        left.append(args)

    async def notify(title: str, message: str) -> None:
        notifications.append((title, message))

    runtime = SimpleNamespace(approvals=SimpleNamespace(leave=leave), notify=notify)
    tool = next(tool for tool in build_meta_tools() if tool.spec.name == ASK_USER_TOOL)
    context = _context(runtime)
    prepared = await tool.prepare({"question": "Which port should I note?"}, context)

    result = await prepared.run(context)

    assert "left for the user" in result.text
    assert left
    assert notifications == [("Dot has a question", "Which port should I note?")]

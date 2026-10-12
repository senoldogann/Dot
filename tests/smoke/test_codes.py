"""``code_fetch`` in a runtime (item 31): it exists only for the applications of ``[policy] code_sources``, refuses the
others, and the policy treats a read as a high-risk action of the Mac. The live read of a code from Mail is the owner's
to run (``DOT_MAIL_TESTS=1``): it sends a message to the account's own address and reads the code back."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.errors import ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.desktop.script import run_osascript
from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools.base import PreparedCall
from dotagent.tools.codes import CODE_TOOL
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import tools_for_mode

pytestmark = pytest.mark.asyncio(loop_scope="module")

_TEST_CODE: str = "482913"
_SUBJECT: str = "Dot code test"


class _Recorder:
    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    home: Path = tmp_path_factory.mktemp("home")
    patch.setenv("DOT_HOME", str(home))
    settings: Path = home / "config.toml"
    await asyncio.to_thread(settings.write_text, '[policy]\ncode_sources = ["mail"]\n', "utf-8")
    built: Runtime = await build_runtime(load_config(settings))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _prepare(runtime: Runtime, arguments: JsonObject) -> tuple[PreparedCall, ToolContext]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="codes-test",
        agent_id=None,
        thread_id="codes-test",
        mode="task",
        approvals="yolo",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=20_000,
        facts=new_facts,
    )
    return await runtime.registry.tools[CODE_TOOL].prepare(arguments, ctx), ctx


async def test_the_tool_reads_only_the_listed_applications_and_is_a_high_risk_action(runtime: Runtime) -> None:
    assert CODE_TOOL in {tool.spec.name for tool in tools_for_mode(runtime.registry, "task")}
    assert CODE_TOOL not in {tool.spec.name for tool in tools_for_mode(runtime.registry, "research")}
    with pytest.raises(ToolInputError, match="Messages is not in"):
        await _prepare(runtime, {"app": "messages"})
    prepared, _ = await _prepare(runtime, {"app": "mail", "sender": "shop.com", "within_minutes": 3})
    assert (prepared.action.kind, prepared.action.risk, prepared.action.target) == ("app_control", "high", "Mail")
    assert prepared.action.summary == "Read a one-time code from Mail (from 'shop.com', the last 3 min)"
    assert decide(prepared.action, "task", True, "aggressive", [], None).verdict == "allow"
    assert decide(prepared.action, "task", False, "balanced", [], None).verdict == "ask"
    assert decide(prepared.action, "research", False, "aggressive", [], None).source == "mode"


@pytest.mark.skipif(
    os.environ.get("DOT_MAIL_TESTS") != "1", reason="sends a mail from the owner's Mail; DOT_MAIL_TESTS=1"
)
async def test_a_code_mailed_to_the_owner_is_read_back(runtime: Runtime) -> None:
    address: str = await run_osascript(
        "AppleScript", 'tell application "Mail" to get email addresses of account 1', (), 30.0
    )
    own: str = address.split(",")[0].strip()
    await run_osascript(
        "AppleScript",
        "\n".join(
            (
                'tell application "Mail"',
                f'  set m to make new outgoing message with properties {{subject:"{_SUBJECT}", content:"Your Dot test code is {_TEST_CODE}.", visible:false}}',
                f'  tell m to make new to recipient at end of to recipients with properties {{address:"{own}"}}',
                "  send m",
                "end tell",
            )
        ),
        (),
        30.0,
    )
    deadline: float = asyncio.get_running_loop().time() + 120.0
    while True:
        prepared, ctx = await _prepare(runtime, {"app": "mail", "subject": _SUBJECT, "within_minutes": 5})
        result = await prepared.run(ctx)
        if not result.is_error or asyncio.get_running_loop().time() > deadline:
            break
        await asyncio.sleep(5.0)
    assert result.text.startswith(f"Code {_TEST_CODE} from Mail"), result.text

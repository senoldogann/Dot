"""A run gives a script that needs it more time than the configured timeout (item 1180): Word, started, opening a
document and updating its table of contents, took longer than the 30 s every script had, and timed out each time."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import DesktopError
from dotagent.policy.sandbox import NO_SEAL, UNSANDBOXED, SwitchedSandbox
from dotagent.tools.apple import MAX_SCRIPT_TIMEOUT_S, AppleScriptArgs, build_apple_tool, script_timeout_s
from dotagent.tools.context import ToolContext

CONFIGURED_TIMEOUT_S: float = 1.0
ASKED_TIMEOUT_S: int = 10
# Longer than the configured timeout, well within the one asked for.
_SLOW: str = 'delay 2\nreturn "finished"'


def _ctx() -> ToolContext:
    default: AppConfig = load_default_config()
    config: AppConfig = default.model_copy(
        update={"desktop": default.desktop.model_copy(update={"osascript_timeout_s": CONFIGURED_TIMEOUT_S})}
    )
    return cast(
        ToolContext,
        SimpleNamespace(
            runtime=SimpleNamespace(
                config=config,
                shell_env={"PATH": "/usr/bin:/bin"},
                protected=(),
                dot_paths=(),
                sandbox=SwitchedSandbox(UNSANDBOXED, NO_SEAL, False, False),
            ),
            workspace=Path("/nonexistent-workspace"),
            approvals="yolo",
            unattended=False,
        ),
    )


@pytest.mark.macos
async def test_a_script_given_a_longer_timeout_finishes_where_the_configured_one_kills_it() -> None:
    tool = build_apple_tool()
    ctx: ToolContext = _ctx()
    with pytest.raises(DesktopError) as caught:
        await (await tool.prepare({"script": _SLOW}, ctx)).run(ctx)
    assert "did not finish within 1 s" in caught.value.message
    assert "may go on with what it was asked" in caught.value.message
    result = await (await tool.prepare({"script": _SLOW, "timeout_s": ASKED_TIMEOUT_S}, ctx)).run(ctx)
    assert "finished" in result.text


def test_a_timeout_stays_under_the_tools_own() -> None:
    assert script_timeout_s(None, 30.0) == 30.0
    assert script_timeout_s(ASKED_TIMEOUT_S, 30.0) == ASKED_TIMEOUT_S
    assert script_timeout_s(None, 500.0) == MAX_SCRIPT_TIMEOUT_S
    with pytest.raises(ValidationError):
        AppleScriptArgs(script="return 1", timeout_s=MAX_SCRIPT_TIMEOUT_S + 1)

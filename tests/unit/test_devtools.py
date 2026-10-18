"""Where a run's scripts reach its Chrome with the DevTools Protocol (item 265): Dot's own Chrome with a port set, and
the system prompt says so only then, to a run with a tool that runs its scripts; a port is 0 or from 1024 up."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.browser.devtools import devtools_address, devtools_switches
from dotagent.config import BrowserConfig, load_default_config
from dotagent.store.models import BrowserMode

ADDRESS: str = "http://127.0.0.1:9222"


def _browser(mode: BrowserMode, port: int) -> BrowserConfig:
    return load_default_config().browser.model_copy(update={"mode": mode, "debug_port": port})


def _prompt(devtools: str | None, tools: list[str]) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            "ui",
            "aggressive",
            "yolo",
            "wait",
            False,
            False,
            Path("/tmp/work"),
            [],
            0,
            tools,
            [],
            0,
            None,
            [],
            None,
            (),
            (),
            devtools,
            "full",
        )
    )


def test_the_address_is_dots_own_chrome_with_a_port_set() -> None:
    assert load_default_config().browser.debug_port == 0
    assert devtools_address(_browser("own", 9222)) == ADDRESS
    assert devtools_address(_browser("own", 0)) is None
    assert devtools_address(_browser("attach", 9222)) is None
    assert (devtools_switches(9222), devtools_switches(0)) == (["--remote-debugging-port=9222"], [])


def test_a_port_is_none_or_one_above_the_systems() -> None:
    assert BrowserConfig.model_validate(_browser("own", 1024).model_dump()).debug_port == 1024
    for port in (80, 1023, 65536):
        with pytest.raises(ValidationError, match="debug_port"):
            BrowserConfig.model_validate(_browser("own", port).model_dump())


def test_the_prompt_names_the_address_only_when_there_is_one_and_a_tool_runs_scripts() -> None:
    told: str = _prompt(ADDRESS, ["shell", "browser_open"])
    assert f"- Dot's Chrome also takes DevTools connections at {ADDRESS} once one of your browser calls" in told
    assert "connect_over_cdp with no_defaults=True" in told
    assert f"at {ADDRESS}" in _prompt(ADDRESS, ["terminal_send", "browser_open"])
    assert "DevTools" not in _prompt(ADDRESS, ["browser_open", "browser_eval"])
    assert "DevTools" not in _prompt(None, ["shell", "browser_open"])

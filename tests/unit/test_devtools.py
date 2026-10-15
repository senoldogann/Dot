"""Where a run's scripts reach its Chrome with the DevTools Protocol (item 265): Dot's own Chrome with a port set, and
the system prompt says so only then."""

from pathlib import Path

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.browser.devtools import devtools_address, devtools_switches
from dotagent.config import BrowserConfig, load_default_config
from dotagent.store.models import BrowserMode

ADDRESS: str = "http://127.0.0.1:9222"


def _browser(mode: BrowserMode, port: int) -> BrowserConfig:
    return load_default_config().browser.model_copy(update={"mode": mode, "debug_port": port})


def _prompt(devtools: str | None) -> str:
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
            ["shell", "browser_open"],
            [],
            0,
            None,
            [],
            None,
            (),
            (),
            devtools,
        )
    )


def test_the_address_is_dots_own_chrome_with_a_port_set() -> None:
    assert load_default_config().browser.debug_port == 0
    assert devtools_address(_browser("own", 9222)) == ADDRESS
    assert devtools_address(_browser("own", 0)) is None
    assert devtools_address(_browser("attach", 9222)) is None
    assert (devtools_switches(9222), devtools_switches(0)) == (["--remote-debugging-port=9222"], [])


def test_the_prompt_names_the_address_only_when_there_is_one() -> None:
    told: str = _prompt(ADDRESS)
    assert f"- Dot's Chrome also takes DevTools connections at {ADDRESS} while it runs" in told
    assert "connect_over_cdp" in told
    assert "DevTools" not in _prompt(None)

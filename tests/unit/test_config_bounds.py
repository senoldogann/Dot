"""The numbers of the configuration that would stop the daemon from working are refused when it is read: no run could
start, the scheduler would spin, every call would time out at once, or a browser wait would never end."""

import pytest
from pydantic import ValidationError

from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import JsonObject


def _with(path: tuple[str, ...], value: float) -> JsonObject:
    """The default configuration as read from a file, with the key at ``path`` set to ``value``."""
    data: JsonObject = load_default_config().model_dump(mode="json")
    section: JsonObject = data
    for key in path[:-1]:
        inner = section[key]
        assert isinstance(inner, dict)
        section = inner
    section[path[-1]] = value
    return data


@pytest.mark.parametrize(
    "path",
    [
        ("agent", "max_concurrent_runs"),
        ("agent", "parallel_tools"),
        ("agent", "model_call_timeout_s"),
        ("scheduler", "tick_s"),
        ("policy", "approval_timeout_s"),
        ("server", "port"),
        ("providers", "ollama", "max_concurrent"),
        ("providers", "anthropic", "attempts"),
        ("providers", "opencode_go", "read_timeout_s"),
        ("browser", "max_tabs"),
        ("browser", "action_timeout_s"),
    ],
)
def test_a_number_that_would_stop_the_daemon_is_refused(path: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(path, 0))
    assert [error["loc"] for error in refused.value.errors()] == [path]


def test_the_defaults_are_within_the_bounds() -> None:
    assert AppConfig.model_validate(load_default_config().model_dump(mode="json")) == load_default_config()

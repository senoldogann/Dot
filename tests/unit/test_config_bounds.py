"""The values of the configuration that would stop the daemon from working are refused when it is read: no run could
start, the scheduler would spin, every call would time out at once, a browser wait would never end, every look at a page
would fail, no request could reach a provider, or a model or a spending cap would name what does not exist."""

import pytest
from pydantic import JsonValue, ValidationError

from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import JsonObject


def _with(path: tuple[str, ...], value: JsonValue) -> JsonObject:
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
        ("agent", "tool_output_chars"),
        ("agent", "context_tokens"),
        ("shell", "login_env_timeout_s"),
        ("shell", "default_timeout_s"),
        ("shell", "max_timeout_s"),
        ("desktop", "ax_timeout_s"),
        ("desktop", "osascript_timeout_s"),
        ("web", "fetch_timeout_s"),
    ],
)
def test_a_number_that_would_stop_the_daemon_is_refused(path: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(path, 0))
    assert [error["loc"] for error in refused.value.errors()] == [path]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("browser", "viewport_width"), 0),
        (("browser", "viewport_width"), 100_000),
        (("browser", "viewport_height"), 0),
        (("browser", "viewport_height"), 100_000),
        (("browser", "screenshot_quality"), 0),
        (("browser", "screenshot_quality"), 101),
        (("browser", "viewport_margin_px"), -1),
        (("browser", "zoom"), 24),
        (("browser", "zoom"), 501),
    ],
)
def test_a_page_size_or_a_screenshot_quality_chrome_cannot_take_is_refused(path: tuple[str, ...], value: int) -> None:
    """Item 144: a page Chrome cannot open, or a screenshot it cannot take, would fail every run's first look."""
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(path, value))
    assert [error["loc"] for error in refused.value.errors()] == [path]


def test_the_defaults_are_within_the_bounds() -> None:
    assert AppConfig.model_validate(load_default_config().model_dump(mode="json")) == load_default_config()


def test_a_default_shell_timeout_above_the_longest_is_refused() -> None:
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(("shell", "default_timeout_s"), 3600))
    assert [error["loc"] for error in refused.value.errors()] == [("shell",)]


@pytest.mark.parametrize(
    "address", ["localhost:11434", "http://", "ftp://example.com", "http://[::1", "http://localhost:port"]
)
def test_a_provider_address_the_http_client_cannot_use_is_refused(address: str) -> None:
    """Item 177: such an address failed every call of the provider later, with an error of the HTTP client."""
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(("providers", "ollama", "base_url"), address))
    assert [error["loc"] for error in refused.value.errors()] == [("providers", "ollama", "base_url")]


@pytest.mark.parametrize(
    ("path", "value", "where"),
    [
        (("models", "main"), "minimax-m2.7", ("models", "main")),
        (("models", "fast"), "olama:gpt-oss:20b", ("models", "fast")),
        (("models", "search"), "ollama:gpt-oss:20b", ("models", "search")),
        (("models", "helpers"), ["openai"], ("models", "helpers", 0)),
    ],
)
def test_a_model_that_names_no_provider_dot_knows_is_refused(
    path: tuple[str, ...], value: JsonValue, where: tuple[str | int, ...]
) -> None:
    """Item 177: such a model failed every run that used it later; web search relays through an Anthropic model only."""
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(path, value))
    assert [error["loc"] for error in refused.value.errors()] == [where]


@pytest.mark.parametrize("site", ["www.example.com", "https://example.com", "Example.com", "example.com/shop"])
def test_a_spending_cap_for_a_site_no_address_matches_is_refused(site: str) -> None:
    """Item 177: a payment step's site is its host in lower case without ``www.``; a cap under another name never
    applied, so its purchases asked although the cap was meant to approve them."""
    caps: JsonValue = {site: {"per_purchase": 50, "per_day": 100}, "example.org": {"per_purchase": 50, "per_day": 100}}
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(("policy", "spend"), caps))
    assert [error["loc"][:3] for error in refused.value.errors()] == [("policy", "spend", site)]

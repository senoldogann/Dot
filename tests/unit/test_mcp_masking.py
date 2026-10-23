"""A key in an MCP server's address, in its arguments or in what it printed as it failed is shown and logged by none of
the pages and logs that say where a server is and why it failed (item 558)."""

import json

from structlog.testing import capture_logs

from dotagent.config import McpServerConfig
from dotagent.core.errors import McpError
from dotagent.mcp.hub import _failure
from dotagent.mcp.masking import masked_context, shown_command, shown_url

PATH_KEY: str = "k3Yx9Zq81LmNpR5tUv7W2aB"
QUERY_KEY: str = "zq81LmNpR5tUv7W2aBk3Yx"
ARGUMENT_KEY: str = "abc123def456ghi789"
URL: str = f"https://mcp.example.com/{PATH_KEY}/mcp?api_key={QUERY_KEY}"


def test_a_url_is_shown_without_its_user_its_query_and_the_segments_that_read_as_keys() -> None:
    assert shown_url(URL) == "https://mcp.example.com/***/mcp?…"
    assert shown_url(f"https://ada:hunter2@mcp.example.com:8443/mcp#{PATH_KEY}") == "https://mcp.example.com:8443/mcp"
    # The addresses of the services of the catalogue, and words with a digit in them, are shown as they are.
    assert shown_url("https://mcp.notion.com/mcp") == "https://mcp.notion.com/mcp"
    assert (
        shown_url("http://127.0.0.1:8765/streamable-http/v2025-06-18")
        == "http://127.0.0.1:8765/streamable-http/v2025-06-18"
    )


def test_a_command_is_shown_with_the_value_of_its_secret_options_masked() -> None:
    assert shown_command("npx", ["-y", "@scope/server", "--token", ARGUMENT_KEY]) == "npx -y @scope/server --token ***"
    assert shown_command("uvx", ["server-fetch", "--api-key", "sk-live-0123456789abcdefghij"]) == (
        "uvx server-fetch --api-key ***"
    )
    assert shown_command("npx", ["-y", "@scope/server"]) == "npx -y @scope/server"


def test_what_an_error_of_a_server_tells_is_masked_wherever_the_key_stands() -> None:
    masked: dict[str, object] = dict(
        masked_context(
            {
                "server": "keyed",
                "transport": "http",
                "url": URL,
                "args": ["--token", ARGUMENT_KEY],
                "causes": [f"HTTPStatusError: 401 for {URL}", "ConnectError: All connection attempts failed"],
                "stderr_tail": f"using token={ARGUMENT_KEY} failed",
                "seconds": 3,
            }
        )
    )
    told: str = json.dumps(masked)
    for key in (PATH_KEY, QUERY_KEY, ARGUMENT_KEY):
        assert key not in told, (key, told)
    assert masked["url"] == "https://mcp.example.com/***/mcp?…"
    assert masked["args"] == ["--token", "***"]
    assert masked["causes"] == [
        "HTTPStatusError: 401 for https://mcp.example.com/***/mcp?…",
        "ConnectError: All connection attempts failed",
    ]
    assert (masked["server"], masked["transport"], masked["seconds"]) == ("keyed", "http", 3)


def test_a_server_left_out_is_stored_and_logged_masked() -> None:
    config: McpServerConfig = McpServerConfig(name="keyed", url=URL)
    error: McpError = McpError("the MCP server could not be reached", {"server": "keyed", "url": URL, "args": []})
    with capture_logs() as logs:
        failure = _failure(config, error)
    assert PATH_KEY not in json.dumps(failure.detail) and QUERY_KEY not in json.dumps(failure.detail)
    assert failure.detail["url"] == "https://mcp.example.com/***/mcp?…"
    left: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "mcp_server_left_out"]
    assert len(left) == 1 and PATH_KEY not in json.dumps(left[0], default=str), logs


def test_a_fault_that_names_a_key_is_stored_without_it() -> None:
    config: McpServerConfig = McpServerConfig(name="keyed", url=URL)
    failure = _failure(config, RuntimeError("the key sk-live-0123456789abcdefghij was refused"))
    assert "sk-live-0123456789abcdefghij" not in failure.error and failure.error.startswith("RuntimeError: the key ")

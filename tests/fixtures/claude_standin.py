"""A stand-in for ``claude -p`` (item 1013), started by a wrapper that names a folder first: it writes there what it was
started with (its arguments, the names of its environment, its folder, the mode of its MCP config file), reads the
first message on its input, and answers as the message says:

- ``extra tools``: an init line that lists a tool of Claude Code's own besides the door's, and nothing more;
- ``limit``: an init line, then a ``rate_limit_event`` whose window refuses calls;
- ``finish then talk``: as ``talk with me``, its first turn a call of ``finish`` that did not succeed (item 1076's
  pre-push review: what comes after it decides the run's end);
- ``finish then check``: a call of ``finish`` that succeeded and found nothing new, then a call of ``shell`` and a
  plain answer that the work failed after all (item 1076's pre-push review: a call after a finish drops it);
- ``talk with me``: an init line, a second's pause, an answer and a ``result``; then the same for each message that
  comes on its input, taken in first (item 1076: a run the owner holds waits for their next message);
- ``needs a code``: an init line, then a call of ``finish`` that did not succeed and needs a code from the owner, and
  a ``result`` (item 1076);
- ``slow command``: an init line, then a call of ``shell`` that sleeps before it writes ``late.txt`` in the workspace,
  for a Stop to cut short (item 1076);
- anything else: an init line naming the door's tools, a step that calls ``write_file`` twice through the door's MCP
  endpoint with the config file it was given (once into the workspace's ``.claude`` folder, once into ``notes.txt``),
  a step that answers with what the calls said, and a ``result``; then it waits for its input to close.

It calls no model and reaches nothing but Dot's bridge on this Mac."""

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ListToolsResult, TextContent

SESSION: str = "ses-standin-1"


def _say(item: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()


def _event(event: dict[str, object]) -> None:
    _say({"type": "stream_event", "event": event, "session_id": SESSION, "parent_tool_use_id": None})


def _step(blocks: list[tuple[str, str]], stop: str) -> None:
    """A reply of blocks (``text`` with its words, or ``tool_use`` with the tool's name), streamed."""
    _event({"type": "message_start", "message": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 20}}})
    for index, (kind, value) in enumerate(blocks):
        if kind == "text":
            _event({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
            _event({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": value}})
        else:
            _event(
                {"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "name": value}}
            )
        _event({"type": "content_block_stop", "index": index})
    _event({"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 7}})
    _event({"type": "message_stop"})


def _flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def _text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


def _init(door: str, tools: list[str]) -> None:
    _say(
        {
            "type": "system",
            "subtype": "init",
            "session_id": SESSION,
            "tools": tools,
            "mcp_servers": [{"name": door, "status": "connected"}],
            "slash_commands": [],
            "skills": [],
            # As the real program lists them (2.1.318): the plugins it comes with, which a run lets be.
            "plugins": [
                {"name": "cc-plugin-sec-default", "path": "builtin", "source": "cc-plugin-sec-default@builtin"},
                {"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@builtin"},
            ],
        }
    )


async def _work(server: dict[str, object], door: str, said: str) -> None:
    """Connect to the door as Claude Code would, say what the model has, then work as ``said`` asks."""
    headers: dict[str, str] = server["headers"]  # type: ignore[assignment]  # the config Dot wrote
    async with (
        httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(10.0, read=60.0)) as http,
        Client(streamable_http_client(str(server["url"]), http_client=http), mode="auto") as client,
    ):
        listed: ListToolsResult = await client.list_tools()
        _init(door, [f"mcp__{door}__{tool.name}" for tool in listed.tools])
        if "limit" in said:
            refused: dict[str, object] = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": 1_900_000_000}
            _say({"type": "rate_limit_event", "rate_limit_info": refused})
            return
        if "finish then talk" in said:
            _step([("tool_use", f"mcp__{door}__finish")], "tool_use")
            await client.call_tool(
                "finish", {"answer": "The bank asks for a code.", "success": False, "needs_from_user": "The code."}
            )
        if "talk with me" in said or "finish then talk" in said:
            await asyncio.sleep(1.0)
            _step([("text", "Heard you.")], "end_turn")
            _say(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Heard you.",
                    "session_id": SESSION,
                }
            )
            loop = asyncio.get_running_loop()
            while line := await loop.run_in_executor(None, sys.stdin.readline):
                more: dict[str, object] = json.loads(line)["message"]
                _say({"type": "user", "isReplay": True, "message": more, "session_id": SESSION})
                _step([("text", "Heard you again.")], "end_turn")
                _say(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Heard you again.",
                        "session_id": SESSION,
                    }
                )
            return
        if "needs a code" in said:
            _step([("tool_use", f"mcp__{door}__finish")], "tool_use")
            args: dict[str, object] = {
                "answer": "The bank asks for a code.",
                "success": False,
                "needs_from_user": "The code the bank sent to your phone.",
            }
            await client.call_tool("finish", args)
            _step([("text", "")], "end_turn")
            _say({"type": "result", "subtype": "success", "is_error": False, "result": "", "session_id": SESSION})
            return
        if "finish then check" in said:
            _step([("tool_use", f"mcp__{door}__finish")], "tool_use")
            await client.call_tool("finish", {"answer": "Paid the bill.", "success": True, "nothing_new": True})
            _step([("tool_use", f"mcp__{door}__shell")], "tool_use")
            await client.call_tool("shell", {"command": "echo checked"})
            late: str = "The payment was declined after all."
            _step([("text", late)], "end_turn")
            _say({"type": "result", "subtype": "success", "is_error": False, "result": late, "session_id": SESSION})
            return
        if "slow command" in said:
            _step([("text", "Running it."), ("tool_use", f"mcp__{door}__shell")], "tool_use")
            await client.call_tool("shell", {"command": "sleep 2; echo late > late.txt"})
            return
        _step([("text", "Writing the files."), ("tool_use", f"mcp__{door}__write_file")], "tool_use")
        sealed: CallToolResult = await client.call_tool(
            "write_file", {"path": ".claude/settings.json", "content": '{"hooks": {}}'}
        )
        notes: CallToolResult = await client.call_tool("write_file", {"path": "notes.txt", "content": "from claude"})
    answer: str = (
        f"Done. .claude: {'refused' if sealed.is_error else 'written'}: {_text(sealed)} | "
        f"notes: {'refused' if notes.is_error else 'written'}: {_text(notes)}"
    )
    _step([("text", answer)], "end_turn")
    _say({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"}})
    _say({"type": "result", "subtype": "success", "is_error": False, "result": answer, "session_id": SESSION})


def main() -> None:
    record: Path = Path(sys.argv[1])
    args: list[str] = sys.argv[2:]
    config_file: Path = Path(_flag(args, "--mcp-config"))
    door: str = _flag(args, "--allowedTools").split(",")[0].removeprefix("mcp__")
    (record / "started.json").write_text(
        json.dumps(
            {
                "args": args,
                "env": sorted(os.environ),
                "env_values": {
                    name: os.environ.get(name, "")
                    for name in ("DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "HOME")
                },
                "cwd": os.getcwd(),
                "config_file": str(config_file),
                "config_mode": stat.S_IMODE(config_file.stat().st_mode),
                "folder_mode": stat.S_IMODE(config_file.parent.stat().st_mode),
            }
        ),
        encoding="utf-8",
    )
    first: dict[str, object] = json.loads(sys.stdin.readline())
    message: dict[str, object] = first["message"]  # type: ignore[assignment]  # Dot's user line
    # As the real program does with --replay-user-messages: it says it took the message in.
    if "--replay-user-messages" in sys.argv:
        _say({"type": "user", "isReplay": True, "message": message, "session_id": SESSION})
    said: str = json.dumps(message["content"])
    (record / "first.json").write_text(json.dumps(message["content"]), encoding="utf-8")
    if "extra tools" in said:
        _init(door, ["Bash", f"mcp__{door}__notify"])
    else:
        server: dict[str, object] = json.loads(config_file.read_text(encoding="utf-8"))["mcpServers"][door]
        asyncio.run(_work(server, door, said))
    # Dot closes the input once each message has its result, or ends the program.
    sys.stdin.read()


if __name__ == "__main__":
    main()

"""A stand-in for ``claude -p --output-format json`` as a single call of Dot's starts it (item 1017): it writes what it
was started with (its arguments, its environment's names, the mode of its system prompt file, its input) to the folder
of its first argument, and answers as the program does, a JSON list of its lines, as the file ``mode`` there says:
``propose`` (the structured output of a harness proposal), ``tool`` (the model was given a tool of the program's own),
``limit`` (the plan's window refused the call) or ``text`` (a text answer)."""

import json
import os
import stat
import sys
from pathlib import Path

record: Path = Path(sys.argv[1])
argv: list[str] = sys.argv[2:]
system_file: Path = Path(argv[argv.index("--system-prompt-file") + 1])
asked: str = sys.stdin.read()
(record / "started.json").write_text(
    json.dumps(
        {
            "argv": argv,
            "env": sorted(os.environ),
            "system_mode": stat.S_IMODE(system_file.stat().st_mode),
            "system": system_file.read_text(encoding="utf-8"),
            "stdin": asked,
            "cwd": os.getcwd(),
        }
    ),
    encoding="utf-8",
)
mode: str = (record / "mode").read_text(encoding="utf-8").strip()
built_in: list[dict[str, str]] = [
    {"name": "cc-plugin-privacy", "path": "builtin", "source": "cc-plugin-privacy@builtin"}
]
tools: list[str] = (
    ["StructuredOutput", "Bash"] if mode == "tool" else ["StructuredOutput"] if "--json-schema" in argv else []
)
init: dict[str, object] = {
    "type": "system",
    "subtype": "init",
    "tools": tools,
    "mcp_servers": [],
    "slash_commands": [],
    "skills": [],
    "plugins": built_in,
}
status: str = "rejected" if mode == "limit" else "allowed"
window: dict[str, object] = {
    "type": "rate_limit_event",
    "rate_limit_info": {"status": status, "resetsAt": 1794021000, "rateLimitType": "five_hour"},
}
usage: dict[str, int] = {
    "input_tokens": 2,
    "cache_creation_input_tokens": 1200,
    "cache_read_input_tokens": 300,
    "output_tokens": 80,
}
proposal: dict[str, object] = {
    "keep": False,
    "habits": None,
    "tool_guidance": "- Wait for a page to settle before you read it.",
    "moments": ["step 2 read the page before it settled"],
    "summary": "Wait for pages",
}
result: dict[str, object] = (
    {"type": "result", "subtype": "error", "is_error": True, "result": "You've hit your limit"}
    if mode == "limit"
    else {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "A plain answer." if mode == "text" else json.dumps(proposal),
        "stop_reason": "end_turn" if mode == "text" else "tool_use",
        "usage": usage,
        **({} if mode == "text" else {"structured_output": proposal}),
    }
)
sys.stdout.write(json.dumps([init, window, result]))

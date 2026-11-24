"""The bridge of code mode (item 1500): the frames of the code's calls are text a program wrote, and each is read as
exactly one thing; whatever else it is ends the code. The twin of every malformed frame is the call that is well formed
and is answered."""

import json
import os
from pathlib import Path

import pytest
from code_mode_stand_ins import Box, box

from dotagent.codemode.bridge import (
    MAX_FRAME_BYTES,
    BridgeAnswer,
    BridgeRequest,
    MalformedFrame,
    read_request,
    reply_frame,
)
from dotagent.codemode.runner import Ended


def _frame(**parts: object) -> bytes:
    body: dict[str, object] = {"id": 1, "tool": "read_file", "args": {"path": "a"}}
    body.update(parts)
    return (json.dumps(body) + "\n").encode()


def test_a_call_is_read_as_the_object_with_its_number_its_tool_and_its_arguments() -> None:
    request: BridgeRequest = read_request(_frame(), 1)
    assert (request.id, request.tool, request.args) == (1, "read_file", {"path": "a"})
    assert read_request(_frame(id=7), 7).id == 7


@pytest.mark.parametrize(
    ("line", "expected", "why"),
    [
        (b'{"id":1,"tool":"read_file","args":{}}', 1, "does not end with a newline"),
        (b"not json at all\n", 1, "not a call"),
        (b'["id", 1]\n', 1, "not a call"),
        (b"null\n", 1, "not a call"),
        (b'{"id":1,"tool":"read_file"}\n', 1, "args"),
        (b'{"id":1,"args":{}}\n', 1, "tool"),
        (b'{"tool":"read_file","args":{}}\n', 1, "id"),
        (b'{"id":1,"tool":"read_file","args":{},"extra":true}\n', 1, "extra"),
        (b'{"id":"1","tool":"read_file","args":{}}\n', 1, "id"),
        (b'{"id":1.0,"tool":"read_file","args":{}}\n', 1, "id"),
        (b'{"id":true,"tool":"read_file","args":{}}\n', 1, "id"),
        (b'{"id":1,"tool":"","args":{}}\n', 1, "tool"),
        (b'{"id":1,"tool":7,"args":{}}\n', 1, "tool"),
        (b'{"id":1,"tool":"read_file","args":[]}\n', 1, "args"),
        (b'{"id":1,"tool":"read_file","args":"path=a"}\n', 1, "args"),
        (_frame(id=2), 1, "numbered 2 where 1 was next"),
        (_frame(id=1), 2, "numbered 1 where 2 was next"),
        (_frame(tool="x" * 201), 1, "tool"),
        (b"\xff\xfe\n", 1, "not a call"),
        (b"{" * 10000 + b"\n", 1, "not a call"),
    ],
)
def test_a_line_that_is_not_exactly_a_call_is_a_malformed_frame(line: bytes, expected: int, why: str) -> None:
    with pytest.raises(MalformedFrame) as error:
        read_request(line, expected)
    assert why in error.value.reason


def test_a_frame_past_the_most_a_line_may_be_is_refused_before_it_is_read() -> None:
    with pytest.raises(MalformedFrame, match="bytes"):
        read_request(b"x" * (MAX_FRAME_BYTES + 1) + b"\n", 1)


def test_a_reply_is_one_ascii_line_whatever_the_result_holds() -> None:
    nasty: str = "line one\nline two\r  åäö 中文 \U0001f600 \x00 \x1b[0m"
    line: bytes = reply_frame(4, BridgeAnswer(ok=True, text=nasty, images=("/a/b.png",)))
    assert line.endswith(b"\n") and line.count(b"\n") == 1 and line.isascii()
    assert json.loads(line) == {"id": 4, "ok": True, "text": nasty, "images": ["/a/b.png"]}
    failed: bytes = reply_frame(5, BridgeAnswer(ok=False, error="Not allowed: no\nnever", refused=True))
    assert failed.count(b"\n") == 1
    assert json.loads(failed) == {"id": 5, "ok": False, "error": "Not allowed: no\nnever", "refused": True}


# What the code writes to the bridge in place of a call: each ends it, whatever it says, and the daemon answers nothing.
_GARBAGE: dict[str, str] = {
    "not json": "garbage",
    "an array": "[1, 2, 3]",
    "no tool": '{"id": 1, "args": {}}',
    "another key": '{"id": 1, "tool": "t", "args": {}, "more": 1}',
    "a call out of its turn": '{"id": 5, "tool": "t", "args": {}}',
    "a string id": '{"id": "1", "tool": "t", "args": {}}',
    "arguments that are no object": '{"id": 1, "tool": "t", "args": [1]}',
}


@pytest.mark.macos
@pytest.mark.parametrize("name", list(_GARBAGE))
def test_a_malformed_frame_ends_the_code_and_no_tool_is_called(name: str, tmp_path: Path) -> None:
    code_box: Box = box(tmp_path)
    garbage: str = _GARBAGE[name]
    ended: Ended = code_box.start(
        "import dot_tools._bridge as bridge, time\n"
        f"bridge._requests.write({garbage!r} + '\\n'); bridge._requests.flush()\n"
        "time.sleep(5)\nprint('the code went on')"
    )
    assert ended.ending == "protocol", name
    assert ended.seconds < 4.0 and "the code went on" not in ended.output
    assert code_box.calls == []


@pytest.mark.macos
def test_a_frame_that_never_ends_a_line_or_is_too_long_ends_the_code(tmp_path: Path) -> None:
    code_box: Box = box(tmp_path)
    half: Ended = code_box.start(
        "import dot_tools._bridge as bridge, os\n"
        'os.write(bridge._requests.fileno(), b\'{"id": 1, "tool"\')\nos._exit(0)'
    )
    # The pipe closes in the middle of a frame: the daemon reads a line that is not one.
    assert half.ending == "protocol" and code_box.calls == []
    long: Ended = code_box.start(
        "import dot_tools._bridge as bridge, os\n"
        f"os.write(bridge._requests.fileno(), b'x' * {MAX_FRAME_BYTES + 10})\nimport time\ntime.sleep(5)"
    )
    assert long.ending == "protocol" and code_box.calls == []


@pytest.mark.macos
def test_a_call_that_is_well_formed_is_answered_and_a_refusal_reaches_the_code_as_one(tmp_path: Path) -> None:
    code_box: Box = box(tmp_path)
    code_box.says("read_file", '{"rows": [1, 2, 3]}')
    code_box.refuses("write_file", "Not allowed: the owner said no. Do not retry this action.")
    ended: Ended = code_box.start(
        "from dot_tools._bridge import call, ToolRefused, ToolError\n"
        "print(call('read_file', {'path': 'a'}))\n"
        "try:\n    call('write_file', {'path': 'b'})\nexcept ToolRefused as error:\n    print('refused:', error)\n"
        "print(isinstance(ToolRefused('x'), ToolError))"
    )
    assert ended.ending == "finished"
    assert ended.output.splitlines() == [
        "{'rows': [1, 2, 3]}",
        "refused: Not allowed: the owner said no. Do not retry this action.",
        "True",
    ]
    assert [call for call, _ in code_box.calls] == ["read_file", "write_file"]


@pytest.mark.macos
def test_the_code_cannot_make_the_daemon_hold_replies_it_never_reads(tmp_path: Path) -> None:
    code_box: Box = box(tmp_path)
    code_box.says("big", json.dumps({"text": "y" * 3_000_000}))
    # A code that sends call after call and reads no answer is held at the first reply: the daemon does not read its
    # next request until the answer is read, so nothing piles up in the daemon and the code's own time runs out.
    ended: Ended = code_box.start(
        "import dot_tools._bridge as bridge, time\n"
        "for number in range(1, 40):\n"
        '    bridge._requests.write(f\'{{"id": {number}, "tool": "big", "args": {{}}}}\\n\')\n'
        "    bridge._requests.flush()\ntime.sleep(60)",
        seconds=2.0,
    )
    assert ended.ending == "timeout"
    assert len(code_box.calls) <= 2
    assert os.path.exists(code_box.layout.scratch)

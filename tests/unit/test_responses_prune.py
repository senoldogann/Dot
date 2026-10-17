"""What the Responses format sends back of earlier turns (item 351): a run of reasoning items before a call or a message
goes back whole; before, only the last of them was kept, and the thinking of the others was lost to the next step."""

from dotagent.core.types import JsonObject
from dotagent.llm.codec_responses import prune_unpaired


def _reasoning(identifier: str) -> JsonObject:
    return {"type": "reasoning", "id": identifier, "summary": [], "encrypted_content": f"ENC-{identifier}"}


def test_a_run_of_reasoning_before_a_call_or_a_message_is_kept_whole() -> None:
    call: JsonObject = {"type": "function_call", "call_id": "call_1", "name": "list_dir", "arguments": "{}"}
    output: JsonObject = {"type": "function_call_output", "call_id": "call_1", "output": "a.txt"}
    message: JsonObject = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Done."}],
    }
    items: list[JsonObject] = [
        _reasoning("rs_1"),
        _reasoning("rs_2"),
        call,
        output,
        _reasoning("rs_3"),
        _reasoning("rs_4"),
        message,
        # Reasoning whose call lost its result, and reasoning at the end, precede nothing that is sent.
        _reasoning("rs_5"),
        {"type": "function_call", "call_id": "call_2", "name": "list_dir", "arguments": "{}"},
        _reasoning("rs_6"),
    ]
    assert prune_unpaired(items) == [
        _reasoning("rs_1"),
        _reasoning("rs_2"),
        call,
        output,
        _reasoning("rs_3"),
        _reasoning("rs_4"),
        message,
    ]

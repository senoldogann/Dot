"""What the Responses format sends back of earlier turns (item 351): a run of reasoning items before a call or a message
goes back whole; before, only the last of them was kept, and the thinking of the others was lost to the next step."""

from dotagent.core.types import JsonObject
from dotagent.llm.codec_responses import ResponsesState, initial_responses_state, prune_unpaired, responses_reply


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


def test_a_call_whose_id_is_empty_or_given_twice_gets_one_of_its_own() -> None:
    """Every call of a reply has an id of its own, as the chat codec gives one (item 386): a result answers its call
    only by its id, and the next request carries the calls back by these ids (``store`` is off)."""
    calls: tuple[JsonObject, ...] = (
        {"type": "function_call", "call_id": "", "name": "list_dir", "arguments": "{}"},
        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": '{"path": "a.txt"}'},
        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": '{"path": "b.txt"}'},
    )
    state: ResponsesState = initial_responses_state("gpt-test").model_copy(update={"final_output": calls})
    ids: list[str] = [call.id for call in responses_reply(state).tool_calls]
    assert ids[1] == "call_1" and len(set(ids)) == 3 and all(ids)

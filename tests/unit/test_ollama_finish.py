"""How a reply Ollama streamed without tool calls is told to have ended."""

import pytest

from dotagent.core.types import JsonObject
from dotagent.llm.ollama import StreamState, apply_chunk, finish_reason, initial_state
from dotagent.llm.types import FinishReason


@pytest.mark.parametrize(
    ("said", "finish"),
    [
        ({"done_reason": "stop"}, "stop"),
        ({"done_reason": ""}, "stop"),
        ({}, "stop"),
        ({"done_reason": "length"}, "length"),
        ({"done_reason": "unload"}, "other"),
    ],
)
def test_the_end_of_a_reply_is_what_ollama_said_and_a_stop_when_it_said_nothing(
    said: JsonObject, finish: FinishReason
) -> None:
    chunk: JsonObject = {"model": "m", "message": {"role": "assistant", "content": "Done."}, "done": True, **said}
    state: StreamState = apply_chunk(initial_state("m"), chunk, {"model": "m"})[0]
    assert finish_reason(state) == finish

"""How a reply Ollama streamed without tool calls is told to have ended."""

import pytest

from dotagent.core.errors import ProviderHttpError
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


def test_an_error_inside_the_stream_keeps_the_words_of_ollama() -> None:
    """Item 185: an error Ollama reports inside its stream is raised with its words and the status the table of stream
    errors gives it (500 for one it does not name); before, its words were only in the context, under an invented
    502."""
    words: str = "model requires more system memory (12.0 GiB) than is available (8.0 GiB)"
    with pytest.raises(ProviderHttpError) as raised:
        apply_chunk(initial_state("m"), {"error": words}, {"model": "m"})
    assert raised.value.status == 500
    assert raised.value.message == f"provider answered HTTP 500: {words}"

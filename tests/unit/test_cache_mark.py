"""The prompt cache past the elision point (item 237). Past 60 % of a run's budget each step shortens the tool result
that leaves the newest six, further back from the end of the prompt than the 20 positions Anthropic looks back from a
cache mark (a run of tool_use blocks, or of tool_result blocks, is one position), so a mark at the end alone found
nothing to read and every such step read the whole prompt anew. A step's request now also marks the end of the part
of the run the next step sends unchanged."""

from dotagent.agent.compact import last_settled, shown_key, shrink_old_tool_results, with_shown
from dotagent.core.text import truncate_middle
from dotagent.core.types import JsonObject
from dotagent.llm.anthropic import claude_traits
from dotagent.llm.codec_messages import MessagesOptions, build_messages_body
from dotagent.llm.traits import ModelTraits
from dotagent.llm.types import (
    ChatRequest,
    ImageData,
    Message,
    ToolCall,
    assistant_message,
    system_message,
    tool_message,
    user_message,
)

_LOOKBACK: int = 20
# The defaults of [agent] keep_recent_tool_results and keep_recent_screenshots.
_KEEP_RESULTS: int = 6
_KEEP_IMAGES: int = 2
_ORIGIN: str = "anthropic"
_CLAUDE: MessagesOptions = MessagesOptions(
    adaptive_thinking=True, prompt_cache=True, settled_mark=True, default_max_tokens=None, long_ttl=True
)
_GATEWAY: MessagesOptions = MessagesOptions(
    adaptive_thinking=False, prompt_cache=True, settled_mark=False, default_max_tokens=32768, long_ttl=False
)
_OPUS: ModelTraits = claude_traits(
    {
        "id": "claude-opus-5-5",
        "max_input_tokens": 1_000_000,
        "max_tokens": 128_000,
        "capabilities": {
            "image_input": {"supported": True},
            "effort": {"supported": True, "low": {"supported": True}, "high": {"supported": True}},
            "thinking": {"supported": True, "types": {"adaptive": {"supported": True}}},
        },
    }
)
_SCREENSHOT: ImageData = ImageData(media_type="image/png", data_b64="iVBORw0KGgo=")

type Position = tuple[JsonObject, ...]


def _step(number: int) -> list[Message]:
    """One step as Dot keeps it: the model's turn as Anthropic sent it (thinking, text, a call), and a long result."""
    call: ToolCall = ToolCall(id=f"toolu_{number}", name="read_file", arguments={"path": f"notes/{number}.md"})
    turn: Message = Message(
        role="assistant",
        content=f"Reading part {number}.",
        images=(),
        tool_calls=(call,),
        tool_call_id=None,
        tool_name=None,
        thinking="Next part.",
        raw=(
            {"type": "thinking", "thinking": "Next part.", "signature": f"sig-{number}"},
            {"type": "text", "text": f"Reading part {number}."},
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments},
        ),
        raw_origin=_ORIGIN,
    )
    return [turn, tool_message(call, f"A line of part {number}.\n" * 200, ())]


def _request(history: list[Message]) -> ChatRequest:
    """A step's request as the loop makes it: the system message, then the history, marked where it settles."""
    settled: int | None = last_settled(history, _KEEP_RESULTS, _KEEP_IMAGES)
    return ChatRequest(
        model="claude-opus-5-5",
        messages=(system_message("You are Dot."), *history),
        tools=(),
        temperature=None,
        max_output_tokens=None,
        effort="high",
        speed="standard",
        session_key="thread",
        cache_mark=None if settled is None else settled + 1,
        prompt_reuse="conversation",
        window=None,
    )


def _positions(body: JsonObject) -> list[Position]:
    """The blocks of the body's messages as Anthropic's lookback counts them."""
    positions: list[Position] = []
    messages: object = body["messages"]
    assert isinstance(messages, list)
    for turn in messages:
        for block in turn["content"]:
            kind: str = block["type"]
            if positions and kind in ("tool_use", "tool_result") and positions[-1][-1]["type"] == kind:
                positions[-1] = (*positions[-1], block)
            else:
                positions.append((block,))
    return positions


def _plain(position: Position) -> Position:
    return tuple({key: value for key, value in block.items() if key != "cache_control"} for block in position)


def _marks(positions: list[Position]) -> list[int]:
    return [index for index, position in enumerate(positions) if any("cache_control" in block for block in position)]


def test_the_step_after_a_shortening_reads_the_run_before_it_from_the_cache() -> None:
    # Every step past the elision point, as the loop trims the history before it asks the model.
    history: list[Message] = [user_message("Summarize the notes.", ())]
    for number in range(1, 13):
        history = shrink_old_tool_results([*history, *_step(number)], _KEEP_RESULTS)
    before: list[Position] = _positions(build_messages_body(_request(history), _OPUS, _ORIGIN, _CLAUDE))
    history = shrink_old_tool_results([*history, *_step(13)], _KEEP_RESULTS)
    after: list[Position] = _positions(build_messages_body(_request(history), _OPUS, _ORIGIN, _CLAUDE))
    # The step changed the prompt 20 positions or more before its end, out of reach of a mark at the end alone.
    changed: int = next(
        index for index, (old, new) in enumerate(zip(before, after, strict=False)) if _plain(old) != _plain(new)
    )
    assert len(after) - 1 - changed >= _LOOKBACK
    # What the step before wrote to the cache at its mark is where this step starts, within the reach of this mark.
    (mark_before,) = _marks(before)
    (mark_after,) = _marks(after)
    assert mark_before < changed
    assert [_plain(position) for position in before[: mark_before + 1]] == [
        _plain(position) for position in after[: mark_before + 1]
    ]
    assert 0 < mark_after - mark_before < _LOOKBACK


def test_the_settled_part_ends_before_what_a_later_step_may_still_shorten_or_strip() -> None:
    steps: list[Message] = [message for number in range(1, 9) for message in _step(number)]
    # A screenshot older than the newest six results is stripped once two newer ones come.
    shot: Message = tool_message(
        ToolCall(id="toolu_shot", name="screenshot", arguments={}), "The page.", (_SCREENSHOT,)
    )
    history: list[Message] = [user_message("Look.", ()), *steps[:2], shot, *steps[2:]]
    assert last_settled(history, _KEEP_RESULTS, _KEEP_IMAGES) == 2
    # With no result and no screenshot yet, the whole run is settled.
    assert last_settled(history[:1], _KEEP_RESULTS, _KEEP_IMAGES) == 0
    # The gateway takes no mark inside the conversation, and a mark on the newest message is the automatic one.
    gateway: list[Position] = _positions(build_messages_body(_request(history), _OPUS, _ORIGIN, _GATEWAY))
    assert _marks(gateway) == []
    alone: list[Position] = _positions(build_messages_body(_request(history[:1]), _OPUS, _ORIGIN, _CLAUDE))
    assert _marks(alone) == []


def test_old_output_is_shortened_once_and_then_left_as_it_is() -> None:
    # The mark of what was dropped used to come on top of the size, so each of the next steps shortened it again.
    once: str = truncate_middle("A line of output.\n" * 300, 700)
    assert len(once) <= 700
    assert truncate_middle(once, 700) == once


def test_a_question_asked_once_writes_no_cache_and_a_step_does() -> None:
    # A review or a summary is asked once: a cache written for it is paid for and never read (item 1010).
    history: list[Message] = [user_message("Is this step what the owner asked for?", ())]
    step: ChatRequest = _request(history)
    once: ChatRequest = step.model_copy(update={"prompt_reuse": "single", "cache_mark": None})
    for options in (_CLAUDE, _GATEWAY):
        asked: JsonObject = build_messages_body(once, _OPUS, _ORIGIN, options)
        assert "cache_control" not in asked
        assert _marks(_positions(asked)) == []
        assert build_messages_body(step, _OPUS, _ORIGIN, options)["cache_control"] == {"type": "ephemeral"}


def test_the_words_shown_next_to_results_stand_on_the_newest_results_alone_past_the_settled_mark() -> None:
    """Item 1192: the lessons shown next to a call's result in a step's request, which nothing stores, stand on the
    newest results that trimming leaves whole: the part of the prompt the cache keeps never changes for them, and a
    result that grows old loses them."""
    calls: list[ToolCall] = [
        ToolCall(id=f"call_{index}", name="read_file", arguments={"path": f"{index}.txt"}) for index in range(9)
    ]
    history: list[Message] = [user_message("Read the files.", ())]
    for call in calls:
        history.append(assistant_message("", None, (call,)))
        history.append(tool_message(call, f"text of {call.id}", ()))
    first, last = history[2], history[-1]
    seen: list[Message] = with_shown(history, {shown_key(first): "OLD", shown_key(last): "NEW"}, _KEEP_RESULTS)
    settled: int | None = last_settled(history, _KEEP_RESULTS, _KEEP_IMAGES)
    assert settled is not None
    assert seen[: settled + 1] == history[: settled + 1]
    assert [message.content for message in seen if message.role == "tool"][-1] == "text of call_8\n\nNEW"
    assert all("OLD" not in message.content for message in seen)
    assert with_shown(history, {}, _KEEP_RESULTS) == history
    # The words go with the result they came with, not with another call that a provider gave the same id, and only
    # once where two such calls read the same text.
    twin: Message = tool_message(ToolCall(id="call_8", name="read_file", arguments={}), "another text", ())
    assert with_shown([*history, twin], {shown_key(last): "NEW"}, _KEEP_RESULTS)[-1] == twin
    same: Message = tool_message(ToolCall(id="call_8", name="read_file", arguments={}), last.content, ())
    doubled: list[Message] = with_shown([*history, same], {shown_key(last): "NEW"}, _KEEP_RESULTS)
    assert doubled[-2].content.endswith("NEW") and doubled[-1] == same

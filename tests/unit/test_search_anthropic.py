"""The Anthropic rung (item 69): the one Messages request of a search and the reading of its answer."""

import json

import pytest

from dotagent.core.errors import WebError, WebThrottledError
from dotagent.store.stats import SearchCount, ToolSample, search_counts
from dotagent.web.search import anthropic_search_body, parse_anthropic

# An answer in the documented shape: the relay's tool use, the tool result with two pages, and a cited sentence.
ANSWER: dict[str, object] = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "dot agent macos"}},
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://example.com/dot",
                    "title": "Dot, an agent for the Mac",
                    "encrypted_content": "AAAA",
                    "page_age": "October 3, 2026",
                },
                {
                    "type": "web_search_result",
                    "url": "https://example.org/agents",
                    "title": "Agents on macOS",
                    "encrypted_content": "BBBB",
                    "page_age": None,
                },
                {
                    "type": "web_search_result",
                    "url": "https://example.com/dot",
                    "title": "Dot, again",
                    "encrypted_content": "CCCC",
                    "page_age": None,
                },
            ],
        },
        {
            "type": "text",
            "text": "Dot runs on the Mac.",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://example.com/dot",
                    "title": "Dot, an agent for the Mac",
                    "encrypted_index": "x",
                    "cited_text": "Dot is an agent that works on your Mac.",
                }
            ],
        },
    ],
    "usage": {"input_tokens": 100, "output_tokens": 30, "server_tool_use": {"web_search_requests": 1}},
}


def _error(code: str) -> bytes:
    return json.dumps(
        {
            "content": [
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": {"type": "web_search_tool_result_error", "error_code": code},
                }
            ]
        }
    ).encode()


def test_the_request_is_one_search_relayed_by_the_model() -> None:
    body = json.loads(anthropic_search_body("claude-haiku-5-5", "dot agent macos"))
    assert body["model"] == "claude-haiku-5-5"
    assert body["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}]
    assert body["messages"] == [{"role": "user", "content": "dot agent macos"}]
    assert "exactly once" in body["system"]


def test_the_results_become_hits_with_the_cited_text_as_snippet_each_address_once() -> None:
    hits = parse_anthropic(json.dumps(ANSWER).encode(), "dot agent macos", 10)
    assert [hit.url for hit in hits] == ["https://example.com/dot", "https://example.org/agents"]
    assert hits[0].title == "Dot, an agent for the Mac"
    assert hits[0].snippet == "Dot is an agent that works on your Mac. (October 3, 2026)"
    assert hits[1].snippet == ""
    assert len(parse_anthropic(json.dumps(ANSWER).encode(), "dot agent macos", 1)) == 1


def test_a_limiting_or_unavailable_service_benches_the_rung_and_another_error_fails_the_search() -> None:
    with pytest.raises(WebThrottledError, match="too_many_requests"):
        parse_anthropic(_error("too_many_requests"), "q", 5)
    with pytest.raises(WebThrottledError, match="unavailable"):
        parse_anthropic(_error("unavailable"), "q", 5)
    with pytest.raises(WebError, match="query_too_long"):
        parse_anthropic(_error("query_too_long"), "q", 5)
    with pytest.raises(WebError, match="documented shape"):
        parse_anthropic(b'{"content": "no"}', "q", 5)


def test_an_answer_with_no_search_in_it_is_no_answer_and_a_search_that_found_nothing_is() -> None:
    """Item 554: the relay may answer or decline without using the tool, which is no result of a search: the ladder asks
    the next rung and the empty answer is not kept. A search that ran and found nothing has its result block."""
    declined: bytes = json.dumps(
        {"content": [{"type": "text", "text": "I cannot search for that.", "citations": None}]}
    ).encode()
    with pytest.raises(WebError, match="did not search") as refused:
        parse_anthropic(declined, "q", 5)
    assert not isinstance(refused.value, WebThrottledError) and refused.value.context == {"query": "q"}
    with pytest.raises(WebError, match="did not search"):
        parse_anthropic(b'{"content": []}', "q", 5)
    nothing: bytes = json.dumps(
        {"content": [{"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []}]}
    ).encode()
    assert parse_anthropic(nothing, "q", 5) == ()


def _sample(tool: str, ok: bool, text: str) -> ToolSample:
    return ToolSample(model="m", tool=tool, ok=ok, seconds=0.1, waited_s=0.0, text=text)


def test_the_searches_are_counted_by_the_engine_that_answered() -> None:
    samples = [
        _sample("web_search", True, "10 results for 'a' from ollama. Read the best ones with web_fetch.\n..."),
        _sample("web_search", True, "3 results for 'b' from anthropic (duckduckgo: benched). Read the best ones."),
        _sample("web_search", True, "No results for 'c' from anthropic. Try other words or a broader query."),
        _sample("web_search", False, "ollama: the key is missing"),
        _sample("web_fetch", True, "2 results for 'd' from brave"),
    ]
    assert search_counts(samples) == (
        SearchCount(engine="anthropic", searches=2),
        SearchCount(engine="ollama", searches=1),
    )

"""The photo path of ``generate_image`` (item 1969): the request body of Gemini's ``generateContent`` and the picture
read out of its answer, on an answer shaped as the API reference gives it
(``tests/fixtures/gemini_image_answer.json``; no call is made), and the page the drawn path hands the browser."""

import base64
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.core.errors import ProviderFilterError, ProviderProtocolError, ToolInputError
from dotagent.core.types import JsonObject
from dotagent.llm.google_image import ImageRequest, image_body, image_from_answer
from dotagent.tools.images import GenerateImageArgs, drawn_page

ANSWER: JsonObject = json.loads((Path(__file__).parents[1] / "fixtures" / "gemini_image_answer.json").read_text())
CONTEXT: JsonObject = {"url": "https://example.test/v1/models/m:generateContent"}


def test_the_body_asks_for_the_picture_alone_and_names_its_shape_only_when_given() -> None:
    plain: JsonObject = image_body(ImageRequest(model="m", prompt="a red fox", aspect_ratio=None))
    assert plain == {
        "contents": [{"parts": [{"text": "a red fox"}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    shaped: JsonObject = image_body(ImageRequest(model="m", prompt="a red fox", aspect_ratio="16:9"))
    assert shaped["generationConfig"] == {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}}


def test_the_picture_its_words_and_its_tokens_are_read_from_the_answer() -> None:
    made = image_from_answer(ANSWER, CONTEXT)
    assert made.media_type == "image/png" and made.data.startswith(b"\x89PNG")
    assert made.note == "Here is the picture."
    assert (made.usage.input_tokens, made.usage.output_tokens) == (9, 1120)


def test_a_thought_picture_is_a_draft_and_not_the_answer() -> None:
    draft: JsonObject = {
        "thought": True,
        "inlineData": {"mimeType": "image/png", "data": base64.b64encode(b"x").decode()},
    }
    final: JsonObject = {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(b"\x89PNG-final").decode()}}
    answer: JsonObject = {"candidates": [{"content": {"parts": [draft, final]}, "finishReason": "STOP"}]}
    assert image_from_answer(answer, CONTEXT).data == b"\x89PNG-final"


def test_a_blocked_prompt_and_a_stopped_reply_are_the_content_filter_not_a_broken_answer() -> None:
    blocked: JsonObject = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(ProviderFilterError) as refused:
        image_from_answer(blocked, CONTEXT)
    assert refused.value.context["block_reason"] == "SAFETY"
    stopped: JsonObject = {"candidates": [{"finishReason": "IMAGE_SAFETY"}]}
    with pytest.raises(ProviderFilterError):
        image_from_answer(stopped, CONTEXT)


def test_an_answer_with_words_and_no_picture_says_what_the_model_wrote() -> None:
    said: JsonObject = {
        "candidates": [{"content": {"parts": [{"text": "I cannot draw that."}]}, "finishReason": "STOP"}]
    }
    with pytest.raises(ProviderProtocolError) as broken:
        image_from_answer(said, CONTEXT)
    assert broken.value.context["said"] == "I cannot draw that."
    assert broken.value.context["url"] == CONTEXT["url"]


def test_a_picture_that_is_not_base64_is_a_broken_answer() -> None:
    bad: JsonObject = {
        "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "@@@"}}]}}]
    }
    with pytest.raises(ProviderProtocolError):
        image_from_answer(bad, CONTEXT)


def test_a_drawing_is_given_as_one_of_svg_html_or_photo_and_a_photo_takes_no_size() -> None:
    with pytest.raises(ValidationError, match="exactly one of svg, html or photo"):
        GenerateImageArgs.model_validate({"path": "a.png"})
    with pytest.raises(ValidationError, match="exactly one"):
        GenerateImageArgs.model_validate({"path": "a.png", "svg": "<svg/>", "photo": "a fox"})
    with pytest.raises(ValidationError, match=r"must end in \.png"):
        GenerateImageArgs.model_validate({"path": "a.jpg", "svg": "<svg/>"})
    with pytest.raises(ValidationError, match="a photo takes aspect_ratio"):
        GenerateImageArgs.model_validate({"path": "a.png", "photo": "a fox", "width": 800})
    with pytest.raises(ValidationError, match="a drawn picture takes width and height"):
        GenerateImageArgs.model_validate({"path": "a.png", "svg": "<svg/>", "aspect_ratio": "1:1"})
    with pytest.raises(ValidationError):
        GenerateImageArgs.model_validate({"path": "a.png", "svg": "<svg/>", "width": 10_000})
    photo: GenerateImageArgs = GenerateImageArgs.model_validate(
        {"path": "a.png", "photo": "a fox", "aspect_ratio": "4:3"}
    )
    assert photo.aspect_ratio == "4:3"


def test_the_page_of_a_drawing_bars_scripts_and_requests_before_anything_the_run_wrote() -> None:
    page: str = drawn_page(None, "<h1>Hi</h1><script>fetch('/x')</script>", 600, 300)
    assert page.startswith('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy"')
    policy: str = page.split('content="', 1)[1].split('"', 1)[0]
    assert "script-src 'none'" in policy and "default-src 'none'" in policy and "connect-src 'none'" in policy
    assert "img-src data:" in policy
    assert page.endswith("<h1>Hi</h1><script>fetch('/x')</script>")
    assert "width:600px;height:300px" in page


def test_an_svg_is_drawn_as_a_picture_and_text_that_is_no_svg_is_refused() -> None:
    page: str = drawn_page('<svg viewBox="0 0 10 10"><rect width="10" height="10"/></svg>', None, 640, 480)
    assert '<img src="data:image/svg+xml;base64,' in page and 'width="640" height="480"' in page
    assert "<rect" not in page
    with pytest.raises(ToolInputError, match="not an SVG document"):
        drawn_page("<p>no</p>", None, 640, 480)
    with pytest.raises(ToolInputError, match="svg or as html"):
        drawn_page(None, None, 640, 480)

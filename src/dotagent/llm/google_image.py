"""Gemini's picture generation (``generate_image``'s photo path): the request body of
``models/<id>:generateContent`` and the picture read out of its answer.

The Gemini API generates pictures on the same ``generateContent`` route as text. The prompt is one text part; the answer
holds the picture as an ``inlineData`` part (base64 bytes and their media type), and may hold text parts and, for a
model that thinks first, thought parts (``"thought": true``) whose pictures are drafts and are not the answer. Pictures
are not on the free tier of a Google AI Studio key: the project needs billing (docs/image-generation.md).
"""

import base64
import binascii

from dotagent.core.errors import ProviderFilterError, ProviderProtocolError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import Usage
from dotagent.llm.wire import get_int, get_obj, get_objects, get_str

# A shape as the API reads it, "16:9": which ones a model serves is Google's to say, and it answers a shape it does not
# serve with an error that names it.
ASPECT_RATIO_PATTERN: str = r"^\d{1,2}:\d{1,2}$"


class ImageRequest(FrozenModel):
    """One picture to generate: the words that describe it and, when the caller cares, its shape (``None`` leaves the
    shape to the model)."""

    model: str
    prompt: str
    aspect_ratio: str | None


class GeneratedImage(FrozenModel):
    """What came back: the picture's bytes as the API sent them, their media type, any words the model wrote beside
    the picture and the tokens the call used."""

    data: bytes
    media_type: str
    note: str
    usage: Usage


def image_body(request: ImageRequest) -> JsonObject:
    """The JSON body of the call. Only the picture is asked for, so the model does not spend tokens on words."""
    config: JsonObject = {"responseModalities": ["IMAGE"]}
    if request.aspect_ratio is not None:
        config["imageConfig"] = {"aspectRatio": request.aspect_ratio}
    return {"contents": [{"parts": [{"text": request.prompt}]}], "generationConfig": config}


def _usage(answer: JsonObject) -> Usage:
    """The tokens the call used: what was sent, and what was written, the model's thinking among it."""
    meta: JsonObject = get_obj(answer, "usageMetadata")
    written: int = get_int(meta, "candidatesTokenCount") + get_int(meta, "thoughtsTokenCount")
    return Usage(input_tokens=get_int(meta, "promptTokenCount"), output_tokens=written, cached_tokens=0)


def image_from_answer(answer: JsonObject, context: JsonObject) -> GeneratedImage:
    """The picture of the answer. A prompt Google blocked, or a reply it stopped for safety, raises the filter error; an
    answer with no picture raises the protocol error with what the model wrote instead, so the caller can say why."""
    blocked: str = get_str(get_obj(answer, "promptFeedback"), "blockReason")
    if blocked:
        raise ProviderFilterError("Google blocked the prompt", {**context, "block_reason": blocked})
    candidates: list[JsonObject] = get_objects(answer, "candidates")
    first: JsonObject = candidates[0] if candidates else {}
    parts: list[JsonObject] = get_objects(get_obj(first, "content"), "parts")
    notes: list[str] = []
    found: JsonObject | None = None
    for part in parts:
        if part.get("thought") is True:
            continue
        inline: JsonObject = get_obj(part, "inlineData")
        if get_str(inline, "data") and found is None:
            found = inline
        elif get_str(part, "text"):
            notes.append(get_str(part, "text"))
    finish: str = get_str(first, "finishReason")
    if found is None:
        if finish in ("SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            raise ProviderFilterError("Google stopped the picture for safety", {**context, "finish_reason": finish})
        raise ProviderProtocolError(
            "the answer holds no picture",
            {**context, "finish_reason": finish, "said": " ".join(notes)[:500], "parts": len(parts)},
        )
    try:
        data: bytes = base64.b64decode(get_str(found, "data"), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ProviderProtocolError(
            "the picture in the answer is not base64", {**context, "error": str(error)}
        ) from error
    media_type: str = get_str(found, "mimeType") or "image/png"
    return GeneratedImage(data=data, media_type=media_type, note=" ".join(notes), usage=_usage(answer))

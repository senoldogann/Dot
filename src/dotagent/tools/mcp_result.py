"""What the model reads back from an MCP tool call: the content blocks of the server as text and pictures, fenced as data."""

import asyncio
import base64
import binascii

from mcp.types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from dotagent.core.errors import McpError, ToolExecutionError
from dotagent.core.images import EncodedImage, encode_image
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import truncate_tail
from dotagent.llm.types import ImageData
from dotagent.tools.base import ToolResult

MAX_IMAGES: int = 4
FENCE_OVERHEAD_CHARS: int = 300
_NO_CONTENT: str = "(the tool returned no content)"


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fence_mcp(server: str, tool: str, body: str, nonce: str) -> str:
    """Wrap what a server returned so the prompt can state that everything inside is data and only ``nonce`` closes it."""
    safe: str = body.replace("</mcp_content", "<\\/mcp_content")
    opening: str = f'<mcp_content nonce="{nonce}" server={_quote(server)} tool={_quote(tool)}>'
    return f'{opening}\n{safe}\n</mcp_content nonce="{nonce}">'


def _describe_block(block: ContentBlock) -> str:
    """Text for every kind of block that is not a picture."""
    match block:
        case TextContent():
            return block.text
        case AudioContent():
            return f"[audio, {block.mime_type}, {len(block.data)} base64 characters; it cannot be played here]"
        case ResourceLink():
            detail: str = f" ({block.mime_type})" if block.mime_type else ""
            return f"[resource link {block.name}{detail}: {block.uri}]"
        case EmbeddedResource(resource=TextResourceContents() as resource):
            return f"[resource {resource.uri}]\n{resource.text}"
        case EmbeddedResource(resource=BlobResourceContents() as resource):
            return f"[binary resource {resource.uri}, {resource.mime_type or 'unknown type'}, {len(resource.blob)} base64 characters]"
        case _:
            raise McpError(
                "an MCP server returned a kind of content that is not supported", {"type": type(block).__name__}
            )


async def _render_image(block: ImageContent, number: int, max_edge: int, quality: int) -> tuple[str, ImageData | None]:
    """A line for the text, and the picture itself when it could be read."""
    try:
        raw: bytes = base64.b64decode(block.data, validate=True)
    except binascii.Error as exc:
        return f"[image {number}: the data is not valid base64 ({exc})]", None
    try:
        encoded: EncodedImage = await asyncio.to_thread(encode_image, raw, max_edge, quality)
    except ToolExecutionError as exc:
        return f"[image {number}, {block.mime_type}: {exc.message}]", None
    return f"[image {number}: {encoded.source_width}x{encoded.source_height}px]", encoded.image


async def render_call_result(
    server: str, tool: str, result: CallToolResult, text_chars: int, max_edge: int, quality: int, nonce: str
) -> ToolResult:
    """The answer of a tool as the model sees it. Everything a server returns counts as untrusted input."""
    parts: list[str] = []
    images: list[ImageData] = []
    pictures: int = 0
    for block in result.content:
        if not isinstance(block, ImageContent):
            parts.append(_describe_block(block))
            continue
        pictures += 1
        if pictures > MAX_IMAGES:
            parts.append(f"[image {pictures}: not shown; at most {MAX_IMAGES} pictures per result]")
            continue
        note, image = await _render_image(block, pictures, max_edge, quality)
        parts.append(note)
        if image is not None:
            images.append(image)
    if not parts and result.structured_content is not None:
        parts.append(dumps_compact(result.structured_content))
    body: str = "\n".join(parts) if parts else _NO_CONTENT
    text: str = fence_mcp(server, tool, truncate_tail(body, text_chars), nonce)
    return ToolResult(text=text, images=tuple(images), is_error=result.is_error, untrusted=True)

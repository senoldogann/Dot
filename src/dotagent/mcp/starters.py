"""Turn MCP prompt messages into an editable composer draft."""

from mcp.types import EmbeddedResource, GetPromptResult, TextContent, TextResourceContents

from dotagent.core.errors import McpError

_MAX_DRAFT_CHARS: int = 40_000


def prompt_draft(result: GetPromptResult, server: str, name: str) -> str:
    """Keep text in message order, with speaker labels for a multi-turn conversation."""
    messages: list[str] = []
    for message in result.messages:
        match message.content:
            case TextContent(text=text) | EmbeddedResource(resource=TextResourceContents(text=text)):
                pass
            case other:
                raise McpError(
                    "this MCP starter contains content that cannot be put in a text draft",
                    {"server": server, "prompt": name, "type": other.type},
                )
        label: str = f"{message.role.capitalize()}: " if len(result.messages) > 1 or message.role != "user" else ""
        messages.append(label + text)
    draft: str = "\n\n".join(messages)
    if not draft.strip() or len(draft) > _MAX_DRAFT_CHARS:
        raise McpError(
            "the MCP starter is empty or too large for a draft",
            {"server": server, "prompt": name, "chars": len(draft), "limit": _MAX_DRAFT_CHARS},
        )
    return draft

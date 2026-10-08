"""Typed errors. Every error carries structured, JSON-serialisable debug context."""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import JsonValue


class DotError(Exception):
    """Base class: a human-readable message plus structured debug context."""

    def __init__(self, message: str, context: Mapping[str, JsonValue]) -> None:
        super().__init__(message)
        self.message: str = message
        self.context: dict[str, JsonValue] = dict(context)

    def __str__(self) -> str:
        rendered: str = json.dumps(self.context, ensure_ascii=False, default=str)
        return f"{self.message} | context={rendered}"


class ConfigError(DotError):
    """Configuration is missing, malformed or inconsistent."""


class JsonParseError(DotError):
    """A payload that must be a JSON object was not."""


class ProviderError(DotError):
    """A model provider call failed."""


class ProviderHttpError(ProviderError):
    """The provider answered with a non-success HTTP status."""

    def __init__(self, message: str, status: int, context: Mapping[str, JsonValue]) -> None:
        super().__init__(message, {**context, "status": status})
        self.status: int = status


class ProviderAuthError(ProviderHttpError):
    """401/403: missing, invalid or unauthorised credentials."""


class ProviderRateLimitError(ProviderHttpError):
    """429: rate or usage limit reached."""

    def __init__(
        self, message: str, status: int, retry_after_s: float | None, context: Mapping[str, JsonValue]
    ) -> None:
        super().__init__(message, status, context)
        self.retry_after_s: float | None = retry_after_s


class ProviderQuotaError(ProviderHttpError):
    """Credits, spend limit or usage allowance exhausted: retrying in a few seconds cannot help."""

    def __init__(
        self, message: str, status: int, retry_after_s: float | None, context: Mapping[str, JsonValue]
    ) -> None:
        super().__init__(message, status, context)
        self.retry_after_s: float | None = retry_after_s


class ProviderTransportError(ProviderError):
    """Connection, timeout or stream-interruption failure."""


class ProviderProtocolError(ProviderError):
    """The provider answered successfully but the payload violated the wire format."""


class ToolInputError(DotError):
    """The model supplied arguments that do not satisfy the tool schema."""


class ToolExecutionError(DotError):
    """A tool failed while running; the message is shown to the model."""


class PolicyDenied(DotError):
    """The policy engine refused an action outright."""


class ApprovalRejected(DotError):
    """A human rejected, or let expire, an approval request."""


class HandedOff(DotError):
    """The user took over an action themselves; the agent must not perform it."""


class RunCancelled(DotError):
    """The run was stopped by the user or by a limit."""


class BrowserError(DotError):
    """Browser automation failed."""


class DesktopError(DotError):
    """Desktop (computer use) automation failed."""


class DesktopPermissionError(DesktopError):
    """A macOS privacy permission (Screen Recording, Accessibility, Automation) is missing."""


class FailSafeTriggered(DesktopError):
    """The user moved the pointer to the failsafe corner; desktop control must stop."""


class WebError(DotError):
    """A web request was refused by the address rules, failed on the network, or returned something unusable."""


class WebTransportError(WebError):
    """A web request failed in transit (timeout, reset connection); trying again can help."""


class WebThrottledError(WebError):
    """A web service answered with its rate limit or its bot check; trying again after a pause can help."""


class McpError(DotError):
    """An MCP server failed to start, list tools or run a tool."""


class SkillError(DotError):
    """A skill folder does not follow the Agent Skills layout, so it is left out."""


class PluginError(DotError):
    """A plugin folder does not follow the Claude Code plugin layout, so it is left out."""


class StoreError(DotError):
    """The persistent store rejected an operation."""


class NotFoundError(StoreError):
    """The thread, run, approval, note or schedule asked for does not exist."""


class InvalidValueError(StoreError):
    """A value handed to the store cannot be kept as it is, such as an empty or oversized note."""


class RunConflict(DotError):
    """A run cannot be started, steered or stopped, or an approval decided, in the state it is in."""


class EvalError(DotError):
    """An eval task or its reference solution is wrong, or the harness could not run it."""


class ServiceError(DotError):
    """The daemon does not answer, or its launchd job cannot be installed, removed or inspected."""

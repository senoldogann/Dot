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


class MissingSecretError(ConfigError):
    """A secret, such as the API key of a provider, is set in none of the places Dot reads secrets from."""


class JsonParseError(DotError):
    """A payload that must be a JSON object was not."""


class ProviderError(DotError):
    """A model provider call failed."""


class ProviderFilterError(ProviderError):
    """The model service's content filter stopped the reply. The model is not unavailable to other runs, but asked again
    with the same conversation it is stopped again."""


class ProviderHttpError(ProviderError):
    """The provider answered with a non-success HTTP status."""

    def __init__(self, message: str, status: int, context: Mapping[str, JsonValue]) -> None:
        super().__init__(message, {**context, "status": status})
        self.status: int = status


class ProviderAuthError(ProviderHttpError):
    """401: a missing, invalid or revoked key."""


class ProviderAccessError(ProviderHttpError):
    """403: the provider knows the key and refuses what was asked: a model, a region or a plan the key may not
    use. OpenRouter's 404 for a model the account's privacy settings leave no provider of is one too (item 361)."""


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


class ProviderCreditError(ProviderQuotaError):
    """OpenRouter's 402 for an account whose credits do not pay for the request. The price of a request counts the
    longest answer it allows, which is the model's own limit when it names none, so the account may pay for a shorter
    one: ``affordable_tokens`` is the longest answer its credits do pay for, ``None`` when the message names none."""

    def __init__(
        self,
        message: str,
        status: int,
        retry_after_s: float | None,
        context: Mapping[str, JsonValue],
        affordable_tokens: int | None,
    ) -> None:
        super().__init__(message, status, retry_after_s, context)
        self.affordable_tokens: int | None = affordable_tokens


class ProviderRunnerError(ProviderHttpError):
    """The provider's server answered, but the process behind it that runs the model broke off (Ollama's runner,
    reached over the loopback, reset the connection): as retryable as a connection that broke."""


class ProviderTransportError(ProviderError):
    """Connection, timeout or stream-interruption failure."""


class ProviderProtocolError(ProviderError):
    """The provider answered successfully but the payload violated the wire format."""


class ProviderRefusedError(ProviderError):
    """The model declined to answer: its provider ended the reply as a refusal. Asking the same again gets the same."""


class ToolInputError(DotError):
    """The model supplied arguments that do not satisfy the tool schema."""


class ToolExecutionError(DotError):
    """A tool failed while running; the message is shown to the model."""


class TerminalInputFullError(ToolExecutionError):
    """The program of a terminal session has not read what was typed into it before, as much as waits for it at most;
    nothing more is typed until it reads."""


class PolicyDenied(DotError):
    """The policy engine refused an action outright."""


class PairingRefused(DotError):
    """A phone's pairing code is wrong, used or out of date (item 267)."""


class ApprovalRejected(DotError):
    """A human rejected, or let expire, an approval request."""


class ApprovalParked(ApprovalRejected):
    """A step kept for the owner (item 482): nobody answered its question in time, so it did not happen and waits for
    them; its message is what the run is told. Every handler of a refusal handles it too."""


class HandedOff(DotError):
    """The user took over an action themselves; the agent must not perform it."""


class RunCancelled(DotError):
    """The run was stopped by the user or by a limit."""


class RunHoldLapsed(RunCancelled):
    """The owner held the run with a ✋ and wrote nothing to it for as long as the hold may last (item 586): it stops,
    with this error's words as its result."""


class BrowserError(DotError):
    """Browser automation failed."""


class ForbiddenUrlError(BrowserError):
    """The browser rules refuse an address: Dot's own interface, a blocked host, a host outside
    ``browser.allowed_hosts`` or a scheme that is not the web's. ``url`` is the address as Chrome read it, ``reason``
    why it is refused and ``resource_type`` what the request was for (``document`` for a page, else Chrome's name for
    the resource)."""

    def __init__(self, message: str, url: str, reason: str, resource_type: str) -> None:
        super().__init__(message, {"url": url, "reason": reason, "resource_type": resource_type})
        self.url: str = url
        self.reason: str = reason
        self.resource_type: str = resource_type


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


class McpRejected(McpError):
    """An MCP server answered a call with an error of its own, a JSON-RPC error: its words are in the context, as
    ``error``, and the model reads them as it reads an answer of the server (item 551)."""


class OpenCodeError(DotError):
    """OpenCode's own agent (item 283) could not be started, reached or used, or its session failed."""


class OpenCodeMissing(OpenCodeError):
    """The ``opencode`` program that runs Zen's free models (item 283) is not installed."""


class PreviewError(DotError):
    """macOS's Quick Look could not draw a file for the panel beside a chat (item 268)."""


class SkillError(DotError):
    """A skill folder does not follow the Agent Skills layout, so it is left out."""


class PluginError(DotError):
    """A plugin folder does not follow the Claude Code plugin layout, so it is left out."""


class GalleryError(DotError):
    """A plugin of the gallery cannot be installed: it is there already, or its archive cannot be downloaded or
    unpacked, or its files are not the ones pinned (item 542)."""


class ElsewhereError(DotError):
    """A skill or an MCP server that another agent of the Mac set up cannot be added to Dot, or removed from it, as
    asked: it is not there, Dot has it already, its name is taken, or Dot cannot use it as it is written."""


class StoreError(DotError):
    """The persistent store rejected an operation."""


class HarnessError(DotError):
    """A step or a probe of the harness (docs/harness.md) could not be carried out."""


class NotFoundError(StoreError):
    """The thread, run, approval, note, schedule or terminal session asked for does not exist."""


class InvalidValueError(StoreError):
    """A value handed to the store cannot be kept as it is, such as an empty or oversized note."""


class RunConflict(DotError):
    """A run cannot be started, steered or stopped, an approval decided, a lesson of a run saved or dismissed, the file
    changes of a run taken back, a campaign go on, stop or be marked achieved, or a chat deleted, in the state it is
    in."""


class UndoError(DotError):
    """The journal of a run's file changes cannot be written or read, or a change cannot be taken back."""


class EvalError(DotError):
    """An eval task or its reference solution is wrong, or the harness could not run it."""


class ServiceError(DotError):
    """The daemon does not answer, or its launchd job cannot be installed, removed or inspected."""


class SignInError(DotError):
    """An official sign-in of Google or Microsoft could not be made or used (item 422): its client is not set, the
    provider refused, its tokens cannot be read or refreshed, or the Keychain did not answer."""


class VaultRefused(DotError):
    """Dot's vault shows a secret to this Mac's own page alone (item 621): a request that came another way, a phone's
    over Tailscale, is refused."""


class AccountApiError(DotError):
    """A call to the mail or the calendar of an account signed in failed (item 424): Gmail's, Google Calendar's or
    Microsoft Graph's API refused it, could not be reached, or answered with what Dot does not read."""


class PhoneSetupError(DotError):
    """What a phone needs to reach Dot away from home could not be set up (item 271): Tailscale is missing, not running
    or refused to serve, or the Mac cannot be kept awake."""


class ChannelError(DotError):
    """A channel of the owner's messaging apps (iMessage; item 572) could not be set up or used: a handle that is not
    one, a code that is wrong, Messages refusing or not answering."""


class VoiceError(DotError):
    """This Mac could not write a recording down (item 626). The message is for the owner, in plain words, and says what
    to do; ``context["code"]`` is what a page tells it by: ``unavailable`` (this Mac cannot write recordings down),
    ``unsupported-language``, ``no-audio``, ``timeout`` or ``failed``."""

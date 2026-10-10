"""The typed judge: yes/no calls, each with the probability of yes, where the harness has no code of its own to decide
(``[judge]``, off by default). It answers two questions:

- whether a click on a browser control whose label no pattern of the policy knows publishes, sends, pays or deletes
  (:meth:`Judge.judge_click`): a probability at or above ``public_action_threshold`` gives the click the reason a
  pattern would have given it, so the policy treats it as a click on "Post" or "Delete";
- whether a campaign's claim of its targets holds (:meth:`Judge.judge_claim`): below ``target_threshold`` the claim is
  refused before the goal checker is asked.

The judge never decides a step and never grants anything: it adds flags that make the policy ask, or stops a claim the
goal checker would have examined. What it sends has its secrets masked, and a page's address goes without its query and
fragment. A missing key or a failed request makes it unavailable for ``[models] helper_cooldown_s``, with a
``judge_unavailable`` event, and meanwhile the patterns and the goal checker decide alone.
"""

import asyncio
import time
from typing import Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import JsonValue

from dotagent.config import AppConfig, JudgeConfig
from dotagent.core.errors import DotError, MissingSecretError, ProviderError
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_json
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.events import EventBus
from dotagent.llm.router import unavailable_reason
from dotagent.llm.typesafe import NoulQuestion, TypeSafeClient
from dotagent.secrets import require_secret

log = get_logger(__name__)

JUDGE_UNAVAILABLE_EVENT: str = "judge_unavailable"
# The questions about a click, each by the flag it stands for, with the reason of dotagent.policy.elements that gives
# that flag.
CLICK_QUESTIONS: tuple[tuple[NoulQuestion, str], ...] = (
    (
        NoulQuestion(
            id="public_post",
            instructions=(
                "Does clicking this control publish something other people can see, such as a post, a repost, a "
                "quote, a public reply or a comment?"
            ),
        ),
        "post",
    ),
    (
        NoulQuestion(
            id="external_send",
            instructions="Does clicking this control send a message, an e-mail, an invitation or a file to someone?",
        ),
        "send",
    ),
    (
        NoulQuestion(
            id="financial", instructions="Does clicking this control pay, buy, order, subscribe for money or donate?"
        ),
        "financial",
    ),
    (
        NoulQuestion(
            id="destructive",
            instructions="Does clicking this control delete, remove or irreversibly destroy something?",
        ),
        "destructive",
    ),
)
_CLAIM_QUESTION: NoulQuestion = NoulQuestion(
    id="targets_reached",
    instructions=(
        "Does the claim show, with what this session read and measured, that every target is reached as the "
        "verification asks?"
    ),
)
_ERROR_CHARS: int = 300


class ClickFacts(FrozenModel):
    """The control a click lands on, as the judge sees it: its accessible name and role, and the page's address."""

    label: str
    role: str
    url: str


class ClickJudgment(FrozenModel):
    """What the judge found about a click: the reasons it gives the click, named as :mod:`dotagent.policy.elements`
    names them, and the words for the action's summary ("judge: public_post 0.93"; "judge off" while the judge is
    unavailable; empty when it found nothing or is switched off)."""

    reasons: tuple[str, ...]
    note: str


class ClaimJudgment(FrozenModel):
    """The probability that a campaign's claim holds; ``None`` when the judge is switched off or unavailable."""

    probability: float | None


class Judge(Protocol):
    async def judge_click(self, facts: ClickFacts, run_id: str) -> ClickJudgment: ...

    async def judge_claim(self, claim: JsonObject, run_id: str) -> ClaimJudgment: ...

    async def retire(self) -> None: ...

    async def aclose(self) -> None: ...


def _page(url: str) -> str:
    """A page's address as the judge gets it: without the query and the fragment, which can carry tokens and say nothing
    of what a click does, and without a user name or password."""
    parts: SplitResult = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


def click_judgment(probabilities: dict[str, float], threshold: float) -> ClickJudgment:
    """The reasons of the click questions whose probability of yes is at least ``threshold``, and the note naming them."""
    found: list[tuple[NoulQuestion, str]] = [
        (question, reason) for question, reason in CLICK_QUESTIONS if probabilities[question.id] >= threshold
    ]
    if not found:
        return ClickJudgment(reasons=(), note="")
    named: str = ", ".join(f"{question.id} {probabilities[question.id]:.2f}" for question, _ in found)
    return ClickJudgment(reasons=tuple(reason for _, reason in found), note=f"judge: {named}")


class NoJudge:
    """``[judge] provider = "none"``: nothing is asked and nothing is added."""

    async def judge_click(self, facts: ClickFacts, run_id: str) -> ClickJudgment:
        return ClickJudgment(reasons=(), note="")

    async def judge_claim(self, claim: JsonObject, run_id: str) -> ClaimJudgment:
        return ClaimJudgment(probability=None)

    async def retire(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class TypeSafeJudge:
    """``[judge] provider = "typesafe"``: TypeSafe's System One API answers. The key is read at the first judgment and
    again after :meth:`retire` (a key saved or removed in Settings, which also ends a pause)."""

    def __init__(self, config: JudgeConfig, cooldown_s: float, bus: EventBus) -> None:
        self._config: JudgeConfig = config
        self._cooldown_s: float = cooldown_s
        self._bus: EventBus = bus
        self._client: TypeSafeClient | None = None
        # The clients `retire` took out of service, closed at `aclose`, so a judgment one of them serves is not cut.
        self._retired: list[TypeSafeClient] = []
        self._unavailable_until: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def judge_click(self, facts: ClickFacts, run_id: str) -> ClickJudgment:
        state: JsonObject = {"control": {"label": facts.label, "role": facts.role}, "page": _page(facts.url)}
        probabilities: dict[str, float] | None = await self._ask(
            state, tuple(question for question, _ in CLICK_QUESTIONS), run_id
        )
        if probabilities is None:
            return ClickJudgment(reasons=(), note="judge off")
        return click_judgment(probabilities, self._config.public_action_threshold)

    async def judge_claim(self, claim: JsonObject, run_id: str) -> ClaimJudgment:
        probabilities: dict[str, float] | None = await self._ask(claim, (_CLAIM_QUESTION,), run_id)
        return ClaimJudgment(probability=None if probabilities is None else probabilities[_CLAIM_QUESTION.id])

    async def retire(self) -> None:
        async with self._lock:
            if self._client is not None:
                self._retired.append(self._client)
                self._client = None
            self._unavailable_until = 0.0

    async def aclose(self) -> None:
        async with self._lock:
            clients: list[TypeSafeClient] = [*self._retired, *([] if self._client is None else [self._client])]
            self._client = None
            self._retired = []
        for client in clients:
            await client.aclose()

    async def _connect(self) -> TypeSafeClient:
        async with self._lock:
            if self._client is None:
                key: str = await asyncio.to_thread(require_secret, self._config.api_key_env, "the typed judge")
                self._client = TypeSafeClient(self._config, key)
            return self._client

    async def _ask(self, state: JsonValue, questions: tuple[NoulQuestion, ...], run_id: str) -> dict[str, float] | None:
        """The probability of yes for each question, or ``None`` while the judge is unavailable or once this request
        made it so."""
        if time.time() < self._unavailable_until:
            return None
        try:
            async with asyncio.timeout(self._config.timeout_s):
                client: TypeSafeClient = await self._connect()
                return await client.nouls(redact_json(state), questions)
        except (ProviderError, MissingSecretError, TimeoutError) as error:
            await self._pause(error, run_id)
            return None

    async def _pause(self, error: Exception, run_id: str) -> None:
        reason: str = unavailable_reason(error) or ("timed out" if isinstance(error, TimeoutError) else "failed")
        until: float = time.time() + self._cooldown_s
        self._unavailable_until = until
        message: str = error.message if isinstance(error, DotError) else str(error) or type(error).__name__
        context: JsonObject = error.context if isinstance(error, DotError) else {}
        log.warning(
            "judge_unavailable",
            run_id=run_id,
            reason=reason,
            until=until,
            error_type=type(error).__name__,
            error=message,
            context=context,
        )
        await self._bus.emit(
            run_id, JUDGE_UNAVAILABLE_EVENT, {"reason": reason, "until": until, "error": message[:_ERROR_CHARS]}, True
        )


def judge_for(config: AppConfig, bus: EventBus) -> Judge:
    """The judge ``[judge] provider`` names."""
    match config.judge.provider:
        case "none":
            return NoJudge()
        case "typesafe":
            return TypeSafeJudge(config.judge, config.models.helper_cooldown_s, bus)

"""The typed judge: yes/no calls, each with the probability of yes, where the harness has no code of its own to decide
(``[judge]``, off by default). It answers two questions:

- whether a click on a browser control whose label no pattern of the policy knows publishes, sends, pays or deletes
  (:meth:`Judge.judge_click`): a probability at or above ``public_action_threshold`` gives the click the reason a
  pattern would have given it, so the policy treats it as a click on "Post" or "Delete";
- whether a campaign's claim of its targets holds, and whether a run's answer in goal mode reaches its goal
  (:meth:`Judge.judge_claim`): below ``target_threshold`` the claim is refused before the goal checker is asked;
- what the page after a browser action shows (:meth:`Judge.judge_page`), when the action named what it expected to
  see: whether it shows that, and whether it is a login wall, a CAPTCHA or an error page, so the next turn starts from
  those numbers.

The click questions are asked in every mode, ``yolo`` too: a payment, a message or a post they find is the user's there
as well (item 429).

The judge never decides a step and never grants anything: it adds flags that make the policy ask, stops a claim the
goal checker would have examined, or puts numbers above a page the model reads anyway. What it sends has its secrets
masked, and a page's address goes without its query and fragment. It asks through the :class:`TypeSafeService` of the
runtime, which the judge_items tool shares: a missing key or an outage makes the service unavailable for ``[models]
helper_cooldown_s``, with a ``judge_unavailable`` event, and meanwhile the patterns and the goal checker decide alone; a
request TypeSafe rejects is a ``judge_error`` of this code, logged with the request, and does not pause the service.

With ``[judge] second_opinion`` each question is asked a second time, in a separate request with an independent
phrasing (:data:`SECOND_CLICK_QUESTIONS`, :data:`SECOND_CLAIM_QUESTION`), and the judge acts on the two answers
together: a click gets its flag when either answer is at the threshold, so a disagreement ends in the policy's
question, and the summary names both answers; a claim is refused only when both answers are below the threshold, so a
disagreement leaves it to the goal checker (:func:`claim_refused`).
"""

import asyncio
import time
from typing import Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import JsonValue

from dotagent.config import AppConfig, JudgeConfig, JudgeProvider
from dotagent.core.errors import (
    ConfigError,
    DotError,
    MissingSecretError,
    ProviderError,
    ProviderHttpError,
    ProviderProtocolError,
)
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_json
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.events import EventBus
from dotagent.llm.router import unavailable_reason
from dotagent.llm.typesafe import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    TypeSafeClient,
)
from dotagent.secrets import require_secret

log = get_logger(__name__)

JUDGE_UNAVAILABLE_EVENT: str = "judge_unavailable"
JUDGE_ERROR_EVENT: str = "judge_error"
# TypeSafe answers a body it rejects with HTTP 422.
_REJECTED_STATUS: int = 422
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
CLAIM_QUESTION: NoulQuestion = NoulQuestion(
    id="targets_reached",
    instructions=(
        "Does the claim show, with what this session read and measured, that every target is reached as the "
        "verification asks?"
    ),
)
# The second phrasing of each question, asked in a request of its own when [judge] second_opinion is on; the ids are
# the same, so the answers line up with the first ones.
SECOND_CLICK_QUESTIONS: tuple[NoulQuestion, ...] = (
    NoulQuestion(
        id="public_post",
        instructions=(
            "After this control is clicked, will other users of the site be able to see something new from this "
            "account, such as a post, a share, a public reply or a comment?"
        ),
    ),
    NoulQuestion(
        id="external_send",
        instructions=(
            "After this control is clicked, will another person receive a message, an e-mail, an invitation or a "
            "file from this account?"
        ),
    ),
    NoulQuestion(
        id="financial",
        instructions="After this control is clicked, will money be charged, paid, ordered, subscribed or donated?",
    ),
    NoulQuestion(
        id="destructive",
        instructions="After this control is clicked, will something be gone for good: deleted, removed or destroyed?",
    ),
)
SECOND_CLAIM_QUESTION: NoulQuestion = NoulQuestion(
    id="targets_reached",
    instructions=(
        "Is every target met according to its own verification, going only by the facts this session read and "
        "measured, not by what the claim asserts?"
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
    """The probability that a campaign's claim holds, ``None`` when the judge is switched off or unavailable, and the
    second answer when ``[judge] second_opinion`` asked for one."""

    probability: float | None
    second: float | None


class PageFacts(FrozenModel):
    """The page after a browser action, as the judge sees it: its address, its title, its visible text and what the
    action expected to see."""

    url: str
    title: str
    text: str
    expected: str


class PageCheck(FrozenModel):
    """What the judge found on the page after an action: the probability that it shows what the action expected, and
    that it is a login wall, a CAPTCHA or an error page."""

    expected: float
    login_wall: float
    captcha: float
    error_page: float


# The questions about the page after an action, besides the one about what the action expected.
PAGE_QUESTIONS: tuple[NoulQuestion, ...] = (
    NoulQuestion(
        id="login_wall",
        instructions=(
            "Is the page at `page` a sign-in or log-in wall: it asks for credentials or an account before it shows "
            "what the address is for?"
        ),
    ),
    NoulQuestion(
        id="captcha",
        instructions="Does the page at `page` ask to solve a CAPTCHA, a puzzle or a verification that one is human?",
    ),
    NoulQuestion(
        id="error_page",
        instructions=(
            "Is the page at `page` an error page: a server error, a page not found, an access denied, a rate limit or "
            "a message that something went wrong, in place of the content?"
        ),
    ),
)
EXPECTED_QUESTION: NoulQuestion = NoulQuestion(
    id="expected",
    instructions="Does the page at `page` show what `expected` describes, as the result of the action just taken?",
)


def page_state(facts: PageFacts) -> JsonObject:
    return {
        "page": {"url": _page(facts.url), "title": facts.title, "text": facts.text},
        "expected": facts.expected,
    }


def page_check(probabilities: dict[str, float]) -> PageCheck:
    return PageCheck(
        expected=probabilities[EXPECTED_QUESTION.id],
        login_wall=probabilities["login_wall"],
        captcha=probabilities["captcha"],
        error_page=probabilities["error_page"],
    )


def render_page_check(check: PageCheck) -> str:
    """The judge's line above an action's result: the probabilities, as the next turn reads them."""
    return (
        f"Typed check of the page after the steps: what you expected shows {check.expected:.2f}; login wall "
        f"{check.login_wall:.2f}, CAPTCHA {check.captcha:.2f}, error page {check.error_page:.2f}."
    )


def claim_state(goal: str, plan: str, targets: str, digest: str, facts: str, claim: str) -> JsonObject:
    """What the typed judge reads of a claim that a goal or a campaign's targets are reached: what the goal checker
    reads."""
    return {
        "goal": goal,
        "plan": plan,
        "targets": targets,
        "ledger": digest,
        "session_facts": facts,
        "claim": claim,
    }


class Judge(Protocol):
    async def judge_click(self, facts: ClickFacts, run_id: str) -> ClickJudgment: ...

    async def judge_claim(self, claim: JsonObject, run_id: str) -> ClaimJudgment: ...

    async def judge_page(self, facts: PageFacts, run_id: str) -> PageCheck | None: ...

    async def retire(self) -> None: ...

    async def aclose(self) -> None: ...


def _page(url: str) -> str:
    """A page's address as the judge gets it: without the query and the fragment, which can carry tokens and say nothing
    of what a click does, and without a user name or password."""
    parts: SplitResult = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


def click_judgment(probabilities: dict[str, float], threshold: float) -> ClickJudgment:
    """The reasons of the click questions whose probability of yes is at least ``threshold``, and the note naming
    them."""
    found: list[tuple[NoulQuestion, str]] = [
        (question, reason) for question, reason in CLICK_QUESTIONS if probabilities[question.id] >= threshold
    ]
    if not found:
        return ClickJudgment(reasons=(), note="")
    named: str = ", ".join(f"{question.id} {probabilities[question.id]:.2f}" for question, _ in found)
    return ClickJudgment(reasons=tuple(reason for _, reason in found), note=f"judge: {named}")


def agreed_click_judgment(first: dict[str, float], second: dict[str, float], threshold: float) -> ClickJudgment:
    """The click judgment of two answers to each question: a reason is given when either answer is at ``threshold``,
    and the note names both answers, marking the questions the two disagreed on."""
    found: list[tuple[NoulQuestion, str]] = [
        (question, reason)
        for question, reason in CLICK_QUESTIONS
        if first[question.id] >= threshold or second[question.id] >= threshold
    ]
    if not found:
        return ClickJudgment(reasons=(), note="")
    named: list[str] = []
    for question, _ in found:
        agreed: bool = (first[question.id] >= threshold) == (second[question.id] >= threshold)
        named.append(
            f"{question.id} {first[question.id]:.2f}/{second[question.id]:.2f}{'' if agreed else ' (disagreed)'}"
        )
    return ClickJudgment(reasons=tuple(reason for _, reason in found), note=f"judge: {', '.join(named)}")


def claim_refused(judgment: ClaimJudgment, threshold: float) -> bool:
    """Whether the judge refuses a claim: its answer is below ``threshold`` and, when a second one was asked, so is
    that one; a disagreement leaves the claim to the goal checker."""
    if judgment.probability is None or judgment.probability >= threshold:
        return False
    return judgment.second is None or judgment.second < threshold


def describe_claim(judgment: ClaimJudgment) -> str:
    """The answers of the judge to a claim, for its refusal: ``0.08``, or ``0.08 and 0.11 on a second phrasing``."""
    first: str = "-" if judgment.probability is None else f"{judgment.probability:.2f}"
    return first if judgment.second is None else f"{first} and {judgment.second:.2f} on a second phrasing"


class NoJudge:
    """``[judge] provider = "none"``: nothing is asked and nothing is added."""

    async def judge_click(self, facts: ClickFacts, run_id: str) -> ClickJudgment:
        return ClickJudgment(reasons=(), note="")

    async def judge_claim(self, claim: JsonObject, run_id: str) -> ClaimJudgment:
        return ClaimJudgment(probability=None, second=None)

    async def judge_page(self, facts: PageFacts, run_id: str) -> PageCheck | None:
        return None

    async def retire(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class TypeSafeService:
    """One connection to TypeSafe's System One API, shared by the typed judge and the judge_items tool, with the
    pause an outage brings. The key is read at the first request and again after :meth:`retire` (a key saved or
    removed in Settings, which also ends a pause).

    Its failures are told apart. An outage (a refused or missing key, a spent quota, a rate limit, an overloaded
    service, a timeout, a transport error) pauses the service for ``cooldown_s`` with a ``judge_unavailable`` event,
    and meanwhile every request answers ``None``. A request TypeSafe rejects (HTTP 422) or an answer that is not what
    its reference documents is an error of this code or of the model, logged with the request as ``judge_error`` and
    raised to the caller; the service stays on.
    """

    def __init__(self, config: JudgeConfig, cooldown_s: float, bus: EventBus) -> None:
        self._config: JudgeConfig = config
        self._cooldown_s: float = cooldown_s
        self._bus: EventBus = bus
        self._client: TypeSafeClient | None = None
        # The clients `retire` took out of service, closed at `aclose`, so a judgment one of them serves is not cut.
        self._retired: list[TypeSafeClient] = []
        self._unavailable_until: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def config(self) -> JudgeConfig:
        return self._config

    def unavailable(self) -> bool:
        """Whether the service is paused after an outage."""
        return time.time() < self._unavailable_until

    async def nouls(
        self, state: JsonValue, questions: tuple[NoulQuestion, ...], run_id: str
    ) -> dict[str, float] | None:
        """The probability of yes for each question, or ``None`` while the service is paused or once this request
        paused it. Raises :class:`ProviderError` for a request TypeSafe rejected or an answer it could not read."""
        if self.unavailable():
            return None
        try:
            async with asyncio.timeout(self._config.timeout_s):
                client: TypeSafeClient = await self._connect()
                return await client.nouls(redact_json(state), questions)
        except (ProviderError, MissingSecretError, TimeoutError) as error:
            await self._failed(error, run_id, state, len(questions))
            return None

    async def scores(
        self, state: JsonValue, questions: tuple[ScoreQuestion, ...], run_id: str
    ) -> dict[str, ScoreAnswer] | None:
        """The score answer to each question, or ``None`` while the service is paused or once this request paused it.
        Raises :class:`ProviderError` for a request TypeSafe rejected or an answer it could not read."""
        if self.unavailable():
            return None
        try:
            async with asyncio.timeout(self._config.timeout_s):
                client: TypeSafeClient = await self._connect()
                return await client.scores(redact_json(state), questions)
        except (ProviderError, MissingSecretError, TimeoutError) as error:
            await self._failed(error, run_id, state, len(questions))
            return None

    async def choices(
        self, state: JsonValue, questions: tuple[ChoiceQuestion, ...], run_id: str
    ) -> dict[str, ChoiceAnswer] | None:
        """The choice answer to each question, or ``None`` while the service is paused or once this request paused it.
        Raises :class:`ProviderError` for a request TypeSafe rejected or an answer it could not read."""
        if self.unavailable():
            return None
        try:
            async with asyncio.timeout(self._config.timeout_s):
                client: TypeSafeClient = await self._connect()
                return await client.choices(redact_json(state), questions)
        except (ProviderError, MissingSecretError, TimeoutError) as error:
            await self._failed(error, run_id, state, len(questions))
            return None

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

    async def _failed(self, error: Exception, run_id: str, state: JsonValue, questions: int) -> None:
        """A request error is logged with its request and raised again; an outage pauses the service."""
        if request_error(error):
            message, context = _described(error)
            log.error(
                "judge_error",
                run_id=run_id,
                error_type=type(error).__name__,
                error=message,
                context=context,
                questions=questions,
                state=redact_json(state),
            )
            await self._bus.emit(run_id, JUDGE_ERROR_EVENT, {"error": message[:_ERROR_CHARS]}, True)
            raise error
        reason: str = unavailable_reason(error) or ("timed out" if isinstance(error, TimeoutError) else "failed")
        until: float = time.time() + self._cooldown_s
        self._unavailable_until = until
        message, context = _described(error)
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


def request_error(error: Exception) -> bool:
    """Whether ``error`` is TypeSafe rejecting the request (HTTP 422) or answering outside its reference: an error of
    this code or of the model, not an outage of the service."""
    if isinstance(error, ProviderHttpError):
        return error.status == _REJECTED_STATUS
    return isinstance(error, ProviderProtocolError)


def _described(error: Exception) -> tuple[str, JsonObject]:
    message: str = error.message if isinstance(error, DotError) else str(error) or type(error).__name__
    context: JsonObject = error.context if isinstance(error, DotError) else {}
    return message, context


class TypeSafeJudge:
    """``[judge] provider = "typesafe"``: TypeSafe's System One API answers, through the :class:`TypeSafeService` of
    the runtime. A request the service rejected leaves the judgment to the patterns and the goal checker, like an
    outage, after the service's ``judge_error`` event."""

    def __init__(self, service: TypeSafeService, config: JudgeConfig) -> None:
        self._service: TypeSafeService = service
        self._config: JudgeConfig = config

    async def judge_click(self, facts: ClickFacts, run_id: str) -> ClickJudgment:
        state: JsonObject = {"control": {"label": facts.label, "role": facts.role}, "page": _page(facts.url)}
        probabilities: dict[str, float] | None = await self._ask(
            state, tuple(question for question, _ in CLICK_QUESTIONS), run_id
        )
        if probabilities is None:
            return ClickJudgment(reasons=(), note="judge off")
        threshold: float = self._config.public_action_threshold
        if not self._config.second_opinion:
            return click_judgment(probabilities, threshold)
        second: dict[str, float] | None = await self._ask(state, SECOND_CLICK_QUESTIONS, run_id)
        if second is None:
            return click_judgment(probabilities, threshold)
        return agreed_click_judgment(probabilities, second, threshold)

    async def judge_claim(self, claim: JsonObject, run_id: str) -> ClaimJudgment:
        probabilities: dict[str, float] | None = await self._ask(claim, (CLAIM_QUESTION,), run_id)
        if probabilities is None:
            return ClaimJudgment(probability=None, second=None)
        first: float = probabilities[CLAIM_QUESTION.id]
        if not self._config.second_opinion:
            return ClaimJudgment(probability=first, second=None)
        second: dict[str, float] | None = await self._ask(claim, (SECOND_CLAIM_QUESTION,), run_id)
        return ClaimJudgment(probability=first, second=None if second is None else second[SECOND_CLAIM_QUESTION.id])

    async def judge_page(self, facts: PageFacts, run_id: str) -> PageCheck | None:
        probabilities: dict[str, float] | None = await self._ask(
            page_state(facts), (EXPECTED_QUESTION, *PAGE_QUESTIONS), run_id
        )
        return None if probabilities is None else page_check(probabilities)

    async def retire(self) -> None:
        await self._service.retire()

    async def aclose(self) -> None:
        await self._service.aclose()

    async def _ask(self, state: JsonValue, questions: tuple[NoulQuestion, ...], run_id: str) -> dict[str, float] | None:
        try:
            return await self._service.nouls(state, questions, run_id)
        except ProviderError:
            return None


class SwitchedJudge:
    """The judge ``[judge] provider`` names, switched in Settings without a restart (item 255): ``"typesafe"`` answers
    through the runtime's TypeSafe service, ``"none"`` answers nothing. The service, when the runtime has one, is
    retired and closed through here whatever the switch says."""

    def __init__(self, service: TypeSafeService | None, config: JudgeConfig, provider: JudgeProvider) -> None:
        self._none: NoJudge = NoJudge()
        self._typed: TypeSafeJudge | None = None if service is None else TypeSafeJudge(service, config)
        self.provider: JudgeProvider = provider
        if provider == "typesafe":
            self._require_service()

    def set_provider(self, provider: JudgeProvider) -> None:
        """Switch to ``provider`` from the next judgment on; ``"typesafe"`` needs the service, else nothing changes."""
        if provider == "typesafe":
            self._require_service()
        self.provider = provider

    def _require_service(self) -> None:
        if self._typed is None:
            raise ConfigError(
                "the typed judge needs the TypeSafe service: save TYPESAFE_API_KEY, then dotx service install",
                {"provider": "typesafe"},
            )

    def _active(self) -> Judge:
        return self._typed if self.provider == "typesafe" and self._typed is not None else self._none

    async def judge_click(self, facts: ClickFacts, run_id: str) -> ClickJudgment:
        return await self._active().judge_click(facts, run_id)

    async def judge_claim(self, claim: JsonObject, run_id: str) -> ClaimJudgment:
        return await self._active().judge_claim(claim, run_id)

    async def judge_page(self, facts: PageFacts, run_id: str) -> PageCheck | None:
        return await self._active().judge_page(facts, run_id)

    async def retire(self) -> None:
        await (self._none if self._typed is None else self._typed).retire()

    async def aclose(self) -> None:
        await (self._none if self._typed is None else self._typed).aclose()


def judge_for(config: AppConfig, typesafe: TypeSafeService | None) -> SwitchedJudge:
    """The judge ``[judge] provider`` names, switchable; ``typesafe`` is the runtime's service, present whenever the
    provider is ``typesafe`` or its key is set."""
    return SwitchedJudge(typesafe, config.judge, config.judge.provider)

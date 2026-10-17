"""TypeSafe's System One API, for the typed judge and the tools built on it: yes/no questions ("Noul" in TypeSafe's
words) about a state, each answered with the probability of yes; ratings on ordered levels ("Score"), each answered
with the expected level and the probability of each; and picks of one option out of a named set ("Choice"), each
answered with the option and the probability of each.

One request carries the state and every question about it: ``POST {base_url}/v1/systemone`` with the key as a bearer
token and the body ``{"state", "model", "questions": {id: {"type", "instructions", "criteria"?}}}``; the answer is
``{"model", "answers": {id: ...}, "usage"}``. A Noul answer is ``{"type": "noul", "noul": p}`` with ``p`` from 0 (no)
to 1 (yes); a Score answer is ``{"type": "score", "score": s, "confidence": c, "legend": {"0": level, ...},
"probabilities": {"0": p, ...}}`` with ``s`` the expected level, from 0 for the first level to one less than their
number for the last; a Choice answer is ``{"type": "choice", "choice": option, "confidence": c, "probabilities":
{option: p, ...}}``, its question carrying the options as ``criteria: {option: description}``. A refused key is HTTP
401, a body it rejects 422, a rate limit 429 and an overloaded service 529; the last two are retried.
"""

import httpx
from pydantic import JsonValue

from dotagent.config import JudgeConfig
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.connector import post_json
from dotagent.llm.http import build_client
from dotagent.llm.provider_errors import classify_generic
from dotagent.llm.wire import get_obj

_PATH: str = "/v1/systemone"
# One retry: a click waits for its judgment, whose whole time the judge bounds with its timeout anyway.
_POLICY: RetryPolicy = RetryPolicy(attempts=2, base_delay_s=0.5, max_delay_s=2.0)


# A Score has at least two levels and at most ten, by TypeSafe's reference.
MIN_LEVELS: int = 2
MAX_LEVELS: int = 10


class NoulQuestion(FrozenModel):
    """A yes/no question about the state. ``id`` names its answer; TypeSafe does not show it to the model."""

    id: str
    instructions: str


class ScoreQuestion(FrozenModel):
    """A rating of the state on the ordered ``levels``, the lowest first, each a situation of its own."""

    id: str
    instructions: str
    levels: tuple[str, ...]


class ChoiceQuestion(FrozenModel):
    """A pick of one option out of ``options`` (name to description) about the state."""

    id: str
    instructions: str
    options: dict[str, str]


class ChoiceAnswer(FrozenModel):
    """TypeSafe's answer to a :class:`ChoiceQuestion`: the option picked, the probability of each option and how
    concentrated they are."""

    choice: str
    probabilities: dict[str, float]
    confidence: float


class ScoreAnswer(FrozenModel):
    """TypeSafe's answer to a :class:`ScoreQuestion`: the expected level (0 for the first level, one less than their
    number for the last), the probability of each level in their order and how concentrated they are."""

    score: float
    probabilities: tuple[float, ...]
    confidence: float


def _number(value: JsonValue, low: float, high: float) -> float | None:
    """``value`` as a number between ``low`` and ``high``, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        return None
    return float(value)


def _probabilities(reply: JsonObject, questions: tuple[NoulQuestion, ...], url: str) -> dict[str, float]:
    """The probability of yes for each question, by its id, read from TypeSafe's answer."""
    answers: JsonObject = get_obj(reply, "answers")
    found: dict[str, float] = {}
    for question in questions:
        answer: JsonValue = answers.get(question.id)
        value: JsonValue = answer.get("noul") if isinstance(answer, dict) else None
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
            raise ProviderProtocolError(
                "TypeSafe's answer has no probability for a question",
                {"url": url, "question": question.id, "answer": answer},
            )
        found[question.id] = float(value)
    return found


def _scores(reply: JsonObject, questions: tuple[ScoreQuestion, ...], url: str) -> dict[str, ScoreAnswer]:
    """The score answer to each question, by its id, read from TypeSafe's answer."""
    answers: JsonObject = get_obj(reply, "answers")
    found: dict[str, ScoreAnswer] = {}
    for question in questions:
        answer: JsonValue = answers.get(question.id)
        top: int = len(question.levels) - 1
        score: float | None = _number(answer.get("score"), 0, top) if isinstance(answer, dict) else None
        confidence: float | None = _number(answer.get("confidence"), 0, 1) if isinstance(answer, dict) else None
        given: JsonValue = answer.get("probabilities") if isinstance(answer, dict) else None
        probabilities: list[float | None] = [
            _number(given.get(str(level)), 0, 1) if isinstance(given, dict) else None for level in range(top + 1)
        ]
        if score is None or confidence is None or any(p is None for p in probabilities):
            raise ProviderProtocolError(
                "TypeSafe's answer has no score for a question",
                {"url": url, "question": question.id, "answer": answer},
            )
        found[question.id] = ScoreAnswer(
            score=score, probabilities=tuple(p for p in probabilities if p is not None), confidence=confidence
        )
    return found


def _choices(reply: JsonObject, questions: tuple[ChoiceQuestion, ...], url: str) -> dict[str, ChoiceAnswer]:
    """The choice answer to each question, by its id, read from TypeSafe's answer."""
    answers: JsonObject = get_obj(reply, "answers")
    found: dict[str, ChoiceAnswer] = {}
    for question in questions:
        answer: JsonValue = answers.get(question.id)
        choice: JsonValue = answer.get("choice") if isinstance(answer, dict) else None
        confidence: float | None = _number(answer.get("confidence"), 0, 1) if isinstance(answer, dict) else None
        given: JsonValue = answer.get("probabilities") if isinstance(answer, dict) else None
        probabilities: dict[str, float | None] = {
            option: _number(given.get(option), 0, 1) if isinstance(given, dict) else None for option in question.options
        }
        if (
            not isinstance(choice, str)
            or choice not in question.options
            or confidence is None
            or any(p is None for p in probabilities.values())
        ):
            raise ProviderProtocolError(
                "TypeSafe's answer has no choice for a question",
                {"url": url, "question": question.id, "answer": answer},
            )
        found[question.id] = ChoiceAnswer(
            choice=choice,
            probabilities={option: p for option, p in probabilities.items() if p is not None},
            confidence=confidence,
        )
    return found


class TypeSafeClient:
    """A connection to TypeSafe's System One API with one key."""

    def __init__(self, config: JudgeConfig, api_key: str) -> None:
        self._model: str = config.model
        self._url: str = f"{config.base_url.rstrip('/')}{_PATH}"
        self._headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
        self._client: httpx.AsyncClient = build_client(config.timeout_s, config.timeout_s)

    async def nouls(self, state: JsonValue, questions: tuple[NoulQuestion, ...]) -> dict[str, float]:
        """Ask ``questions`` about ``state`` in one request; the probability of yes for each, by its id."""
        reply: JsonObject = await self._post(
            state, {question.id: {"type": "noul", "instructions": question.instructions} for question in questions}
        )
        return _probabilities(reply, questions, self._url)

    async def scores(self, state: JsonValue, questions: tuple[ScoreQuestion, ...]) -> dict[str, ScoreAnswer]:
        """Ask ``questions`` about ``state`` in one request; the score answer to each, by its id."""
        reply: JsonObject = await self._post(
            state,
            {
                question.id: {
                    "type": "score",
                    "instructions": question.instructions,
                    "criteria": [*question.levels],
                }
                for question in questions
            },
        )
        return _scores(reply, questions, self._url)

    async def choices(self, state: JsonValue, questions: tuple[ChoiceQuestion, ...]) -> dict[str, ChoiceAnswer]:
        """Ask ``questions`` about ``state`` in one request; the choice answer to each, by its id."""
        reply: JsonObject = await self._post(
            state,
            {
                question.id: {
                    "type": "choice",
                    "instructions": question.instructions,
                    "criteria": dict(question.options),
                }
                for question in questions
            },
        )
        return _choices(reply, questions, self._url)

    async def _post(self, state: JsonValue, questions: JsonObject) -> JsonObject:
        payload: JsonObject = {"state": state, "model": self._model, "questions": questions}
        return await post_json(self._client, self._url, self._headers, payload, classify_generic, _POLICY, "typesafe")

    async def aclose(self) -> None:
        await self._client.aclose()

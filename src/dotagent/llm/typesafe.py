"""TypeSafe's System One API, for the typed judge: yes/no questions ("Noul" in TypeSafe's words) about a state, each
answered with the probability of yes.

One request carries the state and every question about it: ``POST {base_url}/v1/systemone`` with the key as a bearer
token and the body ``{"state", "model", "questions": {id: {"type": "noul", "instructions"}}}``; the answer is
``{"model", "answers": {id: {"type": "noul", "noul": p}}, "usage"}`` with ``p`` from 0 (no) to 1 (yes). A refused key
is HTTP 401, a body it rejects 422, a rate limit 429 and an overloaded service 529; the last two are retried.
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


class NoulQuestion(FrozenModel):
    """A yes/no question about the state. ``id`` names its answer; TypeSafe does not show it to the model."""

    id: str
    instructions: str


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


class TypeSafeClient:
    """A connection to TypeSafe's System One API with one key."""

    def __init__(self, config: JudgeConfig, api_key: str) -> None:
        self._model: str = config.model
        self._url: str = f"{config.base_url.rstrip('/')}{_PATH}"
        self._headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
        self._client: httpx.AsyncClient = build_client(config.timeout_s, config.timeout_s)

    async def nouls(self, state: JsonValue, questions: tuple[NoulQuestion, ...]) -> dict[str, float]:
        """Ask ``questions`` about ``state`` in one request; the probability of yes for each, by its id."""
        payload: JsonObject = {
            "state": state,
            "model": self._model,
            "questions": {
                question.id: {"type": "noul", "instructions": question.instructions} for question in questions
            },
        }
        reply: JsonObject = await post_json(
            self._client, self._url, self._headers, payload, classify_generic, _POLICY, "typesafe"
        )
        return _probabilities(reply, questions, self._url)

    async def aclose(self) -> None:
        await self._client.aclose()

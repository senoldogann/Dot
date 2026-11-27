"""The asks of Dot to the Dot Next app (item 2051, :mod:`dotagent.phone.asks`): ``GET /api/phone/asks/next`` waits for
the next ask meant for the phone that calls it, and ``POST /api/phone/asks/{id}`` takes its answer. Only a paired
phone's own key reaches them (:func:`dotagent.server.routes_phone.asking_phone`): an ask is one phone's."""

from fastapi import APIRouter, Request, Response

from dotagent.phone.asks import POLL_S, AnswerBody, PhoneAsk
from dotagent.server.context import ServerContext
from dotagent.server.routes_phone import asking_phone


def phone_asks_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    @router.get("/phone/asks/next", response_model=None)
    async def next_ask(http: Request) -> PhoneAsk | Response:
        """The next ask for this phone, or 204 after :data:`POLL_S` with none; the phone asks again at once while Dot
        Next is on its screen. A request the phone dropped takes no ask."""
        phone_id: str = asking_phone(http)
        ask: PhoneAsk | None = await ctx.runtime.phone_link.next_ask(phone_id, POLL_S, http.is_disconnected)
        return Response(status_code=204) if ask is None else ask

    @router.post("/phone/asks/{ask_id}", status_code=204)
    async def answer_ask(ask_id: str, body: AnswerBody, http: Request) -> None:
        """The phone's answer to one of its asks: 404 when the ask is not this phone's or no longer waits (its run gave
        up), 422 when the answer is of another kind."""
        ctx.runtime.phone_link.answer(asking_phone(http), ask_id, body.answer)

    return router

"""Routes of the hooks (:mod:`dotagent.hooks`, ``docs/hooks.md``).

``POST /api/hooks/{id}`` is the call a service makes: the guard lets it through without the bearer token (its key is
the signature, :func:`dotagent.server.guard.is_hook_call`), and nothing else under ``/api/hooks`` is let through so.
The rest is Settings › Hooks: the list, making a hook (whose answer is the one that shows its secret), rotating the
secret (shown once again), switching it and deleting it; the guard answers these to the installation's token alone,
never a phone's key, since a hook is a way into Dot."""

import time
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from dotagent.core.errors import DotError
from dotagent.core.paths import hooks_path
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.hooks.admin import (
    HOOK_PATH,
    MadeHook,
    add_hook,
    list_hooks,
    remove_hook,
    rotate_hook,
    switch_hook,
)
from dotagent.hooks.calls import CallAnswer, CallCounter, Refusals, take_call
from dotagent.hooks.model import SIGNATURE_HEADER, TIMESTAMP_HEADER, Hook, HookCall, HookDraft
from dotagent.hooks.start import start_hook
from dotagent.server.context import ServerContext


class HookView(FrozenModel):
    """A hook as the page shows it: everything but its secret and the signatures it remembers."""

    id: str
    name: str
    path: str
    schedule_id: str | None
    agent_id: str | None
    template: str
    fields: tuple[str, ...]
    per_minute: int
    enabled: bool
    created_at: float
    calls: tuple[HookCall, ...]
    refused: tuple[HookCall, ...]


class MadeHookView(FrozenModel):
    """A hook just made or rotated, and its secret: shown this once."""

    hook: HookView
    secret: str


class HookSwitch(FrozenModel):
    enabled: bool


def hook_view(hook: Hook, refused: tuple[HookCall, ...]) -> HookView:
    """``hook`` as the page shows it, with the calls refused before their signature matched (``refused``, kept in the
    daemon's memory), the newest first like its calls."""
    return HookView(
        id=hook.id,
        name=hook.name,
        path=HOOK_PATH.format(id=hook.id),
        schedule_id=hook.schedule_id,
        agent_id=hook.agent_id,
        template=hook.template,
        fields=hook.fields,
        per_minute=hook.per_minute,
        enabled=hook.enabled,
        created_at=hook.created_at,
        calls=tuple(reversed(hook.calls)),
        refused=tuple(reversed(refused)),
    )


def _made(made: MadeHook) -> MadeHookView:
    return MadeHookView(hook=hook_view(made.hook, ()), secret=made.secret)


def hooks_router(ctx: ServerContext, status_of: Callable[[DotError], int]) -> APIRouter:
    """The routes of the hooks; ``status_of`` is the status the API answers an error with (the app's own)."""
    router: APIRouter = APIRouter(prefix="/api")
    counter: CallCounter = CallCounter()
    refusals: Refusals = Refusals()

    def view(hook: Hook) -> HookView:
        return hook_view(hook, refusals.of(hook.id))

    @router.get("/hooks")
    async def hooks() -> list[HookView]:
        """The hooks, the one made first first."""
        return [view(hook) for hook in await list_hooks(hooks_path())]

    @router.post("/hooks", status_code=201)
    async def make(draft: HookDraft) -> MadeHookView:
        """Make a hook, switched on: the answer carries its secret, which no answer carries again."""
        return _made(await add_hook(ctx.runtime.store, hooks_path(), draft, time.time()))

    @router.post("/hooks/{hook_id}/rotate")
    async def rotate(hook_id: str) -> MadeHookView:
        """A new secret for the hook, shown this once; the old one signs nothing from now on."""
        return _made(await rotate_hook(hooks_path(), hook_id))

    @router.post("/hooks/{hook_id}/switch")
    async def switch(hook_id: str, request: HookSwitch) -> HookView:
        return view(await switch_hook(hooks_path(), hook_id, request.enabled))

    @router.post("/hooks/{hook_id}/delete")
    async def delete(hook_id: str) -> HookView:
        made: Hook = await remove_hook(hooks_path(), hook_id)
        kept: frozenset[str] = frozenset(hook.id for hook in await list_hooks(hooks_path()))
        counter.forget(kept)
        refusals.forget(kept)
        return hook_view(made, ())

    @router.post("/hooks/{hook_id}")
    async def call(hook_id: str, request: Request) -> JSONResponse:
        """A service's call: 202 with the run it started, or 401, 422, 429, 503 or the status of why the work could not
        start (409 while a run of the schedule works). The body is read whole by the body-limit layer, at most
        :data:`dotagent.hooks.model.MOST_BODY` bytes."""
        body: bytes = await request.body()
        now: float = time.time()

        async def start(hook: Hook, payload: JsonObject) -> str:
            return await start_hook(ctx.runtime, ctx.manager, ctx.scheduler, hook, payload, now)

        answer: CallAnswer = await take_call(
            hooks_path(),
            hook_id,
            request.headers.get(TIMESTAMP_HEADER),
            request.headers.get(SIGNATURE_HEADER),
            body,
            now,
            counter,
            refusals,
            start,
            status_of,
        )
        return JSONResponse(
            {"message": answer.message, "run_id": answer.run_id},
            status_code=answer.status,
            headers={"Retry-After": "60"} if answer.status == 429 else None,
        )

    return router

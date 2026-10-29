"""Routes for what Dot does to come to the owner (items 570, 571 and 715): the morning brief, the watchers and Dot's own
initiative."""

import asyncio
import time

from fastapi import APIRouter

from dotagent.core.types import FrozenModel
from dotagent.oauth.providers import ProviderId
from dotagent.oauth.tokens import Account
from dotagent.proactive.brief import BriefRequest, BriefView, brief_view, set_brief
from dotagent.proactive.github import GITHUB_TOKEN_NAME
from dotagent.proactive.initiative import idle_seconds
from dotagent.proactive.todos import (
    InitiativeRequest,
    InitiativeView,
    TodoRequest,
    initiative_view,
    let_go,
    owners_todo,
    set_initiative,
    take_up_again,
)
from dotagent.proactive.watchers import (
    DEFAULT_MINUTES,
    WatcherRecord,
    WatcherRequest,
    WatcherView,
    add_watcher,
    change_watcher,
    get_watcher,
    list_watchers,
    remove_watcher,
    watcher_view,
)
from dotagent.proactive.watching import LookOutcome, try_now
from dotagent.secrets import optional_secret
from dotagent.server.context import ServerContext


class AccountChoice(FrozenModel):
    """An account a mail or calendar watcher can look in: the provider and the address it was signed in with."""

    provider: ProviderId
    address: str


class WatcherOptions(FrozenModel):
    """What the form of a watcher offers: the accounts signed in, whether GitHub's token is kept (never the token), the
    minutes each kind starts with, the limits an hour holds, and whether the watchers look at all
    (``[watchers] enabled``)."""

    accounts: tuple[AccountChoice, ...]
    github: bool
    minutes: dict[str, int]
    tells_per_hour: int
    runs_per_hour: int
    looking: bool


class TryResult(FrozenModel):
    """What Check now came to: the look's own account of itself and the watcher as its row shows it now."""

    outcome: LookOutcome
    watcher: WatcherView


def proactive_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    def moded(view: InitiativeView) -> InitiativeView:
        """``view`` with the approval mode Dot's own runs start in (item 756) and the hours they wait (item 826), for
        the card to say."""
        config = ctx.runtime.config.initiative
        return view.model_copy(update={"approvals": config.approvals, "peak_hours_utc": config.peak_hours_utc})

    @router.get("/initiative")
    async def get_initiative() -> InitiativeView:
        """Dot's own initiative (item 715): whether it works on its own list while the Mac is idle, when it last looked
        for work, and the list: what is being done and to do, then what was done or let go in the last two weeks; and
        whether macOS says how long the Mac has been idle (``idle_readable``), which it needs to start anything."""
        view: InitiativeView = await initiative_view(ctx.runtime.store)
        readable: bool = await asyncio.to_thread(idle_seconds) is not None
        return moded(view).model_copy(update={"idle_readable": readable})

    @router.put("/initiative")
    async def put_initiative(request: InitiativeRequest) -> InitiativeView:
        """Switch Dot's own initiative on or off; a run of it under way goes on until its end or a stop."""
        return moded(await set_initiative(ctx.runtime.store, request.enabled))

    @router.post("/initiative/todos", status_code=201)
    async def post_todo(request: TodoRequest) -> InitiativeView:
        """Put a thing of the owner's on Dot's list; 422 when it is on it, or was done or let go in the last 30 days."""
        return moded(await owners_todo(ctx.runtime.store, request))

    @router.post("/initiative/todos/{todo_id}/drop")
    async def drop_todo(todo_id: str) -> InitiativeView:
        """Take a thing to do off the list (404 for none); a thing being done is left to its run."""
        return moded(await let_go(ctx.runtime.store, todo_id))

    @router.post("/initiative/todos/{todo_id}/again")
    async def again_todo(todo_id: str) -> InitiativeView:
        """Put a thing that was let go back on the list, its tries counted from none."""
        return moded(await take_up_again(ctx.runtime.store, todo_id))

    @router.get("/brief")
    async def get_brief() -> BriefView:
        """The morning brief as its card shows it: whether it is on, its time and days, the owner's own lines, and
        when it was last written and fires next."""
        return await brief_view(ctx.runtime)

    @router.put("/brief")
    async def put_brief(request: BriefRequest) -> BriefView:
        """Set the morning brief: on, its time (``HH:MM``), its days (``every`` or ``weekdays``) and the owner's own
        lines. Switched on it is a schedule of the scheduler (``research``, read-only; its notice is always) that
        shows in ``GET /api/schedules`` as the brief; 422 for what the scheduler refuses (too many schedules on) and
        for lines that are too long or too many."""
        return await set_brief(ctx.runtime, request, time.time())

    @router.get("/watchers")
    async def list_all_watchers() -> list[WatcherView]:
        """The watchers, in the order they were made, each with how its looks go."""
        now: float = time.time()
        return [watcher_view(record, now) for record in await list_watchers(ctx.runtime.store)]

    @router.get("/watchers/options")
    async def watcher_options() -> WatcherOptions:
        """What the form of a watcher offers: the accounts signed in, whether GitHub's token is kept, the minutes each
        kind starts with and the limits."""
        accounts: list[Account] = await ctx.runtime.accounts.accounts()
        token: str | None = await asyncio.to_thread(optional_secret, GITHUB_TOKEN_NAME)
        return WatcherOptions(
            accounts=tuple(AccountChoice(provider=account.provider, address=account.address) for account in accounts),
            github=token is not None,
            minutes=dict(DEFAULT_MINUTES),
            tells_per_hour=ctx.runtime.config.watchers.tells_per_hour,
            runs_per_hour=ctx.runtime.config.watchers.runs_per_hour,
            looking=ctx.runtime.config.watchers.enabled,
        )

    @router.post("/watchers", status_code=201)
    async def add_a_watcher(request: WatcherRequest) -> WatcherView:
        """Make a watcher: a kind (``mail``, ``calendar``, ``github``), the owner's words, how often it looks (mail
        and GitHub) or how many minutes before an event it tells (calendar), and what it does (``tell``, or ``run``
        with the goal of its run). Its first look tells nothing. 422 for what its kind lacks (mail needs an account and
        words, the calendar an account, GitHub takes no account, a run needs its goal) and past 20 watchers."""
        now: float = time.time()
        return watcher_view(await add_watcher(ctx.runtime.store, request, now), now)

    @router.patch("/watchers/{watcher_id}")
    async def change_a_watcher(watcher_id: str, request: WatcherRequest) -> WatcherView:
        """Change a watcher: the whole of its choices, as for a new one. What it handled is kept unless its kind or its
        account changed; one switched on again looks at once. 404 for none."""
        return watcher_view(await change_watcher(ctx.runtime.store, watcher_id, request), time.time())

    @router.delete("/watchers/{watcher_id}")
    async def delete_a_watcher(watcher_id: str) -> WatcherView:
        """Delete a watcher; answers what was deleted. The chats of the runs it started stay. 404 for none."""
        return watcher_view(await remove_watcher(ctx.runtime.store, watcher_id), time.time())

    @router.post("/watchers/{watcher_id}/try")
    async def check_a_watcher_now(watcher_id: str) -> TryResult:
        """Check now: one look at once, whatever the watcher's own time, which tells what is new as any look does (the
        first look of a watcher tells nothing). A look that fails is not an error of this call: it answers with the
        failure in ``outcome.error``, which the row says too. 404 for no watcher; 409 for one switched off, while the
        watchers are off in the configuration and while the emergency stop is engaged."""
        outcome: LookOutcome = await try_now(ctx.runtime, ctx.manager, watcher_id)
        record: WatcherRecord = await get_watcher(ctx.runtime.store, watcher_id)
        return TryResult(outcome=outcome, watcher=watcher_view(record, time.time()))

    return router

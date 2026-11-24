"""What waits on the owner, in one list (item 1422): every approval a run asks, every question (``ask_user``), every
request for a secret (``ask_secret``) and every hand-off, newest first, for the iPhone app's list and its badge. The
web UI's asks read the same records the same way (:func:`open_asks`); the decision routes are the ones they were.

An item carries who asks (the agent), which run and chat, what it is called and since when, the route that opens it,
and for a question the options it offers and, when the run recommended one, which. It never carries what a secret is: a
request for one is told by its id and who asks it alone, under a fixed title, and the card says the rest. A hand-off
(item 1423: a step only the owner can do, which the run parks at: a credential, a payment) is told by the host of the
page alone under a fixed line, and there is one for a chat: nothing of the step, the page or a credential."""

from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ValidationError

from dotagent.agent.held_secrets import SECRET_SEVERITY
from dotagent.agent.questions import QuestionOption, stored_options
from dotagent.core.errors import NotFoundError
from dotagent.core.logs import get_logger
from dotagent.core.text import plain_line, redact_secrets
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, RunRecord

log = get_logger(__name__)

type NeedsYouKind = Literal["approval", "question", "secret", "handoff"]

# How many open asks of each state are read at most: more than a person answers from a list.
MOST_ASKS: int = 200
# How long a title is, as the Activity's rows cut it.
TITLE_CHARS: int = 120
# What a request for a secret is called: never the card's own words, which say what the secret is for.
SECRET_TITLE: str = "Dot asks for a secret"
# What a hand-off is called: a fixed line, with the host of the page it parks at when the step names one, and never the
# step's own words, which carry what the page showed (item 1423).
HANDOFF_TITLE: str = "Dot needs you on {host}"
HANDOFF_TITLE_NO_HOST: str = "Dot needs your help"
# The link that opens a run in the iPhone app.
ROUTE_PREFIX: str = "dotagent://run/"
_HOST_CHARS: int = 253
# Who asks when no agent of the owner's does.
_DOT: str = "Dot"
# The severity a card of a hand-off and of a question carries (``approval_requested``).
_HANDOFF: str = "handoff"
_QUESTION: str = "question"


class NeedsYouItem(FrozenModel):
    """One thing that waits on the owner. ``id`` is the approval's, which the decision routes take; ``kind`` says what
    it is; ``thread_id`` is the chat to open (the top chat for a helper's run); ``since`` is when it was asked, in
    seconds since the epoch; ``route`` is the link that opens the run in the app (``dotagent://run/<run id>``).
    ``choices`` are the labels a question offers and ``recommended`` the one its run recommended: each is left out when
    there is none."""

    id: str
    kind: NeedsYouKind
    run_id: str
    thread_id: str
    agent: str
    title: str
    since: float
    route: str
    choices: tuple[str, ...] | None = None
    recommended: str | None = None


class NeedsYou(FrozenModel):
    """Everything that waits on the owner, newest first, and how many things that is (the app's badge)."""

    count: int
    items: tuple[NeedsYouItem, ...]


def kind_of(severity: str) -> NeedsYouKind:
    """What a card of ``severity`` is: a question, a request for a secret, a hand-off, or else an approval."""
    if severity == _QUESTION:
        return "question"
    if severity == SECRET_SEVERITY:
        return "secret"
    return "handoff" if severity == _HANDOFF else "approval"


async def open_asks(store: Store, live: Mapping[str, RunRecord]) -> list[tuple[ApprovalRecord, RunRecord]]:
    """The cards that wait on the owner, each with the run that asks: those a run at work waits for (``pending``; a
    run not in ``live`` has none, as its card is the leftover of a daemon that stopped), then those a run left for them
    (``left``: a question it went on without, a step kept for them), in the order the store lists them. A card whose
    run is gone is not one."""
    asks: list[tuple[ApprovalRecord, RunRecord]] = []
    for approval in await store.list_approvals("pending", MOST_ASKS):
        waiting: RunRecord | None = live.get(approval.run_id)
        if waiting is not None:
            asks.append((approval, waiting))
    for left in await store.list_approvals("left", MOST_ASKS):
        try:
            asked: RunRecord = live.get(left.run_id) or await store.get_run(left.run_id)
        except NotFoundError:
            log.warning("ask_of_a_run_that_is_gone", approval_id=left.id, run_id=left.run_id)
            continue
        asks.append((left, asked))
    return asks


async def _chat_of(store: Store, run: RunRecord) -> str:
    """The chat a run's ask is read in: its own, or for a helper's run the chat of the run at the top of its
    delegations (its own when a run above it is gone)."""
    top: RunRecord = run
    try:
        while top.parent_run_id is not None:
            top = await store.get_run(top.parent_run_id)
    except NotFoundError:
        return run.thread_id
    return top.thread_id


async def _agent_of(store: Store, run: RunRecord, names: dict[str, str]) -> str:
    """Who asks: the owner's agent that the run works for, else Dot (also for an agent that was deleted since)."""
    if run.agent_id is None:
        return _DOT
    if run.agent_id not in names:
        try:
            names[run.agent_id] = (await store.get_agent(run.agent_id)).name
        except NotFoundError:
            names[run.agent_id] = _DOT
    return names[run.agent_id]


def _options_of(approval: ApprovalRecord) -> tuple[QuestionOption, ...]:
    """The options a question stores, none when it stores none or what it stores cannot be read: the list and the badge
    that counts it still tell the question, which is answered in words."""
    try:
        return stored_options(approval.args)
    except ValidationError as error:
        log.warning("question_options_unreadable", approval_id=approval.id, error=str(error))
        return ()


def _recommended(options: tuple[QuestionOption, ...]) -> str | None:
    """The label of the option the run recommended, when it recommended one."""
    return next((option.label for option in options if option.recommended), None)


def host_of(args: JsonObject) -> str | None:
    """The host of the page a step of a hand-off names as its target (an http or https address), and nothing else of
    the address: not its user, path, query or fragment. ``None`` when the step names no such page."""
    target: object = args.get("target")
    if not isinstance(target, str):
        return None
    try:
        parts = urlsplit(target)
        host: str | None = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or len(host) > _HOST_CHARS:
        return None
    return host


def _title(kind: NeedsYouKind, approval: ApprovalRecord) -> str:
    if kind == "secret":
        return SECRET_TITLE
    if kind == "handoff":
        host: str | None = host_of(approval.args)
        return HANDOFF_TITLE_NO_HOST if host is None else HANDOFF_TITLE.format(host=host)
    return plain_line(redact_secrets(approval.summary), TITLE_CHARS)


async def _item(store: Store, approval: ApprovalRecord, run: RunRecord, names: dict[str, str]) -> NeedsYouItem:
    kind: NeedsYouKind = kind_of(approval.severity)
    options: tuple[QuestionOption, ...] = _options_of(approval) if kind == "question" else ()
    return NeedsYouItem(
        id=approval.id,
        kind=kind,
        run_id=run.id,
        thread_id=await _chat_of(store, run),
        agent=await _agent_of(store, run, names),
        title=_title(kind, approval),
        since=approval.created_at,
        route=f"{ROUTE_PREFIX}{run.id}",
        choices=tuple(option.label for option in options) or None,
        recommended=_recommended(options),
    )


async def waiting_asks(
    store: Store, live: Mapping[str, RunRecord], now: float
) -> list[tuple[ApprovalRecord, RunRecord]]:
    """The cards of :func:`open_asks` that still wait at ``now``: a step kept for the owner whose time is over has
    lapsed, though nothing has closed its card yet, and it is no longer theirs to answer."""
    return [(ask, run) for ask, run in await open_asks(store, live) if ask.kept_until is None or ask.kept_until > now]


async def read_needs_you(store: Store, live: Mapping[str, RunRecord], now: float) -> NeedsYou:
    """What waits on the owner at ``now``, newest first; ``live`` are the runs at work here (:func:`open_asks`). A chat
    has one hand-off at most, the newest: a run parks at one step only the owner can do, and the ones it kept before
    wait behind it."""
    names: dict[str, str] = {}
    items: list[NeedsYouItem] = [
        await _item(store, approval, run, names) for approval, run in await waiting_asks(store, live, now)
    ]
    items.sort(key=lambda item: item.since, reverse=True)
    handed: set[str] = set()
    listed: list[NeedsYouItem] = []
    for item in items:
        if item.kind == "handoff":
            if item.thread_id in handed:
                continue
            handed.add(item.thread_id)
        listed.append(item)
    return NeedsYou(count=len(listed), items=tuple(listed))


async def needs_you_count(store: Store, live: Mapping[str, RunRecord], now: float) -> int:
    """How many things wait on the owner at ``now``: the ``count`` of :func:`read_needs_you`, for the app's badge."""
    return (await read_needs_you(store, live, now)).count

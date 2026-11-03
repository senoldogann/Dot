"""Where the pull requests of the repairs stand on GitHub (item 958, the owner's ask of 2026-11-03: "closed should read
closed and merged merged, or everything reads Waits for you"). A session that left a pull request for the owner, or
one that a second look did not reach, kept the end it had; the owner, or another session's replay of it, merged or
closed that pull request on GitHub, and the page went on saying it waited. Dot reads their state again, when the page
opens and before each night (:meth:`dotagent.scheduler.runner.Scheduler.settle_repairs`), in the slot of the night, so
that the two never write the book at once:

- a session's and a problem's pull request is ``merged``, ``closed`` or ``open`` (``pr_state``), which the page shows;
- a problem that waited on a pull request merged since is fixed, from then on (:func:`dotagent.repair.problems.
  fixed_again` reads a later sighting of it as its fix not holding); one whose pull request was closed unmerged is
  closed, which no night tries again on its own: the owner closed it, or a fix of it went in by another pull request.

What is merged or closed stays so, and is not read again."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from dotagent.core.errors import RepairError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.repair.github import PrStanding, pr_number, pr_standing
from dotagent.repair.night import SESSIONS_KEY, RepairSession, SessionLog, read_sessions
from dotagent.repair.problems import BOOK_KEY, Book, Problem, PrState, read_book
from dotagent.store.db import Store

log = get_logger(__name__)

type Sleep = Callable[[float], Awaitable[None]]

# How many times GitHub is asked for a pull request's state, and how long the first wait between asks is (doubled
# after each).
_ASKS: int = 3
_FIRST_WAIT_S: float = 2.0
_STATES: dict[str, PrState] = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
_SETTLED: frozenset[PrState] = frozenset({"merged", "closed"})
# The ends of a session whose pull request Dot merged itself: nothing to read.
_MERGED_ENDS: frozenset[str] = frozenset({"merged", "merged_unpulled"})


def urls_to_read(book: Book, sessions: Sequence[RepairSession]) -> tuple[str, ...]:
    """The pull requests whose state is read: of each problem that waits on one, and of each session whose pull request
    Dot did not merge itself, unless it was read merged or closed already."""
    urls: list[str] = [
        problem.pr_url
        for problem in book.problems
        if problem.status == "waiting" and problem.pr_url and problem.pr_state not in _SETTLED
    ]
    urls.extend(
        session.pr_url
        for session in sessions
        if session.pr_url and session.end not in _MERGED_ENDS and session.pr_state not in _SETTLED
    )
    return tuple(dict.fromkeys(urls))


class Standing(FrozenModel):
    """Where a pull request stands, as the page names it, and when it was merged or closed (``None`` while open)."""

    state: PrState
    at: float | None


def _settled_problem(problem: Problem, standing: Standing) -> Problem:
    """``problem`` with ``standing``, read of its pull request, when it waits on that pull request; any other is as it
    was, since the pull request it names may be an older session's whose state is no word on it now. One merged or
    closed is settled since GitHub merged or closed it, which :func:`dotagent.repair.problems.fixed_again` reads."""
    if problem.status != "waiting":
        return problem
    if standing.state == "merged":
        return problem.model_copy(
            update={
                "status": "fixed",
                "pr_state": standing.state,
                "settled_at": standing.at,
                "note": "Its pull request was merged on GitHub.",
            }
        )
    if standing.state == "closed":
        return problem.model_copy(
            update={
                "status": "closed",
                "pr_state": standing.state,
                "settled_at": standing.at,
                "note": "Its pull request was closed on GitHub without a merge; Dot does not try it again on its own.",
            }
        )
    return problem.model_copy(update={"pr_state": standing.state})


def with_pr_states(
    book: Book, sessions: Sequence[RepairSession], standings: Mapping[str, Standing]
) -> tuple[Book, tuple[RepairSession, ...]]:
    """``book`` and ``sessions`` with where their pull requests stand (by url): each session takes its state, and a
    problem that waited on a merged one is fixed, one that waited on a closed one closed, one that waits on an open one
    marked so."""
    problems: tuple[Problem, ...] = tuple(
        _settled_problem(problem, standings[problem.pr_url]) if problem.pr_url in standings else problem
        for problem in book.problems
    )
    kept: tuple[RepairSession, ...] = tuple(
        session.model_copy(update={"pr_state": standings[session.pr_url].state})
        if session.pr_url in standings
        else session
        for session in sessions
    )
    return book.model_copy(update={"problems": problems}), kept


def standing_of(read: PrStanding, url: str) -> Standing:
    """GitHub's answer for the pull request at ``url`` as the page names it; one merged or closed says since when."""
    state: PrState | None = _STATES.get(read.state)
    if state is None:
        raise RepairError("GitHub named no known state of a pull request", {"url": url, "state": read.state})
    if state in _SETTLED and read.at is None:
        raise RepairError("GitHub named no time a pull request was merged or closed", {"url": url, "state": state})
    return Standing(state=state, at=read.at)


async def read_standing(repo: Path, url: str, env: Mapping[str, str], sleep: Sleep) -> Standing:
    """Where the pull request at ``url`` stands on GitHub, asked again with a growing wait when the ask fails (a warning
    each time); the last failure is raised."""
    number: int | None = pr_number(url)
    if number is None:
        raise RepairError("a repair's pull request has no number in its address", {"url": url})
    wait: float = _FIRST_WAIT_S
    for ask in range(1, _ASKS + 1):
        try:
            return standing_of(await pr_standing(repo, number, env), url)
        except RepairError as error:
            if ask == _ASKS:
                raise
            log.warning("repair_pr_state_retry", url=url, ask=ask, error=error.message, context=error.context)
            await sleep(wait)
            wait *= 2
    raise RepairError("a pull request's state was never asked", {"url": url})


class Settled(FrozenModel):
    """What a reading came to: the book and the sessions with the states read, and the pull requests GitHub could not
    be asked of, each with the last error, which stay unread until the next reading."""

    book: Book
    sessions: tuple[RepairSession, ...]
    unread: dict[str, str]


async def settle(
    book: Book, sessions: Sequence[RepairSession], repo: Path, env: Mapping[str, str], sleep: Sleep
) -> Settled:
    """``book`` and ``sessions`` with the states of their pull requests read from GitHub now; one that cannot be read
    keeps the others from nothing (the pre-push review of item 958)."""
    standings: dict[str, Standing] = {}
    unread: dict[str, str] = {}
    for url in urls_to_read(book, sessions):
        try:
            standings[url] = await read_standing(repo, url, env, sleep)
        except RepairError as error:
            unread[url] = f"{error.message} {error.context}"
    if standings:
        log.info("repair_pr_states_read", states={url: standing.state for url, standing in standings.items()})
    settled_book, settled_sessions = with_pr_states(book, sessions, standings)
    return Settled(book=settled_book, sessions=settled_sessions, unread=unread)


async def settle_stored(store: Store, repo: Path, env: Mapping[str, str], sleep: Sleep) -> None:
    """Read the book and the sessions kept, settle them (:func:`settle`) and keep them again, when anything changed.
    Raises :class:`RepairError` naming the pull requests that could not be read, once what was read is kept."""
    book: Book = read_book(await store.kv_get(BOOK_KEY))
    sessions: tuple[RepairSession, ...] = read_sessions(await store.kv_get(SESSIONS_KEY))
    settled: Settled = await settle(book, sessions, repo, env, sleep)
    if settled.book != book:
        await store.kv_set(BOOK_KEY, settled.book.model_dump_json())
    if settled.sessions != sessions:
        await store.kv_set(SESSIONS_KEY, SessionLog(sessions=settled.sessions).model_dump_json())
    if settled.unread:
        raise RepairError(
            "GitHub could not be asked where some pull requests stand",
            {"unread": list(settled.unread), "errors": list(settled.unread.values())},
        )

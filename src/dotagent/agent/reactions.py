"""The reactions of the owner on Dot's messages (item 586): what each does while the run it is about works, and
after it ended. docs/runs.md says it for the owner; this is how it is held.

A reaction is the owner's own input, from the page behind the API's token, and it is a small word: it never
vouches. While the run works it reaches the run at its next step as a note from Dot (:meth:`RunManager.tell`), which
clears no mark of untrusted content and tells the reviewer nothing; a burst of reactions within seconds is one note,
and a hold (✋) is told at once and holds the run. After the run ended, "not right", "do it again" and "explain" start
a follow-up run in the same chat, as the owner's own message would (queued behind the run at work), unless Settings
says a reaction is only feedback; the follow-up is at most one per message and reaction, and at most ``[reactions]
follow_ups_per_hour`` an hour. A pin (📌) keeps the message as a note, at work and after, marked as read from outside
content when the run read some. The reaction itself is a row of the store that stands until it is taken back, and an
event of the run, told to every page."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

from dotagent.agent.manager import RunManager
from dotagent.agent.reaction_words import (
    ANSWER,
    QUOTE_CHARS,
    REACTION_ADDED_EVENT,
    REACTION_REMOVED_EVENT,
    Told,
    act_of,
    follow_up_context,
    follow_up_goal,
    message_step,
    quote_of,
    steer_note,
)
from dotagent.config import AfterRun, ReactionsConfig
from dotagent.core.errors import DotError, InvalidValueError, NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.text import plain_line
from dotagent.core.types import FrozenModel
from dotagent.memory.service import SavedMemory, forget_memory, normalize_text, save_memory
from dotagent.policy.elements import looks_like_key
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import WORKING_STATUSES, FollowUpOf, MemoryRecord, ReactionName, ReactionRecord, RunRecord

log = get_logger(__name__)

# How long a burst of reactions on one run at work is gathered into one note: from the first reaction of the burst.
BURST_S: float = 2.5
# The tag of the note a pin makes.
NOTE_TAG: str = "from-chat"
# What a press did, or did not do: nothing (the reaction was there already, or was not), told to the run at work, held
# it, kept the message as a note, started a follow-up run, queued one behind the run at work, kept the reaction as
# feedback only, was refused for the hour's limit; and for a reaction taken back, that it was, that its note went, or
# stayed.
type ReactionEffect = Literal[
    "unchanged",
    "told",
    "held",
    "noted",
    "follow_up",
    "queued",
    "feedback",
    "limit",
    "taken_back",
    "note_gone",
    "note_kept",
]


class ReactionOutcome(FrozenModel):
    """What a press of a reaction, or taking it back, did: the reactions that stand on the message now, what it did
    (``effect``), a sentence for the owner where there is something to say (``said``), and the run a follow-up started
    (``run_id``)."""

    reactions: tuple[ReactionRecord, ...]
    effect: ReactionEffect
    said: str
    run_id: str | None


@dataclass(frozen=True)
class _Acted:
    """What a reaction did: its effect, the sentence for the owner, and the run it started."""

    effect: ReactionEffect
    said: str = ""
    run_id: str | None = None


@dataclass
class _Burst:
    """The reactions on a run at work that wait to be told to it as one note, and the wait."""

    told: list[Told] = field(default_factory=list)
    timer: asyncio.Task[None] | None = None


def note_text(text: str, limit: int) -> str:
    """The message as the note a 📌 makes: its words on one line, the marks of its Markdown left out, cut at a word to
    ``limit`` characters when it is longer, with an ellipsis."""
    line: str = normalize_text(plain_line(text, len(text)))
    if len(line) <= limit:
        return line
    cut: str = line[: limit - 1]
    return f"{cut.rsplit(' ', 1)[0] if ' ' in cut else cut}…"


async def _holds_a_secret(text: str) -> bool:
    """Whether ``text`` looks like it holds an API key or a private key, or holds a secret Dot keeps (read from the env
    file, in a thread of its own, off the event loop): the memory tools refuse such a note, and so does a pin."""
    if looks_like_key(text):
        return True
    held: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    return any(secret in text for secret in held)


class Reactions:
    """The service of the reactions: the owner's presses and their taking back, the settings that say what a reaction
    does after a run (live, :meth:`apply`), and the notes that wait to be told to the runs at work, each for
    ``burst_s`` seconds."""

    def __init__(self, runtime: Runtime, manager: RunManager, burst_s: float = BURST_S) -> None:
        self._runtime: Runtime = runtime
        self._manager: RunManager = manager
        self._burst_s: float = burst_s
        self._config: ReactionsConfig = runtime.config.reactions
        # One press at a time: a double click is one press, and a follow-up is claimed once.
        self._pressing: asyncio.Lock = asyncio.Lock()
        self._bursts: dict[str, _Burst] = {}

    @property
    def config(self) -> ReactionsConfig:
        """The settings as they stand now."""
        return self._config

    def apply(self, key: str, value: bool | AfterRun) -> None:
        """Change one setting: the switch of the follow-ups, or what one reaction does after a run. It holds from the
        next press; the caller checked that ``value`` fits ``key`` and keeps it in the config file."""
        self._config = self._config.model_copy(update={key: value})

    async def close(self) -> None:
        """Drop the notes that wait to be told: the daemon is stopping, and so are its runs."""
        bursts: list[_Burst] = list(self._bursts.values())
        self._bursts.clear()
        waiting: list[asyncio.Task[None]] = [burst.timer for burst in bursts if burst.timer is not None]
        for timer in waiting:
            timer.cancel()
        if waiting:
            await asyncio.wait(waiting)

    # ----- reading -------------------------------------------------------------------------------

    async def of_message(self, run_id: str, message: str) -> list[ReactionRecord]:
        """The reactions that stand on one message; 404 for no such run."""
        await self._runtime.store.get_run(run_id)
        return await self._runtime.store.message_reactions(run_id, message)

    async def of_chat(self, thread_id: str) -> list[ReactionRecord]:
        """The reactions that stand on the messages of a chat; 404 for no such chat."""
        await self._runtime.store.get_thread(thread_id)
        return await self._runtime.store.thread_reactions(thread_id)

    # ----- pressing ------------------------------------------------------------------------------

    async def add(self, run_id: str, message: str, reaction: ReactionName) -> ReactionOutcome:
        """Put a reaction on a message and do what it says (see the module). A reaction already there is left as it
        is and does nothing again. What cannot be done leaves the reaction off: a pin on a message too short to be a
        note."""
        store: Store = self._runtime.store
        async with self._pressing:
            run: RunRecord = await store.get_run(run_id)
            text: str = await self._words(run, message)
            if not await store.put_reaction(run.id, message, reaction, run.thread_id, time.time()):
                return await self._outcome(run.id, message, _Acted("unchanged"))
            try:
                acted: _Acted = await self._act(run, message, reaction, text)
            except BaseException:
                await store.take_reaction(run.id, message, reaction, time.time())
                raise
            await self._tell_pages(REACTION_ADDED_EVENT, run, message, reaction, acted)
            return await self._outcome(run.id, message, acted)

    async def remove(self, run_id: str, message: str, reaction: ReactionName) -> ReactionOutcome:
        """Take a reaction back: it is gone from the message, a run at work is told that the owner took it back (a
        hold taken back lets a held run go on), and the note a pin made is forgotten when it is as it was made. What
        the reaction started stays started."""
        store: Store = self._runtime.store
        async with self._pressing:
            run: RunRecord = await store.get_run(run_id)
            taken: ReactionRecord | None = await store.take_reaction(run.id, message, reaction, time.time())
            if taken is None:
                return await self._outcome(run.id, message, _Acted("unchanged"))
            acted: _Acted = await self._undo(run, taken)
            await self._tell_pages(REACTION_REMOVED_EVENT, run, message, reaction, acted)
            return await self._outcome(run.id, message, acted)

    async def _outcome(self, run_id: str, message: str, acted: _Acted) -> ReactionOutcome:
        reactions: list[ReactionRecord] = await self._runtime.store.message_reactions(run_id, message)
        return ReactionOutcome(reactions=tuple(reactions), effect=acted.effect, said=acted.said, run_id=acted.run_id)

    async def _tell_pages(self, kind: str, run: RunRecord, message: str, reaction: ReactionName, acted: _Acted) -> None:
        """Record the press as an event of the run, which every page hears (the server's lifecycle stream): the chat it
        is in, the message, the reaction, what it did and the run it started."""
        await self._runtime.bus.emit(
            run.id,
            kind,
            {
                "thread_id": run.thread_id,
                "message": message,
                "reaction": reaction,
                "effect": acted.effect,
                "follow_up_run_id": acted.run_id,
            },
            True,
        )

    async def _words(self, run: RunRecord, message: str) -> str:
        """What the message says: the answer a run ended with, or what it said in a step. A message with no words, one
        the events do not hold any more and the answer of a run that has none yet are nothing to react to."""
        step: int | None = message_step(message)
        if step is None:
            if run.status in WORKING_STATUSES:
                raise InvalidValueError("a run has no answer until it ends", {"run_id": run.id, "message": message})
            text: str = run.result
        else:
            said: str | None = await self._runtime.store.assistant_text(run.id, step)
            if said is None:
                raise NotFoundError("the run said nothing in this step", {"run_id": run.id, "message": message})
            text = said
        if text.strip() == "":
            raise InvalidValueError("the message has no words to react to", {"run_id": run.id, "message": message})
        return text

    # ----- what a reaction does ------------------------------------------------------------------

    async def _act(self, run: RunRecord, message: str, reaction: ReactionName, text: str) -> _Acted:
        if reaction == "remember":
            return await self._keep_note(run, message, text)
        if run.id in self._manager.active_run_ids():
            return await self._tell_run(run, message, reaction, text)
        if run.status in WORKING_STATUSES:
            # A run that a dotx command drives cannot be told from here, and nothing can follow it up before it ends.
            return _Acted("feedback")
        return await self._after_run(run, message, reaction, text)

    async def _tell_run(self, run: RunRecord, message: str, reaction: ReactionName, text: str) -> _Acted:
        """A reaction on a message of a run at work: told to it at its next step, with the others that come within
        :data:`BURST_S` seconds, as one note; a hold is told at once, with those that wait."""
        self._gather(
            run.id, Told(reaction=reaction, message=message, quote=quote_of(text, QUOTE_CHARS), taken_back=False)
        )
        return _Acted("held" if reaction == "hold" else "told")

    def _gather(self, run_id: str, item: Told) -> None:
        """Add ``item`` to the note that waits for the run ``run_id``. A reaction taken back that still waits is never
        told, nor its taking back. A hold and its taking back are told at once: a hold waits for nothing. The note is
        told by a task of its own, so that what it finds out (the run ended) is done outside the press that made it."""
        burst: _Burst = self._bursts.setdefault(run_id, _Burst())
        if item.taken_back:
            waiting: Told | None = next(
                (
                    held
                    for held in burst.told
                    if (held.message, held.reaction) == (item.message, item.reaction) and not held.taken_back
                ),
                None,
            )
            if waiting is not None:
                burst.told.remove(waiting)
                if not burst.told:
                    self._forget_burst(run_id)
                return
        burst.told.append(item)
        if item.reaction == "hold":
            if burst.timer is not None:
                burst.timer.cancel()
            burst.timer = asyncio.create_task(self._flush_later(run_id, 0.0), name=f"reactions-{run_id}")
        elif burst.timer is None:
            burst.timer = asyncio.create_task(self._flush_later(run_id, self._burst_s), name=f"reactions-{run_id}")

    def _forget_burst(self, run_id: str) -> None:
        burst: _Burst | None = self._bursts.pop(run_id, None)
        if burst is not None and burst.timer is not None and burst.timer is not asyncio.current_task():
            burst.timer.cancel()

    async def _flush_later(self, run_id: str, wait_s: float) -> None:
        """Task body of the wait of a burst: nothing awaits it, so what goes wrong is logged here."""
        try:
            await asyncio.sleep(wait_s)
            await self._flush(run_id)
        except Exception:
            log.exception("reactions_not_told", run_id=run_id)

    async def _flush(self, run_id: str) -> None:
        """Tell the run ``run_id`` the reactions that wait for it, as one note. A run that ended meanwhile reads nothing
        more: what it was told is done as after a run."""
        burst: _Burst | None = self._bursts.get(run_id)
        if burst is None:
            return
        told: tuple[Told, ...] = tuple(burst.told)
        self._forget_burst(run_id)
        if not told:
            return
        try:
            self._manager.tell(run_id, steer_note(told), act_of(told))
        except RunConflict:
            await self._missed(run_id, told)

    async def _missed(self, run_id: str, told: tuple[Told, ...]) -> None:
        """The run ended before the note reached it: each reaction of it that still stands is done as after a run, and
        the pages hear what it came to (the press answered "told"): the run a follow-up started, or that it is kept as
        feedback."""
        store: Store = self._runtime.store
        run: RunRecord = await store.get_run(run_id)
        for item in told:
            standing: list[ReactionRecord] = [
                record
                for record in await store.message_reactions(run_id, item.message)
                if record.reaction == item.reaction
            ]
            if item.taken_back or not standing:
                continue
            try:
                async with self._pressing:
                    text: str = await self._words(run, item.message)
                    acted: _Acted = await self._after_run(run, item.message, item.reaction, text)
                    await self._tell_pages(REACTION_ADDED_EVENT, run, item.message, item.reaction, acted)
            except DotError as error:
                log.warning(
                    "reaction_not_done_after_the_run", run_id=run_id, error=error.message, context=error.context
                )

    async def _after_run(self, run: RunRecord, message: str, reaction: ReactionName, text: str) -> _Acted:
        """A reaction on a message of a run that ended: a follow-up run in the same chat, as the owner's own message,
        when the settings say so for this reaction and the limits hold; else it is only kept as feedback."""
        config: ReactionsConfig = self._config
        if reaction == "remember":
            return await self._keep_note(run, message, text)
        if not config.follow_ups or getattr(config, reaction) != "follow_up":
            return _Acted("feedback")
        if run.parent_run_id is not None or run.campaign_id is not None:
            # What a delegated run and a campaign's session did is told to the run that asked it, and to the campaign.
            return _Acted("feedback")
        store: Store = self._runtime.store
        claim = await store.claim_follow_up(run.id, message, reaction, time.time(), config.follow_ups_per_hour)
        if claim == "limit":
            return _Acted(
                "limit",
                f"No follow-up run: {config.follow_ups_per_hour} runs from reactions started in the last hour is the "
                "limit ([reactions] follow_ups_per_hour). The reaction is kept as feedback.",
            )
        if claim != "claimed":
            return _Acted("feedback")
        follow_up: FollowUpOf = FollowUpOf(
            run_id=run.id, message=message, reaction=reaction, context=follow_up_context(text)
        )
        try:
            started = await self._manager.follow_up_reaction(
                run.thread_id, follow_up_goal(reaction, message == ANSWER), follow_up
            )
        except DotError as error:
            await store.release_follow_up(run.id, message, reaction)
            return _Acted("feedback", f"No follow-up run: {error.message}. The reaction is kept as feedback.")
        except BaseException:
            await store.release_follow_up(run.id, message, reaction)
            raise
        if started.run is not None:
            return _Acted("follow_up", "Started a follow-up run.", started.run.id)
        return _Acted("queued", "Queued: it starts when the run at work in this chat has ended.")

    async def _keep_note(self, run: RunRecord, message: str, text: str) -> _Acted:
        """A pin: the message is kept as a note, which the owner chose, whether the run works or not. It is marked as
        read from outside content when the run read some at any point, as the notes a run saves while it reads such
        content are (such a note is never handed to a run on its own, and a search that finds it marks the run). A
        message that holds a key or a secret Dot holds is not kept, as the memory tools do not keep such a note."""
        store: Store = self._runtime.store
        memory = self._runtime.memory
        note: str = note_text(text, memory.config.max_chars)
        if await _holds_a_secret(note):
            raise InvalidValueError(
                "the message holds a secret, an API key, a token or a password, and a note never does: record where "
                "it lives, not what it is",
                {"run_id": run.id, "message": message},
            )
        outside: bool = await store.run_read_outside(run.id)
        saved: SavedMemory = await save_memory(memory, note, (NOTE_TAG,), None, None, None, outside)
        if saved.created:
            await store.keep_reaction_note(run.id, message, "remember", saved.record.id)
        marked: str = " It is marked as read from outside content." if outside and saved.created else ""
        return _Acted("noted", f"Kept as a note.{marked}")

    # ----- taking back ---------------------------------------------------------------------------

    async def _undo(self, run: RunRecord, taken: ReactionRecord) -> _Acted:
        if taken.reaction == "remember":
            return await self._forget_note(run, taken)
        if run.id in self._manager.active_run_ids():
            try:
                text: str = await self._words(run, taken.message)
            except DotError:
                # What the message said is gone with the events: the run is told nothing it could not place.
                return _Acted("taken_back")
            quote: str = quote_of(text, QUOTE_CHARS)
            self._gather(run.id, Told(reaction=taken.reaction, message=taken.message, quote=quote, taken_back=True))
        return _Acted("taken_back")

    async def _forget_note(self, run: RunRecord, taken: ReactionRecord) -> _Acted:
        """A pin taken back: the note it made goes, when it is still as it was made. A note the pin found there
        already, and one the owner changed since, stay."""
        if taken.note_id is None:
            return _Acted("note_kept")
        memory = self._runtime.memory
        try:
            record: MemoryRecord = await memory.store.get_memory(taken.note_id)
            text: str = await self._words(run, taken.message)
        except DotError:
            return _Acted("note_kept")
        if record.content != normalize_text(note_text(text, memory.config.max_chars)):
            return _Acted("note_kept", "The note stays in memory: it was changed since.")
        await forget_memory(memory, record.id)
        return _Acted("note_gone", "The note is gone from memory.")

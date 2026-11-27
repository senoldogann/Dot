"""The fast model's pass over the notes whose kind the fixed rules could not tell (item 1975,
:mod:`dotagent.store.kinds`): one question a note, answered with one word, recorded on the note as the model's.

The scheduler runs it off the request path, at most once an hour (``dotagent.scheduler.runner``). A provider's error
or an answer that is not one of the kinds is raised after it is logged: the note keeps no kind, and the next pass asks
again. A note's text goes to the fast model as the run's notes go to the run's model; the answer can only be a kind.
"""

import asyncio

from dotagent.core.errors import ProviderProtocolError
from dotagent.core.logs import get_logger
from dotagent.llm.calls import CallOrigin
from dotagent.llm.router import ModelRef, ModelRouter
from dotagent.llm.types import ChatRequest, ModelReply, StreamDelta, system_message, user_message
from dotagent.store.db import Store
from dotagent.store.kinds import MEMORY_KINDS, MemoryKind
from dotagent.store.models import MemoryRecord

log = get_logger(__name__)

# How many notes one pass asks of: the rest wait for the next pass.
KIND_PASS_NOTES: int = 40

_PROMPT: str = (
    "You sort a personal assistant's notes. Answer with exactly one word, the note's kind:\n"
    "profile - about the owner themself (who they are, their work, studies, accounts, company)\n"
    "people - about another person (a contact, a client, a colleague)\n"
    "workflow - how something is done: steps, a procedure, the working way to use a tool or a site\n"
    "rule - what must or must not be done: the owner's rule, preference, decision or approval\n"
    "fact - what is so now: a project's state, a setting, a measurement, an event\n"
    "lesson - what was learned from a mistake or an attempt"
)


class NotAKindError(ProviderProtocolError):
    """The fast model answered a note's kind with a word that is no kind."""


def parse_kind(answer: str, memory_id: str) -> MemoryKind:
    """The kind ``answer`` names: its one word, case and a closing full stop aside. Raises :class:`NotAKindError` for
    any other answer."""
    word: str = answer.strip().strip(".").lower()
    for kind in MEMORY_KINDS:
        if word == kind:
            return kind
    raise NotAKindError(
        "the fast model's answer is not one of the kinds of a note",
        {"memory_id": memory_id, "answer": answer[:200], "kinds": list(MEMORY_KINDS)},
    )


async def _discard(delta: StreamDelta) -> None:
    return None


async def ask_kind(router: ModelRouter, ref: ModelRef, note: MemoryRecord, timeout_s: float) -> MemoryKind:
    """The kind the fast model ``ref`` tells of ``note``; a provider's error, a timeout (as a ProviderError) or an
    answer that is no kind is raised."""
    request: ChatRequest = ChatRequest(
        model=ref.model,
        messages=(system_message(_PROMPT), user_message(f"Tags: {', '.join(note.tags)}\nNote: {note.content}", ())),
        tools=(),
        temperature=0.0,
        max_output_tokens=None,
        effort="off",
        speed="standard",
        session_key="memory-kinds",
        cache_mark=None,
        prompt_reuse="single",
        window=await router.window_of(ref),
    )
    try:
        async with asyncio.timeout(timeout_s):
            reply: ModelReply = await router.complete(ref, request, _discard, CallOrigin(run_id=None, purpose="memory"))
    except TimeoutError as error:
        raise ProviderProtocolError(
            "the fast model did not tell a note's kind in time", {"model": ref.label, "memory_id": note.id}
        ) from error
    return parse_kind(reply.text, note.id)


async def classify_untold(store: Store, router: ModelRouter, ref: ModelRef, timeout_s: float) -> int:
    """Ask the fast model ``ref`` the kind of each note the rules could not tell, up to :data:`KIND_PASS_NOTES`, and
    record each answer on its note as the model's; returns how many it recorded. A note answered with a word that is
    no kind is logged and keeps none, for the next pass; any other error of the model is raised, after what was
    recorded before it stays."""
    told: int = 0
    for note in await store.memories_without_kind(KIND_PASS_NOTES):
        try:
            kind: MemoryKind = await ask_kind(router, ref, note, timeout_s)
        except NotAKindError as error:
            log.warning("memory_kind_not_told", model=ref.label, error=error.message, context=error.context)
            continue
        if await store.tell_untold_kind(note.id, kind, "model"):
            told += 1
            log.info("memory_kind_told", memory_id=note.id, kind=kind, model=ref.label)
    return told

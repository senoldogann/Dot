"""Teaching Dot a task (item 423): the skill made from the steps the owner showed once in Dot's browser.

The owner works in a tab while :mod:`dotagent.browser.teach` records the steps; they say what the task is for, the
review model writes the skill of it, and the owner saves or discards it. A saved one is a skill like a learned one in
every way a run meets it (a folder under the playbooks, an entry in :mod:`dotagent.agent.playbooks`, offered by the
``skill`` tool), but its header says the owner taught it, nothing stands behind it from a run, and it is never retired
for failures: whether it still fits is the owner's to say. A saved one can repeat (item 666): a schedule in the chat it
was taught in whose runs start with the skill picked, as Run it now's does.
"""

import asyncio
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from dotagent.agent.playbooks import (
    NO_SITE,
    Playbook,
    PlaybookEntry,
    free_name,
    kinds_shown,
    list_playbooks,
    redacted_draft,
    slug,
    taught_name,
    write_playbook,
)
from dotagent.agent.review import PlaybookDraft, PlaybookRequest, PlaybookWriter, playbook_writer_for
from dotagent.browser.teach import Recording
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.paths import playbooks_dir
from dotagent.core.text import plain_line
from dotagent.core.types import FrozenModel
from dotagent.scheduler.plans import ScheduleRequest, create_schedule
from dotagent.secrets import known_secrets
from dotagent.store.models import ChatChoices, RunRecord, ScheduleOptions, ScheduleRecord
from dotagent.tools.schedule import chat_options

if TYPE_CHECKING:
    from dotagent.runtime import Runtime

# What the skill writer is told stands behind a recorded task: no run answered it, the owner did it.
_SHOWN_NOT_ANSWERED: str = (
    "No run answered this: the owner showed the task once, doing it in Dot's browser, and the steps above are theirs."
)


def site_of(recording: Recording) -> str:
    """The site the owner opened most while showing the task, which the skill is offered by: the host a later run of
    the same work meets. :data:`NO_SITE <dotagent.agent.playbooks.NO_SITE>` when they opened no page."""
    sites: Counter[str] = Counter(
        urlsplit(step.label).netloc for step in recording.steps if step.kind == "page" and urlsplit(step.label).netloc
    )
    return sites.most_common(1)[0][0] if sites else NO_SITE


class TaughtSkill(FrozenModel):
    """A skill as the review model drafted it from a recording, and as the panel shows it before the owner saves it:
    its name (the folder), the kind of work, one line of what it does, when it fits, its steps and parameters, and the
    lines the owner's own steps made."""

    name: str
    kind: str
    host: str
    description: str
    when: str
    steps: tuple[str, ...]
    parameters: tuple[str, ...]
    shown: tuple[str, ...]


async def draft_taught_skill(runtime: "Runtime", recording: Recording, words: str) -> TaughtSkill:
    """Ask the review model to write the skill of what the owner showed in ``recording``, which ``words`` says the task
    is for. Nothing is saved: the owner reads it first. A recording with no step, or a task nobody said the use of,
    is refused before the model is asked, and no secret of the key files reaches the skill
    (:func:`dotagent.core.text.redact_known`)."""
    if not recording.steps:
        raise InvalidValueError("nothing was recorded: work in the tab first, then stop", {})
    if not words.strip():
        raise InvalidValueError("say what the task is for, so that a later run knows when this skill fits", {})
    root: Path = playbooks_dir()
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, root)
    host: str = site_of(recording)
    writer: PlaybookWriter = playbook_writer_for(
        runtime.router,
        runtime.roles.review(),
        runtime.roles.after_review(),
        runtime.config.agent.model_call_timeout_s,
        f"{recording.thread_id}:teach",
        None,
    )
    written: PlaybookDraft = await writer(
        PlaybookRequest(
            goal=words.strip(),
            host=host,
            digest=recording.digest(),
            answer=_SHOWN_NOT_ANSWERED,
            kinds=kinds_shown(entries, host),
        )
    )
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    draft: PlaybookDraft = redacted_draft(written, secrets)
    return TaughtSkill(
        name=taught_name(draft.kind),
        kind=draft.kind,
        host=host,
        description=draft.description,
        when=draft.when,
        steps=tuple(draft.steps),
        parameters=tuple(draft.parameters),
        shown=tuple(step.line for step in recording.steps),
    )


def save_taught_skill(skill: TaughtSkill, written_at: float | None = None) -> str:
    """Write ``skill`` as a taught one under the playbooks and return its name: a folder of its own, its record saying
    the owner taught it (so :func:`dotagent.agent.playbooks.retires` never retires it and no run rewrites it), and its
    SKILL.md with the header of a taught skill. A skill of that kind the owner taught before is kept as it is and this
    one takes the next free name."""
    if not slug(skill.kind):
        raise InvalidValueError("a skill needs a kind of work to be named after", {"kind": skill.kind})
    root: Path = playbooks_dir()
    # The name comes from the kind, not from what the panel sent: a skill the owner taught is taught-<kind>, never one
    # of the names a learned skill has.
    name: str = free_name(root, taught_name(skill.kind))
    playbook: Playbook = Playbook(
        name=name,
        kind=skill.kind,
        host=skill.host,
        run_id="",
        agent_id=None,
        goal=skill.description,
        draft=PlaybookDraft(
            kind=skill.kind,
            description=skill.description,
            when=skill.when,
            steps=list(skill.steps),
            parameters=list(skill.parameters),
        ),
        acting_steps=len(skill.steps),
        tainted=False,
        written_at=written_at or time.time(),
        taught=True,
    )
    write_playbook(root, playbook)
    return name


# The longest name a schedule takes, as the schedule tools and the Schedules page keep it.
_SCHEDULE_NAME_CHARS: int = 80


async def repeat_taught_skill(runtime: "Runtime", thread_id: str, name: str, cron: str) -> ScheduleRecord:
    """Make the task the owner taught as ``name`` repeat on ``cron`` (item 666): a task schedule in the chat
    ``thread_id`` whose runs start with that skill picked, and with the chat's choices as a schedule a run of it makes
    has them (:func:`dotagent.tools.schedule.chat_options`), its goal the skill's line of what it does. Only a skill the
    owner taught repeats this way; a name of none is refused, and so is a rhythm the scheduler refuses."""
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
    entry: PlaybookEntry | None = next(
        (each for each in entries if each.skill.name == name and each.meta.taught and not each.retired), None
    )
    if entry is None:
        raise NotFoundError("no task you taught has that name", {"name": name})
    store = runtime.store
    latest: list[RunRecord] = await store.list_runs(1, thread_id)
    choices: ChatChoices | None = await store.chat_choices(thread_id)
    options: ScheduleOptions = (
        chat_options(latest[0], choices)
        if latest
        else ScheduleOptions(
            model=None,
            effort=None,
            speed=None,
            approvals=None,
            goal_mode=None,
            timezone=None,
            history_runs=None,
            same_chat=True,
            stop_after_failures=None,
            notify=None,
            agent_id=None,
        )
    ).model_copy(update={"skills": (entry.skill.name,)})
    request: ScheduleRequest = ScheduleRequest(
        name=plain_line(entry.skill.description, _SCHEDULE_NAME_CHARS),
        goal=entry.skill.description,
        cron=cron,
        at=None,
        mode="task",
        options=options,
    )
    return await create_schedule(
        store, runtime.config.scheduler, runtime.schedule_zone, request, time.time(), thread_id
    )

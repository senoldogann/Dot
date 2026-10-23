"""SQLite persistence (aiosqlite). One shared connection, WAL mode, FTS5 for memory search.

Reads share the connection. Every write runs alone on it (:func:`_writing`): writes wait for one another, and one that
fails or is cancelled part way is rolled back, so the next write never commits it half done. A unit of several
statements that must be stored whole, or that reads what it then writes (a thread's messages, a note added once), is one
``BEGIN IMMEDIATE`` transaction (:meth:`Store._unit`), which keeps the writes of other processes out of it too.
"""

import asyncio
import base64
import contextlib
import hashlib
import os
import re
import secrets
import sqlite3
import struct
import tempfile
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
from pathlib import Path
from typing import Concatenate, Literal

import aiosqlite

from dotagent.core.errors import InvalidValueError, NotFoundError, RunConflict, StoreError
from dotagent.core.ids import new_id
from dotagent.core.jsonutil import dumps_compact, parse_json_object, parse_json_value
from dotagent.core.logs import get_logger
from dotagent.core.text import fold_search, plain_line
from dotagent.core.types import JsonArray, JsonObject
from dotagent.llm.calls import CALL_EVENT, CallPurpose
from dotagent.llm.types import Effort, ImageData, Message, OneMessage, Speed
from dotagent.policy.types import ApprovalMode, PaceClass
from dotagent.store.endings import RESULT_RESTARTED
from dotagent.store.models import (
    NO_PICKS,
    WORKING_STATUSES,
    AgentLook,
    AgentRecord,
    ApprovalRecord,
    ApprovalStatus,
    AutoDecisionCount,
    AwakeNotes,
    Behavior,
    BrowserMode,
    CampaignEntry,
    CampaignEntryKind,
    CampaignRecord,
    ChatChoices,
    DraftChannel,
    DraftRecord,
    DraftStatus,
    EventRecord,
    HarnessExperience,
    HarnessProbe,
    HarnessState,
    HarnessStep,
    HarnessStepState,
    HarnessVersion,
    MemoryRecord,
    MemoryScore,
    MemoryUseHow,
    PushSubscription,
    QueuedMessage,
    RewindCut,
    RuleRecord,
    RunMode,
    RunPicks,
    RunRecord,
    RunStatus,
    ScheduleFiring,
    ScheduleOptions,
    ScheduleRecord,
    SentMessage,
    ShownPage,
    StepRow,
    ThreadRecord,
    ThreadSummary,
    UnattendedCount,
    UnattendedRun,
    WorkingRun,
)
from dotagent.store.stats import (
    ContextEvidence,
    ModelProfile,
    RunCosts,
    collect_run_costs,
    read_context_evidence,
    read_model_profile,
)
from dotagent.tools.team import TEAM_SOURCE

# How many help texts one SELECT asks for by text (SQLite binds up to 32,766 values a statement).
_HELP_VECTORS_ASKED: int = 500

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, pinned_at REAL, seen_at REAL, choices TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    thread_id TEXT NOT NULL, seq INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (thread_id, seq)
);
CREATE TABLE IF NOT EXISTS message_archive (
    thread_id TEXT NOT NULL, version INTEGER NOT NULL, seq INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (thread_id, version, seq)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, goal TEXT NOT NULL, mode TEXT NOT NULL,
    model TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL,
    result TEXT NOT NULL, steps INTEGER NOT NULL, input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL, parent_run_id TEXT, source TEXT NOT NULL, effort TEXT,
    approvals TEXT NOT NULL DEFAULT 'ask', goal_mode INTEGER NOT NULL DEFAULT 0, campaign_id TEXT
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at DESC);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts REAL NOT NULL,
    kind TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at REAL NOT NULL, decided_at REAL,
    status TEXT NOT NULL, tool TEXT NOT NULL, summary TEXT NOT NULL, severity TEXT NOT NULL,
    args TEXT NOT NULL, decided_by TEXT, note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS approvals_run ON approvals(run_id, status);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, channel TEXT NOT NULL, recipients TEXT NOT NULL, cc TEXT NOT NULL,
    subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, decided_at REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts(status, created_at);
CREATE TABLE IF NOT EXISTS shown_pages (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, title TEXT NOT NULL, html TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS shown_pages_run ON shown_pages(run_id);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY, position INTEGER NOT NULL, enabled INTEGER NOT NULL, behavior TEXT NOT NULL,
    tool_glob TEXT NOT NULL, match_regex TEXT NOT NULL, description TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS queued_messages (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, position INTEGER NOT NULL, text TEXT NOT NULL,
    attachments TEXT NOT NULL, mode TEXT NOT NULL, model TEXT, effort TEXT, approvals TEXT, goal_mode INTEGER,
    agent_id TEXT, source TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS queued_messages_thread ON queued_messages(thread_id, position);
CREATE TABLE IF NOT EXISTS queue_holds (thread_id TEXT PRIMARY KEY, held_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS queue_claims (
    thread_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, owner TEXT NOT NULL, claimed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS run_stops (run_id TEXT PRIMARY KEY, asked_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS run_taints (run_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sent_messages (key TEXT PRIMARY KEY, sent_at REAL NOT NULL, target TEXT);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY, content TEXT NOT NULL, tags TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, source_run TEXT,
    tainted INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    id UNINDEXED, content, tags, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS memory_vectors (id TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS help_vectors (
    model TEXT NOT NULL, text TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL, used_at REAL NOT NULL,
    PRIMARY KEY(model, text)
);
CREATE TABLE IF NOT EXISTS memory_uses (
    run_id TEXT NOT NULL, memory_id TEXT NOT NULL, handed_at REAL NOT NULL, how TEXT NOT NULL, status TEXT
);
CREATE INDEX IF NOT EXISTS memory_uses_memory ON memory_uses(memory_id);
CREATE INDEX IF NOT EXISTS memory_uses_run ON memory_uses(run_id);
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, cron TEXT, run_at REAL, goal TEXT NOT NULL,
    mode TEXT NOT NULL, enabled INTEGER NOT NULL, created_at REAL NOT NULL, last_run_at REAL,
    next_run_at REAL, thread_id TEXT, last_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL, targets TEXT NOT NULL, verification TEXT NOT NULL,
    status TEXT NOT NULL, thread_id TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, approvals TEXT NOT NULL,
    files TEXT NOT NULL, cron TEXT, next_session_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    evidence TEXT NOT NULL, browser TEXT
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, instructions TEXT NOT NULL, model TEXT NOT NULL,
    effort TEXT, approvals TEXT, tools TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS campaign_entries (
    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, run_id TEXT, ts REAL NOT NULL, kind TEXT NOT NULL,
    text TEXT NOT NULL, numbers TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS campaign_entries_ts ON campaign_entries(campaign_id, ts);
CREATE TABLE IF NOT EXISTS paced_actions (
    host TEXT NOT NULL, pace TEXT NOT NULL, ts REAL NOT NULL, run_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS paced_actions_host ON paced_actions(host, pace, ts);
CREATE TABLE IF NOT EXISTS spends (
    host TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, ts REAL NOT NULL, run_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spends_host ON spends(host, ts);
CREATE TABLE IF NOT EXISTS harness_versions (
    id TEXT PRIMARY KEY, parent_id TEXT, made_by TEXT NOT NULL, state TEXT NOT NULL, summary TEXT NOT NULL,
    habits TEXT NOT NULL, tool_guidance TEXT NOT NULL, notes TEXT NOT NULL, created_at REAL NOT NULL,
    adopted_at REAL, retired_at REAL
);
CREATE INDEX IF NOT EXISTS harness_versions_state ON harness_versions(state, created_at);
CREATE TABLE IF NOT EXISTS harness_steps (
    id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL, from_run TEXT, base_version TEXT,
    state TEXT NOT NULL, outcome TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS harness_probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, step_id TEXT NOT NULL, version_id TEXT, label TEXT NOT NULL,
    task_id TEXT NOT NULL, attempt INTEGER NOT NULL, passed INTEGER NOT NULL, status TEXT NOT NULL, cut TEXT NOT NULL,
    steps INTEGER NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, tool_errors INTEGER NOT NULL,
    seconds REAL NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS harness_probes_step ON harness_probes(step_id);
CREATE INDEX IF NOT EXISTS harness_probes_version ON harness_probes(version_id);
CREATE TABLE IF NOT EXISTS harness_experiences (
    id TEXT PRIMARY KEY, step_id TEXT NOT NULL, version_id TEXT, cue TEXT NOT NULL, lesson TEXT NOT NULL,
    evidence TEXT NOT NULL, confidence TEXT NOT NULL, created_at REAL NOT NULL
);
"""

_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


# The events that say a lesson is the owner's (item 556): ``owner_saved_memory_ids`` reads them, so a prune leaves them.
# An event stored before ``by`` was written is the owner's (``_SavedData``).
_OWNER_SAVED_LESSON: str = "kind = 'lesson_saved' AND coalesce(json_extract(data, '$.by'), 'owner') = 'owner'"


def _awake(among: AwakeNotes) -> tuple[str, tuple[object, ...]]:
    """`` AND id IN (the notes ``among`` allows)`` and what it binds: not tagged ``rest_tag`` (the tags are kept as one
    comma-separated text), not run out of time, and, when scoped, Dot's or the agent's own."""
    return (
        " AND id IN (SELECT id FROM memories WHERE (',' || tags || ',') NOT LIKE ?"
        " AND (expires_at IS NULL OR expires_at > ?) AND (? = 0 OR agent_id IS NULL OR agent_id = ?))",
        (f"%,{among.rest_tag},%", among.now, int(among.scoped), among.agent_id),
    )


# A screenshot saved this recently may belong to a message or event that is not stored yet, so deleting a chat leaves it
# for ``orphan_images`` to find later.
_RECENT_IMAGE_S: float = 60.0
# The random bytes of a shown page's id. The id is all that serves the page (``GET /pages/<id>``), so it is too
# long to guess, as the API's token is.
_PAGE_ID_BYTES: int = 18
# A chat's last line in the sidebar (item 277): how many characters it keeps, and how many of the latest run's message
# and answer are read for it (a fence or a heading at the start of an answer takes some of them).
_LAST_MESSAGE_CHARS: int = 160
_LAST_MESSAGE_READ_CHARS: int = 1000
# A run and the later runs still in its chat, oldest first: what a rewind to before the run takes out (item 282). Its
# two parameters are the run's id.
_RUNS_FROM: str = (
    "SELECT * FROM runs AS later WHERE later.thread_id = (SELECT thread_id FROM runs WHERE id = ?)"
    " AND later.parent_run_id IS NULL AND later.rewound_at IS NULL"
    " AND (later.created_at, later.rowid) >= (SELECT created_at, rowid FROM runs WHERE id = ?)"
    " ORDER BY later.created_at, later.rowid"
)


def _prepare_dirs(database: Path, image_dir: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)


def pack_vector(values: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


# A word of this many letters or more is looked up by its first letters: Turkish puts its endings after the root
# ("toplantı", "toplantıda", "toplantılar"), so the words that start alike are taken as one.
_STEM_CHARS: int = 5


def _fts_term(term: str) -> str:
    stem: str = term[:_STEM_CHARS]
    quoted: str = '"' + stem.replace('"', '""') + '"'
    return f"{quoted}*" if len(term) >= _STEM_CHARS else quoted


def fts_query(terms: Sequence[str]) -> str:
    """An OR query over ``terms`` (words folded like the indexed notes), each quoted so that it is read as plain text.
    A word of five letters or more matches every indexed word that starts with its first five letters, a shorter one
    only itself."""
    return " OR ".join(_fts_term(term) for term in dict.fromkeys(terms))


def image_file_name(image: ImageData) -> str:
    """The content-addressed file name under which an image is stored."""
    digest: str = hashlib.sha256(image.data_b64.encode("ascii")).hexdigest()[:24]
    return f"{digest}.{_EXTENSIONS.get(image.media_type, 'bin')}"


def _write_image(directory: Path, image: ImageData) -> str:
    """Store the image under its content name. A file that is already there whole is touched: its last change then
    tells when some message last wanted it, which is what keeps a delete or a sweep from removing it just as it is named
    again. Otherwise the bytes go to a file of their own that then takes the name, so a write cut short never leaves a
    part of an image under it, and a part left there before (its size is not the image's) is written again."""
    path: Path = directory / image_file_name(image)
    data: bytes = base64.b64decode(image.data_b64)
    with contextlib.suppress(FileNotFoundError):
        if path.stat().st_size == len(data):
            os.utime(path)
            return path.name
    handle, partial = tempfile.mkstemp(dir=directory, prefix=".", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        os.replace(partial, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(partial)
        raise
    return path.name


# The shape of the names made by ``image_file_name``: how a screenshot file is told from any other file in its folder
# and found in the text of a message or an event.
_IMAGE_NAME: re.Pattern[str] = re.compile(rf"\b[0-9a-f]{{24}}\.(?:{'|'.join([*_EXTENSIONS.values(), 'bin'])})\b")


def image_names_in(texts: Iterable[str]) -> set[str]:
    """The screenshot file names that the stored messages or events in ``texts`` name."""
    return {name for text in texts for name in _IMAGE_NAME.findall(text)}


def _image_files(directory: Path) -> list[tuple[Path, float]]:
    """The screenshot files in ``directory`` with their last change. Blocking: call it off the event loop."""
    return [(path, path.stat().st_mtime) for path in directory.iterdir() if _IMAGE_NAME.fullmatch(path.name)]


def unreferenced_images(
    files: Sequence[tuple[Path, float]], referenced: set[str], now: float, grace_s: float
) -> list[Path]:
    """The files that no message or event names, oldest name first, leaving out those younger than ``grace_s`` seconds:
    a screenshot just saved may not be named by its event yet."""
    return sorted(path for path, changed in files if path.name not in referenced and now - changed > grace_s)


def _remove_files(paths: Sequence[Path], unchanged_since: float) -> None:
    """Delete the files that have not changed since ``unchanged_since`` (one that did was named again meanwhile), then
    report the ones that could not be deleted. A file that is gone already is not an error. Blocking: call it off the
    event loop."""
    failed: JsonArray = []
    for path in paths:
        try:
            if path.stat().st_mtime <= unchanged_since:
                path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            failed.append(f"{path.name}: {error.strerror}")
    if failed:
        raise StoreError("some screenshot files could not be removed", {"files": failed})


def _read_image(directory: Path, media_type: str, file_name: str) -> ImageData:
    path: Path = directory / file_name
    if not path.exists():
        raise StoreError("a stored screenshot file is missing", {"file": str(path)})
    return ImageData(media_type=media_type, data_b64=base64.b64encode(path.read_bytes()).decode("ascii"))


def encode_message(message: Message, image_dir: Path) -> str:
    """Serialise a message; screenshots are written to ``image_dir`` and referenced by file name."""
    dumped: JsonObject = message.model_dump(mode="json")
    dumped["images"] = [
        {"media_type": image.media_type, "file": _write_image(image_dir, image)} for image in message.images
    ]
    return dumps_compact(dumped)


def decode_message(body: str, image_dir: Path, load_images: bool) -> Message:
    return _decoded(parse_json_object(body, "stored message"), image_dir, load_images)


def _image_refs(parsed: JsonObject) -> list[tuple[str, str]]:
    """The screenshots a stored message names: the media type and the file name of each."""
    refs: object = parsed.get("images")
    if not isinstance(refs, list):
        return []
    return [
        (str(ref["media_type"]), str(ref["file"]))
        for ref in refs
        if isinstance(ref, dict) and isinstance(ref.get("file"), str) and isinstance(ref.get("media_type"), str)
    ]


def _decoded(parsed: JsonObject, image_dir: Path, load_images: bool) -> Message:
    """The stored message ``parsed``, with its screenshots read back when ``load_images``, and without them else."""
    images: list[ImageData] = (
        [_read_image(image_dir, media_type, file) for media_type, file in _image_refs(parsed)] if load_images else []
    )
    return Message.model_validate({**parsed, "images": [image.model_dump(mode="json") for image in images]})


def _thread(row: aiosqlite.Row) -> ThreadRecord:
    return ThreadRecord(
        id=row["id"],
        title=row["title"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _summary(row: aiosqlite.Row) -> ThreadSummary:
    answer: str = plain_line(row["latest_result"], _LAST_MESSAGE_CHARS)
    return ThreadSummary(
        id=row["id"],
        title=row["title"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pinned_at=row["pinned_at"],
        run_count=row["run_count"],
        latest_run_id=row["latest_run_id"],
        latest_status=row["latest_status"],
        latest_mode=row["latest_mode"],
        latest_created_at=row["latest_created_at"],
        unread=bool(row["unread"]),
        actor_id=row["actor_id"],
        last_message=answer if answer != "" else plain_line(row["latest_goal"], _LAST_MESSAGE_CHARS),
        last_role="assistant" if answer != "" else "user",
        loop_cron=row["loop_cron"],
        loop_next_at=row["loop_next_at"],
    )


# A chat's loop (item 580): a switched-on repeating schedule of the chat ``t`` whose runs go on in it, by its own choice
# or by ``[scheduler] same_chat`` (the one parameter) when it makes none.
_CHAT_LOOP: str = "s.thread_id = t.id AND s.enabled = 1 AND s.cron IS NOT NULL AND COALESCE(s.same_chat, ?) = 1"


# The runs "while you were away" reads (item 466): of the scheduler's source, no helper's, still in their chat, ended at
# or after the moment or still working; bound to (source, since). Their names come from the schedule of their chat and
# from their campaign.
_UNATTENDED: str = (
    "runs.source = ? AND runs.parent_run_id IS NULL AND runs.rewound_at IS NULL"
    " AND (runs.finished_at >= ? OR runs.finished_at IS NULL)"
)
_UNATTENDED_JOINS: str = (
    "LEFT JOIN threads ON threads.id = runs.thread_id LEFT JOIN schedules ON schedules.id = threads.schedule_id"
    " LEFT JOIN campaigns ON campaigns.id = runs.campaign_id"
)
_UNATTENDED_NAMES: str = "schedules.name AS schedule_name, campaigns.title AS campaign_title"


def _unattended(row: aiosqlite.Row) -> UnattendedRun:
    return UnattendedRun(run=_run(row), schedule_name=row["schedule_name"], campaign_title=row["campaign_title"])


def _run(row: aiosqlite.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        thread_id=row["thread_id"],
        goal=row["goal"],
        mode=row["mode"],
        model=row["model"],
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        result=row["result"],
        steps=row["steps"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        parent_run_id=row["parent_run_id"],
        source=row["source"],
        effort=row["effort"],
        speed=row["speed"],
        once=row["once"],
        approvals=row["approvals"],
        goal_mode=bool(row["goal_mode"]),
        campaign_id=row["campaign_id"],
        agent_id=row["agent_id"],
        rewindable=row["history_version"] is not None and row["history_seq"] is not None,
    )


def _harness_version(row: aiosqlite.Row) -> HarnessVersion:
    return HarnessVersion(
        id=row["id"],
        parent_id=row["parent_id"],
        made_by=row["made_by"],
        state=row["state"],
        summary=row["summary"],
        habits=row["habits"],
        tool_guidance=row["tool_guidance"],
        notes=row["notes"],
        created_at=row["created_at"],
        adopted_at=row["adopted_at"],
        retired_at=row["retired_at"],
    )


def _harness_step(row: aiosqlite.Row) -> HarnessStep:
    return HarnessStep(
        id=row["id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        from_run=row["from_run"],
        base_version=row["base_version"],
        state=row["state"],
        outcome=row["outcome"],
        error=row["error"],
    )


def _harness_probe(row: aiosqlite.Row) -> HarnessProbe:
    return HarnessProbe(
        id=row["id"],
        step_id=row["step_id"],
        version_id=row["version_id"],
        label=row["label"],
        task_id=row["task_id"],
        attempt=row["attempt"],
        passed=bool(row["passed"]),
        status=row["status"],
        cut=row["cut"],
        steps=row["steps"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        tool_errors=row["tool_errors"],
        seconds=row["seconds"],
        created_at=row["created_at"],
    )


def _harness_experience(row: aiosqlite.Row) -> HarnessExperience:
    return HarnessExperience(
        id=row["id"],
        step_id=row["step_id"],
        version_id=row["version_id"],
        cue=row["cue"],
        lesson=row["lesson"],
        evidence=row["evidence"],
        confidence=row["confidence"],
        tainted=bool(row["tainted"]),
        created_at=row["created_at"],
    )


def _tool_names(value: object) -> tuple[str, ...]:
    """The stored tool list of an agent: a JSON array of names."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StoreError("a stored agent has a tool list that is not a list of names", {"tools": repr(value)})
    return tuple(value)


# How many of its run's own events before a step's result are searched for the call it ended (item 469): a call the gate
# stopped has its ``tool_call``, its ``policy`` event and its question's events there, and those of the read-only calls
# that ran beside it; a search with no bound would walk the whole run for a result whose call is not there.
_STEP_CALL_EVENTS: int = 50


def _step_flags(value: object) -> tuple[str, ...]:
    """The flags a step's result keeps of its action (item 468): a JSON array of names."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StoreError("a step's result has flags that are not a list of names", {"flags": repr(value)})
    return tuple(value)


def _agent(row: aiosqlite.Row) -> AgentRecord:
    return AgentRecord(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        instructions=row["instructions"],
        model=row["model"],
        effort=row["effort"],
        approvals=row["approvals"],
        tools=_tool_names(parse_json_value(row["tools"], "agent tools")),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        made_by=row["made_by"],
        look=_look_of(row),
        paused_at=row["paused_at"],
    )


def _look_of(row: aiosqlite.Row) -> AgentLook | None:
    """The look of a stored agent: ``None`` while any of its three parts is unset."""
    if row["shape"] is None or row["expression"] is None or row["colour"] is None:
        return None
    return AgentLook(shape=row["shape"], expression=row["expression"], colour=row["colour"])


def _campaign(row: aiosqlite.Row) -> CampaignRecord:
    return CampaignRecord.model_validate(
        {
            "id": row["id"],
            "title": row["title"],
            "goal": row["goal"],
            "targets": row["targets"],
            "verification": row["verification"],
            "status": row["status"],
            "thread_id": row["thread_id"],
            "model": row["model"],
            "effort": row["effort"],
            "speed": row["speed"],
            "once": row["once"],
            "approvals": row["approvals"],
            "files": parse_json_value(row["files"], "campaign files"),
            "cron": row["cron"],
            "next_session_at": row["next_session_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "evidence": row["evidence"],
            "browser": row["browser"],
        }
    )


def _campaign_entry(row: aiosqlite.Row) -> CampaignEntry:
    return CampaignEntry.model_validate(
        {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "run_id": row["run_id"],
            "ts": row["ts"],
            "kind": row["kind"],
            "text": row["text"],
            "numbers": parse_json_object(row["numbers"], "campaign entry numbers"),
        }
    )


def _event(row: aiosqlite.Row) -> EventRecord:
    return EventRecord(
        id=row["id"],
        run_id=row["run_id"],
        ts=row["ts"],
        kind=row["kind"],
        data=parse_json_object(row["data"], "event"),
    )


def _approval(row: aiosqlite.Row) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        run_id=row["run_id"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        status=row["status"],
        tool=row["tool"],
        summary=row["summary"],
        severity=row["severity"],
        args=parse_json_object(row["args"], "approval args"),
        decided_by=row["decided_by"],
        note=row["note"],
        digest=row["digest"],
        kept_until=row["kept_until"],
        permit_until=row["permit_until"],
        used_at=row["used_at"],
        scope_thread_id=row["scope_thread_id"],
        scope_campaign_id=row["scope_campaign_id"],
    )


def _addresses(value: object) -> tuple[str, ...]:
    """The stored recipients of a draft: a JSON array of addresses."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StoreError("a stored draft has recipients that are not a list of addresses", {"recipients": repr(value)})
    return tuple(value)


def _draft(row: aiosqlite.Row) -> DraftRecord:
    return DraftRecord(
        id=row["id"],
        run_id=row["run_id"],
        channel=row["channel"],
        to=_addresses(parse_json_value(row["recipients"], "draft recipients")),
        cc=_addresses(parse_json_value(row["cc"], "draft cc")),
        subject=row["subject"],
        body=row["body"],
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        error=row["error"],
    )


def _rule(row: aiosqlite.Row) -> RuleRecord:
    return RuleRecord(
        id=row["id"],
        position=row["position"],
        enabled=bool(row["enabled"]),
        behavior=row["behavior"],
        tool_glob=row["tool_glob"],
        match_regex=row["match_regex"],
        description=row["description"],
        created_at=row["created_at"],
    )


def _queued(row: aiosqlite.Row) -> QueuedMessage:
    attachments: str = row["attachments"]
    goal_mode: int | None = row["goal_mode"]
    picks: str | None = row["picks"]
    return QueuedMessage(
        id=row["id"],
        thread_id=row["thread_id"],
        position=row["position"],
        text=row["text"],
        attachments=tuple(item for item in attachments.split(",") if item),
        mode=row["mode"],
        model=row["model"],
        effort=row["effort"],
        speed=row["speed"],
        once=row["once"],
        picks=NO_PICKS if picks is None else RunPicks.model_validate_json(picks),
        approvals=row["approvals"],
        goal_mode=None if goal_mode is None else bool(goal_mode),
        agent_id=row["agent_id"],
        source=row["source"],
        created_at=row["created_at"],
    )


# The note with a text that a run of an agent (the second value; NULL for Dot) is handed: its own first, else Dot's.
_SAME_TEXT: str = (
    "SELECT * FROM memories WHERE content = ? AND (agent_id IS ? OR agent_id IS NULL) ORDER BY agent_id IS NULL LIMIT 1"
)


def _memory(row: aiosqlite.Row) -> MemoryRecord:
    tags: str = row["tags"]
    return MemoryRecord(
        id=row["id"],
        content=row["content"],
        tags=tuple(tag for tag in tags.split(",") if tag),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source_run=row["source_run"],
        agent_id=row["agent_id"],
        used_count=row["used_count"],
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        replaced_by=row["replaced_by"],
        tainted=bool(row["tainted"]),
    )


def _schedule(row: aiosqlite.Row) -> ScheduleRecord:
    return ScheduleRecord(
        id=row["id"],
        name=row["name"],
        cron=row["cron"],
        run_at=row["run_at"],
        goal=row["goal"],
        mode=row["mode"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        last_run_at=row["last_run_at"],
        next_run_at=row["next_run_at"],
        thread_id=row["thread_id"],
        last_status=row["last_status"],
        options=ScheduleOptions(
            model=row["model"],
            effort=row["effort"],
            speed=row["speed"],
            approvals=row["approvals"],
            goal_mode=None if row["goal_mode"] is None else bool(row["goal_mode"]),
            timezone=row["timezone"],
            history_runs=row["history_runs"],
            same_chat=None if row["same_chat"] is None else bool(row["same_chat"]),
            stop_after_failures=row["stop_after_failures"],
            notify=row["notify"],
            agent_id=row["agent_id"],
        ),
        runs_failed_in_row=row["runs_failed_in_row"],
    )


type _OptionValues = tuple[
    str | None,
    str | None,
    str | None,
    int | None,
    str | None,
    int | None,
    int | None,
    str | None,
    int | None,
    str | None,
    str | None,
]


def _option_values(options: ScheduleOptions) -> _OptionValues:
    """The schedule columns that hold ``options``, in the order model, effort, approvals, goal_mode, timezone,
    history_runs, same_chat, speed, stop_after_failures, notify, agent_id."""
    goal_mode: int | None = None if options.goal_mode is None else int(options.goal_mode)
    same_chat: int | None = None if options.same_chat is None else int(options.same_chat)
    return (
        options.model,
        options.effort,
        options.approvals,
        goal_mode,
        options.timezone,
        options.history_runs,
        same_chat,
        options.speed,
        options.stop_after_failures,
        options.notify,
        options.agent_id,
    )


# The kv key that names the adopted version of the harness (item 389); absent while none is adopted.
HARNESS_ADOPTED_KEY: str = "harness.adopted"

# Columns of ``runs`` added after the first version, with the definition that gives the rows of an older database the
# value those runs had in effect: no reasoning level kept, ``ask``, no goal mode, no campaign, no owner (a run of a
# process of before owners, which a daemon that starts takes for gone, as it took every run then), the standard speed,
# no choice for one message (item 251).
_ADDED_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("effort", "TEXT"),
    ("approvals", "TEXT NOT NULL DEFAULT 'ask'"),
    ("goal_mode", "INTEGER NOT NULL DEFAULT 0"),
    ("campaign_id", "TEXT"),
    ("agent_id", "TEXT"),
    ("owner", "TEXT"),
    ("speed", "TEXT NOT NULL DEFAULT 'standard'"),
    ("once", "TEXT"),
    # Where the run's first message went in its chat's history (item 282): the version of that history and the place in
    # it, NULL for a delegated run, which has a chat of its own, and for a run from before; and when a rewind took the
    # run out of its chat, NULL while it is in it.
    ("history_version", "INTEGER"),
    ("history_seq", "INTEGER"),
    ("rewound_at", "REAL"),
    # The version of the harness the run took as it started (item 389), NULL with none adopted and for runs from before.
    ("harness_version", "TEXT"),
)
# Columns of ``memories`` added later: notes of an older database were never counted as used, and are Dot's.
_ADDED_MEMORY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("used_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_used_at", "REAL"),
    ("agent_id", "TEXT"),
    # Item 306: notes held until they were changed, and none was put aside for a newer one.
    ("expires_at", "REAL"),
    ("replaced_by", "TEXT"),
    # The note was saved while its run had read content third parties can author; an older note was not.
    ("tainted", "INTEGER NOT NULL DEFAULT 0"),
)
# The status of a schedule switched off because the agent its runs ran as was deleted (item 462); switching it on
# again, as Dot or as another agent, clears it as it clears item 458's.
AGENT_DELETED_STATUS: str = "switched off: its agent was deleted"
# Columns of ``schedules`` added later: NULL means "what the configuration says when the schedule fires", which is how
# the schedules of an older database ran.
_ADDED_SCHEDULE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("model", "TEXT"),
    ("effort", "TEXT"),
    ("approvals", "TEXT"),
    ("goal_mode", "INTEGER"),
    ("timezone", "TEXT"),
    ("history_runs", "INTEGER"),
    ("same_chat", "INTEGER"),
    ("speed", "TEXT"),
    ("stop_after_failures", "INTEGER"),
    ("runs_failed_in_row", "INTEGER NOT NULL DEFAULT 0"),
    ("notify", "TEXT"),
    ("agent_id", "TEXT"),
)


# Columns of ``campaigns`` added later: NULL means the daemon's browser, which is where an older campaign's sessions
# ran; they ran at the standard speed too, and without a choice for one message.
_ADDED_CAMPAIGN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("browser", "TEXT"),
    ("speed", "TEXT NOT NULL DEFAULT 'standard'"),
    ("once", "TEXT"),
)
# Columns of ``queued_messages`` added later: an older message was sent at the standard speed, without a choice for one
# message and without picks (item 318; a ``RunPicks`` as JSON, NULL for none).
_ADDED_QUEUE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("speed", "TEXT NOT NULL DEFAULT 'standard'"),
    ("once", "TEXT"),
    ("picks", "TEXT"),
)
# Columns of ``harness_experiences`` added later: whether the run the experience was written from had read content
# third parties can author (item 392), which keeps it from the proposer.
_ADDED_HARNESS_EXPERIENCE_COLUMNS: tuple[tuple[str, str], ...] = (("tainted", "INTEGER NOT NULL DEFAULT 0"),)
# Columns of ``threads`` added later: NULL means a chat the owner has not pinned (item 130), one the owner has not seen
# since its runs began or since they marked it unread (item 131), and one without choices of its own yet (a
# ``ChatChoices`` as JSON, item 199), and one that is no chat of a schedule (a firing's chat names its schedule, item
# 257; a firing from before names none). ``history_version`` counts the times the chat's history was written anew,
# by a compaction or by a rewind to a version before (item 282).
_ADDED_THREAD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pinned_at", "REAL"),
    ("seen_at", "REAL"),
    ("choices", "TEXT"),
    ("schedule_id", "TEXT"),
    ("history_version", "INTEGER NOT NULL DEFAULT 0"),
)
# Columns of ``agents`` added later: NULL means the owner made the agent (item 99).
_ADDED_AGENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("made_by", "TEXT"),
    # The look (item 102): all three NULL for an agent that wears the fallback of its id's hue.
    ("shape", "TEXT"),
    ("expression", "TEXT"),
    ("colour", "TEXT"),
    # When the owner paused the agent (item 465); NULL while it works, as every agent did before.
    ("paused_at", "REAL"),
)
# Columns of ``approvals`` added later: a step kept for the owner (item 482) has the fingerprint of its action and when
# it lapses, the owner's yes to a kept ask (item 483) until when it holds and when it was spent, and the conversation
# and the campaign it belongs to (item 484); NULL for every other question, as for all of them before.
_ADDED_APPROVAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("digest", "TEXT"),
    ("kept_until", "REAL"),
    ("permit_until", "REAL"),
    ("used_at", "REAL"),
    ("scope_thread_id", "TEXT"),
    ("scope_campaign_id", "TEXT"),
)
# The kv key of Dot's own look (item 102), an ``AgentLook`` as JSON.
DOT_LOOK_KEY: str = "look:dot"


async def _add_columns(
    connection: aiosqlite.Connection, table: str, added: Sequence[tuple[str, str]]
) -> tuple[str, ...]:
    """Add the columns of ``added`` that ``table`` lacks; returns the names of those it added."""
    async with connection.execute(f"PRAGMA table_info({table})") as cursor:
        columns: set[str] = {str(row["name"]) for row in await cursor.fetchall()}
    missing: tuple[str, ...] = tuple(name for name, _ in added if name not in columns)
    for name, definition in added:
        if name in missing:
            await connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    return missing


async def _add_missing_columns(connection: aiosqlite.Connection) -> None:
    """Give a database created by an older version the columns added since, and the indexes on them, in one
    transaction: a process that dies part way leaves the database as it was, and the next start does it all."""
    await connection.execute("BEGIN")
    added_to_runs: tuple[str, ...] = await _add_columns(connection, "runs", _ADDED_RUN_COLUMNS)
    await _add_columns(connection, "memories", _ADDED_MEMORY_COLUMNS)
    await _add_columns(connection, "schedules", _ADDED_SCHEDULE_COLUMNS)
    await _add_columns(connection, "campaigns", _ADDED_CAMPAIGN_COLUMNS)
    await _add_columns(connection, "queued_messages", _ADDED_QUEUE_COLUMNS)
    await _add_columns(connection, "harness_experiences", _ADDED_HARNESS_EXPERIENCE_COLUMNS)
    await _add_columns(connection, "agents", _ADDED_AGENT_COLUMNS)
    if "scope_thread_id" in await _add_columns(connection, "approvals", _ADDED_APPROVAL_COLUMNS):
        # A step kept before item 484 belongs to the chat of the run that kept it.
        await connection.execute(
            "UPDATE approvals SET scope_thread_id = (SELECT thread_id FROM runs WHERE runs.id = approvals.run_id)"
            " WHERE kept_until IS NOT NULL"
        )
    if "seen_at" in await _add_columns(connection, "threads", _ADDED_THREAD_COLUMNS):
        # The chats of a store from before item 131 are taken as seen, so that none of them shows up unread at once.
        await connection.execute("UPDATE threads SET seen_at = ?", (time.time(),))
    if "speed" in added_to_runs:
        # A store from before fast mode (item 223): the choices its chats kept are those of the standard speed. Only
        # then, as an UPDATE takes the write lock even when it changes nothing, and a store opens beside other writers.
        await connection.execute(
            "UPDATE threads SET choices = json_set(choices, '$.speed', 'standard')"
            " WHERE choices IS NOT NULL AND json_type(choices, '$.speed') IS NULL"
        )
    await connection.execute("CREATE INDEX IF NOT EXISTS runs_campaign ON runs(campaign_id, created_at)")
    # The runs of a chat, of a run that delegated, of an agent; the approvals waiting, newest first.
    await connection.execute("CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id, created_at)")
    await connection.execute("CREATE INDEX IF NOT EXISTS runs_parent ON runs(parent_run_id, created_at)")
    await connection.execute("CREATE INDEX IF NOT EXISTS runs_agent ON runs(agent_id, created_at)")
    # The chats of a schedule, whose runs its next firing reads (item 257).
    await connection.execute("CREATE INDEX IF NOT EXISTS threads_schedule ON threads(schedule_id)")
    await connection.execute("CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status, created_at)")
    # The steps kept for the owner: the same one once in a chat, and the ones whose time is over (item 482).
    await connection.execute("CREATE INDEX IF NOT EXISTS approvals_kept ON approvals(digest, status)")
    await connection.commit()


async def _refresh_memory_index(connection: aiosqlite.Connection) -> None:
    """Make the search index of the notes say what the notes say, in the folded form the queries use, and write it
    again whole when it does not (an index made by an older version, or by a process that did not fold yet). The index
    only holds a copy of the notes, so nothing a person wrote is touched. One transaction that holds the write lock
    from its start: a note another process (the daemon, ``dotx run``) adds meanwhile is in what is read, or is added
    after the index is written, never dropped from it."""
    await connection.execute("BEGIN IMMEDIATE")
    try:
        async with connection.execute("SELECT id, content, tags FROM memories") as cursor:
            wanted: list[tuple[str, str, str]] = sorted(
                (str(note["id"]), fold_search(str(note["content"])), fold_search(str(note["tags"])))
                for note in await cursor.fetchall()
            )
        async with connection.execute("SELECT id, content, tags FROM memories_fts") as cursor:
            indexed: list[tuple[str, str, str]] = sorted(
                (str(row["id"]), str(row["content"]), str(row["tags"])) for row in await cursor.fetchall()
            )
        if indexed != wanted:
            await connection.execute("DELETE FROM memories_fts")
            await connection.executemany("INSERT INTO memories_fts(id, content, tags) VALUES(?, ?, ?)", wanted)
    except BaseException:
        await connection.rollback()
        raise
    await connection.commit()


log = get_logger(__name__)

# A write that finds the database locked (another process writes: dotx prune, a tool of the owner's) is tried again
# after these pauses, each try logged; then the error is raised as a StoreError, as any other refusal of a write is at
# once. The daemon's own writes never lock each other out: they all go through one connection, one at a time
# (``_writing``).
_LOCKED_RETRY_DELAYS_S: tuple[float, ...] = (0.2, 0.5, 1.0, 2.0)


def _is_locked(error: sqlite3.Error) -> bool:
    text: str = str(error).lower()
    return isinstance(error, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


def _refused(method: str, error: sqlite3.Error) -> StoreError:
    """The error of a write the database refused for a reason other than a lock: a full disk, a file it cannot
    write, a constraint."""
    return StoreError("the database refused the write", {"method": method, "error": str(error)})


async def _whole[R](store: "Store", write: Awaitable[R]) -> R:
    """What ``write`` returns. A write that fails or is cancelled part way is rolled back before its error goes on, so
    no part of it waits in an open transaction for the next writer's commit."""
    try:
        return await write
    except BaseException:
        await store._db.rollback()
        raise


def _writing[**P, R](
    method: Callable[Concatenate["Store", P], Awaitable[R]],
) -> Callable[Concatenate["Store", P], Coroutine[object, object, R]]:
    """A method that writes: it runs alone on the connection, so a unit of several statements is committed whole and
    never shares a transaction with another writer, and one that fails or is cancelled part way is rolled back; a lock
    held by another process is waited out with retries. A write the database refuses is a ``StoreError``, as every
    failure of the store is, so that what handles Dot's errors handles it."""

    async def run(self: "Store", /, *args: P.args, **kwargs: P.kwargs) -> R:
        async with self._write_lock:
            for attempt, delay in enumerate(_LOCKED_RETRY_DELAYS_S, start=1):
                try:
                    return await _whole(self, method(self, *args, **kwargs))
                except sqlite3.Error as error:
                    if not _is_locked(error):
                        raise _refused(method.__name__, error) from error
                    log.warning(
                        "store_locked_retry", method=method.__name__, attempt=attempt, delay_s=delay, error=str(error)
                    )
                    await asyncio.sleep(delay)
            try:
                return await _whole(self, method(self, *args, **kwargs))
            except sqlite3.Error as error:
                if not _is_locked(error):
                    raise _refused(method.__name__, error) from error
                raise StoreError(
                    "the database stayed locked by another process",
                    {"method": method.__name__, "tries": len(_LOCKED_RETRY_DELAYS_S) + 1, "error": str(error)},
                ) from error

    return run


class Store:
    """Connector to the SQLite database."""

    def __init__(self, connection: aiosqlite.Connection, database: Path, image_dir: Path) -> None:
        self._db: aiosqlite.Connection = connection
        self._database: Path = database
        self._image_dir: Path = image_dir
        # The one door for writes (``_writing``): the daemon's writers take turns on the one connection.
        self._write_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path, image_dir: Path) -> "Store":
        await asyncio.to_thread(_prepare_dirs, path, image_dir)
        connection: aiosqlite.Connection = await aiosqlite.connect(path)
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.execute("PRAGMA synchronous=NORMAL")
            await connection.execute("PRAGMA busy_timeout=10000")
            await connection.executescript(_SCHEMA)
            await _add_missing_columns(connection)
            await _refresh_memory_index(connection)
        except BaseException as error:
            # The connection's worker thread is not a daemon: left running, it keeps the process alive after the error
            # (item 556), and a daemon that never exits is never started again by launchd.
            with contextlib.suppress(Exception):
                await connection.close()
            if isinstance(error, sqlite3.Error):
                raise StoreError(
                    "the database is locked by another process"
                    if _is_locked(error)
                    else "the database cannot be opened",
                    {"path": str(path), "error": str(error)},
                ) from error
            raise
        return cls(connection, path, image_dir)

    async def close(self) -> None:
        await self._db.close()

    async def _one(self, sql: str, params: Sequence[object]) -> aiosqlite.Row | None:
        async with self._db.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _all(self, sql: str, params: Sequence[object]) -> list[aiosqlite.Row]:
        async with self._db.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    # ----- key/value -----------------------------------------------------------------------

    async def kv_get(self, key: str) -> str | None:
        row: aiosqlite.Row | None = await self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return None if row is None else str(row["value"])

    @_writing
    async def kv_set(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._db.commit()

    @_writing
    async def kv_update(self, key: str, update: Callable[[str | None], str]) -> str:
        """Set ``key`` to what ``update`` makes of its value (``None`` while it has none), read and written in one write
        that no other one interleaves; returns the value set."""
        row: aiosqlite.Row | None = await self._one("SELECT value FROM kv WHERE key = ?", (key,))
        value: str = update(None if row is None else str(row["value"]))
        await self._db.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._db.commit()
        return value

    # ----- harness -------------------------------------------------------------------------

    @_writing
    async def create_harness_version(
        self, parent_id: str | None, made_by: str, summary: str, habits: str, tool_guidance: str, notes: str
    ) -> HarnessVersion:
        """Store a new version of the harness (item 389) as a candidate."""
        record: HarnessVersion = HarnessVersion(
            id=new_id("hrn"),
            parent_id=parent_id,
            made_by=made_by,
            state="candidate",
            summary=summary,
            habits=habits,
            tool_guidance=tool_guidance,
            notes=notes,
            created_at=time.time(),
            adopted_at=None,
            retired_at=None,
        )
        await self._db.execute(
            "INSERT INTO harness_versions(id, parent_id, made_by, state, summary, habits, tool_guidance, notes,"
            " created_at, adopted_at, retired_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.parent_id,
                record.made_by,
                record.state,
                record.summary,
                record.habits,
                record.tool_guidance,
                record.notes,
                record.created_at,
                record.adopted_at,
                record.retired_at,
            ),
        )
        await self._db.commit()
        return record

    async def get_harness_version(self, version_id: str) -> HarnessVersion:
        row: aiosqlite.Row | None = await self._one("SELECT * FROM harness_versions WHERE id = ?", (version_id,))
        if row is None:
            raise NotFoundError("harness version not found", {"version_id": version_id})
        return _harness_version(row)

    async def list_harness_versions(self) -> list[HarnessVersion]:
        """Every version of the harness, the newest first."""
        rows = await self._all("SELECT * FROM harness_versions ORDER BY created_at DESC, id DESC", ())
        return [_harness_version(row) for row in rows]

    async def adopted_harness_id(self) -> str | None:
        """The id of the adopted version, or ``None`` while a run's prompt is Dot's own text alone."""
        return await self.kv_get(HARNESS_ADOPTED_KEY)

    async def adopt_harness(self, version_id: str, now: float) -> HarnessVersion:
        """Make ``version_id`` the version every run takes from now on, and retire the one adopted before; one unit, so
        that no run reads a moment with two adopted or none."""

        async def statements() -> HarnessVersion:
            row: aiosqlite.Row | None = await self._one("SELECT * FROM harness_versions WHERE id = ?", (version_id,))
            if row is None:
                raise NotFoundError("harness version not found", {"version_id": version_id})
            await self._db.execute(
                "UPDATE harness_versions SET state = 'retired', retired_at = ? WHERE state = 'adopted' AND id != ?",
                (now, version_id),
            )
            await self._db.execute(
                "UPDATE harness_versions SET state = 'adopted', adopted_at = ?, retired_at = NULL WHERE id = ?",
                (now, version_id),
            )
            await self._db.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (HARNESS_ADOPTED_KEY, version_id),
            )
            adopted: aiosqlite.Row | None = await self._one(
                "SELECT * FROM harness_versions WHERE id = ?", (version_id,)
            )
            assert adopted is not None
            return _harness_version(adopted)

        return await self._unit(statements)

    async def retire_harness(self, version_id: str, now: float) -> HarnessVersion:
        """Put ``version_id`` aside; when it was the adopted one, a run's prompt is Dot's own text alone from now on."""

        async def statements() -> HarnessVersion:
            row: aiosqlite.Row | None = await self._one("SELECT * FROM harness_versions WHERE id = ?", (version_id,))
            if row is None:
                raise NotFoundError("harness version not found", {"version_id": version_id})
            if row["state"] != "retired":
                await self._db.execute(
                    "UPDATE harness_versions SET state = 'retired', retired_at = ? WHERE id = ?", (now, version_id)
                )
            await self._db.execute("DELETE FROM kv WHERE key = ? AND value = ?", (HARNESS_ADOPTED_KEY, version_id))
            retired: aiosqlite.Row | None = await self._one(
                "SELECT * FROM harness_versions WHERE id = ?", (version_id,)
            )
            assert retired is not None
            return _harness_version(retired)

        return await self._unit(statements)

    @_writing
    async def set_run_harness(self, run_id: str, version_id: str) -> None:
        """Record the version of the harness the run ``run_id`` took as it started."""
        await self._db.execute("UPDATE runs SET harness_version = ? WHERE id = ?", (version_id, run_id))
        await self._db.commit()

    @_writing
    async def set_harness_state(self, version_id: str, state: HarnessState) -> HarnessVersion:
        """Mark a version ``probed`` or ``candidate``; adopting and retiring are :meth:`adopt_harness`'s and
        :meth:`retire_harness`'s, which keep the adopted key with the state."""
        if state in ("adopted", "retired"):
            raise InvalidValueError("adopt_harness and retire_harness set these states", {"state": state})
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE harness_versions SET state = ? WHERE id = ?", (state, version_id)
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("harness version not found", {"version_id": version_id})
        return await self.get_harness_version(version_id)

    @_writing
    async def set_harness_notes(self, version_id: str, notes: str) -> None:
        """Replace a version's notes: what a probe showed of it, the decisions behind it."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE harness_versions SET notes = ? WHERE id = ?", (notes, version_id)
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("harness version not found", {"version_id": version_id})

    @_writing
    async def create_harness_step(
        self, started_at: float, from_run: str | None, base_version: str | None
    ) -> HarnessStep:
        """Store the start of a step of the improvement loop (item 391), as failed until :meth:`finish_harness_step`
        says how it ended: a step the daemon's stop cut off reads so."""
        step: HarnessStep = HarnessStep(
            id=new_id("hst"),
            started_at=started_at,
            ended_at=None,
            from_run=from_run,
            base_version=base_version,
            state="failed",
            outcome="{}",
            error="the step did not end",
        )
        await self._db.execute(
            "INSERT INTO harness_steps(id, started_at, ended_at, from_run, base_version, state, outcome, error)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (step.id, step.started_at, None, step.from_run, step.base_version, step.state, step.outcome, step.error),
        )
        await self._db.commit()
        return step

    @_writing
    async def finish_harness_step(
        self, step_id: str, ended_at: float, state: HarnessStepState, outcome: str, error: str | None
    ) -> HarnessStep:
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE harness_steps SET ended_at = ?, state = ?, outcome = ?, error = ? WHERE id = ?",
            (ended_at, state, outcome, error, step_id),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("harness step not found", {"step_id": step_id})
        return await self.get_harness_step(step_id)

    async def get_harness_step(self, step_id: str) -> HarnessStep:
        row: aiosqlite.Row | None = await self._one("SELECT * FROM harness_steps WHERE id = ?", (step_id,))
        if row is None:
            raise NotFoundError("harness step not found", {"step_id": step_id})
        return _harness_step(row)

    async def list_harness_steps(self, limit: int) -> list[HarnessStep]:
        """The newest ``limit`` steps, the newest first."""
        rows = await self._all("SELECT * FROM harness_steps ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))
        return [_harness_step(row) for row in rows]

    async def count_harness_steps(self) -> int:
        """How many steps there are in all, nightly and by hand (item 527), past the newest a list shows."""
        row = await self._one("SELECT COUNT(*) AS n FROM harness_steps", ())
        return 0 if row is None else int(row["n"])

    @_writing
    async def add_harness_probes(
        self, step_id: str, version_id: str | None, label: str, cards: Sequence[tuple[object, ...]], created_at: float
    ) -> None:
        """Keep the attempts of a probe: each of ``cards`` is ``(task_id, attempt, passed, status, cut, steps,
        input_tokens, output_tokens, tool_errors, seconds)``."""
        await self._db.executemany(
            "INSERT INTO harness_probes(step_id, version_id, label, task_id, attempt, passed, status, cut, steps,"
            " input_tokens, output_tokens, tool_errors, seconds, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(step_id, version_id, label, *card, created_at) for card in cards],
        )
        await self._db.commit()

    async def harness_probes_of(self, step_id: str) -> list[HarnessProbe]:
        rows = await self._all("SELECT * FROM harness_probes WHERE step_id = ? ORDER BY id", (step_id,))
        return [_harness_probe(row) for row in rows]

    async def harness_probes_of_version(self, version_id: str) -> list[HarnessProbe]:
        rows = await self._all("SELECT * FROM harness_probes WHERE version_id = ? ORDER BY id", (version_id,))
        return [_harness_probe(row) for row in rows]

    @_writing
    async def add_harness_experience(
        self,
        step_id: str,
        version_id: str | None,
        cue: str,
        lesson: str,
        evidence: str,
        confidence: str,
        tainted: bool,
    ) -> HarnessExperience:
        """Keep an experience of the loop (item 391); ``tainted`` says the run it was written from had read content
        third parties can author (item 392), which keeps it from the proposer."""
        record: HarnessExperience = HarnessExperience(
            id=new_id("hxp"),
            step_id=step_id,
            version_id=version_id,
            cue=cue,
            lesson=lesson,
            evidence=evidence,
            confidence=confidence,
            created_at=time.time(),
            tainted=tainted,
        )
        await self._db.execute(
            "INSERT INTO harness_experiences(id, step_id, version_id, cue, lesson, evidence, confidence, created_at,"
            " tainted) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.step_id,
                record.version_id,
                record.cue,
                record.lesson,
                record.evidence,
                record.confidence,
                record.created_at,
                int(record.tainted),
            ),
        )
        await self._db.commit()
        return record

    async def harness_steps_of_version(self, version_id: str) -> list[HarnessStep]:
        """The steps that touched a version: as the base of their probes, or named in what they did (a candidate, the
        probed version), the newest first."""
        rows = await self._all(
            "SELECT * FROM harness_steps WHERE base_version = ? OR json_extract(outcome, '$.probed') = ?"
            " OR EXISTS (SELECT 1 FROM json_each(outcome, '$.candidates') WHERE value = ?)"
            " ORDER BY started_at DESC, id DESC",
            (version_id, version_id, version_id),
        )
        return [_harness_step(row) for row in rows]

    async def harness_experiences_of_version(self, version_id: str) -> list[HarnessExperience]:
        rows = await self._all(
            "SELECT * FROM harness_experiences WHERE version_id = ? ORDER BY created_at DESC, id DESC", (version_id,)
        )
        return [_harness_experience(row) for row in rows]

    async def list_harness_experiences(self, limit: int) -> list[HarnessExperience]:
        """The newest ``limit`` experiences, the newest first."""
        rows = await self._all("SELECT * FROM harness_experiences ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [_harness_experience(row) for row in rows]

    async def count_harness_experiences(self) -> int:
        """How many experiences the library keeps in all (item 527), past the newest a list shows."""
        row = await self._one("SELECT COUNT(*) AS n FROM harness_experiences", ())
        return 0 if row is None else int(row["n"])

    async def run_harness(self, run_id: str) -> str | None:
        """The version of the harness the run took, ``None`` with none adopted then or for a run from before."""
        row: aiosqlite.Row | None = await self._one("SELECT harness_version FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise NotFoundError("run not found", {"run_id": run_id})
        return None if row["harness_version"] is None else str(row["harness_version"])

    # ----- threads and messages ------------------------------------------------------------

    @_writing
    async def create_thread(self, title: str, source: str) -> ThreadRecord:
        now: float = time.time()
        record: ThreadRecord = ThreadRecord(
            id=new_id("thr"), title=title, source=source, created_at=now, updated_at=now
        )
        await self._db.execute(
            "INSERT INTO threads(id, title, source, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            (record.id, record.title, record.source, record.created_at, record.updated_at),
        )
        await self._db.commit()
        return record

    async def get_thread(self, thread_id: str) -> ThreadRecord:
        row: aiosqlite.Row | None = await self._one("SELECT * FROM threads WHERE id = ?", (thread_id,))
        if row is None:
            raise NotFoundError("thread not found", {"thread_id": thread_id})
        return _thread(row)

    async def list_threads(self, limit: int) -> list[ThreadRecord]:
        rows: list[aiosqlite.Row] = await self._all("SELECT * FROM threads ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [_thread(row) for row in rows]

    @_writing
    async def rename_thread(self, thread_id: str, title: str) -> None:
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE threads SET title = ? WHERE id = ?", (title, thread_id)
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("thread not found", {"thread_id": thread_id})

    @_writing
    async def pin_thread(self, thread_id: str, pinned: bool) -> None:
        """Pin the chat now, which puts it first among the pinned ones, or unpin it (item 130)."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE threads SET pinned_at = ? WHERE id = ?", (time.time() if pinned else None, thread_id)
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("thread not found", {"thread_id": thread_id})

    @_writing
    async def see_thread(self, thread_id: str, seen: bool) -> None:
        """Mark the chat seen now, or unseen, which makes it unread once its latest run has ended (item 131)."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE threads SET seen_at = ? WHERE id = ?", (time.time() if seen else None, thread_id)
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("thread not found", {"thread_id": thread_id})

    async def chat_choices(self, thread_id: str) -> ChatChoices | None:
        """What the chat's next follow-up starts with (item 199), ``None`` while it has no choices of its own: its
        runs began before chats kept them."""
        row: aiosqlite.Row | None = await self._one("SELECT choices FROM threads WHERE id = ?", (thread_id,))
        if row is None:
            raise NotFoundError("thread not found", {"thread_id": thread_id})
        stored: str | None = row["choices"]
        return None if stored is None else ChatChoices.model_validate_json(stored)

    @_writing
    async def set_chat_choices(self, thread_id: str, choices: ChatChoices) -> None:
        """Keep what the owner picked in the chat's composer for its next follow-up (item 199)."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE threads SET choices = ? WHERE id = ?", (choices.model_dump_json(), thread_id)
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("thread not found", {"thread_id": thread_id})

    @_writing
    async def keep_first_chat_choices(self, thread_id: str, choices: ChatChoices) -> None:
        """Keep what a run of the chat starts with as the chat's choices when it has none yet (item 199): those of its
        first run, or of the next run of a chat from before."""
        await self._db.execute(
            "UPDATE threads SET choices = ? WHERE id = ? AND choices IS NULL", (choices.model_dump_json(), thread_id)
        )
        await self._db.commit()

    async def list_thread_summaries(self, limit: int, same_chat: bool = False) -> list[ThreadSummary]:
        """The chats, the pinned ones first (the last pinned first, so they are listed however old), then the latest
        activity first: every thread with a run a person started on it, with the latest of those runs. Threads holding
        only delegated runs are left out, and so are the threads of a campaign's sessions after the first, which start
        in threads of their own: the campaign's page lists them, and the campaign's conversation stays a chat. The
        runs a rewind took out of their chat (item 282) are not counted. Each chat says its loop (item 580), the
        schedules that make none of their own going on in their chat when ``same_chat`` (``[scheduler] same_chat``)."""
        rows: list[aiosqlite.Row] = await self._all(
            "WITH ranked AS ("
            " SELECT thread_id, id, status, mode, created_at, finished_at,"
            "  ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY created_at DESC, rowid DESC) AS place,"
            "  COUNT(*) OVER (PARTITION BY thread_id) AS run_count"
            " FROM runs WHERE parent_run_id IS NULL AND rewound_at IS NULL"
            " AND (campaign_id IS NULL OR thread_id IN (SELECT thread_id FROM campaigns)))"
            " SELECT t.id, t.title, t.source, t.created_at, MAX(t.updated_at, r.created_at) AS updated_at,"
            "  t.pinned_at, r.run_count, r.id AS latest_run_id, r.status AS latest_status, r.mode AS latest_mode,"
            "  r.created_at AS latest_created_at,"
            "  r.finished_at IS NOT NULL AND (t.seen_at IS NULL OR r.finished_at > t.seen_at) AS unread,"
            "  COALESCE(latest.agent_id, latest.campaign_id) AS actor_id,"
            "  substr(latest.goal, 1, ?) AS latest_goal, substr(latest.result, 1, ?) AS latest_result,"
            f"  (SELECT s.cron FROM schedules AS s WHERE {_CHAT_LOOP}"
            "   ORDER BY s.next_run_at IS NULL, s.next_run_at LIMIT 1) AS loop_cron,"
            f"  (SELECT MIN(s.next_run_at) FROM schedules AS s WHERE {_CHAT_LOOP}) AS loop_next_at"
            " FROM threads AS t JOIN ranked AS r ON r.thread_id = t.id AND r.place = 1"
            " JOIN runs AS latest ON latest.id = r.id"
            " ORDER BY t.pinned_at IS NULL, t.pinned_at DESC, updated_at DESC LIMIT ?",
            (_LAST_MESSAGE_READ_CHARS, _LAST_MESSAGE_READ_CHARS, int(same_chat), int(same_chat), limit),
        )
        return [_summary(row) for row in rows]

    @_writing
    async def delete_threads(self, thread_ids: Sequence[str]) -> list[str]:
        """Delete chats with everything they hold, in one transaction: their messages (with what was kept aside of their
        history) and queue, their runs, the runs
        those delegated to (recursively) with the events, approvals and shown pages of all of them, and the threads of
        the delegated runs, with their messages and any queue, unless a run of their own remains there. Returns the ids
        that named a thread, in the order given; the others are skipped. A schedule keeps the id of the thread it last
        fired into and makes a new one per firing, so it is left alone. Nothing is deleted while a run of any process
        works on one of the chats, itself or through a run it delegated to: ``RunConflict`` names the chats and the
        runs. The check is made in the transaction of the deletes, which holds the write lock from its start."""
        named: tuple[str, ...] = tuple(thread_ids)
        await self._db.execute("BEGIN IMMEDIATE")
        await self._refuse_working_chats(named, "chats with a run under way cannot be deleted")
        found: list[aiosqlite.Row] = await self._all(
            f"SELECT id FROM threads WHERE id IN ({','.join('?' * len(named))})", named
        )
        existing: set[str] = {str(row["id"]) for row in found}
        candidates: set[str] = await self._delete_chats(named)
        await self._db.commit()
        await self._release_images(candidates)
        await self._return_free_pages()
        return [thread_id for thread_id in named if thread_id in existing]

    async def _delete_chats(self, named: tuple[str, ...]) -> set[str]:
        """Delete the chats ``named`` with everything they hold, as :meth:`delete_threads` says, in the transaction its
        caller has begun and commits; the screenshot files that what it deleted named, for ``_release_images`` once
        that transaction is committed."""
        marks: str = ",".join("?" * len(named))
        doomed: str = (
            "WITH RECURSIVE doomed(id) AS ("
            f" SELECT id FROM runs WHERE thread_id IN ({marks})"
            " UNION SELECT runs.id FROM runs JOIN doomed ON runs.parent_run_id = doomed.id)"
        )
        hosts: list[aiosqlite.Row] = await self._all(
            f"{doomed} SELECT DISTINCT thread_id FROM runs"
            f" WHERE id IN (SELECT id FROM doomed) AND thread_id NOT IN ({marks})",
            named + named,
        )
        delegated: tuple[str, ...] = tuple(str(row["thread_id"]) for row in hosts)
        candidates: set[str] = await self._screenshots_of(doomed, named, delegated)
        await self._db.execute(f"{doomed} DELETE FROM events WHERE run_id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"{doomed} DELETE FROM approvals WHERE run_id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"{doomed} DELETE FROM shown_pages WHERE run_id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"{doomed} DELETE FROM drafts WHERE run_id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"{doomed} DELETE FROM run_taints WHERE run_id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"{doomed} DELETE FROM runs WHERE id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"DELETE FROM messages WHERE thread_id IN ({marks})", named)
        await self._db.execute(f"DELETE FROM message_archive WHERE thread_id IN ({marks})", named)
        await self._db.execute(f"DELETE FROM queued_messages WHERE thread_id IN ({marks})", named)
        await self._db.execute(f"DELETE FROM queue_holds WHERE thread_id IN ({marks})", named)
        await self._db.execute(f"DELETE FROM threads WHERE id IN ({marks})", named)
        if delegated:
            host_marks: str = ",".join("?" * len(delegated))
            await self._db.execute(
                f"DELETE FROM messages WHERE thread_id IN ({host_marks})"
                " AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.thread_id = messages.thread_id)",
                delegated,
            )
            for table in ("queued_messages", "queue_holds"):
                await self._db.execute(
                    f"DELETE FROM {table} WHERE thread_id IN ({host_marks})"
                    f" AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.thread_id = {table}.thread_id)",
                    delegated,
                )
            await self._db.execute(
                f"DELETE FROM threads WHERE id IN ({host_marks})"
                " AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.thread_id = threads.id)",
                delegated,
            )
        return candidates

    async def _refuse_working_chats(self, named: tuple[str, ...], refusal: str) -> None:
        """Raise ``RunConflict`` with the message ``refusal`` when a run of any process works on one of the chats
        ``named``, itself or through a run it delegated to, or when a queued message of one is starting as its next run
        (:meth:`claim_queued`); the chats it names are those given."""
        working: tuple[RunStatus, ...] = tuple(sorted(WORKING_STATUSES))
        rows: list[aiosqlite.Row] = await self._all(
            "WITH RECURSIVE doomed(id, chat) AS ("
            f" SELECT id, thread_id FROM runs WHERE thread_id IN ({','.join('?' * len(named))})"
            " UNION SELECT runs.id, doomed.chat FROM runs JOIN doomed ON runs.parent_run_id = doomed.id)"
            " SELECT doomed.chat AS chat, runs.id AS run FROM runs JOIN doomed ON runs.id = doomed.id"
            f" WHERE runs.status IN ({','.join('?' * len(working))}) ORDER BY chat, run",
            (*named, *working),
        )
        starting: list[aiosqlite.Row] = await self._all(
            f"SELECT thread_id FROM queue_claims WHERE thread_id IN ({','.join('?' * len(named))}) ORDER BY thread_id",
            named,
        )
        if rows or starting:
            chats: JsonArray = list(
                dict.fromkeys([*(str(row["chat"]) for row in rows), *(str(row["thread_id"]) for row in starting)])
            )
            runs: JsonArray = list(dict.fromkeys(str(row["run"]) for row in rows))
            raise RunConflict(refusal, {"thread_ids": chats, "run_ids": runs})

    async def _screenshots_of(self, doomed: str, named: tuple[str, ...], delegated: tuple[str, ...]) -> set[str]:
        """The screenshot files named by what ``delete_threads`` is about to delete: the events of the doomed runs, the
        messages of the named threads and of the threads of delegated runs, and what the named threads kept aside."""
        threads: tuple[str, ...] = named + delegated
        rows: list[aiosqlite.Row] = await self._all(
            f"{doomed} SELECT data AS text FROM events WHERE run_id IN (SELECT id FROM doomed)"
            f" UNION ALL SELECT body FROM messages WHERE thread_id IN ({','.join('?' * len(threads))})"
            f" UNION ALL SELECT body FROM message_archive WHERE thread_id IN ({','.join('?' * len(named))})",
            named + threads + named,
        )
        return await asyncio.to_thread(image_names_in, [str(row["text"]) for row in rows])

    async def _release_images(self, candidates: set[str]) -> None:
        """Remove those of ``candidates`` that no stored message or event names any more: a file can be shared, because
        a screenshot is stored under the hash of its content."""
        if not candidates:
            return
        still_named: set[str] = await self.referenced_images()
        await self.remove_images(
            [self._image_dir / name for name in sorted(candidates - still_named)], time.time() - _RECENT_IMAGE_S
        )

    async def _return_free_pages(self) -> None:
        """Hand the pages that a delete freed back to the disk. Does nothing until ``compact`` has switched the
        database file to incremental vacuuming."""
        async with self._db.execute("PRAGMA incremental_vacuum") as cursor:
            await cursor.fetchall()

    async def referenced_images(self) -> set[str]:
        """The screenshot files that a stored message (one kept aside too) or event names."""
        rows: list[aiosqlite.Row] = await self._all(
            "SELECT body AS text FROM messages UNION ALL SELECT body FROM message_archive UNION ALL SELECT data FROM "
            "events",
            (),
        )
        return await asyncio.to_thread(image_names_in, [str(row["text"]) for row in rows])

    async def events_of_runs_ended_before(self, before: float) -> tuple[int, int]:
        """How many runs that ended before ``before`` still have events a prune deletes, and how many events they have
        (:meth:`delete_events_of_runs_ended_before`)."""
        row = await self._one(
            "SELECT COUNT(DISTINCT run_id) AS runs, COUNT(*) AS events FROM events WHERE run_id IN"
            f" (SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?) AND NOT ({_OWNER_SAVED_LESSON})",
            (before,),
        )
        return (0, 0) if row is None else (int(row["runs"]), int(row["events"]))

    @_writing
    async def delete_events_of_runs_ended_before(self, before: float, taint_kinds: Sequence[str]) -> int:
        """Delete the events of the runs that ended before ``before``, and the pages they showed and the drafts they
        wrote, which the conversation showed; the runs stay. Returns how many events went. The screenshots those events
        named become orphans for :meth:`orphan_images`.

        Two things the events said outlive them (item 556), as nothing else records them: that a run ended tainted,
        which decides how a run that resumes it or builds on it starts (:meth:`pruned_run_tainted`), read from the
        newest of its events of ``taint_kinds`` as :func:`dotagent.agent.gate.tainted_after` reads them; and that the
        owner saved a lesson from a run, which keeps the nightly reflection from folding or resting it
        (:meth:`owner_saved_memory_ids`): those events stay."""
        ended: str = "SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?"
        await self._db.execute(
            "INSERT OR IGNORE INTO run_taints(run_id) SELECT e.run_id FROM events AS e WHERE e.id IN"
            f" (SELECT MAX(id) FROM events WHERE kind IN ({','.join('?' * len(taint_kinds))})"
            f" AND run_id IN ({ended}) GROUP BY run_id)"
            " AND json_type(e.data, '$.tainted') = 'true'"
            " AND (e.kind != 'steer' OR json_type(e.data, '$.vouches') = 'false')",
            (*taint_kinds, before),
        )
        cursor = await self._db.execute(
            f"DELETE FROM events WHERE run_id IN ({ended}) AND NOT ({_OWNER_SAVED_LESSON})", (before,)
        )
        await self._db.execute(f"DELETE FROM shown_pages WHERE run_id IN ({ended})", (before,))
        await self._db.execute(f"DELETE FROM drafts WHERE run_id IN ({ended})", (before,))
        await self._db.commit()
        return cursor.rowcount

    async def pruned_run_tainted(self, run_id: str) -> bool:
        """Whether the run ``run_id`` ended tainted, as :meth:`delete_events_of_runs_ended_before` recorded it before it
        deleted the events that said so (item 556); False for a run it recorded nothing of."""
        return await self._one("SELECT 1 FROM run_taints WHERE run_id = ?", (run_id,)) is not None

    async def archived_messages(self) -> tuple[int, int]:
        """How many messages the chats keep aside in earlier versions of their history (:meth:`replace_messages`), and
        their size in bytes. Nothing deletes them but the deletion or the rewinding of their chat, and the screenshots
        they name stay with them (item 556)."""
        row = await self._one(
            "SELECT COUNT(*) AS messages, COALESCE(SUM(LENGTH(body)), 0) AS bytes FROM message_archive", ()
        )
        return (0, 0) if row is None else (int(row["messages"]), int(row["bytes"]))

    async def orphan_images(self, now: float, grace_s: float) -> list[Path]:
        """The screenshot files that nothing names: what chats deleted before their files went with them left behind.
        Files younger than ``grace_s`` seconds are left out."""
        files: list[tuple[Path, float]] = await asyncio.to_thread(_image_files, self._image_dir)
        return unreferenced_images(files, await self.referenced_images(), now, grace_s)

    async def remove_images(self, paths: Sequence[Path], unchanged_since: float) -> None:
        """Delete the screenshot files among ``paths`` that no message has saved again since ``unchanged_since``."""
        await asyncio.to_thread(_remove_files, paths, unchanged_since)

    async def reclaimable_bytes(self) -> int:
        """The size of the free pages inside the database file: what ``compact`` gives back to the disk."""
        free: list[aiosqlite.Row] = await self._all("PRAGMA freelist_count", ())
        size: list[aiosqlite.Row] = await self._all("PRAGMA page_size", ())
        return int(free[0][0]) * int(size[0][0])

    @_writing
    async def compact(self) -> None:
        """Give the free pages of the database file back to the disk, and let later deletes do so by themselves. Needs
        the connection to have no transaction open, so it waits for the write under way (item 351), and a lock held by
        another process is waited out as for any write. The rewritten file reaches the disk through the write-ahead
        log, so the log is emptied too; a connection that is reading at that moment leaves the rest of it for a later
        checkpoint."""
        await self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await self._db.execute("VACUUM")
        async with self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
            await cursor.fetchall()

    def _encoded(self, messages: Sequence[Message]) -> list[str]:
        return [encode_message(message, self._image_dir) for message in messages]

    @_writing
    async def _unit[R](self, statements: Callable[[], Awaitable[R]]) -> R:
        """Run ``statements`` as one transaction on the connection and return what they return: all of it is stored or
        none of it. The write lock is held throughout, so no other writer's statement or commit lands inside the
        unit."""
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            stored: R = await statements()
        except BaseException:
            await self._db.rollback()
            raise
        await self._db.commit()
        return stored

    async def append_messages(self, thread_id: str, messages: Sequence[Message]) -> None:
        """Add ``messages`` after the thread's last one: all of them are stored or none, and concurrent appends to one
        thread are numbered one after the other."""
        bodies: list[str] = await asyncio.to_thread(self._encoded, messages)
        now: float = time.time()

        async def statements() -> None:
            top: aiosqlite.Row | None = await self._one(
                "SELECT COALESCE(MAX(seq), -1) AS top FROM messages WHERE thread_id = ?", (thread_id,)
            )
            first: int = (0 if top is None else int(top["top"])) + 1
            await self._db.executemany(
                "INSERT INTO messages(thread_id, seq, body) VALUES(?, ?, ?)",
                [(thread_id, first + offset, body) for offset, body in enumerate(bodies)],
            )
            await self._db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id))

        await self._unit(statements)

    async def replace_messages(self, thread_id: str, messages: Sequence[Message]) -> None:
        """Swap the thread's whole history for ``messages`` in one step: a failure or a stop leaves the old history. The
        history starts a new version (item 282): of the old one, what came before the latest run that started on it is
        kept aside, so that the chat can still be rewound to before any of its runs (:meth:`rewind_chat`)."""
        bodies: list[str] = await asyncio.to_thread(self._encoded, messages)
        now: float = time.time()

        async def statements() -> None:
            await self._db.execute(
                "INSERT INTO message_archive(thread_id, version, seq, body)"
                " SELECT m.thread_id, t.history_version, m.seq, m.body FROM messages AS m JOIN threads AS t"
                " ON t.id = m.thread_id WHERE m.thread_id = ? AND m.seq < (SELECT MAX(history_seq) FROM runs"
                " WHERE thread_id = ? AND history_version = t.history_version AND rewound_at IS NULL)",
                (thread_id, thread_id),
            )
            await self._db.execute(
                "UPDATE threads SET history_version = history_version + 1 WHERE id = ?", (thread_id,)
            )
            await self._db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            await self._db.executemany(
                "INSERT INTO messages(thread_id, seq, body) VALUES(?, ?, ?)",
                [(thread_id, index, body) for index, body in enumerate(bodies)],
            )
            await self._db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id))

        await self._unit(statements)

    async def load_messages(self, thread_id: str, keep_recent_images: int) -> list[Message]:
        """Load a thread's history; only the newest ``keep_recent_images`` screenshots are re-inflated. The read waits
        for the write under way (item 556): the connection shows its own half-done writes to a read, so one that landed
        between the delete and the insert of :meth:`replace_messages` found the history empty."""
        async with self._write_lock:
            rows: list[aiosqlite.Row] = await self._all(
                "SELECT body FROM messages WHERE thread_id = ? ORDER BY seq", (thread_id,)
            )
        bodies: list[str] = [str(row["body"]) for row in rows]

        def decode_all() -> list[Message]:
            parsed: list[JsonObject] = [parse_json_object(body, "stored message") for body in bodies]
            # The messages that name screenshots; a message that merely mentions a file (a call's ``file`` argument)
            # takes none of the places.
            with_images: list[int] = [index for index, message in enumerate(parsed) if _image_refs(message)]
            allowed: set[int] = (
                set(with_images[len(with_images) - keep_recent_images :]) if keep_recent_images > 0 else set()
            )
            return [_decoded(message, self._image_dir, index in allowed) for index, message in enumerate(parsed)]

        return await asyncio.to_thread(decode_all)

    async def runs_from(self, run_id: str) -> list[RunRecord]:
        """The run ``run_id`` and the later runs still in its chat, oldest first: what :meth:`rewind_chat` would take
        out of the chat now (item 282). None for a helper's run, which has a chat of its own."""
        return [_run(row) for row in await self._all(_RUNS_FROM, (run_id, run_id))]

    async def rewind_chat(self, run_id: str, expected: Sequence[str]) -> RewindCut:
        """Rewind the chat of the run ``run_id`` to before the run's first message (item 282), in one transaction. The
        run and the chat's later runs leave the chat: they stay stored, with what they cost, but no list of the chat's
        runs holds them. ``expected`` are the ids of those runs as :meth:`runs_from` read them for the caller, who
        planned for them; a chat that changed since is not rewound (``RunConflict``). The history the model reads
        ends where it ended before the run: cut back, or, when it was written anew since, made of what was kept aside
        of the version the run started on, as a new version. What a version kept aside after that place goes, and so
        do the versions after it; the screenshots only they named are left to :meth:`orphan_images`. The questions
        those runs, or the runs they delegated to, left for the owner wait no more: they expire. Refused for a
        delegated run, for a run from before the places of messages were kept (``InvalidValueError``), for one a
        rewind took out already, while a run works on the chat and while messages wait in its queue
        (``RunConflict``)."""

        async def statements() -> RewindCut:
            row: aiosqlite.Row | None = await self._one("SELECT * FROM runs WHERE id = ?", (run_id,))
            if row is None:
                raise NotFoundError("run not found", {"run_id": run_id})
            run: RunRecord = _run(row)
            context: JsonObject = {"run_id": run_id, "thread_id": run.thread_id}
            if run.parent_run_id is not None:
                raise InvalidValueError("a helper's run has no message of the owner's to go back to", context)
            if not run.rewindable:
                raise InvalidValueError(
                    "this message was sent before Dot kept the place of each message in its chat, so the chat cannot be"
                    " rewound to it",
                    context,
                )
            if row["rewound_at"] is not None:
                raise RunConflict("the chat was rewound to before this message already", context)
            await self._refuse_working_chats((run.thread_id,), "a chat cannot be rewound while a run works on it")
            queued: aiosqlite.Row | None = await self._one(
                "SELECT COUNT(*) AS n FROM queued_messages WHERE thread_id = ?", (run.thread_id,)
            )
            if queued is not None and int(queued["n"]) > 0:
                raise RunConflict(
                    "messages wait in the chat's queue: send them or take them out before rewinding it",
                    {**context, "queued": int(queued["n"])},
                )
            rewound: tuple[RunRecord, ...] = tuple(_run(item) for item in await self._all(_RUNS_FROM, (run_id, run_id)))
            if [item.id for item in rewound] != list(expected):
                planned: JsonArray = [*expected]
                found: JsonArray = [item.id for item in rewound]
                raise RunConflict(
                    "the chat changed while it was being rewound; try again",
                    {**context, "expected": planned, "found": found},
                )
            now: float = time.time()
            ids: tuple[str, ...] = tuple(item.id for item in rewound)
            marks: str = ",".join("?" * len(ids))
            await self._db.execute(f"UPDATE runs SET rewound_at = ? WHERE id IN ({marks})", (now, *ids))
            await self._db.execute(
                f"WITH RECURSIVE family(id) AS (SELECT id FROM runs WHERE id IN ({marks})"
                " UNION SELECT runs.id FROM runs JOIN family ON runs.parent_run_id = family.id)"
                " UPDATE approvals SET status = 'expired', decided_at = ?, decided_by = 'system', note = ?"
                " WHERE status = 'left' AND run_id IN (SELECT id FROM family)",
                (*ids, now, "the chat was rewound to before the run that asked"),
            )
            # The owner's yes to a step those runs kept goes with them, unused (item 483).
            await self._db.execute(
                f"WITH RECURSIVE family(id) AS (SELECT id FROM runs WHERE id IN ({marks})"
                " UNION SELECT runs.id FROM runs JOIN family ON runs.parent_run_id = family.id)"
                " UPDATE approvals SET permit_until = NULL WHERE status = 'approved' AND used_at IS NULL"
                " AND permit_until IS NOT NULL AND run_id IN (SELECT id FROM family)",
                ids,
            )
            version: int = int(row["history_version"])
            place: int = int(row["history_seq"])
            thread: aiosqlite.Row | None = await self._one(
                "SELECT history_version FROM threads WHERE id = ?", (run.thread_id,)
            )
            if thread is None:
                raise NotFoundError("thread not found", context)
            if int(thread["history_version"]) == version:
                await self._db.execute("DELETE FROM messages WHERE thread_id = ? AND seq >= ?", (run.thread_id, place))
                await self._db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, run.thread_id))
            else:
                kept: aiosqlite.Row | None = await self._one(
                    "SELECT COUNT(*) AS n FROM message_archive WHERE thread_id = ? AND version = ? AND seq < ?",
                    (run.thread_id, version, place),
                )
                if kept is None or int(kept["n"]) != place:
                    raise StoreError(
                        "the history kept aside for this message is not whole",
                        {**context, "version": version, "place": place, "kept": 0 if kept is None else int(kept["n"])},
                    )
                await self._db.execute("DELETE FROM messages WHERE thread_id = ?", (run.thread_id,))
                await self._db.execute(
                    "INSERT INTO messages(thread_id, seq, body) SELECT thread_id, seq, body FROM message_archive"
                    " WHERE thread_id = ? AND version = ? AND seq < ?",
                    (run.thread_id, version, place),
                )
                await self._db.execute(
                    "UPDATE threads SET history_version = history_version + 1, updated_at = ? WHERE id = ?",
                    (now, run.thread_id),
                )
            await self._db.execute(
                "DELETE FROM message_archive WHERE thread_id = ? AND (version > ? OR (version = ? AND seq >= ?))",
                (run.thread_id, version, version, place),
            )
            back: aiosqlite.Row | None = await self._one(
                "SELECT id FROM runs WHERE thread_id = ? AND parent_run_id IS NULL AND rewound_at IS NULL"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (run.thread_id,),
            )
            return RewindCut(
                thread_id=run.thread_id, rewound=rewound, back_to=None if back is None else str(back["id"])
            )

        return await self._unit(statements)

    async def save_images(self, images: Sequence[ImageData]) -> list[str]:
        """Write images to the screenshot directory (idempotent) and return their file names."""
        return await asyncio.to_thread(lambda: [_write_image(self._image_dir, image) for image in images])

    # ----- runs ------------------------------------------------------------------------------

    @_writing
    async def create_run(
        self,
        thread_id: str,
        goal: str,
        mode: RunMode,
        model: str,
        parent_run_id: str | None,
        source: str,
        effort: Effort,
        speed: Speed,
        once: OneMessage | None,
        approvals: ApprovalMode,
        goal_mode: bool,
        campaign_id: str | None,
        agent_id: str | None,
        owner: str | None,
    ) -> RunRecord:
        """A new queued run; ``owner`` is the process that drives it (:mod:`dotagent.agent.owners`)."""
        record: RunRecord = RunRecord(
            id=new_id("run"),
            thread_id=thread_id,
            goal=goal,
            mode=mode,
            model=model,
            status="queued",
            created_at=time.time(),
            finished_at=None,
            result="",
            steps=0,
            input_tokens=0,
            output_tokens=0,
            parent_run_id=parent_run_id,
            source=source,
            effort=effort,
            speed=speed,
            once=once,
            approvals=approvals,
            goal_mode=goal_mode,
            campaign_id=campaign_id,
            agent_id=agent_id,
            rewindable=parent_run_id is None,
        )
        working: tuple[RunStatus, ...] = tuple(sorted(WORKING_STATUSES))
        # Where the run's first message goes in the chat's history (item 282): the history's version and the place after
        # its last message. Nothing comes in between, as a chat has one run at work at a time and a run stores all it
        # says before it stops working. A delegated run works in a chat of its own, which is never rewound.
        place: str = (
            "NULL, NULL"
            if parent_run_id is not None
            else "(SELECT history_version FROM threads WHERE id = ?),"
            " (SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE thread_id = ?)"
        )
        # A chat has one run at work at a time, whichever process drives it, and none beside a queued message that
        # another process is starting (:meth:`claim_queued`); a delegated run works in a chat of its own. A process
        # keeps its own runs on a chat apart itself (``RunManager``), its run whose end is still to be stored too.
        refusal: str = (
            ""
            if parent_run_id is not None
            else " WHERE NOT EXISTS (SELECT 1 FROM runs WHERE thread_id = ? AND parent_run_id IS NULL"
            f" AND status IN ({','.join('?' * len(working))}) AND owner IS NOT ?)"
            " AND NOT EXISTS (SELECT 1 FROM queue_claims WHERE thread_id = ? AND owner IS NOT ?)"
        )
        async with self._db.execute(
            "INSERT INTO runs(id, thread_id, goal, mode, model, status, created_at, finished_at, result, steps,"
            " input_tokens, output_tokens, parent_run_id, source, effort, speed, once, approvals, goal_mode,"
            " campaign_id, agent_id, owner, history_version, history_seq) SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            f" ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {place}{refusal} RETURNING id",
            (
                record.id,
                record.thread_id,
                record.goal,
                record.mode,
                record.model,
                record.status,
                record.created_at,
                record.finished_at,
                record.result,
                record.steps,
                record.input_tokens,
                record.output_tokens,
                record.parent_run_id,
                record.source,
                record.effort,
                record.speed,
                record.once,
                record.approvals,
                int(record.goal_mode),
                record.campaign_id,
                record.agent_id,
                owner,
                *(() if parent_run_id is not None else (thread_id, thread_id)),
                *(() if parent_run_id is not None else (thread_id, *working, owner, thread_id, owner)),
            ),
        ) as cursor:
            inserted: aiosqlite.Row | None = await cursor.fetchone()
        await self._db.commit()
        if inserted is None:
            rows = await self._all(
                "SELECT id FROM runs WHERE thread_id = ? AND parent_run_id IS NULL"
                f" AND status IN ({','.join('?' * len(working))}) AND owner IS NOT ? ORDER BY created_at",
                (thread_id, *working, owner),
            )
            runs: JsonArray = [str(row["id"]) for row in rows]
            raise RunConflict("a run is already active on this thread", {"thread_id": thread_id, "run_ids": runs})
        return record

    @_writing
    async def set_run_status(self, run_id: str, status: RunStatus) -> None:
        await self._db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        await self._db.commit()

    @_writing
    async def set_run_steps(self, run_id: str, steps: int) -> None:
        await self._db.execute("UPDATE runs SET steps = ? WHERE id = ?", (steps, run_id))
        await self._db.commit()

    @_writing
    async def set_run_model(self, run_id: str, model: str, effort: Effort | None) -> None:
        """The model a run works on from now on (a standby model, once its own model cannot answer) and the reasoning
        level it asks that model for (``None``: the configured default)."""
        await self._db.execute("UPDATE runs SET model = ?, effort = ? WHERE id = ?", (model, effort, run_id))
        await self._db.commit()

    @_writing
    async def add_run_tokens(self, run_id: str, input_tokens: int, output_tokens: int) -> None:
        """Add the tokens of one model call of a run (a step or a side call) to its totals."""
        await self._db.execute(
            "UPDATE runs SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ? WHERE id = ?",
            (input_tokens, output_tokens, run_id),
        )
        await self._db.commit()

    @_writing
    async def finish_run(self, run_id: str, status: RunStatus, result: str) -> None:
        await self._set_end(run_id, status, result, time.time())
        await self._db.commit()

    async def end_run(self, run_id: str, status: RunStatus, result: str, data: JsonObject) -> EventRecord:
        """Store how run ``run_id`` ended: its status and result, the scores of the notes it was handed
        (:meth:`close_memory_uses`) and its ``run_finished`` event with ``data``, which is returned. One transaction:
        all of it is stored or none, so an end stored here never lacks the event that tells of it. The time of the end
        is taken once the transaction holds the write lock, as :meth:`finish_run` takes it."""

        async def statements() -> EventRecord:
            now: float = time.time()
            await self._set_end(run_id, status, result, now)
            await self._score_memory_uses(run_id, status)
            return await self._insert_event(run_id, now, "run_finished", data)

        return await self._unit(statements)

    async def _set_end(self, run_id: str, status: RunStatus, result: str, now: float) -> None:
        await self._db.execute(
            "UPDATE runs SET status = ?, result = ?, finished_at = ? WHERE id = ?", (status, result, now, run_id)
        )

    async def get_run(self, run_id: str) -> RunRecord:
        row: aiosqlite.Row | None = await self._one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise NotFoundError("run not found", {"run_id": run_id})
        return _run(row)

    async def list_runs(self, limit: int, thread_id: str | None) -> list[RunRecord]:
        """The latest ``limit`` runs, of the chat ``thread_id`` when it is given, newest first; the runs a rewind
        took out of their chat (item 282) are left out."""
        if thread_id is None:
            rows = await self._all(
                "SELECT * FROM runs WHERE rewound_at IS NULL ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        else:
            rows = await self._all(
                "SELECT * FROM runs WHERE thread_id = ? AND rewound_at IS NULL ORDER BY created_at DESC LIMIT ?",
                (thread_id, limit),
            )
        return [_run(row) for row in rows]

    # ----- while you were away (item 466) ----------------------------------------------------------------------------

    async def unattended_runs(self, source: str, since: float, limit: int) -> list[UnattendedRun]:
        """The newest ``limit`` runs of ``source`` (the scheduler's: a schedule's firings and runs by hand, and the
        sessions of campaigns) that are no helper's and ended at or after ``since``, or work; with the schedule's name
        and the campaign's title."""
        rows = await self._all(
            f"SELECT runs.*, {_UNATTENDED_NAMES} FROM runs {_UNATTENDED_JOINS}"
            f" WHERE {_UNATTENDED} ORDER BY runs.created_at DESC LIMIT ?",
            (source, since, limit),
        )
        return [_unattended(row) for row in rows]

    async def unattended_counts(self, source: str, since: float) -> list[UnattendedCount]:
        """How many of those runs (:meth:`unattended_runs`, all of them) ended so or work, by status, the sessions of
        campaigns apart."""
        rows = await self._all(
            "SELECT runs.status AS status, runs.campaign_id IS NOT NULL AS campaign, COUNT(*) AS count FROM runs"
            f" WHERE {_UNATTENDED} GROUP BY runs.status, campaign",
            (source, since),
        )
        return [
            UnattendedCount(status=row["status"], campaign=bool(row["campaign"]), count=row["count"]) for row in rows
        ]

    async def auto_decision_counts(self, source: str, since: float) -> list[AutoDecisionCount]:
        """The approvals the review model and yolo decided at or after ``since`` in those runs (:meth:`unattended_runs`)
        and in their helpers, counted for each run by who decided and how; neither a summary nor the arguments of one
        are read."""
        rows = await self._all(
            f"WITH unattended AS (SELECT runs.id FROM runs WHERE {_UNATTENDED}),"
            " decided AS (SELECT COALESCE(ran.parent_run_id, ran.id) AS top_id, approvals.decided_by AS decided_by,"
            " approvals.status AS decision FROM approvals JOIN runs AS ran ON ran.id = approvals.run_id"
            " WHERE (ran.id IN unattended OR ran.parent_run_id IN unattended) AND approvals.decided_by IN (?, ?)"
            " AND approvals.status IN ('approved', 'denied') AND approvals.decided_at >= ?)"
            f" SELECT runs.*, {_UNATTENDED_NAMES}, decided.decided_by AS decided_by, decided.decision AS decision,"
            f" COUNT(*) AS count FROM decided JOIN runs ON runs.id = decided.top_id {_UNATTENDED_JOINS}"
            " GROUP BY decided.top_id, decided.decided_by, decided.decision",
            (source, since, "reviewer", "yolo", since),
        )
        return [
            AutoDecisionCount(
                run=_unattended(row), decided_by=row["decided_by"], status=row["decision"], count=row["count"]
            )
            for row in rows
        ]

    async def unattended_drafts(self, source: str, since: float) -> int:
        """How many drafts those runs (:meth:`unattended_runs`) and their helpers wrote at or after ``since`` that still
        wait for the owner."""
        row = await self._one(
            f"WITH unattended AS (SELECT runs.id FROM runs WHERE {_UNATTENDED})"
            " SELECT COUNT(*) AS count FROM drafts JOIN runs AS ran ON ran.id = drafts.run_id"
            " WHERE (ran.id IN unattended OR ran.parent_run_id IN unattended) AND drafts.status = 'waiting'"
            " AND drafts.created_at >= ?",
            (source, since, since),
        )
        return 0 if row is None else int(row["count"])

    async def chat_turns_until(self, thread_id: str, until: float, limit: int) -> list[RunRecord]:
        """The latest ``limit`` turns of the chat ``thread_id`` (its runs that no other run delegated) that started by
        ``until``, newest first; the runs a rewind took out of the chat (item 282) are left out."""
        rows = await self._all(
            "SELECT * FROM runs WHERE thread_id = ? AND parent_run_id IS NULL AND rewound_at IS NULL"
            " AND created_at <= ? ORDER BY created_at DESC LIMIT ?",
            (thread_id, until, limit),
        )
        return [_run(row) for row in rows]

    async def list_child_runs(self, parent_run_id: str) -> list[RunRecord]:
        """The runs started on behalf of ``parent_run_id``, oldest first."""
        rows = await self._all("SELECT * FROM runs WHERE parent_run_id = ? ORDER BY created_at", (parent_run_id,))
        return [_run(row) for row in rows]

    async def list_thread_helpers(self, thread_id: str, limit: int) -> list[RunRecord]:
        """The runs that the runs of the chat ``thread_id`` started on their behalf (item 227), newest first, at most
        ``limit``, those of the runs a rewind took out of the chat (item 282) left out. A helper's own runs are not
        among them: a delegated run delegates no further."""
        rows = await self._all(
            "SELECT * FROM runs WHERE parent_run_id IN (SELECT id FROM runs WHERE thread_id = ? AND rewound_at IS NULL)"
            " ORDER BY created_at DESC LIMIT ?",
            (thread_id, limit),
        )
        return [_run(row) for row in rows]

    async def family_tokens(self, run_id: str) -> int:
        """The tokens (input and output) of a run and of the runs started on its behalf, as their totals stand now."""
        row = await self._one(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS n FROM runs WHERE id = ? OR parent_run_id = ?",
            (run_id, run_id),
        )
        return 0 if row is None else int(row["n"])

    async def active_run_owners(self) -> list[str | None]:
        """The owners of the runs that are active (queued, running, or blocked on a question), each once; ``None`` for
        runs made before owners were kept."""
        rows = await self._all(
            "SELECT DISTINCT owner FROM runs WHERE status IN ('running', 'queued', 'blocked') ORDER BY owner", ()
        )
        return [None if row["owner"] is None else str(row["owner"]) for row in rows]

    async def working_runs_of_others(self, owner: str) -> list[WorkingRun]:
        """The runs at work that processes other than ``owner`` drive (``dotx`` commands, for the daemon), oldest
        first."""
        working: tuple[RunStatus, ...] = tuple(sorted(WORKING_STATUSES))
        rows = await self._all(
            "SELECT id, thread_id, owner FROM runs WHERE owner IS NOT NULL AND owner != ?"
            f" AND status IN ({','.join('?' * len(working))}) ORDER BY created_at, id",
            (owner, *working),
        )
        return [
            WorkingRun(run_id=str(row["id"]), thread_id=str(row["thread_id"]), owner=str(row["owner"])) for row in rows
        ]

    async def last_event_id(self) -> int:
        """The id of the newest stored event, 0 when there is none. Every event with a smaller id is stored already:
        one process writes at a time, and an event gets its id in the write that stores it."""
        row: aiosqlite.Row | None = await self._one("SELECT COALESCE(MAX(id), 0) AS last FROM events", ())
        return 0 if row is None else int(row["last"])

    async def events_of_others(self, owner: str, after: int, upto: int, limit: int) -> list[EventRecord]:
        """The stored events with an id after ``after`` and up to ``upto``, oldest first and at most ``limit``, of the
        runs that processes other than ``owner`` drive."""
        rows = await self._all(
            "SELECT events.* FROM events JOIN runs ON runs.id = events.run_id"
            " WHERE events.id > ? AND events.id <= ? AND runs.owner IS NOT NULL AND runs.owner != ?"
            " ORDER BY events.id LIMIT ?",
            (after, upto, owner, limit),
        )
        return [_event(row) for row in rows]

    @_writing
    async def ask_stop(self, run_id: str) -> None:
        """Ask the process that drives the run ``run_id`` to stop it (:meth:`stops_asked`); nothing is asked of a run
        that is not at work."""
        working: tuple[RunStatus, ...] = tuple(sorted(WORKING_STATUSES))
        await self._db.execute(
            "INSERT OR IGNORE INTO run_stops(run_id, asked_at)"
            f" SELECT id, ? FROM runs WHERE id = ? AND status IN ({','.join('?' * len(working))})",
            (time.time(), run_id, *working),
        )
        await self._db.commit()

    async def stops_asked(self, run_ids: Sequence[str]) -> list[str]:
        """The runs of ``run_ids`` that another process asked to stop (:meth:`ask_stop`)."""
        if not run_ids:
            return []
        rows = await self._all(
            f"SELECT run_id FROM run_stops WHERE run_id IN ({','.join('?' * len(run_ids))}) ORDER BY run_id",
            tuple(run_ids),
        )
        return [str(row["run_id"]) for row in rows]

    @_writing
    async def forget_stops(self, run_ids: Sequence[str]) -> None:
        """The asks to stop ``run_ids`` were answered, or their runs ended: they go."""
        await self._db.execute(
            f"DELETE FROM run_stops WHERE run_id IN ({','.join('?' * len(run_ids))})", tuple(run_ids)
        )
        await self._db.commit()

    @_writing
    async def forget_stops_of_ended_runs(self) -> None:
        """The asks to stop runs that are no longer at work go: their process ended them, or went, before it saw the
        ask."""
        working: tuple[RunStatus, ...] = tuple(sorted(WORKING_STATUSES))
        await self._db.execute(
            "DELETE FROM run_stops WHERE run_id NOT IN"
            f" (SELECT id FROM runs WHERE status IN ({','.join('?' * len(working))}))",
            working,
        )
        await self._db.commit()

    async def claim_sent(self, key: str, now: float, stale_before: float, forget_before: float) -> SentMessage | None:
        """Take the key of a message the pages send for this sending, in one write: ``None`` when it is taken, else
        the message of the key, sent before or under way. A key whose sending began before ``stale_before`` and came to
        nothing (its process went) is taken again; the keys sent before ``forget_before`` are forgotten."""

        async def statements() -> SentMessage | None:
            await self._db.execute("DELETE FROM sent_messages WHERE sent_at < ?", (forget_before,))
            async with self._db.execute(
                "INSERT INTO sent_messages(key, sent_at, target) VALUES (?, ?, NULL) ON CONFLICT(key) DO UPDATE"
                " SET sent_at = excluded.sent_at WHERE sent_messages.target IS NULL AND sent_messages.sent_at < ?"
                " RETURNING key",
                (key, now, stale_before),
            ) as cursor:
                taken: aiosqlite.Row | None = await cursor.fetchone()
            if taken is not None:
                return None
            row: aiosqlite.Row | None = await self._one("SELECT * FROM sent_messages WHERE key = ?", (key,))
            if row is None:
                raise StoreError("a key that could not be taken is not stored", {"key": key})
            return SentMessage(key=str(row["key"]), sent_at=float(row["sent_at"]), target=row["target"])

        return await self._unit(statements)

    @_writing
    async def settle_sent(self, key: str, target: str) -> None:
        """Keep what the message of ``key`` came to: the run it started or steered, or the queued message it became."""
        await self._db.execute("UPDATE sent_messages SET target = ? WHERE key = ?", (target, key))
        await self._db.commit()

    @_writing
    async def release_sent(self, key: str) -> None:
        """Let the key of a message whose sending failed go, so that sending it again is tried anew."""
        await self._db.execute("DELETE FROM sent_messages WHERE key = ? AND target IS NULL", (key,))
        await self._db.commit()

    @_writing
    async def mark_interrupted_runs(self, gone: Sequence[str]) -> int:
        """Runs left active by processes that are gone (the owners of ``gone``, and every run made before owners were
        kept) become 'failed', finished at their last sign of life: their newest event, else the time they were created.
        Each one also gets, at that time, the ``run_finished`` event that every other end of a run stores, and that ends
        the stream of its events; so does a run closed this way before that event was stored, unless its events were
        pruned since (the run keeps none, and gets none back). The events of delegated runs come first, as a run's
        end follows the ends of the runs it delegated (item 197). Returns how many runs changed."""
        async with self._db.execute(
            "UPDATE runs SET status = 'failed', result = ?,"
            " finished_at = COALESCE((SELECT MAX(ts) FROM events WHERE events.run_id = runs.id), created_at)"
            " WHERE status IN ('running', 'queued', 'blocked')"
            f" AND (owner IS NULL OR owner IN ({','.join('?' * len(gone))})) RETURNING id",
            (RESULT_RESTARTED, *gone),
        ) as cursor:
            closed: list[str] = [str(row["id"]) for row in await cursor.fetchall()]
        await self._db.execute(
            "INSERT INTO events(run_id, ts, kind, data) SELECT id, finished_at, 'run_finished', ? FROM runs"
            " WHERE status = 'failed' AND result = ?"
            f" AND (id IN ({','.join('?' * len(closed))})"
            " OR EXISTS (SELECT 1 FROM events WHERE events.run_id = runs.id))"
            " AND NOT EXISTS (SELECT 1 FROM events WHERE events.run_id = runs.id AND events.kind = 'run_finished')"
            " ORDER BY parent_run_id IS NULL",
            (dumps_compact({"status": "failed", "result": RESULT_RESTARTED}), RESULT_RESTARTED, *closed),
        )
        await self._db.commit()
        return len(closed)

    @_writing
    async def close_runs_of(self, owners: Sequence[str], result: str) -> list[EventRecord]:
        """The active runs of ``owners``, processes that are gone, become 'failed' with ``result``, finished at their
        last sign of life: their newest event, else the time they were created. In the same write each gets, at that
        time, the ``run_finished`` event that every other end of a run stores, those of delegated runs first; returns
        those events, for the pages that follow the runs. Unlike :meth:`mark_interrupted_runs`, a run without an owner
        stays as it is."""
        async with self._db.execute(
            "UPDATE runs SET status = 'failed', result = ?,"
            " finished_at = COALESCE((SELECT MAX(ts) FROM events WHERE events.run_id = runs.id), created_at)"
            f" WHERE status IN ('running', 'queued', 'blocked') AND owner IN ({','.join('?' * len(owners))})"
            " RETURNING id, finished_at, parent_run_id",
            (result, *owners),
        ) as cursor:
            rows: list[sqlite3.Row] = list(await cursor.fetchall())
        closed: list[tuple[str, float]] = [
            (str(row["id"]), float(row["finished_at"]))
            for row in sorted(rows, key=lambda row: row["parent_run_id"] is None)
        ]
        data: JsonObject = {"status": "failed", "result": result}
        told: list[EventRecord] = []
        for run_id, finished_at in closed:
            async with self._db.execute(
                "INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, 'run_finished', ?) RETURNING *",
                (run_id, finished_at, dumps_compact(data)),
            ) as cursor:
                told.extend(_event(row) for row in await cursor.fetchall())
        await self._db.commit()
        return told

    async def runs_finished_since(self, since: float) -> list[RunRecord]:
        """The runs that finished at or after ``since``, oldest first."""
        rows = await self._all("SELECT * FROM runs WHERE finished_at >= ? ORDER BY created_at", (since,))
        return [_run(row) for row in rows]

    # ----- events ----------------------------------------------------------------------------

    async def first_event_id_since(self, since: float) -> int:
        """The id of the first event at or after ``since``, one past the last event when none is; found by halving the
        ids, which grow with the time, so no read walks the whole table (it has no index on the time, item 469). The
        ends of runs are not probed (item 556): the end of a run closed after its process went is stored at the run's
        last sign of life, which may be days before the events stored beside it, and a probe that landed on one made
        the search go on past events that are in the time asked for."""
        bounds = await self._one("SELECT MIN(id) AS low, MAX(id) AS high FROM events", ())
        if bounds is None or bounds["low"] is None:
            return 0
        low: int = int(bounds["low"])
        high: int = int(bounds["high"]) + 1
        while low < high:
            middle: int = (low + high) // 2
            row = await self._one(
                "SELECT id, ts FROM events WHERE id >= ? AND kind != 'run_finished' ORDER BY id LIMIT 1", (middle,)
            )
            if row is None or float(row["ts"]) >= since:
                high = middle if row is None else min(middle, int(row["id"]))
            else:
                low = int(row["id"]) + 1
        return low

    async def tool_steps(self, floor_id: int, before_id: int, limit: int, taint_kinds: Sequence[str]) -> list[StepRow]:
        """The newest ``limit`` steps whose action reached the policy (a ``tool_result`` with a kind) among the events
        from ``floor_id`` up to, not with, ``before_id``, newest first (item 469): with whether their run was in yolo,
        whether the run was tainted before the step's result (after the newest of its events of ``taint_kinds`` before
        it, as :func:`dotagent.agent.gate.tainted_after` reads them), and, for a step whose record says the gate did not
        let it through, what the ``policy`` event of its call said, with the taint it kept (item 474), looked for among
        the run's own events since the call's ``tool_call``, at most :data:`_STEP_CALL_EVENTS` back. SQLite reads the
        fields out of each event's JSON, so a page carries no event's whole text."""
        kinds: str = ", ".join("?" for _ in taint_kinds)
        rows = await self._all(
            "SELECT e.id, e.run_id, e.ts, json_extract(e.data, '$.tool') AS tool,"
            " json_extract(e.data, '$.kind') AS action_kind, json_extract(e.data, '$.risk') AS risk,"
            " json_extract(e.data, '$.summary') AS summary, json_extract(e.data, '$.target') AS target,"
            " json_extract(e.data, '$.ok') AS ok, json_extract(e.data, '$.decision') AS decision,"
            " json_type(e.data, '$.decision') IS NOT NULL AS recorded, json_extract(e.data, '$.flags') AS flags,"
            " COALESCE(r.approvals = 'yolo', 0) AS yolo,"
            " COALESCE((SELECT (t.kind != 'steer' OR json_type(t.data, '$.vouches') = 'false')"
            " AND json_type(t.data, '$.tainted') = 'true' FROM events AS t"
            f" WHERE t.run_id = e.run_id AND t.id < e.id AND t.kind IN ({kinds}) ORDER BY t.id DESC LIMIT 1), 0)"
            " AS tainted_before,"
            " CASE WHEN json_type(e.data, '$.decision') = 'null' THEN (SELECT json_object('source',"
            " json_extract(p.data, '$.source'), 'verdict', json_extract(p.data, '$.verdict'),"
            " 'tainted', json(p.data -> '$.tainted')) FROM events AS p"
            " WHERE p.run_id = e.run_id AND p.kind = 'policy' AND p.id < e.id AND p.id > (SELECT c.id FROM events AS c"
            " WHERE c.run_id = e.run_id AND c.id < e.id AND c.id >= COALESCE((SELECT a.id FROM events AS a"
            " WHERE a.run_id = e.run_id AND a.id < e.id ORDER BY a.id DESC LIMIT 1 OFFSET ?), 0)"
            " AND c.kind = 'tool_call' AND json_extract(c.data, '$.call_id') = json_extract(e.data, '$.call_id')"
            " ORDER BY c.id DESC LIMIT 1)"
            " AND json_extract(p.data, '$.summary') = json_extract(e.data, '$.summary')"
            " AND json_extract(p.data, '$.target') = json_extract(e.data, '$.target') ORDER BY p.id DESC LIMIT 1)"
            " END AS stopped_by"
            " FROM events AS e LEFT JOIN runs AS r ON r.id = e.run_id"
            " WHERE e.id >= ? AND e.id < ? AND e.kind = 'tool_result'"
            " AND COALESCE(json_extract(e.data, '$.kind'), '') != '' ORDER BY e.id DESC LIMIT ?",
            (*taint_kinds, _STEP_CALL_EVENTS - 1, floor_id, before_id, limit),
        )
        return [
            StepRow(
                event_id=row["id"],
                run_id=row["run_id"],
                at=row["ts"],
                tool=row["tool"] or "",
                kind=row["action_kind"] or "",
                risk=row["risk"] or "",
                summary=row["summary"] or "",
                target=row["target"] or "",
                ok=bool(row["ok"]),
                decision=None if row["decision"] is None else parse_json_object(row["decision"], "a step's decision"),
                recorded=bool(row["recorded"]),
                flags=() if row["flags"] is None else _step_flags(parse_json_value(row["flags"], "a step's flags")),
                tainted_before=bool(row["tainted_before"]),
                stopped_by=(
                    None if row["stopped_by"] is None else parse_json_object(row["stopped_by"], "a step's policy event")
                ),
                yolo=bool(row["yolo"]),
            )
            for row in rows
        ]

    @_writing
    async def add_event(self, run_id: str, kind: str, data: JsonObject) -> EventRecord:
        record: EventRecord = await self._insert_event(run_id, time.time(), kind, data)
        await self._db.commit()
        return record

    async def _insert_event(self, run_id: str, now: float, kind: str, data: JsonObject) -> EventRecord:
        cursor: aiosqlite.Cursor = await self._db.execute(
            "INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, ?, ?)",
            (run_id, now, kind, dumps_compact(data)),
        )
        if cursor.lastrowid is None:
            raise StoreError("event insert returned no row id", {"run_id": run_id, "kind": kind})
        return EventRecord(id=cursor.lastrowid, run_id=run_id, ts=now, kind=kind, data=data)

    async def picked_servers(self, thread_id: str, event_kind: str) -> frozenset[str]:
        """The MCP servers the owner picked for the messages of the chat ``thread_id`` (item 415), as the runs' events
        of ``event_kind`` (the picks event) recorded them: what a run's tools may take in by the owner's word, never
        by what a model called."""
        rows = await self._all(
            "SELECT data FROM events WHERE kind = ? AND run_id IN "
            "(SELECT id FROM runs WHERE thread_id = ? AND rewound_at IS NULL)",
            (event_kind, thread_id),
        )
        servers: set[str] = set()
        for row in rows:
            plugins: object = parse_json_object(str(row["data"]), "picks event").get("plugins")
            if isinstance(plugins, list):
                servers.update(name for name in plugins if isinstance(name, str))
        return frozenset(servers)

    async def list_events(self, run_id: str, after_id: int, limit: int) -> list[EventRecord]:
        rows = await self._all(
            "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?", (run_id, after_id, limit)
        )
        return [_event(row) for row in rows]

    async def last_event(self, run_id: str, kind: str) -> EventRecord | None:
        """The newest event of ``kind`` of a run, or ``None`` when it has none."""
        row = await self._one(
            "SELECT * FROM events WHERE run_id = ? AND kind = ? ORDER BY id DESC LIMIT 1", (run_id, kind)
        )
        return None if row is None else _event(row)

    async def count_model_calls(self, run_id: str, purpose: CallPurpose) -> int:
        """How many model calls for ``purpose`` a run made, the ones that failed included."""
        row: aiosqlite.Row | None = await self._one(
            "SELECT COUNT(*) FROM events WHERE run_id = ? AND kind = ? AND json_extract(data, '$.purpose') = ?",
            (run_id, CALL_EVENT, purpose),
        )
        return 0 if row is None else int(row[0])

    async def events_of_kind_after(self, run_id: str, kind: str, after: int) -> list[EventRecord]:
        """The events of ``kind`` of a run whose id is after ``after``, oldest first."""
        rows = await self._all(
            "SELECT * FROM events WHERE run_id = ? AND kind = ? AND id > ? ORDER BY id", (run_id, kind, after)
        )
        return [_event(row) for row in rows]

    async def events_of_kind(self, run_id: str, kind: str, limit: int) -> list[EventRecord]:
        """The newest ``limit`` events of ``kind`` of a run, oldest first."""
        rows = await self._all(
            "SELECT * FROM (SELECT * FROM events WHERE run_id = ? AND kind = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (run_id, kind, limit),
        )
        return [_event(row) for row in rows]

    async def events_of_kinds(self, run_id: str, kinds: Sequence[str], limit: int) -> list[EventRecord]:
        """The newest ``limit`` events of a run that are of any of ``kinds``, oldest first."""
        marks: str = ",".join("?" * len(kinds))
        rows = await self._all(
            "SELECT * FROM (SELECT * FROM events WHERE run_id = ? AND kind IN"
            f" ({marks}) ORDER BY id DESC LIMIT ?) ORDER BY id",
            (run_id, *kinds, limit),
        )
        return [_event(row) for row in rows]

    async def tool_calls_named(self, run_id: str, tool: str) -> list[EventRecord]:
        """The ``tool_call`` events of a run that call the tool ``tool``, oldest first."""
        rows = await self._all(
            "SELECT * FROM events WHERE run_id = ? AND kind = 'tool_call' AND json_extract(data, '$.tool') = ?"
            " ORDER BY id",
            (run_id, tool),
        )
        return [_event(row) for row in rows]

    async def run_sites(self, run_id: str) -> list[str]:
        """The sites a run's calls reached (the ``site`` of its ``tool_result`` events: page hosts and applications),
        the one it reached most often first (item 308)."""
        rows = await self._all(
            "SELECT json_extract(data, '$.site') AS site FROM events WHERE run_id = ? AND kind = 'tool_result'"
            " AND coalesce(json_extract(data, '$.site'), '') != '' GROUP BY site ORDER BY COUNT(*) DESC, site",
            (run_id,),
        )
        return [str(row["site"]) for row in rows]

    async def read_outside(self, run_id: str, kinds: Sequence[str], channels: Sequence[str]) -> bool:
        """Whether a run read content third parties can author, from every one of its ``tool_result`` events: one of
        ``kinds`` or over one of ``channels``, or one the run made while it was tainted."""
        kind_marks: str = ",".join("?" for _ in kinds)
        channel_marks: str = ",".join("?" for _ in channels)
        row: aiosqlite.Row | None = await self._one(
            "SELECT EXISTS(SELECT 1 FROM events WHERE run_id = ? AND kind = 'tool_result' AND ("
            "json_extract(data, '$.tainted') = 1"
            f" OR json_extract(data, '$.kind') IN ({kind_marks})"
            f" OR json_extract(data, '$.channel') IN ({channel_marks})))",
            (run_id, *kinds, *channels),
        )
        return row is not None and bool(row[0])

    async def model_profile(self, model: str, runs: int) -> ModelProfile:
        """How reliably ``model`` worked in its newest ``runs`` finished runs, read on a read-only connection."""
        return await asyncio.to_thread(read_model_profile, self._database, model, runs)

    async def context_evidence(self, model: str, days: int) -> ContextEvidence:
        """What the runs of ``model`` created in the last ``days`` days say about its context size (item 250), read on a
        read-only connection."""
        return await asyncio.to_thread(read_context_evidence, self._database, model, days, time.time())

    async def run_costs(self, run_id: str) -> RunCosts:
        """What the run cost and where it went, read on a read-only connection; its minutes run to now while it
        works."""
        return await asyncio.to_thread(collect_run_costs, self._database, run_id, time.time())

    # ----- approvals -------------------------------------------------------------------------

    @_writing
    async def _insert_approval(self, record: ApprovalRecord) -> None:
        await self._db.execute(
            "INSERT INTO approvals(id, run_id, created_at, decided_at, status, tool, summary, severity, args, "
            "decided_by, note, digest, kept_until, permit_until, used_at, scope_thread_id, scope_campaign_id)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.run_id,
                record.created_at,
                record.decided_at,
                record.status,
                record.tool,
                record.summary,
                record.severity,
                dumps_compact(record.args),
                record.decided_by,
                record.note,
                record.digest,
                record.kept_until,
                record.permit_until,
                record.used_at,
                record.scope_thread_id,
                record.scope_campaign_id,
            ),
        )
        await self._db.commit()

    async def create_approval(
        self, run_id: str, tool: str, summary: str, severity: str, args: JsonObject
    ) -> ApprovalRecord:
        record: ApprovalRecord = ApprovalRecord(
            id=new_id("apr"),
            run_id=run_id,
            created_at=time.time(),
            decided_at=None,
            status="pending",
            tool=tool,
            summary=summary,
            severity=severity,
            args=args,
            decided_by=None,
            note="",
            digest=None,
            kept_until=None,
            permit_until=None,
            used_at=None,
            scope_thread_id=None,
            scope_campaign_id=None,
        )
        await self._insert_approval(record)
        return record

    async def create_left_approval(
        self, run_id: str, tool: str, summary: str, severity: str, args: JsonObject
    ) -> ApprovalRecord:
        """Store a question that a run left for the user and went on without: ``left`` until it is answered or skipped,
        never listed as pending and never expired."""
        record: ApprovalRecord = ApprovalRecord(
            id=new_id("apr"),
            run_id=run_id,
            created_at=time.time(),
            decided_at=None,
            status="left",
            tool=tool,
            summary=summary,
            severity=severity,
            args=args,
            decided_by=None,
            note="",
            digest=None,
            kept_until=None,
            permit_until=None,
            used_at=None,
            scope_thread_id=None,
            scope_campaign_id=None,
        )
        await self._insert_approval(record)
        return record

    async def create_kept_approval(
        self,
        run_id: str,
        tool: str,
        summary: str,
        severity: str,
        args: JsonObject,
        digest: str | None,
        kept_until: float,
        scope_thread_id: str,
        scope_campaign_id: str | None,
    ) -> ApprovalRecord:
        """Store a step a run kept for the owner when nobody answered its question in time (item 482): ``left`` until
        the owner answers it, skips it or it lapses at ``kept_until``; ``digest`` is the fingerprint of its action, None
        for one whose card shows less than all of it; it belongs to the conversation ``scope_thread_id`` and the
        campaign ``scope_campaign_id`` (item 484)."""
        record: ApprovalRecord = ApprovalRecord(
            id=new_id("apr"),
            run_id=run_id,
            created_at=time.time(),
            decided_at=None,
            status="left",
            tool=tool,
            summary=summary,
            severity=severity,
            args=args,
            decided_by=None,
            note="",
            digest=digest,
            kept_until=kept_until,
            permit_until=None,
            used_at=None,
            scope_thread_id=scope_thread_id,
            scope_campaign_id=scope_campaign_id,
        )
        await self._insert_approval(record)
        return record

    async def open_kept(self, digest: str, thread_id: str, campaign_id: str | None) -> ApprovalRecord | None:
        """The step of fingerprint ``digest`` kept in the conversation ``thread_id`` (of the campaign ``campaign_id``)
        that nobody answered yet, the oldest; None when there is none (items 482, 484)."""
        row = await self._one(
            "SELECT * FROM approvals WHERE digest = ? AND status = 'left' AND scope_thread_id = ?"
            " AND scope_campaign_id IS ? ORDER BY created_at LIMIT 1",
            (digest, thread_id, campaign_id),
        )
        return None if row is None else _approval(row)

    async def count_open_kept(
        self, thread_id: str, campaign_id: str | None, schedule_id: str | None
    ) -> tuple[int, int]:
        """How many steps kept for the owner wait now: of the conversation ``thread_id`` (of the campaign
        ``campaign_id``) and the chats of the schedule ``schedule_id`` (each firing of a schedule may have a chat of its
        own), and of all (items 482, 484)."""
        row = await self._one(
            "SELECT COUNT(*) AS overall, COALESCE(SUM((a.scope_thread_id = ? AND a.scope_campaign_id IS ?)"
            " OR (t.schedule_id IS NOT NULL AND t.schedule_id = ?)), 0) AS here FROM approvals AS a"
            " LEFT JOIN threads AS t ON t.id = a.scope_thread_id WHERE a.status = 'left' AND a.kept_until IS NOT NULL",
            (thread_id, campaign_id, schedule_id),
        )
        if row is None:
            return 0, 0
        return int(row["here"]), int(row["overall"])

    @_writing
    async def grant_permit(self, approval_id: str, decided_by: str, note: str, permit_until: float) -> ApprovalRecord:
        """The owner's yes to an ask kept for them (item 483): decided ``approved``, holding until ``permit_until``,
        unused; a kept ask no longer open keeps its decision."""
        await self._db.execute(
            "UPDATE approvals SET status = 'approved', decided_at = ?, decided_by = ?, note = ?, permit_until = ?"
            " WHERE id = ? AND status = 'left' AND kept_until IS NOT NULL",
            (time.time(), decided_by, note, permit_until, approval_id),
        )
        await self._db.commit()
        return await self.get_approval(approval_id)

    @_writing
    async def reopen_kept(self, approval_id: str) -> ApprovalRecord:
        """Open again an ask kept for the owner whose yes could not be delivered (item 483): ``left`` as it was, the yes
        gone; one whose yes was spent meanwhile keeps it."""
        await self._db.execute(
            "UPDATE approvals SET status = 'left', decided_at = NULL, decided_by = NULL, note = '', permit_until = NULL"
            " WHERE id = ? AND status = 'approved' AND permit_until IS NOT NULL AND used_at IS NULL",
            (approval_id,),
        )
        await self._db.commit()
        return await self.get_approval(approval_id)

    @_writing
    async def take_permit(
        self, digest: str, thread_id: str, campaign_id: str | None, now: float
    ) -> ApprovalRecord | None:
        """Spend the owner's yes to the step of fingerprint ``digest`` kept in the conversation ``thread_id`` (of the
        campaign ``campaign_id``; items 483, 484): the oldest unused one still in time at ``now`` is spent, once; None
        when there is none."""
        row = await self._one(
            "SELECT id FROM approvals WHERE digest = ? AND status = 'approved' AND permit_until > ? AND used_at IS NULL"
            " AND scope_thread_id = ? AND scope_campaign_id IS ? ORDER BY decided_at LIMIT 1",
            (digest, now, thread_id, campaign_id),
        )
        if row is None:
            return None
        spent = await self._db.execute(
            "UPDATE approvals SET used_at = ? WHERE id = ? AND used_at IS NULL AND status = 'approved'"
            " AND permit_until > ?",
            (now, row["id"], now),
        )
        await self._db.commit()
        return await self.get_approval(row["id"]) if spent.rowcount == 1 else None

    async def skipped_kept(
        self, digest: str, thread_id: str, campaign_id: str | None, owners: Sequence[str]
    ) -> ApprovalRecord | None:
        """The newest step of fingerprint ``digest`` kept in the conversation ``thread_id`` (of the campaign
        ``campaign_id``) that the owner skipped (decided by one of ``owners``, the sources of the owner's answers and
        runs) and that they have not taken back since by writing there: no run of theirs started in the conversation
        after it, and no message of theirs reached a run of it (a steer that vouches, into a run of the conversation or
        a session of the campaign; items 483, 484). None when there is none."""
        marks: str = ", ".join("?" for _ in owners)
        row = await self._one(
            "SELECT a.* FROM approvals AS a WHERE a.digest = ? AND a.scope_thread_id = ? AND a.scope_campaign_id IS ?"
            f" AND a.status = 'denied' AND a.kept_until IS NOT NULL AND a.decided_by IN ({marks})"
            " AND NOT EXISTS (SELECT 1 FROM runs AS o WHERE o.thread_id = a.scope_thread_id"
            f" AND o.created_at > a.decided_at AND o.source IN ({marks}))"
            " AND NOT EXISTS (SELECT 1 FROM events AS e JOIN runs AS s ON s.id = e.run_id"
            " WHERE (s.thread_id = a.scope_thread_id OR s.campaign_id = a.scope_campaign_id) AND e.kind = 'steer'"
            " AND e.ts > a.decided_at AND COALESCE(json_type(e.data, '$.vouches'), '') != 'false')"
            " ORDER BY a.decided_at DESC LIMIT 1",
            (digest, thread_id, campaign_id, *owners, *owners),
        )
        return None if row is None else _approval(row)

    async def kept_due(self, now: float) -> list[ApprovalRecord]:
        """The steps kept for the owner whose time is over at ``now``, still unanswered (item 482), oldest first."""
        rows = await self._all(
            "SELECT * FROM approvals WHERE status = 'left' AND kept_until IS NOT NULL AND kept_until <= ?"
            " ORDER BY kept_until",
            (now,),
        )
        return [_approval(row) for row in rows]

    async def create_decided_approval(
        self,
        run_id: str,
        tool: str,
        summary: str,
        severity: str,
        args: JsonObject,
        status: Literal["approved", "denied"],
        decided_by: str,
        note: str,
    ) -> ApprovalRecord:
        """Store a question that was answered without a person, in one step: it is never listed as pending."""
        now: float = time.time()
        record: ApprovalRecord = ApprovalRecord(
            id=new_id("apr"),
            run_id=run_id,
            created_at=now,
            decided_at=now,
            status=status,
            tool=tool,
            summary=summary,
            severity=severity,
            args=args,
            decided_by=decided_by,
            note=note,
            digest=None,
            kept_until=None,
            permit_until=None,
            used_at=None,
            scope_thread_id=None,
            scope_campaign_id=None,
        )
        await self._insert_approval(record)
        return record

    @_writing
    async def decide_approval(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, note: str
    ) -> ApprovalRecord:
        """Decide an approval that is still open (``pending`` or ``left``); a decided one keeps its decision."""
        await self._db.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, note = ?"
            " WHERE id = ? AND status IN ('pending', 'left')",
            (status, time.time(), decided_by, note, approval_id),
        )
        await self._db.commit()
        return await self.get_approval(approval_id)

    async def get_approval(self, approval_id: str) -> ApprovalRecord:
        row = await self._one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if row is None:
            raise NotFoundError("approval not found", {"approval_id": approval_id})
        return _approval(row)

    async def list_approvals(self, status: ApprovalStatus | None, limit: int) -> list[ApprovalRecord]:
        if status is None:
            rows = await self._all("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            rows = await self._all(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
            )
        return [_approval(row) for row in rows]

    @_writing
    async def add_draft(
        self, run_id: str, channel: DraftChannel, to: Sequence[str], cc: Sequence[str], subject: str, body: str
    ) -> DraftRecord:
        """Keep a draft a run wrote (item 314): it waits for the owner."""
        record: DraftRecord = DraftRecord(
            id=new_id("drf"),
            run_id=run_id,
            channel=channel,
            to=tuple(to),
            cc=tuple(cc),
            subject=subject,
            body=body,
            status="waiting",
            created_at=time.time(),
            decided_at=None,
            error=None,
        )
        await self._db.execute(
            "INSERT INTO drafts(id, run_id, channel, recipients, cc, subject, body, status, created_at, decided_at, "
            "error) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (
                record.id,
                run_id,
                channel,
                dumps_compact(list(record.to)),
                dumps_compact(list(record.cc)),
                subject,
                body,
                record.status,
                record.created_at,
            ),
        )
        await self._db.commit()
        return record

    async def get_draft(self, draft_id: str) -> DraftRecord:
        row = await self._one("SELECT * FROM drafts WHERE id = ?", (draft_id,))
        if row is None:
            raise NotFoundError("draft not found", {"draft_id": draft_id})
        return _draft(row)

    async def list_drafts(self, status: DraftStatus, limit: int) -> list[DraftRecord]:
        """The drafts in ``status``, newest first."""
        rows = await self._all(
            "SELECT * FROM drafts WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
        )
        return [_draft(row) for row in rows]

    @_writing
    async def claim_draft(
        self, draft_id: str, to: Sequence[str], cc: Sequence[str], subject: str, body: str
    ) -> DraftRecord:
        """Take a draft that waits, or whose last send failed, for sending, with the recipients and words the owner
        sends: it is ``sending`` from now on, so that a second press, here or on another screen, finds it taken."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE drafts SET status = 'sending', recipients = ?, cc = ?, subject = ?, body = ?, error = NULL"
            " WHERE id = ? AND status IN ('waiting', 'failed')",
            (dumps_compact(list(to)), dumps_compact(list(cc)), subject, body, draft_id),
        )
        await self._db.commit()
        record: DraftRecord = await self.get_draft(draft_id)
        if cursor.rowcount == 0:
            raise RunConflict(f"the draft is {record.status}, not waiting", {"draft_id": draft_id})
        return record

    @_writing
    async def settle_draft(self, draft_id: str, error: str | None) -> DraftRecord:
        """End the send of a draft: ``sent``, or ``failed`` with the application's ``error``."""
        await self._db.execute(
            "UPDATE drafts SET status = ?, decided_at = ?, error = ? WHERE id = ? AND status = 'sending'",
            ("sent" if error is None else "failed", time.time(), error, draft_id),
        )
        await self._db.commit()
        return await self.get_draft(draft_id)

    @_writing
    async def discard_draft(self, draft_id: str) -> DraftRecord:
        """Drop a draft that waits, or whose last send failed: nothing goes out."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE drafts SET status = 'discarded', decided_at = ? WHERE id = ? AND status IN ('waiting', 'failed')",
            (time.time(), draft_id),
        )
        await self._db.commit()
        record: DraftRecord = await self.get_draft(draft_id)
        if cursor.rowcount == 0:
            raise RunConflict(f"the draft is {record.status}, not waiting", {"draft_id": draft_id})
        return record

    @_writing
    async def fail_sending_drafts(self, error: str) -> list[DraftRecord]:
        """Mark ``failed`` with ``error`` the drafts a stopped daemon was sending, and return them: whether they went
        out is not known, so none is sent again by itself."""
        rows = await self._all("SELECT id FROM drafts WHERE status = 'sending'", ())
        ids: list[str] = [str(row["id"]) for row in rows]
        await self._db.execute(
            "UPDATE drafts SET status = 'failed', decided_at = ?, error = ? WHERE status = 'sending'",
            (time.time(), error),
        )
        await self._db.commit()
        return [await self.get_draft(draft_id) for draft_id in ids]

    async def list_left_questions(self, run_id: str) -> list[ApprovalRecord]:
        """The questions ``run_id`` left for the user that are not answered or skipped yet, oldest first."""
        rows = await self._all(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'left' ORDER BY created_at", (run_id,)
        )
        return [_approval(row) for row in rows]

    @_writing
    async def expire_pending_approvals(self, note: str) -> int:
        """The approvals still pending for runs that are no longer active expire, with ``note`` for why; those of an
        active run (one that a process that lives drives) wait on."""
        cursor = await self._db.execute(
            "UPDATE approvals SET status = 'expired', decided_at = ?, decided_by = 'system', note = ?"
            " WHERE status = 'pending' AND run_id NOT IN"
            " (SELECT id FROM runs WHERE status IN ('running', 'queued', 'blocked'))",
            (time.time(), note),
        )
        await self._db.commit()
        return cursor.rowcount

    # ----- shown pages -----------------------------------------------------------------------

    @_writing
    async def add_shown_page(self, run_id: str, title: str, html: str) -> ShownPage:
        """Keep a page that the run ``run_id`` shows, under an id that cannot be guessed (:data:`_PAGE_ID_BYTES`)."""
        page: ShownPage = ShownPage(
            id=f"page_{secrets.token_urlsafe(_PAGE_ID_BYTES)}",
            run_id=run_id,
            title=title,
            html=html,
            created_at=time.time(),
        )
        await self._db.execute(
            "INSERT INTO shown_pages(id, run_id, title, html, created_at) VALUES(?, ?, ?, ?, ?)",
            (page.id, page.run_id, page.title, page.html, page.created_at),
        )
        await self._db.commit()
        return page

    async def get_shown_page(self, page_id: str) -> ShownPage:
        row = await self._one("SELECT * FROM shown_pages WHERE id = ?", (page_id,))
        if row is None:
            raise NotFoundError("page not found", {"page_id": page_id})
        return ShownPage(
            id=row["id"], run_id=row["run_id"], title=row["title"], html=row["html"], created_at=row["created_at"]
        )

    # ----- rules -----------------------------------------------------------------------------

    async def list_rules(self) -> list[RuleRecord]:
        rows = await self._all("SELECT * FROM rules ORDER BY position, created_at", ())
        return [_rule(row) for row in rows]

    @_writing
    async def add_rule(self, behavior: Behavior, tool_glob: str, match_regex: str, description: str) -> RuleRecord:
        row = await self._one("SELECT COALESCE(MAX(position), 0) AS top FROM rules", ())
        position: int = (0 if row is None else int(row["top"])) + 1
        record: RuleRecord = RuleRecord(
            id=new_id("rul"),
            position=position,
            enabled=True,
            behavior=behavior,
            tool_glob=tool_glob,
            match_regex=match_regex,
            description=description,
            created_at=time.time(),
        )
        await self._db.execute(
            "INSERT INTO rules(id, position, enabled, behavior, tool_glob, match_regex, description, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.position,
                1,
                record.behavior,
                record.tool_glob,
                record.match_regex,
                record.description,
                record.created_at,
            ),
        )
        await self._db.commit()
        return record

    @_writing
    async def move_rule(self, rule_id: str, places: int) -> None:
        """Move the rule ``rule_id`` ``places`` places along the order the policy reads the rules in (a negative number
        toward the first), read here under the write lock, and number every rule 1 to n in that order, which also
        closes the gaps that deleted rules left."""
        rows = await self._all("SELECT id FROM rules ORDER BY position, created_at", ())
        order: list[str] = [str(row["id"]) for row in rows]
        if rule_id not in order:
            raise NotFoundError("rule not found", {"rule_id": rule_id})
        index: int = order.index(rule_id) + places
        if not 0 <= index < len(order):
            raise InvalidValueError(
                "a rule cannot move past either end of the order",
                {"rule_id": rule_id, "places": places, "rules": len(order)},
            )
        order.remove(rule_id)
        order.insert(index, rule_id)
        await self._db.executemany(
            "UPDATE rules SET position = ? WHERE id = ?", [(position, moved) for position, moved in enumerate(order, 1)]
        )
        await self._db.commit()

    @_writing
    async def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        await self._db.execute("UPDATE rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
        await self._db.commit()

    @_writing
    async def delete_rule(self, rule_id: str) -> None:
        await self._db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        await self._db.commit()

    # ----- queued messages ---------------------------------------------------------------------

    async def list_queued(self, thread_id: str) -> list[QueuedMessage]:
        """The messages waiting in a chat's queue, the next one first."""
        rows = await self._all("SELECT * FROM queued_messages WHERE thread_id = ? ORDER BY position", (thread_id,))
        return [_queued(row) for row in rows]

    async def get_queued(self, message_id: str) -> QueuedMessage:
        row = await self._one("SELECT * FROM queued_messages WHERE id = ?", (message_id,))
        if row is None:
            raise NotFoundError("queued message not found", {"message_id": message_id})
        return _queued(row)

    async def queue_held(self, thread_id: str) -> bool:
        """Whether a chat's queue waits for the owner to resume it (see :meth:`hold_queue`)."""
        return await self._one("SELECT 1 FROM queue_holds WHERE thread_id = ?", (thread_id,)) is not None

    async def queued_threads(self) -> list[str]:
        """The chats whose queue holds a message."""
        rows = await self._all("SELECT DISTINCT thread_id FROM queued_messages ORDER BY thread_id", ())
        return [str(row["thread_id"]) for row in rows]

    @_writing
    async def add_queued(
        self,
        thread_id: str,
        text: str,
        attachments: Sequence[str],
        mode: RunMode,
        model: str | None,
        effort: Effort | None,
        speed: Speed,
        once: OneMessage | None,
        picks: RunPicks,
        approvals: ApprovalMode | None,
        goal_mode: bool | None,
        agent_id: str | None,
        source: str,
    ) -> QueuedMessage:
        """Put a message at the end of a chat's queue."""
        row = await self._one(
            "SELECT COALESCE(MAX(position), 0) AS top FROM queued_messages WHERE thread_id = ?", (thread_id,)
        )
        record: QueuedMessage = QueuedMessage(
            id=new_id("que"),
            thread_id=thread_id,
            position=(0 if row is None else int(row["top"])) + 1,
            text=text,
            attachments=tuple(attachments),
            mode=mode,
            model=model,
            effort=effort,
            speed=speed,
            once=once,
            picks=picks,
            approvals=approvals,
            goal_mode=goal_mode,
            agent_id=agent_id,
            source=source,
            created_at=time.time(),
        )
        await self._insert_queued(record)
        await self._db.commit()
        return record

    async def _insert_queued(self, record: QueuedMessage) -> None:
        """Store ``record`` in its chat's queue; the caller commits."""
        await self._db.execute(
            "INSERT INTO queued_messages(id, thread_id, position, text, attachments, mode, model, effort, speed, once,"
            " picks, approvals, goal_mode, agent_id, source, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.thread_id,
                record.position,
                record.text,
                ",".join(record.attachments),
                record.mode,
                record.model,
                record.effort,
                record.speed,
                record.once,
                None if record.picks == NO_PICKS else record.picks.model_dump_json(),
                record.approvals,
                None if record.goal_mode is None else int(record.goal_mode),
                record.agent_id,
                record.source,
                record.created_at,
            ),
        )

    async def _not_waiting(self, message_id: str) -> RunConflict | NotFoundError:
        """Why the queued message ``message_id`` cannot be changed: it is starting as its chat's next run
        (:meth:`claim_queued`), or it is in no queue."""
        if await self._one("SELECT 1 FROM queue_claims WHERE message_id = ?", (message_id,)) is not None:
            return RunConflict("the message is starting as its chat's next run", {"message_id": message_id})
        return NotFoundError("queued message not found", {"message_id": message_id})

    @_writing
    async def edit_queued(self, message_id: str, text: str) -> None:
        """Change the text of a queued message; one starting as its chat's next run is refused (``RunConflict``)."""
        cursor = await self._db.execute(
            "UPDATE queued_messages SET text = ? WHERE id = ? AND id NOT IN (SELECT message_id FROM queue_claims)",
            (text, message_id),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise await self._not_waiting(message_id)

    @_writing
    async def edit_queued_with_files(self, message_id: str, text: str, attachments: Sequence[str]) -> None:
        """Change the text of a queued message and the files it carries (item 376); one starting as its chat's next run
        is refused (``RunConflict``)."""
        cursor = await self._db.execute(
            "UPDATE queued_messages SET text = ?, attachments = ?"
            " WHERE id = ? AND id NOT IN (SELECT message_id FROM queue_claims)",
            (text, ",".join(attachments), message_id),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise await self._not_waiting(message_id)

    @_writing
    async def delete_queued(self, message_id: str) -> None:
        """Take a message out of its chat's queue; one starting as its chat's next run is refused (``RunConflict``)."""
        cursor = await self._db.execute(
            "DELETE FROM queued_messages WHERE id = ? AND id NOT IN (SELECT message_id FROM queue_claims)",
            (message_id,),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise await self._not_waiting(message_id)

    @_writing
    async def take_queued(self, message_id: str) -> QueuedMessage:
        """Take a message out of its chat's queue as it is now, in one write, to send it to the run at work; one
        starting as its chat's next run is refused (``RunConflict``)."""
        async with self._db.execute(
            "DELETE FROM queued_messages WHERE id = ? AND id NOT IN (SELECT message_id FROM queue_claims) RETURNING *",
            (message_id,),
        ) as cursor:
            row: aiosqlite.Row | None = await cursor.fetchone()
        await self._db.commit()
        if row is None:
            raise await self._not_waiting(message_id)
        return _queued(row)

    @_writing
    async def put_back_queued(self, message: QueuedMessage) -> None:
        """Put a message taken out of its chat's queue (:meth:`take_queued`) back where it was."""
        await self._insert_queued(message)
        await self._db.commit()

    @_writing
    async def claim_queued(self, thread_id: str, owner: str) -> QueuedMessage | None:
        """Claim the first message of a chat's queue for the chat's next run, for the process ``owner``, in one write:
        unless the queue is held, a run of any process works on the chat, or a message of it is claimed already. The
        claimed message stays in the queue, where it cannot be edited, taken out or steered, until its run has started
        (:meth:`drop_claimed`) or could not start (:meth:`hold_claimed`). Returns it, or ``None`` when none was
        claimed."""
        working: tuple[RunStatus, ...] = tuple(sorted(WORKING_STATUSES))
        async with self._db.execute(
            "INSERT INTO queue_claims(thread_id, message_id, owner, claimed_at)"
            " SELECT thread_id, id, ?, ? FROM queued_messages WHERE thread_id = ?"
            " AND NOT EXISTS (SELECT 1 FROM queue_holds WHERE thread_id = ?)"
            " AND NOT EXISTS (SELECT 1 FROM queue_claims WHERE thread_id = ?)"
            f" AND NOT EXISTS (SELECT 1 FROM runs WHERE thread_id = ? AND status IN ({','.join('?' * len(working))}))"
            " ORDER BY position LIMIT 1 RETURNING message_id",
            (owner, time.time(), thread_id, thread_id, thread_id, thread_id, *working),
        ) as cursor:
            claimed: aiosqlite.Row | None = await cursor.fetchone()
        await self._db.commit()
        if claimed is None:
            return None
        row: aiosqlite.Row | None = await self._one(
            "SELECT * FROM queued_messages WHERE id = ?", (str(claimed["message_id"]),)
        )
        if row is None:
            raise StoreError("the claimed message is not in the queue", {"thread_id": thread_id})
        return _queued(row)

    @_writing
    async def drop_claimed(self, thread_id: str) -> None:
        """The claimed message of a chat has started its run (:meth:`claim_queued`): it leaves the queue, and its claim
        ends, in one write."""
        await self._db.execute(
            "DELETE FROM queued_messages WHERE id IN (SELECT message_id FROM queue_claims WHERE thread_id = ?)",
            (thread_id,),
        )
        await self._db.execute("DELETE FROM queue_claims WHERE thread_id = ?", (thread_id,))
        await self._db.commit()

    @_writing
    async def hold_claimed(self, thread_id: str) -> None:
        """The claimed message of a chat could not start its run (:meth:`claim_queued`): its claim ends, it stays first
        in the queue, and the queue is held until the owner resumes it, in one write."""
        await self._db.execute("DELETE FROM queue_claims WHERE thread_id = ?", (thread_id,))
        await self._db.execute(
            "INSERT OR IGNORE INTO queue_holds(thread_id, held_at) VALUES(?, ?)", (thread_id, time.time())
        )
        await self._db.commit()

    async def claim_owners(self) -> list[str]:
        """The processes that claim a queued message (:meth:`claim_queued`), each once."""
        rows = await self._all("SELECT DISTINCT owner FROM queue_claims ORDER BY owner", ())
        return [str(row["owner"]) for row in rows]

    @_writing
    async def hold_claims_of(self, owners: Sequence[str]) -> int:
        """The claims of ``owners``, processes that are gone, end, and the queue of each of their chats is held, in one
        write. A claimed message whose run the process made before it went (a run of that process on the chat, made at
        the claim or after) leaves the queue, as it started; any other stays first, for the owner to resume. Returns
        how many claims ended."""
        marks: str = ",".join("?" * len(owners))
        await self._db.execute(
            "DELETE FROM queued_messages WHERE id IN (SELECT claim.message_id FROM queue_claims AS claim"
            f" WHERE claim.owner IN ({marks}) AND EXISTS (SELECT 1 FROM runs WHERE runs.thread_id = claim.thread_id"
            " AND runs.owner = claim.owner AND runs.created_at >= claim.claimed_at))",
            tuple(owners),
        )
        async with self._db.execute(
            f"DELETE FROM queue_claims WHERE owner IN ({marks}) RETURNING thread_id",
            tuple(owners),
        ) as cursor:
            chats: list[str] = [str(row["thread_id"]) for row in await cursor.fetchall()]
        held_at: float = time.time()
        await self._db.executemany(
            "INSERT OR IGNORE INTO queue_holds(thread_id, held_at) VALUES(?, ?)", [(chat, held_at) for chat in chats]
        )
        await self._db.commit()
        return len(chats)

    @_writing
    async def order_queue(self, thread_id: str, message_ids: Sequence[str]) -> None:
        """Put a chat's queue in the order of ``message_ids``, which must name each of its messages once."""
        rows = await self._all("SELECT id FROM queued_messages WHERE thread_id = ?", (thread_id,))
        queued: list[str] = sorted(str(row["id"]) for row in rows)
        if sorted(message_ids) != queued:
            given: JsonArray = list(message_ids)
            known: JsonArray = list(queued)
            raise InvalidValueError(
                "the order must name each message of the queue once",
                {"thread_id": thread_id, "given": given, "queued": known},
            )
        await self._db.executemany(
            "UPDATE queued_messages SET position = ? WHERE id = ?",
            [(position, message_id) for position, message_id in enumerate(message_ids, start=1)],
        )
        await self._db.commit()

    @_writing
    async def hold_queue(self, thread_id: str) -> None:
        """Keep a chat's queue from starting its next message until :meth:`release_queue`."""
        await self._db.execute(
            "INSERT OR IGNORE INTO queue_holds(thread_id, held_at) VALUES(?, ?)", (thread_id, time.time())
        )
        await self._db.commit()

    @_writing
    async def release_queue(self, thread_id: str) -> None:
        await self._db.execute("DELETE FROM queue_holds WHERE thread_id = ?", (thread_id,))
        await self._db.commit()

    # ----- push subscriptions ------------------------------------------------------------------

    @_writing
    async def add_push_subscription(self, endpoint: str, p256dh: str, auth: str) -> PushSubscription:
        """Keep a browser's subscription; the same endpoint again takes the new keys."""
        record: PushSubscription = PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth, created_at=time.time())
        await self._db.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth",
            (record.endpoint, record.p256dh, record.auth, record.created_at),
        )
        await self._db.commit()
        return record

    async def list_push_subscriptions(self) -> list[PushSubscription]:
        rows = await self._all("SELECT * FROM push_subscriptions ORDER BY created_at", ())
        return [
            PushSubscription(
                endpoint=str(row["endpoint"]),
                p256dh=str(row["p256dh"]),
                auth=str(row["auth"]),
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    @_writing
    async def delete_push_subscription(self, endpoint: str) -> None:
        await self._db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await self._db.commit()

    # ----- memories --------------------------------------------------------------------------

    @_writing
    async def add_memory(
        self,
        content: str,
        tags: Sequence[str],
        source_run: str | None,
        agent_id: str | None,
        vector: Sequence[float] | None,
        tainted: bool,
    ) -> MemoryRecord:
        """Add a note that holds until it is changed; ``tainted`` says its run had read content third parties can
        author."""
        record: MemoryRecord = await self._insert_memory(content, tags, source_run, agent_id, vector, None, tainted)
        await self._db.commit()
        return record

    async def add_memory_once(
        self,
        content: str,
        tags: Sequence[str],
        source_run: str | None,
        agent_id: str | None,
        vector: Sequence[float] | None,
        expires_at: float | None,
        tainted: bool,
    ) -> tuple[MemoryRecord, bool]:
        """Add a note unless one that a run of ``agent_id`` is handed has this text already (saved meanwhile, in this
        process or another; :meth:`find_memory_by_content`): the look and the add are one transaction. Returns the note
        with the text and whether it was added. ``expires_at`` is when it stops holding, ``None`` for never;
        ``tainted`` says the saving run had read content third parties can author. A unit tried again after a lock
        returns what its last try stored (item 351: the first try's note, rolled back, came back)."""

        async def statements() -> tuple[MemoryRecord, bool]:
            row: aiosqlite.Row | None = await self._one(_SAME_TEXT, (content, agent_id))
            if row is not None:
                return _memory(row), False
            return (
                await self._insert_memory(content, tags, source_run, agent_id, vector, expires_at, tainted),
                True,
            )

        return await self._unit(statements)

    async def _insert_memory(
        self,
        content: str,
        tags: Sequence[str],
        source_run: str | None,
        agent_id: str | None,
        vector: Sequence[float] | None,
        expires_at: float | None,
        tainted: bool,
    ) -> MemoryRecord:
        """The statements that add a note, in the caller's transaction."""
        now: float = time.time()
        record: MemoryRecord = MemoryRecord(
            id=new_id("mem"),
            content=content,
            tags=tuple(tags),
            created_at=now,
            updated_at=now,
            source_run=source_run,
            agent_id=agent_id,
            used_count=0,
            last_used_at=None,
            expires_at=expires_at,
            replaced_by=None,
            tainted=tainted,
        )
        tag_text: str = ",".join(record.tags)
        await self._db.execute(
            "INSERT INTO memories(id, content, tags, created_at, updated_at, source_run, agent_id, expires_at,"
            " tainted) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.id, content, tag_text, now, now, source_run, agent_id, expires_at, int(tainted)),
        )
        await self._db.execute(
            "INSERT INTO memories_fts(id, content, tags) VALUES(?, ?, ?)",
            (record.id, fold_search(content), fold_search(tag_text)),
        )
        if vector is not None:
            await self._db.execute(
                "INSERT INTO memory_vectors(id, dim, vec) VALUES(?, ?, ?)",
                (record.id, len(vector), pack_vector(vector)),
            )
        return record

    @_writing
    async def update_memory(
        self,
        memory_id: str,
        content: str,
        tags: Sequence[str],
        vector: Sequence[float] | None,
        source_run: str | None,
        tainted: bool,
    ) -> MemoryRecord:
        """Rewrite a memory's text and tags, written now by ``source_run``. A tainted run's rewrite marks the note
        tainted; a clean one leaves the mark a note had."""
        return await self._rewrite_memory(memory_id, content, tags, vector, source_run, "MAX(tainted, ?)", int(tainted))

    @_writing
    async def update_memory_vouched(
        self,
        memory_id: str,
        content: str,
        tags: Sequence[str],
        vector: Sequence[float] | None,
        source_run: str | None,
    ) -> MemoryRecord:
        """The user's rewrite of a memory's text and tags, as :meth:`update_memory`; in the same write the mark of
        having been saved while reading outside content comes off, as the user vouches for the text they save (item
        384)."""
        return await self._rewrite_memory(memory_id, content, tags, vector, source_run, "?", 0)

    async def _rewrite_memory(
        self,
        memory_id: str,
        content: str,
        tags: Sequence[str],
        vector: Sequence[float] | None,
        source_run: str | None,
        mark: Literal["MAX(tainted, ?)", "?"],
        tainted: int,
    ) -> MemoryRecord:
        """The statements of a rewrite, committed together; ``mark`` is how the note's mark is set from ``tainted``."""
        tag_text: str = ",".join(tags)
        updated = await self._db.execute(
            f"UPDATE memories SET content = ?, tags = ?, updated_at = ?, source_run = ?, tainted = {mark} WHERE id = ?",
            (content, tag_text, time.time(), source_run, tainted, memory_id),
        )
        if updated.rowcount == 0:
            # The note was deleted while its rewrite waited on the embedding (item 556): nothing of it is written, as
            # an index row or a vector for a note that is not there would take a place in the next search.
            raise NotFoundError("memory not found", {"memory_id": memory_id})
        await self._db.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
        await self._db.execute(
            "INSERT INTO memories_fts(id, content, tags) VALUES(?, ?, ?)",
            (memory_id, fold_search(content), fold_search(tag_text)),
        )
        await self._db.execute("DELETE FROM memory_vectors WHERE id = ?", (memory_id,))
        if vector is not None:
            await self._db.execute(
                "INSERT INTO memory_vectors(id, dim, vec) VALUES(?, ?, ?)",
                (memory_id, len(vector), pack_vector(vector)),
            )
        await self._db.commit()
        return await self.get_memory(memory_id)

    @_writing
    async def set_memory_tags(self, memory_id: str, tags: Sequence[str]) -> MemoryRecord:
        """Replace the tags of a memory; its text, its vector and ``updated_at`` stay: what the note says did not
        change, so a retag (the nightly ``rest``, merged tags) makes it no newer than its correction, nor a change a
        running run is handed (item 157)."""
        tag_text: str = ",".join(tags)
        await self._db.execute("UPDATE memories SET tags = ? WHERE id = ?", (tag_text, memory_id))
        await self._db.execute("UPDATE memories_fts SET tags = ? WHERE id = ?", (fold_search(tag_text), memory_id))
        await self._db.commit()
        return await self.get_memory(memory_id)

    @_writing
    async def delete_memory(self, memory_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await self._db.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
        await self._db.execute("DELETE FROM memory_vectors WHERE id = ?", (memory_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_memory(self, memory_id: str) -> MemoryRecord:
        row = await self._one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        if row is None:
            raise NotFoundError("memory not found", {"memory_id": memory_id})
        return _memory(row)

    async def get_memories(self, ids: Sequence[str]) -> list[MemoryRecord]:
        if not ids:
            return []
        marks: str = ",".join("?" for _ in ids)
        rows = await self._all(f"SELECT * FROM memories WHERE id IN ({marks})", tuple(ids))
        by_id: dict[str, MemoryRecord] = {record.id: record for record in (_memory(row) for row in rows)}
        return [by_id[identifier] for identifier in ids if identifier in by_id]

    async def list_memories(self, limit: int, offset: int) -> list[MemoryRecord]:
        rows = await self._all("SELECT * FROM memories ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset))
        return [_memory(row) for row in rows]

    async def count_memories(self) -> int:
        row = await self._one("SELECT COUNT(*) AS n FROM memories", ())
        return 0 if row is None else int(row["n"])

    async def search_memories_terms(
        self, terms: Sequence[str], limit: int, among: AwakeNotes | None = None
    ) -> list[str]:
        """Ids of the memories that contain any of ``terms`` (folded words, see ``fts_query``), ranked by BM25, best
        first, at most ``limit``: of every note, or of the ones ``among`` allows, which the cut is made among (item
        557), so that notes that rest, ran out of time or are another agent's do not use up the places."""
        if not terms:
            return []
        allowed, bound = ("", ()) if among is None else _awake(among)
        rows = await self._all(
            f"SELECT id FROM memories_fts WHERE memories_fts MATCH ?{allowed} ORDER BY bm25(memories_fts) LIMIT ?",
            (fts_query(terms), *bound, limit),
        )
        return [str(row["id"]) for row in rows]

    async def memory_term_counts(self, terms: Sequence[str]) -> dict[str, int]:
        """In how many memories each of ``terms`` occurs."""
        counts: dict[str, int] = {}
        for term in terms:
            row = await self._one(
                "SELECT count(*) AS notes FROM memories_fts WHERE memories_fts MATCH ?", (fts_query([term]),)
            )
            counts[term] = 0 if row is None else int(row["notes"])
        return counts

    async def all_memory_vectors(self, among: AwakeNotes | None = None) -> list[tuple[str, tuple[float, ...]]]:
        """The embeddings of every note, or of the ones ``among`` allows (as :meth:`search_memories_terms`), and of no
        vector that has no note (item 557)."""
        allowed, bound = ("", ()) if among is None else _awake(among)
        rows = await self._all(f"SELECT id, vec FROM memory_vectors WHERE 1 = 1{allowed}", bound)
        return [(str(row["id"]), unpack_vector(bytes(row["vec"]))) for row in rows]

    async def help_vectors(self, model: str) -> dict[str, tuple[float, ...]]:
        """The embeddings kept of the texts help is ranked by (item 404), by text, those ``model`` made."""
        rows = await self._all("SELECT text, vec FROM help_vectors WHERE model = ?", (model,))
        return {str(row["text"]): unpack_vector(bytes(row["vec"])) for row in rows}

    async def help_vectors_of(self, model: str, texts: Sequence[str]) -> dict[str, tuple[float, ...]]:
        """The embeddings kept of the help texts ``texts``, those ``model`` made, by text; a text with none is left
        out."""
        found: dict[str, tuple[float, ...]] = {}
        for start in range(0, len(texts), _HELP_VECTORS_ASKED):
            chunk: list[str] = list(texts[start : start + _HELP_VECTORS_ASKED])
            marks: str = ", ".join("?" for _ in chunk)
            rows = await self._all(
                f"SELECT text, vec FROM help_vectors WHERE model = ? AND text IN ({marks})", (model, *chunk)
            )
            found.update((str(row["text"]), unpack_vector(bytes(row["vec"]))) for row in rows)
        return found

    @_writing
    async def add_help_vectors(self, model: str, vectors: Sequence[tuple[str, Sequence[float]]], now: float) -> None:
        """Keep the embeddings ``model`` made of help texts, each by its text and in use as of ``now``; a text
        embedded again takes the new one."""
        await self._db.executemany(
            "INSERT INTO help_vectors(model, text, dim, vec, used_at) VALUES(?, ?, ?, ?, ?) ON CONFLICT(model, text)"
            " DO UPDATE SET dim = excluded.dim, vec = excluded.vec, used_at = excluded.used_at",
            [(model, text, len(vector), pack_vector(vector), now) for text, vector in vectors],
        )
        await self._db.commit()

    @_writing
    async def touch_help_vectors(self, model: str, texts: Sequence[str], now: float) -> None:
        """The help texts of ``texts`` are in use as of ``now``: a search asked for them."""
        await self._db.executemany(
            "UPDATE help_vectors SET used_at = ? WHERE model = ? AND text = ?", [(now, model, text) for text in texts]
        )
        await self._db.commit()

    @_writing
    async def prune_help_vectors(self, model: str, before: float) -> int:
        """Drop the help vectors no search asked for since ``before``, and those of any model but ``model``; returns
        how many went."""
        cursor = await self._db.execute("DELETE FROM help_vectors WHERE model != ? OR used_at < ?", (model, before))
        await self._db.commit()
        return int(cursor.rowcount)

    async def find_memory_by_content(self, content: str, agent_id: str | None) -> MemoryRecord | None:
        """The note with this text that a run of ``agent_id`` (``None`` for Dot) is handed: the agent's own first, else
        Dot's; another agent's note with the same text is not the saver's to merge into."""
        row = await self._one(_SAME_TEXT, (content, agent_id))
        return None if row is None else _memory(row)

    @_writing
    async def wake_memory(
        self, memory_id: str, tags: Sequence[str], when: float, expires_at: float | None
    ) -> MemoryRecord:
        """Give a note ``tags`` and write it anew at ``when`` (its ``updated_at``), holding until ``expires_at``: a
        resting lesson learnt again is awake, its rest is counted from now, and no newer note replaces it. Its text and
        vector stay."""
        tag_text: str = ",".join(tags)
        await self._db.execute(
            "UPDATE memories SET tags = ?, updated_at = ?, expires_at = ?, replaced_by = NULL WHERE id = ?",
            (tag_text, when, expires_at, memory_id),
        )
        await self._db.execute("UPDATE memories_fts SET tags = ? WHERE id = ?", (fold_search(tag_text), memory_id))
        await self._db.commit()
        return await self.get_memory(memory_id)

    @_writing
    async def retire_memory(self, memory_id: str, tags: Sequence[str], replaced_by: str) -> MemoryRecord:
        """Put a note aside for the newer note ``replaced_by`` (item 306): it rests with ``tags`` (which hold ``rest``)
        and says what replaced it; its text, its vector and ``updated_at`` stay, as with :meth:`set_memory_tags`."""
        tag_text: str = ",".join(tags)
        await self._db.execute(
            "UPDATE memories SET tags = ?, replaced_by = ? WHERE id = ?", (tag_text, replaced_by, memory_id)
        )
        await self._db.execute("UPDATE memories_fts SET tags = ? WHERE id = ?", (fold_search(tag_text), memory_id))
        await self._db.commit()
        return await self.get_memory(memory_id)

    async def memories_with_tag(self, tag: str, limit: int) -> list[MemoryRecord]:
        """The most recently changed memories carrying ``tag`` (tags never contain commas)."""
        rows = await self._all(
            "SELECT * FROM memories WHERE instr(',' || tags || ',', ?) > 0 ORDER BY updated_at DESC LIMIT ?",
            (f",{tag},", limit),
        )
        return [_memory(row) for row in rows]

    async def agent_memories(self, agent_id: str, limit: int) -> list[MemoryRecord]:
        """The most recently changed notes that are the agent's own (item 94)."""
        rows = await self._all(
            "SELECT * FROM memories WHERE agent_id = ? ORDER BY updated_at DESC LIMIT ?", (agent_id, limit)
        )
        return [_memory(row) for row in rows]

    async def user_memories(self, limit: int) -> list[MemoryRecord]:
        """The most recently changed notes the user wrote (no run saved them)."""
        rows = await self._all(
            "SELECT * FROM memories WHERE source_run IS NULL ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [_memory(row) for row in rows]

    async def memories_changed_since(self, since: float, run_id: str) -> list[MemoryRecord]:
        """Notes written or changed after ``since`` that the run ``run_id`` did not save itself, oldest first."""
        rows = await self._all(
            "SELECT * FROM memories WHERE updated_at > ? AND (source_run IS NULL OR source_run != ?) "
            "ORDER BY updated_at",
            (since, run_id),
        )
        return [_memory(row) for row in rows]

    async def handed_memory_ids(self, run_id: str) -> set[str]:
        """The notes run ``run_id`` was handed so far, however it was handed them (item 308)."""
        rows = await self._all("SELECT DISTINCT memory_id FROM memory_uses WHERE run_id = ?", (run_id,))
        return {str(row["memory_id"]) for row in rows}

    async def handed_in_thread(self, thread_id: str, run_id: str) -> set[str]:
        """The notes the runs of thread ``thread_id`` other than ``run_id`` were handed, however they were handed them:
        what a chat has recalled before one of its runs (item 335). A run a rewind took out of the chat (item 282) is no
        longer the chat's, and neither are its notes."""
        rows = await self._all(
            "SELECT DISTINCT memory_uses.memory_id AS memory_id FROM memory_uses"
            " JOIN runs ON runs.id = memory_uses.run_id"
            " WHERE runs.thread_id = ? AND memory_uses.run_id != ? AND runs.rewound_at IS NULL",
            (thread_id, run_id),
        )
        return {str(row["memory_id"]) for row in rows}

    @_writing
    async def mark_memories_used(self, run_id: str, memory_ids: Sequence[str], when: float, how: MemoryUseHow) -> None:
        """Count one more use of each note, at ``when``, and record that run ``run_id`` was handed it (``how``: at its
        start, by a search, as a changed note, or as a lesson of a site it reached); the note's ``updated_at`` stays.
        The use is scored once the run ends (:meth:`close_memory_uses`)."""
        if not memory_ids:
            return
        marks: str = ",".join("?" for _ in memory_ids)
        await self._db.execute(
            f"UPDATE memories SET used_count = used_count + 1, last_used_at = ? WHERE id IN ({marks})",
            (when, *memory_ids),
        )
        await self._db.executemany(
            "INSERT INTO memory_uses(run_id, memory_id, handed_at, how, status) VALUES(?, ?, ?, ?, NULL)",
            [(run_id, memory_id, when, how) for memory_id in memory_ids],
        )
        await self._db.commit()

    async def owner_saved_memory_ids(self) -> set[str]:
        """The notes the owner saved as lessons from a run's page (a ``lesson_saved`` event by the owner, which a prune
        leaves)."""
        rows = await self._all(
            f"SELECT json_extract(data, '$.memory_id') AS memory_id FROM events WHERE {_OWNER_SAVED_LESSON}", ()
        )
        return {str(row["memory_id"]) for row in rows if row["memory_id"] is not None}

    @_writing
    async def close_memory_uses(self, run_id: str, status: RunStatus) -> None:
        """Score the notes run ``run_id`` was handed with how it ended."""
        await self._score_memory_uses(run_id, status)
        await self._db.commit()

    async def _score_memory_uses(self, run_id: str, status: RunStatus) -> None:
        await self._db.execute(
            "UPDATE memory_uses SET status = ? WHERE run_id = ? AND status IS NULL", (status, run_id)
        )

    async def memory_scores(self, memory_ids: Sequence[str], since: float) -> dict[str, MemoryScore]:
        """How the runs ended that were handed each of ``memory_ids`` since ``since`` (``0.0`` for ever); a note no
        run was handed is absent."""
        return await self._memory_scores(memory_ids, since, "", ())

    async def agent_memory_scores(
        self, memory_ids: Sequence[str], since: float, agent_id: str
    ) -> dict[str, MemoryScore]:
        """Like :meth:`memory_scores`, counting only the runs of the agent ``agent_id`` (item 94: an agent's scores
        are its own)."""
        return await self._memory_scores(
            memory_ids, since, " AND run_id IN (SELECT id FROM runs WHERE agent_id = ?)", (agent_id,)
        )

    async def _memory_scores(
        self, memory_ids: Sequence[str], since: float, extra: str, params: tuple[str, ...]
    ) -> dict[str, MemoryScore]:
        if not memory_ids:
            return {}
        marks: str = ",".join("?" for _ in memory_ids)
        rows = await self._all(
            "SELECT memory_id, COUNT(DISTINCT run_id) AS handed,"
            " COUNT(DISTINCT CASE WHEN status = 'done' THEN run_id END) AS done,"
            " COUNT(DISTINCT CASE WHEN status = 'failed' THEN run_id END) AS failed"
            f" FROM memory_uses WHERE memory_id IN ({marks}) AND handed_at >= ?{extra} GROUP BY memory_id",
            (*memory_ids, since, *params),
        )
        return {
            str(row["memory_id"]): MemoryScore(
                handed=int(row["handed"]), done=int(row["done"]), failed=int(row["failed"])
            )
            for row in rows
        }

    @_writing
    async def clear_memory_vectors(self) -> None:
        await self._db.execute("DELETE FROM memory_vectors")
        await self._db.commit()

    async def memories_without_vectors(self, limit: int) -> list[MemoryRecord]:
        rows = await self._all(
            "SELECT m.* FROM memories m LEFT JOIN memory_vectors v ON v.id = m.id WHERE v.id IS NULL LIMIT ?",
            (limit,),
        )
        return [_memory(row) for row in rows]

    @_writing
    async def set_memory_vector(self, memory_id: str, vector: Sequence[float]) -> None:
        await self._db.execute(
            "INSERT INTO memory_vectors(id, dim, vec) VALUES(?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET dim = excluded.dim, vec = excluded.vec",
            (memory_id, len(vector), pack_vector(vector)),
        )
        await self._db.commit()

    # ----- schedules -------------------------------------------------------------------------

    @_writing
    async def add_schedule(
        self,
        name: str,
        cron: str | None,
        run_at: float | None,
        goal: str,
        mode: RunMode,
        next_run_at: float | None,
        options: ScheduleOptions,
        thread_id: str | None,
        enabled: bool,
    ) -> ScheduleRecord:
        """A new schedule, switched on or off (``enabled``; a template's is added switched off, item 464, with no
        ``next_run_at``); ``thread_id`` is the chat its first firing goes on in when it fires in the same chat (a
        schedule a run made, item 291), ``None`` for none yet."""
        record: ScheduleRecord = ScheduleRecord(
            id=new_id("sch"),
            name=name,
            cron=cron,
            run_at=run_at,
            goal=goal,
            mode=mode,
            enabled=enabled,
            created_at=time.time(),
            last_run_at=None,
            next_run_at=next_run_at,
            thread_id=thread_id,
            last_status="",
            options=options,
            runs_failed_in_row=0,
        )
        await self._db.execute(
            "INSERT INTO schedules(id, name, cron, run_at, goal, mode, enabled, created_at, last_run_at, next_run_at,"
            " thread_id, last_status, model, effort, approvals, goal_mode, timezone, history_runs, same_chat, speed,"
            " stop_after_failures, notify, agent_id)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                name,
                cron,
                run_at,
                goal,
                mode,
                int(enabled),
                record.created_at,
                None,
                next_run_at,
                thread_id,
                "",
                *_option_values(options),
            ),
        )
        await self._db.commit()
        return record

    @_writing
    async def update_schedule(
        self,
        schedule_id: str,
        name: str,
        cron: str | None,
        run_at: float | None,
        goal: str,
        mode: RunMode,
        options: ScheduleOptions,
        enabled: bool,
        next_run_at: float | None,
    ) -> ScheduleRecord:
        """Replace what a schedule does and when; its history (last run, thread, status) stays, its row of failed runs
        starts afresh, and one switched on no longer says it was switched off for them (item 458), as the form's switch
        is this way and not ``enable``'s."""
        on: int = 1 if enabled else 0
        await self._db.execute(
            "UPDATE schedules SET name = ?, cron = ?, run_at = ?, goal = ?, mode = ?, model = ?, effort = ?,"
            " approvals = ?, goal_mode = ?, timezone = ?, history_runs = ?, same_chat = ?, speed = ?,"
            " stop_after_failures = ?, notify = ?, agent_id = ?, enabled = ?, next_run_at = ?, runs_failed_in_row = 0,"
            " last_status = CASE WHEN ? = 1 AND last_status LIKE 'switched off:%' THEN '' ELSE last_status END"
            " WHERE id = ?",
            (name, cron, run_at, goal, mode, *_option_values(options), on, next_run_at, on, schedule_id),
        )
        await self._db.commit()
        return await self.get_schedule(schedule_id)

    async def get_schedule(self, schedule_id: str) -> ScheduleRecord:
        row = await self._one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        if row is None:
            raise NotFoundError("schedule not found", {"schedule_id": schedule_id})
        return _schedule(row)

    async def list_schedules(self) -> list[ScheduleRecord]:
        rows = await self._all("SELECT * FROM schedules ORDER BY created_at", ())
        return [_schedule(row) for row in rows]

    async def due_schedules(self, now: float) -> list[ScheduleRecord]:
        rows = await self._all(
            "SELECT * FROM schedules WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ? ORDER BY "
            "next_run_at",
            (now,),
        )
        return [_schedule(row) for row in rows]

    @_writing
    async def record_schedule_fire(
        self, schedule_id: str, fired_at: float, next_run_at: float | None, thread_id: str, status: str
    ) -> None:
        await self._db.execute(
            "UPDATE schedules SET last_run_at = ?, next_run_at = ?, thread_id = ?, last_status = ?,"
            " enabled = CASE WHEN ? IS NULL THEN 0 ELSE enabled END WHERE id = ?",
            (fired_at, next_run_at, thread_id, status, next_run_at, schedule_id),
        )
        await self._db.commit()

    async def schedule_of_thread(self, thread_id: str) -> str | None:
        """The schedule whose chat ``thread_id`` is (item 257), None for a chat of no schedule or one not there."""
        row = await self._one("SELECT schedule_id FROM threads WHERE id = ?", (thread_id,))
        return None if row is None else row["schedule_id"]

    @_writing
    async def link_schedule_thread(self, thread_id: str, schedule_id: str) -> None:
        """Mark the chat ``thread_id`` as one of the schedule ``schedule_id`` (item 257), whose next firings read the
        runs it started there."""
        await self._db.execute("UPDATE threads SET schedule_id = ? WHERE id = ?", (schedule_id, thread_id))
        await self._db.commit()

    async def schedule_firings(self, schedule_id: str, source: str, limit: int) -> list[ScheduleFiring]:
        """The last ``limit`` runs of ``source`` (the scheduler's) in the chats of the schedule ``schedule_id``, newest
        first, one at work among them, each with the title of its chat (item 455): its firings, at its time and by
        hand, without the runs they delegated or the owner's follow-ups."""
        rows = await self._all(
            "SELECT r.*, t.title AS chat_title FROM runs r JOIN threads t ON t.id = r.thread_id"
            " WHERE t.schedule_id = ? AND r.source = ? AND r.parent_run_id IS NULL"
            " ORDER BY r.created_at DESC LIMIT ?",
            (schedule_id, source, limit),
        )
        return [ScheduleFiring(run=_run(row), chat_title=str(row["chat_title"])) for row in rows]

    @_writing
    async def record_schedule_hand_run(self, schedule_id: str, started_at: float, thread_id: str) -> None:
        """A run of the schedule ``schedule_id`` the owner started by hand at ``started_at`` in the chat ``thread_id``
        (item 455): its last run and its chat, and nothing of when it fires next nor of whether it is switched on,
        which :meth:`record_schedule_fire` would change."""
        await self._db.execute(
            "UPDATE schedules SET last_run_at = ?, thread_id = ?, last_status = 'running' WHERE id = ?",
            (started_at, thread_id, schedule_id),
        )
        await self._db.commit()

    async def schedule_runs(self, schedule_id: str, source: str, limit: int) -> list[RunRecord]:
        """The last ``limit`` ended runs of ``source`` (the scheduler's) in the chats of the schedule ``schedule_id``,
        newest first: its firings that have an answer, without the runs they delegated or the owner's follow-ups (item
        257)."""
        rows = await self._all(
            "SELECT r.* FROM runs r JOIN threads t ON t.id = r.thread_id"
            " WHERE t.schedule_id = ? AND r.source = ? AND r.parent_run_id IS NULL"
            " AND r.status IN ('done', 'failed', 'cancelled') ORDER BY r.created_at DESC LIMIT ?",
            (schedule_id, source, limit),
        )
        return [_run(row) for row in rows]

    @_writing
    async def keep_schedule_due(self, schedule_id: str, next_run_at: float, fired_at: float) -> None:
        """A one-time firing at ``fired_at`` whose run did not start: due again at ``next_run_at`` and switched on, as
        it was before :meth:`record_schedule_fire` gave it no next time (item 351). A schedule changed since that
        firing (given a time, switched on, fired again) is left as it is."""
        await self._db.execute(
            "UPDATE schedules SET next_run_at = ?, enabled = 1"
            " WHERE id = ? AND last_run_at = ? AND next_run_at IS NULL AND enabled = 0",
            (next_run_at, schedule_id, fired_at),
        )
        await self._db.commit()

    @_writing
    async def record_schedule_skip(self, schedule_id: str, next_run_at: float | None, status: str) -> None:
        """A due firing that did not start a run: move on to ``next_run_at`` (``None`` ends the schedule)."""
        await self._db.execute(
            "UPDATE schedules SET next_run_at = ?, last_status = ?,"
            " enabled = CASE WHEN ? IS NULL THEN 0 ELSE enabled END WHERE id = ?",
            (next_run_at, status, next_run_at, schedule_id),
        )
        await self._db.commit()

    @_writing
    async def set_schedule_status(self, schedule_id: str, status: str) -> None:
        await self._db.execute("UPDATE schedules SET last_status = ? WHERE id = ?", (status, schedule_id))
        await self._db.commit()

    @_writing
    async def set_schedule_enabled(self, schedule_id: str, enabled: bool, next_run_at: float | None) -> None:
        """Switch a schedule on or off; one switched on starts its row of failed runs afresh, and no longer says it was
        switched off for them (item 458)."""
        on: int = 1 if enabled else 0
        await self._db.execute(
            "UPDATE schedules SET enabled = ?, next_run_at = ?,"
            " runs_failed_in_row = CASE WHEN ? = 1 THEN 0 ELSE runs_failed_in_row END,"
            " last_status = CASE WHEN ? = 1 AND last_status LIKE 'switched off:%' THEN '' ELSE last_status END"
            " WHERE id = ?",
            (on, next_run_at, on, on, schedule_id),
        )
        await self._db.commit()

    @_writing
    async def record_schedule_outcome(self, schedule_id: str, status: str) -> int | None:
        """How a run of the schedule ``schedule_id`` ended, as its last status, and its row of failed runs with it
        (item 458): ``done`` sets it to 0, ``failed`` adds one, anything else (``cancelled``) leaves it. The row after
        the write; ``None`` when the schedule was deleted meanwhile."""
        row = await self._one(
            "UPDATE schedules SET last_status = ?, runs_failed_in_row = CASE ? WHEN 'done' THEN 0"
            " WHEN 'failed' THEN runs_failed_in_row + 1 ELSE runs_failed_in_row END WHERE id = ?"
            " RETURNING runs_failed_in_row",
            (status, status, schedule_id),
        )
        await self._db.commit()
        return None if row is None else int(row["runs_failed_in_row"])

    @_writing
    async def switch_off_schedule(self, schedule_id: str, status: str) -> bool:
        """Switch a schedule that is on off for good, with ``status`` saying why (item 458); whether it was on."""
        cursor = await self._db.execute(
            "UPDATE schedules SET enabled = 0, next_run_at = NULL, last_status = ? WHERE id = ? AND enabled = 1",
            (status, schedule_id),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    @_writing
    async def delete_schedule(self, schedule_id: str) -> None:
        await self._db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        await self._db.commit()

    # ----- campaigns -------------------------------------------------------------------------

    @_writing
    async def create_campaign(
        self,
        title: str,
        goal: str,
        thread_id: str,
        model: str,
        effort: Effort,
        speed: Speed,
        once: OneMessage | None,
        approvals: ApprovalMode,
        files: Sequence[str],
        browser: BrowserMode | None,
    ) -> CampaignRecord:
        """Store a new campaign: active, without targets, with no session planned yet, on the Chrome ``browser``
        names (``None``: the daemon's browser mode)."""
        now: float = time.time()
        record: CampaignRecord = CampaignRecord(
            id=new_id("cmp"),
            title=title,
            goal=goal,
            targets="",
            verification="",
            status="active",
            thread_id=thread_id,
            model=model,
            effort=effort,
            speed=speed,
            once=once,
            approvals=approvals,
            files=tuple(files),
            cron=None,
            next_session_at=None,
            created_at=now,
            updated_at=now,
            evidence="",
            browser=browser,
        )
        await self._db.execute(
            "INSERT INTO campaigns(id, title, goal, targets, verification, status, thread_id, model, effort, speed,"
            " once, approvals, files, cron, next_session_at, created_at, updated_at, evidence, browser)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.title,
                record.goal,
                record.targets,
                record.verification,
                record.status,
                record.thread_id,
                record.model,
                record.effort,
                record.speed,
                record.once,
                record.approvals,
                dumps_compact(list(record.files)),
                record.cron,
                record.next_session_at,
                record.created_at,
                record.updated_at,
                record.evidence,
                record.browser,
            ),
        )
        await self._db.commit()
        return record

    async def get_campaign(self, campaign_id: str) -> CampaignRecord:
        row: aiosqlite.Row | None = await self._one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        if row is None:
            raise NotFoundError("campaign not found", {"campaign_id": campaign_id})
        return _campaign(row)

    async def list_campaigns(self) -> list[CampaignRecord]:
        """Every campaign: the active ones first, each part the newest first."""
        rows = await self._all("SELECT * FROM campaigns ORDER BY status = 'active' DESC, created_at DESC", ())
        return [_campaign(row) for row in rows]

    @_writing
    async def _update_campaign(self, campaign_id: str, assignments: str, params: Sequence[object]) -> None:
        cursor: aiosqlite.Cursor = await self._db.execute(
            f"UPDATE campaigns SET {assignments}, updated_at = ? WHERE id = ?", (*params, time.time(), campaign_id)
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            raise NotFoundError("campaign not found", {"campaign_id": campaign_id})

    async def set_campaign_targets(self, campaign_id: str, targets: str, verification: str) -> None:
        await self._update_campaign(campaign_id, "targets = ?, verification = ?", (targets, verification))

    async def plan_campaign_sessions(self, campaign_id: str, cron: str, next_session_at: float) -> None:
        """Give the campaign's sessions the rhythm ``cron``, with the next one at ``next_session_at``."""
        await self._update_campaign(campaign_id, "cron = ?, next_session_at = ?", (cron, next_session_at))

    async def set_next_session(self, campaign_id: str, next_session_at: float | None) -> None:
        await self._update_campaign(campaign_id, "next_session_at = ?", (next_session_at,))

    async def set_campaign_status(
        self, campaign_id: str, status: Literal["active", "stopped"], next_session_at: float | None
    ) -> None:
        """A campaign that is not achieved has no evidence."""
        await self._update_campaign(
            campaign_id, "status = ?, next_session_at = ?, evidence = ''", (status, next_session_at)
        )

    async def achieve_campaign(self, campaign_id: str, evidence: str) -> None:
        """The campaign's outcome is verified: no session follows."""
        await self._update_campaign(
            campaign_id, "status = 'achieved', evidence = ?, next_session_at = NULL", (evidence,)
        )

    async def due_campaigns(self, now: float) -> list[CampaignRecord]:
        """The active campaigns whose next session is due at ``now`` and none of whose sessions is still working (in
        any process), the longest due first."""
        working: tuple[RunStatus, ...] = tuple(sorted(WORKING_STATUSES))
        rows = await self._all(
            "SELECT * FROM campaigns WHERE status = 'active' AND next_session_at IS NOT NULL AND next_session_at <= ?"
            " AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.campaign_id = campaigns.id"
            f" AND runs.status IN ({','.join('?' * len(working))})) ORDER BY next_session_at",
            (now, *working),
        )
        return [_campaign(row) for row in rows]

    # ---------------------------------------------------------------------------------------------------- agents

    @_writing
    async def create_agent(
        self,
        name: str,
        role: str,
        instructions: str,
        model: str,
        effort: Effort | None,
        approvals: ApprovalMode | None,
        tools: Sequence[str],
        made_by: str | None,
        look: AgentLook | None,
    ) -> AgentRecord:
        """Store a new agent (item 86); ``made_by`` is the run that made it (item 99), ``None`` for the owner; ``look``
        is its character (item 102), ``None`` for the fallback of its id's hue."""
        now: float = time.time()
        record: AgentRecord = AgentRecord(
            id=new_id("agt"),
            name=name,
            role=role,
            instructions=instructions,
            model=model,
            effort=effort,
            approvals=approvals,
            tools=tuple(tools),
            created_at=now,
            updated_at=now,
            made_by=made_by,
            look=look,
            paused_at=None,
        )
        await self._db.execute(
            "INSERT INTO agents(id, name, role, instructions, model, effort, approvals, tools, created_at, updated_at,"
            " made_by, shape, expression, colour) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.name,
                record.role,
                record.instructions,
                record.model,
                record.effort,
                record.approvals,
                dumps_compact(list(record.tools)),
                record.created_at,
                record.updated_at,
                record.made_by,
                None if look is None else look.shape,
                None if look is None else look.expression,
                None if look is None else look.colour,
            ),
        )
        await self._db.commit()
        return record

    @_writing
    async def update_agent(
        self,
        agent_id: str,
        name: str,
        role: str,
        instructions: str,
        model: str,
        effort: Effort | None,
        approvals: ApprovalMode | None,
        tools: Sequence[str],
        look: AgentLook | None,
    ) -> AgentRecord:
        """Replace what the agent is; its id, its runs and its pause (item 465) stay."""
        await self.get_agent(agent_id)
        await self._db.execute(
            "UPDATE agents SET name = ?, role = ?, instructions = ?, model = ?, effort = ?, approvals = ?, tools = ?,"
            " updated_at = ?, shape = ?, expression = ?, colour = ? WHERE id = ?",
            (
                name,
                role,
                instructions,
                model,
                effort,
                approvals,
                dumps_compact(list(tools)),
                time.time(),
                None if look is None else look.shape,
                None if look is None else look.expression,
                None if look is None else look.colour,
                agent_id,
            ),
        )
        await self._db.commit()
        return await self.get_agent(agent_id)

    @_writing
    async def set_agent_paused(self, agent_id: str, paused_at: float | None) -> AgentRecord:
        """Pause the agent at ``paused_at`` (item 465), or resume it with ``None``; nothing else of it changes."""
        await self.get_agent(agent_id)
        await self._db.execute("UPDATE agents SET paused_at = ? WHERE id = ?", (paused_at, agent_id))
        await self._db.commit()
        return await self.get_agent(agent_id)

    @_writing
    async def set_agent_look(self, agent_id: str, look: AgentLook) -> AgentRecord:
        """Give the agent a look (item 102) and nothing else."""
        await self.get_agent(agent_id)
        await self._db.execute(
            "UPDATE agents SET shape = ?, expression = ?, colour = ?, updated_at = ? WHERE id = ?",
            (look.shape, look.expression, look.colour, time.time(), agent_id),
        )
        await self._db.commit()
        return await self.get_agent(agent_id)

    async def dot_look(self) -> AgentLook | None:
        """Dot's own look (item 102), ``None`` until the owner chooses one."""
        value: str | None = await self.kv_get(DOT_LOOK_KEY)
        return None if value is None else AgentLook.model_validate_json(value)

    async def set_dot_look(self, look: AgentLook) -> None:
        await self.kv_set(DOT_LOOK_KEY, look.model_dump_json())

    async def get_agent(self, agent_id: str) -> AgentRecord:
        rows = await self._all("SELECT * FROM agents WHERE id = ?", (agent_id,))
        if not rows:
            raise NotFoundError("agent not found", {"agent_id": agent_id})
        return _agent(rows[0])

    async def list_agents(self) -> list[AgentRecord]:
        """Every agent the owner made, oldest first."""
        rows = await self._all("SELECT * FROM agents ORDER BY created_at", ())
        return [_agent(row) for row in rows]

    @_writing
    async def delete_agent(self, agent_id: str) -> None:
        """Forget the agent; its runs and its lessons stay, as Dot's (their ``agent_id`` is cleared). Its schedules
        (item 462) are switched off with :data:`AGENT_DELETED_STATUS` and named for no agent, in the same write: one
        never goes on firing as Dot, with every tool, nor fails at every tick for an agent that is gone."""
        await self.get_agent(agent_id)
        await self._db.execute("UPDATE runs SET agent_id = NULL WHERE agent_id = ?", (agent_id,))
        await self._db.execute("UPDATE memories SET agent_id = NULL WHERE agent_id = ?", (agent_id,))
        await self._db.execute(
            "UPDATE schedules SET enabled = 0, next_run_at = NULL, agent_id = NULL, last_status = ? WHERE agent_id = ?",
            (AGENT_DELETED_STATUS, agent_id),
        )
        await self._db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await self._db.commit()

    async def agent_runs(self, agent_id: str, limit: int) -> list[RunRecord]:
        """The newest ``limit`` runs of the agent, newest first."""
        rows = await self._all(
            "SELECT * FROM runs WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?", (agent_id, limit)
        )
        return [_run(row) for row in rows]

    async def team_runs(self, since: float) -> list[RunRecord]:
        """The runs behind the messages between agents (item 87) started at or after ``since``, oldest first."""
        rows = await self._all(
            "SELECT * FROM runs WHERE source = ? AND created_at >= ? ORDER BY created_at", (TEAM_SOURCE, since)
        )
        return [_run(row) for row in rows]

    async def agent_messages(self, agent_id: str, limit: int) -> list[RunRecord]:
        """The newest ``limit`` runs behind the messages the agent sent or received (item 87), newest first."""
        rows = await self._all(
            "SELECT runs.* FROM runs LEFT JOIN runs AS sender ON sender.id = runs.parent_run_id"
            " WHERE runs.source = ? AND (runs.agent_id = ? OR sender.agent_id = ?)"
            " ORDER BY runs.created_at DESC LIMIT ?",
            (TEAM_SOURCE, agent_id, agent_id, limit),
        )
        return [_run(row) for row in rows]

    async def latest_agent_runs(self) -> dict[str, RunRecord]:
        """The newest run of every agent that ran, by agent id."""
        rows = await self._all(
            "SELECT * FROM runs WHERE agent_id IS NOT NULL AND created_at = ("
            "SELECT MAX(created_at) FROM runs AS newest WHERE newest.agent_id = runs.agent_id)",
            (),
        )
        return {row["agent_id"]: _run(row) for row in rows}

    @_writing
    async def delete_campaign(self, campaign_id: str) -> None:
        """Delete a campaign and its ledger; its sessions stay as runs of their threads, no longer the campaign's: a run
        that named a campaign that is gone would 404 on its page and keep its chat's queue refused (item 553)."""
        await self._db.execute("DELETE FROM campaign_entries WHERE campaign_id = ?", (campaign_id,))
        await self._db.execute("UPDATE runs SET campaign_id = NULL WHERE campaign_id = ?", (campaign_id,))
        await self._db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        await self._db.commit()

    @_writing
    async def delete_campaign_with_sessions(self, campaign_id: str) -> None:
        """Delete a campaign that is not active, on the owner's word, in one transaction: its ledger, and the threads of
        its sessions after the first with everything they hold, as :meth:`delete_threads` deletes chats. The campaign's
        conversation stays a chat: its runs are no longer the campaign's, and the questions they left for the owner
        expire, as no session reads their answers any more. ``RunConflict`` while the campaign is active or a run of any
        process works in its conversation or in the thread of one of its sessions; the checks are made in the
        transaction of the deletes, which holds the write lock from its start."""
        await self._db.execute("BEGIN IMMEDIATE")
        campaign: CampaignRecord = await self.get_campaign(campaign_id)
        if campaign.status == "active":
            raise RunConflict("an active campaign cannot be deleted: stop it first", {"campaign_id": campaign.id})
        rows: list[aiosqlite.Row] = await self._all(
            "SELECT DISTINCT thread_id FROM runs WHERE campaign_id = ? AND thread_id != ? ORDER BY thread_id",
            (campaign.id, campaign.thread_id),
        )
        sessions: tuple[str, ...] = tuple(str(row["thread_id"]) for row in rows)
        await self._refuse_working_chats(
            (campaign.thread_id, *sessions), "a campaign cannot be deleted while a run works in one of its chats"
        )
        candidates: set[str] = await self._delete_chats(sessions) if sessions else set()
        await self._db.execute(
            "WITH RECURSIVE family(id) AS (SELECT id FROM runs WHERE campaign_id = ?"
            " UNION SELECT runs.id FROM runs JOIN family ON runs.parent_run_id = family.id)"
            " UPDATE approvals SET status = 'expired', decided_at = ?, decided_by = 'system', note = ?"
            " WHERE status = 'left' AND run_id IN (SELECT id FROM family)",
            (campaign.id, time.time(), "the campaign was deleted"),
        )
        await self._db.execute("UPDATE runs SET campaign_id = NULL WHERE campaign_id = ?", (campaign.id,))
        await self._db.execute("DELETE FROM campaign_entries WHERE campaign_id = ?", (campaign.id,))
        await self._db.execute("DELETE FROM campaigns WHERE id = ?", (campaign.id,))
        await self._db.commit()
        await self._release_images(candidates)
        await self._return_free_pages()

    @_writing
    async def add_campaign_entry(
        self, campaign_id: str, run_id: str | None, kind: CampaignEntryKind, text: str, numbers: dict[str, float]
    ) -> CampaignEntry:
        entry: CampaignEntry = CampaignEntry(
            id=new_id("cme"),
            campaign_id=campaign_id,
            run_id=run_id,
            ts=time.time(),
            kind=kind,
            text=text,
            numbers=numbers,
        )
        measured: JsonObject = {name: value for name, value in entry.numbers.items()}
        await self._db.execute(
            "INSERT INTO campaign_entries(id, campaign_id, run_id, ts, kind, text, numbers) VALUES(?, ?, ?, ?, ?, ?, "
            "?)",
            (entry.id, entry.campaign_id, entry.run_id, entry.ts, entry.kind, entry.text, dumps_compact(measured)),
        )
        await self._db.commit()
        return entry

    async def campaign_entries(self, campaign_id: str, limit: int) -> list[CampaignEntry]:
        """The newest ``limit`` entries of the campaign's ledger, oldest first."""
        rows = await self._all(
            "SELECT * FROM campaign_entries WHERE campaign_id = ? ORDER BY ts DESC, rowid DESC LIMIT ?",
            (campaign_id, limit),
        )
        return [_campaign_entry(row) for row in reversed(rows)]

    async def campaign_entries_of_kind(self, campaign_id: str, kind: CampaignEntryKind) -> list[CampaignEntry]:
        """Every entry of one kind in the campaign's ledger, oldest first."""
        rows = await self._all(
            "SELECT * FROM campaign_entries WHERE campaign_id = ? AND kind = ? ORDER BY ts, rowid", (campaign_id, kind)
        )
        return [_campaign_entry(row) for row in rows]

    async def latest_campaign_entry(self, campaign_id: str, kind: CampaignEntryKind) -> CampaignEntry | None:
        row = await self._one(
            "SELECT * FROM campaign_entries WHERE campaign_id = ? AND kind = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
            (campaign_id, kind),
        )
        return None if row is None else _campaign_entry(row)

    @_writing
    async def record_paced_action(self, host: str, pace: PaceClass, ts: float, run_id: str) -> None:
        """Count a public or social action of ``run_id`` on ``host`` (:mod:`dotagent.policy.pace`)."""
        await self._db.execute(
            "INSERT INTO paced_actions(host, pace, ts, run_id) VALUES(?, ?, ?, ?)", (host, pace, ts, run_id)
        )
        await self._db.commit()

    @_writing
    async def record_spend(self, host: str, amount: float, currency: str, ts: float, run_id: str) -> None:
        """Count a pre-approved spend against the site's day."""
        await self._db.execute(
            "INSERT INTO spends(host, amount, currency, ts, run_id) VALUES(?, ?, ?, ?, ?)",
            (host, amount, currency, ts, run_id),
        )
        await self._db.commit()

    async def spent_since(self, host: str, since: float) -> float:
        """What the pre-approved spends on ``host`` after ``since`` add up to."""
        row: aiosqlite.Row | None = await self._one(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM spends WHERE host = ? AND ts > ?", (host, since)
        )
        return float(row["total"]) if row is not None else 0.0

    async def paced_times(self, host: str, pace: PaceClass, since: float) -> list[float]:
        """The times of the ``pace`` actions on ``host`` after ``since``, oldest first."""
        rows = await self._all(
            "SELECT ts FROM paced_actions WHERE host = ? AND pace = ? AND ts > ? ORDER BY ts", (host, pace, since)
        )
        return [float(row["ts"]) for row in rows]

    async def campaign_sessions(self, campaign_id: str) -> list[RunRecord]:
        """The sessions of the campaign, oldest first."""
        rows = await self._all("SELECT * FROM runs WHERE campaign_id = ? ORDER BY created_at", (campaign_id,))
        return [_run(row) for row in rows]

    async def campaign_questions(self, campaign_id: str, tool: str, limit: int) -> list[ApprovalRecord]:
        """The newest ``limit`` questions that the campaign's sessions asked the owner with ``tool``, whatever became of
        them, oldest first."""
        rows = await self._all(
            "SELECT approvals.* FROM approvals JOIN runs ON runs.id = approvals.run_id"
            " WHERE runs.campaign_id = ? AND approvals.tool = ? ORDER BY approvals.created_at DESC LIMIT ?",
            (campaign_id, tool, limit),
        )
        return [_approval(row) for row in reversed(rows)]

    async def campaign_kept(self, campaign_id: str, limit: int) -> list[ApprovalRecord]:
        """The newest ``limit`` steps that the campaign's sessions kept for the owner (item 484), whatever became of
        them, oldest first."""
        rows = await self._all(
            "SELECT * FROM approvals WHERE scope_campaign_id = ? AND kept_until IS NOT NULL"
            " ORDER BY created_at DESC LIMIT ?",
            (campaign_id, limit),
        )
        return [_approval(row) for row in reversed(rows)]

    async def run_approvals(self, run_id: str) -> list[ApprovalRecord]:
        """Every approval and question of ``run_id``, whatever became of it, oldest first."""
        rows = await self._all("SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at, rowid", (run_id,))
        return [_approval(row) for row in rows]

    async def run_questions(self, run_id: str, tool: str) -> list[ApprovalRecord]:
        """The questions ``run_id`` asked the owner with ``tool``, whatever became of them, oldest first."""
        rows = await self._all(
            "SELECT * FROM approvals WHERE run_id = ? AND tool = ? ORDER BY created_at", (run_id, tool)
        )
        return [_approval(row) for row in rows]

    async def campaign_tokens(self, campaign_id: str) -> int:
        """The tokens (input and output) of the campaign's sessions and of the runs they delegated to."""
        row = await self._one(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS n FROM runs WHERE campaign_id = ?"
            " OR parent_run_id IN (SELECT id FROM runs WHERE campaign_id = ?)",
            (campaign_id, campaign_id),
        )
        return 0 if row is None else int(row["n"])

"""SQLite persistence (aiosqlite). One shared connection, WAL mode, FTS5 for memory search.

Single statements share the connection. A unit of several statements that must be stored whole (a thread's messages)
runs in a worker thread on a connection of its own, in one transaction: the shared connection commits for every
coroutine at once, so a unit cut off after its first statement would be committed half done by the next write.
"""

import asyncio
import base64
import hashlib
import re
import sqlite3
import struct
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import aiosqlite

from dotagent.core.errors import NotFoundError, StoreError
from dotagent.core.ids import new_id
from dotagent.core.jsonutil import dumps_compact, parse_json_object, parse_json_value
from dotagent.core.text import fold_search
from dotagent.core.types import JsonArray, JsonObject
from dotagent.llm.calls import CALL_EVENT, CallPurpose
from dotagent.llm.types import Effort, ImageData, Message
from dotagent.policy.types import ApprovalMode, PaceClass
from dotagent.store.endings import RESULT_RESTARTED
from dotagent.store.models import (
    WORKING_STATUSES,
    ApprovalRecord,
    ApprovalStatus,
    Behavior,
    BrowserMode,
    CampaignEntry,
    CampaignEntryKind,
    CampaignRecord,
    EventRecord,
    MemoryRecord,
    PushSubscription,
    RuleRecord,
    RunMode,
    RunRecord,
    RunStatus,
    ScheduleOptions,
    ScheduleRecord,
    ThreadRecord,
    ThreadSummary,
)
from dotagent.store.stats import ModelProfile, RunCosts, collect_run_costs, read_model_profile

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    thread_id TEXT NOT NULL, seq INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (thread_id, seq)
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
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY, position INTEGER NOT NULL, enabled INTEGER NOT NULL, behavior TEXT NOT NULL,
    tool_glob TEXT NOT NULL, match_regex TEXT NOT NULL, description TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY, content TEXT NOT NULL, tags TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, source_run TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    id UNINDEXED, content, tags, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS memory_vectors (id TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL);
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
"""

_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


# How long a write waits for another writer; the same 10 s as the busy timeout of the shared connection.
_WRITE_WAIT_S: float = 10.0

# A screenshot saved this recently may belong to a message or event that is not stored yet, so deleting a chat leaves it
# for ``orphan_images`` to find later.
_RECENT_IMAGE_S: float = 60.0


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
    """Store the image under its content name. A file that is already there is touched: its last change then tells when
    some message last wanted it, which is what keeps a delete or a sweep from removing it just as it is named again."""
    path: Path = directory / image_file_name(image)
    if path.exists():
        path.touch()
    else:
        path.write_bytes(base64.b64decode(image.data_b64))
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


@contextmanager
def _write_transaction(database: Path) -> Iterator[sqlite3.Connection]:
    """One write transaction on a connection of its own: all of it is stored or none of it.

    The write lock is taken up front, so concurrent units wait for each other (up to ``_WRITE_WAIT_S``) instead of
    reading stale numbers, and any error rolls the whole unit back before it is raised again.
    """
    connection: sqlite3.Connection = sqlite3.connect(database, timeout=_WRITE_WAIT_S, isolation_level=None)
    try:
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _append_unit(database: Path, image_dir: Path, thread_id: str, messages: Sequence[Message], now: float) -> None:
    bodies: list[str] = [encode_message(message, image_dir) for message in messages]
    with _write_transaction(database) as connection:
        top: tuple[int] = connection.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        rows: list[tuple[str, int, str]] = [
            (thread_id, int(top[0]) + 1 + offset, body) for offset, body in enumerate(bodies)
        ]
        connection.executemany("INSERT INTO messages(thread_id, seq, body) VALUES(?, ?, ?)", rows)
        connection.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id))


def _replace_unit(database: Path, image_dir: Path, thread_id: str, messages: Sequence[Message], now: float) -> None:
    bodies: list[str] = [encode_message(message, image_dir) for message in messages]
    with _write_transaction(database) as connection:
        connection.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
        connection.executemany(
            "INSERT INTO messages(thread_id, seq, body) VALUES(?, ?, ?)",
            [(thread_id, index, body) for index, body in enumerate(bodies)],
        )
        connection.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id))


def decode_message(body: str, image_dir: Path, load_images: bool) -> Message:
    parsed: JsonObject = parse_json_object(body, "stored message")
    refs: object = parsed.get("images")
    images: list[ImageData] = []
    if isinstance(refs, list) and load_images:
        for ref in refs:
            if isinstance(ref, dict) and isinstance(ref.get("file"), str) and isinstance(ref.get("media_type"), str):
                images.append(_read_image(image_dir, str(ref["media_type"]), str(ref["file"])))
    parsed["images"] = [image.model_dump(mode="json") for image in images]
    return Message.model_validate(parsed)


def _thread(row: aiosqlite.Row) -> ThreadRecord:
    return ThreadRecord(
        id=row["id"],
        title=row["title"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _summary(row: aiosqlite.Row) -> ThreadSummary:
    return ThreadSummary(
        id=row["id"],
        title=row["title"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        run_count=row["run_count"],
        latest_run_id=row["latest_run_id"],
        latest_status=row["latest_status"],
        latest_mode=row["latest_mode"],
    )


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
        approvals=row["approvals"],
        goal_mode=bool(row["goal_mode"]),
        campaign_id=row["campaign_id"],
    )


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


def _memory(row: aiosqlite.Row) -> MemoryRecord:
    tags: str = row["tags"]
    return MemoryRecord(
        id=row["id"],
        content=row["content"],
        tags=tuple(tag for tag in tags.split(",") if tag),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source_run=row["source_run"],
        used_count=row["used_count"],
        last_used_at=row["last_used_at"],
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
            approvals=row["approvals"],
            goal_mode=None if row["goal_mode"] is None else bool(row["goal_mode"]),
            timezone=row["timezone"],
        ),
    )


def _option_values(options: ScheduleOptions) -> tuple[str | None, str | None, str | None, int | None, str | None]:
    """The schedule columns that hold ``options``, in the order model, effort, approvals, goal_mode, timezone."""
    goal_mode: int | None = None if options.goal_mode is None else int(options.goal_mode)
    return options.model, options.effort, options.approvals, goal_mode, options.timezone


# Columns of ``runs`` added after the first version, with the definition that gives the rows of an older database the
# value those runs had in effect: no reasoning level kept, ``ask``, no goal mode, no campaign.
_ADDED_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("effort", "TEXT"),
    ("approvals", "TEXT NOT NULL DEFAULT 'ask'"),
    ("goal_mode", "INTEGER NOT NULL DEFAULT 0"),
    ("campaign_id", "TEXT"),
)
# Columns of ``memories`` added later: notes of an older database were never counted as used.
_ADDED_MEMORY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("used_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_used_at", "REAL"),
)
# Columns of ``schedules`` added later: NULL means "what the configuration says when the schedule fires", which is how
# the schedules of an older database ran.
_ADDED_SCHEDULE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("model", "TEXT"),
    ("effort", "TEXT"),
    ("approvals", "TEXT"),
    ("goal_mode", "INTEGER"),
    ("timezone", "TEXT"),
)


# Columns of ``campaigns`` added later: NULL means the daemon's browser, which is where an older campaign's sessions ran.
_ADDED_CAMPAIGN_COLUMNS: tuple[tuple[str, str], ...] = (("browser", "TEXT"),)


async def _add_columns(connection: aiosqlite.Connection, table: str, added: Sequence[tuple[str, str]]) -> None:
    async with connection.execute(f"PRAGMA table_info({table})") as cursor:
        columns: set[str] = {str(row["name"]) for row in await cursor.fetchall()}
    for name, definition in added:
        if name not in columns:
            await connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


async def _add_missing_columns(connection: aiosqlite.Connection) -> None:
    """Give a database created by an older version the columns added since, and the indexes on them."""
    await _add_columns(connection, "runs", _ADDED_RUN_COLUMNS)
    await _add_columns(connection, "memories", _ADDED_MEMORY_COLUMNS)
    await _add_columns(connection, "schedules", _ADDED_SCHEDULE_COLUMNS)
    await _add_columns(connection, "campaigns", _ADDED_CAMPAIGN_COLUMNS)
    await connection.execute("CREATE INDEX IF NOT EXISTS runs_campaign ON runs(campaign_id, created_at)")


async def _refresh_memory_index(connection: aiosqlite.Connection) -> None:
    """Make the search index of the notes say what the notes say, in the folded form the queries use, and write it
    again whole when it does not (an index made by an older version, or by a process that did not fold yet). The index
    only holds a copy of the notes, so nothing a person wrote is touched."""
    async with connection.execute("SELECT id, content, tags FROM memories") as cursor:
        wanted: list[tuple[str, str, str]] = sorted(
            (str(note["id"]), fold_search(str(note["content"])), fold_search(str(note["tags"])))
            for note in await cursor.fetchall()
        )
    async with connection.execute("SELECT id, content, tags FROM memories_fts") as cursor:
        indexed: list[tuple[str, str, str]] = sorted(
            (str(row["id"]), str(row["content"]), str(row["tags"])) for row in await cursor.fetchall()
        )
    if indexed == wanted:
        return
    await connection.execute("DELETE FROM memories_fts")
    await connection.executemany("INSERT INTO memories_fts(id, content, tags) VALUES(?, ?, ?)", wanted)


class Store:
    """Connector to the SQLite database."""

    def __init__(self, connection: aiosqlite.Connection, database: Path, image_dir: Path) -> None:
        self._db: aiosqlite.Connection = connection
        self._database: Path = database
        self._image_dir: Path = image_dir

    @classmethod
    async def open(cls, path: Path, image_dir: Path) -> "Store":
        await asyncio.to_thread(_prepare_dirs, path, image_dir)
        connection: aiosqlite.Connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA synchronous=NORMAL")
        await connection.execute("PRAGMA busy_timeout=10000")
        await connection.executescript(_SCHEMA)
        await _add_missing_columns(connection)
        await _refresh_memory_index(connection)
        await connection.commit()
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

    async def kv_set(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._db.commit()

    # ----- threads and messages ------------------------------------------------------------

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

    async def rename_thread(self, thread_id: str, title: str) -> None:
        await self._db.execute("UPDATE threads SET title = ? WHERE id = ?", (title, thread_id))
        await self._db.commit()

    async def delete_thread(self, thread_id: str) -> None:
        await self._db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
        await self._db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        await self._db.commit()

    async def list_thread_summaries(self, limit: int) -> list[ThreadSummary]:
        """The chats, latest activity first: every thread with a run a person started on it, with the latest of those
        runs. Threads holding only delegated runs are left out, and so are the threads of a campaign's sessions after the
        first, which start in threads of their own: the campaign's page lists them, and the campaign's conversation
        stays a chat."""
        rows: list[aiosqlite.Row] = await self._all(
            "WITH ranked AS ("
            " SELECT thread_id, id, status, mode, created_at,"
            "  ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY created_at DESC, rowid DESC) AS place,"
            "  COUNT(*) OVER (PARTITION BY thread_id) AS run_count"
            " FROM runs WHERE parent_run_id IS NULL"
            " AND (campaign_id IS NULL OR thread_id IN (SELECT thread_id FROM campaigns)))"
            " SELECT t.id, t.title, t.source, t.created_at, MAX(t.updated_at, r.created_at) AS updated_at,"
            "  r.run_count, r.id AS latest_run_id, r.status AS latest_status, r.mode AS latest_mode"
            " FROM threads AS t JOIN ranked AS r ON r.thread_id = t.id AND r.place = 1"
            " ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        return [_summary(row) for row in rows]

    async def delete_threads(self, thread_ids: Sequence[str]) -> list[str]:
        """Delete chats with everything they hold, in one transaction: their messages, their runs, the runs those
        delegated to (recursively) with the events and approvals of all of them, and the threads of the delegated runs
        unless a run of their own remains there. Returns the ids that named a thread, in the order given; the others
        are skipped. A schedule keeps the id of the thread it last fired into and makes a new one per firing, so it is
        left alone."""
        named: tuple[str, ...] = tuple(thread_ids)
        marks: str = ",".join("?" * len(named))
        doomed: str = (
            "WITH RECURSIVE doomed(id) AS ("
            f" SELECT id FROM runs WHERE thread_id IN ({marks})"
            " UNION SELECT runs.id FROM runs JOIN doomed ON runs.parent_run_id = doomed.id)"
        )
        found: list[aiosqlite.Row] = await self._all(f"SELECT id FROM threads WHERE id IN ({marks})", named)
        existing: set[str] = {str(row["id"]) for row in found}
        hosts: list[aiosqlite.Row] = await self._all(
            f"{doomed} SELECT DISTINCT thread_id FROM runs"
            f" WHERE id IN (SELECT id FROM doomed) AND thread_id NOT IN ({marks})",
            named + named,
        )
        delegated: tuple[str, ...] = tuple(str(row["thread_id"]) for row in hosts)
        candidates: set[str] = await self._screenshots_of(doomed, named, delegated)
        await self._db.execute(f"{doomed} DELETE FROM events WHERE run_id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"{doomed} DELETE FROM approvals WHERE run_id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"{doomed} DELETE FROM runs WHERE id IN (SELECT id FROM doomed)", named)
        await self._db.execute(f"DELETE FROM messages WHERE thread_id IN ({marks})", named)
        await self._db.execute(f"DELETE FROM threads WHERE id IN ({marks})", named)
        if delegated:
            host_marks: str = ",".join("?" * len(delegated))
            await self._db.execute(
                f"DELETE FROM messages WHERE thread_id IN ({host_marks})"
                " AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.thread_id = messages.thread_id)",
                delegated,
            )
            await self._db.execute(
                f"DELETE FROM threads WHERE id IN ({host_marks})"
                " AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.thread_id = threads.id)",
                delegated,
            )
        await self._db.commit()
        await self._release_images(candidates)
        await self._return_free_pages()
        return [thread_id for thread_id in named if thread_id in existing]

    async def _screenshots_of(self, doomed: str, named: tuple[str, ...], delegated: tuple[str, ...]) -> set[str]:
        """The screenshot files named by what ``delete_threads`` is about to delete: the events of the doomed runs and
        the messages of the named threads and of the threads of delegated runs."""
        threads: tuple[str, ...] = named + delegated
        rows: list[aiosqlite.Row] = await self._all(
            f"{doomed} SELECT data AS text FROM events WHERE run_id IN (SELECT id FROM doomed)"
            f" UNION ALL SELECT body FROM messages WHERE thread_id IN ({','.join('?' * len(threads))})",
            named + threads,
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
        """The screenshot files that a stored message or event names."""
        rows: list[aiosqlite.Row] = await self._all(
            "SELECT body AS text FROM messages UNION ALL SELECT data FROM events", ()
        )
        return await asyncio.to_thread(image_names_in, [str(row["text"]) for row in rows])

    async def events_of_runs_ended_before(self, before: float) -> tuple[int, int]:
        """How many runs that ended before ``before`` still have events, and how many events they have."""
        row = await self._one(
            "SELECT COUNT(DISTINCT run_id) AS runs, COUNT(*) AS events FROM events WHERE run_id IN"
            " (SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?)",
            (before,),
        )
        return (0, 0) if row is None else (int(row["runs"]), int(row["events"]))

    async def delete_events_of_runs_ended_before(self, before: float) -> int:
        """Delete the events of the runs that ended before ``before``; the runs stay. Returns how many events went.
        The screenshots those events named become orphans for :meth:`orphan_images`."""
        cursor = await self._db.execute(
            "DELETE FROM events WHERE run_id IN (SELECT id FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?)",
            (before,),
        )
        await self._db.commit()
        return cursor.rowcount

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

    async def compact(self) -> None:
        """Give the free pages of the database file back to the disk, and let later deletes do so by themselves. Needs
        the connection to have no transaction open. The rewritten file reaches the disk through the write-ahead log, so
        the log is emptied too; a connection that is reading at that moment leaves the rest of it for a later
        checkpoint."""
        await self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await self._db.execute("VACUUM")
        async with self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
            await cursor.fetchall()

    async def append_messages(self, thread_id: str, messages: Sequence[Message]) -> None:
        """Add ``messages`` after the thread's last one: all of them are stored or none, and concurrent appends to one
        thread are numbered one after the other."""
        await asyncio.to_thread(_append_unit, self._database, self._image_dir, thread_id, messages, time.time())

    async def replace_messages(self, thread_id: str, messages: Sequence[Message]) -> None:
        """Swap the thread's whole history for ``messages`` in one step: a failure or a stop leaves the old history."""
        await asyncio.to_thread(_replace_unit, self._database, self._image_dir, thread_id, messages, time.time())

    async def load_messages(self, thread_id: str, keep_recent_images: int) -> list[Message]:
        """Load a thread's history; only the newest ``keep_recent_images`` screenshots are re-inflated."""
        rows: list[aiosqlite.Row] = await self._all(
            "SELECT body FROM messages WHERE thread_id = ? ORDER BY seq", (thread_id,)
        )
        bodies: list[str] = [str(row["body"]) for row in rows]

        def decode_all() -> list[Message]:
            with_images: list[int] = [index for index, body in enumerate(bodies) if '"file"' in body]
            allowed: set[int] = (
                set(with_images[len(with_images) - keep_recent_images :]) if keep_recent_images > 0 else set()
            )
            return [decode_message(body, self._image_dir, index in allowed) for index, body in enumerate(bodies)]

        return await asyncio.to_thread(decode_all)

    async def save_images(self, images: Sequence[ImageData]) -> list[str]:
        """Write images to the screenshot directory (idempotent) and return their file names."""
        return await asyncio.to_thread(lambda: [_write_image(self._image_dir, image) for image in images])

    # ----- runs ------------------------------------------------------------------------------

    async def create_run(
        self,
        thread_id: str,
        goal: str,
        mode: RunMode,
        model: str,
        parent_run_id: str | None,
        source: str,
        effort: Effort,
        approvals: ApprovalMode,
        goal_mode: bool,
        campaign_id: str | None,
    ) -> RunRecord:
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
            approvals=approvals,
            goal_mode=goal_mode,
            campaign_id=campaign_id,
        )
        await self._db.execute(
            "INSERT INTO runs(id, thread_id, goal, mode, model, status, created_at, finished_at, result, steps,"
            " input_tokens, output_tokens, parent_run_id, source, effort, approvals, goal_mode, campaign_id)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                record.approvals,
                int(record.goal_mode),
                record.campaign_id,
            ),
        )
        await self._db.commit()
        return record

    async def set_run_status(self, run_id: str, status: RunStatus) -> None:
        await self._db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        await self._db.commit()

    async def set_run_steps(self, run_id: str, steps: int) -> None:
        await self._db.execute("UPDATE runs SET steps = ? WHERE id = ?", (steps, run_id))
        await self._db.commit()

    async def set_run_model(self, run_id: str, model: str, effort: Effort | None) -> None:
        """The model a run works on from now on (a standby model, once its own model cannot answer) and the reasoning
        level it asks that model for (``None``: the configured default)."""
        await self._db.execute("UPDATE runs SET model = ?, effort = ? WHERE id = ?", (model, effort, run_id))
        await self._db.commit()

    async def add_run_tokens(self, run_id: str, input_tokens: int, output_tokens: int) -> None:
        """Add the tokens of one model call of a run (a step or a side call) to its totals."""
        await self._db.execute(
            "UPDATE runs SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ? WHERE id = ?",
            (input_tokens, output_tokens, run_id),
        )
        await self._db.commit()

    async def finish_run(self, run_id: str, status: RunStatus, result: str) -> None:
        await self._db.execute(
            "UPDATE runs SET status = ?, result = ?, finished_at = ? WHERE id = ?",
            (status, result, time.time(), run_id),
        )
        await self._db.commit()

    async def get_run(self, run_id: str) -> RunRecord:
        row: aiosqlite.Row | None = await self._one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise NotFoundError("run not found", {"run_id": run_id})
        return _run(row)

    async def list_runs(self, limit: int, thread_id: str | None) -> list[RunRecord]:
        if thread_id is None:
            rows = await self._all("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            rows = await self._all(
                "SELECT * FROM runs WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?", (thread_id, limit)
            )
        return [_run(row) for row in rows]

    async def list_child_runs(self, parent_run_id: str) -> list[RunRecord]:
        """The runs started on behalf of ``parent_run_id``, oldest first."""
        rows = await self._all("SELECT * FROM runs WHERE parent_run_id = ? ORDER BY created_at", (parent_run_id,))
        return [_run(row) for row in rows]

    async def family_tokens(self, run_id: str) -> int:
        """The tokens (input and output) of a run and of the runs started on its behalf, as their totals stand now."""
        row = await self._one(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS n FROM runs WHERE id = ? OR parent_run_id = ?",
            (run_id, run_id),
        )
        return 0 if row is None else int(row["n"])

    async def mark_interrupted_runs(self) -> int:
        """Runs left active by a crashed daemon (queued, running, or blocked on a question) become 'failed', finished
        at their last sign of life: their newest event, else the time they were created. Each one also gets, at that
        time, the ``run_finished`` event that every other end of a run stores, and that ends the stream of its events;
        so does a run closed this way before that event was stored. Returns how many runs changed."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE runs SET status = 'failed', result = ?,"
            " finished_at = COALESCE((SELECT MAX(ts) FROM events WHERE events.run_id = runs.id), created_at)"
            " WHERE status IN ('running', 'queued', 'blocked')",
            (RESULT_RESTARTED,),
        )
        changed: int = cursor.rowcount
        await self._db.execute(
            "INSERT INTO events(run_id, ts, kind, data) SELECT id, finished_at, 'run_finished', ? FROM runs"
            " WHERE status = 'failed' AND result = ?"
            " AND NOT EXISTS (SELECT 1 FROM events WHERE events.run_id = runs.id AND events.kind = 'run_finished')",
            (dumps_compact({"status": "failed", "result": RESULT_RESTARTED}), RESULT_RESTARTED),
        )
        await self._db.commit()
        return changed

    async def runs_finished_since(self, since: float) -> list[RunRecord]:
        """The runs that finished at or after ``since``, oldest first."""
        rows = await self._all("SELECT * FROM runs WHERE finished_at >= ? ORDER BY created_at", (since,))
        return [_run(row) for row in rows]

    # ----- events ----------------------------------------------------------------------------

    async def add_event(self, run_id: str, kind: str, data: JsonObject) -> EventRecord:
        now: float = time.time()
        cursor: aiosqlite.Cursor = await self._db.execute(
            "INSERT INTO events(run_id, ts, kind, data) VALUES(?, ?, ?, ?)",
            (run_id, now, kind, dumps_compact(data)),
        )
        await self._db.commit()
        if cursor.lastrowid is None:
            raise StoreError("event insert returned no row id", {"run_id": run_id, "kind": kind})
        return EventRecord(id=cursor.lastrowid, run_id=run_id, ts=now, kind=kind, data=data)

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

    async def events_of_kind(self, run_id: str, kind: str, limit: int) -> list[EventRecord]:
        """The newest ``limit`` events of ``kind`` of a run, oldest first."""
        rows = await self._all(
            "SELECT * FROM (SELECT * FROM events WHERE run_id = ? AND kind = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (run_id, kind, limit),
        )
        return [_event(row) for row in rows]

    async def model_profile(self, model: str, runs: int) -> ModelProfile:
        """How reliably ``model`` worked in its newest ``runs`` finished runs, read on a read-only connection."""
        return await asyncio.to_thread(read_model_profile, self._database, model, runs)

    async def run_costs(self, run_id: str) -> RunCosts:
        """What the run cost and where it went, read on a read-only connection; its minutes run to now while it works."""
        return await asyncio.to_thread(collect_run_costs, self._database, run_id, time.time())

    # ----- approvals -------------------------------------------------------------------------

    async def _insert_approval(self, record: ApprovalRecord) -> None:
        await self._db.execute(
            "INSERT INTO approvals(id, run_id, created_at, decided_at, status, tool, summary, severity, args, decided_by, note)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        )
        await self._insert_approval(record)
        return record

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
        )
        await self._insert_approval(record)
        return record

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

    async def list_left_questions(self, run_id: str) -> list[ApprovalRecord]:
        """The questions ``run_id`` left for the user that are not answered or skipped yet, oldest first."""
        rows = await self._all(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'left' ORDER BY created_at", (run_id,)
        )
        return [_approval(row) for row in rows]

    async def expire_pending_approvals(self) -> int:
        cursor = await self._db.execute(
            "UPDATE approvals SET status = 'expired', decided_at = ?, decided_by = 'system', note = 'daemon restarted'"
            " WHERE status = 'pending'",
            (time.time(),),
        )
        await self._db.commit()
        return cursor.rowcount

    # ----- rules -----------------------------------------------------------------------------

    async def list_rules(self) -> list[RuleRecord]:
        rows = await self._all("SELECT * FROM rules ORDER BY position, created_at", ())
        return [_rule(row) for row in rows]

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

    async def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        await self._db.execute("UPDATE rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
        await self._db.commit()

    async def delete_rule(self, rule_id: str) -> None:
        await self._db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        await self._db.commit()

    # ----- push subscriptions ------------------------------------------------------------------

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

    async def delete_push_subscription(self, endpoint: str) -> None:
        await self._db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await self._db.commit()

    # ----- memories --------------------------------------------------------------------------

    async def add_memory(
        self, content: str, tags: Sequence[str], source_run: str | None, vector: Sequence[float] | None
    ) -> MemoryRecord:
        now: float = time.time()
        record: MemoryRecord = MemoryRecord(
            id=new_id("mem"),
            content=content,
            tags=tuple(tags),
            created_at=now,
            updated_at=now,
            source_run=source_run,
            used_count=0,
            last_used_at=None,
        )
        tag_text: str = ",".join(record.tags)
        await self._db.execute(
            "INSERT INTO memories(id, content, tags, created_at, updated_at, source_run) VALUES(?, ?, ?, ?, ?, ?)",
            (record.id, content, tag_text, now, now, source_run),
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
        await self._db.commit()
        return record

    async def update_memory(
        self, memory_id: str, content: str, tags: Sequence[str], vector: Sequence[float] | None
    ) -> MemoryRecord:
        tag_text: str = ",".join(tags)
        await self._db.execute(
            "UPDATE memories SET content = ?, tags = ?, updated_at = ? WHERE id = ?",
            (content, tag_text, time.time(), memory_id),
        )
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

    async def set_memory_tags(self, memory_id: str, tags: Sequence[str]) -> MemoryRecord:
        """Replace the tags of a memory; its text and vector stay."""
        tag_text: str = ",".join(tags)
        await self._db.execute(
            "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?", (tag_text, time.time(), memory_id)
        )
        await self._db.execute("UPDATE memories_fts SET tags = ? WHERE id = ?", (fold_search(tag_text), memory_id))
        await self._db.commit()
        return await self.get_memory(memory_id)

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

    async def search_memories_terms(self, terms: Sequence[str], limit: int) -> list[str]:
        """Ids of the memories that contain any of ``terms`` (folded words, see ``fts_query``), ranked by BM25, best
        first."""
        if not terms:
            return []
        rows = await self._all(
            "SELECT id FROM memories_fts WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
            (fts_query(terms), limit),
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

    async def all_memory_vectors(self) -> list[tuple[str, tuple[float, ...]]]:
        rows = await self._all("SELECT id, vec FROM memory_vectors", ())
        return [(str(row["id"]), unpack_vector(bytes(row["vec"]))) for row in rows]

    async def find_memory_by_content(self, content: str) -> MemoryRecord | None:
        row = await self._one("SELECT * FROM memories WHERE content = ? LIMIT 1", (content,))
        return None if row is None else _memory(row)

    async def memories_with_tag(self, tag: str, limit: int) -> list[MemoryRecord]:
        """The most recently changed memories carrying ``tag`` (tags never contain commas)."""
        rows = await self._all(
            "SELECT * FROM memories WHERE instr(',' || tags || ',', ?) > 0 ORDER BY updated_at DESC LIMIT ?",
            (f",{tag},", limit),
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

    async def mark_memories_used(self, memory_ids: Sequence[str], when: float) -> None:
        """Count one more use of each note, at ``when``; its ``updated_at`` stays."""
        if not memory_ids:
            return
        marks: str = ",".join("?" for _ in memory_ids)
        await self._db.execute(
            f"UPDATE memories SET used_count = used_count + 1, last_used_at = ? WHERE id IN ({marks})",
            (when, *memory_ids),
        )
        await self._db.commit()

    async def clear_memory_vectors(self) -> None:
        await self._db.execute("DELETE FROM memory_vectors")
        await self._db.commit()

    async def memories_without_vectors(self, limit: int) -> list[MemoryRecord]:
        rows = await self._all(
            "SELECT m.* FROM memories m LEFT JOIN memory_vectors v ON v.id = m.id WHERE v.id IS NULL LIMIT ?",
            (limit,),
        )
        return [_memory(row) for row in rows]

    async def set_memory_vector(self, memory_id: str, vector: Sequence[float]) -> None:
        await self._db.execute(
            "INSERT INTO memory_vectors(id, dim, vec) VALUES(?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET dim = excluded.dim, vec = excluded.vec",
            (memory_id, len(vector), pack_vector(vector)),
        )
        await self._db.commit()

    # ----- schedules -------------------------------------------------------------------------

    async def add_schedule(
        self,
        name: str,
        cron: str | None,
        run_at: float | None,
        goal: str,
        mode: RunMode,
        next_run_at: float | None,
        options: ScheduleOptions,
    ) -> ScheduleRecord:
        record: ScheduleRecord = ScheduleRecord(
            id=new_id("sch"),
            name=name,
            cron=cron,
            run_at=run_at,
            goal=goal,
            mode=mode,
            enabled=True,
            created_at=time.time(),
            last_run_at=None,
            next_run_at=next_run_at,
            thread_id=None,
            last_status="",
            options=options,
        )
        await self._db.execute(
            "INSERT INTO schedules(id, name, cron, run_at, goal, mode, enabled, created_at, last_run_at, next_run_at,"
            " thread_id, last_status, model, effort, approvals, goal_mode, timezone)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                name,
                cron,
                run_at,
                goal,
                mode,
                1,
                record.created_at,
                None,
                next_run_at,
                None,
                "",
                *_option_values(options),
            ),
        )
        await self._db.commit()
        return record

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
        """Replace what a schedule does and when; its history (last run, thread, status) stays."""
        await self._db.execute(
            "UPDATE schedules SET name = ?, cron = ?, run_at = ?, goal = ?, mode = ?, model = ?, effort = ?,"
            " approvals = ?, goal_mode = ?, timezone = ?, enabled = ?, next_run_at = ? WHERE id = ?",
            (name, cron, run_at, goal, mode, *_option_values(options), 1 if enabled else 0, next_run_at, schedule_id),
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
            "SELECT * FROM schedules WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ? ORDER BY next_run_at",
            (now,),
        )
        return [_schedule(row) for row in rows]

    async def record_schedule_fire(
        self, schedule_id: str, fired_at: float, next_run_at: float | None, thread_id: str, status: str
    ) -> None:
        await self._db.execute(
            "UPDATE schedules SET last_run_at = ?, next_run_at = ?, thread_id = ?, last_status = ?,"
            " enabled = CASE WHEN ? IS NULL THEN 0 ELSE enabled END WHERE id = ?",
            (fired_at, next_run_at, thread_id, status, next_run_at, schedule_id),
        )
        await self._db.commit()

    async def record_schedule_skip(self, schedule_id: str, next_run_at: float | None, status: str) -> None:
        """A due firing that did not start a run: move on to ``next_run_at`` (``None`` ends the schedule)."""
        await self._db.execute(
            "UPDATE schedules SET next_run_at = ?, last_status = ?,"
            " enabled = CASE WHEN ? IS NULL THEN 0 ELSE enabled END WHERE id = ?",
            (next_run_at, status, next_run_at, schedule_id),
        )
        await self._db.commit()

    async def set_schedule_status(self, schedule_id: str, status: str) -> None:
        await self._db.execute("UPDATE schedules SET last_status = ? WHERE id = ?", (status, schedule_id))
        await self._db.commit()

    async def set_schedule_enabled(self, schedule_id: str, enabled: bool, next_run_at: float | None) -> None:
        await self._db.execute(
            "UPDATE schedules SET enabled = ?, next_run_at = ? WHERE id = ?",
            (1 if enabled else 0, next_run_at, schedule_id),
        )
        await self._db.commit()

    async def delete_schedule(self, schedule_id: str) -> None:
        await self._db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        await self._db.commit()

    # ----- campaigns -------------------------------------------------------------------------

    async def create_campaign(
        self,
        title: str,
        goal: str,
        thread_id: str,
        model: str,
        effort: Effort,
        approvals: ApprovalMode,
        files: Sequence[str],
        browser: BrowserMode,
    ) -> CampaignRecord:
        """Store a new campaign: active, without targets, with no session planned yet, on the Chrome ``browser`` names."""
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
            "INSERT INTO campaigns(id, title, goal, targets, verification, status, thread_id, model, effort, approvals,"
            " files, cron, next_session_at, created_at, updated_at, evidence, browser)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    async def delete_campaign(self, campaign_id: str) -> None:
        """Delete a campaign and its ledger; its sessions stay as runs of their threads."""
        await self._db.execute("DELETE FROM campaign_entries WHERE campaign_id = ?", (campaign_id,))
        await self._db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        await self._db.commit()

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
            "INSERT INTO campaign_entries(id, campaign_id, run_id, ts, kind, text, numbers) VALUES(?, ?, ?, ?, ?, ?, ?)",
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

    async def record_paced_action(self, host: str, pace: PaceClass, ts: float, run_id: str) -> None:
        """Count a public or social action of ``run_id`` on ``host`` (:mod:`dotagent.policy.pace`)."""
        await self._db.execute(
            "INSERT INTO paced_actions(host, pace, ts, run_id) VALUES(?, ?, ?, ?)", (host, pace, ts, run_id)
        )
        await self._db.commit()

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

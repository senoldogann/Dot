"""SQLite persistence (aiosqlite). One connection, WAL mode, FTS5 for memory search."""

import asyncio
import base64
import hashlib
import struct
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import aiosqlite

from dotagent.core.errors import NotFoundError, StoreError
from dotagent.core.ids import new_id
from dotagent.core.jsonutil import dumps_compact, parse_json_object
from dotagent.core.types import JsonObject
from dotagent.llm.types import Effort, ImageData, Message
from dotagent.policy.types import ApprovalMode
from dotagent.store.models import (
    ApprovalRecord,
    ApprovalStatus,
    Behavior,
    EventRecord,
    MemoryRecord,
    RuleRecord,
    RunMode,
    RunRecord,
    RunStatus,
    ScheduleOptions,
    ScheduleRecord,
    ThreadRecord,
    ThreadSummary,
)

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
    approvals TEXT NOT NULL DEFAULT 'ask', goal_mode INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY, position INTEGER NOT NULL, enabled INTEGER NOT NULL, behavior TEXT NOT NULL,
    tool_glob TEXT NOT NULL, match_regex TEXT NOT NULL, description TEXT NOT NULL, created_at REAL NOT NULL
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
"""

_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _prepare_dirs(database: Path, image_dir: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)


def pack_vector(values: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def fts_query(terms: Sequence[str]) -> str:
    """An OR query over ``terms``, each quoted so that it is read as plain text."""
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def image_file_name(image: ImageData) -> str:
    """The content-addressed file name under which an image is stored."""
    digest: str = hashlib.sha256(image.data_b64.encode("ascii")).hexdigest()[:24]
    return f"{digest}.{_EXTENSIONS.get(image.media_type, 'bin')}"


def _write_image(directory: Path, image: ImageData) -> str:
    path: Path = directory / image_file_name(image)
    if not path.exists():
        path.write_bytes(base64.b64decode(image.data_b64))
    return path.name


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
# value those runs had in effect: no reasoning level kept, ``ask``, no goal mode.
_ADDED_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("effort", "TEXT"),
    ("approvals", "TEXT NOT NULL DEFAULT 'ask'"),
    ("goal_mode", "INTEGER NOT NULL DEFAULT 0"),
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


async def _add_columns(connection: aiosqlite.Connection, table: str, added: Sequence[tuple[str, str]]) -> None:
    async with connection.execute(f"PRAGMA table_info({table})") as cursor:
        columns: set[str] = {str(row["name"]) for row in await cursor.fetchall()}
    for name, definition in added:
        if name not in columns:
            await connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


async def _add_missing_columns(connection: aiosqlite.Connection) -> None:
    """Give a database created by an older version the columns added since."""
    await _add_columns(connection, "runs", _ADDED_RUN_COLUMNS)
    await _add_columns(connection, "memories", _ADDED_MEMORY_COLUMNS)
    await _add_columns(connection, "schedules", _ADDED_SCHEDULE_COLUMNS)


class Store:
    """Connector to the SQLite database."""

    def __init__(self, connection: aiosqlite.Connection, image_dir: Path) -> None:
        self._db: aiosqlite.Connection = connection
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
        await connection.commit()
        return cls(connection, image_dir)

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
        runs. Threads holding only delegated runs are left out."""
        rows: list[aiosqlite.Row] = await self._all(
            "WITH ranked AS ("
            " SELECT thread_id, id, status, mode, created_at,"
            "  ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY created_at DESC, rowid DESC) AS place,"
            "  COUNT(*) OVER (PARTITION BY thread_id) AS run_count"
            " FROM runs WHERE parent_run_id IS NULL)"
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
        return [thread_id for thread_id in named if thread_id in existing]

    async def append_messages(self, thread_id: str, messages: Sequence[Message]) -> None:
        row: aiosqlite.Row | None = await self._one(
            "SELECT COALESCE(MAX(seq), -1) AS top FROM messages WHERE thread_id = ?", (thread_id,)
        )
        start: int = (-1 if row is None else int(row["top"])) + 1
        encoded: list[tuple[str, int, str]] = await asyncio.to_thread(
            lambda: [
                (thread_id, start + offset, encode_message(message, self._image_dir))
                for offset, message in enumerate(messages)
            ]
        )
        await self._db.executemany("INSERT INTO messages(thread_id, seq, body) VALUES(?, ?, ?)", encoded)
        await self._db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (time.time(), thread_id))
        await self._db.commit()

    async def replace_messages(self, thread_id: str, messages: Sequence[Message]) -> None:
        encoded: list[tuple[str, int, str]] = await asyncio.to_thread(
            lambda: [
                (thread_id, index, encode_message(message, self._image_dir)) for index, message in enumerate(messages)
            ]
        )
        await self._db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
        await self._db.executemany("INSERT INTO messages(thread_id, seq, body) VALUES(?, ?, ?)", encoded)
        await self._db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (time.time(), thread_id))
        await self._db.commit()

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
        )
        await self._db.execute(
            "INSERT INTO runs(id, thread_id, goal, mode, model, status, created_at, finished_at, result, steps,"
            " input_tokens, output_tokens, parent_run_id, source, effort, approvals, goal_mode)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )
        await self._db.commit()
        return record

    async def set_run_status(self, run_id: str, status: RunStatus) -> None:
        await self._db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        await self._db.commit()

    async def update_run_progress(self, run_id: str, steps: int, input_tokens: int, output_tokens: int) -> None:
        await self._db.execute(
            "UPDATE runs SET steps = ?, input_tokens = ?, output_tokens = ? WHERE id = ?",
            (steps, input_tokens, output_tokens, run_id),
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

    async def mark_interrupted_runs(self) -> int:
        """Runs left 'running' by a crashed daemon become 'failed'. Returns how many changed."""
        cursor: aiosqlite.Cursor = await self._db.execute(
            "UPDATE runs SET status = 'failed', result = 'daemon stopped while the run was active', finished_at = ?"
            " WHERE status IN ('running', 'queued')",
            (time.time(),),
        )
        await self._db.commit()
        return cursor.rowcount

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

    async def events_of_kind(self, run_id: str, kind: str, limit: int) -> list[EventRecord]:
        """The newest ``limit`` events of ``kind`` of a run, oldest first."""
        rows = await self._all(
            "SELECT * FROM (SELECT * FROM events WHERE run_id = ? AND kind = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (run_id, kind, limit),
        )
        return [_event(row) for row in rows]

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
        await self._db.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, note = ? WHERE id = ? AND status = 'pending'",
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
            "INSERT INTO memories_fts(id, content, tags) VALUES(?, ?, ?)", (record.id, content, tag_text)
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
            "INSERT INTO memories_fts(id, content, tags) VALUES(?, ?, ?)", (memory_id, content, tag_text)
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
        await self._db.execute("UPDATE memories_fts SET tags = ? WHERE id = ?", (tag_text, memory_id))
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
        """Ids of the memories that contain any of ``terms`` (lowercase words), ranked by BM25, best first."""
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

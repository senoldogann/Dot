"""A chat's revision (item 1686): one integer per thread (``threads.revision``) that moves whenever something a
phone's view of the chat shows changes (its name, pin, seen mark and choices, its runs' and its helpers' runs' states,
its queue and the queue's hold and claim, its reactions, its schedules, its campaign, its meetings, its runs' asks and
drafts, and its helpers let go to the background, which ``kv`` holds under :data:`DETACHED_HELPERS_KEY`).

What must hold is that no writer can miss it: the revision is moved by SQLite triggers on the tables of that state, in
the transaction of the write, whichever method or process (a ``dotx`` command's) writes. The statements are made here,
once; the store creates them at every open, beside the indexes (``Store``'s additive layer), and state kept in memory
(a chat's terminal sessions) moves it with the same statement (:func:`bump_sql`).

The revisions come from one counter for every chat (``kv`` key :data:`COUNTER_KEY`), so each one is larger than any
before it: a chat's revision only grows, though not by one, and the daemon finds the chats that changed since a
revision with one indexed read. A deleted chat leaves a mark (``kv`` key :data:`DELETED_PREFIX` + its id, its value the
revision of the delete) for the daemon to tell, which it then removes. A step of a run (its tokens, its step count, its
events, the words being written) moves nothing: that comes on the run's own stream.
"""

from dataclasses import dataclass

COUNTER_KEY: str = "thread_revision"
DELETED_PREFIX: str = "thread_deleted:"
# Past every key that starts with the prefix (``;`` follows ``:``): the marks are read as the range between.
DELETED_END: str = "thread_deleted;"
# What the ids of a row's thread are read as, for the tables whose rows name a run, not a thread: the run's chat and
# the chat of the run it works for (a helper's run lives in a chat of its own, and its parent's chat shows it).
_RUN_THREADS: str = (
    "SELECT thread_id FROM runs WHERE id = {row}.run_id"
    " UNION SELECT parent.thread_id FROM runs AS child JOIN runs AS parent ON parent.id = child.parent_run_id"
    " WHERE child.id = {row}.run_id"
)


# The ``kv`` key of the helpers let go to the background (``agent.helper_reports.HELPERS_KEY``): a list of them as
# JSON, each with the chat its report goes to (``thread_id``), whose view lists it as background work.
DETACHED_HELPERS_KEY: str = "detached_helpers"
_DETACHED_THREADS: str = (
    "SELECT json_extract(value, '$.thread_id') FROM json_each(CASE WHEN json_valid({row}.value) THEN {row}.value"
    " ELSE '[]' END)"
)


@dataclass(frozen=True)
class RevisionSource:
    """A table whose rows are part of a chat's state: ``threads`` is the SQL that gives the ids of the chats a row
    belongs to, written with ``{row}`` for ``NEW`` or ``OLD``; ``columns`` the columns an update of which moves the
    revision, empty for any column; ``rows`` the condition a row must meet to be state (with ``{row}``), empty for
    every row."""

    table: str
    threads: str
    columns: tuple[str, ...]
    rows: str


SOURCES: tuple[RevisionSource, ...] = (
    # A chat's own row: not ``updated_at``, which every message moves.
    RevisionSource(
        "threads",
        "SELECT {row}.id",
        ("title", "pinned_at", "seen_at", "choices", "agent_id", "team_id", "schedule_id", "history_version"),
        "",
    ),
    # A run's chat and its parent's chat (a helper): its state, not its steps or its tokens.
    RevisionSource(
        "runs",
        "SELECT {row}.thread_id UNION SELECT thread_id FROM runs WHERE id = {row}.parent_run_id",
        ("status", "finished_at", "rewound_at", "thread_id", "end_reason"),
        "",
    ),
    RevisionSource("queued_messages", "SELECT {row}.thread_id", (), ""),
    RevisionSource("queue_holds", "SELECT {row}.thread_id", (), ""),
    RevisionSource("queue_claims", "SELECT {row}.thread_id", (), ""),
    RevisionSource("reactions", "SELECT {row}.thread_id", (), ""),
    RevisionSource("schedules", "SELECT {row}.thread_id", (), ""),
    RevisionSource("campaigns", "SELECT {row}.thread_id", ("status", "thread_id", "title", "next_session_at"), ""),
    # Not ``lines`` and ``updated_at``, which each line said in the meeting moves.
    RevisionSource("meetings", "SELECT {row}.thread_id", ("status", "thread_id", "admitted_at", "ended_at"), ""),
    RevisionSource("approvals", _RUN_THREADS, ("status", "decided_at"), ""),
    RevisionSource("drafts", _RUN_THREADS, ("status", "decided_at"), ""),
    # A helper let go to the background, and its report handed or its parent's end: the chat it reports to.
    RevisionSource("kv", _DETACHED_THREADS, ("value",), f"{{row}}.key = '{DETACHED_HELPERS_KEY}'"),
)

# The tables whose rows name a chat or a run that are not part of what a view of the chat shows, each with why; a
# test fails for a table that names one and is in neither list.
NOT_STATE: dict[str, str] = {
    "messages": "the conversation comes on the runs' streams; a rewind moves threads.history_version",
    "message_archive": "what a rewind set aside; the rewind moves threads.history_version and runs.rewound_at",
    "events": "a run's events come on its own stream, one by one",
    "todos": "a run's plan comes on its stream as events",
    "work_orders": "the agents' desk; the runs an order starts move their chats' revisions",
    "shown_pages": "a page a run showed comes on its stream as an event",
    "run_stops": "a stop asked of a run; the run's status that follows moves the revision",
    "run_taints": "whether a run read outside content, not shown",
    "thread_taints": "whether a chat's runs read outside content, not shown",
    "correspondents": "who a chat's mail went to, not shown",
    "live_activities": "the phone's Live Activity of a run, not a chat's state",
    "memory_uses": "the notes a run was handed, not shown in the chat",
    "campaign_entries": "a campaign's journal, its own page",
    "paced_actions": "the pace of a run's actions on a host, not shown",
    "spends": "what a run spent, the spending page",
    "chat_permits": "a chat's 'don't ask again', the rules page",
    "chat_index": "the words of the chats that search_chats reads, kept by triggers of their own, not shown",
}


def bump_statements(threads: str) -> tuple[str, str]:
    """The two statements that move the revision of the chats ``threads`` (a SELECT of their ids) to a new one: the
    counter's step, then the chats' revision set to it."""
    return (
        f"UPDATE kv SET value = CAST(value AS INTEGER) + 1 WHERE key = '{COUNTER_KEY}'",
        f"UPDATE threads SET revision = (SELECT CAST(value AS INTEGER) FROM kv WHERE key = '{COUNTER_KEY}')"
        f" WHERE id IN ({threads})",
    )


def bump_sql(threads: str) -> str:
    """:func:`bump_statements` as one text, the body of a trigger."""
    counter, chats = bump_statements(threads)
    return f"{counter}; {chats};"


def _trigger(source: RevisionSource, event: str) -> str:
    rows: tuple[str, ...] = {"INSERT": ("NEW",), "UPDATE": ("NEW", "OLD"), "DELETE": ("OLD",)}[event]
    threads: str = " UNION ".join(source.threads.format(row=row) for row in rows)
    when: str = f"{event} OF {', '.join(source.columns)}" if event == "UPDATE" and source.columns else event
    rows_sql: str = " OR ".join(source.rows.format(row=row) for row in rows) if source.rows else ""
    only: str = f" WHEN {rows_sql}" if rows_sql else ""
    return (
        f"CREATE TRIGGER IF NOT EXISTS {source.table}_revision_{event.lower()} AFTER {when} ON {source.table}{only}"
        f" BEGIN {bump_sql(threads)} END"
    )


def _deleted_trigger() -> str:
    """A deleted chat moves the counter and leaves its mark for the daemon to tell."""
    return (
        "CREATE TRIGGER IF NOT EXISTS threads_revision_delete AFTER DELETE ON threads BEGIN"
        f" UPDATE kv SET value = CAST(value AS INTEGER) + 1 WHERE key = '{COUNTER_KEY}';"
        f" INSERT OR REPLACE INTO kv(key, value) SELECT '{DELETED_PREFIX}' || OLD.id, value FROM kv"
        f" WHERE key = '{COUNTER_KEY}'; END"
    )


def revision_statements() -> tuple[str, ...]:
    """The statements that make the counter, the index the daemon reads changes by, and every trigger; each may run
    again and again."""
    triggers: list[str] = []
    for source in SOURCES:
        for event in ("INSERT", "UPDATE", "DELETE"):
            if source.table == "threads" and event == "DELETE":
                triggers.append(_deleted_trigger())
            else:
                triggers.append(_trigger(source, event))
    return (
        f"INSERT OR IGNORE INTO kv(key, value) VALUES ('{COUNTER_KEY}', '0')",
        "CREATE INDEX IF NOT EXISTS threads_revision ON threads(revision)",
        *triggers,
    )

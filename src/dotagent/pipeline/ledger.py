"""The ledger of a continuing piece of work (item 1227): who its items are (``clients``: a client, a lead), each
external action taken for one (``actions``: a mail sent, a call), and what came of them (``outcomes``: a reply, a
meeting, a deal). It lives in a block of the workspace file that is the work's one record (an objective's
``record``), between two markers, as three Markdown tables the owner can read and edit; the rest of the file is theirs
and is never touched.

Every function here is pure: it reads text or a ledger and returns a new one, or raises :class:`LedgerRefusal` with
what to do instead. The file is read and written by :mod:`dotagent.pipeline.record`.

An action is begun before it goes out and ended once its result is known, so that a run that broke off between the two,
or a send whose result was not seen, is never taken for one that failed: a begun or uncertain action blocks a new
attempt at the same action until a run looked (the Sent folder, the channel's history) and ended it as sent, failed or
uncertain. Only an action that ended failed, with what showed it, may be tried again; one sent is never sent again under
its name. Outcomes are kept apart from actions and each says when it happened and where that was seen, so that what
was done is never counted as what came of it."""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, get_args

START_MARK: str = "<!-- dot:pipeline:start"
END_MARK: str = "<!-- dot:pipeline:end -->"
_START_LINE: str = (
    "<!-- dot:pipeline:start — Dot keeps the three tables below with its pipeline tools; a cell may be edited, the "
    "columns stay{outside} -->"
)
# Said in the start line once a run that had read content third parties can author (a reply's) wrote the block: its
# cells may hold a third party's words from then on, and a run that reads them is marked so.
OUTSIDE_WORD: str = "; outside content was written here"
_CLIENTS: str = "Clients"
_ACTIONS: str = "Actions"
_OUTCOMES: str = "Outcomes"
_CLIENT_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "contact",
    "status",
    "last action",
    "last at",
    "expecting",
    "next step",
    "next at",
)
_ACTION_COLUMNS: tuple[str, ...] = ("key", "client", "action", "variant", "state", "tries", "at", "evidence")
_OUTCOME_COLUMNS: tuple[str, ...] = ("client", "outcome", "at", "source")
_ID: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_MINUTE: str = "%Y-%m-%d %H:%M"
# Below this many clients reached, a rate says little: the comparison says so.
FEW: int = 5

type ClientStatus = Literal[
    "new", "contacted", "waiting", "replied", "meeting", "proposal", "won", "lost", "paused", "do-not-contact"
]
type ActionState = Literal["begun", "sent", "failed", "uncertain"]
type ActionResult = Literal["sent", "failed", "uncertain"]
type OutcomeKind = Literal["reply", "meeting", "proposal", "won", "lost", "declined", "bounced", "unsubscribed"]
CLIENT_STATUSES: tuple[str, ...] = get_args(ClientStatus.__value__)
OUTCOME_KINDS: tuple[str, ...] = get_args(OutcomeKind.__value__)
ACTION_STATES: tuple[str, ...] = get_args(ActionState.__value__)
# The states in which nobody may try an action again before a run looked at what became of it.
_UNSETTLED: frozenset[str] = frozenset({"begun", "uncertain"})
# The statuses of a client no action is begun for: the owner said not to, or paused them.
_HELD: frozenset[str] = frozenset({"paused", "do-not-contact"})
# The outcomes that show a client answered the action before them.
_ANSWERS: frozenset[str] = frozenset({"reply", "meeting", "proposal", "won", "declined"})


class LedgerRefusal(Exception):
    """What a ledger change may not do, and what to do instead; the file is left as it was. ``quoted`` is the part that
    quotes the record (a client's name, an action's evidence), which a reply's run may have written; ``advice`` is
    Dot's own words, which quote nothing of it, so that a run handed the record's words fenced still reads what to do
    (the third pre-push review of item 1227)."""

    def __init__(self, quoted: str, advice: str) -> None:
        super().__init__(f"{quoted} {advice}".strip())
        self.quoted: str = quoted
        self.advice: str = advice


class LedgerFormatError(Exception):
    """The record's block cannot be read safely (a marker without its pair, a table whose columns changed); nothing is
    written to it until it is mended."""


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    contact: str
    status: str
    last_action: str
    last_at: str
    expecting: str
    next_step: str
    next_at: str


@dataclass(frozen=True)
class Action:
    """``key`` is ``<client>/<action>``: one row a client's action, however often it was tried (``tries``)."""

    key: str
    client: str
    action: str
    variant: str
    state: str
    tries: int
    at: str
    evidence: str


@dataclass(frozen=True)
class Outcome:
    client: str
    kind: str
    at: str
    source: str


@dataclass(frozen=True)
class Ledger:
    """``outside`` says that a run that had read content third parties can author wrote it."""

    clients: tuple[Client, ...]
    actions: tuple[Action, ...]
    outcomes: tuple[Outcome, ...]
    outside: bool


@dataclass(frozen=True)
class ClientChange:
    """The parts of a client a change sets; ``None`` keeps a part, an empty string clears it."""

    name: str | None
    contact: str | None
    status: str | None
    expecting: str | None
    next_step: str | None
    next_at: str | None


@dataclass(frozen=True)
class VariantResult:
    """How one way of acting did, from the ledger alone: the clients an action of it went to (``reached``), those who
    answered after it (``answered``), and those it won."""

    variant: str
    reached: int
    answered: int
    won: int


EMPTY: Ledger = Ledger(clients=(), actions=(), outcomes=(), outside=False)


def minute(at: datetime) -> str:
    """A moment as the ledger writes it, to the minute, in the Mac's time."""
    return at.strftime(_MINUTE)


def moment(text: str) -> datetime:
    """A date (``2026-11-19``) or a moment (``2026-11-19 10:02``, ``2026-11-19T10:02``, ``…T08:15Z``) the ledger reads,
    in the Mac's time: a moment with an offset is moved to it."""
    try:
        read: datetime = datetime.fromisoformat(text.strip())
    except ValueError as error:
        raise LedgerRefusal(
            "", f"'{text}' is no date: write it as 2026-11-19 or 2026-11-19 10:02, as the source shows it"
        ) from error
    return read if read.tzinfo is None else read.astimezone().replace(tzinfo=None)


def _dated_only(text: str) -> bool:
    return len(text.strip()) == len("2026-11-19")


def _cell(text: str) -> str:
    """A cell's text on one line, its pipes escaped and no comment mark in it: a third party's words that held one (a
    reply's) would end the block, and every later read of it would fail."""
    flat: str = " ".join(text.split()).replace("<!--", "<! --").replace("-->", "-- >")
    return flat.replace("|", "\\|")


def _uncell(text: str) -> str:
    return text.strip().replace("\\|", "|")


def _cells(line: str) -> list[str]:
    """The cells of a table row: split on the pipes no backslash escapes."""
    inner: str = line.strip()
    inner = inner.removeprefix("|")
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [_uncell(cell) for cell in re.split(r"(?<!\\)\|", inner)]


def _table(lines: list[str], columns: tuple[str, ...], name: str) -> list[list[str]]:
    """The rows of the table under ``### name``: its header must be ``columns``, as the ledger wrote it."""
    rows: list[str] = [line for line in lines if line.strip().startswith("|")]
    if not rows:
        return []
    header: list[str] = [cell.casefold() for cell in _cells(rows[0])]
    if tuple(header) != columns:
        raise LedgerFormatError(
            f"the {name} table's columns are {header}, not {list(columns)}: put them back before Dot writes to it"
        )
    body: list[list[str]] = []
    for line in rows[2:]:
        cells: list[str] = _cells(line)
        if len(cells) != len(columns):
            raise LedgerFormatError(f"a row of the {name} table has {len(cells)} cells, not {len(columns)}: {line!r}")
        body.append(cells)
    return body


def _sections(block: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return found


def _bounds(text: str) -> tuple[int, int] | None:
    """Where the block starts and where its end marker ends, or ``None`` for a file without one."""
    start: int = text.find(START_MARK)
    end: int = text.find(END_MARK)
    if start == -1 and end == -1:
        return None
    if start == -1 or end == -1 or end < start or text.count(START_MARK) > 1 or text.count(END_MARK) > 1:
        raise LedgerFormatError(
            "the record has its pipeline markers out of order, twice or one without the other: mend them before Dot "
            "writes to it"
        )
    return start, end + len(END_MARK)


def parse(text: str) -> Ledger:
    """The ledger in a record's text; empty for a record that has no block yet."""
    bounds: tuple[int, int] | None = _bounds(text)
    if bounds is None:
        return EMPTY
    block: str = text[bounds[0] : bounds[1]]
    sections: dict[str, list[str]] = _sections(block)
    clients: tuple[Client, ...] = tuple(
        Client(*cells) for cells in _table(sections.get(_CLIENTS, []), _CLIENT_COLUMNS, _CLIENTS)
    )
    for kept in clients:
        # A status edited by hand that is none of the ledger's would slip past a hold: the block is not used then.
        if kept.status not in CLIENT_STATUSES:
            raise LedgerFormatError(
                f"the status of the client {kept.id} is '{kept.status}', none of {', '.join(CLIENT_STATUSES)}: "
                "write one of them before Dot uses the record"
            )
    actions: list[Action] = []
    for key, client, action, variant, state, tries, at, evidence in _table(
        sections.get(_ACTIONS, []), _ACTION_COLUMNS, _ACTIONS
    ):
        if not tries.isdigit():
            raise LedgerFormatError(f"the tries of the action {key} are '{tries}', not a number")
        if state not in ACTION_STATES:
            # 'Sent' or 'sent?' read as no state would let a sent mail be begun again.
            raise LedgerFormatError(
                f"the state of the action {key} is '{state}', none of {', '.join(ACTION_STATES)}: write one of them "
                "before Dot uses the record"
            )
        actions.append(Action(key, client, action, variant, state, int(tries), at, evidence))
    outcomes: tuple[Outcome, ...] = tuple(
        Outcome(*cells) for cells in _table(sections.get(_OUTCOMES, []), _OUTCOME_COLUMNS, _OUTCOMES)
    )
    first_line: str = block.split("\n", 1)[0]
    return Ledger(clients=clients, actions=tuple(actions), outcomes=outcomes, outside=OUTSIDE_WORD in first_line)


def _render_table(name: str, columns: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines: list[str] = [
        f"### {name}",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows),
    ]
    return "\n".join(lines)


def render(ledger: Ledger) -> str:
    """The block of ``ledger``, markers included."""
    clients: list[tuple[str, ...]] = [
        (c.id, c.name, c.contact, c.status, c.last_action, c.last_at, c.expecting, c.next_step, c.next_at)
        for c in ledger.clients
    ]
    actions: list[tuple[str, ...]] = [
        (a.key, a.client, a.action, a.variant, a.state, str(a.tries), a.at, a.evidence) for a in ledger.actions
    ]
    outcomes: list[tuple[str, ...]] = [(o.client, o.kind, o.at, o.source) for o in ledger.outcomes]
    return "\n\n".join(
        (
            _START_LINE.format(outside=OUTSIDE_WORD if ledger.outside else ""),
            _render_table(_CLIENTS, _CLIENT_COLUMNS, clients),
            _render_table(_ACTIONS, _ACTION_COLUMNS, actions),
            _render_table(_OUTCOMES, _OUTCOME_COLUMNS, outcomes),
            END_MARK,
        )
    )


def written(text: str, ledger: Ledger) -> str:
    """``text`` with its block replaced by ``ledger``'s, or the block added at its end: nothing else changes."""
    block: str = render(ledger)
    bounds: tuple[int, int] | None = _bounds(text)
    if bounds is None:
        lead: str = "" if not text else text if text.endswith("\n\n") else text.rstrip("\n") + "\n\n"
        return f"{lead}{block}\n"
    return text[: bounds[0]] + block + text[bounds[1] :]


def _client(ledger: Ledger, client_id: str) -> Client:
    found: Client | None = next((each for each in ledger.clients if each.id == client_id), None)
    if found is None:
        known: str = ", ".join(each.id for each in ledger.clients[:20]) or "none yet"
        raise LedgerRefusal(
            f"no client has the id '{client_id}' in this record (known: {known}).", "Add it first with pipeline_client."
        )
    return found


def _status(status: str) -> str:
    if status not in CLIENT_STATUSES:
        raise LedgerRefusal("", f"'{status}' is no client status: one of {', '.join(CLIENT_STATUSES)}")
    return status


def upsert_client(ledger: Ledger, client_id: str, change: ClientChange) -> Ledger:
    """``ledger`` with the client ``client_id`` added (a name is needed then) or changed as ``change`` says."""
    if not _ID.match(client_id):
        raise LedgerRefusal(
            "", f"'{client_id}' is no client id: lower-case letters, digits and hyphens, as kuivaksen-leipomo"
        )
    if change.status is not None:
        _status(change.status)
    if change.next_at:
        moment(change.next_at)
    present: Client | None = next((each for each in ledger.clients if each.id == client_id), None)
    if present is None:
        if not change.name:
            raise LedgerRefusal("", f"a new client needs its name: '{client_id}' is not in this record yet")
        added: Client = Client(
            id=client_id,
            name=change.name,
            contact=change.contact or "",
            status=change.status or "new",
            last_action="",
            last_at="",
            expecting=change.expecting or "",
            next_step=change.next_step or "",
            next_at=change.next_at or "",
        )
        return replace(ledger, clients=(*ledger.clients, added))
    changed: Client = replace(
        present,
        **{
            name: value
            for name, value in (
                ("name", change.name),
                ("contact", change.contact),
                ("status", change.status),
                ("expecting", change.expecting),
                ("next_step", change.next_step),
                ("next_at", change.next_at),
            )
            if value is not None
        },
    )
    return replace(ledger, clients=tuple(changed if each.id == client_id else each for each in ledger.clients))


def action_key(client_id: str, action: str) -> str:
    return f"{client_id}/{action}"


def begin(ledger: Ledger, client_id: str, action: str, variant: str, now: str) -> Ledger:
    """``ledger`` with ``action`` for the client begun at ``now``: written down before it goes out, so that a run that
    breaks off before its result is known leaves it begun, not missing. Refused for a client the owner holds back, for
    an action sent before, and for one begun or uncertain whose result nobody has looked at."""
    client: Client = _client(ledger, client_id)
    if client.status in _HELD:
        raise LedgerRefusal(
            f"{client.name} is {client.status}.",
            "Nothing is sent to a client held back (do-not-contact, paused); its status changes only on the owner's "
            "word.",
        )
    if not _ID.match(action):
        raise LedgerRefusal("", f"'{action}' is no action name: lower-case letters, digits and hyphens, as first-mail")
    key: str = action_key(client_id, action)
    present: Action | None = next((each for each in ledger.actions if each.key == key), None)
    if present is not None and present.state == "sent":
        raise LedgerRefusal(
            f"{key} was sent at {present.at} ({present.evidence}).",
            "It is not sent again: a further one is an action of its own name (reminder-1), begun when the rules call "
            "for it.",
        )
    if present is not None and present.state in _UNSETTLED:
        raise LedgerRefusal(
            f"{key} was {present.state} at {present.at}.",
            "Nobody has seen what came of it. Look first (the Sent folder, the channel's history, the recipient's "
            "reply), then end it with pipeline_end: sent with what showed it, failed with what showed it did not go, "
            "or uncertain. Only an action that failed is tried again.",
        )
    started: Action = Action(
        key=key,
        client=client_id,
        action=action,
        variant=variant,
        state="begun",
        tries=1 if present is None else present.tries + 1,
        at=now,
        evidence="",
    )
    actions: tuple[Action, ...] = (
        (*ledger.actions, started)
        if present is None
        else tuple(started if each.key == key else each for each in ledger.actions)
    )
    return replace(ledger, actions=actions)


def end(ledger: Ledger, key: str, result: ActionResult, evidence: str, now: str) -> Ledger:
    """``ledger`` with the begun or uncertain action ``key`` ended as ``result`` at ``now``, with what showed it. A sent
    action is the client's last; one that failed may be begun again; one uncertain stays in the way until it is
    looked at again."""
    present: Action | None = next((each for each in ledger.actions if each.key == key), None)
    if present is None:
        raise LedgerRefusal("", f"no action has the key '{key}': begin it with pipeline_begin before it goes out")
    if present.state not in _UNSETTLED:
        raise LedgerRefusal(f"{key} ended {present.state} at {present.at} already ({present.evidence}).", "")
    if not evidence.strip():
        raise LedgerRefusal(
            "",
            "say what showed it: the mail in Sent and its time, the error the service gave, or, for uncertain, what "
            "was seen and what was not",
        )
    ended: Action = replace(present, state=result, at=now, evidence=evidence.strip())
    actions: tuple[Action, ...] = tuple(ended if each.key == key else each for each in ledger.actions)
    found: Client | None = next((each for each in ledger.clients if each.id == present.client), None)
    if result != "sent" or found is None:
        # A client whose row the owner took away still has its action ended, so that the key is not held for good.
        return replace(ledger, actions=actions)
    client: Client = found
    moved: Client = replace(
        client,
        last_action=present.action,
        last_at=now,
        status="contacted" if client.status == "new" else client.status,
    )
    clients: tuple[Client, ...] = tuple(moved if each.id == client.id else each for each in ledger.clients)
    return replace(ledger, clients=clients, actions=actions)


def note_outcome(ledger: Ledger, client_id: str, kind: str, at: str, source: str) -> tuple[Ledger, bool]:
    """``ledger`` with what came of the work for a client: when (``at``, as the source dates it) and where that was
    seen (``source``). The same outcome at the same moment is kept once (``False`` then: it was there)."""
    _client(ledger, client_id)
    if kind not in OUTCOME_KINDS:
        raise LedgerRefusal("", f"'{kind}' is no outcome: one of {', '.join(OUTCOME_KINDS)}")
    if not source.strip():
        raise LedgerRefusal(
            "", "say where it was seen (the mail and its time, the call, the page): no outcome is guessed"
        )
    when: str = at.strip() if _dated_only(at) else minute(moment(at))
    moment(when)
    if any(each.client == client_id and each.kind == kind and each.at == when for each in ledger.outcomes):
        return ledger, False
    added: Outcome = Outcome(client=client_id, kind=kind, at=when, source=source.strip())
    return replace(ledger, outcomes=(*ledger.outcomes, added)), True


def pending(ledger: Ledger) -> tuple[Action, ...]:
    """The actions begun or uncertain: each is to be looked at before anything else is done for its client."""
    return tuple(each for each in ledger.actions if each.state in _UNSETTLED)


def compare(ledger: Ledger) -> tuple[VariantResult, ...]:
    """How each variant of the actions did, from what the ledger holds and nothing else: the clients a sent action of
    it reached, those with an answer dated at or after that send, and those won. An action with no variant counts
    under its own name."""
    first_sent: dict[tuple[str, str], datetime] = {}
    for each in ledger.actions:
        if each.state != "sent":
            continue
        name: str = each.variant or each.action
        sent_at: datetime = moment(each.at)
        held: datetime | None = first_sent.get((name, each.client))
        first_sent[(name, each.client)] = sent_at if held is None or sent_at < held else held
    results: list[VariantResult] = []
    for name in sorted({variant for variant, _ in first_sent}):
        reached: dict[str, datetime] = {client: at for (variant, client), at in first_sent.items() if variant == name}
        answered: int = sum(
            1
            for client, sent_at in reached.items()
            if any(o.client == client and o.kind in _ANSWERS and _after(o.at, sent_at) for o in ledger.outcomes)
        )
        won: int = sum(1 for client in reached if any(o.client == client and o.kind == "won" for o in ledger.outcomes))
        results.append(VariantResult(variant=name, reached=len(reached), answered=answered, won=won))
    return tuple(results)


def _after(at: str, sent_at: datetime) -> bool:
    """Whether an outcome dated ``at`` came at or after a send at ``sent_at``: one dated by its day alone counts from
    that day, so a reply of the same day is not taken for one before the send."""
    when: datetime = moment(at)
    return when.date() >= sent_at.date() if _dated_only(at) else when >= sent_at


def summary(ledger: Ledger, client_id: str | None) -> str:
    """The ledger for a run to read: what is unsettled first, then the clients (or the one asked), then how each way
    of acting did."""
    lines: list[str] = []
    unsettled: tuple[Action, ...] = tuple(
        each for each in pending(ledger) if client_id is None or each.client == client_id
    )
    if unsettled:
        lines.append("Unsettled, to look at before anything else for these clients:")
        lines.extend(f"- {a.key} {a.state} since {a.at} (try {a.tries}) {a.evidence}".rstrip() for a in unsettled)
    shown: tuple[Client, ...] = (
        ledger.clients if client_id is None else tuple(c for c in ledger.clients if c.id == client_id)
    )
    if client_id is not None and not shown:
        raise LedgerRefusal("", f"no client has the id '{client_id}' in this record")
    lines.append(f"Clients ({len(shown)}):" if shown else "No clients in this record yet.")
    for c in shown:
        done: str = f"last {c.last_action} at {c.last_at}" if c.last_action else "nothing sent yet"
        waits: str = f"; expecting {c.expecting}" if c.expecting else ""
        nxt: str = f"; next: {c.next_step}" + (f" at {c.next_at}" if c.next_at else "") if c.next_step else ""
        lines.append(f"- {c.id} ({c.name}, {c.contact or 'no contact'}) {c.status}; {done}{waits}{nxt}")
        if client_id is not None:
            lines.extend(
                f"  action {a.action} {a.state} at {a.at} (try {a.tries}): {a.evidence}"
                for a in ledger.actions
                if a.client == c.id
            )
            lines.extend(f"  outcome {o.kind} at {o.at} ({o.source})" for o in ledger.outcomes if o.client == c.id)
    compared: tuple[VariantResult, ...] = compare(ledger)
    if compared:
        lines.append("How each way of acting did (from this record only):")
        for result in compared:
            few: str = f"; {result.reached} is too few to tell" if result.reached < FEW else ""
            lines.append(
                f"- {result.variant}: reached {result.reached}, answered {result.answered}, won {result.won}{few}"
            )
    return "\n".join(lines)

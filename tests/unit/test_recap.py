"""The recap of a run (item 536): when a new one is due, the record it is written from, and what of the model's text
the section shows."""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from dotagent.agent import recap
from dotagent.agent.plan import EMPTY_PLAN, PlanItem, PlanPayload
from dotagent.agent.recap import Recap, RecapView, clipped, is_due, recap_record, shown
from dotagent.config import RecapConfig
from dotagent.llm.types import assistant_message, user_message
from dotagent.runtime import Runtime
from dotagent.store.models import ApprovalRecord, ApprovalStatus, RunRecord, RunStatus

CONFIG: RecapConfig = RecapConfig(enabled=True, first_after_steps=4, every_steps=8)


def _run(steps: int, status: RunStatus) -> RunRecord:
    return RunRecord(
        id="run_a",
        thread_id="thr_a",
        goal="Gather this week's invoices into one folder",
        mode="task",
        model="ollama:own",
        status=status,
        created_at=0.0,
        finished_at=None,
        result="",
        steps=steps,
        input_tokens=0,
        output_tokens=0,
        parent_run_id=None,
        source="ui",
        effort=None,
        speed="standard",
        once=None,
        approvals="ask",
        goal_mode=False,
        campaign_id=None,
        agent_id=None,
        rewindable=False,
    )


def _recap(steps: int, status: str) -> Recap:
    return Recap(text="Found 3 invoices.", model="ollama:fast", steps=steps, status=status, written_at=0.0)


def _approval(status: ApprovalStatus, summary: str) -> ApprovalRecord:
    return ApprovalRecord(
        id=f"apr_{status}",
        run_id="run_a",
        created_at=0.0,
        decided_at=None,
        status=status,
        tool="ask_owner",
        summary=summary,
        severity="low",
        args={},
        decided_by=None,
        note="",
        digest=None,
        kept_until=None,
        permit_until=None,
        used_at=None,
        scope_thread_id=None,
        scope_campaign_id=None,
    )


def test_the_first_recap_is_due_once_the_run_took_enough_steps() -> None:
    assert not is_due(None, _run(3, "running"), CONFIG)
    assert is_due(None, _run(4, "running"), CONFIG)
    # A short run that ended has no recap: what it did fits its page.
    assert not is_due(None, _run(2, "done"), CONFIG)


def test_a_new_recap_is_due_every_few_steps_and_once_more_when_the_run_ended() -> None:
    assert not is_due(_recap(4, "running"), _run(11, "running"), CONFIG)
    assert is_due(_recap(4, "running"), _run(12, "running"), CONFIG)
    # The run ended after its last recap: one more says how it ended; one written after the end is the last.
    assert is_due(_recap(10, "running"), _run(11, "done"), CONFIG)
    assert is_due(_recap(10, "blocked"), _run(10, "failed"), CONFIG)
    assert not is_due(_recap(11, "done"), _run(11, "done"), CONFIG)


def test_no_recap_is_due_while_recaps_are_off() -> None:
    off: RecapConfig = CONFIG.model_copy(update={"enabled": False})
    assert not is_due(None, _run(40, "running"), off)
    assert not is_due(_recap(4, "running"), _run(40, "done"), off)


def test_the_record_holds_the_task_its_state_its_plan_what_waits_and_the_conversation() -> None:
    plan: PlanPayload = PlanPayload(
        steps=(PlanItem(text="List the invoices", status="done"), PlanItem(text="Copy them", status="in_progress"))
    )
    approvals: list[ApprovalRecord] = [
        _approval("pending", "Copy the invoices into ~/Invoices?"),
        _approval("left", "Which year should go first?"),
        _approval("approved", "Read the Downloads folder?"),
    ]
    messages = [user_message("Gather this week's invoices", ()), assistant_message("Found 3 invoices.", None, ())]
    record: str = recap_record(_run(9, "blocked"), plan, approvals, messages)
    assert "Gather this week's invoices into one folder" in record
    assert "blocked, 9 steps" in record and "it has ended" not in record
    assert "List the invoices" in record and "Copy them" in record
    assert "waits for the owner's answer: Copy the invoices into ~/Invoices?" in record
    assert "left for the owner to answer later: Which year should go first?" in record
    # An answered question waits for nobody.
    assert "Read the Downloads folder?" not in record
    assert "Found 3 invoices." in record
    ended: str = recap_record(_run(9, "done"), EMPTY_PLAN, [], messages)
    assert "done, 9 steps, it has ended" in ended
    assert "# What waits for the owner\nnothing" in ended


def test_a_secret_the_cut_runs_through_leaves_no_part_of_itself() -> None:
    """The pre-push review: masked after the cut, a secret across the 1,200th character kept its first part."""
    secret: str = "Kq7Vb2Lm9Xw4Rt6Yz1Np3Hs8Jd5Fg0Ca"
    text: str = "a" * 1_180 + " " + secret + " and the words after it"
    assert secret[:10] in clipped(text)
    out: str = shown(text, (secret,))
    assert secret[:10] not in out and "***" in out and len(out) <= 1_200


class _Recaps:
    """One run's recaps as the stand-ins of ``_latest`` and ``_write`` keep them: the newest, and the asks that wrote.
    An ask without force waits for ``released`` and writes one only when ``unforced_writes``; a forced one writes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, unforced_writes: bool) -> None:
        self.newest: Recap | None = None
        self.asked: list[bool] = []
        self.released: asyncio.Event = asyncio.Event()
        self._unforced_writes: bool = unforced_writes

        async def get_run(run_id: str) -> RunRecord:
            return _run(9, "running")

        self.runtime: Runtime = cast(
            "Runtime", SimpleNamespace(config=SimpleNamespace(recap=CONFIG), store=SimpleNamespace(get_run=get_run))
        )
        monkeypatch.setattr(recap, "_latest", self._latest)
        monkeypatch.setattr(recap, "_write", self._write)

    async def _latest(self, runtime: Runtime, run_id: str) -> Recap | None:
        return self.newest

    async def _write(self, runtime: Runtime, run_id: str, force: bool) -> RecapView:
        self.asked.append(force)
        if not force:
            await self.released.wait()
        if force or self._unforced_writes:
            self.newest = Recap(
                text=f"written by ask {len(self.asked)}",
                model="ollama:fast",
                steps=9,
                status="running",
                written_at=float(len(self.asked)),
            )
        return RecapView(enabled=True, recap=self.newest, steps=9, status="running", due=False)

    async def ask(self, force: bool) -> asyncio.Task[RecapView]:
        """An ask, once it has read the recap there is and waits in its turn."""
        task: asyncio.Task[RecapView] = asyncio.create_task(recap.write_recap(self.runtime, "run_a", force))
        for _ in range(3):
            await asyncio.sleep(0)
        return task


async def test_a_forced_ask_behind_an_ask_that_wrote_none_writes_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-push review: the button pressed while another page's ask found none due wrote nothing."""
    recaps: _Recaps = _Recaps(monkeypatch, unforced_writes=False)
    looking: asyncio.Task[RecapView] = await recaps.ask(False)
    pressed: asyncio.Task[RecapView] = await recaps.ask(True)
    recaps.released.set()
    assert (await looking).recap is None
    view: RecapView = await pressed
    assert view.recap is not None and view.recap.text == "written by ask 2"
    assert recaps.asked == [False, True]


async def test_two_forced_asks_behind_an_ask_that_wrote_none_write_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-push review: both went on and each wrote its own; then a loop of joins could spin without end."""
    recaps: _Recaps = _Recaps(monkeypatch, unforced_writes=False)
    looking: asyncio.Task[RecapView] = await recaps.ask(False)
    here: asyncio.Task[RecapView] = await recaps.ask(True)
    there: asyncio.Task[RecapView] = await recaps.ask(True)
    recaps.released.set()
    await looking
    texts: set[str] = {view.recap.text for view in (await here, await there) if view.recap is not None}
    assert texts == {"written by ask 2"}
    assert recaps.asked == [False, True]


async def test_an_ask_behind_an_ask_that_wrote_one_takes_it_and_writes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    recaps: _Recaps = _Recaps(monkeypatch, unforced_writes=True)
    first: asyncio.Task[RecapView] = await recaps.ask(False)
    second: asyncio.Task[RecapView] = await recaps.ask(True)
    recaps.released.set()
    views: list[RecapView] = [await first, await second]
    assert [view.recap.text for view in views if view.recap is not None] == ["written by ask 1"] * 2
    assert recaps.asked == [False]


def test_the_section_shows_a_few_lines_and_a_few_hundred_characters_whatever_the_model_wrote() -> None:
    assert clipped("  First line.\n\n\nSecond line.  \n") == "First line.\nSecond line."
    many: str = "\n".join(f"Line {number}." for number in range(20))
    assert clipped(many).splitlines() == [f"Line {number}." for number in range(8)]
    long: str = clipped("word " * 600)
    assert len(long) <= 1_200 and long.endswith("…")

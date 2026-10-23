"""The records of the improvement loop and how they are bounded (item 589): the versions come a page at a time and are
counted by state, the records older than ``[harness] keep_days`` are deleted but for the adopted version and its
lineage, and an attempt of a probe keeps the approvals its run asked and how many of the task's checks held."""

import json
import sqlite3
from pathlib import Path

import pytest

from dotagent.cli_harness import render_lineage
from dotagent.evals.results import CheckOutcome, RunMetrics, TaskOutcome
from dotagent.harness.graph import lineage_of
from dotagent.harness.probe import card_of
from dotagent.harness.service import taint_of
from dotagent.store.db import Store
from dotagent.store.models import HarnessPruned, HarnessVersion

OLD: float = 1_000.0
RECENT: float = 10_000.0
CUTOFF: float = 5_000.0
CARD: tuple[object, ...] = ("write-todo-note", 1, True, "done", "none", 2, 100, 10, 0, 1.0, 0, 3, 3)


async def _store(tmp_path: Path) -> Store:
    return await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")


def _set(tmp_path: Path, statement: str, *arguments: object) -> None:
    """Make a record look as old as the test needs: the store writes the time of now and nothing else."""
    connection: sqlite3.Connection = sqlite3.connect(tmp_path / "dot.sqlite3")
    try:
        connection.execute(statement, arguments)
        connection.commit()
    finally:
        connection.close()


async def _version(
    store: Store, tmp_path: Path, state: str, parent: str | None = None, notes: str = "", when: float = OLD
) -> HarnessVersion:
    made: HarnessVersion = await store.create_harness_version(parent, "proposer", "A habit", "- Read first.", "", notes)
    _set(
        tmp_path,
        "UPDATE harness_versions SET state = ?, created_at = ?, retired_at = ?, adopted_at = ? WHERE id = ?",
        state,
        when,
        when if state == "retired" else None,
        when if state == "adopted" else None,
        made.id,
    )
    if state == "adopted":
        await store.adopt_harness(made.id, when)
        _set(tmp_path, "UPDATE harness_versions SET adopted_at = ? WHERE id = ?", when, made.id)
    return await store.get_harness_version(made.id)


async def test_the_records_older_than_the_days_kept_go_but_the_adopted_version_and_its_lineage(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        # The version in use came from a version and was composed of two candidates: all four are its lineage.
        grandparent: HarnessVersion = await _version(store, tmp_path, "retired")
        part_one: HarnessVersion = await _version(store, tmp_path, "retired")
        part_two: HarnessVersion = await _version(store, tmp_path, "retired")
        notes: str = json.dumps({"composed_of": [part_one.id, part_two.id]})
        in_use: HarnessVersion = await _version(store, tmp_path, "adopted", grandparent.id, notes)
        loser: HarnessVersion = await _version(store, tmp_path, "retired")
        recent_loser: HarnessVersion = await _version(store, tmp_path, "retired", when=RECENT)
        waiting: HarnessVersion = await _version(store, tmp_path, "probed")
        # Steps: one touched the version in use, one touched nothing that stays, one is recent.
        touching = await store.create_harness_step(OLD, None, None)
        other = await store.create_harness_step(OLD, None, None)
        recent = await store.create_harness_step(RECENT, None, None)
        for step, outcome in ((touching, {"probed": in_use.id}), (other, {"candidates": [loser.id]}), (recent, {})):
            await store.finish_harness_step(step.id, step.started_at + 1, "done", json.dumps(outcome), None)
        _set(tmp_path, "UPDATE harness_steps SET started_at = ? WHERE id IN (?, ?)", OLD, touching.id, other.id)
        # Attempts: those of a deleted step, those of a deleted version in a step that stays, and those that stay.
        await store.add_harness_probes(other.id, loser.id, loser.id, [CARD, CARD], OLD)
        await store.add_harness_probes(touching.id, loser.id, loser.id, [CARD], OLD)
        await store.add_harness_probes(touching.id, in_use.id, in_use.id, [CARD, CARD], OLD)
        await store.add_harness_probes(recent.id, None, "base", [CARD], RECENT)
        # Experiences: about the version in use, about a deleted one, about none, and a recent one.
        for step, version, when in (
            (touching, in_use, OLD),
            (other, loser, OLD),
            (other, None, OLD),
            (recent, None, RECENT),
        ):
            made = await store.add_harness_experience(
                step.id, None if version is None else version.id, "a cue", "a lesson", "edit 1", "low", False
            )
            _set(tmp_path, "UPDATE harness_experiences SET created_at = ? WHERE id = ?", when, made.id)

        gone: HarnessPruned = await store.prune_harness(CUTOFF)
        assert gone == HarnessPruned(versions=1, steps=1, probes=3, experiences=2)
        left: set[str] = {version.id for version in await store.list_harness_versions()}
        assert left == {grandparent.id, part_one.id, part_two.id, in_use.id, recent_loser.id, waiting.id}
        assert {step.id for step in await store.list_harness_steps(10)} == {touching.id, recent.id}
        assert (
            len(await store.harness_probes_of(touching.id)) == 2 and len(await store.harness_probes_of(other.id)) == 0
        )
        assert len(await store.harness_probes_of(recent.id)) == 1
        kept = await store.list_harness_experiences(10)
        assert sorted(experience.version_id is None for experience in kept) == [False, True]
        assert (await store.adopted_harness_id()) == in_use.id
        # Once more: nothing left to delete.
        assert await store.prune_harness(CUTOFF) == HarnessPruned(versions=0, steps=0, probes=0, experiences=0)
    finally:
        await store.close()


async def test_the_lineage_counts_each_kind_of_attempt_on_a_line_of_its_own(tmp_path: Path) -> None:
    """Item 589, pre-push review round 2: ``dotx harness show`` added every attempt recorded under a version into one
    line: the step's own, the confirmation's (twice as many, on new attempts) and those of the times it stood as the
    base. The counts answer different questions, so each kind has a line, and an attempt under a label that is none
    of them is not left out."""
    store: Store = await _store(tmp_path)
    try:
        version: HarnessVersion = await _version(store, tmp_path, "adopted")
        step = await store.create_harness_step(OLD, None, None)
        miss: tuple[object, ...] = ("write-todo-note", 1, False, "failed", "none", 2, 100, 10, 0, 1.0, 1, 1, 3)
        await store.add_harness_probes(step.id, version.id, version.id, [CARD, miss], OLD)
        await store.add_harness_probes(step.id, version.id, f"confirm-{version.id}", [CARD] * 4, OLD)
        await store.add_harness_probes(step.id, version.id, "base", [miss, miss], OLD)
        await store.add_harness_probes(step.id, version.id, "confirm-base", [CARD] * 3, OLD)
        await store.add_harness_probes(step.id, version.id, "an-older-label", [CARD], OLD)
        lines: list[str] = render_lineage(await lineage_of(store, version.id))
    finally:
        await store.close()
    assert [line for line in lines if line.startswith("## probes")] == [
        "## probes in the step that tried it: 1 of 2 attempts passed, 4 of 6 checks held, 1 approvals asked",
        "## probes in the confirmation, on new attempts: 4 of 4 attempts passed, 12 of 12 checks held, "
        "0 approvals asked",
        "## probes as the base: 0 of 2 attempts passed, 2 of 6 checks held, 2 approvals asked",
        "## probes as the base in a confirmation: 3 of 3 attempts passed, 9 of 9 checks held, 0 approvals asked",
        "## probes labelled an-older-label: 1 of 1 attempts passed, 3 of 3 checks held, 0 approvals asked",
    ]


async def test_a_version_that_waits_keeps_its_parts_so_that_it_is_not_taken_for_tainted(tmp_path: Path) -> None:
    """Item 589, pre-push review: a composed survivor that waits past the days kept itself, and the candidates it was
    composed of, retired the night it was written, went; ``taint_of`` found a part gone, which it counts as a taint, and
    the page asked before **Use** of a version nothing tainted. What waits keeps its lineage as the version in use does,
    and no longer once it is set aside."""
    store: Store = await _store(tmp_path)
    try:
        parent: HarnessVersion = await _version(store, tmp_path, "retired")
        part_one: HarnessVersion = await _version(store, tmp_path, "retired")
        part_two: HarnessVersion = await _version(store, tmp_path, "retired")
        part_three: HarnessVersion = await _version(store, tmp_path, "retired")
        loser: HarnessVersion = await _version(store, tmp_path, "retired")
        survivor: HarnessVersion = await _version(
            store,
            tmp_path,
            "probed",
            parent.id,
            json.dumps({"composed_of": [part_one.id, part_two.id], "tainted": False}),
        )
        composed: HarnessVersion = await _version(
            store, tmp_path, "candidate", None, json.dumps({"composed_of": [part_three.id]})
        )
        gone: HarnessPruned = await store.prune_harness(CUTOFF)
        assert gone.versions == 1
        left: set[str] = {version.id for version in await store.list_harness_versions()}
        assert left == {parent.id, part_one.id, part_two.id, part_three.id, survivor.id, composed.id}
        assert loser.id not in left
        for waiting in (survivor, composed):
            assert await taint_of(store, await store.get_harness_version(waiting.id)) == ()
        # Set aside, the survivor is old like the rest, and goes with what only it kept.
        await store.retire_harness(survivor.id, OLD)
        gone = await store.prune_harness(CUTOFF)
        assert gone.versions == 4
        assert {version.id for version in await store.list_harness_versions()} == {part_three.id, composed.id}
    finally:
        await store.close()


async def test_a_version_set_aside_lately_keeps_its_parts_while_its_age_keeps_it(tmp_path: Path) -> None:
    """The pre-push review of 589: a step composes a survivor and sets its candidates aside; weeks later the owner sets
    the composed version aside too. While its age keeps it, so are its parts, or ``taint_of`` would find one gone and
    the page would take it for one written from outside content; once it is old, it goes with what only it kept."""
    store: Store = await _store(tmp_path)
    try:
        part_one: HarnessVersion = await _version(store, tmp_path, "retired")
        part_two: HarnessVersion = await _version(store, tmp_path, "retired")
        composed: HarnessVersion = await _version(
            store,
            tmp_path,
            "retired",
            None,
            json.dumps({"composed_of": [part_one.id, part_two.id], "tainted": False}),
            RECENT,
        )
        gone: HarnessPruned = await store.prune_harness(CUTOFF)
        assert gone.versions == 0
        assert {version.id for version in await store.list_harness_versions()} == {
            part_one.id,
            part_two.id,
            composed.id,
        }
        assert await taint_of(store, await store.get_harness_version(composed.id)) == ()
        # Old in its turn, it goes with its parts.
        _set(tmp_path, "UPDATE harness_versions SET retired_at = ? WHERE id = ?", OLD, composed.id)
        gone = await store.prune_harness(CUTOFF)
        assert gone.versions == 3
        assert await store.list_harness_versions() == []
    finally:
        await store.close()


async def test_with_no_version_in_use_the_old_retired_ones_go_and_the_ones_that_wait_stay(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        retired: HarnessVersion = await _version(store, tmp_path, "retired")
        candidate: HarnessVersion = await _version(store, tmp_path, "candidate")
        probed: HarnessVersion = await _version(store, tmp_path, "probed")
        await store.add_harness_experience("hst_gone", None, "a cue", "a lesson", "edit 1", "low", False)
        _set(tmp_path, "UPDATE harness_experiences SET created_at = ?", OLD)
        gone: HarnessPruned = await store.prune_harness(CUTOFF)
        assert (gone.versions, gone.experiences) == (1, 1)
        assert {version.id for version in await store.list_harness_versions()} == {candidate.id, probed.id}
        assert retired.id not in {version.id for version in await store.list_harness_versions()}
    finally:
        await store.close()


async def test_notes_and_outcomes_that_are_not_json_do_not_stop_a_prune(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        in_use: HarnessVersion = await _version(store, tmp_path, "adopted", notes="not json")
        broken = await store.create_harness_step(OLD, None, None)
        _set(tmp_path, "UPDATE harness_steps SET started_at = ?, outcome = 'not json' WHERE id = ?", OLD, broken.id)
        gone: HarnessPruned = await store.prune_harness(CUTOFF)
        assert (gone.versions, gone.steps) == (0, 1)
        assert (await store.get_harness_version(in_use.id)).state == "adopted"
    finally:
        await store.close()


async def test_the_versions_come_a_page_at_a_time_and_are_counted_by_state(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        made: list[HarnessVersion] = [await _version(store, tmp_path, "candidate", when=OLD + n) for n in range(5)]
        await store.retire_harness(made[0].id, 1.0)
        await store.set_harness_state(made[1].id, "probed")
        newest_first: list[str] = [version.id for version in reversed(made)]
        assert [version.id for version in await store.list_harness_versions()] == newest_first
        assert [version.id for version in await store.list_harness_versions(2)] == newest_first[:2]
        assert [version.id for version in await store.list_harness_versions(2, 2)] == newest_first[2:4]
        assert [version.id for version in await store.list_harness_versions(2, 4)] == newest_first[4:]
        assert await store.list_harness_versions(2, 9) == []
        assert await store.count_harness_versions() == {"candidate": 3, "probed": 1, "retired": 1}
    finally:
        await store.close()


async def test_an_attempt_keeps_the_approvals_its_run_asked_and_the_checks_that_held(tmp_path: Path) -> None:
    """Item 589 (L9): the scorecard is what docs/harness.md says: the ending, the steps, the tokens, the seconds, the
    tool errors, the approvals asked and the checks passed."""
    store: Store = await _store(tmp_path)
    try:
        step = await store.create_harness_step(1.0, None, None)
        await store.add_harness_probes(step.id, None, "base", [CARD], 2.0)
        (probe,) = await store.harness_probes_of(step.id)
        assert (probe.approvals_asked, probe.checks_passed, probe.checks_total) == (0, 3, 3)
        assert (probe.input_tokens, probe.output_tokens, probe.tool_errors, probe.steps) == (100, 10, 0, 2)
    finally:
        await store.close()


async def test_a_store_from_before_the_scorecard_grew_opens_and_its_attempts_hold_none_of_the_three(
    tmp_path: Path,
) -> None:
    database: Path = tmp_path / "dot.sqlite3"
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE harness_probes (id INTEGER PRIMARY KEY AUTOINCREMENT, step_id TEXT NOT NULL, version_id TEXT,"
            " label TEXT NOT NULL, task_id TEXT NOT NULL, attempt INTEGER NOT NULL, passed INTEGER NOT NULL,"
            " status TEXT NOT NULL, cut TEXT NOT NULL, steps INTEGER NOT NULL, input_tokens INTEGER NOT NULL,"
            " output_tokens INTEGER NOT NULL, tool_errors INTEGER NOT NULL, seconds REAL NOT NULL,"
            " created_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO harness_probes(step_id, version_id, label, task_id, attempt, passed, status, cut, steps,"
            " input_tokens, output_tokens, tool_errors, seconds, created_at)"
            " VALUES('hst_old', NULL, 'base', 'write-todo-note', 1, 1, 'done', 'none', 2, 100, 10, 0, 1.0, 1.0)"
        )
        connection.commit()
    finally:
        connection.close()
    store: Store = await _store(tmp_path)
    try:
        (old,) = await store.harness_probes_of("hst_old")
        assert (old.passed, old.approvals_asked, old.checks_passed, old.checks_total) == (True, 0, 0, 0)
        await store.add_harness_probes("hst_old", None, "base", [CARD], 2.0)
        assert [probe.checks_total for probe in await store.harness_probes_of("hst_old")] == [0, 3]
    finally:
        await store.close()


@pytest.mark.parametrize(
    ("held", "asked", "expected"),
    [((True, True, False), 2, (2, 3, 2)), ((), None, (0, 0, 0)), ((True,), 0, (1, 1, 0))],
)
def test_a_scorecard_counts_the_checks_that_held_and_the_approvals_the_run_asked(
    held: tuple[bool, ...], asked: int | None, expected: tuple[int, int, int]
) -> None:
    outcome: TaskOutcome = TaskOutcome(
        task_id="write-todo-note",
        category="files",
        attempt=1,
        passed=all(held),
        status="done",
        cut="none",
        answer="Done.",
        checks=tuple(
            CheckOutcome(check=f"check {number}", passed=passed, detail="") for number, passed in enumerate(held)
        ),
        metrics=RunMetrics(
            steps=2,
            input_tokens=100,
            output_tokens=10,
            tool_calls=1,
            tool_errors=0,
            invalid_arguments=0,
            stalls=0,
            compactions=0,
            unanswered_questions=0,
            approvals_asked=asked,
        ),
        seconds=1.0,
    )
    card = card_of(outcome)
    assert (card.checks_passed, card.checks_total, card.approvals_asked) == expected

"""The night's digest of real runs (item 987): the ways a tool failed, counted by run, chat, day, source and site from
Dot's own record, the evaluation's runs left out; how the runs handed a lesson ended beside the others; and the night
kept as a step of the harness that changes no text."""

import json
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotagent.config import load_default_config
from dotagent.harness.digest import Digest, digest_of, digest_words, night_digest
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, MemoryRecord, RunRecord, RunTally

ZONE: ZoneInfo = ZoneInfo("Europe/Helsinki")
SNAPSHOT: str = "browser_open: taking the page snapshot failed"


async def _run(store: Store, source: str, results: list[tuple[str, bool, str, str, bool]], status: str) -> RunRecord:
    """A run of ``source`` in a chat of its own whose tools gave ``results`` (tool, ok, text, site, tainted)."""
    thread = await store.create_thread("A chat", "ui")
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, source, None, "standard", None, "ask", False, None, None, None
    )
    for tool, ok, text, site, tainted in results:
        await store.add_event(
            run.id, "tool_result", {"tool": tool, "ok": ok, "text": text, "site": site, "tainted": tainted}
        )
    await store.finish_run(run.id, "done" if status == "done" else "failed", "ok")
    return run


async def test_a_night_counts_how_the_real_runs_tools_failed_and_how_lessons_fared(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        # The same failure in two chats, one of a schedule that had read a page, with different ids in its words.
        await _run(
            store,
            "ui",
            [
                ("browser_open", True, "Opened.", "shop.example", False),
                ("browser_open", False, "taking the page snapshot failed: target 41 closed", "shop.example", False),
            ],
            "failed",
        )
        await _run(
            store,
            "schedule",
            [("browser_open", False, "taking the page snapshot failed: target 97 closed", "news.example", True)],
            "done",
        )
        # A tool that failed once, in a run that used it.
        await _run(store, "ui", [("read_file", False, "no such file: /tmp/x.txt", "", False)], "done")
        # The evaluation's runs play tasks: they are not the owner's work.
        await _run(store, "eval", [("browser_open", False, "taking the page snapshot failed", "", False)], "failed")
        lesson: MemoryRecord = await store.add_memory("Wait for the page.", ("lesson",), None, None, None, False)
        taught: RunRecord = await _run(store, "ui", [("browser_open", True, "Opened.", "shop.example", False)], "x")
        await store.mark_memories_used(taught.id, [lesson.id], time.time(), "start")

        step: HarnessStep = await night_digest(store, load_default_config().harness, time.time() + 1, ZONE)
        assert (step.state, step.from_run, step.base_version, step.error) == ("keep", None, None, None)
        record = json.loads(step.outcome)
        assert record["in_use"] is None
        digest: Digest = Digest.model_validate(record["digest"])
        assert digest.tally == RunTally(ended=4, done=2, failed=2, with_lesson=1, with_lesson_done=0)
        first, second = digest.rows
        assert (first.signature, first.failures, first.runs_failing, first.runs_using, first.chats, first.days) == (
            SNAPSHOT,
            2,
            2,
            3,
            2,
            1,
        )
        assert (first.sources, first.sites, first.tainted_runs) == (
            ("schedule", "ui"),
            ("shop.example", "news.example"),
            1,
        )
        assert (second.signature, second.runs_failing, second.runs_using) == ("read_file: no such file", 1, 1)
        assert record["decision"] == (
            "4 runs ended, 2 done and 2 failed; 0 of 1 run handed a lesson ended done, 2 of 3 without one; failed most:"
            f" {SNAPSHOT} (2 runs of 3, 2 chats); read_file: no such file (1 run of 1, 1 chat)."
        )
    finally:
        await store.close()


def test_a_quiet_day_says_so() -> None:
    nothing: RunTally = RunTally(ended=0, done=0, failed=0, with_lesson=0, with_lesson_done=0)
    assert digest_words(digest_of((), nothing, 0.0, 1.0, ZONE)) == "0 runs ended, 0 done and 0 failed."

"""Pages a run shows (item 193): the document the daemon serves for one, what its headers let it do and who may frame
it, and how long the store keeps it: as long as the run's events."""

import time
from pathlib import Path

import pytest

from dotagent.agent.pages import THEME_VARIABLES
from dotagent.core.errors import NotFoundError
from dotagent.server.guard import response_headers
from dotagent.server.routes_pages import framed_page
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ShownPage, ThreadRecord

PAGE: str = '<!doctype html><html lang="tr"><head><title>Sales</title></head><body><p>Sales by month</p></body></html>'


def test_a_page_is_served_after_the_bootstrap_as_the_run_wrote_it() -> None:
    served: str = framed_page("Sales by month", PAGE)
    assert served.startswith('<!doctype html><meta charset="utf-8">')
    assert served.endswith(PAGE)
    assert all(f"{name}:{value};" in served for name, value in THEME_VARIABLES)
    assert "<title>Sales by month</title>" not in served
    assert framed_page("Sales <by> month", "<p>Sales</p>").endswith("<title>Sales &lt;by&gt; month</title><p>Sales</p>")


def test_a_page_is_sandboxed_loads_nothing_of_the_daemon_and_only_the_ui_frames_it() -> None:
    page: dict[str, str] = dict(response_headers("/pages/page_x"))
    policy: str = page["Content-Security-Policy"]
    assert policy.startswith("sandbox allow-scripts; default-src 'none';")
    assert policy.endswith("frame-ancestors 'self'")
    assert "'self'" not in policy.removesuffix("frame-ancestors 'self'")
    assert "X-Frame-Options" not in page
    assert page["Cache-Control"] == "no-store"
    ui: dict[str, str] = dict(response_headers("/"))
    assert "frame-src 'self'" in ui["Content-Security-Policy"]
    assert (ui["X-Frame-Options"], ui["Cache-Control"]) == ("DENY", "no-cache")
    assert dict(response_headers("/api/status"))["Cache-Control"] == "no-store"


async def _ended_run(store: Store, thread: ThreadRecord) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread.id, "chart", "task", "ollama:x", None, "test", None, "ask", False, None, None, None
    )
    await store.finish_run(run.id, "done", "done")
    return run


async def test_a_page_is_kept_under_an_id_nobody_can_guess_as_long_as_its_runs_events(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        old: RunRecord = await _ended_run(store, await store.create_thread("old", "test"))
        deleted: RunRecord = await _ended_run(store, await store.create_thread("deleted", "test"))
        kept: RunRecord = await _ended_run(store, await store.create_thread("kept", "test"))
        pages: list[ShownPage] = [await store.add_shown_page(run.id, "Sales", PAGE) for run in (old, deleted, kept)]
        assert all(page.id.startswith("page_") and len(page.id) == len("page_") + 24 for page in pages)
        assert len({page.id for page in pages}) == len(pages)
        assert await store.get_shown_page(pages[2].id) == pages[2]

        await store._db.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (time.time() - 3600.0, old.id))
        await store._db.commit()
        await store.delete_events_of_runs_ended_before(time.time() - 60.0)
        await store.delete_threads([deleted.thread_id])
        for gone in pages[:2]:
            with pytest.raises(NotFoundError):
                await store.get_shown_page(gone.id)
        assert await store.get_shown_page(pages[2].id) == pages[2]
    finally:
        await store.close()

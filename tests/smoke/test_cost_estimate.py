"""The estimate in dollars end to end (item 1856), against a real store and a price list served on this Mac: a run's
tokens are read by the model of each call, at its tier, with what its helpers used and what a run counts beyond its
calls; the list is fetched with its ETag and kept, a 304 keeps it with a new date, and a list that fails is tried three
times and then raised, the kept one staying."""

import http.server
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from dotagent.core.errors import PricingError
from dotagent.llm import price_list
from dotagent.llm.price_list import PriceList, read_book
from dotagent.llm.pricing import CostEstimate, PriceBook, TokenUse, estimate, read_catalogue, tier_sizes
from dotagent.store.db import Store
from dotagent.store.models import RunRecord

_CATALOGUE: Path = Path(__file__).parent.parent / "fixtures" / "prices" / "catalogue.json"
_ETAG: str = '"v1"'
_TEXT: str = _CATALOGUE.read_text(encoding="utf-8")


class _Served:
    """What the local price list answered and how often."""

    def __init__(self) -> None:
        self.status: int = 200
        self.asked: list[str | None] = []


def _handler(served: _Served) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            etag: str | None = self.headers.get("If-None-Match")
            served.asked.append(etag)
            if served.status != 200:
                self.send_response(served.status)
                self.end_headers()
                self.wfile.write(b"down for maintenance")
                return
            if etag == _ETAG:
                self.send_response(304)
                self.end_headers()
                return
            body: bytes = _TEXT.encode("utf-8")
            self.send_response(200)
            self.send_header("ETag", _ETAG)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return None

    return Handler


@pytest.fixture
def served() -> Iterator[tuple[_Served, str]]:
    state: _Served = _Served()
    server: http.server.ThreadingHTTPServer = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/api.json"
    finally:
        server.shutdown()
        server.server_close()


async def test_the_list_is_fetched_with_its_etag_kept_and_a_failure_tried_three_times(
    tmp_path: Path, served: tuple[_Served, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    state, url = served
    monkeypatch.setattr(price_list, "FIRST_WAIT_S", 0.01)
    path: Path = tmp_path / "prices.json"
    prices: PriceList = PriceList(path, url)
    await prices.load()
    assert prices.book is None and prices.due(100.0)
    first: PriceBook = await prices.refresh(100.0)
    assert (first.etag, set(first.prices)) == (_ETAG, {"anthropic", "openai", "openrouter", "opencode"})
    assert read_book(path) == first
    assert not prices.due(100.0 + 3600)
    # Unchanged: the same prices with a new date.
    again: PriceBook = await prices.refresh(200.0)
    assert (again.fetched_at, again.prices) == (200.0, first.prices)
    assert state.asked == [None, _ETAG]
    # Down: three tries, then the error with the status and the answer; the kept book stays.
    state.status = 503
    with pytest.raises(PricingError) as failed:
        await prices.refresh(300.0)
    assert failed.value.context["status"] == 503 and "maintenance" in str(failed.value.context["body"])
    assert len(state.asked) == 5
    assert prices.book == again and read_book(path) == again
    # A list that is switched off is never due.
    assert not PriceList(path, "").due(10**10)


async def _run(store: Store, model: str, parent: str | None) -> RunRecord:
    thread = await store.create_thread("Cost", "ui")
    return await store.create_run(
        thread.id, "Cost", "task", model, parent, "ui", None, "standard", None, "ask", False, None, None, None
    )


async def _call(store: Store, run: RunRecord, model: str, used_in: int, used_out: int, cached: int) -> None:
    await store.add_run_tokens(run.id, used_in, used_out)
    await store.add_event(
        run.id,
        "model_call",
        {
            "purpose": "step",
            "model": model,
            "input_tokens": used_in,
            "output_tokens": used_out,
            "cached_tokens": cached,
            "cache_write_tokens": 0,
        },
    )


async def test_a_runs_tokens_are_read_by_the_model_of_each_call_with_its_helpers(tmp_path: Path) -> None:
    book: PriceBook = PriceBook(fetched_at=1.0, source="local", etag=None, prices=read_catalogue(_TEXT))
    sizes: tuple[int, ...] = tier_sizes(book)
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        lead: RunRecord = await _run(store, "anthropic:claude-sonnet-5-5", None)
        # A call below the tier and one above it, and a standby model's call.
        await _call(store, lead, "anthropic:claude-sonnet-5-5", 100_000, 1_000, 50_000)
        await _call(store, lead, "anthropic:claude-sonnet-5-5", 300_000, 1_000, 0)
        await _call(store, lead, "openai:gpt-6", 10_000, 500, 0)
        # A helper on OpenCode's own agent, which records no calls: its tokens are its own model's.
        helper: RunRecord = await _run(store, "opencode:qwen4-max-free", lead.id)
        await store.add_run_tokens(helper.id, 40_000, 2_000)
        family: dict[str, list[TokenUse]] = await store.token_uses_of_family(lead.id, sizes)
        own: CostEstimate = estimate(book, family["own"], sizes)
        # 50k x 3 + 50k x 0.3 + 1k x 15, then 300k x 6 + 1k x 22.5 (the tier), then 10k x 1.25 + 500 x 10, per million.
        expected: float = (150_000 + 15_000 + 15_000 + 1_800_000 + 22_500 + 12_500 + 5_000) / 1_000_000
        assert own.usd == pytest.approx(expected)
        assert {use.model for use in family["helpers"] if use.input} == {"opencode:qwen4-max-free"}
        assert estimate(book, [*family["own"], *family["helpers"]], sizes).usd == pytest.approx(expected)
        by_run: dict[str, list[TokenUse]] = await store.token_uses_of_runs([lead.id, helper.id], sizes)
        assert set(by_run) == {lead.id, helper.id}
        by_day: dict[str, list[TokenUse]] = await store.token_uses_by_day(time.time() - 3600, sizes)
        assert sum(use.input for uses in by_day.values() for use in uses) == 450_000
        # A run on a model the list has no price for is unknown, never short.
        stray: RunRecord = await _run(store, "anthropic:claude-unlisted", None)
        await _call(store, stray, "anthropic:claude-unlisted", 1_000, 10, 0)
        assert estimate(book, (await store.token_uses_of_runs([stray.id], sizes))[stray.id], sizes).usd is None
    finally:
        await store.close()

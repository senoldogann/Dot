"""The keys of the messages the pages send (item 189): a key is taken once; taken again it gives what its first sending
came to, or that the sending is under way; a sending under way that has gone stale is taken anew, a failed one lets
its key go, and old keys are forgotten."""

from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import SentMessage

KEY: str = "run:0b4cbb1e-7b1a-4c0e-9d55-1f8b1c2d3e4f"


async def test_a_key_is_taken_once_and_then_gives_its_sending(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        assert await store.claim_sent(KEY, 1000.0, 880.0, 0.0) is None
        assert await store.claim_sent(KEY, 1001.0, 881.0, 0.0) == SentMessage(key=KEY, sent_at=1000.0, target=None)
        await store.settle_sent(KEY, "run_1")
        # A settled key is kept: neither its release nor its age takes it from the run it started.
        await store.release_sent(KEY)
        settled: SentMessage = SentMessage(key=KEY, sent_at=1000.0, target="run_1")
        assert await store.claim_sent(KEY, 5000.0, 4880.0, 0.0) == settled
    finally:
        await store.close()


async def test_a_stale_failed_or_old_key_is_taken_anew(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        assert await store.claim_sent(KEY, 1000.0, 880.0, 0.0) is None
        # Its sending began before the stale time and came to nothing: the key is taken again.
        assert await store.claim_sent(KEY, 1200.0, 1080.0, 0.0) is None
        await store.release_sent(KEY)
        assert await store.claim_sent(KEY, 1201.0, 1081.0, 0.0) is None
        await store.settle_sent(KEY, "run_1")
        # Sent before the time keys are forgotten: the key is a new one.
        assert await store.claim_sent(KEY, 90_000.0, 89_880.0, 3_000.0) is None
    finally:
        await store.close()

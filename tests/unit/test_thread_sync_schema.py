"""The phone's sync contract (item 1686) is frozen in `docs/schemas/thread-sync.schema.json`, which the iPhone app's
tests read too: the models of `dotagent.server.thread_sync` must make the file as it is committed."""

from pathlib import Path

from dotagent.server.thread_sync import contract_schema

SCHEMA: Path = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "thread-sync.schema.json"


def test_the_committed_schema_is_what_the_models_make() -> None:
    assert SCHEMA.read_text(encoding="utf-8") == contract_schema(), (
        "the contract changed: run `uv run python scripts/thread_sync_schema.py`, and tell the iPhone app (a field "
        "removed or renamed breaks it)"
    )

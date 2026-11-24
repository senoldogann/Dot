"""The schema of the phone's sync contract (item 1686): `docs/schemas/thread-sync.schema.json`, made from the
models of `dotagent.server.thread_sync`. The iPhone app's tests read the same file;
`tests/unit/test_thread_sync_schema.py` fails when the models and the file differ.

Usage:
    uv run python scripts/thread_sync_schema.py   write the file
"""

from pathlib import Path

from dotagent.server.thread_sync import contract_schema

SCHEMA: Path = Path(__file__).resolve().parent.parent / "docs" / "schemas" / "thread-sync.schema.json"


def main() -> None:
    SCHEMA.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA.write_text(contract_schema(), encoding="utf-8")


if __name__ == "__main__":
    main()

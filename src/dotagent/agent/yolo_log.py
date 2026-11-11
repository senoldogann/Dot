"""What a yolo run did, written down without ever asking anyone or stopping the run.

Each tool call appends one line under the run's undo folder. The line is a record, not a gate: a disk that refuses
the line is logged and the call's result is what the run reads. A call that failed is told to check what changed and
go on by another way, still without asking.
"""

import json
import os
from pathlib import Path

from dotagent.core.logs import get_logger

log = get_logger(__name__)

YOLO_LOG: str = "yolo.jsonl"
_PRIVATE_DIR: int = 0o700
_PRIVATE_FILE: int = 0o600


def note_checkpoint(root: Path, run_id: str, tool: str, ok: bool, summary: str) -> None:
    """Append one line for a yolo call. Never raises: a run is not held up by its own record."""
    try:
        folder: Path = root / run_id
        folder.mkdir(parents=True, mode=_PRIVATE_DIR, exist_ok=True)
        path: Path = folder / YOLO_LOG
        line: bytes = (json.dumps({"tool": tool, "ok": ok, "summary": summary}) + "\n").encode()
        descriptor: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _PRIVATE_FILE)
        try:
            os.write(descriptor, line)
        finally:
            os.close(descriptor)
    except OSError as exc:
        log.warning("yolo_checkpoint_not_written", run_id=run_id, tool=tool, error=str(exc))

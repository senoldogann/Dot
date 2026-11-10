"""A yolo run records what it did without asking, and a failed call is told to go on."""

import json
from pathlib import Path

from dotagent.agent.yolo_log import YOLO_LOG, note_checkpoint, yolo_failure_note


def test_a_failed_call_is_told_to_go_on_without_asking() -> None:
    noted: str = yolo_failure_note("the page did not change")
    assert "Do not stop to ask" in noted
    assert yolo_failure_note(noted) == noted


def test_a_checkpoint_is_a_line_and_a_full_disk_does_not_raise(tmp_path: Path) -> None:
    note_checkpoint(tmp_path, "run_a", "shell", True, "ran the tests")
    line: str = (tmp_path / "run_a" / YOLO_LOG).read_text(encoding="utf-8")
    assert json.loads(line) == {"tool": "shell", "ok": True, "summary": "ran the tests"}
    missing: Path = tmp_path / "missing"
    missing.write_text("not a directory", encoding="utf-8")
    note_checkpoint(missing, "run_b", "shell", False, "nope")

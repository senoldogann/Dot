"""What dotx.log says of a daemon that ended with an error (``dotx service status`` and ``dotx service install`` read it
back): the latest failure of ``dotx daemon``, unless a start that went well came after it."""

import json
from pathlib import Path

from dotagent.daemon_failure import DaemonFailure, daemon_failure_in, failure_text, last_daemon_failure


def _line(event: str, timestamp: str, **fields: str) -> str:
    return json.dumps({**fields, "event": event, "level": "info", "timestamp": timestamp})


_REFUSED: str = _line(
    "command_failed",
    "2026-10-18T04:12:30.100000",
    command="daemon",
    error="configuration failed validation: web.searxng_url is not a setting this version of Dot knows",
)
_STARTED: str = _line("daemon_started", "2026-10-18T09:00:00.000000", url="http://127.0.0.1:8765")


def test_the_latest_failure_of_the_daemon_is_found_unless_a_start_came_after_it() -> None:
    other: str = _line("command_failed", "2026-10-18T04:13:00.000000", command="run", error="no model")
    assert last_daemon_failure([_STARTED, _REFUSED, other]) == DaemonFailure(
        at="2026-10-18T04:12:30.100000",
        error="configuration failed validation: web.searxng_url is not a setting this version of Dot knows",
    )
    assert last_daemon_failure([_REFUSED, _STARTED, other]) is None
    assert last_daemon_failure([other]) is None
    assert last_daemon_failure([]) is None


def test_a_line_that_is_not_an_event_is_passed_over_and_the_failure_reads_as_a_line() -> None:
    cut: str = 'ge": "x", "event": "daemon_started"}'
    found: DaemonFailure | None = last_daemon_failure([cut, "", "[]", _REFUSED])
    assert found is not None
    assert failure_text(found).endswith("knows (2026-10-18 04:12)")


def test_the_end_of_the_log_is_read_and_a_missing_log_has_no_failure(tmp_path: Path) -> None:
    log: Path = tmp_path / "dotx.log"
    assert daemon_failure_in(log) is None
    log.write_text("\n".join([_STARTED, _REFUSED]) + "\n", encoding="utf-8")
    found: DaemonFailure | None = daemon_failure_in(log)
    assert found is not None and found.error.startswith("configuration failed validation")

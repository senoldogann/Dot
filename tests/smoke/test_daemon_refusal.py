"""A daemon that refuses its configuration says why in the log the owner reads, not only in the stderr of its launchd
job: ``dotx daemon`` on a file with a setting this version does not know ends with the setting named and one
``command_failed`` event in dotx.log, which ``dotx service status`` and ``dotx service install`` read back. The real
command runs in a child process with a throw-away home and logs; it ends before it binds an address.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from dotagent.daemon_failure import DaemonFailure, daemon_failure_in

_TIME_LIMIT_S: float = 60.0
_NAMED: str = "web.searxng_urls is not a setting this version of Dot knows (a newer Dot may add it)"


def test_a_daemon_that_refuses_its_configuration_names_the_setting_in_the_log(tmp_path: Path) -> None:
    home: Path = tmp_path / "home"
    logs: Path = tmp_path / "logs"
    home.mkdir()
    (home / "config.toml").write_text('[web]\nsearxng_urls = "http://127.0.0.1:8888"\n', encoding="utf-8")
    env: dict[str, str] = {
        **os.environ,
        "DOT_HOME": str(home),
        "DOT_LOG_DIR": str(logs),
        "DOT_CHROME_DIR": str(tmp_path / "chrome"),
    }
    started: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "dotagent", "daemon"],
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIME_LIMIT_S,
        check=False,
    )
    assert started.returncode == 2, started.stderr
    assert _NAMED in started.stderr
    events: list[dict[str, object]] = [
        json.loads(line) for line in (logs / "dotx.log").read_text(encoding="utf-8").splitlines()
    ]
    failed: list[dict[str, object]] = [event for event in events if event["event"] == "command_failed"]
    assert [(event["command"], event["level"]) for event in failed] == [("daemon", "error")]
    failure: DaemonFailure | None = daemon_failure_in(logs / "dotx.log")
    assert failure is not None and _NAMED in failure.error

"""``dotx auth set`` stores every key that Settings › API keys manages, the typed judge's included, in Dot's ``.env``
file with mode 0600, from what the terminal gives it. The real command runs in a child process with a throw-away home;
the key is not a real one and no service is asked.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

from dotagent.secrets import parse_env_file

KEY: str = "TYPESAFE_API_KEY"
VALUE: str = "not-a-real-typesafe-key-0001"
_TIME_LIMIT_S: float = 60.0


def test_auth_set_stores_the_key_of_the_typed_judge(tmp_path: Path) -> None:
    home: Path = tmp_path / "home"
    env: dict[str, str] = {**os.environ, "DOT_HOME": str(home), "DOT_LOG_DIR": str(tmp_path / "logs")}
    stored: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "dotagent", "auth", "set", KEY],
        input=f"{VALUE}\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIME_LIMIT_S,
        check=False,
    )
    env_file: Path = home / ".env"
    assert stored.returncode == 0, stored.stderr
    assert parse_env_file(env_file.read_text(encoding="utf-8")) == {KEY: VALUE}
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

"""The key file (item 350): a value that would not stay on its line is refused, and the file is left as it was (a line
break in a value wrote an entry of its own); a change waits for the lock another process holds, so two changes at
once both land."""

import fcntl
import os
import threading
from pathlib import Path

import pytest

from dotagent.core.errors import ConfigError
from dotagent.secrets import env_lock_path, parse_env_file, write_env_value

_WAIT_S: float = 0.3


def test_a_value_with_a_line_break_is_refused_and_the_file_is_left_as_it_was(tmp_path: Path) -> None:
    path: Path = tmp_path / ".env"
    write_env_value(path, "OPENAI_API_KEY", "sk-first")
    for value in ("sk-abc\nTYPESAFE_API_KEY=other", "sk-abc\rTYPESAFE_API_KEY=other", "sk-abc x"):
        with pytest.raises(ConfigError) as refused:
            write_env_value(path, "OPENAI_API_KEY", value)
        assert "sk-abc" not in str(refused.value.context)
    with pytest.raises(ConfigError):
        write_env_value(path, "OPENAI_API_KEY=X", "sk-second")
    assert parse_env_file(path.read_text(encoding="utf-8")) == {"OPENAI_API_KEY": "sk-first"}


def test_a_change_waits_for_the_lock_another_process_holds(tmp_path: Path) -> None:
    path: Path = tmp_path / ".env"
    write_env_value(path, "OPENAI_API_KEY", "sk-first")
    # Another open of the lock file stands for another process: flock keeps the two apart.
    held: int = os.open(env_lock_path(path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    writer: threading.Thread = threading.Thread(target=write_env_value, args=(path, "ANTHROPIC_API_KEY", "sk-second"))
    try:
        writer.start()
        writer.join(_WAIT_S)
        assert writer.is_alive(), "the change did not wait for the lock"
        assert parse_env_file(path.read_text(encoding="utf-8")) == {"OPENAI_API_KEY": "sk-first"}
    finally:
        os.close(held)
    writer.join(5.0)
    assert parse_env_file(path.read_text(encoding="utf-8")) == {
        "ANTHROPIC_API_KEY": "sk-second",
        "OPENAI_API_KEY": "sk-first",
    }

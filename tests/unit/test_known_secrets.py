"""The secrets Dot holds are known by their values (review Y3): those it looked up in this process and those of its env
file, each once, the longest first, none too short to mask. Every value is made up and starts with MARKER-."""

from itertools import pairwise
from pathlib import Path

import pytest

from dotagent import secrets
from dotagent.secrets import known_secrets, resolve_secret


def test_the_values_dot_looked_up_and_those_of_its_env_file_are_known_the_longest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    # What a process has looked up is kept for as long as it runs: this test looks one up in a set of its own, which is
    # put back when it ends, so that no other test of the process finds its value among the secrets Dot holds.
    monkeypatch.setattr(secrets, "_resolved", set())
    looked_up: str = "MARKER-" + "e" * 12
    saved: str = "MARKER-" + "f" * 20
    monkeypatch.setenv("DOT_TEST_LOOKED_UP_KEY", looked_up)
    (tmp_path / ".env").write_text(f'NOTES_TOKEN="{saved}"\nDEBUG=1\nSHORT=MARKER-\n', encoding="utf-8")
    assert resolve_secret("DOT_TEST_LOOKED_UP_KEY") == looked_up
    known: tuple[str, ...] = known_secrets(filled=True)
    assert saved in known and looked_up in known
    assert known.index(saved) < known.index(looked_up)
    assert all(len(longer) >= len(shorter) for longer, shorter in pairwise(known))
    assert "1" not in known and "MARKER-" not in known
    # A value the env file no longer holds, and that nobody looked up, is no longer known.
    (tmp_path / ".env").write_text("DEBUG=1\n", encoding="utf-8")
    assert saved not in known_secrets(filled=True) and looked_up in known_secrets(filled=True)

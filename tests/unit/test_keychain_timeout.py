"""A Keychain that does not answer (locked, or waiting for a prompt nobody answers) is a Dot error that says so, not
the timeout of a child process (item 177)."""

import subprocess

import pytest

from dotagent.core.errors import ConfigError
from dotagent.secrets import _from_keychain


def test_a_keychain_that_does_not_answer_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def hanging(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=command, timeout=15.0)

    monkeypatch.setattr(subprocess, "run", hanging)
    with pytest.raises(ConfigError, match="the macOS Keychain did not answer") as refused:
        _from_keychain("DOT_TEST_177_KEY")
    assert refused.value.context["name"] == "DOT_TEST_177_KEY"

"""An offline test finds none of the owner's keys (item 558, ``tests/conftest.py``): the variables the providers' keys
are looked for in are out of its environment, and the login Keychain, where the owner may keep them, is never asked, so
a test that lists the models of every provider cannot send a real key to one."""

import os
from pathlib import Path

import pytest

from dotagent import secrets
from dotagent.config import load_default_config, providers_by_key
from dotagent.secrets import resolve_secret


def test_an_offline_test_finds_no_key_of_the_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_default_config()
    names: tuple[str, ...] = (*providers_by_key(config), config.providers.openai.organization_env)
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    assert not [name for name in names if name in os.environ]
    assert secrets._from_keychain(config.providers.openai.api_key_env) is None
    assert [name for name in names if resolve_secret(name) is not None] == []


def test_a_key_a_test_sets_for_itself_is_the_one_it_finds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the owner's values stay out: a stand-in of a provider is given a key under the provider's own name."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-of-this-test-alone")
    assert resolve_secret("OPENROUTER_API_KEY") == "sk-or-of-this-test-alone"

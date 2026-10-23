"""A Keychain that refuses the lookup (locked with nobody to ask, a prompt the owner denied) is a Dot error that says
so, not a key that is missing, which sends the owner to enter a key that is there (item 561); one that holds none is
no error, and the view of where keys are kept goes on showing the key unset, with the log saying why. A key a service
can be opened without (:func:`dotagent.secrets.optional_secret`) is no key then, so the daemon starts; a key the
service cannot be built without keeps the error."""

import subprocess
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from dotagent import secrets
from dotagent.config import AppConfig, load_config, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.memory.service import MemoryIndex, close_memory_index, open_memory_index
from dotagent.secrets import SecretState, _from_keychain, optional_secret, require_secret, resolve_secret, secret_state
from dotagent.store.db import Store

NAME: str = "DOT_TEST_561_KEY"
NOT_FOUND: str = "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain."
DENIED: str = "security: SecKeychainSearchCopyNext: User interaction is not allowed.\n"


def _security(code: int, stdout: str = "", stderr: str = "") -> object:
    """A ``security`` that exits with ``code``."""

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)

    return run


@pytest.fixture(autouse=True)
def nowhere_else(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The key is in neither the environment nor the env file of a home of the test's own, and the Keychain is asked
    through the ``security`` each test stands in for: the root conftest keeps the lookup from an offline test (item
    558), and every test here replaces the program it runs, so no test reaches the owner's Keychain."""
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setattr(secrets, "_from_keychain", _from_keychain)


@pytest.mark.parametrize("code", [44, 37, 50])
def test_a_keychain_that_holds_no_such_key_holds_none(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """The exit of ``security`` is the low byte of the status: item not found (44), no default keychain (37), no such
    keychain (50)."""
    monkeypatch.setattr(subprocess, "run", _security(code, stderr=NOT_FOUND))
    assert _from_keychain(NAME) is None
    assert resolve_secret(NAME) is None


def test_a_key_the_keychain_holds_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _security(0, stdout="held-value-0123456789\n"))
    assert _from_keychain(NAME) == "held-value-0123456789"


@pytest.mark.parametrize(
    ("code", "said"),
    [
        (36, "security: SecKeychainSearchCopyNext: User interaction is not allowed."),
        (128, "security: SecKeychainSearchCopyNext: User canceled the operation."),
        (51, "security: SecKeychainSearchCopyNext: The user name or passphrase you entered is not correct."),
        (1, "usage: security find-generic-password"),
    ],
)
def test_a_keychain_that_refuses_is_a_config_error_that_says_so(
    monkeypatch: pytest.MonkeyPatch, code: int, said: str
) -> None:
    monkeypatch.setattr(subprocess, "run", _security(code, stderr=f"{said}\n"))
    with pytest.raises(ConfigError, match="the macOS Keychain refused the lookup") as refused:
        _from_keychain(NAME)
    assert refused.value.context["name"] == NAME
    assert refused.value.context["returncode"] == code
    assert refused.value.context["stderr"] == said
    assert "unlock the login keychain" in str(refused.value.context["hint"])
    # A run that needs the key is told so, and not "missing API key".
    with pytest.raises(ConfigError, match="refused the lookup"):
        resolve_secret(NAME)


@pytest.mark.parametrize("code", [36, 128])
def test_the_view_of_a_key_shows_it_unset_when_the_keychain_refuses_and_the_log_says_why(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """The page of keys and the roster of models must render with a Keychain that does not answer."""
    monkeypatch.setattr(subprocess, "run", _security(code, stderr="security: User interaction is not allowed.\n"))
    with capture_logs() as logs:
        state: SecretState = secret_state(NAME)
    assert (state.source, state.saved, state.tail) == (None, False, "")
    (said,) = [entry for entry in logs if entry["event"] == "keychain_unreadable"]
    assert (said["name"], said["returncode"], said["log_level"]) == (NAME, code, "warning")
    assert "hint" not in said


@pytest.mark.parametrize("code", [36, 128, 51, 1])
def test_an_optional_key_is_none_when_the_keychain_refuses_and_the_log_says_why(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    monkeypatch.setattr(subprocess, "run", _security(code, stderr=DENIED))
    with capture_logs() as logs:
        assert optional_secret(NAME) is None
    (said,) = [entry for entry in logs if entry["event"] == "keychain_unreadable"]
    assert (said["name"], said["returncode"], said["log_level"]) == (NAME, code, "warning")
    assert "hint" not in said
    # A key the service cannot be built without keeps the error that says so.
    with pytest.raises(ConfigError, match="refused the lookup"):
        require_secret(NAME, "a test")


def test_an_optional_key_is_none_when_the_keychain_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def silent(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1.0)

    monkeypatch.setattr(subprocess, "run", silent)
    with capture_logs() as logs:
        assert optional_secret(NAME) is None
    assert [entry["name"] for entry in logs if entry["event"] == "keychain_unreadable"] == [NAME]


def test_an_optional_key_the_keychain_holds_or_lacks_is_read_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _security(0, stdout="held-value-0123456789\n"))
    assert optional_secret(NAME) == "held-value-0123456789"
    monkeypatch.setattr(subprocess, "run", _security(44, stderr=NOT_FOUND))
    with capture_logs() as logs:
        assert optional_secret(NAME) is None
    assert [entry for entry in logs if entry["event"] == "keychain_unreadable"] == []


@pytest.fixture
def nothing_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """The default config, whose names for the keys are in no source of the test's: not in the environment, not in an
    env file (the home is the test's own), and the Keychain is the one the test stands in for."""
    config: AppConfig = load_default_config()
    providers = config.providers
    for name in (
        providers.ollama.api_key_env,
        providers.openai.api_key_env,
        providers.openai.organization_env,
        providers.openai.project_env,
        providers.anthropic.api_key_env,
    ):
        monkeypatch.delenv(name, raising=False)
    return config


@pytest.mark.parametrize("code", [36, 128, 51])
async def test_the_memory_opens_with_a_keychain_that_refuses_the_ollama_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, nothing_in_the_environment: AppConfig, code: int
) -> None:
    """The default config embeds with Ollama, whose key the daemon starts without: it was read through the lookup that
    raises, so a Keychain that refused it stopped the daemon from starting (the review of item 561)."""
    monkeypatch.setattr(subprocess, "run", _security(code, stderr=DENIED))
    config: AppConfig = load_config(tmp_path / "config.toml")
    assert (config.memory.embed_enabled, config.memory.embed_provider) == (True, "ollama")
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        with capture_logs() as logs:
            index: MemoryIndex = await open_memory_index(store, config)
        try:
            assert index.embedder is not None
        finally:
            await close_memory_index(index)
    finally:
        await store.close()
    assert config.providers.ollama.api_key_env in [e["name"] for e in logs if e["event"] == "keychain_unreadable"]


async def test_the_memory_with_openai_opens_without_its_organization_when_the_keychain_refuses_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, nothing_in_the_environment: AppConfig
) -> None:
    """OpenAI's key stays required (the error says the Keychain refused); its organization and project are optional."""
    config: AppConfig = load_config(tmp_path / "config.toml")
    config = config.model_copy(update={"memory": config.memory.model_copy(update={"embed_provider": "openai"})})
    monkeypatch.setattr(subprocess, "run", _security(36, stderr=DENIED))
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        with pytest.raises(ConfigError, match="refused the lookup"):
            await open_memory_index(store, config)
        monkeypatch.setenv(config.providers.openai.api_key_env, "sk-this-test-alone")
        index: MemoryIndex = await open_memory_index(store, config)
        await close_memory_index(index)
    finally:
        await store.close()


async def _recorded(call: ModelCall) -> None:
    del call


async def test_the_router_makes_ollama_and_openai_connectors_with_a_keychain_that_refuses_the_optional_keys(
    monkeypatch: pytest.MonkeyPatch, nothing_in_the_environment: AppConfig
) -> None:
    config: AppConfig = nothing_in_the_environment
    monkeypatch.setattr(subprocess, "run", _security(36, stderr=DENIED))
    router: ModelRouter = ModelRouter(config, _recorded, {})
    try:
        await router.provider("ollama")
        # The key of a provider that cannot be used without one is still a question of the Keychain.
        with pytest.raises(ConfigError, match="refused the lookup"):
            await router.provider("openai")
        monkeypatch.setenv(config.providers.openai.api_key_env, "sk-this-test-alone")
        await router.provider("openai")
    finally:
        await router.aclose()

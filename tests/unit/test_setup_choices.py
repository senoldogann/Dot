"""What ``dotx setup`` leaves in the config for the parts of Dot that follow its model choice (item 558): one of Zen's
free models is the main model alone (Dot's own calls cannot use one), and the models of another provider on a Mac
whose Ollama does not serve the embedding model switch the notes to be found by their words, unless the owner chose
their embedder already. Ollama's answer is the argument of these functions, so no server is asked."""

import socket
from pathlib import Path

import pytest

from dotagent import cli_setup
from dotagent.config import AppConfig, ModelsConfig, load_config
from dotagent.llm.router import ModelRef
from dotagent.llm.types import ModelInfo


def _info(vision: bool) -> ModelInfo:
    return ModelInfo(
        id="big-pickle",
        vision=vision,
        tools=True,
        thinking=False,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=(),
        free=True,
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    return tmp_path


def _closed_port() -> int:
    """A port of this Mac nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_a_pick_of_zens_free_models_is_the_main_model_alone(home: Path) -> None:
    path: Path = home / "config.toml"
    path.write_text('[models]\nfast = "openai:gpt-5.5-mini"\n', encoding="utf-8")
    before: ModelsConfig = load_config(path).models
    written = cli_setup._write_models(ModelRef(provider="opencode", model="big-pickle"), _info(vision=True))
    assert written == ()
    after: ModelsConfig = load_config(path).models
    assert after.main == "opencode:big-pickle"
    assert (after.fast, after.review, after.vision) == (before.fast, before.review, before.vision)
    assert after.fast == "openai:gpt-5.5-mini"


@pytest.mark.parametrize(("vision", "roles"), [(True, ("fast", "review", "vision")), (False, ("fast", "review"))])
def test_another_provider_is_the_model_of_dots_own_work_too(home: Path, vision: bool, roles: tuple[str, ...]) -> None:
    ref: ModelRef = ModelRef(provider="openai", model="gpt-5.5")
    assert cli_setup._write_models(ref, _info(vision)) == roles
    models: ModelsConfig = load_config(home / "config.toml").models
    assert models.main == ref.label
    assert all(getattr(models, role) == ref.label for role in roles)


def test_setup_says_which_roles_keep_their_models_and_where_one_cannot_do_the_work(home: Path) -> None:
    (home / "config.toml").write_text('[models]\nfast = "opencode:big-pickle"\n', encoding="utf-8")
    config: AppConfig = load_config(home / "config.toml")
    ref: ModelRef = ModelRef(provider="opencode", model="big-pickle")
    said: str = cli_setup._models_lines(config, ref, (), ollama_down=True)
    assert "main      opencode:big-pickle\n" in said
    fast, review, vision = (
        line for line in said.splitlines() if line.lstrip().startswith(("fast", "review", "vision"))
    )
    assert "Zen's free models answer only as OpenCode's own agent" in fast and "choose another" in fast
    # The review and vision models are Ollama's by default: setup says that this Mac has none to run them.
    assert "does not answer on this Mac" in review and "does not answer on this Mac" in vision
    # A model that sees no images leaves the vision model, and the others are written.
    openai: ModelRef = ModelRef(provider="openai", model="gpt-5.5")
    said = cli_setup._models_lines(config, openai, ("fast", "review"), ollama_down=False)
    assert "fast      openai:gpt-5.5\n" in said and "review    openai:gpt-5.5\n" in said
    assert f"vision    {config.models.vision} (kept: openai:gpt-5.5 sees no images)" in said


def _config(home: Path, text: str) -> AppConfig:
    """The config file with ``text`` in it, read as Dot reads it."""
    (home / "config.toml").write_text(text, encoding="utf-8")
    return load_config(home / "config.toml")


def _file(home: Path) -> str:
    return (home / "config.toml").read_text(encoding="utf-8")


async def test_the_notes_are_found_by_their_words_where_this_mac_has_no_embedder(home: Path) -> None:
    config: AppConfig = _config(home, '[models]\nmain = "openai:gpt-5.5"\n')
    assert config.memory.embed_enabled and config.memory.embed_provider == "ollama"
    said: str | None = await cli_setup._keep_notes_working(config, "openai", None)
    assert said is not None and "no Ollama answers on this Mac" in said and "embed_enabled = false" in said
    assert not load_config(home / "config.toml").memory.embed_enabled
    # Ollama answers without the embedding model: its name is in the way back.
    config = _config(home, '[models]\nmain = "openai:gpt-5.5"\n')
    said = await cli_setup._keep_notes_working(config, "anthropic", ("llama3:8b",))
    assert said is not None and "Ollama has no bge-m3" in said and "ollama pull bge-m3" in said
    assert not load_config(home / "config.toml").memory.embed_enabled


async def test_the_notes_stay_with_ollama_where_it_serves_them_or_the_models_are_its_own(home: Path) -> None:
    config: AppConfig = _config(home, '[models]\nmain = "openai:gpt-5.5"\n')
    for provider, installed in (("openai", ("bge-m3:latest",)), ("ollama", None)):
        assert await cli_setup._keep_notes_working(config, provider, installed) is None
    assert load_config(home / "config.toml").memory.embed_enabled


async def test_an_embedder_the_owner_chose_is_left_as_it_is(home: Path) -> None:
    for memory in ('embed_provider = "openai"\nembed_model = "text-embedding-3-small"\n', "embed_enabled = false\n"):
        text: str = f'[models]\nmain = "openai:gpt-5.5"\n\n[memory]\n{memory}'
        config: AppConfig = _config(home, text)
        assert await cli_setup._keep_notes_working(config, "openai", None) is None
        assert _file(home) == text


async def test_an_ollama_that_does_not_answer_is_none(home: Path) -> None:
    # The key is one no source has, so that no key of the owner is looked for or sent.
    ollama: str = f'base_url = "http://127.0.0.1:{_closed_port()}"\napi_key_env = "DOT_TEST_SURELY_UNSET_SECRET"\n'
    config: AppConfig = _config(home, f"[providers.ollama]\n{ollama}")
    assert await cli_setup._ollama_models(config) is None

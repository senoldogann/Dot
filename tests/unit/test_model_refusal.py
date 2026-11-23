"""A message whose model cannot answer at all is refused before anything starts, and the error names the model and
the default model to send it on instead: no run, no chat and no queued message is made, so the message stays where it
was typed. A model whose provider has its key starts as before. No model is called."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, PluginsConfig, SkillsConfig, load_default_config
from dotagent.core.errors import ModelUnavailableError
from dotagent.evals.runner import SCRIPTED_MODEL, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.toolcalls_play import no_keychain
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import NO_PICKS, RunRecord

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_KEYLESS: str = "openai:gpt-5.5"


def _config() -> AppConfig:
    base: AppConfig = load_default_config()
    return base.model_copy(
        update={
            "browser": base.browser.model_copy(update={"headless": True, "mode": "own"}),
            "desktop": base.desktop.model_copy(update={"enabled": False, "keep_awake": False}),
            "memory": base.memory.model_copy(update={"embed_enabled": False}),
            "mcp": [],
            "skills": SkillsConfig(dirs=[], elsewhere=False),
            "plugins": PluginsConfig(dirs=[]),
            "agent": base.agent.model_copy(update={"playbooks": False, "lessons": "off", "busy_parallel": False}),
            "shell": base.shell.model_copy(update={"sandbox": False}),
        }
    )


@contextmanager
def _no_openai_key() -> Iterator[None]:
    """OpenAI's key is in neither the environment nor the Keychain."""
    kept: str | None = os.environ.pop("OPENAI_API_KEY", None)
    try:
        with no_keychain():
            yield
    finally:
        if kept is not None:
            os.environ["OPENAI_API_KEY"] = kept


async def _started(manager: RunManager, model: str, thread_id: str | None = None) -> RunRecord:
    return await manager.start("Hello.", "chat", model, thread_id, "ui", None, None, "standard", None, None, None, ())


@pytest.mark.asyncio
async def test_a_message_for_a_model_with_no_key_is_refused_naming_it_and_the_default_and_starts_nothing() -> None:
    with scratch_home() as home, eval_environment(home, {}), _no_openai_key():
        runtime: Runtime = await open_runtime(_config(), {})
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, ()))
            manager.set_main_model(SCRIPTED_MODEL)
            first: RunRecord = await _started(manager, SCRIPTED_MODEL)
            with pytest.raises(ModelUnavailableError) as refused:
                await _started(manager, _KEYLESS)
            with pytest.raises(ModelUnavailableError) as in_chat:
                await _started(manager, _KEYLESS, first.thread_id)
            with pytest.raises(ModelUnavailableError) as queued:
                await manager.queue(
                    first.thread_id,
                    "Hello again.",
                    (),
                    "chat",
                    _KEYLESS,
                    None,
                    "standard",
                    None,
                    NO_PICKS,
                    None,
                    None,
                    None,
                    "ui",
                )
            runs: list[RunRecord] = await runtime.store.list_runs(10, None)
            waiting: int = len(await runtime.store.list_queued(first.thread_id))
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    for error in (refused.value, in_chat.value, queued.value):
        assert error.context == {"model": _KEYLESS, "problem": "no openai key is set", "fallback": SCRIPTED_MODEL}
        assert _KEYLESS in error.message and SCRIPTED_MODEL in error.message
        assert "no openai key is set" in error.message
    # Only the first message's run was made: no run for the refused ones, no queued message.
    assert [run.id for run in runs] == [first.id]
    assert waiting == 0


@pytest.mark.asyncio
async def test_no_default_is_offered_when_the_default_cannot_answer_either() -> None:
    with scratch_home() as home, eval_environment(home, {}), _no_openai_key():
        runtime: Runtime = await open_runtime(_config(), {})
        manager: RunManager = RunManager(runtime)
        try:
            manager.set_main_model(_KEYLESS)
            with pytest.raises(ModelUnavailableError) as refused:
                await _started(manager, "anthropic:claude-opus-4-5")
            with pytest.raises(ModelUnavailableError) as default:
                await _started(manager, _KEYLESS)
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    assert refused.value.context["fallback"] is None
    assert "Send it on" not in refused.value.message
    assert default.value.context["fallback"] is None

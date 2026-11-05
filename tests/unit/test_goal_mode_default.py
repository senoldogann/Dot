"""A task or research run starts in goal mode. A chat does not. Passing false, and the packaged patience, stay."""

import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, PluginsConfig, SkillsConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import RunRecord

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _config() -> AppConfig:
    base: AppConfig = load_default_config()
    assert base.agent.goal_mode is True
    assert base.agent.goal_patience == 4
    assert base.policy.approvals == "ask"
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


@pytest.mark.asyncio
async def test_a_task_and_a_research_run_start_in_goal_mode_and_a_chat_does_not() -> None:
    config: AppConfig = _config()
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, {})
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, ()))
            task: RunRecord = await manager.start(
                "Write the note.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, None, None, ()
            )
            research: RunRecord = await manager.start(
                "Compare the notes.",
                "research",
                SCRIPTED_MODEL,
                None,
                "ui",
                None,
                None,
                "standard",
                None,
                None,
                None,
                (),
            )
            chat: RunRecord = await manager.start(
                "Hello.", "chat", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, None, None, ()
            )
            off: RunRecord = await manager.start(
                "Write the note.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, None, False, ()
            )
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    assert task.goal_mode and research.goal_mode
    assert not chat.goal_mode and not off.goal_mode
    assert (task.approvals, off.approvals) == ("ask", "ask")

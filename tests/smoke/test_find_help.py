"""find_help (item 163): a run looks for the help its Mac offers before it takes the hard way. Through the real loop,
the scripted model asks find_help for what it needs and is told first of the skill installed for it, with how to use
it; and on this Mac, Calendar is found scriptable, with the commands and classes of its own dictionary, for adding an
event to a calendar. A run offered more skills than its instructions list is listed those that fit its goal, and told
how many more find_help searches (item 166). With the embeddings of the memory, a goal and a need in Turkish find the
skills described in English (item 169); with the embedding service out of reach, find_help answers by the words and
says that finding help by meaning failed.

No network and no model service is used: the scripted model plays the run. The embeddings come from bge-m3 on the local
Ollama server; the test that needs it is skipped when it is not there.
"""

import asyncio
import re
import socket

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, OllamaConfig, load_default_config
from dotagent.core.paths import skills_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.help.apps import APP_ROOTS, scriptable_apps
from dotagent.help.rank import rank
from dotagent.help.types import HelpItem
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, Message, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Write this week's report."
NEED: str = "write the weekly report from my notes"
SKILL: str = """---
name: weekly-report
description: Write the weekly report from the week's notes, in the owner's format.
---
Read the notes of the week, then write report.md with one section per project.
"""
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
TURKISH_GOAL: str = "Bu haftanın raporunu yaz ve ekibe e-postayla gönder."
TURKISH_NEED: str = "haftalık raporu notlarımdan yaz"
_TIME_LIMIT_S: float = 60.0
_ASK: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="find_help", arguments={"need": NEED}),))
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "report.md is written.", "success": True}),)
)


# Two of them share words with the goal of the run of item 166: the weekly report, and an email to send.
OFFERED: dict[str, str] = {
    "release-notes": "Draft the release notes of a version from its commits.",
    "weekly-report": "Write the weekly report from the week's notes, in the owner's format.",
    "invoice-pdf": "Fill in the invoice PDF form of a client.",
    "mail-merge": "Send one email to each contact of a list.",
}


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def _embedder_ready() -> bool:
    """Whether the local Ollama server answers and has the embedding model of the default configuration."""
    config: AppConfig = load_default_config()
    try:
        answer: httpx.Response = httpx.get(f"{config.providers.ollama.base_url}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(
        model["name"].startswith(config.memory.embed_model) for model in answer.json()["models"]
    )


requires_embedder = pytest.mark.skipif(not _embedder_ready(), reason="needs the local Ollama server with bge-m3")


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def test_a_run_asks_find_help_and_is_told_first_of_the_skill_installed_for_its_need(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        folder = skills_dir() / "weekly-report"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_ASK, _FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
        finally:
            await close_runtime(runtime)
    answers: list[str] = [message.content for message in messages if message.role == "tool"]
    assert done.status == "done", done.result
    # The answer of find_help, then the one of finish.
    assert len(answers) == 2, answers
    lines: list[str] = answers[0].splitlines()
    assert lines[0] == f"Help on this Mac for: {NEED}", answers[0]
    assert lines[1].startswith("1. Skill weekly-report: Write the weekly report"), answers[0]
    assert 'call skill with name "weekly-report"' in lines[1], answers[0]
    # The eval's configuration has no desktop, so the run cannot script applications, and is told so.
    assert "scriptable applications were not searched" in answers[0], answers[0]


async def test_a_run_offered_more_skills_than_its_instructions_list_is_listed_those_that_fit_its_goal(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        for name, description in OFFERED.items():
            folder = skills_dir() / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n", encoding="utf-8"
            )
        base: AppConfig = eval_config(load_default_config())
        config: AppConfig = base.model_copy(update={"skills": base.skills.model_copy(update={"listed": 2})})
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, [_FINISH]))
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    "Write this week's report and email it to the team.",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    UI_SOURCE,
                    None,
                    None,
                    "standard",
                    None,
                    "yolo",
                    False,
                    (),
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)
    assert done.status == "done", done.result
    skills: str = model.requests[0].messages[0].content.split("\n# Skills\n", 1)[1]
    # Two of the seven skills are listed; the two others offered and the three that come with Dot (item 196) are not.
    assert (
        "- weekly-report: Write the weekly report from the week's notes, in the owner's format.\n"
        "- mail-merge: Send one email to each contact of a list.\n"
        "- 5 more installed skills fit your goal less well and are not listed here: find_help searches them all by "
        "your need, and skill reads one by its name."
    ) in skills, skills
    assert "release-notes" not in skills and "invoice-pdf" not in skills, skills


async def test_calendar_is_found_scriptable_by_its_own_dictionary_for_adding_an_event() -> None:
    apps: list[HelpItem] = await asyncio.to_thread(scriptable_apps, APP_ROOTS)
    best: list[HelpItem] = rank(apps, "add an event to a calendar", [], 3)
    assert [item.name for item in best][:1] == ["Calendar"], [item.name for item in best]
    assert "classes" in best[0].summary and "event" in best[0].summary, best[0].summary
    assert 'tell application "Calendar"' in best[0].use, best[0].use


@requires_embedder
async def test_with_the_embeddings_a_goal_and_a_need_in_turkish_find_the_skills_described_in_english(
    login_env: dict[str, str],
) -> None:
    ask: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="find_help", arguments={"need": TURKISH_NEED}),))
    with scratch_home() as home, eval_environment(home, {}):
        for name, description in OFFERED.items():
            folder = skills_dir() / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n", encoding="utf-8"
            )
        base: AppConfig = eval_config(load_default_config())
        config: AppConfig = base.model_copy(
            update={
                "skills": base.skills.model_copy(update={"listed": 2}),
                "memory": base.memory.model_copy(update={"embed_enabled": True}),
            }
        )
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, [ask, _FINISH]))
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    TURKISH_GOAL,
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    UI_SOURCE,
                    None,
                    None,
                    "standard",
                    None,
                    "yolo",
                    False,
                    (),
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
        finally:
            await close_runtime(runtime)
    assert done.status == "done", done.result
    # The goal shares no word with the skills: the one closest to it in meaning is listed first, the report's.
    skills: str = model.requests[0].messages[0].content.split("\n# Skills\n", 1)[1]
    listed: list[str] = [line.split(":", 1)[0] for line in skills.splitlines() if line.startswith("- ")]
    assert listed[0] == "- weekly-report", skills
    assert "more installed skills fit your goal less well" in skills, skills
    assert "release-notes" not in skills and "invoice-pdf" not in skills, skills
    # The need in Turkish finds the report's skill first, with how to use it.
    answers: list[str] = [message.content for message in messages if message.role == "tool"]
    lines: list[str] = answers[0].splitlines()
    assert lines[0] == f"Help on this Mac for: {TURKISH_NEED}", answers[0]
    assert lines[1].startswith("1. Skill weekly-report: Write the weekly report"), answers[0]


async def test_with_the_embedding_service_out_of_reach_find_help_answers_by_the_words_and_says_so(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        folder = skills_dir() / "weekly-report"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")
        base: AppConfig = eval_config(load_default_config())
        # Nothing listens at the embedding service's address; the run's model is the scripted one.
        ollama: OllamaConfig = base.providers.ollama.model_copy(
            update={"base_url": f"http://127.0.0.1:{_closed_port()}"}
        )
        config: AppConfig = base.model_copy(
            update={
                "providers": base.providers.model_copy(update={"ollama": ollama}),
                "memory": base.memory.model_copy(update={"embed_enabled": True}),
            }
        )
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_ASK, _FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            messages: list[Message] = await runtime.store.load_messages(done.thread_id, 0)
        finally:
            await close_runtime(runtime)
    assert done.status == "done", done.result
    answers: list[str] = [message.content for message in messages if message.role == "tool"]
    lines: list[str] = answers[0].splitlines()
    assert lines[1].startswith("1. Skill weekly-report: Write the weekly report"), answers[0]
    # What broke the connection is named (its words are the platform's).
    assert re.search(
        r" Help was found by its words alone: finding it by meaning failed: transport failure while talking to the"
        r" provider \(ConnectError: .+\)\.$",
        lines[-1],
    ), answers[0]

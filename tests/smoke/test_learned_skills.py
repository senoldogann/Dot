"""A run that went well becomes a learned skill (ROADMAP item 92): the review model writes it from the acting steps
once the run has ended, the next run is offered it as advice under Skills, the API lists it and drops it.

No model service is used: the scripted model plays the runs and answers for the review model too. The run's home is
a throw-away directory, so the skill folder is written there.
"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import PLAYBOOK_WRITTEN
from dotagent.agent.review import PLAYBOOK_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.skills.types import SKILL_FILE
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

GOAL: str = "Keep the line hello in notes.txt and read it back."
NOTES: str = "notes.txt"
KIND: str = "Keep a line in a notes file"
SKILL_NAME: str = "learned-keep-a-line-in-a-notes-file"
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
PORT: int = 18767
# A run of the owner's own, not an eval: evals never learn.
SOURCE: str = "test"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


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


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager
    model: RecordingProvider


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """An eval run's configuration with learned skills on after three acting steps, lessons off, and the scripted
    model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"playbooks": True, "skill_min_steps": 3, "lessons": "off"}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


@asynccontextmanager
async def _home(login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, turns))
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager, model=model)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


@asynccontextmanager
async def _client(home: Home) -> AsyncIterator[httpx.AsyncClient]:
    context: ServerContext = ServerContext(
        runtime=home.runtime,
        manager=home.manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=PORT,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(context)),
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


async def _run(home: Home) -> RunRecord:
    """``GOAL`` as a task run, once it ended and its skill work is done."""
    run: RunRecord = await home.manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, SOURCE, None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await home.manager.wait(run.id)
        await home.manager.distilled(run.id)
    return record


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _write() -> ScriptedCall:
    return ScriptedCall(tool="write_file", arguments={"path": NOTES, "content": "hello\n"})


def _read() -> ScriptedCall:
    return ScriptedCall(tool="read_file", arguments={"path": NOTES})


FINISH: ScriptedTurn = _turn(
    ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The file holds hello.", "success": True})
)
SKILL: ScriptedTurn = _turn(
    ScriptedCall(
        tool=PLAYBOOK_TOOL,
        arguments={
            "kind": KIND,
            "description": "Write a line to a notes file and read it back to check it.",
            "when": "A goal that asks to keep a line in a file.",
            "steps": ["Write <line> to <file> with write_file.", "Read <file> back and check the line is there."],
            "parameters": ["<line>: the text to keep", "<file>: the file's path"],
        },
    )
)


async def test_a_run_that_went_well_becomes_a_skill_offered_to_the_next_run_and_the_api_lists_and_drops_it(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        # The first run: three acting steps, then done; then the skill writer's answer.
        _turn(_write()),
        _turn(_read()),
        _turn(_read()),
        FINISH,
        SKILL,
        # The second run ends at once: two acting steps are short of the three a skill takes.
        _turn(_read()),
        FINISH,
    ]
    async with _home(login_env, turns) as home:
        first: RunRecord = await _run(home)
        assert first.status == "done"
        written: EventRecord | None = await home.runtime.store.last_event(first.id, PLAYBOOK_WRITTEN)
        assert written is not None
        assert (written.data["name"], written.data["host"], written.data["updated"]) == (SKILL_NAME, "this Mac", False)
        file = playbooks_dir() / SKILL_NAME / SKILL_FILE
        assert file.is_file()
        body: str = file.read_text(encoding="utf-8")
        assert f"## Kind of work\n\n{KIND}\n" in body and f"- Run {first.id}: 3 acting steps" in body
        # The writer saw the three acting steps and where the run acted.
        asked: list[str] = [
            request.messages[-1].content
            for request in home.model.requests
            if request.session_key == f"{first.thread_id}:playbook"
        ]
        assert len(asked) == 1 and "1. write_file " in asked[0] and "3. read_file " in asked[0]
        assert "this Mac" in asked[0]

        # The next run is offered the skill as advice, in its prompt's Skills section.
        second: RunRecord = await _run(home)
        assert second.status == "done"
        prompts: list[str] = [
            request.messages[0].content
            for request in home.model.requests
            if request.session_key == f"{second.thread_id}:run" or request.messages[0].content.count(SKILL_NAME)
        ]
        assert prompts and f"- {SKILL_NAME}: Write a line to a notes file" in prompts[-1]
        assert "advice from your own work" in prompts[-1]
        assert await home.runtime.store.last_event(second.id, PLAYBOOK_WRITTEN) is None

        async with _client(home) as client:
            listed = await client.get("/api/skills/learned")
            assert listed.status_code == 200
            items = listed.json()["items"]
            assert [(item["name"], item["kind"], item["runs"], item["retired"]) for item in items] == [
                (SKILL_NAME, KIND, [first.id], False)
            ]
            dropped = await client.delete(f"/api/skills/learned/{SKILL_NAME}")
            assert dropped.status_code == 200 and dropped.json()["items"] == []
            assert not (playbooks_dir() / SKILL_NAME).exists()
            assert (await client.delete(f"/api/skills/learned/{SKILL_NAME}")).status_code == 404

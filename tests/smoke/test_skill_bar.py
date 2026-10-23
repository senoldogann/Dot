"""The bar for a learned skill, in the real loop (item 585): a run whose steps the review model judges no procedure that
recurs gives no skill (the skip is logged with why), and the owner's Save skill ignores that judgement; a run that
changed Dot itself gives none, whatever the model says, and the model is not even asked; Save skill refuses such a run
with why. What counts as changing Dot itself is what the policy stored on a step (the files it flagged as Dot's own, and
the verdict of the shell guard on a command in yolo, whatever the words of the line say: a working folder, capital
letters, a link, a copy over the whole of Dot's home), what an MCP call's arguments name (the server of files that
writes Dot's configuration), and the words of a step that name a place Dot guards or loads or run its own program.

No network and no model service is used: the scripted model plays the runs and answers as the review model too; the
home is a throw-away directory, and the MCP server is the fixture of the MCP tests, which pretends.
"""

import asyncio
import secrets
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import PLAYBOOK_WRITTEN
from dotagent.agent.review import PLAYBOOK_TOOL
from dotagent.config import AppConfig, McpServerConfig, load_default_config
from dotagent.core.paths import config_path, dot_home, playbooks_dir, workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.skills.types import SKILL_FILE
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Keep the line hello in notes.txt and read it back."
NOTES: str = "notes.txt"
KIND: str = "Keep a line in a notes file"
SKILL_NAME: str = "learned-keep-a-line-in-a-notes-file"
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
PORT: int = 18768
FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
# The server of the MCP tests: its ``wipe`` takes a target and pretends, and declares no hints, as most servers do.
FILES: McpServerConfig = McpServerConfig(
    name="fixture", command=sys.executable, args=[str(FIXTURE)], startup_timeout_s=40.0, call_timeout_s=30.0
)
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


def _config(servers: Sequence[McpServerConfig]) -> AppConfig:
    """An eval run's configuration with learned skills on after three acting steps, lessons off, the scripted model as
    the review model too and the MCP ``servers`` it is given."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"playbooks": True, "skill_min_steps": 3, "lessons": "off"}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            "mcp": list(servers),
        }
    )


@asynccontextmanager
async def _home(
    login_env: dict[str, str],
    turns: Callable[[], Sequence[ScriptedTurn]],
    servers: Sequence[McpServerConfig] = (),
) -> AsyncIterator[Home]:
    """A runtime in a throw-away home whose model plays the turns ``turns`` gives once the home is there."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(servers), login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, turns()))
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


async def _run(home: Home, approvals: ApprovalMode) -> RunRecord:
    """``GOAL`` as a task run in the approval mode ``approvals``, once it ended and its skill work is done."""
    run: RunRecord = await home.manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, SOURCE, None, None, "standard", None, approvals, False, ()
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await home.manager.wait(run.id)
        await home.manager.distilled(run.id)
    assert record.status == "done", record
    return record


def _asked(home: Home, run: RunRecord) -> list[ChatRequest]:
    """The requests the skill writer was sent about ``run``."""
    return [request for request in home.model.requests if request.session_key == f"{run.thread_id}:playbook"]


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _write(path: str = NOTES) -> ScriptedCall:
    return ScriptedCall(tool="write_file", arguments={"path": path, "content": "hello\n"})


def _read() -> ScriptedCall:
    return ScriptedCall(tool="read_file", arguments={"path": NOTES})


FINISH: ScriptedTurn = _turn(
    ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The file holds hello.", "success": True})
)


def _writer_says(*, recurs: bool) -> ScriptedTurn:
    """The skill writer's answer for a run, with its judgement of whether the steps recur."""
    return _turn(
        ScriptedCall(
            tool=PLAYBOOK_TOOL,
            arguments={
                "recurs": recurs,
                "kind": KIND,
                "description": "Write a line to a notes file and read it back to check it.",
                "when": "A goal that asks to keep a line in a file.",
                "steps": ["Write <line> to <file> with write_file.", "Read <file> back and check the line is there."],
                "parameters": ["<line>: the text to keep", "<file>: the file's path"],
            },
        )
    )


def _skipped(logs: list[dict[str, object]]) -> list[str]:
    """The reasons the skill work gave for leaving a run's skill unwritten."""
    return [str(entry["reason"]) for entry in logs if entry["event"] == "playbook_skipped"]


async def test_a_run_whose_steps_the_model_judges_no_recurring_procedure_gives_no_skill_but_save_skill_writes_it(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        _turn(_write()),
        _turn(_read()),
        _turn(_read()),
        FINISH,
        _writer_says(recurs=False),
        # The owner's Save skill: the same judgement, which is not read at their word.
        _writer_says(recurs=False),
    ]
    async with _home(login_env, lambda: turns) as home:
        with capture_logs() as logs:
            run: RunRecord = await _run(home, "ask")
        assert len(_asked(home, run)) == 1
        assert _skipped(logs) == ["the review model judged the steps no procedure that recurs"]
        assert await home.runtime.store.last_event(run.id, PLAYBOOK_WRITTEN) is None
        assert not (playbooks_dir() / SKILL_NAME).exists()
        async with _client(home) as client:
            saved = await client.post(f"/api/runs/{run.id}/skill")
        assert saved.status_code == 201 and saved.json() == {"name": SKILL_NAME, "updated": False}
        assert (playbooks_dir() / SKILL_NAME / SKILL_FILE).is_file()


async def test_a_run_that_changed_dots_own_files_gives_no_skill_and_the_model_is_not_asked(
    login_env: dict[str, str],
) -> None:
    """The step is flagged by the policy as a change of Dot's own files (here a file written in the playbooks' folder,
    which a yolo run is allowed to). Save skill refuses the run, saying why."""

    def turns() -> list[ScriptedTurn]:
        return [_turn(_write()), _turn(_read()), _turn(_write(str(playbooks_dir() / "scratch.txt"))), FINISH]

    async with _home(login_env, turns) as home:
        with capture_logs() as logs:
            run: RunRecord = await _run(home, "yolo")
        assert _asked(home, run) == []
        assert len(_skipped(logs)) == 1 and "a step changed Dot's own files (write_file" in _skipped(logs)[0]
        assert await home.runtime.store.last_event(run.id, PLAYBOOK_WRITTEN) is None
        assert not (playbooks_dir() / SKILL_NAME).exists()
        async with _client(home) as client:
            refused = await client.post(f"/api/runs/{run.id}/skill")
        assert refused.status_code in (400, 422), refused.text
        assert "this run cannot be saved as a skill: a step changed Dot's own files" in refused.text
        assert _asked(home, run) == []


async def test_a_run_that_ran_dots_own_program_gives_no_skill_and_the_model_is_not_asked(
    login_env: dict[str, str],
) -> None:
    command: ScriptedCall = ScriptedCall(tool="shell", arguments={"command": "echo dotx skills restore a 1"})
    turns: list[ScriptedTurn] = [_turn(command), _turn(_write()), _turn(_read()), FINISH]
    async with _home(login_env, lambda: turns) as home:
        with capture_logs() as logs:
            run: RunRecord = await _run(home, "yolo")
        assert _asked(home, run) == []
        assert len(_skipped(logs)) == 1 and "a step ran Dot's own program (shell" in _skipped(logs)[0]
        assert await home.runtime.store.last_event(run.id, PLAYBOOK_WRITTEN) is None
        async with _client(home) as client:
            refused = await client.post(f"/api/runs/{run.id}/skill")
        assert refused.status_code in (400, 422) and "Dot's own program" in refused.text


def _shell(command: str, cwd: Path | None = None) -> ScriptedCall:
    arguments: dict[str, object] = {"command": command}
    if cwd is not None:
        arguments["cwd"] = str(cwd)
    return ScriptedCall(tool="shell", arguments=arguments)


def _wipe(target: str) -> ScriptedCall:
    """A call of the fixture server's ``wipe``: its arguments name ``target``, its target (to the policy) the server."""
    return ScriptedCall(tool="mcp__fixture__wipe", arguments={"target": target})


def _a_run_of(*acting: ScriptedCall) -> list[ScriptedTurn]:
    """The run: a write and a read of its own work, the calls under test, the end, and the writer's answer, which only
    a run the bar lets through is given."""
    return [_turn(_write()), _turn(_read()), *(_turn(call) for call in acting), FINISH, _writer_says(recurs=True)]


async def _refused_as_a_change_of_dot(home: Home, run: RunRecord, logs: list[dict[str, object]], why: str) -> None:
    """The model was not asked, the skill work said why, and no skill was written, nor does Save skill write one."""
    assert _asked(home, run) == []
    assert len(_skipped(logs)) == 1 and why in _skipped(logs)[0], _skipped(logs)
    assert await home.runtime.store.last_event(run.id, PLAYBOOK_WRITTEN) is None
    assert not (playbooks_dir() / SKILL_NAME).exists()
    async with _client(home) as client:
        refused = await client.post(f"/api/runs/{run.id}/skill")
    assert refused.status_code in (400, 422) and "this run cannot be saved as a skill" in refused.text, refused.text
    assert _asked(home, run) == []


async def test_a_command_the_guard_flagged_gives_no_skill_whatever_the_words_of_its_line_say(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """The security review of item 585: in yolo a command may change Dot's own files, and the guard stores what it
    saw (``secret_access``, or ``protected_read`` for a copy it cannot read the target of). The bar read only the
    words of the line, which name none of the places in these four: the folder is the line's working folder, the
    letters are capitals, the place is behind a link, and the target is the folder above the places."""
    stage: Path = tmp_path / "stage"
    cases: dict[str, Callable[[], ScriptedCall]] = {
        "a working folder of Dot's": lambda: _shell("printf 'x\\n' >> scratch.txt", cwd=playbooks_dir()),
        "capital letters": lambda: _shell(
            f"printf 'x\\n' >> {str(playbooks_dir()).replace('/playbooks', '/PLAYBOOKS')}/scratch.txt"
        ),
        "a link in the workspace": lambda: _shell("printf 'x\\n' >> pb/scratch.txt"),
        "a copy over the whole of Dot's home": lambda: _shell(f"cp -R {stage}/. {dot_home()}/"),
    }
    for label, call in cases.items():

        def turns(call: Callable[[], ScriptedCall] = call) -> list[ScriptedTurn]:
            playbooks_dir().mkdir(parents=True, exist_ok=True)
            (workspace_dir() / "pb").symlink_to(playbooks_dir(), target_is_directory=True)
            (stage / "playbooks").mkdir(parents=True, exist_ok=True)
            (stage / "playbooks" / "scratch.txt").write_text("x\n", encoding="utf-8")
            return _a_run_of(call())

        async with _home(login_env, turns) as home:
            with capture_logs() as logs:
                run: RunRecord = await _run(home, "yolo")
            # The step really changed Dot's own files: the case is what the bar is for, not a command that failed.
            assert (playbooks_dir() / "scratch.txt").is_file(), label
            steps = await home.runtime.store.result_steps(run.id)
            assert {"secret_access", "protected_read"} & set(steps[2].flags), (label, steps[2])
            await _refused_as_a_change_of_dot(home, run, logs, "a step the policy flagged")


async def test_an_mcp_call_that_names_a_place_of_dots_in_its_arguments_gives_no_skill(
    login_env: dict[str, str],
) -> None:
    """The security review of item 585: a server of files writes Dot's configuration, and the step's target is the
    server's name, so the bar read nothing of it. The executor reads the arguments where the call is classified and
    stores what they named with the step; the bar reads that. The same call that names a file of the run's own work is
    a skill, and so is a read of the same place through a server that declares itself read-only (as a native read of it
    is)."""

    def naming(place: Callable[[], str]) -> Callable[[], list[ScriptedTurn]]:
        return lambda: _a_run_of(_wipe(place()))

    for label, place in (
        ("its configuration", lambda: str(config_path())),
        ("its playbooks", lambda: str(playbooks_dir() / SKILL_NAME / SKILL_FILE)),
    ):
        async with _home(login_env, naming(place), (FILES,)) as home:
            with capture_logs() as logs:
                run: RunRecord = await _run(home, "yolo")
            steps = await home.runtime.store.result_steps(run.id)
            assert (steps[2].tool, steps[2].kind, steps[2].target) == ("mcp__fixture__wipe", "mcp", "fixture"), label
            assert steps[2].names_dot != "" and steps[2].names_dot.casefold() in place().casefold(), (label, steps[2])
            assert steps[0].names_dot == steps[1].names_dot == "", steps
            await _refused_as_a_change_of_dot(home, run, logs, "a step named")

    async with _home(login_env, naming(lambda: "scratch.txt"), (FILES,)) as home:
        run = await _run(home, "yolo")
        assert (await home.runtime.store.result_steps(run.id))[2].names_dot == ""
        assert len(_asked(home, run)) == 1
        assert await home.runtime.store.last_event(run.id, PLAYBOOK_WRITTEN) is not None
        assert (playbooks_dir() / SKILL_NAME / SKILL_FILE).is_file()

    def reading() -> list[ScriptedTurn]:
        return _a_run_of(
            _wipe("scratch.txt"), ScriptedCall(tool="mcp__fixture__echo", arguments={"text": str(config_path())})
        )

    async with _home(login_env, reading, (FILES,)) as home:
        run = await _run(home, "yolo")
        read = (await home.runtime.store.result_steps(run.id))[3]
        assert (read.kind, read.names_dot != "") == ("mcp_read", True)
        assert len(_asked(home, run)) == 1


async def test_a_run_whose_helper_changed_dots_own_files_gives_no_skill_and_one_whose_helper_did_not_gives_one(
    login_env: dict[str, str],
) -> None:
    """A run that delegates part of its work is no cleaner than the helper was: the steps of the runs it delegated are
    read with its own. Here the helper writes Dot's configuration in yolo, and the run that delegated it ends done after
    three acting steps of its own (the delegate call is none) and still gives no skill; the same run whose helper only
    writes a file of its own work gives one."""
    helped: ScriptedCall = ScriptedCall(
        tool="delegate", arguments={"goal": "Put the line hello into the file notes.txt of the workspace."}
    )

    def turns(helper: Callable[[], ScriptedCall]) -> Callable[[], list[ScriptedTurn]]:
        # The model is asked in the order the runs ask it: the run's three steps and its delegation, then the helper's
        # one step and its end, then the run's end, then the skill writer (given only to a run the bar lets through).
        return lambda: [
            _turn(_write()),
            _turn(_read()),
            _turn(_read()),
            _turn(helped),
            _turn(helper()),
            FINISH,
            FINISH,
            _writer_says(recurs=True),
        ]

    async with _home(login_env, turns(lambda: _write(str(config_path())))) as home:
        with capture_logs() as logs:
            run: RunRecord = await _run(home, "yolo")
        # The helper really changed Dot's own file, as a run of its own, and the steps of the family are read together.
        assert "hello" in await asyncio.to_thread(config_path().read_text, encoding="utf-8")
        children = await home.runtime.store.list_child_runs(run.id)
        assert [child.status for child in children] == ["done"]
        steps = await home.runtime.store.result_steps(run.id)
        assert [(step.tool, step.helper) for step in steps if step.tool == "write_file"] == [
            ("write_file", False),
            ("write_file", True),
        ]
        assert "secret_access" in next(step for step in steps if step.helper and step.tool == "write_file").flags
        await _refused_as_a_change_of_dot(home, run, logs, "(in a helper run: write_file")

    async with _home(login_env, turns(lambda: _write("helper-notes.txt"))) as home:
        run = await _run(home, "yolo")
        seen = [(step.tool, step.helper) for step in await home.runtime.store.result_steps(run.id)]
        assert ("write_file", True) in seen and ("delegate", False) in seen
        assert len(_asked(home, run)) == 1
        assert await home.runtime.store.last_event(run.id, PLAYBOOK_WRITTEN) is not None
        assert (playbooks_dir() / SKILL_NAME / SKILL_FILE).is_file()

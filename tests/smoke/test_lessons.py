"""Lessons in the real loop: once a run that reached its goal after a call failed has ended, the review model is asked
for its lessons. With ``propose`` they wait on the run until the owner saves or dismisses each through the API; with
``auto`` they are saved as notes, except after a run that read a web page, and a lesson that a saved note already says
is dropped. With lessons off, or after a run along which nothing went wrong, nothing is asked.

No model service is used: the scripted model plays the runs and answers for the review model too, the web page is
served on this machine and the memory has no embeddings (see ``eval_config``).
"""

import asyncio
import secrets
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.lessons import LessonView, RunLessons, run_lessons
from dotagent.agent.manager import RunManager
from dotagent.agent.review import LESSONS_TOOL
from dotagent.config import AppConfig, LessonsMode, load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, MemoryRecord, MemoryScore, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Keep the line hello in notes.txt and read it back."
NOTES: str = "notes.txt"
DRAFT: str = "draft.txt"
LESSON: str = "read_file fails on a file that does not exist yet; create notes.txt with write_file first."
OTHER: str = "Reading a file back right after writing it confirms that the write worked."
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
PORT: int = 18766
PAGE: bytes = b"<html><body><h1>Notes</h1><p>Anyone could have written this page.</p></body></html>"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_EVENTS_READ: int = 10


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
    """A runtime in a throw-away home, its run manager and the scripted model that plays every run in it."""

    runtime: Runtime
    manager: RunManager
    model: RecordingProvider


class _Page(BaseHTTPRequestHandler):
    """A web page, the kind of content third parties can author."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


def _config(lessons: LessonsMode) -> AppConfig:
    """The configuration of an eval run with ``lessons``, the scripted model as the review model too and the web pages
    of this machine allowed."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"lessons": lessons}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            "web": config.web.model_copy(update={"allow_private_hosts": True}),
        }
    )


@asynccontextmanager
async def _home(config: AppConfig, login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    """A runtime with ``config`` in a throw-away home, whose model plays ``turns``: one model call each, in order,
    whichever run or judgement makes it."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
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
    """The daemon's API on ``home``, without a socket."""
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
    """``GOAL`` as an unattended task run, once it ended done and its lessons are drawn."""
    run: RunRecord = await home.manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "ask", False, ()
    )
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await home.manager.wait(run.id)
        await home.manager.learned(run.id)
    assert record.status == "done", record
    return record


def _asked(home: Home, run: RunRecord) -> list[str]:
    """The prompts the lesson writer was sent about ``run``."""
    return [
        request.messages[-1].content
        for request in home.model.requests
        if request.session_key == f"{run.thread_id}:lessons"
    ]


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _read(name: str) -> ScriptedCall:
    return ScriptedCall(tool="read_file", arguments={"path": name})


def _write(name: str) -> ScriptedCall:
    return ScriptedCall(tool="write_file", arguments={"path": name, "content": "hello\n"})


FINISH: ScriptedTurn = _turn(
    ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The file holds hello.", "success": True})
)


def _stumbles(name: str, extra: Sequence[ScriptedCall]) -> list[ScriptedTurn]:
    """The steps of a run that reads ``name`` before it exists, writes it and reads it back (a later call of the same
    tool and target worked after the first one failed), makes the ``extra`` calls and finishes."""
    return [_turn(_read(name)), _turn(_write(name)), _turn(_read(name)), *(_turn(call) for call in extra), FINISH]


def _lessons(*lessons: str) -> ScriptedTurn:
    """The review model's answer to the lesson writer's question."""
    return _turn(ScriptedCall(tool=LESSONS_TOOL, arguments={"lessons": list(lessons)}))


async def test_proposed_lessons_wait_on_the_run_until_the_owner_saves_or_dismisses_each(
    login_env: dict[str, str],
) -> None:
    async with _home(_config("propose"), login_env, [*_stumbles(NOTES, ()), _lessons(LESSON, OTHER)]) as home:
        store: Store = home.runtime.store
        run: RunRecord = await _run(home)
        prompts: list[str] = _asked(home, run)
        assert len(prompts) == 1
        assert f"- read_file {home.runtime.workspace}/{NOTES} failed (" in prompts[0]
        assert "); a later call of the same tool and target worked." in prompts[0]
        proposed: RunLessons = RunLessons(
            lessons=[
                LessonView(index=0, text=LESSON, state="proposed", memory_id=None),
                LessonView(index=1, text=OTHER, state="proposed", memory_id=None),
            ],
            tainted=False,
            error=None,
        )
        assert await run_lessons(store, run.id) == proposed
        assert await store.count_memories() == 0
        lessons: str = f"/api/runs/{run.id}/lessons"
        async with _client(home) as client:
            shown: httpx.Response = await client.get(f"/api/runs/{run.id}")
            saved: httpx.Response = await client.post(f"{lessons}/0/save")
            dismissed: httpx.Response = await client.post(f"{lessons}/1/dismiss")
            again: list[int] = [
                (await client.post(f"{lessons}/{index}/{verb}")).status_code
                for index in (0, 1)
                for verb in ("save", "dismiss")
            ]
            unknown: list[int] = [
                (await client.post(path)).status_code for path in (f"{lessons}/2/save", "/api/runs/nope/lessons/0/save")
            ]
        assert shown.json()["lessons"] == proposed.model_dump(mode="json")
        assert (saved.status_code, dismissed.status_code) == (200, 200), (saved.text, dismissed.text)
        memory_id: str = saved.json()["lessons"]["lessons"][0]["memory_id"]
        assert dismissed.json()["lessons"] == RunLessons(
            lessons=[
                LessonView(index=0, text=LESSON, state="saved", memory_id=memory_id),
                LessonView(index=1, text=OTHER, state="dismissed", memory_id=None),
            ],
            tainted=False,
            error=None,
        ).model_dump(mode="json")
        # A lesson is saved or dismissed once; a lesson or a run that does not exist is not found.
        assert (again, unknown) == ([409, 409, 409, 409], [404, 404])
        note: MemoryRecord = await store.get_memory(memory_id)
        assert (note.content, note.tags, note.source_run) == (LESSON, ("lesson",), run.id)
        assert await store.count_memories() == 1


async def test_two_presses_of_save_at_once_save_the_lesson_once(login_env: dict[str, str]) -> None:
    async with _home(_config("propose"), login_env, [*_stumbles(NOTES, ()), _lessons(LESSON)]) as home:
        run: RunRecord = await _run(home)
        async with _client(home) as client:
            presses: tuple[httpx.Response, ...] = tuple(
                await asyncio.gather(*(client.post(f"/api/runs/{run.id}/lessons/0/save") for _ in range(2)))
            )
        assert sorted(press.status_code for press in presses) == [200, 409], [press.text for press in presses]
        assert len(await home.runtime.store.events_of_kind(run.id, "lesson_saved", _EVENTS_READ)) == 1
        assert await home.runtime.store.count_memories() == 1


async def test_auto_leaves_a_lesson_alone_that_the_owner_dismissed_while_it_saved(login_env: dict[str, str]) -> None:
    async with (
        _home(_config("auto"), login_env, [*_stumbles(NOTES, ()), _lessons(LESSON, OTHER)]) as home,
        home.runtime.bus.subscribe(None) as heard,
    ):
        run: RunRecord = await home.manager.start(
            GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "ask", False, ()
        )
        async with asyncio.timeout(_TIME_LIMIT_S):
            while (await heard.queue.get()).kind != "lessons_proposed":
                continue
            # The owner dismisses the second lesson as soon as the page offers it, while the first is being saved.
            await home.manager.dismiss_lesson(run.id, 1)
            await home.manager.learned(run.id)
        lessons: RunLessons | None = await run_lessons(home.runtime.store, run.id)
        assert lessons is not None
        assert [lesson.state for lesson in lessons.lessons] == ["saved", "dismissed"]
        assert await home.runtime.store.count_memories() == 1


async def test_auto_saves_the_lessons_of_a_run_and_drops_a_lesson_a_saved_note_already_says(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [*_stumbles(NOTES, ()), _lessons(LESSON), *_stumbles(DRAFT, ()), _lessons(LESSON)]
    async with _home(_config("auto"), login_env, turns) as home:
        store: Store = home.runtime.store
        first: RunRecord = await _run(home)
        saved: list[EventRecord] = await store.events_of_kind(first.id, "lesson_saved", _EVENTS_READ)
        assert [(event.data["index"], event.data["by"]) for event in saved] == [(0, "auto")]
        memory_id: str = str(saved[0].data["memory_id"])
        assert await run_lessons(store, first.id) == RunLessons(
            lessons=[LessonView(index=0, text=LESSON, state="saved", memory_id=memory_id)], tainted=False, error=None
        )
        note: MemoryRecord = await store.get_memory(memory_id)
        assert (note.content, note.tags, note.source_run) == (LESSON, ("lesson",), first.id)
        # The next run that stumbles is shown the saved lesson, and the same lesson proposed again is dropped.
        second: RunRecord = await _run(home)
        prompts: list[str] = _asked(home, second)
        assert len(prompts) == 1
        assert f"- {LESSON}" in prompts[0]
        assert await run_lessons(store, second.id) is None
        assert await store.count_memories() == 1
        # The second run was handed the lesson and ended done: the lesson's score says so.
        assert await store.memory_scores([memory_id], 0.0) == {memory_id: MemoryScore(handed=1, done=1, failed=0)}


async def test_the_lessons_of_a_run_that_read_a_web_page_are_only_proposed(
    login_env: dict[str, str], site: str
) -> None:
    fetch: ScriptedCall = ScriptedCall(tool="web_fetch", arguments={"url": f"{site}/notes"})
    async with _home(_config("auto"), login_env, [*_stumbles(NOTES, (fetch,)), _lessons(LESSON)]) as home:
        run: RunRecord = await _run(home)
        assert await run_lessons(home.runtime.store, run.id) == RunLessons(
            lessons=[LessonView(index=0, text=LESSON, state="proposed", memory_id=None)], tainted=True, error=None
        )
        assert await home.runtime.store.count_memories() == 0


async def test_lessons_are_proposed_by_default_and_off_leaves_a_run_unasked(login_env: dict[str, str]) -> None:
    assert load_default_config().agent.lessons == "propose"
    async with _home(_config("off"), login_env, _stumbles(NOTES, ())) as home:
        run: RunRecord = await _run(home)
        assert _asked(home, run) == []
        assert await run_lessons(home.runtime.store, run.id) is None


async def test_a_run_along_which_nothing_went_wrong_is_not_asked_for_lessons(login_env: dict[str, str]) -> None:
    async with _home(_config("propose"), login_env, [_turn(_write(NOTES)), _turn(_read(NOTES)), FINISH]) as home:
        run: RunRecord = await _run(home)
        assert _asked(home, run) == []
        assert await run_lessons(home.runtime.store, run.id) is None

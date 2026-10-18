"""What was learned on a site comes back there (item 308), through the real loop in a real headless Chrome with a
profile of its own and a page served on this Mac: a chat opens the page, and its next step is handed the lesson tagged
with the page's host and the learned skill written there that its instructions did not list, the lesson counted as
handed; a lesson of another site is not. A lesson of the run is tagged with the host it names. No model is called:
every role is the scripted model and Ollama's address is a closed port.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.lessons import LESSON_TAG, lesson_tags
from dotagent.agent.manager import RunManager
from dotagent.agent.playbooks import Playbook, playbook_name, write_playbook
from dotagent.agent.review import PlaybookDraft
from dotagent.agent.sites import site_tag
from dotagent.config import load_config
from dotagent.core.paths import playbooks_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.memory.service import save_memory
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, MemoryRecord, RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_HOST: str = "127.0.0.1"
_GOAL: str = "Check the local test page."
_HERE: str = "On 127.0.0.1 the second table holds what the first one leaves out."
_THERE: str = "On example.org the menu hides the archive."
_NOTE_START: str = "[system note] Earlier runs learned this where this run just arrived."
_RUN_WAIT_S: float = 60.0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = b"<!doctype html><title>Hours</title><h1>Opening hours</h1><table><tr><td>9-17</td></tr></table>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


class _Recorder:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self.script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self.script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self.script.list_models()

    async def aclose(self) -> None:
        return None


@pytest.fixture(scope="module")
def page() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer((_HOST, 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://{_HOST}:{server.server_address[1]}/"
    finally:
        server.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A throw-away home where every model role is the scripted one, a run's instructions list one skill, and no
    model service can be reached."""
    home: Path = tmp_path_factory.mktemp("home")
    roles: str = "".join(f'{role} = "{SCRIPTED_MODEL}"\n' for role in ("main", "fast", "review", "vision"))
    (home / "config.toml").write_text(
        f"[browser]\nheadless = true\n\n[models]\n{roles}\n[memory]\nembed_enabled = false\n\n"
        '[skills]\nlisted = 1\nelsewhere = false\n\n[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


def _learned(kind: str, host: str, description: str) -> Playbook:
    return Playbook(
        name=playbook_name(kind),
        kind=kind,
        host=host,
        draft=PlaybookDraft(
            kind=kind, description=description, when=description, steps=("Open the page.",), parameters=()
        ),
        run_id="run_old",
        agent_id=None,
        goal=description,
        written_at=1_700_000_000.0,
        acting_steps=2,
        tainted=False,
    )


async def test_a_run_that_reaches_a_site_is_handed_what_was_learned_there(runtime: Runtime, page: str) -> None:
    here: MemoryRecord = (
        await save_memory(runtime.memory, _HERE, (LESSON_TAG, site_tag(_HOST)), "run_old", None, None, False)
    ).record
    there: MemoryRecord = (
        await save_memory(runtime.memory, _THERE, (LESSON_TAG, site_tag("example.org")), "run_old", None, None, False)
    ).record
    # The shop's skill fits the goal's words and is the one the instructions list; the local page's skill is not.
    listed: Playbook = _learned("check the shop page", "shop.example.com", "Check the local test page of the shop.")
    local: Playbook = _learned("read the hours table", _HOST, "Read the opening hours from the library table.")
    for playbook in (listed, local):
        await asyncio.to_thread(write_playbook, playbooks_dir(), playbook)
    model: _Recorder = _Recorder(
        ScriptedProvider(
            _PROVIDER,
            [
                ScriptedTurn(text="", calls=(ScriptedCall(tool="browser_open", arguments={"url": page}),)),
                ScriptedTurn(text="The page is open.", calls=()),
            ],
        )
    )
    await runtime.router.register(_PROVIDER, model)
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            _GOAL, "chat", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", None, ()
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    finally:
        await manager.shutdown()
    assert ended.status == "done", ended.result
    assert len(model.requests) == 2
    assert (
        listed.name in model.requests[0].messages[0].content and local.name not in model.requests[0].messages[0].content
    )
    notes: list[str] = [
        message.content
        for message in model.requests[1].messages
        if isinstance(message.content, str) and message.content.startswith(_NOTE_START)
    ]
    assert len(notes) == 1, [message.content for message in model.requests[1].messages][-3:]
    assert f"On {_HOST}:\n- [{here.id}] {_HERE}" in notes[0]
    assert f"- Learned skill {local.name}: Read the opening hours from the library table." in notes[0]
    assert _THERE not in notes[0] and listed.name not in notes[0]
    # The lesson counts as handed to the run, and the run's page says where it was learned.
    assert await runtime.store.handed_memory_ids(run.id) == {here.id}
    events: list[EventRecord] = await runtime.store.events_of_kind(run.id, "memories", 5)
    assert [(event.data["ids"], event.data["site"]) for event in events] == [([here.id], _HOST)]
    # A lesson of this run that names the page's host is tagged with it; one that names nothing it reached is not.
    assert await runtime.store.run_sites(run.id) == [_HOST]
    assert await lesson_tags(runtime.store, run.id, "On 127.0.0.1 the table has two pages.") == (
        LESSON_TAG,
        site_tag(_HOST),
    )
    assert await lesson_tags(runtime.store, run.id, "Read a file before changing it.") == (LESSON_TAG,)
    assert (await runtime.store.get_memory(there.id)).used_count == 0

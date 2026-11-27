"""A run's start does not wait for a slow embedding service (item 2004): with an Ollama stand-in that holds every
embedding for longer than ``[memory] start_recall_s`` (as the owner's local server did while it served a chat model: a
start waited 304 s), a run of the real loop finds its note, and then picks its skills, by their words once the deadline
passes, says so in an ``embedding_failed`` line each, records how long each read of its start took (``start_reads``),
and ends done.

No network and no model service is used: the scripted model plays the run, and a stand-in on the loopback plays the
embedding route.
"""

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, OllamaConfig, load_default_config
from dotagent.core.paths import skills_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord
from dotagent.store.perf import START_READS_EVENT
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Write the report for the team."
NOTE: str = "The report goes to the team on Fridays, as a PDF."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "report.md is written.", "success": True}),)
)
# How long the stand-in holds an embedding, and the start's deadline for it.
_HELD_S: float = 20.0
_DEADLINE_S: float = 0.5
_TIME_LIMIT_S: float = 60.0
# More skills than the prompt lists, so that the start picks them by meaning too.
SKILLS: dict[str, str] = {
    "weekly-report": "Write the weekly report from the week's notes, in the owner's format.",
    "invoice-pdf": "Fill in the invoice PDF form of a client.",
}


class _SlowRunner(BaseHTTPRequestHandler):
    """Ollama's embedding route that answers only after ``_HELD_S``: a server busy with another model."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        time.sleep(_HELD_S)
        body: bytes = json.dumps({"embeddings": [[1.0, 0.0]]}).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            # The start gave up on the embedding at its deadline and closed the connection: what the test is after.
            return

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def _slow_ollama() -> Iterator[str]:
    """The base address of the stand-in, serving until the block ends; a request still held is not waited for."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _SlowRunner)
    server.daemon_threads = True
    server.block_on_close = False
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


async def test_a_start_finds_its_notes_by_their_words_once_the_embedding_service_keeps_it_past_the_deadline(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}), _slow_ollama() as ollama_url:
        for name, description in SKILLS.items():
            folder = skills_dir() / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n", encoding="utf-8"
            )
        base: AppConfig = eval_config(load_default_config())
        ollama: OllamaConfig = base.providers.ollama.model_copy(update={"base_url": ollama_url})
        config: AppConfig = base.model_copy(
            update={
                "providers": base.providers.model_copy(update={"ollama": ollama}),
                "memory": base.memory.model_copy(update={"embed_enabled": True, "start_recall_s": _DEADLINE_S}),
                "skills": base.skills.model_copy(update={"listed": 1}),
            }
        )
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            note, _ = await runtime.store.add_memory_once(NOTE, [], None, None, None, None, False)
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH]))
            manager: RunManager = RunManager(runtime)
            try:
                began: float = time.monotonic()
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    done: RunRecord = await manager.wait(run.id)
                took: float = time.monotonic() - began
                events: list[EventRecord] = await runtime.store.list_events(run.id, 0, 500)
                assert done.status == "done", done.result
                # The run did not wait out the stand-in's hold.
                assert took < _HELD_S, took
                failed: list[EventRecord] = [event for event in events if event.kind == "embedding_failed"]
                assert [(event.data["purpose"], event.data["error"]) for event in failed] == [
                    ("notes", f"the notes were not found by meaning within {_DEADLINE_S:g} s"),
                    ("skills", f"the skills were not found by meaning within {_DEADLINE_S:g} s"),
                ]
                recalled: list[EventRecord] = [event for event in events if event.kind == "memories"]
                assert len(recalled) == 1 and recalled[0].data["ids"] == [note.id]
                reads: list[EventRecord] = [event for event in events if event.kind == START_READS_EVENT]
                assert len(reads) == 1
                seconds = reads[0].data["seconds"]
                assert isinstance(seconds, dict)
                assert set(seconds) == {
                    "profile",
                    "agent",
                    "agents",
                    "accounts",
                    "skills",
                    "notes",
                    "last call",
                    "secrets and harness",
                }
                notes_s = seconds["notes"]
                assert isinstance(notes_s, float) and _DEADLINE_S <= notes_s < _HELD_S
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)

"""A night of the reflection with the life of the notes (item 584), in the real runtime on the scripted model: the
notes that say the same in other words are merged by the review model's answer, a lesson the owner saved or pinned is
left alone, the notes that rested long enough are counted as proposals and none is forgotten, the trash is emptied of
what stayed in it for its days and of nothing else, and the owner section is as it was. The API then lists the
proposals, forgets and keeps by the ids the page names, restores from the trash and takes the merge back.

No model service is used: the scripted model plays the review model, a stub the embedding service."""

import asyncio
import dataclasses
import math
import sqlite3
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.reflection import last_reflection
from dotagent.agent.reflection_record import summary_line
from dotagent.agent.review import MERGE_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import db_path
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.memory.ranking import Vector
from dotagent.memory.service import OWNER_KEY, REST_TAG, MemoryIndex, OwnerSection, set_owner_section
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "memory-lifecycle-token"
PORT: int = 18768
DAY_S: float = 86400.0
TWIN: str = "pdftotext keeps the columns of a table only with the -layout flag."
OTHER: str = "Use pdftotext with -layout when a table must keep its columns."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _unit(angle: float) -> Vector:
    return (math.cos(angle), math.sin(angle))


class _Embedder:
    """The embedding service: each text where the test placed it, any other far away."""

    def __init__(self) -> None:
        self.places: dict[str, Vector] = {}

    @property
    def model(self) -> str:
        return "stub"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self.places.setdefault(text, _unit(1.0 + 0.7 * len(self.places))) for text in texts]

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager
    embedder: _Embedder


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _home(login_env: dict[str, str], turns: Sequence[ScriptedTurn]) -> AsyncIterator[Home]:
    """An eval run's configuration with the scripted model as the review model, whose runtime remembers by a stub
    embedding service (the eval configuration has the embeddings off)."""
    with scratch_home() as home, eval_environment(home, {}):
        config: AppConfig = eval_config(load_default_config())
        config = config.model_copy(update={"models": config.models.model_copy(update={"review": SCRIPTED_MODEL})})
        opened: Runtime = await open_runtime(config, login_env)
        try:
            await opened.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
            embedder: _Embedder = _Embedder()
            runtime: Runtime = dataclasses.replace(
                opened, memory=MemoryIndex(store=opened.store, embedder=embedder, config=opened.config.memory)
            )
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager, embedder=embedder)
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(opened)


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


def _age(memory_id: str, *, created: float, used: float | None = None) -> None:
    """Make a note look as if an older daemon had written it and a run last used it at ``used``."""
    connection: sqlite3.Connection = sqlite3.connect(db_path())
    try:
        connection.execute(
            "UPDATE memories SET created_at = ?, updated_at = ?, last_used_at = ? WHERE id = ?",
            (created, created, used, memory_id),
        )
        connection.commit()
    finally:
        connection.close()


def _merge_answer(*verdicts: tuple[int, list[int], int]) -> ScriptedTurn:
    return ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(
                tool=MERGE_TOOL,
                arguments={
                    "verdicts": [
                        {"group": group, "merge": merge, "keep": keep, "reason": "the same thing"}
                        for group, merge, keep in verdicts
                    ]
                },
            ),
        ),
    )


async def test_a_night_merges_what_says_the_same_proposes_what_rested_and_leaves_the_owner_section_and_the_trash_alone(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env, [_merge_answer((1, [1, 2], 1))]) as home:
        store: Store = home.runtime.store
        now: float = time.time()
        # Two lessons that say the same in other words (0.825 close: past the bar of the merge, short of the outdated
        # check's 0.85 and the fold's 0.92), a lesson the owner saved and one pinned, both old and unused, an old lesson
        # that rested long ago and one that rested for a few days.
        home.embedder.places.update({TWIN: _unit(0.0), OTHER: _unit(0.6)})
        first = await store.add_memory(TWIN, ("lesson", "pdf"), "run_1", None, _unit(0.0), False)
        second = await store.add_memory(OTHER, ("lesson", "tables"), "run_2", None, _unit(0.6), False)
        saved = await store.add_memory(
            "Pdftotext with -layout. A lesson the owner saved.", ("lesson",), "run_3", None, _unit(2.0), False
        )
        await store.add_event("run_3", "lesson_saved", {"index": 0, "memory_id": saved.id, "by": "owner"})
        pinned = await store.add_memory(
            "A pinned lesson about -layout and pdftotext.", ("lesson",), "run_4", None, _unit(3.0), False
        )
        await store.set_memory_pin(pinned.id, now)
        for stale in (saved, pinned):
            _age(stale.id, created=now - 400 * DAY_S)
        long_rested = await store.add_memory(
            "A lesson nobody read for a year.", ("lesson", REST_TAG), "run_5", None, None, False
        )
        _age(long_rested.id, created=now - 400 * DAY_S)
        recent_rest = await store.add_memory(
            "A lesson that rested a week ago.", ("lesson",), "run_6", None, None, False
        )
        await store.rest_memory(recent_rest.id, ("lesson", REST_TAG), now - 7 * DAY_S)
        forgotten_long_ago = await store.add_memory("Forgotten a month ago.", (), "run_7", None, None, False)
        forgotten_lately = await store.add_memory("Forgotten last week.", (), "run_8", None, None, False)
        await store.trash_memory(forgotten_long_ago.id, now - 31 * DAY_S)
        await store.trash_memory(forgotten_lately.id, now - 7 * DAY_S)
        await set_owner_section(
            store, OwnerSection(dot_lines=("Works late.",), owner_lines=("I read Turkish.",), updated_at=5.0)
        )
        section_before: str | None = await store.kv_get(OWNER_KEY)
        before: int = await store.count_memories()

        reflection = await home.manager.reflect(now)

        # One merge: the two lessons are one note with the words the model kept, and rest whole beside it.
        assert (reflection.merged, reflection.to_forget, reflection.purged, reflection.runs_read) == (1, 1, 1, 0)
        assert summary_line(reflection) == "Reflected on 0 runs: 0 lessons, 1 merged, 0 retired, 1 to forget."
        assert await last_reflection(store) == reflection
        notes: list[MemoryRecord] = await store.list_memories(50, 0)
        merged: MemoryRecord = next(note for note in notes if note.merged_from)
        # The newer of the two was the first the model was shown, and its words were kept.
        assert (merged.content, merged.tags, merged.tainted) == (OTHER, ("lesson", "tables", "pdf"), False)
        for source in (first, second):
            held: MemoryRecord = await store.get_memory(source.id)
            assert held.merged_into == merged.id and REST_TAG in held.tags and held.content == source.content
        # The lessons the owner saved and pinned are as they were, though old and unused.
        for kept in (saved, pinned):
            held = await store.get_memory(kept.id)
            assert REST_TAG not in held.tags and held.merged_into is None
        # Nothing was forgotten by the night: the notes are the ones there were, the merged note and the journal more;
        # the trash lost the note that had waited its days and kept the other.
        assert reflection.journal is not None
        assert await store.count_memories() == before + 2
        assert {found.record.id for found in await store.trashed_memories(10)} == {forgotten_lately.id}
        # The owner section is the one it was.
        assert await store.kv_get(OWNER_KEY) == section_before

        async with _client(home) as client:
            proposed = await client.get("/api/memories/proposals")
            assert proposed.status_code == 200, proposed.text
            body = proposed.json()
            assert (body["forget_after_days"], body["trash_days"]) == (90, 30)
            assert [item["record"]["id"] for item in body["items"]] == [long_rested.id]
            # The press names the ids the page showed: this one, and one that is not proposed.
            forgot = await client.post("/api/memories/proposals/forget", json={"ids": [long_rested.id, recent_rest.id]})
            assert forgot.status_code == 200, forgot.text
            assert [note["id"] for note in forgot.json()["done"]] == [long_rested.id]
            assert forgot.json()["skipped"] == [recent_rest.id]
            trash = await client.get("/api/memories/trash")
            assert {item["record"]["id"] for item in trash.json()["items"]} == {long_rested.id, forgotten_lately.id}
            # The trash says how many notes it holds in all, as it lists the newest 200 at most.
            assert trash.json()["total"] == 2 and trash.json()["trash_days"] == 30
            restored = await client.post(f"/api/memories/trash/{long_rested.id}/restore")
            assert restored.status_code == 200 and REST_TAG not in restored.json()["tags"]
            # The merge is taken back: its sources wake apart, the merged note waits in the trash.
            undone = await client.post(f"/api/memories/{merged.id}/unmerge")
            assert undone.status_code == 200, undone.text
            assert {note["id"] for note in undone.json()["woken"]} == {first.id, second.id}
            assert all("keep-separate" in note["tags"] for note in undone.json()["woken"])
            missing = await client.get(f"/api/memories/{merged.id}")
            assert missing.status_code == 404
            # Delete for good is the owner's press too, and only for a note in the trash.
            deleted = await client.delete(f"/api/memories/trash/{forgotten_lately.id}")
            assert deleted.status_code == 200 and forgotten_lately.id not in {
                item["record"]["id"] for item in deleted.json()["items"]
            }
            again = await client.delete(f"/api/memories/trash/{forgotten_lately.id}")
            assert again.status_code == 404
            # A pin through the API, and the order of the list.
            pin = await client.put(f"/api/memories/{recent_rest.id}/pin", json={"pinned": True})
            assert pin.status_code == 200 and pin.json()["pinned_at"] is not None and REST_TAG not in pin.json()["tags"]
            listed = await client.get("/api/memories", params={"order": "least_used", "limit": 3})
            assert listed.status_code == 200 and len(listed.json()["items"]) == 3
            refused = await client.get("/api/memories", params={"order": "sideways"})
            assert refused.status_code == 422


async def test_a_night_with_nothing_to_merge_asks_the_review_model_nothing_and_writes_nothing_to_the_owner(
    login_env: dict[str, str],
) -> None:
    async with _home(login_env, []) as home:
        store: Store = home.runtime.store
        await store.add_memory("One lesson about the garden shed.", ("lesson",), "run_1", None, _unit(0.0), False)
        await store.add_memory(
            "A lesson about something else: the cellar.", ("lesson",), "run_2", None, _unit(2.5), False
        )
        reflection = await home.manager.reflect(time.time())
        assert (reflection.merged, reflection.to_forget, reflection.purged) == (0, 0, 0)
        assert await store.kv_get(OWNER_KEY) is None

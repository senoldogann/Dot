"""The files a message came with stand under it as its run starts (item 967), not once the run's start is over: a run's
start may take seconds (here a vision model that has not yet described the picture), and the page waited for it. A real
run on a scripted model, whose vision call is held until the files were seen."""

import asyncio
import struct
import zlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.attachments import ATTACHMENTS_EVENT, StoredAttachment, store_attachment
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 30.0


class _HeldProvider(ScriptedProvider):
    """The scripted model, whose first call (the vision model describing the picture) waits for ``release``."""

    def __init__(self, turns: tuple[ScriptedTurn, ...], release: asyncio.Event) -> None:
        super().__init__(_PROVIDER, turns)
        self._release: asyncio.Event = release
        self._calls: int = 0
        self.asked: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self._calls += 1
        if self._calls == 1:
            self.asked.set()
            await self._release.wait()
        return await super().complete(request, sink)


def _png() -> bytes:
    """A 16 x 16 PNG of one colour."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row: bytes = b"\x00" + bytes((200, 40, 40)) * 16
    header: bytes = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * 16)) + chunk(b"IEND", b"")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\nvision = "{SCRIPTED_MODEL}"\n\n[agent]'
        "\ngoal_mode = false",
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


async def test_the_pictures_of_a_message_are_told_before_the_vision_model_has_described_them(runtime: Runtime) -> None:
    release: asyncio.Event = asyncio.Event()
    provider: _HeldProvider = _HeldProvider(
        (
            ScriptedTurn(text="A red square.", calls=()),
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "A red square.", "success": True}),)
            ),
        ),
        release,
    )
    await runtime.router.register(_PROVIDER, provider)
    picture: StoredAttachment = await asyncio.to_thread(
        store_attachment, runtime.workspace, "red.png", "image/png", _png()
    )
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "What is this?",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (picture,),
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            await provider.asked.wait()
        told: EventRecord | None = await runtime.store.last_event(run.id, ATTACHMENTS_EVENT)
        assert told is not None, "the picture was not told while the vision model was still describing it"
        assert [file["name"] for file in told.data["files"]] == ["red.png"], told.data
        release.set()
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
        assert ended.status == "done", ended.status
    finally:
        release.set()
        await manager.shutdown()

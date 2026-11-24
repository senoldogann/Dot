"""What the owner does with Dot's tells (item 1743), in the real runtime, store and routes on the scripted model: a
tell of Dot's own and the end of a run are recorded when they are told; opening the chat of a tell's run answers it, a
closed notification is reported once, and the owner writing in the chat makes it acted on; `GET /api/tells` counts them
by kind and by watcher. No model service is used and nothing leaves the Mac."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.router import parse_model_ref
from dotagent.notify.owner import tell_owner
from dotagent.notify.tell_ledger import TELL_SOURCE_KINDS, watch_tells
from dotagent.notify.tells import TellSent
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "tell-ledger-token"
PORT: int = 18769
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _finish() -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),))


@dataclass(frozen=True)
class Home:
    runtime: Runtime
    manager: RunManager


@pytest_asyncio.fixture(loop_scope="module")
async def home() -> AsyncIterator[Home]:
    with scratch_home() as scratch, eval_environment(scratch, {}):
        config: AppConfig = eval_config(load_default_config())
        runtime: Runtime = await open_runtime(config, await read_login_env(10.0, ()))
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_finish(), _finish()]))
            manager: RunManager = RunManager(runtime)
            try:
                yield Home(runtime=runtime, manager=manager)
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


async def _until(count: int, home: Home) -> list[TellSent]:
    """The tells the ledger holds once it holds ``count``."""
    for _ in range(100):
        tells: list[TellSent] = await home.runtime.store.tells_since(0.0)
        if len(tells) >= count:
            return tells
        await asyncio.sleep(0.05)
    raise AssertionError(f"the ledger holds {len(await home.runtime.store.tells_since(0.0))} tells, not {count}")


async def test_a_tell_is_answered_by_opening_its_chat_or_closing_its_notification_and_counted(home: Home) -> None:
    stopping: asyncio.Event = asyncio.Event()
    async with home.runtime.bus.subscribe_kinds(TELL_SOURCE_KINDS) as feed, _client(home) as client:
        subscription: Subscription = feed
        ledger: asyncio.Task[None] = asyncio.create_task(watch_tells(home.runtime.store, subscription, stopping))
        try:
            first: RunRecord = await home.manager.start(
                "Say done.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
            )  # fmt: skip
            assert (await home.manager.wait(first.id)).status == "done"
            # The end of the run goes to the phone, so it is a tell; so is what Dot says on its own, with its watcher.
            await tell_owner(
                home.runtime.bus, "Bank mail", "A statement came.", f"#/runs/{first.id}", "watcher", "wat_1"
            )
            await tell_owner(home.runtime.bus, "Landlord", "Re: rent.", "#/settings/schedules", "watcher", "wat_2")
            tells: list[TellSent] = await _until(3, home)
            assert sorted((tell.kind, tell.subject) for tell in tells) == [
                ("run_end", ""),
                ("watcher", "wat_1"),
                ("watcher", "wat_2"),
            ]
            # A chat opened answers the tells that open it, once; a chat they do not open answers none.
            opened = await client.patch(f"/api/threads/{first.thread_id}", json={"seen": True})
            assert opened.status_code == 204
            again = await client.patch(f"/api/threads/{first.thread_id}", json={"seen": True})
            assert again.status_code == 204
            responses = await home.runtime.store.tell_responses_since(0.0)
            assert sorted(response.response for response in responses) == ["opened", "opened"]
            # A notification closed unopened is reported with its route, once, and only for a tell that went out.
            closed = await client.post("/api/tells/dismissed", json={"route": "#/settings/schedules", "via": "desktop"})
            assert closed.json() == {"recorded": True}
            twice = await client.post("/api/tells/dismissed", json={"route": "#/settings/schedules", "via": "push"})
            assert twice.json() == {"recorded": False}
            nothing = await client.post("/api/tells/dismissed", json={"route": "#/runs/none", "via": "push"})
            assert nothing.json() == {"recorded": False}
            # The owner writes in the chat: the tells that open it are acted on, not only opened.
            followed: RunRecord = await home.manager.start(
                "And again.", "task", SCRIPTED_MODEL, first.thread_id, "ui", None, None, "standard", None, "yolo",
                False, (),
            )  # fmt: skip
            assert (await home.manager.wait(followed.id)).status == "done"
            await _until(4, home)
            report = (await client.get("/api/tells")).json()
        finally:
            stopping.set()
            await ledger
    rows = {(row["kind"], row["subject"]): row["counts"] for row in report["rows"]}
    assert report["days"] == 30 and report["window_s"] == 1800.0
    assert rows[("watcher", "wat_1")]["acted"] == 1
    assert rows[("watcher", "wat_2")]["dismissed"] == 1 and rows[("watcher", "wat_2")]["unmeasured"] == 0
    # Two ends of runs were told: the first was acted on by the follow-up, the second has had no time to be.
    assert rows[("run_end", "")]["sent"] == 2
    assert (rows[("run_end", "")]["acted"], rows[("run_end", "")]["pending"]) == (1, 1)
    assert rows[("watcher", "wat_1")]["precision"] == 1.0

"""A chat whose dock has everything to show (item 1383), served by a real daemon in a throw-away home: a run at work
that waits on two helpers, with a message in its queue, a loop and a command in the background; the same chat asking
for an approval; and a chat at rest. The browser tests of the dock and the screenshots of its pull request share it.

No network and no model service is used: the scripted model plays the run and its helpers, which work in a shell
command each (the model is asked again only when a command ends, which nothing here lets happen).
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from playwright.async_api import Locator, Page, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import NO_PICKS, RunRecord
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

TOKEN: str = "dock-scenario-token"
WAIT_S: float = 30.0
PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
GOAL: str = "Ship the dictation work"
JOBS: tuple[str, str] = ("Build direct keyboard Ask", "Particle dictation animation fix")
QUEUED: str = "Then write a short summary of what changed."
LOOP_EVERY: str = "10m"
LOOP_MESSAGE: str = "check the build"
# A command with a long path, as a run leaves it in the background: it is cut at a line's end on a Mac and wraps in a
# phone's sheet.
BACKGROUND_COMMAND: str = "cd /tmp && sleep 600  # /Users/someone/Desktop/Project/.claude/worktrees/feature-branch"
# A command that waits for the owner's approval under the cautious preset.
ASKING_COMMAND: str = "touch " + " ".join(f"shelf-{number:03}.txt" for number in range(40))

Kind = Literal["work", "ask", "rest"]
# The widest window that gets a phone's dock: one capsule over the composer, its panes in sheets (css/run.css).
PHONE_WIDTH: int = 720


def is_phone(page: Page) -> bool:
    """Whether the window of ``page`` is as narrow as a phone's, which draws no pane over the composer."""
    size = page.viewport_size
    return size is not None and size["width"] <= PHONE_WIDTH


async def open_sheet(page: Page, segment: str) -> Locator:
    """On a phone's width the queue, the loops and the commands at work are shown in the sheet their segment of the
    capsule opens: open it, and return it. A wider window has them over the composer, and nothing is opened there."""
    await page.locator(f'[data-segment="{segment}"]').click()
    sheet: Locator = page.locator(".dock-sheet:not([hidden])")
    await expect(sheet).to_have_count(1)
    return sheet


class _Routing(ScriptedProvider):
    """The run delegates the two jobs; each helper then waits in a shell command."""

    def __init__(self) -> None:
        super().__init__(PROVIDER, [])
        calls: tuple[ScriptedCall, ...] = tuple(
            ScriptedCall(tool=DELEGATE_TOOL, arguments={"goal": job}) for job in JOBS
        )
        self._lead: ScriptedProvider = ScriptedProvider(PROVIDER, [ScriptedTurn(text="", calls=calls)])
        sleeping: ScriptedTurn = ScriptedTurn(
            text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 300"}),)
        )
        self._helper: ScriptedProvider = ScriptedProvider(PROVIDER, [sleeping] * 2)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        # A helper's own goal is the second message of its conversation; the run's is the owner's text.
        own: str = repr(request.messages[1]) if len(request.messages) > 1 else ""
        if any(job in own for job in JOBS):
            return await self._helper.complete(request, sink)
        return await self._lead.complete(request, sink)


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


@dataclass(frozen=True)
class Scene:
    """What a scene serves: the daemon's runtime and manager, its address and token, the chat's run and its helpers."""

    runtime: Runtime
    manager: RunManager
    address: str
    token: str
    run: RunRecord
    helpers: tuple[RunRecord, ...]
    server: DotServer
    stopping: asyncio.Event

    @property
    def port(self) -> int:
        return int(self.address.rpartition(":")[2])

    def stop_serving(self) -> None:
        """The daemon stops serving, as one that is stopped or crashed does: the pages lose their streams."""
        self.server.should_exit = True
        self.stopping.set()


async def _both_working(runtime: Runtime, parent_id: str) -> tuple[RunRecord, ...]:
    """The helpers of ``parent_id`` once both of them have made their call."""
    deadline: float = time.monotonic() + WAIT_S
    while True:
        helpers: list[RunRecord] = [r for r in await runtime.store.list_runs(20, None) if r.parent_run_id == parent_id]
        if len(helpers) == len(JOBS):
            calls: list[int] = [len(await runtime.store.events_of_kind(r.id, "tool_call", 1)) for r in helpers]
            if all(calls):
                return tuple(helpers)
        assert time.monotonic() < deadline, f"the helpers stand as {[r.status for r in helpers]} after {WAIT_S} s"
        await asyncio.sleep(0.1)


async def _asking(runtime: Runtime, run: RunRecord) -> None:
    """Wait until ``run`` has asked for an approval."""
    deadline: float = time.monotonic() + WAIT_S
    while not [record for record in await runtime.store.list_approvals("pending", 20) if record.run_id == run.id]:
        assert time.monotonic() < deadline, f"the run {run.id} asked nothing in {WAIT_S} s"
        await asyncio.sleep(0.05)


async def _extras(
    runtime: Runtime, manager: RunManager, run: RunRecord, address: str, home: Path, advertise: int | None
) -> None:
    """A message in the chat's queue, a loop of the chat and a command it left in the background."""
    await manager.queue(
        run.thread_id,
        QUEUED,
        (),
        "task",
        SCRIPTED_MODEL,
        None,
        "standard",
        None,
        NO_PICKS,
        "ask",
        False,
        None,
        UI_SOURCE,
    )
    # Behind a proxy the daemon answers to the proxy's port by name, so that is the name the request carries.
    named: dict[str, str] = {} if advertise is None else {"Host": f"127.0.0.1:{advertise}"}
    async with httpx.AsyncClient(
        base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}", **named}, timeout=30.0
    ) as client:
        made: httpx.Response = await client.post(
            f"/runs/{run.id}/loop", json={"every": LOOP_EVERY, "text": LOOP_MESSAGE}
        )
        assert made.status_code == 201, made.text
    await runtime.terminals.open(run.thread_id, BACKGROUND_COMMAND, (), home, True, run.id)


@asynccontextmanager
async def scene(login_env: dict[str, str], kind: Kind, advertise: int | None = None) -> AsyncIterator[Scene]:
    """A daemon serving the scene ``kind``: ``work`` (two helpers waited on, a queued message, a loop, a command in the
    background), ``ask`` (the run waits for an approval, with the same queue, loop and command) or ``rest`` (a chat
    whose run is done). ``advertise`` is the port the daemon answers to by name when a proxy stands in front of it (the
    daemon refuses a request that names another port than its own)."""
    config: AppConfig = eval_config(load_default_config())
    if kind == "ask":
        config = config.model_copy(update={"policy": config.policy.model_copy(update={"preset": "cautious"})})
    model: ScriptedProvider = (
        _Routing()
        if kind == "work"
        else ScriptedProvider(
            PROVIDER,
            [
                ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": ASKING_COMMAND}),))
                if kind == "ask"
                else _finish("Done."),
                _finish("Done."),
            ],
        )
    )
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            stopping: asyncio.Event = asyncio.Event()
            listener = bind_listener("127.0.0.1", 0)
            port: int = listener.getsockname()[1]
            context: ServerContext = ServerContext(
                runtime=runtime,
                manager=manager,
                scheduler=None,
                token=TOKEN,
                host="127.0.0.1",
                port=port if advertise is None else advertise,
                started_at=time.time(),
                stopping=stopping,
            )
            server: DotServer = DotServer(serving_config(create_app(context)), stopping)
            serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started:
                assert not serving.done(), "the server stopped while starting"
                await asyncio.sleep(0.05)
            address: str = f"http://127.0.0.1:{port}"
            try:
                run: RunRecord = await manager.start(
                    GOAL if kind != "ask" else "Write the shelves",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    UI_SOURCE,
                    None,
                    None,
                    "standard",
                    None,
                    "ask",
                    False,
                    (),
                )
                helpers: tuple[RunRecord, ...] = ()
                if kind == "work":
                    helpers = await _both_working(runtime, run.id)
                    await _extras(runtime, manager, run, address, home, advertise)
                elif kind == "ask":
                    await _asking(runtime, run)
                    await _extras(runtime, manager, run, address, home, advertise)
                else:
                    async with asyncio.timeout(WAIT_S):
                        await manager.wait(run.id)
                yield Scene(runtime, manager, address, TOKEN, run, helpers, server, stopping)
            finally:
                await runtime.terminals.close_all()
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def read_login() -> dict[str, str]:
    """The login environment a daemon of the scenes opens with."""
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())

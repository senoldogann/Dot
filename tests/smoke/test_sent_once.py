"""A message the pages send once (item 189). Each new message of the pages carries a key of its own; the message sent
again with it, after an answer that was lost, gets what its first sending made (the run it started with its chat, its
place in a queue, its steer) instead of a second one. A key whose sending failed is free again, and one whose sending is
under way is refused. The page itself sends the same key again after an answer that was lost, wide and on a phone,
light and dark.

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import APIResponse, Browser, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.core.paths import workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Wait for the build."
SUMMARY: str = "Then write the summary."
NUDGE: str = "Use the nightly build."
TOKEN: str = "sent-once-test-token"
WAIT_S: float = 30.0
GOAL_BOX: str = "textarea[aria-label='Goal']"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Runs until the test creates the file ``stop`` in the workspace, at most 30 seconds.
_WAIT_COMMAND: str = "for i in $(seq 300); do [ -e stop ] && break; sleep 0.1; done"
_WAITING: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The build is done.", "success": True}),)
)
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], turns: list[ScriptedTurn]
) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, whose model plays ``turns``, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
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
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            await asyncio.to_thread(_let_the_command_end)
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


@asynccontextmanager
async def _api(address: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=WAIT_S
    ) as client:
        yield client


def _let_the_command_end() -> None:
    workspace: Path = workspace_dir()
    if workspace.is_dir():
        (workspace / "stop").touch()


def _new_run(goal: str, key: str) -> dict[str, str | None]:
    return {"goal": goal, "model": SCRIPTED_MODEL, "approvals": "yolo", "thread_id": None, "key": key}


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run_id)


async def _until_called(runtime: Runtime, run_id: str) -> None:
    """Wait until the run called its tool: it is at work on it then, and takes a steer."""
    deadline: float = time.monotonic() + WAIT_S
    while not await runtime.store.events_of_kind(run_id, "tool_call", 1):
        assert time.monotonic() < deadline, f"the run {run_id} called no tool in {WAIT_S} s"
        await asyncio.sleep(0.05)


async def _until_the_queue_started(runtime: Runtime, thread_id: str) -> list[RunRecord]:
    """Wait until the chat's queue is empty and its message started a run after the first; the chat's runs, newest
    first."""
    deadline: float = time.monotonic() + WAIT_S
    while len(runs := await runtime.store.list_runs(10, thread_id)) < 2 or await runtime.store.list_queued(thread_id):
        assert time.monotonic() < deadline, f"the queue of {thread_id} did not start in {WAIT_S} s"
        await asyncio.sleep(0.05)
    return runs


def _losing_the_first_answer(lost: list[str]) -> Callable[[Route], Awaitable[None]]:
    """A handler of the page's requests to ``/api/runs`` that lets the daemon start the first run and loses the answer
    on its way back, as a gateway between them that timed out does (the run's id goes to ``lost``); the requests after
    it go through. A connection reset instead is not lost for sure: the browser may send the request again itself."""

    async def handle(route: Route) -> None:
        if route.request.method != "POST" or lost:
            await route.continue_()
            return
        answer: APIResponse = await route.fetch()
        lost.append(str((await answer.json())["id"]))
        await route.fulfill(status=504, content_type="text/plain", body="The gateway timed out.")

    return handle


async def test_a_new_chat_sent_again_with_its_key_is_the_one_the_first_sending_started(
    login_env: dict[str, str],
) -> None:
    """Before, the second sending started a second run in a second chat."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, [_FINISH] * 4) as (runtime, manager, address), _api(address) as api:
            key: str = str(uuid.uuid4())
            first: httpx.Response = await api.post("/api/runs", json=_new_run(GOAL, key))
            again: httpx.Response = await api.post("/api/runs", json=_new_run(GOAL, key))
            assert (first.status_code, again.status_code) == (201, 201), again.text
            assert again.json()["id"] == first.json()["id"]
            assert (await _ended(manager, first.json()["id"])).status == "done"
            assert [run.id for run in await runtime.store.list_runs(10, None)] == [first.json()["id"]]
            assert len(await runtime.store.list_threads(10)) == 1
            # Another message is another key, and another chat.
            other: httpx.Response = await api.post("/api/runs", json=_new_run(GOAL, str(uuid.uuid4())))
            assert other.json()["id"] != first.json()["id"]
            await _ended(manager, other.json()["id"])


async def test_a_queued_message_and_a_steer_sent_again_with_their_keys_are_done_once(login_env: dict[str, str]) -> None:
    """Before, each sending again queued the message a second time and steered the run a second time."""
    with scratch_home() as home, eval_environment(home, {}):
        turns: list[ScriptedTurn] = [_WAITING, _FINISH, _FINISH, _FINISH]
        async with _daemon(login_env, turns) as (runtime, manager, address), _api(address) as api:
            started: httpx.Response = await api.post("/api/runs", json=_new_run(GOAL, str(uuid.uuid4())))
            run_id: str = started.json()["id"]
            thread_id: str = started.json()["thread_id"]
            await _until_called(runtime, run_id)
            queued: dict[str, str | None] = {"text": SUMMARY, "model": SCRIPTED_MODEL, "approvals": "yolo"}
            queue_key: str = str(uuid.uuid4())
            for _ in range(2):
                sent: httpx.Response = await api.post(
                    f"/api/threads/{thread_id}/queue", json={**queued, "key": queue_key}
                )
                assert sent.status_code == 201, sent.text
                assert [message["text"] for message in sent.json()["messages"]] == [SUMMARY]
            steer_key: str = str(uuid.uuid4())
            for _ in range(2):
                steered: httpx.Response = await api.post(
                    f"/api/runs/{run_id}/steer", json={"text": NUDGE, "key": steer_key}
                )
                assert steered.status_code == 202, steered.text
            await asyncio.to_thread(_let_the_command_end)
            assert (await _ended(manager, run_id)).status == "done"
            steers: list[str] = [
                str(event.data["text"]) for event in await runtime.store.events_of_kind(run_id, "steer", 10)
            ]
            assert steers == [NUDGE]
            runs: list[RunRecord] = await _until_the_queue_started(runtime, thread_id)
            assert [run.goal for run in runs] == [SUMMARY, GOAL]
            await _ended(manager, runs[0].id)


async def test_a_message_whose_sending_failed_is_sent_anew_and_one_under_way_is_refused(
    login_env: dict[str, str],
) -> None:
    """A sending that fails lets its key go; one under way, as in another tab, refuses the same key (409)."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, [_FINISH] * 4) as (runtime, manager, address), _api(address) as api:
            key: str = str(uuid.uuid4())
            failed: httpx.Response = await api.post(
                "/api/runs", json={**_new_run(GOAL, key), "thread_id": "thr_not_there"}
            )
            assert failed.status_code == 404, failed.text
            sent: httpx.Response = await api.post("/api/runs", json=_new_run(GOAL, key))
            assert sent.status_code == 201, sent.text
            await _ended(manager, sent.json()["id"])
            busy: str = str(uuid.uuid4())
            now: float = time.time()
            assert await runtime.store.claim_sent(f"run:{busy}", now, now - 120.0, now - 86_400.0) is None
            refused: httpx.Response = await api.post("/api/runs", json=_new_run(GOAL, busy))
            assert refused.status_code == 409, refused.text
            assert "being sent already" in refused.text
            assert len(await runtime.store.list_runs(10, None)) == 1


@pytest.mark.browser
async def test_the_page_sends_a_message_again_with_its_key_after_an_answer_that_was_lost(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """The first answer to the new chat is lost on its way to the page (a gateway timed out after the daemon started the
    run): the page says so and keeps the message; sent again, it opens the run that the first sending started. Before,
    a second run started in a second chat."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, [_FINISH] * 8) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    lost: list[str] = []
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{address}/#/runs")
                    await page.route("**/api/runs", _losing_the_first_answer(lost))
                    goal: Locator = page.locator(GOAL_BOX)
                    text: str = f"{GOAL} ({width}, {scheme})"
                    await goal.fill(text)
                    await goal.press("Enter")
                    await expect(page.locator(".toast.error")).to_be_visible()
                    await expect(goal).to_have_value(text)
                    await page.screenshot(path=str(tmp_path / f"sent-again-{width}-{scheme}.png"))
                    await goal.press("Enter")
                    await expect(page).to_have_url(f"{address}/#/runs/{lost[0]}")
                    await _ended(manager, lost[0])
                    assert [run.id for run in await runtime.store.list_runs(10, None) if run.goal == text] == lost
                    await page.close()
            finally:
                await browser.close()

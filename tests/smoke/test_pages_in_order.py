"""The pages a run shows in one step stand in the order of its calls, and a page that names a picture or a video by a
web address is refused with why, so that the owner never sees it empty (item 1721). The scripted model plays the run;
the store is slowed for the first pages, which ended last when the calls of a step ran at once."""

import asyncio
from collections.abc import Callable, Coroutine

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.pages import PAGE_EVENT
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import EventRecord, RunRecord, ShownPage
from dotagent.tools.pages import SHOW_PAGE_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 60.0
_TITLES: tuple[str, ...] = ("Part one", "Part two", "Part three")
# How long the store takes to keep each page: the first longest.
_SLOW_S: dict[str, float] = {"Part one": 0.4, "Part two": 0.2}
_REMOTE: str = "http://127.0.0.1:50899/final.mp4"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _slowed(keep: Callable[[str, str, str], Coroutine[None, None, ShownPage]]) -> Callable[..., object]:
    async def slow(run_id: str, title: str, html: str) -> ShownPage:
        await asyncio.sleep(_SLOW_S.get(title, 0.0))
        return await keep(run_id, title, html)

    return slow


async def test_pages_of_one_step_keep_the_call_order_and_a_web_video_is_refused(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pages: tuple[ScriptedCall, ...] = tuple(
        ScriptedCall(tool=SHOW_PAGE_TOOL, arguments={"title": title, "html": f"<p>{title}</p>"}) for title in _TITLES
    )
    remote: ScriptedCall = ScriptedCall(
        tool=SHOW_PAGE_TOOL, arguments={"title": "The video", "html": f'<video controls src="{_REMOTE}"></video>'}
    )
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text="", calls=pages),
        ScriptedTurn(text="", calls=(remote,)),
        ScriptedTurn(text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Shown.", "success": True}),)),
    ]
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
        monkeypatch.setattr(runtime.store, "add_shown_page", _slowed(runtime.store.add_shown_page))
        manager: RunManager = RunManager(runtime)
        try:
            run: RunRecord = await manager.start(
                "Show the parts.",
                "task",
                SCRIPTED_MODEL,
                None,
                UI_SOURCE,
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            async with asyncio.timeout(_WAIT_S):
                assert (await manager.wait(run.id)).status == "done"
            shown: list[EventRecord] = await runtime.store.events_of_kind(run.id, PAGE_EVENT, 10)
            assert [event.data["title"] for event in shown] == list(_TITLES)
            results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 10)
            refused: list[EventRecord] = [event for event in results if _REMOTE in str(event.data)]
            assert len(refused) == 1 and refused[0].data["ok"] is False, [event.data for event in results]
            assert "The page was not shown" in str(refused[0].data)
        finally:
            await manager.shutdown()
            await close_runtime(runtime)

"""What a run did that no undo can take back (item 1404), end to end on a scripted model, a real server and a real
headless Chrome. The undo journal holds `write_file`, `edit_file` and `write_notes` only, so the line at the head of
the side panel's Changes tab says, besides the files a run changed, how many steps it ran with a command, the browser,
the screen or a message: ``GET /api/runs/{id}/actions`` carries them in `outside`, a run that changed a file and ran a
command shows both on one card, one that only ran commands shows the sentence alone, and one that did neither shows
nothing. Wide and on a phone 390 px wide, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, async_playwright, expect
from test_run_changes_card import TOKEN, _line, _open

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_SENTENCE_TWO: str = "Dot also ran 2 commands. Undo takes back files only, so it cannot take these back."
_SENTENCE_ONE: str = "Dot also ran 1 command. Undo takes back files only, so it cannot take this back."


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _turn(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _script() -> list[ScriptedTurn]:
    """Three runs: one writes a file and runs a command, one runs two commands, one only reads."""
    done: dict[str, object] = {"answer": "Done.", "success": True}
    return [
        _turn("shell", {"command": "echo first"}),
        _turn("write_file", {"path": "made.txt", "content": "made\n"}),
        _turn(FINISH_TOOL, done),
        _turn("shell", {"command": "echo second"}),
        _turn("shell", {"command": "echo third"}),
        _turn(FINISH_TOOL, done),
        _turn("list_dir", {"path": "."}),
        _turn(FINISH_TOOL, done),
    ]


@pytest_asyncio.fixture(loop_scope="module")
async def served(login_env: dict[str, str]) -> AsyncIterator[tuple[str, RunRecord, RunRecord, RunRecord]]:
    """The server, the run that wrote a file and ran a command, the run that ran two commands and the run that read."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
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
                runs: list[RunRecord] = []
                for goal in ("Run a command and write a file.", "Run two commands.", "Look at the folder."):
                    run: RunRecord = await manager.start(
                        goal, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "yolo", False, ()
                    )
                    async with asyncio.timeout(_TIME_LIMIT_S):
                        assert (await manager.wait(run.id)).status == "done"
                    runs.append(run)
                yield f"http://127.0.0.1:{port}", runs[0], runs[1], runs[2]
            finally:
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def test_the_api_counts_the_steps_that_ran_outside_the_journal_and_none_of_a_run_that_only_read(
    served: tuple[str, RunRecord, RunRecord, RunRecord],
) -> None:
    address, mixed, commands, read = served
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        views: dict[str, dict[str, object]] = {}
        for name, run in (("mixed", mixed), ("commands", commands), ("read", read)):
            answer: httpx.Response = await client.get(f"/api/runs/{run.id}/actions")
            assert answer.status_code == 200, answer.text
            views[name] = answer.json()
    assert views["mixed"]["outside"] == [{"category": "shell", "steps": 1}]
    assert views["commands"]["outside"] == [{"category": "shell", "steps": 2}]
    assert views["read"]["outside"] == []
    # The file the first run wrote is in the journal, so its undo plan has it; the commands are not in any plan.
    assert [edit["tool"] for edit in views["mixed"]["edits"]] == ["write_file"]  # type: ignore[attr-defined]
    assert views["commands"]["edits"] == []
    # What the runs did by their record (item 1746): the file written, the commands run, no act and no plan.
    mixed_account: object = views["mixed"]["account"]
    read_account: object = views["read"]["account"]
    assert isinstance(mixed_account, dict) and isinstance(read_account, dict)
    assert (mixed_account["changed_count"], mixed_account["commands"], mixed_account["sent_count"]) == (1, 1, 0)
    assert mixed_account["open_steps"] == [] and mixed_account["unshown"] == []
    assert read_account["commands"] == 0
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        detail: httpx.Response = await client.get(f"/api/runs/{commands.id}")
    assert detail.json()["account"]["commands"] == 2


async def test_the_card_says_what_ran_outside_the_journal_beside_the_files_and_alone_at_1280_and_390(
    served: tuple[str, RunRecord, RunRecord, RunRecord], tmp_path: Path
) -> None:
    address, mixed, commands, read = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for width, scheme in ((1280, "light"), (390, "dark")):
                page, errors = await _open(browser, address, mixed.id, width, scheme)
                card: Locator = await _line(page)
                await expect(card.locator(".run-changes-words")).to_have_text("Created a file")
                await expect(card.locator("[data-outside]")).to_have_text(_SENTENCE_ONE)
                # The card stays inside the window, the sentence wrapping under the files.
                box = await card.bounding_box()
                assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
                await page.screenshot(path=str(tmp_path / f"outside-with-files-{width}-{scheme}.png"))
                assert errors == []
                await page.context.close()
                alone, alone_errors = await _open(browser, address, commands.id, width, scheme)
                line: Locator = await _line(alone)
                await expect(line.locator("[data-outside]")).to_have_text(_SENTENCE_TWO)
                await expect(line.locator(".run-changes-words")).to_have_count(0)
                await expect(line.get_by_role("button", name="Undo")).to_have_count(0)
                await alone.screenshot(path=str(tmp_path / f"outside-alone-{width}-{scheme}.png"))
                assert alone_errors == []
                await alone.context.close()
                none, none_errors = await _open(browser, address, read.id, width, scheme)
                await expect(none.locator(".answer").last.locator(".answer-foot")).to_be_visible()
                await expect(none.locator(".run-changes")).to_be_hidden()
                assert none_errors == []
                await none.context.close()
        finally:
            await browser.close()

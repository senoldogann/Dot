"""The switch of the sandbox of a run's commands (item 262), on a daemon served by a real server: the API says the
sandbox is on, a shell command of a run that is not yolo is refused Dot's own key file; switched off, the next command
of the same run reads it, the config file keeps ``[shell] sandbox = false``, and switched on again the command is
refused again, all without a restart. A daemon whose sandbox program does not start refuses to switch it on and stays
as it was. The Rules page in Chrome shows the switch and moves it, wide and on a phone, light and dark. Before, the
sandbox was chosen by the config file alone, read at start.

No network and no model service is used.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

import dotagent.policy.sandbox
from dotagent.agent.gate import RunGate
from dotagent.agent.manager import RunManager
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import vapid_path, workspace_dir
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall
from dotagent.tools.context import ToolContext
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "sandbox-switch-test-token"
GOAL: str = "Read a file."
REFUSED: str = "operation not permitted"
# The first line of the daemon's web push key, which it writes at its start into its home: one of Dot's own files.
KEY_HEAD: str = "-----BEGIN"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
SWITCH: str = "Keep Dot's commands in a safe box"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str], sandbox: bool) -> AsyncIterator[tuple[Runtime, str]]:
    """The daemon in the home of the environment, its sandbox on or off at start, served at the address it yields."""
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(update={"shell": base.shell.model_copy(update={"sandbox": sandbox})})
    runtime: Runtime = await open_runtime(config, login_env)
    try:
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
            yield runtime, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _auto_run(runtime: Runtime) -> ToolContext:
    """The tools of a task run whose questions go to the review model: a run that is not yolo."""
    thread: ThreadRecord = await runtime.store.create_thread("sandbox switch", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        GOAL,
        "task",
        "ollama:none",
        None,
        "test",
        "medium",
        "standard",
        None,
        "auto",
        False,
        None,
        None,
        None,
    )
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals="auto",
        questions="leave",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            runtime.config.policy.spend,
            "auto",
            _nobody,
            (GOAL,),
            runtime.site_locks,
        ),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


async def _read_key(ctx: ToolContext) -> str:
    """What a shell command of the run printed when it read the daemon's key file, run as the executor runs it once the
    gate has let it through."""
    prepared: PreparedCall = await ctx.runtime.registry.tools["shell"].prepare(
        {"command": f"head -1 '{vapid_path()}'"}, ctx
    )
    return (await prepared.run(ctx)).text


async def test_the_sandbox_is_switched_off_and_on_through_the_api_and_the_next_command_follows(
    login_env: dict[str, str],
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, True) as (runtime, address):
            run: ToolContext = await _auto_run(runtime)
            async with httpx.AsyncClient(
                base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
            ) as client:
                assert (await client.get("/shell/sandbox")).json() == {"sandbox": True}
                sealed: str = await _read_key(run)
                assert REFUSED in sealed.casefold() and KEY_HEAD not in sealed
                off: httpx.Response = await client.put("/shell/sandbox", json={"sandbox": False})
                assert (off.status_code, off.json()) == (200, {"sandbox": False})
                assert "[shell]\nsandbox = false\n" in (home / "config.toml").read_text(encoding="utf-8")
                assert KEY_HEAD in await _read_key(run)
                on: httpx.Response = await client.put("/shell/sandbox", json={"sandbox": True})
                assert (on.status_code, on.json()) == (200, {"sandbox": True})
                assert "[shell]\nsandbox = true\n" in (home / "config.toml").read_text(encoding="utf-8")
                again: str = await _read_key(run)
                assert REFUSED in again.casefold() and KEY_HEAD not in again


async def test_a_sandbox_that_does_not_start_is_not_switched_on(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        monkeypatch.setattr(dotagent.policy.sandbox, "SANDBOX_EXEC", str(home / "no-sandbox-exec"))
        async with _daemon(login_env, False) as (runtime, address):
            run: ToolContext = await _auto_run(runtime)
            async with httpx.AsyncClient(
                base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
            ) as client:
                refused: httpx.Response = await client.put("/shell/sandbox", json={"sandbox": True})
                assert refused.status_code == 422 and "cannot start" in refused.text
                assert (await client.get("/shell/sandbox")).json() == {"sandbox": False}
                assert not (home / "config.toml").exists() or "[shell]" not in (home / "config.toml").read_text(
                    encoding="utf-8"
                )
                assert KEY_HEAD in await _read_key(run)


@pytest.mark.browser
async def test_the_rules_page_shows_the_switch_and_moves_it(login_env: dict[str, str], tmp_path: Path) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, True) as (runtime, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{address}/#/settings/rules")
                    # The switch is in Advanced, which is shut until it is asked for (item 622).
                    advanced: Locator = page.locator("details.rule-advanced")
                    await expect(advanced).not_to_have_attribute("open", "")
                    await expect(page.get_by_role("switch", name=SWITCH)).to_be_hidden()
                    await advanced.locator("> summary").click()
                    switch: Locator = page.get_by_role("switch", name=SWITCH)
                    await switch.wait_for()
                    await page.wait_for_function(
                        '(name) => { const s = document.querySelector(`[role=switch][aria-label="${name}"]`);'
                        " return s !== null && !s.disabled; }",
                        arg=SWITCH,
                    )
                    assert await switch.get_attribute("aria-checked") == "true"
                    card: Locator = advanced.locator(".rule-advanced-body")
                    assert "[shell] sandbox" in (await card.text_content() or "")
                    assert await card.evaluate("(card) => card.getBoundingClientRect().right <= window.innerWidth")
                    await page.screenshot(path=tmp_path / f"sandbox-switch-{width}-{scheme}-on.png")
                    await switch.click()
                    await page.wait_for_function(
                        '(name) => document.querySelector(`[role=switch][aria-label="${name}"]`)'
                        ".getAttribute('aria-checked') === 'false'",
                        arg=SWITCH,
                    )
                    assert runtime.sandbox.on is False
                    await page.screenshot(path=tmp_path / f"sandbox-switch-{width}-{scheme}-off.png")
                    await switch.click()
                    await page.wait_for_function(
                        '(name) => document.querySelector(`[role=switch][aria-label="${name}"]`)'
                        ".getAttribute('aria-checked') === 'true'",
                        arg=SWITCH,
                    )
                    assert runtime.sandbox.on is True
                    await page.close()
            finally:
                await browser.close()

"""A model of the owner's Claude plan in Dot's own work (item 1032): it may be the review model, every question of
which is a single one the plan answers (:func:`dotagent.claude_code.ask.question_of`); not the fast model, which takes a
run's steps, nor the vision model, which reads pictures, nor the model that looks for work on Dot's own initiative,
whose tools Claude Code's door does not give. Settings › Models chooses them through the daemon's routes.

The ``claude`` Dot finds is a script that does nothing, and Claude Code's state file says an account is signed in (no
secret in it): no call reaches a model."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "plan-roles-token"


def _lay_out(root: Path) -> tuple[Path, Path, Path]:
    """Dot's home with its config, the ``claude`` Dot finds (a script that does nothing) and Claude Code's state folder,
    whose state file says which account is signed in (no secret in it)."""
    home: Path = root / "home"
    home.mkdir()
    (home / "config.toml").write_text("[browser]\nheadless = true", encoding="utf-8")
    program: Path = root / "bin" / "claude"
    program.parent.mkdir()
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    program.chmod(0o755)
    state: Path = root / "claude-state"
    state.mkdir()
    (state / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "owner@example.org", "organizationName": "Owner"}}),
        encoding="utf-8",
    )
    return home, program, state


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[Runtime, str]]:
    home, program, state = _lay_out(tmp_path_factory.mktemp("plan-roles"))
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(home.parent / "chrome"))
    patch.setenv("DOT_CLAUDE", str(program))
    patch.setenv("CLAUDE_CONFIG_DIR", str(state))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
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
    try:
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        yield runtime, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def test_a_model_of_the_claude_plan_may_review_but_not_take_steps_read_pictures_or_look_for_work(
    served: tuple[Runtime, str],
) -> None:
    runtime, address = served
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        before: tuple[str, str, str] = (runtime.roles.fast(), runtime.roles.review(), runtime.roles.vision())
        for role in ("fast", "vision"):
            refused: httpx.Response = await http.post(
                "/api/models/roles", json={"role": role, "model": "claude_code:opus"}
            )
            assert refused.status_code == 422 and "answers only as Claude Code" in refused.text, refused.text
        assert (runtime.roles.fast(), runtime.roles.review(), runtime.roles.vision()) == before
        chosen: httpx.Response = await http.post(
            "/api/models/roles", json={"role": "review", "model": "claude_code:opus"}
        )
        assert chosen.status_code == 200, chosen.text
        assert runtime.roles.review() == "claude_code:opus"
        assert load_config(config_path()).models.review == "claude_code:opus"
        # The look for work is a run whose tools (its list, the owner's mail and calendar) Claude Code's door does not
        # give it: a model of the plan cannot look.
        refused_look: httpx.Response = await http.put(
            "/api/initiative/look-model", json={"model": "claude_code:sonnet"}
        )
        assert refused_look.status_code == 422 and "cannot look for work" in refused_look.text, refused_look.text
        assert runtime.roles.look() == ""

"""The real tool records a repository snapshot under its own run ID and the daemon reads it back."""

import asyncio
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import RunRecord


def _prepare(folder: Path) -> tuple[Path, str]:
    repo: Path = folder.resolve() / "repo"
    tree: Path = repo.parent / "fixture-tree"
    repo.mkdir()
    git_env: dict[str, str] = {
        "HOME": "/var/empty",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    for args in (
        ("init", "-b", "main"),
        ("-c", "user.name=Dot tests", "-c", "user.email=dot@example.test", "commit", "--allow-empty", "-m", "Initial"),
        ("worktree", "add", "-b", "fixture-task", str(tree)),
    ):
        subprocess.run(("/usr/bin/git", "-C", str(repo), *args), check=True, capture_output=True, env=git_env)
    base: str = subprocess.run(
        ("/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"), check=True, capture_output=True, text=True, env=git_env
    ).stdout.strip()
    return tree, base


@pytest.mark.asyncio
async def test_the_coding_work_tool_records_a_snapshot_for_only_its_run(tmp_path: Path) -> None:
    tree, base = await asyncio.to_thread(_prepare, tmp_path)
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), {})
        manager: RunManager = RunManager(runtime)
        try:
            provider: str = parse_model_ref(SCRIPTED_MODEL).provider
            finish: ScriptedTurn = ScriptedTurn(
                text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Recorded.", "success": True}),)
            )
            turns: list[ScriptedTurn] = [
                ScriptedTurn(text="", calls=(ScriptedCall(tool="use_tools", arguments={"group": "worktree"}),)),
                ScriptedTurn(
                    text="",
                    calls=(ScriptedCall(tool="track_coding_work", arguments={"worktree": str(tree), "base": base}),),
                ),
                finish,
                finish,
            ]
            await runtime.router.register(provider, ScriptedProvider(provider, turns))
            runs: list[RunRecord] = []
            for goal in ("Record this linked coding worktree", "Unrelated task"):
                run: RunRecord = await manager.start(
                    goal, "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "ask", False, ()
                )
                async with asyncio.timeout(30):
                    assert (await manager.wait(run.id)).status == "done"
                runs.append(run)
            token: str = "coding-work-fixture"
            context: ServerContext = ServerContext(
                runtime=runtime,
                manager=manager,
                scheduler=None,
                token=token,
                host="127.0.0.1",
                port=8765,
                started_at=time.time(),
                stopping=asyncio.Event(),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(context)),
                base_url="http://127.0.0.1:8765",
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                tracked = await client.get(f"/api/runs/{runs[0].id}/coding-work")
                assert tracked.status_code == 200
                assert tracked.json()["branch"] == "fixture-task"
                assert tracked.json()["base"] == base
                other = await client.get(f"/api/runs/{runs[1].id}/coding-work")
                assert other.status_code == 200 and other.json() is None
        finally:
            await manager.shutdown()
            await close_runtime(runtime)

"""The skills and MCP servers of the Mac's other agents (item 164), through the API of a real in-process server: a home
made here holds a skill of Claude Code, which Dot is set to read, and a server of Claude Desktop, whose environment
holds a key. ``GET /api/skills/elsewhere`` lists both by name, says which one Dot uses, and never the key.

No network and no model service is used.
"""

import asyncio
import json
import secrets
import time
from pathlib import Path

import httpx
import pytest

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, SkillsConfig, load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
KEY: str = f"key-{secrets.token_hex(12)}"
PORT: int = 8765


def _other_agents(home: Path) -> Path:
    """A Claude Code skill and a Claude Desktop server in ``home``; the folder of Claude Code's skills."""
    skills: Path = home / ".claude" / "skills"
    (skills / "pdf-tools").mkdir(parents=True)
    (skills / "pdf-tools" / "SKILL.md").write_text(
        "---\nname: pdf-tools\ndescription: Fill and merge PDF forms.\n---\nUse pdftk.\n", encoding="utf-8"
    )
    desktop: Path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(
        json.dumps({"mcpServers": {"notion": {"command": "npx", "args": ["-y", "notion-mcp"], "env": {"KEY": KEY}}}}),
        encoding="utf-8",
    )
    return skills


async def test_the_api_lists_what_other_agents_set_up_and_which_of_it_dot_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_skills: Path = _other_agents(tmp_path)
    with scratch_home() as home, eval_environment(home, {}):
        base: AppConfig = eval_config(load_default_config())
        config: AppConfig = base.model_copy(update={"skills": SkillsConfig(dirs=[str(claude_skills)])})
        runtime: Runtime = await open_runtime(config, {})
        try:
            manager: RunManager = RunManager(runtime)
            context: ServerContext = ServerContext(
                runtime=runtime,
                manager=manager,
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
                response: httpx.Response = await client.get("/api/skills/elsewhere")
            await manager.shutdown()
        finally:
            await close_runtime(runtime)
    assert response.status_code == 200, response.text
    found: dict[str, list[dict[str, object]]] = response.json()
    assert [(skill["agent"], skill["name"], skill["used"]) for skill in found["skills"]] == [
        ("Claude Code", "pdf-tools", True)
    ]
    assert [(server["agent"], server["name"], server["reaches"], server["used"]) for server in found["servers"]] == [
        ("Claude Desktop", "notion", "npx", False)
    ]
    assert KEY not in response.text

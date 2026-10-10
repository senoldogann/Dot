"""Skills and plugins end to end, on real folders in a throwaway Dot home: a plugin brings its MCP server and its skills,
the prompt lists the skills, the ``skill`` tool hands over the instructions and the files, and ``dotx skills list``
shows what was found and what was left out. Nothing is mocked."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.skills.catalog import load_skills
from dotagent.skills.types import SkillCatalog
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.usefixtures("environment")]

FIXTURE: Path = Path(__file__).resolve().parent.parent / "fixtures" / "mcp_fixture_server.py"
MODEL: str = "gpt-oss:20b-cloud"
CLI_TIMEOUT_S: float = 90.0
FENCE: re.Pattern[str] = re.compile(
    r'<skill_content nonce="(?P<nonce>[0-9a-f]+)" name="(?P<name>[^"]+)">\n(?P<body>.*)\n</skill_content nonce="(?P=nonce)">',
    re.DOTALL,
)
RELEASE_NOTES: str = """---
name: release-notes
description: "Write the release notes of a version from the merged pull requests.
  Use when asked for release notes or a changelog entry."
allowed-tools: shell web_fetch
---
# Release notes

1. Run `scripts/collect.sh <version>` to list the merged pull requests.
2. Group them by area; one line each, in the past tense.
"""
DEPLOY_SITE: str = """---
name: deploy-site
description: Deploy the marketing site. Use when asked to publish or deploy the site.
---
Run `acme deploy --site marketing` and wait for the green check.
"""


class _Recorder:
    """Stands in for the run gate: lets every action through."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@dataclass(frozen=True)
class CliOutcome:
    code: int
    out: str
    err: str


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_home(root: Path) -> Path:
    _write(root / "config.toml", f'[models]\nmain = "ollama:{MODEL}"\n')
    _write(root / "skills" / "release-notes" / "SKILL.md", RELEASE_NOTES)
    _write(root / "skills" / "release-notes" / "scripts" / "collect.sh", "#!/bin/sh\necho collected\n")
    _write(root / "skills" / "broken" / "SKILL.md", "# No frontmatter here\n")
    plugin: Path = root / "plugins" / "acme-tools"
    _write(
        plugin / ".claude-plugin" / "plugin.json",
        json.dumps({"name": "acme-tools", "version": "1.2.0", "description": "Acme's tools"}),
    )
    _write(plugin / "skills" / "deploy-site" / "SKILL.md", DEPLOY_SITE)
    servers: JsonObject = {
        "mcpServers": {"fixture": {"command": sys.executable, "args": ["${CLAUDE_PLUGIN_ROOT}/server.py"]}}
    }
    _write(plugin / ".mcp.json", json.dumps(servers))
    shutil.copy(FIXTURE, plugin / "server.py")
    _write(root / "plugins" / "not-a-plugin" / "README.md", "just a folder\n")
    return root


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_home(tmp_path_factory.mktemp("home"))


@pytest.fixture(scope="module")
def environment(home: Path) -> Iterator[None]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(home / "logs"))
    yield
    patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path, environment: None) -> AsyncIterator[Runtime]:
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[ToolResult, ActionInfo]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="skills-test",
        thread_id="skills-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    return await prepared.run(ctx), prepared.action


async def _dotx(home: Path, arguments: list[str]) -> CliOutcome:
    environment: dict[str, str] = {**os.environ, "DOT_HOME": str(home), "DOT_LOG_DIR": str(home / "logs")}
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "dotagent.cli",
        *arguments,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), CLI_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    assert process.returncode is not None
    return CliOutcome(code=process.returncode, out=out.decode(), err=err.decode())


async def test_a_plugin_brings_its_mcp_server_into_the_registry(runtime: Runtime, home: Path) -> None:
    assert [plugin.name for plugin in runtime.plugins.plugins] == ["acme-tools"]
    assert [server.name for server in runtime.plugins.servers()] == ["acme-tools-fixture"]
    assert [problem.path for problem in runtime.plugins.problems] == [home / "plugins" / "not-a-plugin"]
    assert "mcp__acme-tools-fixture__add" in runtime.registry.tools
    result, _ = await _call(runtime, "mcp__acme-tools-fixture__add", {"a": 1, "b": 1})
    assert not result.is_error, result.text
    assert 'server="acme-tools-fixture"' in result.text and "\n2\n" in result.text


async def test_skills_are_found_in_the_home_and_in_the_plugins(runtime: Runtime, home: Path) -> None:
    catalog: SkillCatalog = load_skills(runtime.config.skills, runtime.plugins.plugins, None)
    assert [(skill.name, skill.source) for skill in catalog.skills] == [
        ("release-notes", "home"),
        ("deploy-site", "plugin acme-tools"),
    ]
    (problem,) = catalog.problems
    assert problem.path == home / "skills" / "broken" / "SKILL.md"
    assert "does not start with the --- line" in problem.reason


async def test_the_prompt_lists_the_skills_and_tells_the_model_to_read_one_first(runtime: Runtime) -> None:
    catalog: SkillCatalog = load_skills(runtime.config.skills, runtime.plugins.plugins, None)
    prompt: str = build_system_prompt(
        collect_prompt_inputs(
            "task",
            "cli",
            "aggressive",
            "ask",
            "wait",
            False,
            runtime.workspace,
            [],
            list(runtime.registry.tools),
            catalog.skills,
            None,
            [],
        )
    )
    assert "# Skills" in prompt
    assert "- release-notes: Write the release notes of a version from the merged pull requests. Use when" in prompt
    assert "- deploy-site: Deploy the marketing site." in prompt
    assert "call skill with its name before you start" in prompt


async def test_the_skill_tool_hands_over_the_instructions_and_the_files(runtime: Runtime, home: Path) -> None:
    result, action = await _call(runtime, "skill", {"name": "release-notes"})
    assert not result.is_error, result.text
    assert not result.untrusted
    assert (action.kind, action.risk, action.summary) == ("meta", "safe", "Read skill release-notes")
    found: re.Match[str] | None = FENCE.search(result.text)
    assert found is not None, result.text
    assert found["name"] == "release-notes"
    assert found["body"].startswith("# Release notes")
    assert "Run `scripts/collect.sh <version>`" in found["body"]
    root: Path = home / "skills" / "release-notes"
    assert str(root) in result.text
    assert result.text.rstrip().endswith("scripts/collect.sh")


async def test_an_unknown_skill_is_refused_with_the_installed_names(runtime: Runtime) -> None:
    result, _ = await _call(runtime, "skill", {"name": "nope"})
    assert result.is_error
    assert "No skill is called nope" in result.text
    assert "release-notes" in result.text and "deploy-site" in result.text


async def test_dotx_skills_list_shows_everything_and_what_was_left_out(home: Path) -> None:
    outcome: CliOutcome = await _dotx(home, ["skills", "list"])
    assert outcome.code == 1, outcome
    assert "Plugins (1):" in outcome.out
    assert "acme-tools 1.2.0  skill folders: 1, MCP servers: acme-tools-fixture" in outcome.out
    assert "Skills (2):" in outcome.out
    assert "release-notes  [home]" in outcome.out
    assert "deploy-site  [plugin acme-tools]" in outcome.out
    assert "Left out:" in outcome.out
    assert str(home / "plugins" / "not-a-plugin") in outcome.out
    assert str(home / "skills" / "broken" / "SKILL.md") in outcome.out

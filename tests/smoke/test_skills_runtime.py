"""Skills and plugins end to end, on real folders in a throwaway Dot home: a plugin brings its MCP server and its
skills, the prompt lists the skills, the ``skill`` tool hands over the instructions and the files of a skill the run
is offered and of no other (item 168), a run reads and runs them but changes what Dot loads only with the file tools
and once the user agrees, never with a command line (items 135 and 398), and ``dotx skills list`` shows what was found
and what was left out. Nothing is mocked."""

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

from dotagent.agent.playbooks import Playbook, playbook_name, write_playbook
from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.agent.review import PlaybookDraft
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.paths import playbooks_dir, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.harness.components import NO_HARNESS
from dotagent.policy.engine import auto_review, decide, preset_verdict
from dotagent.policy.types import ActionInfo, Decision
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
    r'<skill_content nonce="(?P<nonce>[0-9a-f]+)" name="(?P<name>[^"]+)">\n(?P<body>.*)\n</skill_content '
    r'nonce="(?P=nonce)">',
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
SCOUT: str = "agent-scout"
LEARNED_KIND: str = "Check a claim against the ministry register"
LEARNED: str = playbook_name(LEARNED_KIND)
LEARNED_STEP: str = "Search the register for the claim and quote its entry."
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


def _context(runtime: Runtime, agent_id: str | None) -> ToolContext:
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="skills-test",
        agent_id=agent_id,
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


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[ToolResult, ActionInfo]:
    return await _call_as(runtime, None, name, arguments)


async def _call_as(
    runtime: Runtime, agent_id: str | None, name: str, arguments: JsonObject
) -> tuple[ToolResult, ActionInfo]:
    """Call a tool in a run of the agent ``agent_id`` (``None`` for Dot)."""
    ctx: ToolContext = _context(runtime, agent_id)
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    return await prepared.run(ctx), prepared.action


async def _action(runtime: Runtime, name: str, arguments: JsonObject) -> ActionInfo:
    """What the policy is shown for the call; nothing runs."""
    return (await runtime.registry.tools[name].prepare(arguments, _context(runtime, None))).action


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
    # The skills that come with Dot are read last (item 196).
    assert [(skill.name, skill.source) for skill in catalog.skills] == [
        ("release-notes", "home"),
        ("deploy-site", "plugin acme-tools"),
        ("excel", "built-in"),
        ("onedrive", "built-in"),
        ("word", "built-in"),
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
            False,
            runtime.workspace,
            [],
            0,
            list(runtime.registry.tools),
            catalog.skills,
            0,
            None,
            [],
            None,
            (),
            (),
            None,
            "full",
            NO_HARNESS,
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


async def test_a_run_reads_and_runs_a_skill_and_changes_what_dot_loads_only_once_the_user_agrees(
    runtime: Runtime, home: Path
) -> None:
    skill: Path = home / "skills" / "release-notes"
    plugin: Path = home / "plugins" / "acme-tools"
    reads: list[tuple[str, JsonObject]] = [
        ("read_file", {"path": str(skill / "SKILL.md")}),
        ("read_file", {"path": str(plugin / ".mcp.json")}),
        ("shell", {"command": f"sh {skill / 'scripts' / 'collect.sh'}"}),
    ]
    # The file tools ask the user first (item 398): a question no rule and no review model answers in the user's place.
    asked: list[tuple[str, JsonObject]] = [
        ("write_file", {"path": str(plugin / ".mcp.json"), "content": "{}"}),
        ("write_file", {"path": str(home / "plugins" / "new" / ".mcp.json"), "content": "{}"}),
        ("edit_file", {"path": str(skill / "SKILL.md"), "old": "# Release notes", "new": "# Notes"}),
        ("write_file", {"path": str(home / "playbooks" / "learned-notes" / "SKILL.md"), "content": "x"}),
        ("write_file", {"path": str(home / "plugins-data" / "acme-tools" / "state.json"), "content": "{}"}),
    ]
    # A command line never changes them.
    refused_lines: list[tuple[str, JsonObject]] = [
        ("shell", {"command": f"rm -f {home / 'STOP'}"}),
        ("shell", {"command": f"echo '{{}}' > {plugin / '.mcp.json'}"}),
    ]
    for name, arguments in reads:
        action: ActionInfo = await _action(runtime, name, arguments)
        assert auto_review(action) is None and preset_verdict("aggressive", action)[0] == "allow", action
    for name, arguments in asked:
        action = await _action(runtime, name, arguments)
        decision: Decision = decide(action, "task", False, "aggressive", [], None)
        assert (decision.verdict, decision.source) == ("ask", "own_files"), action
    for name, arguments in refused_lines:
        action = await _action(runtime, name, arguments)
        refused: Decision | None = auto_review(action)
        assert refused is not None and refused.verdict == "deny", action
    assert "what Dot loads and runs" in (await _action(runtime, *asked[0])).summary


async def test_an_unknown_skill_is_refused_with_the_names_of_the_skills_offered(runtime: Runtime) -> None:
    result, _ = await _call(runtime, "skill", {"name": "nope"})
    assert result.is_error
    assert "No skill offered to this run is called nope" in result.text
    assert "release-notes" in result.text and "deploy-site" in result.text


async def test_a_long_skill_is_read_in_parts_that_each_fit_a_result(runtime: Runtime, home: Path) -> None:
    # Instructions longer than a tool result come in parts, each within the result, so the cut every result gets never
    # takes their middle (item 176); the files come with the first part.
    steps: str = "\n".join(
        f"{number}. Check line {number} of the ledger against the bank statement." for number in range(1, 1200)
    )
    root: Path = home / "skills" / "long-ledger"
    _write(
        root / "SKILL.md", f"---\nname: long-ledger\ndescription: Reconcile the ledger line by line.\n---\n{steps}\n"
    )
    _write(root / "scripts" / "fetch.sh", "#!/bin/sh\necho fetched\n")
    room: int = runtime.config.agent.tool_output_chars
    try:
        catalog: SkillCatalog = load_skills(runtime.config.skills, runtime.plugins.plugins, None)
        (skill,) = [entry for entry in catalog.skills if entry.name == "long-ledger"]
        assert len(skill.body) > 3 * room
        parts: list[str] = []
        texts: list[str] = []
        arguments: JsonObject = {"name": "long-ledger"}
        while True:
            result, _ = await _call(runtime, "skill", arguments)
            assert not result.is_error, result.text
            assert len(result.text) <= room, len(result.text)
            found: re.Match[str] | None = FENCE.search(result.text)
            assert found is not None, result.text
            parts.append(found["body"])
            texts.append(result.text)
            going_on: re.Match[str] | None = re.search(r"skill\(name='long-ledger', start=(\d+)\)", result.text)
            if going_on is None:
                break
            arguments = {"name": "long-ledger", "start": int(going_on[1])}
        assert "".join(parts) == skill.body
        assert "scripts/fetch.sh" in texts[0] and not any("scripts/fetch.sh" in text for text in texts[1:])
        past, _ = await _call(runtime, "skill", {"name": "long-ledger", "start": len(skill.body)})
        assert past.is_error and f"end at character {len(skill.body)}" in past.text, past.text
    finally:
        await asyncio.to_thread(shutil.rmtree, root)


def _learned(agent_id: str) -> Playbook:
    """A learned skill of the agent ``agent_id``, as a run of its would have written it."""
    return Playbook(
        name=LEARNED,
        kind=LEARNED_KIND,
        host="register.example.gov",
        draft=PlaybookDraft(
            kind=LEARNED_KIND,
            description="Look a claim up in the ministry register and quote the entry.",
            when="A goal that asks to check a claim against the register.",
            steps=("Open the register's search.", LEARNED_STEP),
            parameters=("<claim>: the claim to check",),
        ),
        run_id="run-earlier",
        agent_id=agent_id,
        goal="Check the claim",
        written_at=1_760_000_000.0,
        acting_steps=4,
        tainted=False,
    )


async def test_a_run_reads_only_the_skills_it_is_offered_and_hears_of_no_other(runtime: Runtime) -> None:
    # A learned skill of an agent is offered to that agent's runs alone (item 94): another run can neither read it by
    # its name nor find its name among the skills a refusal lists (item 168).
    written: Path = await asyncio.to_thread(write_playbook, playbooks_dir(), _learned(SCOUT))
    try:
        own, _ = await _call_as(runtime, SCOUT, "skill", {"name": LEARNED})
        assert not own.is_error and LEARNED_STEP in own.text, own.text
        refused, _ = await _call_as(runtime, None, "skill", {"name": LEARNED})
        assert refused.is_error and LEARNED_STEP not in refused.text, refused.text
        assert f"No skill offered to this run is called {LEARNED}" in refused.text, refused.text
        unknown, _ = await _call_as(runtime, None, "skill", {"name": "nope"})
        assert "release-notes" in unknown.text and LEARNED not in unknown.text, unknown.text
    finally:
        await asyncio.to_thread(shutil.rmtree, written.parent)


async def test_dotx_skills_list_shows_everything_and_what_was_left_out(home: Path) -> None:
    outcome: CliOutcome = await _dotx(home, ["skills", "list"])
    assert outcome.code == 1, outcome
    assert "Plugins (1):" in outcome.out
    assert "acme-tools 1.2.0  skill folders: 1, MCP servers: acme-tools-fixture" in outcome.out
    assert "Skills (5):" in outcome.out
    assert "release-notes  [home]" in outcome.out
    assert "deploy-site  [plugin acme-tools]" in outcome.out
    assert "excel  [built-in]" in outcome.out
    assert "Left out:" in outcome.out
    assert str(home / "plugins" / "not-a-plugin") in outcome.out
    assert str(home / "skills" / "broken" / "SKILL.md") in outcome.out

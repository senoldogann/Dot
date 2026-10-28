"""The choice tasks of the evals through the real loop, the real tools and real stand-in MCP servers (item 659): the
reference of each passes, the prompt of its run says what each server is for in the one way the task gives, the same
task played with the lines of the groups left bare is what a live run is measured against, and a run that picks wrong
(a decoy's group, a decoy's skill, no skill at all, an answer from memory) fails the check that tells.

No network and no model service is used: the scripted model plays the runs. A model that reads the prompt is the
live run of ``docs/development.md``, and costs tokens, so no test makes it.
"""

import pytest
import pytest_asyncio
from pydantic import JsonValue

from dotagent.config import AppConfig, load_default_config
from dotagent.evals import runner
from dotagent.evals.catalog import select_tasks
from dotagent.evals.results import TaskOutcome
from dotagent.evals.runner import eval_config, replay_reference
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import EvalTask, ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.tools.meta import FINISH_TOOL, USE_TOOLS_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_RULE: str = 'Words in quotes after "describes itself as" are what that MCP server says of itself'
CHOICE_IDS: tuple[str, ...] = (
    "changelog-skill-among-decoys",
    "tweet-skill-among-many",
    "rates-server-by-its-words",
    "timetable-server-by-owners-words",
    "forecast-server-by-its-tools",
)


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return eval_config(load_default_config())


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env(config: AppConfig) -> dict[str, str]:
    return await read_login_env(config.shell.login_env_timeout_s, ())


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> list[ChatRequest]:
    """Every request the scripted model of the attempts is sent, in order."""
    seen: list[ChatRequest] = []

    class Recording(ScriptedProvider):
        async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
            seen.append(request)
            return await super().complete(request, sink)

    monkeypatch.setattr(runner, "ScriptedProvider", Recording)
    return seen


def _bare(config: AppConfig) -> AppConfig:
    return config.model_copy(update={"agent": config.agent.model_copy(update={"mcp_purposes": False})})


def _turn(tool: str, **arguments: JsonValue) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=dict(arguments)),))


def _with(task_id: str, reference: list[ScriptedTurn]) -> EvalTask:
    return select_tasks([task_id])[0].model_copy(update={"reference": tuple(reference)})


def _finish(answer: str) -> ScriptedTurn:
    return _turn(FINISH_TOOL, answer=answer, success=True)


def _failed(outcome: TaskOutcome) -> list[str]:
    """What did not hold in the attempt."""
    return [check.check for check in outcome.checks if not check.passed]


def _lines(prompt: str) -> list[str]:
    """The lines of the groups of the MCP servers in the prompt."""
    return [line for line in prompt.split("\n") if line.startswith("- mcp__")]


@pytest.mark.parametrize("task_id", CHOICE_IDS)
async def test_the_reference_of_each_choice_task_is_passed_through_the_real_loop_with_nothing_failing_on_the_way(
    task_id: str, config: AppConfig, login_env: dict[str, str]
) -> None:
    outcome: TaskOutcome = await replay_reference(select_tasks([task_id])[0], config, login_env)
    assert outcome.passed, outcome
    assert (outcome.status, outcome.metrics.steps, outcome.metrics.tool_errors) == ("done", 3, 0)
    assert outcome.metrics.help_calls == 0 and outcome.metrics.unanswered_questions == 0


_RATES_LINES: list[str] = [
    "- mcp__ab-tools: the tools of the MCP server ab-tools, which describes itself as \"The bank's own exchange "
    'rates: the fixed rate of each currency pair on each day of March 2026." (mcp__ab-tools__lookup)',
    '- mcp__zq-kit: the tools of the MCP server zq-kit, which describes itself as "Converts lengths, weights and '
    'temperatures between metric and imperial units." (mcp__zq-kit__convert)',
]
_TIMETABLE_LINES: list[str] = [
    "- mcp__kb-desk: the tools of the MCP server kb-desk: the school's timetable, which class has which subject in "
    "which room on each weekday (mcp__kb-desk__get)",
    "- mcp__mv-lab: the tools of the MCP server mv-lab: the physics lab's freezers, their temperature and door "
    "openings by day (mcp__mv-lab__get)",
]
_FORECAST_LINES: list[str] = [
    '- mcp__ml-pack: the tools of the MCP server ml-pack, which describes itself as "Gives the weather forecast that '
    'the Turkish State Meteorological Service published for a city on a day." (mcp__ml-pack__get)',
    '- mcp__pq-dev: the tools of the MCP server pq-dev, which describes itself as "Gives the build status of a '
    'continuous-integration pipeline of a repository." (mcp__pq-dev__get)',
]
_BARE_LINES: dict[str, list[str]] = {
    "rates-server-by-its-words": [
        "- mcp__ab-tools: the tools of the MCP server ab-tools (mcp__ab-tools__lookup)",
        "- mcp__zq-kit: the tools of the MCP server zq-kit (mcp__zq-kit__convert)",
    ],
    "timetable-server-by-owners-words": [
        "- mcp__kb-desk: the tools of the MCP server kb-desk (mcp__kb-desk__get)",
        "- mcp__mv-lab: the tools of the MCP server mv-lab (mcp__mv-lab__get)",
    ],
    "forecast-server-by-its-tools": [
        "- mcp__ml-pack: the tools of the MCP server ml-pack (mcp__ml-pack__get)",
        "- mcp__pq-dev: the tools of the MCP server pq-dev (mcp__pq-dev__get)",
    ],
}


@pytest.mark.parametrize(
    ("task_id", "said", "quoted"),
    [
        ("rates-server-by-its-words", _RATES_LINES, True),
        ("timetable-server-by-owners-words", _TIMETABLE_LINES, False),
        ("forecast-server-by-its-tools", _FORECAST_LINES, True),
    ],
)
async def test_the_prompt_of_a_server_task_says_what_each_server_is_for_in_the_one_way_the_task_gives(
    task_id: str,
    said: list[str],
    quoted: bool,
    config: AppConfig,
    login_env: dict[str, str],
    requests: list[ChatRequest],
) -> None:
    """What the server says of itself (its instructions, else the first sentence of its tool), or what its owner wrote
    in the table: the run starts with its tools in groups, off, and the lines are all it has to go by."""
    outcome: TaskOutcome = await replay_reference(select_tasks([task_id])[0], config, login_env)
    assert outcome.passed, outcome
    prompt: str = requests[0].messages[0].content
    assert _lines(prompt) == said
    assert (_RULE in prompt) is quoted
    # The run starts with the core tools only: the servers' tools are not in the first request.
    assert not any(spec.name.startswith("mcp__") for spec in requests[0].tools)
    assert USE_TOOLS_TOOL in {spec.name for spec in requests[0].tools}


@pytest.mark.parametrize("task_id", sorted(_BARE_LINES))
async def test_with_the_lines_left_bare_the_same_task_plays_the_prompt_it_always_had_and_the_reference_still_passes(
    task_id: str, config: AppConfig, login_env: dict[str, str], requests: list[ChatRequest]
) -> None:
    """The other arm of the measurement: ``[agent] mcp_purposes = false`` (``dotx eval run --no-mcp-purposes``) leaves
    each line as the server's name and its tools' names. The scripted reference passes either way; a live model's
    pass rate is what the words change."""
    outcome: TaskOutcome = await replay_reference(select_tasks([task_id])[0], _bare(config), login_env)
    assert outcome.passed, outcome
    prompt: str = requests[0].messages[0].content
    assert _lines(prompt) == _BARE_LINES[task_id]
    assert "describes itself" not in prompt and _RULE not in prompt


async def test_the_skill_task_among_many_lists_the_decoy_that_shares_words_and_only_names_the_skill_that_fits(
    config: AppConfig, login_env: dict[str, str], requests: list[ChatRequest]
) -> None:
    """The list of the instructions keeps the skills that share a word with the goal. The post for X shares none
    ("tweet" is not "post for X"), so its description is not there, but its name is, among the others (item 659): and
    the decoy, the post for LinkedIn, shares "launch" and "post", and is listed."""
    outcome: TaskOutcome = await replay_reference(select_tasks(["tweet-skill-among-many"])[0], config, login_env)
    assert outcome.passed, outcome
    skills: str = requests[0].messages[0].content.split("\n# Skills\n", 1)[1].split("\n\n", 1)[0]
    listed: list[str] = [line.split(":", 1)[0] for line in skills.split("\n") if line.startswith("- ")]
    assert listed[:-1] == ["- linkedin-post"] and listed[-1].startswith("- 25 more installed skills")
    assert "Writes posts for X in the owner's voice" not in skills
    named: str = skills.split("They are: ", 1)[1]
    assert "x-post-writer" in named.split(", ") and "linkedin-post" not in named
    # Twenty-two skills of the task and the four that come with Dot: one listed, the rest named, none out of sight.
    assert len(named.rstrip(".").split(", ")) == 25


async def test_a_run_that_turns_on_the_decoys_group_fails_though_it_goes_on_to_the_right_one(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    both: EvalTask = _with(
        "rates-server-by-its-words",
        [
            _turn(USE_TOOLS_TOOL, group="mcp__zq-kit"),
            _turn(USE_TOOLS_TOOL, group="mcp__ab-tools"),
            _turn("mcp__ab-tools__lookup", key="2026-03-02 EUR/TRY"),
            _finish("250 euros are 9,653.13 Turkish lira."),
        ],
    )
    outcome: TaskOutcome = await replay_reference(both, config, login_env)
    assert not outcome.passed and outcome.status == "done"
    assert _failed(outcome) == ["the tool group mcp__zq-kit stayed off"]


async def test_a_call_of_a_tool_whose_group_is_off_runs_and_turns_the_group_on(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    straight: EvalTask = _with(
        "rates-server-by-its-words",
        [
            _turn("mcp__ab-tools__lookup", key="2026-03-02 EUR/TRY"),
            _finish("250 euros are 9,653.13 Turkish lira."),
        ],
    )
    outcome: TaskOutcome = await replay_reference(straight, config, login_env)
    assert outcome.passed, outcome
    # The call ran at once (item 742); a call of the decoy's tool turns the decoy's group on, which the task refuses.
    assert outcome.metrics.tool_errors == 0
    wrong: EvalTask = _with(
        "rates-server-by-its-words",
        [
            _turn("mcp__zq-kit__convert", key="250 EUR to TRY"),
            _turn(USE_TOOLS_TOOL, group="mcp__ab-tools"),
            _turn("mcp__ab-tools__lookup", key="2026-03-02 EUR/TRY"),
            _finish("250 euros are 9,653.13 Turkish lira."),
        ],
    )
    assert _failed(await replay_reference(wrong, config, login_env)) == ["the tool group mcp__zq-kit stayed off"]


async def test_an_answer_the_run_knew_without_the_server_does_not_pass_a_task_that_asks_for_the_pick(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    from_memory: EvalTask = _with(
        "rates-server-by-its-words", [_finish("250 euros are 9,653.13 Turkish lira at the bank's rate of 38.6125.")]
    )
    outcome: TaskOutcome = await replay_reference(from_memory, config, login_env)
    assert not outcome.passed and outcome.status == "done"
    assert _failed(outcome) == ["the run called mcp__ab-tools__lookup and the call ran"]
    # The answer was right; the pick was not made.
    assert outcome.checks[0].passed


async def test_a_run_that_reads_the_decoy_skill_or_none_at_all_fails_though_its_file_is_right(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    write: ScriptedTurn = _turn(
        "write_file",
        path="CHANGELOG.md",
        content="# Changelog\n\n### 2026-03-02 / 2.4.0\nNEW: dark mode\nFIXED: login loop\n",
    )
    done: ScriptedTurn = _finish("I added the entry.")
    both: EvalTask = _with(
        "changelog-skill-among-decoys",
        [_turn("skill", name="release-mail"), _turn("skill", name="shop-changelog"), write, done],
    )
    outcome: TaskOutcome = await replay_reference(both, config, login_env)
    assert not outcome.passed and _failed(outcome) == ["the run did not read the skill release-mail"]
    guessed: EvalTask = _with("changelog-skill-among-decoys", [write, done])
    outcome = await replay_reference(guessed, config, login_env)
    assert not outcome.passed and _failed(outcome) == ["the run read the skill shop-changelog"]
    # The file it wrote is the house form, so every check of the file held: only the pick was not made.
    assert all(check.passed for check in outcome.checks if "CHANGELOG" in check.check or "grep" in check.check)
    # Reading the right skill and writing the entry the usual way, not the house form, fails the file.
    other: EvalTask = _with(
        "changelog-skill-among-decoys",
        [
            _turn("skill", name="shop-changelog"),
            _turn("write_file", path="CHANGELOG.md", content="# Changelog\n\n## [2.4.0] - 2026-03-02\n### Added\n"),
            done,
        ],
    )
    outcome = await replay_reference(other, config, login_env)
    assert not outcome.passed and "CHANGELOG.md contains '### 2026-03-02 / 2.4.0'" in _failed(outcome)

"""A rule from the owner's words (item 110): the pattern written from the drafted parts fits the actions the rule means
and not the ones around them, a draft that breaks a check is refused, and the drafter asks the review model once more
for such a draft but answers words that make no rule with the model's reason."""

from pathlib import Path

import pytest

from dotagent.agent.review import RULE_TOOL, RuleDrafter, RuleWordsRequest, ToolBrief, rule_drafter_for
from dotagent.config import load_default_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.types import JsonObject
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.policy.drafting import DraftedRule, RuleDraft, rule_from_draft
from dotagent.policy.engine import match_rule
from dotagent.policy.types import ActionInfo
from dotagent.store.models import Behavior, RuleRecord
from dotagent.tools.shell import ShellArgs, _classify_shell

TOOLS: frozenset[str] = frozenset({"shell", "browser_open", "browser_act", "write_file"})
WORDS: RuleWordsRequest = RuleWordsRequest(
    sentence="Let Dot run git status without asking me",
    tools=tuple(ToolBrief(name=name, description=f"The {name} tool. More about it.") for name in sorted(TOOLS)),
)


def _draft(tool: str, answer: Behavior, commands: list[str], mentions: list[str]) -> RuleDraft:
    return RuleDraft(tool=tool, answer=answer, commands=commands, mentions=mentions, problem="")


def _fits(rule: DraftedRule, action: ActionInfo) -> bool:
    record: RuleRecord = RuleRecord(
        id="rule-1",
        position=0,
        enabled=True,
        behavior=rule.behavior,
        tool_glob=rule.tool_glob,
        match_regex=rule.match_regex,
        description=rule.description,
        created_at=0.0,
    )
    return match_rule(action, [record]) is not None


def _shell(command: str, workspace: Path) -> ActionInfo:
    """A shell command as the shell tool shows it to the policy: its summary, a line break, the command."""
    return _classify_shell(ShellArgs(command=command), workspace, (), ())


def _page(url: str) -> ActionInfo:
    return ActionInfo(
        tool="browser_open", kind="browser_navigate", risk="low", summary=f"Open {url}", target=url, flags=()
    )


@pytest.mark.parametrize(
    ("command", "fits"),
    [
        ("git status", True),
        ("git status --short", True),
        ("  git diff HEAD~1 -- src/app.py", True),
        ("cd /Users/me/project && git status", True),
        ("git status; rm -rf build", False),
        ("git status && rm -rf build", False),
        ("git diff | sh", False),
        ("git diff > /tmp/out.txt", False),
        ("git status $(rm -rf build)", False),
        ("git status\nrm -rf build", False),
        ("echo git status", False),
        ("git statusx", False),
        ("git push origin main", False),
    ],
)
def test_a_rule_that_lets_dot_run_a_command_fits_it_alone(command: str, fits: bool, tmp_path: Path) -> None:
    rule: DraftedRule = rule_from_draft(_draft("shell", "allow", ["git status", "git diff"], []), TOOLS)
    assert _fits(rule, _shell(command, tmp_path)) is fits


@pytest.mark.parametrize(
    ("command", "fits"),
    [
        ("rm -rf build", True),
        ("cd /tmp && rm -rf build", True),
        ("sudo rm notes.txt", True),
        ("npm run format", False),
        ("git status", False),
    ],
)
def test_a_rule_that_stops_dot_catches_a_command_anywhere(command: str, fits: bool, tmp_path: Path) -> None:
    rule: DraftedRule = rule_from_draft(_draft("shell", "ask", ["rm"], []), TOOLS)
    assert _fits(rule, _shell(command, tmp_path)) is fits


@pytest.mark.parametrize(
    ("url", "fits"),
    [
        ("https://x.com/home", True),
        ("https://mobile.x.com/compose/post", True),
        ("https://netflix.com/browse", False),
        ("https://x.company.example/", False),
    ],
)
def test_a_mention_fits_as_a_whole_word(url: str, fits: bool) -> None:
    rule: DraftedRule = rule_from_draft(_draft("browser_*", "handoff", [], ["x.com"]), TOOLS)
    assert _fits(rule, _page(url)) is fits


@pytest.mark.parametrize(
    ("draft", "problem"),
    [
        (_draft("calendar_*", "allow", [], []), "the tool fits none of Dot's tools"),
        (_draft("browser_open", "allow", ["open"], []), "only a rule for the shell tool names commands"),
        (_draft("shell", "allow", ["git status"], ["status"]), "a rule names commands or mentions, not both"),
        (_draft("shell", "allow", ["git status && git push"], []), "a command that a rule lets Dot run is one plain"),
        (_draft("browser_*", "ask", [], ["..."]), "a command or a mention needs a word to match on"),
    ],
)
def test_a_draft_that_breaks_a_check_is_refused(draft: RuleDraft, problem: str) -> None:
    with pytest.raises(InvalidValueError) as caught:
        rule_from_draft(draft, TOOLS)
    assert caught.value.message.startswith(problem)


def test_the_rule_reads_back_as_a_sentence_in_its_description() -> None:
    assert rule_from_draft(_draft("shell", "allow", ["git status", "git diff"], []), TOOLS).description == (
        "For “git status” or “git diff” run alone in the shell, with plain arguments (after a cd at most): "
        "Dot does it without asking."
    )
    assert rule_from_draft(_draft("browser_*", "handoff", [], ["mybank.com"]), TOOLS).description == (
        "For any use of browser_* (2 tools) that mentions “mybank.com”: Dot hands it to you."
    )
    assert rule_from_draft(_draft("write_file", "ask", [], []), TOOLS).description == (
        "For every use of write_file: Dot asks you first."
    )


def _turn(tool: str, commands: list[str], problem: str) -> ScriptedTurn:
    arguments: JsonObject = {
        "tool": tool,
        "answer": "allow",
        "commands": list(commands),
        "mentions": [],
        "problem": problem,
    }
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=RULE_TOOL, arguments=arguments),))


async def _drafter(turns: list[ScriptedTurn], calls: list[ModelCall]) -> RuleDrafter:
    async def record(call: ModelCall) -> None:
        calls.append(call)

    router: ModelRouter = ModelRouter(load_default_config(), record)
    await router.register("ollama", ScriptedProvider("ollama", turns))
    return rule_drafter_for(router, "ollama:scripted", 30.0, "rules:test")


async def test_a_draft_that_breaks_a_check_is_asked_for_once_more() -> None:
    calls: list[ModelCall] = []
    drafter: RuleDrafter = await _drafter(
        [_turn("browser_open", ["git status"], ""), _turn("shell", ["git status"], "")], calls
    )
    rule: DraftedRule = await drafter(WORDS)
    assert (rule.tool_glob, rule.behavior, rule.description) == (
        "shell",
        "allow",
        "For “git status” run alone in the shell, with plain arguments (after a cd at most): Dot does it without asking.",
    )
    assert [call.purpose for call in calls] == ["rule_draft", "rule_draft"]


async def test_words_that_make_no_rule_are_answered_with_the_models_reason() -> None:
    calls: list[ModelCall] = []
    drafter: RuleDrafter = await _drafter(
        [_turn("", [], "This is about the weather, not about what Dot may do.")], calls
    )
    with pytest.raises(InvalidValueError, match="about the weather"):
        await drafter(WORDS)
    assert len(calls) == 1

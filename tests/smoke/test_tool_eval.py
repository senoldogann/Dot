"""The eval of tool use played end to end (item 736) with the scripted model as the model under test, which finishes
at once: the request of each case is captured as a run of its goal makes it, the model is asked, and its call judged.
No model service is used."""

import pytest

from dotagent import secrets
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.tool_cases import CASES
from dotagent.evals.toolcalls import CaseResult, ToolCase
from dotagent.evals.toolcalls_play import keys_for, no_keychain, play_cases
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)

pytestmark = pytest.mark.asyncio


async def test_the_cases_are_played_and_a_model_that_only_finishes_passes_none_but_the_one_where_finishing_fits() -> (
    None
):
    config = eval_config(load_default_config())
    seen: list[str] = []
    finishing: ScriptedProvider = ScriptedProvider(_PROVIDER, [_FINISH] * len(CASES))
    results: list[CaseResult] = await play_cases(
        config,
        await read_login_env(10.0, ()),
        {},
        SCRIPTED_MODEL,
        CASES,
        1,
        lambda result: seen.append(result.case_id),
        finishing,
    )
    assert seen == [case.id for case in CASES]
    passed: dict[str, bool] = {result.case_id: result.passed for result in results}
    # Reading the page it opened, the model may answer at once: the order says it is confirmed.
    assert passed.pop("read-what-the-page-shows") is True
    assert not any(passed.values()), passed
    edit: CaseResult = next(result for result in results if result.case_id == "edit-with-dots-own-arguments")
    assert (edit.tool, edit.why) == ("finish", "called finish, not one of edit_file")


def test_the_keys_of_a_model_come_from_the_environment_and_the_homes_env_file_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("DOT_HOME", str(home))
    (home / ".env").write_text("OPENCODE_API_KEY=from-the-file\n", encoding="utf-8")
    config = load_default_config()
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert keys_for(config, "opencode_go:deepseek-v4.1-flash") == {"OPENCODE_API_KEY": "from-the-file"}
    monkeypatch.setenv("OPENCODE_API_KEY", "from-the-environment")
    assert keys_for(config, "opencode_go:deepseek-v4.1-flash") == {"OPENCODE_API_KEY": "from-the-environment"}
    # A key in neither is left out: nothing asks the Keychain.
    assert keys_for(config, "anthropic:claude-haiku-5-5") == {}


def test_the_keychain_is_not_asked_while_the_cases_play() -> None:
    """Item 736, pre-push review round 1: the runtime the cases play in looked for the keys it did not find in the
    Keychain; while they play, the lookup answers that there is none, and is put back after."""
    before = secrets._from_keychain
    with no_keychain():
        assert secrets._from_keychain is not before
        assert secrets._from_keychain("OLLAMA_API_KEY") is None
    assert secrets._from_keychain is before


async def test_a_model_that_only_plans_fails_its_case() -> None:
    plan: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="update_plan", arguments={"steps": ["a"]}),))
    page: list[ToolCase] = [case for case in CASES if case.id == "read-what-the-page-shows"]
    results: list[CaseResult] = await play_cases(
        eval_config(load_default_config()),
        await read_login_env(10.0, ()),
        {},
        SCRIPTED_MODEL,
        page,
        1,
        answering=ScriptedProvider(_PROVIDER, [plan] * 3),
    )
    assert [(result.passed, result.why) for result in results] == [(False, "it only planned")]

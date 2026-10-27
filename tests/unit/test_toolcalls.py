"""The judge of the eval of tool use (item 736): the first call that does work, the tool it must be and the arguments
it must give, the calls it must not make; and the cases themselves."""

from dotagent.evals.tool_cases import CASES
from dotagent.evals.toolcalls import Expected, PriorStep, ToolCase, first_work, judge, prior_messages
from dotagent.llm.types import ModelReply, ToolCall, empty_usage

_EDIT: ToolCase = ToolCase(
    id="edit",
    failure="test",
    goal="Change it.",
    expect=Expected(tools=("edit_file",), present=("path", "old", "new"), match={"old": r"milk"}, forbid=("shell",)),
)


def _call(name: str, **arguments: str) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=dict(arguments))


def test_a_call_passes_with_its_tool_and_its_arguments() -> None:
    assert judge(_EDIT, _call("edit_file", path="a", old="buy milk", new="buy oat milk")) == (True, "")


def test_a_call_fails_for_another_tool_a_forbidden_one_or_none() -> None:
    assert judge(_EDIT, None) == (False, "no call that does work")
    finishing: ToolCase = _EDIT.model_copy(update={"expect": Expected(tools=("finish", "read_file"))})
    assert judge(finishing, None) == (True, "")
    assert judge(_EDIT, _call("shell", command="sed")) == (False, "called shell, which this moment must not")
    assert judge(_EDIT, _call("write_file", path="a")) == (False, "called write_file, not one of edit_file")


def test_another_agents_argument_names_fail() -> None:
    passed, why = judge(_EDIT, _call("edit_file", file_path="a", old_string="buy milk", new_string="x"))
    assert not passed and why.startswith("left out path, old, new")
    assert judge(_EDIT, _call("edit_file", path="a", old="bread", new="x")) == (False, "old does not match 'milk'")


def test_planning_first_is_no_work_and_the_work_after_it_is_judged() -> None:
    reply: ModelReply = ModelReply(
        model="m",
        text="",
        thinking="",
        tool_calls=(_call("update_plan", plan="x"), _call("edit_file", path="a", old="milk", new="y")),
        usage=empty_usage(),
        finish="tool_calls",
        raw_finish="tool_calls",
        raw=(),
    )
    work: ToolCall | None = first_work(reply)
    assert work is not None and work.name == "edit_file"


def test_the_steps_before_a_moment_are_a_call_and_its_result() -> None:
    messages = prior_messages((PriorStep(tool="read_file", arguments={"path": "a"}, result="1\tx"),))
    assert [message.role for message in messages] == ["assistant", "tool"]
    assert messages[0].tool_calls[0].name == "read_file" and messages[1].content == "1\tx"


def test_every_case_has_its_own_id_a_failure_and_a_reachable_answer() -> None:
    assert len({case.id for case in CASES}) == len(CASES)
    for case in CASES:
        assert case.failure and case.goal
        assert not set(case.expect.tools) & set(case.expect.forbid), case.id

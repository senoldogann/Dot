"""The steps a checkpoint's judge reads (item 1132)."""

from dotagent.agent.compact import render_recent_steps, render_transcript
from dotagent.agent.review import goal_digest
from dotagent.llm.types import Message, ToolCall

_GOAL: str = "Protect main in every public repository"
_APPLIED: str = "zsh /tmp/apply_protection.sh"


def _message(role: str, content: str, calls: tuple[ToolCall, ...], name: str | None) -> Message:
    return Message.model_validate(
        {
            "role": role,
            "content": content,
            "images": (),
            "tool_calls": calls,
            "tool_call_id": calls[0].id if calls and role == "tool" else None,
            "tool_name": name,
            "thinking": None,
            "raw": (),
            "raw_origin": None,
        }
    )


def _step(index: int, command: str, output: str) -> list[Message]:
    call: ToolCall = ToolCall(id=f"call_{index}", name="shell", arguments={"command": command})
    return [_message("assistant", "", (call,), None), _message("tool", output, (), "shell")]


def _run() -> list[Message]:
    """A run of the shape the owner's showed: twenty steps of reading, the step that does the work, then twelve more
    steps reading long output, each result longer than a transcript line keeps."""
    messages: list[Message] = [_message("user", _GOAL, (), None)]
    for index in range(20):
        messages += _step(index, f"gh api repos/owner/repo{index}/actions/workflows", "workflow\n" * 400)
    messages += _step(20, _APPLIED, "repo0 protected=true\n" * 5)
    for index in range(21, 33):
        messages += _step(index, f"sed -n 1,200p /tmp/wf/repo{index}/gate.yml", "job: gate\n" * 400)
    return messages


def test_the_digest_cut_in_the_middle_lost_the_step_that_did_the_work() -> None:
    """What the judge read before: the start and the end, without the step that applied the protection."""
    assert _APPLIED not in goal_digest(render_transcript(_run()))


def test_the_checkpoint_reads_the_newest_steps_whole() -> None:
    digest: str = render_recent_steps(_run(), 8_000)
    assert len(digest) <= 8_000
    assert _APPLIED in goal_digest(digest)
    assert "repo32/gate.yml" in digest
    assert digest.startswith("[the ")
    assert "repo0/actions" not in digest


def test_a_run_that_fits_is_read_from_its_goal_on() -> None:
    digest: str = render_recent_steps(_run(), 30_000)
    assert digest.startswith(f"USER: {_GOAL}")
    assert "left out" not in digest


def test_a_secret_is_masked_before_its_line_is_shortened() -> None:
    token: str = "ghp_" + "a1B2c3D4e5" * 4
    messages: list[Message] = _step(0, f"GH_TOKEN={token} gh api user " + "x" * 600, "ok")
    assert token[:12] not in render_recent_steps(messages, 8_000)

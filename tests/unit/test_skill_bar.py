"""The bar for a learned skill (item 585): no skill from a run that changed Dot itself (what each kind of step is read
for: the verdict the policy stored on it, what an MCP call's arguments name, the spellings of a path and of the daemon's
address, which steps only observe), none from a chat-mode run or at the owner's word from a run that is not their own,
and a skill written only when the review model says the steps are a procedure that recurs: the answer without the
field is asked for again. Pure; no files, no model."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.agent.playbooks import arguments_name_dot, daemon_addresses, dot_names, touched_dot, unsavable
from dotagent.agent.review import _PLAYBOOK_SPEC, PlaybookDraft, PlaybookReply
from dotagent.evals import EVAL_SOURCE
from dotagent.store.models import ResultStep, RunRecord

HOME: Path = Path("/Users/someone")
PLACES: tuple[Path, ...] = (
    HOME / ".dot" / "playbooks",
    HOME / ".dot" / "config.toml",
    Path("/opt/dot/src/dotagent"),
)
ADDRESSES: tuple[str, ...] = ("127.0.0.1:8765", "localhost:8765", "[::1]:8765")


def _step(tool: str, kind: str, target: str, *flags: str, names: str = "", helper: bool = False) -> ResultStep:
    return ResultStep(tool=tool, kind=kind, target=target, flags=tuple(flags), names_dot=names, helper=helper)


def _why(*steps: ResultStep) -> str | None:
    return touched_dot(steps, PLACES, ADDRESSES, HOME)


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(_step("write_file", "write", "/Users/someone/.dot/playbooks/learned-x/SKILL.md"), id="full path"),
        pytest.param(_step("write_file", "write", "~/.dot/playbooks/learned-x/SKILL.md"), id="tilde"),
        pytest.param(_step("shell", "shell", "cp draft $HOME/.dot/playbooks/learned-x/SKILL.md"), id="$HOME"),
        pytest.param(_step("shell", "shell", "cp draft ${HOME}/.dot/config.toml"), id="${HOME}"),
        pytest.param(_step("write_file", "write", "/opt/dot/src/dotagent/agent/loop.py"), id="its code"),
        pytest.param(
            _step("shell", "shell", "curl -s http://127.0.0.1:8765/api/settings"), id="the daemon, by address"
        ),
        pytest.param(
            _step("browser_act", "browser", "http://localhost:8765/#/customize/skills"), id="the daemon, in a page"
        ),
        pytest.param(_step("shell", "shell", "dotx skills restore learned-x 1"), id="dotx"),
        pytest.param(_step("shell", "shell", "uv run dotagent doctor"), id="dotagent"),
        pytest.param(_step("shell", "shell", "cd /tmp && python3 -m dotagent status"), id="python -m dotagent"),
        pytest.param(_step("write_file", "write", "notes.txt", "self_change"), id="a step the policy flagged"),
        pytest.param(
            _step("custom", "mcp_write", "a thing", "self_change", "outside_workspace"), id="flagged among others"
        ),
        # The verdict the shell guard stored is read, whatever the words of the line (the security review of item 585):
        # a working folder it does not name, a path in other letters, a link, a copy over the whole of Dot's home.
        pytest.param(
            _step("shell", "shell", "printf 'x\\n' >> scratch.txt", "secret_access"), id="a command the guard flagged"
        ),
        pytest.param(
            _step("shell", "shell", "cp -R /tmp/backup/. ~/.dot/", "protected_read", "outside_workspace"),
            id="a read the guard could not tell from a protected one",
        ),
        pytest.param(_step("terminal_send", "shell", "ls", "secret_access"), id="a terminal the guard flagged"),
        pytest.param(_step("write_file", "write", "/etc/x", "secret_access"), id="a file the policy flagged"),
        # What the executor found in the arguments of an MCP call, whose target is the server's name.
        pytest.param(_step("mcp__fs__write_file", "mcp", "fs", "destructive", names="~/.dot/config.toml"), id="mcp"),
        # Letter case is no difference on a Mac's volume.
        pytest.param(_step("shell", "shell", "printf x >> /Users/someone/.DOT/PLAYBOOKS/y"), id="capital letters"),
        pytest.param(_step("shell", "shell", "DOTX skills restore a 1"), id="DOTX"),
    ],
)
def test_a_step_that_changed_what_dot_guards_or_loads_is_why_a_run_gives_no_skill(step: ResultStep) -> None:
    why: str | None = _why(_step("read_file", "read", "notes.txt"), step, _step("read_file", "read", "notes.txt"))
    assert why is not None and step.tool in why


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(_step("read_file", "read", "/Users/someone/.dot/playbooks/learned-x/SKILL.md"), id="a read"),
        pytest.param(_step("skill", "meta", "learned-x /Users/someone/.dot/playbooks"), id="a skill read"),
        pytest.param(_step("browser_read", "browser_read", "http://127.0.0.1:8765/"), id="a page read"),
        pytest.param(_step("memory_search", "memory_read", "~/.dot/config.toml"), id="a note read"),
        pytest.param(_step("write_file", "write", "/Users/someone/Documents/report.md"), id="a file of the owner's"),
        pytest.param(_step("write_file", "write", "notes_dotagent.txt"), id="a name that holds the program's"),
        pytest.param(_step("shell", "shell", "echo dotx-backups and mydotx"), id="a longer word"),
        pytest.param(_step("shell", "shell", "git status && ls"), id="a plain command"),
        pytest.param(_step("shell", "shell", "curl -s http://127.0.0.1:9999/health"), id="another port"),
        pytest.param(_step("write_file", "write", "/Users/someone/.dotrc"), id="a path that starts the same"),
        # A flag the policy raised on a step that only observes is no change of Dot's: reading is what the flags of a
        # read say, and the owner's skills are read by path.
        pytest.param(_step("read_file", "read", "~/.ssh/config", "secret_access"), id="a read of a protected place"),
        pytest.param(_step("mcp__fs__read", "mcp_read", "fs", names="~/.dot/config.toml"), id="a read through MCP"),
        pytest.param(_step("mcp__fs__write_file", "mcp", "fs", "destructive"), id="an MCP call that named nothing"),
        pytest.param(_step("shell", "shell", "ls", "external_send", "outside_workspace"), id="other flags"),
    ],
)
def test_what_only_observes_or_does_not_name_what_dot_guards_leaves_a_run_as_it_was(step: ResultStep) -> None:
    assert _why(step) is None


def test_the_reason_names_the_step_that_decided_it_and_stays_short() -> None:
    long_path: str = "/Users/someone/.dot/playbooks/" + "x" * 1000
    why: str | None = _why(_step("shell", "shell", "echo hello"), _step("write_file", "write", long_path))
    assert why is not None and "write_file" in why and "echo hello" not in why and len(why) < 400
    assert "which Dot guards or loads" in why
    assert "changed Dot's own files" in str(_why(_step("write_file", "write", "x", "self_change")))
    assert "ran Dot's own program" in str(_why(_step("shell", "shell", "dotx status")))
    # A run with no step, or only steps of its own work, is as it was.
    assert _why() is None and _why(_step("write_file", "write", "notes.txt")) is None


ARGUMENT_NAMES: tuple[str, ...] = dot_names(PLACES, ADDRESSES, HOME)


@pytest.mark.parametrize(
    ("arguments", "named"),
    [
        pytest.param({"path": "/Users/someone/.dot/config.toml"}, "/Users/someone/.dot/config.toml", id="a path"),
        pytest.param({"path": "~/.dot/config.toml", "content": "x"}, "~/.dot/config.toml", id="a tilde path"),
        pytest.param({"files": [{"to": "$HOME/.dot/playbooks/learned-x/SKILL.md"}]}, "$HOME/.dot/playbooks", id="deep"),
        pytest.param({"/Users/someone/.dot/config.toml": "x"}, "/Users/someone/.dot/config.toml", id="a key"),
        pytest.param({"path": "/USERS/SOMEONE/.DOT/CONFIG.TOML"}, "/Users/someone/.dot/config.toml", id="the case"),
        pytest.param({"url": "http://localhost:8765/api/settings"}, "localhost:8765", id="the daemon"),
        pytest.param({"command": "dotx skills restore a 1"}, "dotx", id="the program"),
        pytest.param({"command": "python -m dotagent doctor"}, "dotagent", id="the package"),
        pytest.param(
            {"a": ["b", {"c": "x" * 100_000 + "/opt/dot/src/dotagent/agent/loop.py"}]},
            "/opt/dot/src/dotagent",
            id="long",
        ),
    ],
)
def test_the_arguments_of_a_call_are_read_for_what_dot_guards_or_loads(
    arguments: dict[str, object], named: str
) -> None:
    found: str | None = arguments_name_dot(arguments, ARGUMENT_NAMES)  # type: ignore[arg-type]
    assert found is not None and found.casefold() == named.casefold()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": "notes.txt", "content": "hello", "append": True, "count": 3, "none": None},
        {"path": "/Users/someone/Documents/report.md", "tags": ["a", "b"]},
        {"url": "http://127.0.0.1:9999/health", "note": "mydotx and dotx-backups"},
        {"path": "/Users/someone/.dotrc"},
    ],
)
def test_the_arguments_that_name_nothing_of_dots_are_left_alone(arguments: dict[str, object]) -> None:
    assert arguments_name_dot(arguments, ARGUMENT_NAMES) is None  # type: ignore[arg-type]


def test_the_daemons_address_is_written_the_ways_a_command_or_a_page_writes_it() -> None:
    assert daemon_addresses(8765) == ("127.0.0.1:8765", "localhost:8765", "[::1]:8765")
    names: tuple[str, ...] = dot_names((HOME / ".dot" / "config.toml",), daemon_addresses(8765), HOME)
    assert names == (
        "/Users/someone/.dot/config.toml",
        "~/.dot/config.toml",
        "$HOME/.dot/config.toml",
        "${HOME}/.dot/config.toml",
        "127.0.0.1:8765",
        "localhost:8765",
        "[::1]:8765",
    )


def test_a_step_of_a_helper_run_is_read_as_the_runs_own_and_the_reason_says_whose_it_was() -> None:
    """The follow-up of round 1 (item 585): a run that delegated work to a run of its own is no cleaner than the helper
    was, so the steps of its helpers are read with its own, by the same rules, and the reason names the helper."""
    own: ResultStep = _step("write_file", "write", "notes.txt")
    for step, said in (
        (_step("write_file", "write", "/Users/someone/.dot/config.toml", "self_change", helper=True), "changed"),
        (_step("shell", "shell", "printf x >> y", "secret_access", helper=True), "flagged secret_access"),
        (_step("shell", "shell", "cp -R a/. ~/", "protected_read", helper=True), "flagged protected_read"),
        (_step("write_file", "write", "~/.dot/playbooks/learned-x/SKILL.md", helper=True), "named ~/.dot/playbooks"),
        (_step("mcp__fs__write_file", "mcp", "fs", names="localhost:8765", helper=True), "named localhost:8765"),
        (_step("shell", "shell", "dotx status", helper=True), "ran Dot's own program"),
    ):
        why: str | None = _why(own, step, own)
        assert why is not None and said in why and "(in a helper run: " in why, (step, why)
        # The same step taken by the run itself says nothing of a helper.
        alone: str | None = _why(own, step.model_copy(update={"helper": False}), own)
        assert alone is not None and said in alone and "helper" not in alone, (step, alone)
    # What a helper only observed, or did that names nothing of Dot's, leaves the run as it was.
    assert _why(own, _step("read_file", "read", "~/.ssh/config", "secret_access", helper=True)) is None
    assert (
        _why(own, _step("write_file", "write", "report.md", helper=True), _step("shell", "shell", "ls", helper=True))
        is None
    )


def test_the_reason_names_what_the_policy_flagged_or_what_the_call_named() -> None:
    flagged: str | None = _why(_step("shell", "shell", "printf x >> y", "secret_access"))
    assert flagged is not None and "flagged secret_access" in flagged and "shell printf x >> y" in flagged
    named: str | None = _why(_step("mcp__fs__write_file", "mcp", "fs", names="/Users/someone/.dot/config.toml"))
    assert named is not None and "named /Users/someone/.dot/config.toml, which Dot guards or loads" in named
    assert "mcp__fs__write_file fs" in named


def _run(
    *,
    mode: str = "task",
    status: str = "done",
    source: str = "ui",
    model: str = "anthropic:claude-opus-5-5",
    parent: str | None = None,
) -> RunRecord:
    return RunRecord(
        id="run-1",
        thread_id="thr-1",
        goal="Keep the line hello in notes.txt",
        mode=mode,  # type: ignore[arg-type]
        model=model,
        status=status,  # type: ignore[arg-type]
        created_at=1_760_000_000.0,
        finished_at=1_760_000_060.0,
        result="done",
        steps=4,
        input_tokens=0,
        output_tokens=0,
        parent_run_id=parent,
        source=source,
        effort=None,
        speed="standard",
        once=None,
        approvals="ask",
        goal_mode=False,
        campaign_id=None,
        agent_id=None,
        rewindable=False,
    )


def test_a_run_gives_no_skill_at_the_owners_word_unless_it_is_a_task_that_acted_and_ended_done() -> None:
    assert unsavable(_run(), 3) is None
    assert "chat-mode run only talks" in str(unsavable(_run(mode="chat"), 3))
    assert "no acting step" in str(unsavable(_run(), 0))
    assert "ended failed" in str(unsavable(_run(status="failed"), 3))
    assert "eval" in str(unsavable(_run(source=EVAL_SOURCE), 3))
    assert "delegated run" in str(unsavable(_run(parent="run-0"), 3))
    assert "OpenCode" in str(unsavable(_run(model="opencode:big-pickle"), 3))


def _answer(**more: object) -> dict[str, object]:
    return {
        "kind": "Keep a line in a notes file",
        "description": "Write a line to a notes file and read it back.",
        "when": "A goal that asks to keep a line in a file.",
        "steps": ["Write <line> to <file>.", "Read <file> back."],
        "parameters": ["<line>: the text"],
        **more,
    }


def test_the_writers_answer_needs_the_judgement_and_the_skill_it_keeps_does_not_hold_it() -> None:
    with pytest.raises(ValidationError, match="recurs"):
        PlaybookReply.model_validate(_answer())
    kept: PlaybookReply = PlaybookReply.model_validate(_answer(recurs=True))
    assert kept.recurs is True and PlaybookReply.model_validate(_answer(recurs=False)).recurs is False
    # The writer's tool asks for the field, and a draft (a merge, a rewrite) is not asked for it.
    required: object = _PLAYBOOK_SPEC.parameters["required"]
    assert isinstance(required, list) and "recurs" in required
    assert "recurs" not in PlaybookDraft.model_fields and PlaybookDraft.model_validate(_answer()).kind

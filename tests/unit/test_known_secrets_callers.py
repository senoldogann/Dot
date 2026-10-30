"""Who asks for the secrets Dot holds, and which of its two lists each asks for (item 621).

A value filled into a page (``hold_filled``) is a mask and no more: a run never saw it, and a check that acts
differently by whether a guess is the value (a refusal, a block, another outcome) tells whoever watches it act, the run
among them, the value. So ``known_secrets`` has no default for ``filled``: each caller says which it is, and this test
holds the answer for all of them, by reading the source. A caller that masks asks with the values (``filled=True``); one
that refuses, blocks or chooses asks without them (``filled=False``); one that does both refuses by the second and then
stores or shows its text with every value masked. A new caller, or one that changes its list, is a line here, which a
review reads."""

import ast
from pathlib import Path

import pytest

from dotagent import secrets
from dotagent.core.text import mask_known, redact_known
from dotagent.secrets import known_secrets, mask_filled

SOURCE: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent"

# Where ``known_secrets`` is asked for (the file, the function it stands in) and the ``filled`` of each call, in the
# order of the source.
EXPECTED: dict[str, tuple[bool, ...]] = {
    # ----- Masking: a text that is shown, logged, stored or sent on with the secrets replaced (``filled=True``). -----
    # A reply, a summary, a stream, a record of a step, the tail of a log, the errors of a server: the model's own words
    # and what was done, masked where they are made.
    "agent/aside.py:answer_aside": (True,),
    "agent/executor.py:_execute_one": (True,),
    "agent/gate.py:_shown": (True,),
    "agent/loop.py:_compact": (True,),
    "agent/manager.py:RunManager._record_end": (True,),
    "agent/loop.py:_route_step": (True,),
    "agent/recap.py:_write": (True,),
    "agent/review.py:_attempt": (True,),
    "mcp/hub.py:_failure": (True,),
    "mcp/masking.py:shown_command": (True,),
    "mcp/masking.py:masked_context": (True,),
    "opencode/run.py:_Follow.seen": (True,),
    "opencode/run.py:_Follow.tool": (True,),
    "opencode/run.py:_Follow.step_ended": (True,),
    "server/diagnostics.py:read_tail": (True,),
    "terminal/session.py:_loggable": (True,),
    "server/routes_runs.py:runs_router.run_changes_view": (True,),
    # What goes to the owner's phone.
    "channels/router.py:ChannelRouter._follow": (True,),
    "channels/router.py:ChannelRouter._say_progress": (True,),
    "channels/router.py:ChannelRouter._say": (True,),
    "channels/router.py:ChannelRouter._say_answer": (True,),
    "channels/service.py:Channels._seen": (True,),
    # A skill a model wrote, masked before it is written.
    "agent/playbooks.py:_write_reply": (True,),
    "agent/skill_proposals.py:propose_merges": (True,),
    "teach.py:draft_taught_skill": (True,),
    # ----- Refusing, blocking or choosing: nothing a run can watch tells it a value (``filled=False``). -----
    # The gates of the texts that stand in a prompt or a store (the one gate for learned texts, item 587): a text that
    # holds a secret is refused, left out or dropped. What they pass is shown with the values filled masked
    # (``given_text``, ``owner_prompt_lines``, ``save_memory``).
    "agent/consolidate.py:rest_repeating_habits": (False,),
    "agent/lessons.py:draw_lessons": (False,),
    "agent/lessons.py:propose_lesson": (False,),
    "agent/reflection.py:_describe_owner": (False,),
    "harness/propose.py:propose_from_run": (False,),
    "harness/step.py:_carry_out": (False,),
    # Whether a review model's line past its bound may be cut: refused when, whole, it holds a value (item 840).
    "agent/review.py:_refused_values": (False,),
    "agent/review.py:harness_proposer_for.propose": (False,),
    "agent/review.py:harness_composer_for.compose": (False,),
    "agent/review.py:experience_writer_for.write": (False,),
    "cli_harness.py:cmd_harness_show": (False,),
    "cli_harness.py:cmd_harness_set": (False,),
    "cli_harness.py:cmd_harness_adopt": (False,),
    "cli_harness.py:cmd_harness_final": (False,),
    "server/routes_harness.py:harness_router.view": (False,),
    "server/routes_harness.py:harness_router.write_version": (False,),
    "server/routes_harness.py:harness_router.adopt_version": (False,),
    # A note a tool saves or a pin makes is refused for a secret it holds; saved, it has the values masked.
    "tools/memory.py:_secret_refusal": (False,),
    "agent/reactions.py:_holds_a_secret": (False,),
    # An agent is exported only if its words hold none; the file has the values masked.
    "server/routes_agents.py:agents_router.export_agent": (False,),
    # ----- Both: refuses by the first list, then masks by the second. -----
    # A step of a run that read outside content is refused for the secret it would send out of the Mac; its record
    # masks them all.
    "agent/gate.py:RunGate._refuse_carried_secret": (False, True),
    # The prompt of a run: the texts it takes from what Dot learned are asked for again (refused by the first), and the
    # reply of the step is streamed masked (the second).
    "agent/loop.py:run_agent": (False, True),
    # The words of the owner for a change of a skill: refused for a secret; the draft is written with the values masked.
    "agent/skill_proposals.py:propose_rewrite": (False, True),
}


def _is_known_secrets(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "known_secrets") or (
        isinstance(node, ast.Attribute) and node.attr == "known_secrets"
    )


def _callers() -> dict[str, list[bool | None]]:
    """Every call of ``known_secrets`` in the source (also as the function a thread is given), by file and function,
    with the ``filled`` it passes: ``None`` for a call that passes none, or one that is not a plain ``True`` or
    ``False``."""
    found: dict[str, list[bool | None]] = {}

    class Reader(ast.NodeVisitor):
        def __init__(self, module: str) -> None:
            self.module: str = module
            self.inside: list[str] = []

        def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
            self.inside.append(node.name)
            self.generic_visit(node)
            self.inside.pop()

        visit_FunctionDef = _enter
        visit_AsyncFunctionDef = _enter
        visit_ClassDef = _enter

        def visit_Call(self, node: ast.Call) -> None:
            if _is_known_secrets(node.func) or (node.args and _is_known_secrets(node.args[0])):
                flag: bool | None = None
                for keyword in node.keywords:
                    if keyword.arg == "filled" and isinstance(keyword.value, ast.Constant):
                        value: object = keyword.value.value
                        flag = value if isinstance(value, bool) else None
                found.setdefault(f"{self.module}:{'.'.join(self.inside) or '<module>'}", []).append(flag)
            self.generic_visit(node)

    for path in sorted(SOURCE.rglob("*.py")):
        if path == SOURCE / "secrets.py":
            continue
        Reader(str(path.relative_to(SOURCE))).visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_every_caller_of_known_secrets_says_which_list_it_asks_for_and_this_is_who_asks_which() -> None:
    callers: dict[str, list[bool | None]] = _callers()
    undecided: list[str] = [where for where, flags in callers.items() if None in flags]
    assert undecided == [], f"these ask without saying True or False for filled: {undecided}"
    assert {where: tuple(flags) for where, flags in callers.items()} == EXPECTED


def test_a_caller_that_refuses_does_not_look_for_a_value_a_page_was_given_and_one_that_masks_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two lists differ by the values filled into a page and by nothing else: the keys of the env file and the
    values looked up stay in both."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setattr(secrets, "_filled", frozenset())
    # The values an earlier test of this process resolved are among them too: none here, whichever ran before.
    monkeypatch.setattr(secrets, "_resolved", set())
    kept: str = "MARKER-" + "k" * 16
    (tmp_path / ".env").write_text(f"SHOP_KEY={kept}\n", encoding="utf-8")
    password: str = "S3cret-Passw0rd!"
    secrets.hold_filled(password, "short")
    assert known_secrets(filled=True) == (kept, password)
    assert known_secrets(filled=False) == (kept,)
    # What masks has them all, and what refuses does not look for the password.
    text: str = f"{kept} {password}"
    assert mask_known(text, known_secrets(filled=True)) == "*** ***"
    assert mask_known(text, known_secrets(filled=False)) == f"*** {password}"
    assert redact_known(f"the password is {password}", known_secrets(filled=True)).endswith("***")


def test_mask_filled_masks_the_values_a_page_was_given_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets, "_filled", frozenset())
    key: str = "sk-" + "a" * 24
    assert mask_filled(f"the password is S3cret-Passw0rd! and the key is {key}") == (
        f"the password is S3cret-Passw0rd! and the key is {key}"
    ), "nothing filled, nothing masked"
    secrets.hold_filled("S3cret-Passw0rd!", "tiny")
    assert mask_filled(f"the password is S3cret-Passw0rd! and the key is {key}") == (
        f"the password is *** and the key is {key}"
    ), "the key's form is the business of redact_secrets, not of this"
    assert mask_filled("a tiny word") == "a tiny word", "a value too short to mask is left"


# Where the code compares text with a value a page was given (a password, a one-time code) and does something because
# of the answer, by the function it stands in (item 621, round 7). An outcome that depends on whether a word is the
# value tells whoever can try words and watch the outcome, and a run can wherever a script of its own runs
# (``browser_eval``). So a request is refused for carrying a value in its address, and a field marked again for holding
# one, in the tab the value was typed into alone, where scripts stay off (``_kept_out``, which the session holds by
# tab, and ``Tab.filled_values``); the rest only puts the value's mask where the value stands. A new place, or one that
# reads the values of every tab, is a line here, which a review reads against that rule.
DECIDES_BY_A_VALUE: dict[str, tuple[str, ...]] = {
    # A request is refused for carrying a value in its address: the values of the tab it comes from (``_kept_out_of``).
    "browser/session.py:BrowserSession._judge": ("in_address",),
    "browser/session.py:BrowserSession._kept_out_of": ("_kept_out", "covers"),
    # A field is marked again for holding a value: the values of the tab, in a document of their origin.
    "browser/driver.py:remark_held": ("covers",),
    # The words of a log line are made without the values of every tab (``_shown``), and the words a run reads of a
    # refused address without those of any chat but its own (``_shown_to``): a mask, nothing is decided by it.
    "browser/session.py:BrowserSession._shown": ("_vault_secrets",),
    "browser/session.py:BrowserSession._shown_to": ("_vault_secrets",),
    "browser/session.py:BrowserSession._words_of": ("in_address",),
    "browser/session.py:BrowserSession._named": ("in_address",),
    # Where the values are kept and let go of.
    "browser/session.py:BrowserSession.__init__": ("_kept_out", "_vault_secrets"),
    "browser/session.py:BrowserSession._forget_tabs": ("_kept_out",),
    "browser/session.py:BrowserSession._let_go": ("_kept_out",),
    "browser/session.py:BrowserSession._on_page_closed": ("_kept_out",),
    "browser/session.py:BrowserSession.fill_from_vault.typing": ("_kept_out", "_vault_secrets"),
}


def _places_of_a_value() -> dict[str, tuple[str, ...]]:
    """The functions of the source that call ``in_address`` or ``covers``, or read ``_kept_out`` or ``_vault_secrets``,
    with which of the four each does."""
    found: dict[str, set[str]] = {}
    wanted: frozenset[str] = frozenset({"in_address", "covers", "_kept_out", "_vault_secrets"})

    class Reader(ast.NodeVisitor):
        def __init__(self, module: str) -> None:
            self.module: str = module
            self.inside: list[str] = []

        def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
            self.inside.append(node.name)
            self.generic_visit(node)
            self.inside.pop()

        visit_FunctionDef = _enter
        visit_AsyncFunctionDef = _enter
        visit_ClassDef = _enter

        def _note(self, name: str) -> None:
            # ``covers`` means a held value's origin in the browser's code (a rule of the owner's covers a step).
            if name == "covers" and not self.module.startswith("browser/"):
                return
            if name in wanted and self.inside:
                found.setdefault(f"{self.module}:{'.'.join(self.inside)}", set()).add(name)

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name):
                self._note(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                self._note(node.func.attr)
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            self._note(node.attr)
            self.generic_visit(node)

    for path in sorted(SOURCE.rglob("*.py")):
        Reader(str(path.relative_to(SOURCE))).visit(ast.parse(path.read_text(encoding="utf-8")))
    return {where: tuple(sorted(names)) for where, names in found.items()}


def test_a_request_is_refused_and_a_field_marked_for_a_value_only_where_it_is_held_to_its_tab() -> None:
    assert _places_of_a_value() == {where: tuple(sorted(names)) for where, names in DECIDES_BY_A_VALUE.items()}

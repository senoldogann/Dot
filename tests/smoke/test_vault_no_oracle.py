"""No tool, route or pipeline tells a run whether a guess is the password a vault filled (item 621, rounds 5 and 7).

A value filled into a page is a mask and no more: a run never saw it, and a refusal, a block or another outcome that
depends on whether a guess is the value tells whoever can ask the run the value, one guess at a time (a tainted run
found one in 13 calls). The review found it in ``memory_save`` after the check of item 525 had been closed, so this
test names no tool: it fills a login, then for every tool, route and pipeline whose outcome can depend on a secret Dot
holds it tries the password and a word of the same shape, and the two must end the same way. A control: a key of the
owner's env file ends as a refusal in each of them, so that the test finds what it looks for.

What differs is the text, which has the value masked where it stands (a note, a prompt, a file): that is the limit the
safety page states (what a run can still guess) and is tested here as a mask, not as an outcome.

Round 7 adds the rule these tests keep: a check that decides by a filled value is an oracle wherever the run can act and
watch, so Dot decides by a value only in the documents of the tab it was typed into, where no script of the run can run,
and everywhere else by kind or not at all. The last tests are for the size of a note and the owner's rules, which were
decided on the masked text; the request guard of the browser, which has the same rule, is tried against a script of the
run's own in ``test_vault_items_fill.py``, where the sites and the Chrome are.
"""

import asyncio
import dataclasses
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
import pytest_asyncio
from test_harness import GUIDANCE
from test_harness import _daemon as harness_daemon
from test_harness import _Recorder as HarnessRecorder
from test_harness import _run as harness_run
from test_harness_roles import _FINISH as ROLES_FINISH
from test_harness_roles import _run as roles_run
from test_harness_roles import _session as roles_session
from test_harness_roles import _turn as roles_turn
from test_lessons import NOTES, _config, _home, _lessons, _stumbles
from test_lessons import _run as lessons_run
from test_owner_gate import _session as owner_session
from test_vault_items_fill import _call, _context
from vault_stand_ins import MemorySecretStore

from dotagent import secrets
from dotagent.agent.executor import execute_calls
from dotagent.agent.lessons import propose_lesson, run_lessons
from dotagent.agent.manager import RunManager
from dotagent.agent.reactions import _holds_a_secret
from dotagent.agent.reflection import _describe_owner
from dotagent.agent.review import PROPOSE_TOOL
from dotagent.agent.skill_proposals import propose_rewrite
from dotagent.config import load_config, load_default_config
from dotagent.core.errors import InvalidValueError, NotFoundError, PolicyDenied
from dotagent.core.paths import env_file_path
from dotagent.core.types import JsonObject
from dotagent.evals.runner import eval_environment, scratch_home
from dotagent.harness.propose import propose_from_run
from dotagent.harness.service import given_text
from dotagent.llm.types import ToolCall
from dotagent.memory.service import OwnerSection, owner_prompt_lines, owner_section, save_memory, set_owner_section
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import known_secrets, write_env_value
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import ToolRegistry
from dotagent.tools.shell import read_login_env
from dotagent.vault.items import LoginDraft, VaultItems

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "no-oracle-token"
# The password the vault fills, a word of the same shape that is not, and a key of the owner's env file.
RIGHT: str = "S3cret-Passw0rd!"
WRONG: str = "Guess-Passw0rd-42"
ENV_KEY: str = "correct-horse-battery-staple"


@pytest.fixture(scope="module")
def login_env() -> dict[str, str]:
    return asyncio.run(read_login_env(load_default_config().shell.login_env_timeout_s, ()))


@pytest.fixture(autouse=True)
def _nothing_filled_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """What was filled into a page is what this process remembers: each test starts with nothing filled."""
    monkeypatch.setattr(secrets, "_filled", frozenset())


@dataclasses.dataclass(frozen=True)
class _Lab:
    runtime: Runtime
    api: httpx.AsyncClient


@pytest_asyncio.fixture(loop_scope="module")
async def lab(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Lab]:
    """A runtime whose vault keeps its secrets in a dictionary and whose notes are searched by their words, its API,
    and a key in the env file of its home."""
    home = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[memory]\nembed_enabled = false\n", encoding="utf-8"
    )
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    write_env_value(env_file_path(), "SHOP_SERVICE_KEY", ENV_KEY)
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    runtime: Runtime = dataclasses.replace(built, vault_items=VaultItems(built.store, MemorySecretStore()))
    manager: RunManager = RunManager(runtime)
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(context)),
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as api:
            yield _Lab(runtime, api)
    finally:
        await manager.shutdown()
        await close_runtime(built)


async def _fill_a_login(lab: _Lab) -> None:
    """A run fills a login of the vault: the daemon reads its password from the Keychain, as the tool does after the
    owner's yes, and notes it among the secrets it masks."""
    login = await lab.runtime.vault_items.create(
        LoginDraft(kind="login", website="example.com", username="ada@example.com", password=RIGHT)  # type: ignore[arg-type]
    )
    await lab.runtime.vault_items.login_for_fill(login.id)
    assert RIGHT in known_secrets(filled=True) and RIGHT not in known_secrets(filled=False)


# ----- the paths: each takes a guess and says how it ended ---------------------------------------------------------


async def _memory_save(lab: _Lab, guess: str) -> str:
    ctx, gate = await _context(lab.runtime, "yolo")
    said = {"content": f"The shop asked for {guess} as its password.", "tags": []}
    return "refused" if (await _call(ctx, gate, "memory_save", said)).is_error else "saved"


async def _memory_update(lab: _Lab, guess: str) -> str:
    saved = await save_memory(
        lab.runtime.memory, f"The shop closes at nine ({time.monotonic_ns()}).", (), None, None, None, False
    )
    ctx, gate = await _context(lab.runtime, "yolo")
    said = {"memory_id": saved.record.id, "content": f"The shop asked for {guess} as its password.", "tags": []}
    return "refused" if (await _call(ctx, gate, "memory_update", said)).is_error else "saved"


async def _pin(lab: _Lab, guess: str) -> str:
    return "refused" if await _holds_a_secret(f"Remember that the shop wants {guess} for the login.") else "pinned"


async def _lesson_of_the_owner(lab: _Lab, guess: str) -> str:
    ctx, _ = await _context(lab.runtime, "yolo")
    words = f"Before paying in the shop, check the total twice; {guess} is not a thing to say aloud."
    try:
        await propose_lesson(lab.runtime, asyncio.Lock(), ctx.run_id, words)
    except InvalidValueError:
        return "refused"
    return "proposed"


async def _words_for_a_skill(lab: _Lab, guess: str) -> str:
    """The owner's words for a change of a skill that is not there: refused for a secret in them before the skill is
    looked for, else not found."""
    try:
        await propose_rewrite(lab.runtime, "no-such-skill", f"Say that the shop wants {guess}.", time.time())
    except InvalidValueError:
        return "refused"
    except NotFoundError:
        return "went on"
    return "went on"


async def _harness_written(lab: _Lab, guess: str) -> str:
    sent = await lab.api.post(
        "/api/harness",
        json={"habits": f"- the shop wants {guess}.", "tool_guidance": None, "summary": "A habit", "adopt": True},
    )
    return {200: "written", 422: "refused"}[sent.status_code]


async def _stored_version(lab: _Lab, guess: str) -> str:
    """A version in the store as an older Dot, or another hand, left it: the gate was never asked of it."""
    version = await lab.runtime.store.create_harness_version(
        None, "owner", "old", f"- the shop wants {guess}.", GUIDANCE, ""
    )
    return version.id


async def _harness_adopted(lab: _Lab, guess: str) -> str:
    adopted = await lab.api.post(f"/api/harness/{await _stored_version(lab, guess)}/adopt")
    return {200: "adopted", 422: "refused"}[adopted.status_code]


async def _harness_given(lab: _Lab, guess: str) -> str:
    version_id: str = await _stored_version(lab, guess)
    await lab.runtime.store.adopt_harness(version_id, time.time())
    view = (await lab.api.get("/api/harness")).json()
    return "refused" if version_id in view["refused"] else "given"


async def _made_agent(lab: _Lab, guess: str) -> str:
    """An agent of the owner's whose instructions hold ``guess``: its id."""
    made = await lab.api.post(
        "/api/agents",
        json={
            "name": "Shopper",
            "role": "",
            "instructions": f"Sign in to the shop with {guess}.",
            "model": "",
            "effort": None,
            "approvals": None,
        },
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


async def _agent_exported(lab: _Lab, guess: str) -> str:
    exported = await lab.api.get(f"/api/agents/{await _made_agent(lab, guess)}/export")
    return {200: "exported", 422: "refused"}[exported.status_code]


async def _tainted_message(lab: _Lab, guess: str) -> str:
    """A run that read outside content sends a message whose words hold the guess (item 525): a send of any kind."""
    _, gate = await _context(lab.runtime, "yolo")
    gate.taint()
    info: ActionInfo = ActionInfo(
        tool="mail_send",
        kind="message",
        risk="high",
        summary="Send a message",
        target="ada@example.com",
        flags=("message_send",),
    )
    try:
        await gate._refuse_carried_secret(info, {"body": f"The password is {guess}."})
    except PolicyDenied:
        return "refused"
    return "sent"


PATHS: dict[str, Callable[[_Lab, str], Awaitable[str]]] = {
    "memory_save": _memory_save,
    "memory_update": _memory_update,
    "a pin": _pin,
    "a lesson of the owner's words": _lesson_of_the_owner,
    "the owner's words for a skill": _words_for_a_skill,
    "a version of the harness written": _harness_written,
    "a version of the harness adopted": _harness_adopted,
    "a version of the harness given to a run": _harness_given,
    "an agent exported": _agent_exported,
    "a message of a run that read outside content": _tainted_message,
}


@pytest.mark.parametrize("path", PATHS)
async def test_the_password_and_a_word_of_its_shape_end_the_same_way_in_every_path_and_a_key_of_the_owner_ends_refused(
    lab: _Lab, path: str
) -> None:
    await _fill_a_login(lab)
    ends = PATHS[path]
    right, wrong = await ends(lab, RIGHT), await ends(lab, WRONG)
    assert right == wrong, f"{path}: the password ended {right!r}, a word of its shape {wrong!r}"
    assert right != "refused", f"{path}: a refusal by the password would tell a run the password"
    assert await ends(lab, ENV_KEY) == "refused", f"{path}: the check does not find a key of the owner's"


async def test_a_login_of_1password_is_held_as_a_login_of_the_vault_is_and_no_path_tells_its_password_either(
    lab: _Lab,
) -> None:
    """The tool that fills a login of 1Password notes the password as it fills it (``_login_secret``): after that the
    two sources are one list, and the note the review found refused is the same for both."""
    secrets.hold_filled(RIGHT)
    assert RIGHT in known_secrets(filled=True) and RIGHT not in known_secrets(filled=False)
    assert await _memory_save(lab, RIGHT) == await _memory_save(lab, WRONG) == "saved"
    assert await _harness_given(lab, RIGHT) == await _harness_given(lab, WRONG) == "given"


# ----- what is stored or shown has the value masked ----------------------------------------------------------------


async def test_a_note_that_holds_the_password_is_saved_with_it_masked_and_one_that_holds_a_word_of_its_shape_is_not(
    lab: _Lab,
) -> None:
    await _fill_a_login(lab)
    ctx, gate = await _context(lab.runtime, "yolo")
    said = f"The shop asked for {RIGHT} as its password."
    for guess in (RIGHT, WRONG):
        result = await _call(ctx, gate, "memory_save", {"content": said.replace(RIGHT, guess), "tags": []})
        assert not result.is_error and RIGHT not in result.text
    notes = [note.content for note in await lab.runtime.store.list_memories(10, 0)]
    assert sorted(notes) == sorted([said.replace(RIGHT, "***"), said.replace(RIGHT, WRONG)])


async def test_a_harness_text_and_an_owner_line_that_hold_the_password_are_given_to_a_run_with_it_masked(
    lab: _Lab,
) -> None:
    await _fill_a_login(lab)
    version = await lab.runtime.store.get_harness_version(await _stored_version(lab, RIGHT))
    given = given_text(version, known_secrets(filled=False))
    assert given.habits == "- the shop wants ***." and given.tool_guidance == GUIDANCE
    section = OwnerSection(dot_lines=(f"Signs in with {RIGHT}.", "Works late."), owner_lines=(), updated_at=1.0)
    assert owner_prompt_lines(section, known_secrets(filled=False)) == ("Signs in with ***.", "Works late.")


async def test_an_agent_that_holds_the_password_is_exported_with_it_masked(lab: _Lab) -> None:
    await _fill_a_login(lab)
    exported = await lab.api.get(f"/api/agents/{await _made_agent(lab, RIGHT)}/export")
    assert exported.status_code == 200 and RIGHT not in exported.text
    assert exported.json()["instructions"] == "Sign in to the shop with ***."


# ----- the pipelines of a run: its lessons, the harness it proposes, the owner section, its prompt -----------------


async def _drawn(login_env: dict[str, str], guess: str) -> str:
    """The lesson the review model writes of a run that stumbled, holding ``guess``: what became of it on the run."""
    secrets.hold_filled(RIGHT)
    lesson = f"Never type {guess} for the shop, Dot fills the login in and the page does not need it said."
    async with _home(_config("propose"), login_env, [*_stumbles(NOTES, ()), _lessons(lesson)]) as home:
        write_env_value(env_file_path(), "SHOP_SERVICE_KEY", ENV_KEY)
        run = await lessons_run(home)
        shown = await run_lessons(home.runtime.store, run.id)
    return "refused" if shown is None or not shown.lessons else shown.lessons[0].state


async def test_a_lesson_the_review_model_writes_is_kept_alike_for_the_password_and_a_word_of_its_shape(
    login_env: dict[str, str],
) -> None:
    right, wrong = await _drawn(login_env, RIGHT), await _drawn(login_env, WRONG)
    assert right == wrong == "proposed"
    assert await _drawn(login_env, ENV_KEY) == "refused"


async def _proposed(login_env: dict[str, str], guess: str) -> str:
    """The proposer of the harness writes a habit that holds ``guess``: a candidate, or none when the check refused the
    reply and the model, asked again, kept the harness."""
    secrets.hold_filled(RIGHT)
    habit = f"- when a page asks for {guess}, say that you cannot type it."
    written = {
        "keep": False,
        "habits": habit,
        "tool_guidance": None,
        "moments": ["step 1 met a password"],
        "summary": "s",
    }
    kept = {"keep": True, "habits": None, "tool_guidance": None, "moments": [], "summary": "k"}
    turns = [ROLES_FINISH, roles_turn(PROPOSE_TOOL, written), roles_turn(PROPOSE_TOOL, kept)]
    async with roles_session(login_env, turns) as (runtime, manager):
        write_env_value(env_file_path(), "SHOP_SERVICE_KEY", ENV_KEY)
        run = await roles_run(manager)
        candidate = await propose_from_run(runtime, run.id, "invariant")
    return "refused" if candidate is None else "written"


async def test_a_habit_the_proposer_of_the_harness_writes_is_kept_alike_for_the_password_and_a_word_of_its_shape(
    login_env: dict[str, str],
) -> None:
    right, wrong = await _proposed(login_env, RIGHT), await _proposed(login_env, WRONG)
    assert right == wrong == "written"
    assert await _proposed(login_env, ENV_KEY) == "refused"


async def _owner_line(login_env: dict[str, str], guess: str) -> str:
    secrets.hold_filled(RIGHT)
    async with owner_session(login_env, [f"Signs in to the shop with {guess}."]) as (runtime, run):
        write_env_value(env_file_path(), "SHOP_SERVICE_KEY", ENV_KEY)
        old = OwnerSection(dot_lines=("An old line.",), owner_lines=(), updated_at=1.0)
        await set_owner_section(runtime.store, old)
        await _describe_owner(runtime, [run], 2.0)
        section = await owner_section(runtime.store)
    return "refused" if section.dot_lines == old.dot_lines else "kept"


async def test_a_line_of_the_owner_section_the_review_model_writes_is_kept_alike_for_the_password_and_its_shape(
    login_env: dict[str, str],
) -> None:
    right, wrong = await _owner_line(login_env, RIGHT), await _owner_line(login_env, WRONG)
    assert right == wrong == "kept"
    assert await _owner_line(login_env, ENV_KEY) == "refused"


async def _prompted(login_env: dict[str, str], guess: str) -> tuple[str, str]:
    """A run starts with a version of the harness and a line of the owner section that hold ``guess``: whether its
    first prompt takes them, and that prompt."""
    secrets.hold_filled(RIGHT)
    recorder: HarnessRecorder = HarnessRecorder([ROLES_FINISH])
    with scratch_home() as home, eval_environment(home, {}):
        write_env_value(env_file_path(), "SHOP_SERVICE_KEY", ENV_KEY)
        async with harness_daemon(login_env, recorder) as (runtime, manager, _):
            stored = await runtime.store.create_harness_version(
                None, "owner", "old", f"- the shop wants {guess}.", GUIDANCE, ""
            )
            await runtime.store.adopt_harness(stored.id, time.time())
            line = OwnerSection(dot_lines=(f"Signs in with {guess}.", "Works late."), owner_lines=(), updated_at=1.0)
            await set_owner_section(runtime.store, line)
            await harness_run(manager)
    prompt: str = recorder.prompts[0]
    takes: bool = "# Habits" in prompt and "the shop wants" in prompt and "Signs in with" in prompt
    return ("given" if takes else "refused"), prompt


async def test_the_prompt_of_a_run_takes_a_harness_text_and_an_owner_line_alike_for_the_password_and_its_shape(
    login_env: dict[str, str],
) -> None:
    (right, said), (wrong, _) = await _prompted(login_env, RIGHT), await _prompted(login_env, WRONG)
    assert right == wrong == "given"
    assert RIGHT not in said, "the model is never given the password"
    assert "the shop wants ***." in said and "Signs in with ***." in said
    assert (await _prompted(login_env, ENV_KEY))[0] == "refused"


# ----- what is decided is of the text as it was written -------------------------------------------------------------
#
# A mask is a text, and a decision taken on the masked text differs from one taken on the written text exactly when a
# word is the password. The size of a note and what the owner's rules ask were both decided after the mask (round 6).


async def test_a_note_is_judged_by_its_size_as_written_alike_for_the_password_and_a_word_of_its_shape(
    lab: _Lab,
) -> None:
    """A note one character over the limit is refused, whether the extra characters are the password or any other word:
    the mask is shorter than the password, so judged after it the note with the password fitted and the note with a word
    of its shape did not (608 characters were saved with the password and refused with a wrong word). A note at the
    limit is saved either way, with the password masked."""
    await _fill_a_login(lab)
    ctx, gate = await _context(lab.runtime, "yolo")
    limit: int = lab.runtime.memory.config.max_chars
    kept = await save_memory(lab.runtime.memory, "The shop opens at nine.", (), None, None, None, False)

    def written(guess: str, size: int) -> str:
        return f"{guess} " + "x" * (size - len(guess) - 1)

    for tool in ("memory_save", "memory_update"):
        for size, saves in ((limit + 1, False), (limit, True)):
            ended: dict[str, tuple[bool, str]] = {}
            for guess in (RIGHT, WRONG):
                arguments = {"content": written(guess, size), "tags": []}
                if tool == "memory_update":
                    arguments["memory_id"] = kept.record.id
                try:
                    ended[guess] = (True, (await _call(ctx, gate, tool, arguments)).text)
                except InvalidValueError as refused:
                    ended[guess] = (False, str(refused))
            assert ended[RIGHT][0] is ended[WRONG][0] is saves, (tool, size, ended)
            if not saves:
                assert ended[RIGHT][1] == ended[WRONG][1], "the refusal says the same of both"
            assert RIGHT not in ended[RIGHT][1]
    assert not any(RIGHT in note.content for note in await lab.runtime.store.list_memories(50, 0))


def _opens_nothing(registry: ToolRegistry) -> ToolRegistry:
    """The registry with ``browser_open`` classified and masked as it is, and opening no page: the test is of what is
    asked before a call runs, which is the executor's, the gate's and the policy's."""
    real: RegisteredTool = registry.tools["browser_open"]

    async def prepare(arguments: JsonObject, ctx: ToolContext) -> PreparedCall:
        call: PreparedCall = await real.prepare(arguments, ctx)

        async def run(context: ToolContext) -> ToolResult:
            return ok("Opened.")

        return dataclasses.replace(call, run=run)

    return ToolRegistry({**registry.tools, "browser_open": dataclasses.replace(real, prepare=prepare)})


async def _asked_about_opening(lab: _Lab, address: str, filled: bool) -> bool:
    """Whether a chat whose approvals ask is asked before it opens ``address``, under the owner's rule, when the call
    is made as the executor makes it; ``filled``: a login was filled into a page of the chat, so the browser tools take
    the password out of the step they show."""
    ctx, gate = await _context(lab.runtime, "ask")
    if filled:
        lab.runtime.browser._vault_values[ctx.thread_id] = {RIGHT}
    call: ToolCall = ToolCall(id="call-1", name="browser_open", arguments={"url": address})
    running = asyncio.create_task(
        execute_calls([call], _opens_nothing(lab.runtime.registry), {}, ctx, gate, 1, lambda outcome: None)
    )
    async with asyncio.timeout(15):
        while not running.done():
            pending = [
                one
                for one in await lab.runtime.store.list_approvals(None, 50)
                if one.run_id == ctx.run_id and one.status == "pending"
            ]
            if pending:
                assert RIGHT not in pending[0].summary, "the question shows the step masked"
                await lab.runtime.approvals.resolve(pending[0].id, True, UI_SOURCE, "")
                (outcome,) = await running
                assert not outcome.result.is_error, outcome.result.text
                return True
            await asyncio.sleep(0.02)
    (outcome,) = await running
    assert not outcome.result.is_error, outcome.result.text
    return False


async def test_an_owners_rule_asks_about_an_address_alike_for_the_password_and_a_word_of_its_shape(lab: _Lab) -> None:
    """The browser tools show a step with the values a login filled masked, and the policy matched the owner's rules
    against what was shown: a rule like `ask before an address with a long value in its query` asked for a wrong word
    and let the password through, which told a run whether its guess was right. The rule is matched against the step as
    it was written; the step is still shown, asked about and recorded masked."""
    await _fill_a_login(lab)
    address: str = "https://shop.example.com/probe?c={}"
    rule = await lab.runtime.store.add_rule(
        "ask", "browser_open", r"[?&]\w+=\S{12,}", "ask before an address with a long value in its query"
    )
    try:
        # The control, in a chat that filled nothing: the rule fits both words, so the test finds what it looks for.
        assert await _asked_about_opening(lab, address.format(RIGHT), filled=False)
        assert await _asked_about_opening(lab, address.format(WRONG), filled=False)
        # In a chat whose page was filled with the password the two are one decision, and the owner is asked in both.
        assert await _asked_about_opening(lab, address.format(RIGHT), filled=True)
        assert await _asked_about_opening(lab, address.format(WRONG), filled=True)
    finally:
        await lab.runtime.store.delete_rule(rule.id)
    assert not await _asked_about_opening(lab, address.format(RIGHT), filled=True), "without the rule nothing asks"

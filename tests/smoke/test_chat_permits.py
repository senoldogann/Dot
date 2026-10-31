"""The owner's "don't ask again in this chat" (item 866). On a real server and a scripted model, in yolo in a chat the
owner opened (where mail asks them, item 860): their yes with the box ticked on a mail lets the same line go unasked
again, recorded as answered from that yes; a line to another recipient is asked; another chat asks again. On the gate: a
yes of the kind answers in the chat and not in another; money, a hand-off and a step of a chat of Dot's own initiative
are never answered from it, and the route refuses the box on such a card. The mailers are at paths that are not there:
every command fails at once and nothing is sent."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.authorization import Authorization
from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.manager import RunManager
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig, load_config
from dotagent.core.errors import ApprovalRejected
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.policy.chat_permits import CHAT_PERMIT_DECIDER, key_of_action, may_remember, permit_key
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio

TOKEN: str = "chat-permits-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 30.0
FIRST: str = "/nonexistent/dot-item-866/sendmail press-one@example.invalid"
SECOND: str = "/nonexistent/dot-item-866/sendmail press-two@example.invalid"


def _shell(command: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": command}),))


_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Sent.", "success": True}),)
)


@pytest_asyncio.fixture
async def served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Runtime, RunManager, httpx.AsyncClient]]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n\n'
        "[policy]\nyolo_taint_check = false\n",
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    turns: list[ScriptedTurn] = [_shell(FIRST), _shell(FIRST), _shell(SECOND), _FINISH, _shell(FIRST), _FINISH]
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
    manager: RunManager = RunManager(runtime)
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as client:
            yield runtime, manager, client
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)


async def _card(client: "httpx.AsyncClient | _Local", seen: set[str]) -> dict[str, object]:
    async with asyncio.timeout(_WAIT_S):
        while True:
            for found in (await client.get("/api/approvals", params={"status": "pending", "limit": 10})).json():
                if found["id"] not in seen:
                    seen.add(found["id"])
                    return found
            await asyncio.sleep(0.05)


async def test_a_yes_with_the_box_lets_the_steps_of_its_kind_go_in_that_chat_alone(
    served: tuple[Runtime, RunManager, httpx.AsyncClient],
) -> None:
    runtime, manager, client = served
    started = await client.post("/api/runs", json={"goal": "Send the five press mails", "approvals": "yolo"})
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    seen: set[str] = set()
    first = await _card(client, seen)
    assert "sendmail" in str(first["summary"])
    given = await client.post(f"/api/approvals/{first['id']}/decision", json={"approve": True, "remember": True})
    assert given.status_code == 200, given.text
    # The same line again goes unasked; another line (another recipient) is another step, asked, and refused here.
    other = await _card(client, seen)
    assert "press-two" in str(other["summary"])
    await client.post(f"/api/approvals/{other['id']}/decision", json={"approve": False})
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(run_id)
    records: list[ApprovalRecord] = [r for r in await runtime.store.list_approvals(None, 20) if r.run_id == run_id]
    answered: list[ApprovalRecord] = [r for r in records if r.decided_by == CHAT_PERMIT_DECIDER]
    assert len(answered) == 1 and "press-one" in answered[0].summary
    assert first["id"] in answered[0].note
    # Another chat asks again for the same kind.
    again = await client.post("/api/runs", json={"goal": "Send one more", "approvals": "yolo"})
    elsewhere = await _card(client, seen)
    assert elsewhere["run_id"] == again.json()["id"] and "press-one" in str(elsewhere["summary"])
    await client.post(f"/api/approvals/{elsewhere['id']}/decision", json={"approve": False})
    async with asyncio.timeout(_WAIT_S):
        await manager.wait(again.json()["id"])


async def test_the_box_is_refused_on_a_card_that_may_not_hold(
    served: tuple[Runtime, RunManager, httpx.AsyncClient],
) -> None:
    runtime, _, client = served
    run: RunRecord = await runtime.store.create_run(
        (await runtime.store.create_thread("Pay", "ui")).id,
        "Pay",
        "task",
        "ollama:none",
        None,
        "ui",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    payment: ApprovalRecord = await runtime.store.create_approval(
        run.id, "browser_act", "click 'Pay now'", "high", {"kind": "browser_click", "flags": ["financial"]}
    )
    refused = await client.post(f"/api/approvals/{payment.id}/decision", json={"approve": True, "remember": True})
    assert refused.status_code == 422, refused.text
    assert (await runtime.store.get_approval(payment.id)).status == "pending"


async def test_the_kind_is_the_tool_its_flags_and_where_it_reaches() -> None:
    send = ActionInfo(
        tool="browser_act",
        kind="browser_click",
        risk="medium",
        summary="click f1e2960 'Send'",
        target="https://webmail.zone.fi/u/0/compose/1305",
        flags=("external_send", "message_send"),
    )
    same = send.model_copy(update={"target": "https://webmail.zone.fi/u/0/compose/1306", "summary": "click a1 'Send'"})
    assert key_of_action(send) == key_of_action(same)
    assert key_of_action(send) != key_of_action(send.model_copy(update={"target": "https://mail.example.invalid/c"}))
    assert key_of_action(send) != key_of_action(send.model_copy(update={"flags": ("external_send",)}))
    # A step that reaches a shell is its exact line in its folder (the pre-push review of item 866): another line, the
    # line in another folder, a script by path, an AppleScript or a typed line hold only for themselves.
    run = "/usr/sbin/sendmail a@b"
    assert permit_key("shell", "shell", ["message_send"], run, f"Run: {run} (sends)") == permit_key(
        "shell", "shell", ["message_send"], run, f"Run: {run} (sends)"
    )
    assert permit_key("shell", "shell", ["message_send"], run, f"Run: {run} (sends)") != permit_key(
        "shell", "shell", ["message_send"], "/usr/sbin/sendmail c@d", "Run: /usr/sbin/sendmail c@d (sends)"
    )
    assert permit_key("shell", "shell", [], "./publish.sh", "Run: ./publish.sh in /a (runs)") != permit_key(
        "shell", "shell", [], "./publish.sh", "Run: ./publish.sh in /b (runs)"
    )
    # Lines typed into a terminal, into Terminal from the desktop, or an AppleScript that reaches a shell are never
    # remembered: their record does not name their folder.
    for tool in ("terminal_send", "desktop_act", "apple_script"):
        assert not may_remember(["message_send"], "high", "preset", tool, "shell"), tool
    assert may_remember(["message_send"], "high", "preset", "shell", "shell")
    # An international name, and an address whose host cannot be read, hold for the exact address alone.
    assert permit_key("browser_act", "browser_click", [], "https://straße.example/a", "").endswith(
        "target:https://straße.example/a"
    )
    assert permit_key("browser_act", "browser_click", [], "https://[bad]/", "").endswith("target:https://[bad]/")
    # Nor a question a run's untrusted reading raised.
    assert may_remember(["external_send"], "high", "taint")
    # Money, a trade, a credential, a fill and Dot's own files are never remembered; nor a hand-off, a question, a
    # secret's card, or a question of Dot's own initiative.
    for flag in ("financial", "trade", "credential", "vault_fill", "self_change"):
        assert not may_remember(["message_send", flag], "high", "preset")
    for severity in ("handoff", "question", "secret"):
        assert not may_remember(["message_send"], severity, "preset")
    assert not may_remember(["message_send"], "high", "initiative")
    assert may_remember(["message_send", "external_send"], "high", "owner_asks")


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked: {request.action.summary}")


def _gate(runtime: Runtime, run: RunRecord, chat: str, owners_word: bool = False, approvals: str = "yolo") -> RunGate:
    return RunGate(
        runtime.store,
        runtime.bus,
        runtime.approvals,
        run.id,
        "task",
        "aggressive",
        PaceConfig(public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0),
        {},
        approvals,  # type: ignore[arg-type]
        _nobody,
        (),
        SiteLocks(),
        taint_check=False,
        owners_word=owners_word,
        owners_chat=not owners_word,
        chat_id=chat,
    )


async def test_a_permit_never_answers_money_nor_a_step_of_dots_own_initiative(
    served: tuple[Runtime, RunManager, httpx.AsyncClient],
) -> None:
    """Even with a permit of the step's kind stored in the chat, by any means: a payment is handed to the owner, and a
    message of a chat Dot opened on its own initiative is asked."""
    runtime, _, _ = served
    chat: str = (await runtime.store.create_thread("Pay and mail", "ui")).id
    run: RunRecord = await runtime.store.create_run(
        chat, "Pay", "task", "ollama:none", None, "ui", "medium", "standard", None, "yolo", False, None, None, None
    )
    payment = ActionInfo(
        tool="browser_act",
        kind="browser_click",
        risk="high",
        summary="click 'Pay now'",
        target="https://shop.example.invalid/checkout",
        flags=("financial",),
    )
    mail = ActionInfo(
        tool="apple_script",
        kind="app_control",
        risk="high",
        summary="send the mail",
        target="Mail",
        flags=("external_send", "message_send"),
    )
    for step in (payment, mail):
        await runtime.store.add_chat_permit(chat, key_of_action(step), "apr_planted")
    paying: asyncio.Task[Authorization] = asyncio.create_task(_gate(runtime, run, chat).authorize_recorded(payment))
    handed = await _card(_Local(runtime), set())
    assert handed["severity"] == "handoff"
    await runtime.approvals.resolve(str(handed["id"]), False, "test", "")
    with pytest.raises(ApprovalRejected):
        async with asyncio.timeout(_WAIT_S):
            await paying
    own: asyncio.Task[Authorization] = asyncio.create_task(
        _gate(runtime, run, chat, owners_word=True).authorize_recorded(mail)
    )
    asked = await _card(_Local(runtime), {str(handed["id"])})
    assert asked["tool"] == "apple_script"
    await runtime.approvals.resolve(str(asked["id"]), False, "test", "")
    with pytest.raises(ApprovalRejected):
        async with asyncio.timeout(_WAIT_S):
            await own
    # Without the initiative, the mail's permit answers it.
    done: Authorization = await _gate(runtime, run, chat).authorize_recorded(mail)
    assert done.decision.answered_by == "user"
    # A run that read outside content uses the chat's permit too, the owner's word of 2026-10-31 ("okumuş olsa da
    # sorma"), knowing what that content could plant within the kind.
    read_mail: RunGate = _gate(runtime, run, chat)
    read_mail.tainted = True
    assert (await read_mail.authorize_recorded(mail)).decision.answered_by == "user"
    # In auto the chat's yes answers before the review model would (the pre-push review of item 866): `_nobody`
    # fails the test if it is asked.
    reviewed: RunGate = _gate(runtime, run, chat, approvals="auto")
    reviewed.tainted = True
    assert (await reviewed.authorize_recorded(mail)).decision.answered_by == "user"


class _Local:
    """The approvals as the route lists them, read from the store, for a test with no client of its own."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    async def get(self, path: str, params: dict[str, object]) -> "_Answer":
        return _Answer([record.model_dump() for record in await self._runtime.store.list_approvals("pending", 10)])


class _Answer:
    def __init__(self, data: list[dict[str, object]]) -> None:
        self._data = data

    def json(self) -> list[dict[str, object]]:
        return self._data

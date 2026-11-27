"""What the tests of code mode share (items 1500 to 1503): a folder of a run's code, a start of the real interpreter in
the real sandbox with a handler that answers the bridge's calls by name, the same code started with no sandbox at all
(each attack test is held against it: an attack the control cannot make is no test of the sandbox), and the loop's own
path for a call over a gate and tools that are stand-ins."""

import asyncio
import dataclasses
import os
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from dotagent.agent.authorization import Authorization, StepDecision
from dotagent.agent.executor import ToolOutcome, _execute_one
from dotagent.codemode import install, runner, sandbox
from dotagent.codemode.bridge import BridgeAnswer
from dotagent.codemode.runner import Ended, Limits, RunPlan
from dotagent.codemode.sandbox import Interpreter, Layout
from dotagent.codemode.tool import build_code_run_tools
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ApprovalRejected, PolicyDenied
from dotagent.core.types import JsonObject
from dotagent.llm.types import ImageData, ToolCall, ToolSpec
from dotagent.policy.types import ActionInfo, ActionKind, Flag, Risk
from dotagent.store.models import EventRecord
from dotagent.tools.base import ALL_MODES, PreparedCall, RegisteredTool, ToolResult, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import ToolRegistry

type Answerer = Callable[[JsonObject], Awaitable[BridgeAnswer]]
type Said = Callable[[JsonObject], Awaitable[ToolResult]]


async def _nothing(_arguments: JsonObject) -> BridgeAnswer:
    return BridgeAnswer(ok=True, text="{}")


@dataclass
class Box:
    """One run's folders and every call the code made over its bridge. Its methods are blocking (each runs its own
    event loop), so that a test is a plain function; :meth:`run` is the coroutine for a test that needs to do other
    things while the code runs."""

    workspace: Path
    layout: Layout
    interpreter: Interpreter
    calls: list[tuple[str, JsonObject]] = field(default_factory=list)
    answers: dict[str, Answerer] = field(default_factory=dict)
    starts: int = 0

    async def handler(self, tool: str, arguments: JsonObject) -> BridgeAnswer:
        self.calls.append((tool, arguments))
        return await self.answers.get(tool, _nothing)(arguments)

    def says(self, tool: str, text: str, after_s: float = 0.0) -> None:
        """Make ``tool`` answer ``text``, ``after_s`` seconds after the call."""

        async def answer(_arguments: JsonObject) -> BridgeAnswer:
            await asyncio.sleep(after_s)
            return BridgeAnswer(ok=True, text=text)

        self.answers[tool] = answer

    def refuses(self, tool: str, words: str) -> None:
        """Make ``tool`` answer as a refused call is answered."""

        async def answer(_arguments: JsonObject) -> BridgeAnswer:
            return BridgeAnswer(ok=False, error=words, refused=True)

        self.answers[tool] = answer

    async def run(
        self,
        code: str,
        *,
        seconds: float = 20.0,
        memory_mb: int = 512,
        calls: int = 200,
        window_bytes: int = 8000,
        cancel: asyncio.Event | None = None,
    ) -> Ended:
        """Start ``code`` in the sandbox as :func:`dotagent.codemode.runner.run_code` does."""
        install.install(self.layout)
        self.starts += 1
        plan: RunPlan = RunPlan(
            self.interpreter,
            self.layout,
            code,
            Limits(seconds, memory_mb, calls, window_bytes),
            self.starts,
            "run_test",
        )
        return await runner.run_code(plan, self.handler, cancel or asyncio.Event())

    def start(self, code: str, **limits: float) -> Ended:
        """:meth:`run`, blocking."""
        return asyncio.run(
            self.run(
                code,
                seconds=limits.get("seconds", 20.0),
                memory_mb=int(limits.get("memory_mb", 512)),
                calls=int(limits.get("calls", 200)),
                window_bytes=int(limits.get("window_bytes", 8000)),
            )
        )

    def unguarded(self, code: str, env: Mapping[str, str] | None = None, timeout_s: float = 20.0) -> str:
        """What ``code`` prints when the same interpreter runs it with no sandbox (the control), in the scratch folder:
        ``never ended`` when it does not end within ``timeout_s``."""
        self.layout.scratch.mkdir(parents=True, exist_ok=True)
        try:
            done: subprocess.CompletedProcess[str] = subprocess.run(
                [str(self.interpreter.executable), "-I", "-S", "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=self.layout.scratch,
                env={"LANG": "en_US.UTF-8", **(env or {})},
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "never ended"
        return done.stdout + done.stderr


def box(tmp_path: Path) -> Box:
    """A run's folders under a throwaway workspace, by their real paths (the profile matches those)."""
    workspace: Path = Path(os.path.realpath(tmp_path)) / "workspace"
    workspace.mkdir()
    return Box(workspace, sandbox.layout_for(workspace, "run_code_test"), sandbox.current_interpreter())


# ----- The loop's own path for a call, over a gate and tools that are stand-ins (item 1501) -----


@dataclasses.dataclass
class Asked:
    """What the gate was asked: the step, the arguments the call carried, and whether the run was tainted then."""

    info: ActionInfo
    arguments: JsonObject | None
    tainted: bool


class FakeGate:
    """The gate of a run, as far as the executor uses it: it records every step it is asked about, in order, answers
    yes, and can be told to refuse the steps of a tool (as the policy or the owner refuses one), or to wait for the
    owner's answer first (``waits_for``)."""

    def __init__(self) -> None:
        self.asked: list[Asked] = []
        self.tainted: bool = False
        self.ever_tainted: bool = False
        self.refusals: dict[str, Exception] = {}
        self.waits_for: dict[str, asyncio.Event] = {}
        self.instructions: tuple[str, ...] = ()

    async def authorize_recorded(
        self, info: ActionInfo, arguments: JsonObject | None = None, weighed: ActionInfo | None = None
    ) -> Authorization:
        self.asked.append(Asked(info, arguments, self.tainted))
        waiting: asyncio.Event | None = self.waits_for.get(info.tool)
        if waiting is not None:
            await waiting.wait()
        refusal: Exception | None = self.refusals.get(info.tool)
        if refusal is not None:
            raise refusal
        decision: StepDecision = StepDecision(
            verdict="allow",
            source="preset",
            reason="a stand-in",
            rule_id=None,
            answered_by="policy",
            preset="aggressive",
            tainted=self.tainted,
        )
        return Authorization(note="", decision=decision)

    async def authorize(self, info: ActionInfo) -> str:
        return (await self.authorize_recorded(info)).note

    def add_instruction(self, text: str) -> None:
        self.instructions = (*self.instructions, text)

    async def wait_free(self) -> float:
        return 0.0

    def observe(self, info: ActionInfo) -> None:
        if info.kind in ("network", "browser_read", "mcp", "mcp_read"):
            self.taint()

    def taint(self) -> None:
        self.tainted = True
        self.ever_tainted = True

    def refuse(self, tool: str, words: str = "the policy refuses this") -> None:
        """Refuse every step of ``tool`` as the policy does."""
        self.refusals[tool] = PolicyDenied(words, {})

    def owner_says_no(self, tool: str, words: str = "the owner rejected this step") -> None:
        """Refuse every step of ``tool`` as the owner does when they say no."""
        self.refusals[tool] = ApprovalRejected(words, {})


class FakePeers:
    def conflict(self, *_arguments: object) -> None:
        return None

    def take(self, *_arguments: object) -> None:
        return None

    def saw(self, *_arguments: object) -> None:
        return None


class FakeBus:
    """Every event of the run, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, JsonObject]] = []
        self.narrating: asyncio.Lock = asyncio.Lock()

    async def emit(self, run_id: str, kind: str, data: JsonObject, _persist: bool = True) -> None:
        self.events.append((kind, data))

    def of(self, kind: str) -> list[JsonObject]:
        return [data for event_kind, data in self.events if event_kind == kind]


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[ImageData] = []

    async def save_images(self, images: tuple[ImageData, ...]) -> list[str]:
        self.saved.extend(images)
        return [f"image-{index}.png" for index, _ in enumerate(images)]

    async def events_of_kind(self, _run_id: str, _kind: str, _limit: int) -> list[EventRecord]:
        """The run's stored events of a kind: none (the narration lines of item 1976 read the newest)."""
        return []


def tool(
    name: str,
    said: Said | str,
    *,
    kind: ActionKind = "read",
    risk: Risk = "safe",
    flags: tuple[Flag, ...] = (),
    modes: frozenset[Any] = ALL_MODES,
    parameters: JsonObject | None = None,
    ran: list[JsonObject] | None = None,
) -> RegisteredTool:
    """A tool of the run that answers ``said`` (a text, or a coroutine of the arguments) and records its calls in
    ``ran``: it is classified as ``kind`` and ``risk``, as the real tool of that kind would be."""

    async def prepare(arguments: JsonObject, _ctx: ToolContext) -> PreparedCall:
        action: ActionInfo = ActionInfo(
            tool=name, kind=kind, risk=risk, summary=f"{name} {arguments}", target=name, flags=flags
        )

        async def run(_context: ToolContext) -> ToolResult:
            if ran is not None:
                ran.append(arguments)
            return ok(said) if isinstance(said, str) else await said(arguments)

        return PreparedCall(action=action, run=run)

    schema: JsonObject = parameters or {"type": "object", "properties": {}}
    return RegisteredTool(
        spec=ToolSpec(name=name, description=f"The stand-in {name}.", parameters=schema),
        modes=modes,
        parallel_safe=False,
        timeout_s=30.0,
        prepare=prepare,
    )


@dataclasses.dataclass
class Run:
    """The loop's own path for a call, over stand-ins: the registry, the context, the gate, the events."""

    registry: ToolRegistry
    ctx: ToolContext
    gate: FakeGate
    bus: FakeBus
    box: Box

    async def call(self, name: str, **arguments: object) -> ToolOutcome:
        """One call of the model's, as the executor makes it."""
        call: ToolCall = ToolCall(id="call_1", name=name, arguments=cast(JsonObject, arguments))
        return await _execute_one(call, self.registry, {}, self.ctx, cast(Any, self.gate))

    async def code(self, code: str, timeout_s: int | None = None) -> ToolOutcome:
        """A ``code_run`` of the model's with ``code``."""
        arguments: JsonObject = {"code": code, "title": "a test"}
        if timeout_s is not None:
            arguments["timeout_s"] = timeout_s
        return await self.call("code_run", **arguments)


def run(
    box_: Box,
    tools: list[RegisteredTool],
    *,
    mode: str = "task",
    config: AppConfig | None = None,
    names: frozenset[str] | None = None,
) -> Run:
    """A run of ``mode`` with ``tools`` and ``code_run``, its context made as the loop makes one."""
    everything: list[RegisteredTool] = [*build_code_run_tools(), *tools]
    registry: ToolRegistry = ToolRegistry(tools={entry.spec.name: entry for entry in everything})
    settings: AppConfig = config or load_default_config()
    # What a run's lessons would add is the loop's: it is not under test.
    settings = settings.model_copy(update={"agent": settings.agent.model_copy(update={"lessons": "off"})})
    bus: FakeBus = FakeBus()
    gate: FakeGate = FakeGate()
    runtime: Any = SimpleNamespace(
        bus=bus,
        store=FakeStore(),
        peers=FakePeers(),
        config=settings,
        registry=registry,
        protected=(),
        dot_paths=(),
    )
    context: ToolContext = ToolContext(
        runtime=runtime,
        browser=cast(Any, None),
        run_id="run_code_test",
        agent_id=None,
        thread_id="thread_code_test",
        mode=cast(Any, mode),
        approvals="ask",
        questions="leave",
        workspace=box_.workspace,
        gate=cast(Any, gate),
        cancel=asyncio.Event(),
        output_chars=12000,
        facts=cast(Any, lambda: None),
        tainted=lambda: gate.tainted,
        ever_tainted=lambda: gate.ever_tainted,
        run_tools=names if names is not None else frozenset(registry.tools),
    )
    return Run(registry, context, gate, bus, box_)

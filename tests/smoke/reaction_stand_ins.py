"""What the tests of the reactions (item 586) stand in for: a scripted model that a test holds at the step it wants, and
a runtime on a scratch home with its run manager and the service of the reactions. Nothing here reaches anything outside
this Mac."""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Collection, Sequence
from contextlib import asynccontextmanager

from dotagent.agent.gate import TAINT_EVENTS, tainted_after
from dotagent.agent.manager import RunManager
from dotagent.agent.reactions import Reactions
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import NO_PICKS, AgentRecord, EventRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import FINISH_TOOL

WAIT_S: float = 30.0
# How long the service gathers a burst of reactions in a test: long enough for two presses in a row to be one burst,
# short enough for a test.
BURST_S: float = 0.4
# How long a test lets a run rest to see that it does nothing.
REST_S: float = 0.6
PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
GOAL: str = "Check the shelves and tell me how many there are."
SAID: str = "I will check the shelves."
ANSWER: str = "The beds face south, with paths between them."
# What the owner's own message says when they put a "not right" on an answer.
THUMB_DOWN: str = (
    "My reaction 👎 to your answer above: that is not right. Reconsider it and change what is wrong; ask me if you "
    "cannot tell what."
)


def turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    """A reply of the model that calls one tool."""
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def says(text: str) -> ScriptedTurn:
    """A step that says something and then works: the message a reaction can be on, in the middle of a run."""
    return ScriptedTurn(text=text, calls=(ScriptedCall(tool="shell", arguments={"command": "echo shelf-42"}),))


def finish(answer: str) -> ScriptedTurn:
    """A reply of the model that ends the run with ``answer``."""
    return turn(FINISH_TOOL, {"answer": answer, "success": True})


class Gated:
    """The scripted model with its asks counted, and some of them held: ask ``n`` (from 1) rests until the test
    ``open``s it when ``n`` is among ``held``. An ask that a message gives up counts too, and consumes no turn."""

    def __init__(self, turns: Sequence[ScriptedTurn], held: Collection[int]) -> None:
        self._script: ScriptedProvider = ScriptedProvider(PROVIDER, turns)
        self._gates: dict[int, asyncio.Event] = {number: asyncio.Event() for number in held}
        self._started: defaultdict[int, asyncio.Event] = defaultdict(asyncio.Event)
        self.asks: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    def open(self, number: int) -> None:
        self._gates[number].set()

    async def started(self, number: int) -> None:
        """Wait until ask ``number`` was made."""
        async with asyncio.timeout(WAIT_S):
            await self._started[number].wait()

    def told(self, number: int) -> str:
        """What the user side of ask ``number`` held, as the model read it."""
        return "\n".join(message.content for message in self.asks[number - 1].messages if message.role == "user")

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asks.append(request)
        number: int = len(self.asks)
        self._started[number].set()
        gate: asyncio.Event | None = self._gates.get(number)
        if gate is not None:
            await gate.wait()
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


class Stage:
    """The model of tests that share a runtime: it plays the turns of the test in hand, which the test loads."""

    def __init__(self) -> None:
        self._model: Gated | None = None

    @property
    def name(self) -> str:
        return PROVIDER

    def load(self, turns: Sequence[ScriptedTurn], held: Collection[int] = ()) -> Gated:
        """Play ``turns`` from now on, the asks of ``held`` resting until the test opens them."""
        self._model = Gated(turns, held)
        return self._model

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        assert self._model is not None, "no test loaded the model"
        return await self._model.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await ScriptedProvider(PROVIDER, []).model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await ScriptedProvider(PROVIDER, []).list_models()

    async def aclose(self) -> None:
        return None


def reaction_config(**reactions: float | bool | str) -> AppConfig:
    """The configuration of an eval run, with the settings of the reactions it is given."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"reactions": config.reactions.model_copy(update=reactions)})


async def until(ready: Callable[[], bool]) -> None:
    """Wait until ``ready`` says so."""
    async with asyncio.timeout(WAIT_S):
        while True:
            if ready():
                return
            await asyncio.sleep(0.02)


class Session:
    """A runtime on a scratch home, its model, its runs and the service of the reactions."""

    def __init__(self, runtime: Runtime, manager: RunManager, reactions: Reactions, model: Gated) -> None:
        self.runtime: Runtime = runtime
        self.manager: RunManager = manager
        self.reactions: Reactions = reactions
        self.model: Gated = model

    async def start(self, thread_id: str | None = None) -> RunRecord:
        """A run of the owner's message in the chat ``thread_id`` (a new chat when ``None``), in yolo."""
        return await self.manager.start(
            GOAL, "task", SCRIPTED_MODEL, thread_id, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
        )

    async def agent(self) -> AgentRecord:
        """One of the owner's agents, which takes the configured model and approvals."""
        return await self.runtime.store.create_agent(
            "Reviewer", "Reads and reports", "Read what the owner points at.", "", None, None, [], None, None
        )

    async def start_agent(self, agent: AgentRecord, thread_id: str | None = None) -> RunRecord:
        """A run of ``agent`` in the chat ``thread_id`` (a new chat when ``None``), in yolo."""
        return await self.manager.start_for_agent(
            agent,
            GOAL,
            "task",
            SCRIPTED_MODEL,
            thread_id,
            UI_SOURCE,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
            NO_PICKS,
        )

    async def start_tainted(self, thread_id: str | None = None) -> RunRecord:
        """A run that starts as untrusted as a run a watcher starts for a mail: what a stranger wrote set it off. It is
        a run of the chat ``thread_id``, or of a chat of its own when that is ``None``."""
        if thread_id is None:
            thread: ThreadRecord = await self.runtime.store.create_thread("Watched", "watcher")
            thread_id = thread.id
        return await self.manager.start_alert(GOAL, f"{GOAL}\n\n(a mail)", thread_id)

    async def ended(self, run_id: str) -> RunRecord:
        async with asyncio.timeout(WAIT_S):
            return await self.manager.wait(run_id)

    async def events(self, run_id: str, kind: str) -> list[EventRecord]:
        return await self.runtime.store.events_of_kind(run_id, kind, 50)

    async def heard(self, run_id: str, kind: str, count: int) -> list[EventRecord]:
        """The events of ``kind`` of the run, once there are ``count`` of them."""
        async with asyncio.timeout(WAIT_S):
            while True:
                found: list[EventRecord] = await self.events(run_id, kind)
                if len(found) >= count:
                    return found
                await asyncio.sleep(0.02)

    async def held(self, run_id: str) -> None:
        """Wait until the run waits for the owner."""
        await until(lambda: self.manager.holding(run_id))

    async def tainted(self, run_id: str) -> bool:
        """Whether the run is tainted after its newest event of each kind that says so, as a run that goes on from it
        starts (:func:`tainted_after`)."""
        found: list[EventRecord] = [
            event for kind in TAINT_EVENTS if (event := await self.runtime.store.last_event(run_id, kind)) is not None
        ]
        return tainted_after(found)

    async def next_run(self, thread_id: str, after: Collection[str]) -> RunRecord:
        """The run of the chat that is not among ``after``, once there is one."""
        async with asyncio.timeout(WAIT_S):
            while True:
                found: list[RunRecord] = [
                    run for run in await self.runtime.store.list_runs(20, thread_id) if run.id not in after
                ]
                if found:
                    return found[0]
                await asyncio.sleep(0.05)


@asynccontextmanager
async def open_session(
    login_env: dict[str, str],
    turns: Sequence[ScriptedTurn],
    held: Collection[int] = (),
    **reactions: float | bool | str,
) -> AsyncIterator[Session]:
    """A scratch home, a runtime whose model plays ``turns`` (the asks of ``held`` rest until the test opens them), a
    run manager and the service of the reactions, which gathers a burst for ``BURST_S`` seconds."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(reaction_config(**reactions), login_env)
        try:
            model: Gated = Gated(turns, held)
            await runtime.router.register(PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            service: Reactions = Reactions(runtime, manager, burst_s=BURST_S)
            try:
                yield Session(runtime, manager, service, model)
            finally:
                await service.close()
                await manager.shutdown()
        finally:
            await close_runtime(runtime)

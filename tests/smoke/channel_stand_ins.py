"""What the tests of the channels (item 572) stand in for: the model, ``osascript`` that sends an iMessage, and
what macOS says of the permissions of the app that runs Dot. Nothing here reaches anything outside this Mac."""

import asyncio
import os
import threading
from collections import deque
from pathlib import Path

from dotagent.channels.service import Macos
from dotagent.connections.mac import AutomationState
from dotagent.core.errors import DesktopError
from dotagent.core.paths import failsafe_path
from dotagent.desktop.script import ScriptLanguage
from dotagent.drafts.scripts import SCRIPTS
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.tools.meta import FINISH_TOOL

PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def stop_file() -> Path:
    """The STOP file of the home a test runs in, which a test makes to engage the emergency stop (item 611). Never the
    owner's own: the home must be the pytest folder of the test's own, and the file is refused otherwise, before it is
    touched."""
    stop: Path = failsafe_path()
    assert stop.parent == Path(os.environ["DOT_HOME"]) and "pytest-of-" in str(stop), f"not a test's own home: {stop}"
    return stop


def finish(answer: str) -> ScriptedTurn:
    """A reply of the model that ends the run with ``answer``."""
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


class Replies:
    """The scripted model with the turns each test pushes, and a gate that holds its next call until a test opens it."""

    def __init__(self) -> None:
        self._turns: deque[ScriptedTurn] = deque()
        self.gate: asyncio.Event | None = None

    @property
    def name(self) -> str:
        return PROVIDER

    def push(self, *turns: ScriptedTurn) -> None:
        self._turns.extend(turns)

    def clear(self) -> None:
        self._turns.clear()
        self.gate = None

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if self.gate is not None:
            await self.gate.wait()
        return await ScriptedProvider(PROVIDER, [self._turns.popleft()]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await ScriptedProvider(PROVIDER, []).model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await ScriptedProvider(PROVIDER, []).list_models()

    async def aclose(self) -> None:
        return None


class Osascript:
    """Stands for ``osascript``: the calls it gets, and the error it raises while one is set."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.timeouts: list[float] = []
        self.error: DesktopError | None = None

    async def __call__(
        self,
        language: ScriptLanguage,
        script: str,
        argv: tuple[str, ...],
        confinement: tuple[str, ...],
        timeout_s: float,
    ) -> str:
        assert language == "AppleScript" and confinement == ()
        if self.error is not None:
            raise self.error
        self.calls.append((script, argv))
        self.timeouts.append(timeout_s)
        return ""

    @property
    def sent(self) -> list[tuple[str, str]]:
        """What was sent to Messages, as (handle, text); the script is the one drafts send with."""
        assert all(script == SCRIPTS["message"] for script, _ in self.calls)
        return [(argv[1], argv[0]) for _, argv in self.calls]


class FakeMacos(Macos):
    """What macOS says of the app that runs Dot, as the test sets it, and the questions it was asked."""

    def __init__(self) -> None:
        self.state: AutomationState = "allowed"
        self.name: str | None = "Dot"
        self.asked: list[str] = []
        # While it is set, a question waits for it (the owner has not answered yet): at most five seconds.
        self.gate: threading.Event | None = None

    def automation(self, bundle_id: str) -> AutomationState:
        return self.state

    def ask(self, bundle_id: str) -> AutomationState:
        self.asked.append(bundle_id)
        if self.gate is not None:
            self.gate.wait(5.0)
        return self.state

    def program(self) -> str | None:
        return self.name

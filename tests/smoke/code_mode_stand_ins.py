"""What the tests of code mode share (items 1500 to 1503): a folder of a run's code, a start of the real interpreter in
the real sandbox with a handler that answers the bridge's calls by name, and the same code started with no sandbox at
all, which is what each attack test is held against: an attack the control cannot make is no test of the sandbox."""

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotagent.codemode import install, runner, sandbox
from dotagent.codemode.bridge import BridgeAnswer
from dotagent.codemode.runner import Ended, Limits, RunPlan
from dotagent.codemode.sandbox import Interpreter, Layout
from dotagent.core.types import JsonObject

type Answerer = Callable[[JsonObject], Awaitable[BridgeAnswer]]


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
            self.interpreter, self.layout, code, Limits(seconds, memory_mb, calls, window_bytes), self.starts
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

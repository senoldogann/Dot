"""A throwaway Dot daemon for DotNext's end-to-end check from the simulator (wave N1-client).

It serves the real app (routes, stores, streams, the revision watch) on 127.0.0.1 in a home of its own, with a model
that replies by a script instead of a service, so no model is called and nothing leaves this Mac. The owner's words
choose the script:

- "helper: ...": the run starts a helper (``delegate``, wait false) that waits 90 s, then ends;
- "wait: ...": the run waits 2 min in ``wait_until`` (a steer meanwhile shows "read after this step", and ends it);
- "slow: ...": the reply takes 8 s (a run at work, for the queue);
- "approve: ...": the run touches a file in /tmp with the shell, which the supervised mode asks the owner about;
- "question: ...": the run asks the owner a question with options and waits for the answer (``ask_user``);
- "secret: ...": the run asks for a secret (``ask_secret``), which the phone types in a secure field;
- "tools: ...": the run reads two files and keeps a plan, so a finished run folds its steps;
- "fail: ...": the model fails, so the run fails with one line of cause;
- "write: ...": the run writes a file of its workspace, a change the inspector can undo;
- "draft: ...": the run drafts an email, a card with Send, Edit and Discard;
- "bg: ...": the run opens a terminal command in the background (``sleep 600``), which the inspector can stop;
- "stream: ...": four steps that each think aloud for about 4 s and read a file, then a long markdown answer, all
  streamed word by word at about 40 words a second, the pace of a hosted model (the chat's streaming is measured on it);
  "stream long: ..." the same with ten steps, a file of its own each;
- anything else: an answer at once, after a few words streamed.

The home's approval mode is ``supervised``, so every acting step asks (reads do not). With ``--seed`` the home also
holds a note the owner wrote and a run's proposed rewrite of it (item 1711), for Settings › Memory's Use and Drop. With
``--seed-activity`` it holds what the Activity tab shows (item 1826): runs two schedules did today (one done, one
failed), the decisions made for the owner in them, drafts that wait (one whose send failed), a thing a run put on Dot's
own list, and two versions of Dot's habits proposed by its learning (one written from a page others can write);
``e2e_activity.sh`` marks "while you were away" read from before them. With
``--model <ref>`` (``claude_code:haiku``) the runs go to that real model instead of the script, for a real-model run of
the phone's flows; the throwaway home has no key of its own, so only a provider that brings its own sign-in (the Claude
plan's ``claude`` command) answers.

Usage (the three folders are throwaway, ``mktemp -d``)::

    DOT_HOME=$t/home DOT_LOG_DIR=$t/logs DOT_CHROME_DIR=$t/chrome \\
        uv run python ios/Next/scripts/e2e_daemon.py 8765 [--seed] [--seed-activity]

It prints ``READY <port> <token>`` once it serves; a pairing code is made with
``curl -X POST -H "Authorization: Bearer <token>" http://127.0.0.1:<port>/api/phone/pairing``.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.llm.types import (
    ChatRequest,
    DeltaSink,
    ModelInfo,
    ModelReply,
    StreamDelta,
    ToolCall,
    Usage,
)
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.plans import UNSET_OPTIONS
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.thread_sync import watch_revisions
from dotagent.store.db import Store
from dotagent.store.models import (
    ApprovalRecord,
    DraftRecord,
    RunRecord,
    RunStatus,
    ScheduleRecord,
    ThreadRecord,
)

TOKEN: str = "dotnext-e2e-token"
MODEL: str = "ollama:scripted"
# The first line of the config this script writes: a home that holds it is this script's own, and may be used again.
CONFIG_MARK: str = "# A throwaway home of ios/Next/scripts/e2e_daemon.py"

# The files the "stream long:" script reads, one a step, all on every Mac.
LONG_READS: tuple[str, ...] = (
    "/etc/shells",
    "/etc/protocols",
    "/etc/services",
    "/etc/group",
    "/etc/networks",
    "/etc/paths",
    "/etc/manpaths",
    "/etc/zshrc",
)

# The "stream:" script's words: a thought of about 40 words said four times a step, and an answer of about 300 words.
THOUGHT: str = (
    "Enough for a status summary, but the money line schedule at 06:40 failed last night. Let me check why quickly, "
    "that is a detail worth one look: the log of the run, the schedule's last output and whether the key expired."
)
ANSWER: str = "\n\n".join(
    [
        "## Where things stand",
        "The morning brief ran at 07:00 and reached you. The money line schedule failed at 06:40 because the "
        "exchange answered with a timeout three times; the next run at 18:40 will try again on its own.",
        "- **Clients:** two new replies in the pipeline, both asking for a quote by Friday.\n"
        "- **Builds:** the iPhone app's last build passed its checks; one pull request waits on your look.\n"
        "- **Money:** nothing was spent today; the paper trades are flat.",
        "### What I would do next",
        "1. Answer the two clients with the approved price list.\n2. Look at the pull request together.\n"
        "3. Leave the money line alone until tonight's run shows whether the timeout repeats.",
        " ".join(["The rest of the day is quiet: no meeting, no ask waiting on you, the queue is empty."] * 8),
    ]
)


class ScriptProvider:
    """Replies by the owner's last words (module docstring); a tool's result is answered with a closing text."""

    def __init__(self) -> None:
        self._calls: int = 0

    @property
    def name(self) -> str:
        return "ollama"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self._calls += 1
        # The newest owner's words: the run adds its own notes as user messages that start with "[system note]".
        owners: list[int] = [
            i for i, m in enumerate(request.messages) if m.role == "user" and not m.content.startswith("[")
        ]
        at: int = owners[-1] if owners else -1
        latest: str = request.messages[at].content.strip() if at >= 0 else ""
        owner: str = latest
        # A tool already answered since the owner's words: the step is over, the run ends with a text.
        answered: bool = any(m.role == "tool" for m in request.messages[at + 1 :])
        steps: int = sum(1 for m in request.messages[at + 1 :] if m.role == "tool")
        calls: tuple[ToolCall, ...] = ()
        text: str = ""
        if any(tool.name == "goal_verdict" for tool in request.tools):
            # The goal check asks the same model: the work is taken as done.
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="goal_verdict",
                    arguments={"decision": "achieved", "reason": "The scripted step is done.", "unproven_steps": []},
                ),
            )
        elif latest.startswith("stream:"):
            return await self._stream(request, sink, steps, 4)
        elif latest.startswith("stream long:"):
            return await self._stream(request, sink, steps, 10)
        elif answered:
            text = f"Done: {owner[:60]}"
        elif latest.startswith("helper:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="delegate",
                    arguments={"goal": "wait for the test, then say done", "wait": False},
                ),
            )
        elif latest.startswith("wait for the test"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="wait_until",
                    arguments={"command": "test -f /nonexistent-dotnext", "every_s": 5, "within_minutes": 1.5},
                ),
            )
        elif latest.startswith("wait:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="wait_until",
                    arguments={"command": "test -f /nonexistent-dotnext", "every_s": 5, "within_minutes": 2},
                ),
            )
        elif latest.startswith("approve:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}", name="shell", arguments={"command": "touch /tmp/dotnext-e2e-approved"}
                ),
            )
        elif latest.startswith("question:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="ask_user",
                    arguments={
                        "question": "Which day suits you for the test?",
                        "options": [{"label": "Monday", "recommended": True}, {"label": "Tuesday"}],
                        "wait": True,
                    },
                ),
            )
        elif latest.startswith("secret:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="ask_secret",
                    arguments={
                        "name": "DOTNEXT_E2E_TOKEN",
                        "label": "A test token",
                        "why": "The e2e check gives a made-up value.",
                    },
                ),
            )
        elif latest.startswith("tools:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}a",
                    name="update_plan",
                    arguments={
                        "steps": [
                            {"text": "Read the notes", "status": "in_progress"},
                            {"text": "Answer", "status": "pending"},
                        ]
                    },
                ),
                ToolCall(id=f"call_{self._calls}b", name="read_file", arguments={"path": "/etc/hosts"}),
                ToolCall(
                    id=f"call_{self._calls}c", name="read_file", arguments={"path": "/nonexistent/dotnext-e2e.txt"}
                ),
            )
        elif latest.startswith("write:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="write_file",
                    arguments={"path": "dotnext-e2e-note.txt", "content": "Written by the e2e check.\n"},
                ),
            )
        elif latest.startswith("draft:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="draft_message",
                    arguments={
                        "channel": "mail",
                        "to": ["owner@example.com"],
                        "subject": "E2E draft",
                        "body": "Hello from the e2e check.",
                    },
                ),
            )
        elif latest.startswith("bg:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="terminal_open",
                    arguments={"command": "sleep 600", "background": True},
                ),
            )
        elif latest.startswith("fail:"):
            raise RuntimeError("the scripted model failed on purpose")
        elif latest.startswith("slow:"):
            await asyncio.sleep(8)
            text = f"Slowly: {owner[:60]}"
        else:
            text = f"Echo: {owner[:80]}"
        for word in text.split(" "):
            await sink(StreamDelta(kind="text", text=word + " "))
        return ModelReply(
            model=request.model,
            text=text,
            thinking="",
            tool_calls=calls,
            usage=Usage(input_tokens=100, output_tokens=10, cached_tokens=0),
            finish="tool_calls" if calls else "stop",
            raw_finish="tool_calls" if calls else "stop",
            raw=(),
        )

    async def _stream(self, request: ChatRequest, sink: DeltaSink, steps: int, reads: int) -> ModelReply:
        """One step of the "stream:" script: thinking and a file read for the first ``reads`` steps (4, or 12 for
        "stream long:", about 45 s, which outlasts the app's time away in the check of what an unseen run costs),
        then the long answer."""
        thinking: str = " ".join(THOUGHT for _ in range(4))
        for word in thinking.split(" "):
            await sink(StreamDelta(kind="thinking", text=word + " "))
            await asyncio.sleep(0.025)
        calls: tuple[ToolCall, ...] = ()
        text: str = ""
        if steps < reads:
            # A file of its own each step past the fourth: the loop stops a step repeated four times as no progress.
            path: str = "/etc/hosts" if steps < 4 else LONG_READS[steps % len(LONG_READS)]
            calls = (ToolCall(id=f"call_{self._calls}", name="read_file", arguments={"path": path}),)
        else:
            text = ANSWER
            for word in text.split(" "):
                await sink(StreamDelta(kind="text", text=word + " "))
                await asyncio.sleep(0.025)
        return ModelReply(
            model=request.model,
            text=text,
            thinking=thinking,
            tool_calls=calls,
            usage=Usage(input_tokens=100, output_tokens=400, cached_tokens=0),
            finish="tool_calls" if calls else "stop",
            raw_finish="tool_calls" if calls else "stop",
            raw=(),
        )

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=False,
            tools=True,
            thinking=False,
            context_length=128_000,
            own_context_length=None,
            window_source="assumed",
            efforts=(),
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("scripted"),)

    async def aclose(self) -> None:
        return None


def prepare_home(model: str) -> None:
    """Refuses a home that may be the owner's, then writes the throwaway home's config (``model``, supervised)."""
    for name in ("DOT_HOME", "DOT_LOG_DIR", "DOT_CHROME_DIR"):
        if name not in os.environ:
            raise SystemExit(f"{name} must name a throwaway folder")
    home: Path = Path(os.environ["DOT_HOME"])
    config: Path = home / "config.toml"
    # Only a throwaway home: one that does not exist yet, an empty one, or one this script made before (its config
    # starts with the mark). Anything else may be the owner's own Dot, whose config this would replace.
    made_here: bool = config.exists() and config.read_text(encoding="utf-8").startswith(CONFIG_MARK)
    if home.exists() and any(home.iterdir()) and not made_here:
        raise SystemExit(f"DOT_HOME={home} is not empty and was not made by this script: give it a mktemp -d folder")
    ensure_layout()
    config.write_text(
        f'{CONFIG_MARK}\n[models]\nmain = "{model}"\n\n[browser]\nheadless = true\n\n'
        '[policy]\napprovals = "supervised"\n',
        encoding="utf-8",
    )


async def seed(runtime: Runtime) -> None:
    """A note the owner wrote and a tainted run's rewrite of it, which waits for the owner as a proposal."""
    note = await runtime.store.add_memory(
        "The owner prefers short answers.", ("owner",), source_run=None, agent_id=None, vector=None, tainted=False
    )
    await runtime.store.update_memory(
        note.id,
        "The owner prefers short answers, with the commands written out in full.",
        ("owner",),
        vector=None,
        source_run="run_e2e_seed",
        tainted=True,
        new_expiry=None,
    )


async def _schedule_run(store: Store, name: str, goal: str, status: RunStatus, answer: str) -> RunRecord:
    """A run of a schedule of ``name`` that ended ``status`` with ``answer``, in the schedule's own chat."""
    schedule: ScheduleRecord = await store.add_schedule(
        name, "0 7 * * *", None, goal, "task", time.time() + 86_400, UNSET_OPTIONS, None, True
    )
    chat: ThreadRecord = await store.create_thread(name, "schedule")
    await store.link_schedule_thread(chat.id, schedule.id)
    run: RunRecord = await store.create_run(
        chat.id, goal, "task", MODEL, None, "schedule", "medium", "standard", None, "yolo", False, None, None, None
    )
    await store.finish_run(run.id, status, answer)
    return run


async def seed_activity(runtime: Runtime) -> None:
    """What the Activity tab shows (module docstring): runs of schedules, decisions made for the owner, drafts, a thing
    on Dot's list."""
    store: Store = runtime.store
    mail: RunRecord = await _schedule_run(
        store,
        "Client mail check",
        "Read new client mail and draft replies.",
        "done",
        "Two clients wrote; both replies are drafted for you.",
    )
    await _schedule_run(
        store, "Nightly backup", "Back up the projects folder.", "failed", "The backup disk was not mounted."
    )
    for decided_by in ("yolo", "yolo", "reviewer"):
        asked: ApprovalRecord = await store.create_approval(mail.id, "shell", "Run a command", "medium", {})
        await store.decide_approval(asked.id, "approved", decided_by, "")
    await store.add_draft(
        mail.id,
        "mail",
        ["ayse@example.com"],
        [],
        "Re: The March invoice",
        "Hi Ayşe,\n\nThanks, the invoice is paid; the receipt is attached.\n\nŞenol",
    )
    refused: DraftRecord = await store.add_draft(
        mail.id, "message", ["+90 555 111 22 33"], [], "", "Running ten minutes late, see you at the café."
    )
    await store.claim_draft(refused.id, list(refused.to), [], "", refused.body)
    await store.settle_draft(refused.id, "Messages could not reach the number.")
    await store.create_harness_version(
        None,
        "proposer",
        "Run the tests of what changed before the whole suite.",
        "- Run the tests of the files a change touched first.",
        "",
        json.dumps({"scope": "tests"}),
    )
    await store.create_harness_version(
        None,
        "proposer",
        "Read the README of a repository before editing it.",
        "- Read a repository's README before the first edit.",
        "",
        json.dumps({"tainted": True, "tainted_by": ["a web page"]}),
    )
    await store.add_todo(
        "Tidy the Downloads folder",
        "tidy the downloads folder",
        "It holds 2,000 files from the last year.",
        "run",
        mail.id,
        False,
    )


async def serve(port: int, seeded: bool, activity: bool, scripted: bool) -> None:
    home: Path = Path(os.environ["DOT_HOME"])
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    if scripted:
        await runtime.router.register("ollama", ScriptProvider())
    if seeded:
        await seed(runtime)
    if activity:
        await seed_activity(runtime)
    manager: RunManager = RunManager(runtime)
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", port)
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
    watching: asyncio.Task[None] = asyncio.create_task(watch_revisions(runtime.store, runtime.bus))
    # As the smoke tests wait for it: the server says it started by a flag, not an event.
    while not server.started:  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    print(f"READY {port} {TOKEN}", flush=True)
    try:
        await serving
    finally:
        watching.cancel()
        await manager.shutdown()
        await close_runtime(runtime)


def model_argument(args: list[str]) -> str:
    """The model ``--model <ref>`` names, else the script's own."""
    if "--model" not in args:
        return MODEL
    at: int = args.index("--model")
    if at + 1 >= len(args):
        raise SystemExit("--model needs a model reference, such as claude_code:haiku")
    return args[at + 1]


if __name__ == "__main__":
    chosen: str = model_argument(sys.argv[2:])
    prepare_home(chosen)
    asyncio.run(serve(int(sys.argv[1]), "--seed" in sys.argv[2:], "--seed-activity" in sys.argv[2:], chosen == MODEL))

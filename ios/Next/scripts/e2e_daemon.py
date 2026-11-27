"""A throwaway Dot daemon for DotNext's end-to-end check from the simulator (wave N1-client).

It serves the real app (routes, stores, streams, the revision watch) on 127.0.0.1 in a home of its own, with a model
that replies by a script instead of a service, so no model is called and nothing leaves this Mac. The owner's words
choose the script:

- "helper: ...": the run starts a helper (``delegate``, wait false) that waits 90 s, then ends;
- "waithelper: ...": a helper that waits 5 min, which the run waits on (``delegate``, wait true), so its row says so
  (item 1920);
- "wait: ...": the run waits 2 min in ``wait_until`` (a steer meanwhile shows "read after this step", and ends it);
- "slow: ...": the reply takes 8 s (a run at work, for the queue);
- "hold: ...": the daemon takes the phone's sending of the message only after 3 s (it shows pending meanwhile);
- "approve: ...": the run touches a file in /tmp with the shell, which the supervised mode asks the owner about;
- "question: ...": the run asks the owner a question with options and waits for the answer (``ask_user``);
- "secret: ...": the run asks for a secret (``ask_secret``), which the phone types in a secure field;
- "tools: ...": the run reads two files and keeps a plan, so a finished run folds its steps;
- "fail: ...": the model fails, so the run fails with one line of cause;
- "write: ...": the run writes a file of its workspace, a change the inspector can undo;
- "draft: ...": the run drafts an email, a card with Send, Edit and Discard;
- "files: ...": the run writes a note of its workspace, then answers naming it and two web links (one the local play
  page), for the file chip and the sources row under an answer (backlog LF1);
- "bg: ...": the run opens a terminal command in the background (``sleep 600``), which the inspector can stop;
- "stream: ...": four steps that each think aloud for about 4 s and read a file, then a long markdown answer, all
  streamed word by word at about 40 words a second, the pace of a hosted model (the chat's streaming is measured on it);
  "stream long: ..." the same with ten steps, a file of its own each;
- "browse: ...": the run opens a local page in its (headless) browser, then waits 8 min with the tab open, so the phone
  can follow the browser live and take its page over (items 1839-1840); the page counts its clicks and shows what is
  typed in its field, and a clock on it ticks so the cast keeps sending frames;
- anything else: an answer at once, after a few words streamed.

The home's approval mode is ``supervised``, so every acting step asks (reads do not). With ``--seed`` the home also
holds a note the owner wrote and a run's proposed rewrite of it (item 1711), for Settings › Memory's Use and Drop, and an active campaign with a progress note (Settings › Campaigns). With
``--seed-activity`` it holds what the Activity tab shows (item 1826): runs two schedules did today (one done, one
failed), the decisions made for the owner in them, drafts that wait (one whose send failed), a thing a run put on Dot's
own list, and two versions of Dot's habits proposed by its learning (one written from a page others can write);
``e2e_activity.sh`` marks "while you were away" read from before them. With ``--seed-work`` it holds an agent,
Scribe, with what a bot's profile lists (items 87, 907, 908): an objective, two work orders (one handed out and
due in three hours, one accepted) and two messages between Dot and Scribe, one of them answered
(``e2e_bots.sh``). With
``--model <ref>`` (``claude_code:haiku``) the runs go to that real model instead of the script, for a real-model run of
the phone's flows; the throwaway home has no key of its own, so only a provider that brings its own sign-in (the Claude
plan's ``claude`` command) answers.

Usage (the three folders are throwaway, ``mktemp -d``)::

    DOT_HOME=$t/home DOT_LOG_DIR=$t/logs DOT_CHROME_DIR=$t/chrome \\
        uv run python ios/Next/scripts/e2e_daemon.py 8765 [--seed] [--seed-activity] [--seed-work] [--slow-deletes <s>]

It prints ``READY <port> <token>`` once it serves; a pairing code is made with
``curl -X POST -H "Authorization: Bearer <token>" http://127.0.0.1:<port>/api/phone/pairing``.
"""

import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import SplitResult, parse_qs, urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
    ObjectiveWorkflow,
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


# The page "browse:" opens: a button that counts its clicks, a field, and a clock that ticks so the cast sends frames.
# What the page received (its clicks, its field's text) it reports to its own server at /state, which keeps it in
# $DOT_HOME/play-page.json: the end-to-end check reads the page's own state, not Dot's count of what was sent.
PLAY_PAGE: bytes = b"""<!doctype html><html><head><title>DotNext play</title>
<meta name="viewport" content="width=device-width"><style>
body { font: 20px -apple-system, sans-serif; margin: 40px; background: #f4f4f2; color: #222; }
button { font-size: 28px; padding: 24px 48px; border-radius: 16px; border: 0; background: #222; color: #fff; }
input { font-size: 24px; padding: 12px; width: 60%; margin-top: 24px; }
#clock { font-size: 64px; font-variant-numeric: tabular-nums; margin: 24px 0; }
.tall { height: 1600px; background: linear-gradient(#f4f4f2, #c9d3dd); }
</style></head><body>
<h1>Play page</h1><div id="clock">0.0</div>
<button id="press" type="button">Press me</button> <span id="count">0 clicks</span><br>
<input id="field" placeholder="Type here" autocomplete="off"><p id="echo"></p><div class="tall"></div>
<script>
let clicks = 0; const t0 = Date.now();
setInterval(() => { document.getElementById("clock").textContent = ((Date.now() - t0) / 1000).toFixed(1); }, 100);
const count = document.getElementById("count");
const field = document.getElementById("field");
const report = () => fetch("/state?clicks=" + clicks + "&text=" + encodeURIComponent(field.value));
document.getElementById("press").onclick = () => { clicks += 1; count.textContent = clicks + " clicks"; report(); };
field.oninput = () => { document.getElementById("echo").textContent = field.value; report(); };
</script></body></html>"""


class _PlayPage(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parts: SplitResult = urlsplit(self.path)
        if parts.path == "/state":
            asked: dict[str, list[str]] = parse_qs(parts.query, keep_blank_values=True)
            state: dict[str, int | str] = {"clicks": int(asked["clicks"][0]), "text": asked["text"][0]}
            (Path(os.environ["DOT_HOME"]) / "play-page.json").write_text(json.dumps(state), encoding="utf-8")
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PLAY_PAGE)))
        self.end_headers()
        self.wfile.write(PLAY_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return None


def serve_play_page() -> str:
    """The play page on a free port of this Mac, served on a thread for the life of the script; its address."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _PlayPage)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


class ScriptProvider:
    """Replies by the owner's last words (module docstring); a tool's result is answered with a closing text."""

    def __init__(self, play_page: str) -> None:
        self._calls: int = 0
        self._play_page: str = play_page

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
        steps: int = sum(1 for m in request.messages[at + 1 :] if m.role == "tool")
        tools_since: list[str] = [m.tool_name or "" for m in request.messages[at + 1 :] if m.role == "tool"]
        answered: bool = bool(tools_since)
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
        elif latest.startswith("browse:") and tools_since == ["browser_new_tab"]:
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="wait_until",
                    arguments={"command": "test -f /nonexistent-dotnext", "every_s": 5, "within_minutes": 8},
                ),
            )
        elif latest.startswith("files:") and answered:
            text = (
                "I wrote `dotnext-e2e-report.md`. The page is at "
                f"{self._play_page} and the source at https://example.com/dotnext."
            )
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
        elif latest.startswith("waithelper:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="delegate",
                    arguments={"goal": "wait for the test, a long one, then say done", "wait": True},
                ),
            )
        elif "wait for the test" in latest:
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="wait_until",
                    arguments={
                        "command": "test -f /nonexistent-dotnext",
                        "every_s": 5,
                        "within_minutes": 5 if "a long one" in latest else 1.5,
                    },
                ),
            )
        elif latest.startswith("browse:"):
            calls = (ToolCall(id=f"call_{self._calls}", name="browser_new_tab", arguments={"url": self._play_page}),)
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
        elif latest.startswith("files:"):
            calls = (
                ToolCall(
                    id=f"call_{self._calls}",
                    name="write_file",
                    arguments={
                        "path": "dotnext-e2e-report.md",
                        "content": "# E2E report\n\nWritten by the e2e check.\n",
                    },
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
        # No floor of free space: a check of the phone is not refused (507) by how full the Mac it runs on is.
        '[policy]\napprovals = "supervised"\n\n[store]\nmin_free_gb = 0\n',
        encoding="utf-8",
    )


async def seed(runtime: Runtime) -> None:
    """A note the owner wrote and a tainted run's rewrite of it, which waits for the owner as a proposal, and
    an active campaign with a progress note."""
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
    # Settings › Campaigns: an active campaign with a progress note (item 1895).
    thread = await runtime.store.create_thread("E2E campaign", "ui")
    campaign = await runtime.store.create_campaign(
        "E2E campaign",
        "Keep the test suite green.",
        thread.id,
        MODEL,
        "medium",
        "standard",
        None,
        "supervised",
        (),
        None,
        (),
    )
    await runtime.store.add_campaign_entry(
        campaign.id, None, "progress", "Two failing tests fixed.", {"tests_fixed": 2}
    )


async def seed_work(runtime: Runtime) -> None:
    """An agent, Scribe, with an objective, two work orders (one handed out and due in three hours, one accepted)
    and two messages between Dot and Scribe, one answered and one still at work: what a bot's profile lists."""
    store: Store = runtime.store
    scribe = await store.create_agent(
        "Scribe", "Writes the weekly note", "Write the weekly note, short.", "", None, None, (), None, None
    )
    objective = await store.add_objective(
        "Keep the weekly note short",
        "The owner reads it on the phone.",
        "Under 200 words, every Friday.",
        ObjectiveWorkflow(next_step="Draft this Friday's note"),
        False,
    )
    finished = await store.add_objective(
        "Choose the note's topics well",
        "Three topics keep the note short.",
        "Three topics a week, one line each.",
        ObjectiveWorkflow(next_step=""),
        False,
    )
    await store.update_objective(finished.id, "done", None, None)
    chat: ThreadRecord = await store.create_thread("Weekly note", "ui")
    lead: RunRecord = await store.create_run(
        chat.id,
        "Hand out the weekly note",
        "task",
        MODEL,
        None,
        "ui",
        "medium",
        "standard",
        None,
        "supervised",
        False,
        None,
        None,
        None,
    )
    await store.finish_run(lead.id, "done", "Handed out.")
    await store.add_work_order(
        scribe.id,
        "Draft the weekly note",
        "Collect the week's highlights and write the note.",
        "A note under 200 words, ready to send.",
        objective.id,
        time.time() + 3 * 3600,
        lead.id,
        chat.id,
        False,
    )
    done = await store.add_work_order(
        scribe.id,
        "Pick the note's topics",
        "Choose three topics.",
        "Three topics, one line each.",
        finished.id,
        None,
        lead.id,
        chat.id,
        False,
    )
    await store.move_work_order(done.id, "in_review", ("assigned",), note="The topics are chosen.")
    await store.move_work_order(done.id, "accepted", ("in_review",), note="Good.")
    asked: RunRecord = await store.create_run(
        chat.id,
        "Which topics fit this week?",
        "task",
        MODEL,
        lead.id,
        "team",
        "medium",
        "standard",
        None,
        "supervised",
        False,
        None,
        scribe.id,
        None,
    )
    await store.finish_run(asked.id, "done", "Launch, the offsite and the new price list.")
    scribe_run: RunRecord = await store.create_run(
        chat.id,
        "Scribe's own run",
        "task",
        MODEL,
        None,
        "ui",
        "medium",
        "standard",
        None,
        "supervised",
        False,
        None,
        scribe.id,
        None,
    )
    await store.create_run(
        chat.id,
        "Is the offsite date final?",
        "task",
        MODEL,
        scribe_run.id,
        "team",
        "medium",
        "standard",
        None,
        "supervised",
        False,
        None,
        None,
        None,
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


class HoldAccepts:
    """Holds for 3 s the phone's sending of a message whose words start with "hold:" (item 1927), so that a test sees
    it pending under the owner's bubble before the daemon takes it; with ``slow_deletes`` above 0, holds a delete of
    chats that long (item 1985), as a Mac far away or busy answers."""

    def __init__(self, app: ASGIApp, slow_deletes: float) -> None:
        self.app: ASGIApp = app
        self.slow_deletes: float = slow_deletes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("method") == "DELETE"
            and scope.get("path") == "/api/threads"
            and self.slow_deletes > 0
        ):
            await asyncio.sleep(self.slow_deletes)
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        more: bool = True
        while more:
            message: Message = await receive()
            chunks.append(message.get("body", b""))
            more = bool(message.get("more_body", False))
        body: bytes = b"".join(chunks)
        if b'"hold:' in body:
            await asyncio.sleep(3)
        replayed: bool = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def serve(port: int, seeded: bool, activity: bool, work: bool, scripted: bool, slow_deletes: float) -> None:
    home: Path = Path(os.environ["DOT_HOME"])
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    play_page: str = serve_play_page()
    print(f"PLAY {play_page}", flush=True)
    if scripted:
        await runtime.router.register("ollama", ScriptProvider(play_page))
    if seeded:
        await seed(runtime)
    if activity:
        await seed_activity(runtime)
    if work:
        await seed_work(runtime)
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
    server: DotServer = DotServer(serving_config(HoldAccepts(create_app(context), slow_deletes)), stopping)
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


def slow_deletes_argument(args: list[str]) -> float:
    """The seconds ``--slow-deletes <s>`` holds a delete of chats, else none."""
    if "--slow-deletes" not in args:
        return 0.0
    at: int = args.index("--slow-deletes")
    if at + 1 >= len(args):
        raise SystemExit("--slow-deletes needs a number of seconds")
    return float(args[at + 1])


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
    asyncio.run(
        serve(
            int(sys.argv[1]),
            "--seed" in sys.argv[2:],
            "--seed-activity" in sys.argv[2:],
            "--seed-work" in sys.argv[2:],
            chosen == MODEL,
            slow_deletes_argument(sys.argv[2:]),
        )
    )

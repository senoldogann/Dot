"""The words of the screens (item 625): the whole interface is easy for someone who knows no software, so no screen
of it says a word that such a person would have to ask about (``daemon``, ``token``, ``MCP``, ``API``, ``preset``,
``config``, ``tool``...), and none says "run" where it means a task. Each screen of the interface, and each menu and
dialog that opens from it, is opened in Chrome on a daemon that holds something to show (chats that worked, failed,
asked and wait for an answer, notes, a schedule, an agent, a phone paired with iMessage), wide and on a phone, and the
words a person sees are read: the text of the page and of what stands over it, and the titles, labels and hints of its
controls.

What a closed fold holds (a ``details`` that is not open: "Details", "Advanced") is not on the screen, and is not read:
the expert's words, the name of a setting's key, a command, a code, stand there. So does the tooltip of an element
marked ``data-expert`` (a schedule's cron beside its rhythm in words, a setting's key beside a note): it shows only to
whoever holds the pointer on it, and its text is read. Diagnostics, which is for finding what
went wrong, keeps its technical words, and so does the list of the licences; Settings › Rules and Settings › Vault are
read by their own tests. A new screen that brings a word of the list back fails here, so the list is the rule
(CONTRIBUTING.md, "Words on the screen").

No network and no model service is used: a scripted model plays the chats, the Mac's permission service, launchd and
the Keychain answer as a Mac at rest, and Messages is a database the test makes.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from channel_stand_ins import FakeMacos, Osascript
from messages_db import MessagesDb
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent import launchd
from dotagent.agent.manager import RunManager
from dotagent.channels.service import Channels
from dotagent.config import load_config
from dotagent.connections import mac
from dotagent.core.paths import dot_home
from dotagent.desktop import perms
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, StreamDelta, ToolCall, Usage
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "plain-words-test-token"
PHONE: str = "+358401234567"
_PROVIDER: str = "ollama"
_WAIT_S: float = 30.0
_SIZES: tuple[int, ...] = (1280, 390)

# The words of a program, which a screen for someone who knows no software does not say. Each is a whole word, in any
# case; a name that holds one (a plugin called "MCP Builder") is the product's own and stays (``_PRODUCTS``).
JARGON: re.Pattern[str] = re.compile(
    r"\b(MCP|daemon|tokens?|APIs?|endpoints?|JSON|schemas?|regex|regular expressions?|presets?|harness|evals?|stdio|"
    r"CLI|PID|launchd|payload|webhooks?|OAuth|localhost|config|config\.toml|sandbox|providers?|tools?|dotx|"
    r"headless|environment variables?|servers?|ports?|HTTPS?|tailnet|cron)\b",
    re.IGNORECASE,
)
# A run is a task: the word names a thing here, not what Dot does ("Run now" does the verb).
RUN_AS_A_THING: re.Pattern[str] = re.compile(
    r"\b(?:a|an|the|this|that|each|every|its|your|their|next|last|new|earlier|no|another|same|these|those|\d+)\s+runs?\b"
    r"|(?:^|[.!?]\s+)Runs\b",
    re.IGNORECASE | re.MULTILINE,
)
_PRODUCTS: tuple[str, ...] = ("MCP Builder",)
# The address of a service ("https://tailscale.com/download", "api.githubcopilot.com") is its own name, not a word of
# the page.
_ADDRESS: re.Pattern[str] = re.compile(
    r"https?://[^\s\"')]+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|dev|io|net|org|ai|app|co|me|tv|cloud)\b", re.IGNORECASE
)
# The Voice card of General is item 626's (the voice and the microphone), which landed with these two sentences in its
# own words: they are left out here until they are made plain (roadmap/625.md says to what), and then this list goes.
_LEFT_FOR_ITEM_626: tuple[str, ...] = ("sends the text it reads to a server", "Google's servers")

# What a person sees of a page: the text of what is drawn (not what a closed fold, a closed dialog or a hidden node
# holds) and the words of the controls (title, aria-label, placeholder, alt).
VISIBLE: str = """() => {
  const found = [];
  const skipped = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "svg", "CANVAS"]);
  const words = ["title", "aria-label", "placeholder", "alt"];
  const hidden = (node) => {
    const style = getComputedStyle(node);
    return style.display === "none" || style.visibility === "hidden" || style.contentVisibility === "hidden";
  };
  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent.replace(/\\s+/g, " ").trim();
      if (text !== "") found.push(text);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || skipped.has(node.tagName) || hidden(node)) return;
    if (node.tagName === "DIALOG" && !node.open) return;
    for (const name of words) {
      // The tooltip of an element marked as the expert's (data-expert) is where the expert's words stand.
      if (name === "title" && node.hasAttribute("data-expert")) continue;
      const value = node.getAttribute(name);
      if (value) found.push(value.replace(/\\s+/g, " ").trim());
    }
    const closed = node.tagName === "DETAILS" && !node.open;
    for (const child of node.childNodes) {
      if (closed && !(child.nodeType === Node.ELEMENT_NODE && child.tagName === "SUMMARY")) continue;
      walk(child);
    }
  };
  walk(document.body);
  return found;
}"""


class _Scenes:
    """A model that plays the chat its goal names: a marker in the goal picks the script, one turn a step; any other
    call (a review, a title) gets a short answer."""

    name: str = _PROVIDER

    def __init__(self, scripts: dict[str, list[tuple[str, str, list[tuple[str, dict[str, object]]]]]]) -> None:
        self._scripts = scripts

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        goal: str = next((message.content for message in request.messages if message.role == "user"), "")
        marker: str | None = next((key for key in self._scripts if key in goal), None)
        text: str = "OK"
        thinking: str = ""
        calls: list[tuple[str, dict[str, object]]] = []
        if marker is not None:
            steps: int = sum(1 for message in request.messages if message.role == "assistant")
            script = self._scripts[marker]
            text, thinking, calls = script[min(steps, len(script) - 1)]
        if thinking:
            await sink(StreamDelta(kind="thinking", text=thinking))
        if text:
            await sink(StreamDelta(kind="text", text=text))
        made: tuple[ToolCall, ...] = tuple(
            ToolCall(id=f"call_{len(request.messages)}_{index}", name=tool, arguments=arguments)
            for index, (tool, arguments) in enumerate(calls)
        )
        return ModelReply(
            model=request.model,
            text=text,
            thinking=thinking,
            tool_calls=made,
            usage=Usage(input_tokens=1000, output_tokens=50, cached_tokens=0),
            finish="tool_calls" if made else "stop",
            raw_finish="tool_calls" if made else "stop",
            raw=(),
        )

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=False,
            tools=True,
            thinking=True,
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


class Served:
    """The address of the daemon, and the ids of the chats it holds."""

    def __init__(self, address: str, runs: dict[str, str]) -> None:
        self.address: str = address
        self.runs: dict[str, str] = runs

    @property
    def port(self) -> int:
        return int(self.address.rpartition(":")[2])


def _scripts(notes: Path) -> dict[str, list[tuple[str, str, list[tuple[str, dict[str, object]]]]]]:
    finish = "finish"
    return {
        "[[files]]": [
            (
                "Let me look at the folder first.",
                "The list is short. I look at the folder, then write the notes down.",
                [("shell", {"command": "ls -la"})],
            ),
            ("", "", [("write_file", {"path": str(notes), "content": "milk\nbread\neggs\n"})]),
            ("", "", [("read_file", {"path": str(notes)})]),
            ("", "", [(finish, {"answer": "I wrote your notes to notes.txt.", "success": True})]),
        ],
        "[[failing]]": [
            ("", "", [("shell", {"command": "cat missing-file.txt"})]),
            (
                "",
                "",
                [(finish, {"answer": "The file does not exist.", "success": False, "needs_from_user": "Where is it?"})],
            ),
        ],
        "[[question]]": [
            (
                "",
                "",
                [
                    (
                        "ask_user",
                        {
                            "question": "Which folder should I tidy?",
                            "options": [{"label": "Downloads"}, {"label": "Desktop"}],
                            "wait": False,
                        },
                    )
                ],
            ),
            ("", "", [(finish, {"answer": "I will tidy the folder you pick.", "success": True})]),
        ],
        "[[approval]]": [
            ("", "", [("write_file", {"path": str(notes.with_name("todo.txt")), "content": "call mum\n"})]),
            ("", "", [(finish, {"answer": "The to-do list is written.", "success": True})]),
        ],
        "[[slow]]": [
            ("", "", [("shell", {"command": "sleep 60"})]),
            ("", "", [(finish, {"answer": "Done waiting.", "success": True})]),
        ],
    }


def _other_apps(home: Path) -> None:
    """What other apps of this Mac set up, in the home of this test: a Claude Code skill and a server of Claude Desktop,
    which Customize › Skills offers to import."""
    skill: Path = home / ".claude" / "skills" / "tidy-desk"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: tidy-desk\ndescription: Put the files of the desktop in folders.\n---\nSort them by kind.\n",
        encoding="utf-8",
    )
    desktop: Path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(
        json.dumps({"mcpServers": {"notes": {"command": "npx", "args": ["-y", "notes-server"]}}}), encoding="utf-8"
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Served]:
    home: Path = tmp_path_factory.mktemp("home")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    # The skills and servers "found on this Mac" are read from the folders of the other apps in the user's home: a home
    # of this test's own, so that none of the owner's is read.
    userhome: Path = tmp_path_factory.mktemp("userhome")
    patch.setenv("HOME", str(userhome))
    _other_apps(userhome)
    # The Mac at rest: macOS's permission service, the programs that answer for it and launchd say nothing new, and
    # nothing is asked of the owner's own.

    async def _not_loaded(arguments: list[str]) -> launchd.LaunchctlResult:
        return launchd.LaunchctlResult(arguments=arguments, returncode=113, stdout="", stderr="")

    patch.setattr(launchd, "run_launchctl", _not_loaded)
    patch.setattr(mac, "automation_state", lambda bundle_id: "not_open")
    patch.setattr(mac, "installed_app", lambda bundle_id: Path("/Applications") / f"{bundle_id}.app")
    patch.setattr(perms, "responsible_program", lambda: Path("/Users/someone/Library/Application Support/Dot/Dot.app"))
    (home / "config.toml").write_text(
        f"[browser]\nheadless = true\n\n[memory]\nembed_enabled = false\n\n"
        f'[models]\nmain = "{SCRIPTED_MODEL}"\nreview = "{SCRIPTED_MODEL}"\n',
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    notes: Path = dot_home() / "workspace" / "notes.txt"
    await runtime.router.register(_PROVIDER, _Scenes(_scripts(notes)))
    manager: RunManager = RunManager(runtime)
    messages: MessagesDb = MessagesDb(tmp_path_factory.mktemp("messages") / "chat.db")
    osascript: Osascript = Osascript()
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    owner: Path = dot_home() / "owner"
    channels: Channels = Channels(
        runtime,
        manager,
        "127.0.0.1",
        port,
        script=osascript,
        messages_db=messages.path,
        macos=FakeMacos(),
        poll_s=0.05,
        home=owner,
        staging=owner / "Pictures" / "Dot",
    )
    await channels.start()
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
        channels=channels,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    runs: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as api:
            await _seed(api, manager, runs, messages, osascript)
            yield Served(f"http://127.0.0.1:{port}", runs)
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await channels.stop()
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _seed(
    api: httpx.AsyncClient, manager: RunManager, runs: dict[str, str], messages: MessagesDb, osascript: Osascript
) -> None:
    """What the screens have to show: chats that finished, failed and asked, one that waits for the owner's yes and one
    that is still at work, notes, a schedule, an agent, and a phone paired with iMessage."""
    for key in ("files", "failing", "question"):
        started = await manager.start(
            f"Please do this [[{key}]]",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        runs[key] = started.id
        async with asyncio.timeout(_WAIT_S):
            await manager.wait(started.id)
    asked = await api.post(
        "/api/rules", json={"behavior": "ask", "tool_glob": "write_file", "match_regex": "todo", "description": "Ask"}
    )
    assert asked.status_code == 201, asked.text
    waiting = await manager.start(
        "Please do this [[approval]]",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        "ask",
        False,
        (),
    )
    runs["approval"] = waiting.id
    working = await manager.start(
        "Please wait for this [[slow]]",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        "yolo",
        False,
        (),
    )
    runs["slow"] = working.id
    for note in (
        {"content": "I prefer short answers.", "tags": ["style"]},
        {"content": "My dentist is on Elm Street.", "tags": []},
    ):
        assert (await api.post("/api/memories", json=note)).status_code == 201
    scheduled = await api.post(
        "/api/schedules",
        json={"name": "Morning news", "goal": "Read the headlines and sum them up", "cron": "0 8 * * *"},
    )
    assert scheduled.status_code == 201, scheduled.text
    agent = await api.post(
        "/api/agents",
        json={
            "name": "Tidy",
            "role": "Keeps folders in order",
            "instructions": "Sort files into folders.",
            "model": SCRIPTED_MODEL,
            "effort": None,
            "approvals": None,
            "tools": None,
        },
    )
    assert agent.status_code == 201, agent.text
    # The phone is paired with iMessage, and the channel on, so that its settings and its chat have something to say.
    await api.post("/api/channels/imessage/code", json={"handle": PHONE})
    code: str = "".join(char for char in osascript.sent[-1][1].split("code is ")[1][:7] if char.isdigit())
    assert (await api.post("/api/channels/imessage/confirm", json={"code": code})).status_code == 200
    assert (await api.post("/api/channels/imessage/switch", json={"on": True})).status_code == 200
    messages.message(messages.chat(PHONE), "Say hello", at=time.time())
    deadline: float = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        chats = (await api.get("/api/threads")).json()
        if any(chat.get("source") == "imessage" or chat.get("channel") == "imessage" for chat in chats):
            break
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.0)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(served: Served) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _context(browser: Browser, served: Served, width: int) -> BrowserContext:
    phone: bool = width < 720
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900},
        is_mobile=phone,
        has_touch=phone,
        reduced_motion="reduce",
        service_workers="block",
    )
    context.set_default_timeout(10_000)
    return context


async def _signed_in(browser: Browser, served: Served, width: int) -> Page:
    page: Page = await (await _context(browser, served, width)).new_page()
    await page.goto(ui_url("127.0.0.1", served.port, TOKEN))
    await expect(page.locator("main.page")).to_be_visible()
    return page


class _Reader:
    """The words a person sees, screen by screen, and the ones of the list among them."""

    def __init__(self, page: Page, width: int) -> None:
        self.page: Page = page
        self.width: int = width
        self.problems: list[str] = []
        self.screens: int = 0

    async def read(self, where: str) -> None:
        """Read what the page shows now, and note each word of the list under the name of the screen."""
        lines: list[str] = await self.page.evaluate(VISIBLE)
        self.screens += 1
        for line in lines:
            shown: str = _ADDRESS.sub("", line)
            for product in (*_PRODUCTS, *_LEFT_FOR_ITEM_626):
                shown = shown.replace(product, "")
            for found in (*JARGON.finditer(shown), *RUN_AS_A_THING.finditer(shown)):
                self.problems.append(f"{where} ({self.width}): “{found.group(0).strip()}” in “{line[:110]}”")

    async def go(self, route: str, where: str, settle: str | None = None) -> None:
        await self.page.goto(f"{self.page.url.split('#')[0]}#{route}")
        if settle is not None:
            await expect(self.page.locator(settle).first).to_be_visible()
        await self.page.wait_for_timeout(700)
        await self.read(where)

    async def opens(self, where: str, opener: Locator, close_with: str = "Escape") -> None:
        """Press `opener`, read what stands over the page, and close it."""
        await opener.click()
        await self.page.wait_for_timeout(500)
        await self.read(where)
        await self.page.keyboard.press(close_with)
        await self.page.wait_for_timeout(250)
        for dialog in await self.page.locator("dialog[open]").all():
            await dialog.evaluate("(node) => node.close()")

    def done(self) -> None:
        assert self.screens > 0
        assert self.problems == [], "\n".join(self.problems)


async def test_the_first_start_says_what_to_do_in_words(browser: Browser, served: Served) -> None:
    """The sign-in screen of a browser that has no key and as it asks again (a code that is not one, a code the Mac does
    not take, a key typed that Dot refuses, a key kept that is no more), the screen of a Dot that cannot be reached, and
    the one of a Dot that answers with an error, and the page a phone keeps for when its Mac is out of reach."""
    for width in _SIZES:
        context: BrowserContext = await _context(browser, served, width)
        page: Page = await context.new_page()
        reader: _Reader = _Reader(page, width)
        await page.goto(f"{served.address}/")
        await expect(page.get_by_role("textbox", name="Sign-in code")).to_be_visible()
        await reader.read("Sign in")
        # The screen as it asks again: a code that is not one, a code the Mac does not take, and a key typed that Dot
        # refuses, which opens the other ways to sign in.
        await page.get_by_role("textbox", name="Sign-in code").fill("nope")
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await expect(page.locator(".signin .note.bad")).to_contain_text("does not look like a sign-in code")
        await reader.read("Sign in with something that is not a code")
        await page.get_by_role("textbox", name="Sign-in code").fill("0000-0000")
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await expect(page.locator(".signin .note.bad")).to_contain_text("sign-in code is wrong")
        await reader.read("Sign in with a code the Mac does not take")
        await page.get_by_text("Other ways to sign in").click()
        await page.get_by_label("Access key", exact=True).fill("not-the-key")
        await page.get_by_role("button", name="Sign in with the key").click()
        await expect(page.locator(".signin .note.bad")).to_contain_text("did not accept that access key")
        await expect(page.locator("details.signin-other")).to_have_attribute("open", "")
        await reader.read("Sign in with a key Dot does not take")
        await page.goto(f"{served.address}/offline.html")
        await reader.read("The page for a Mac out of reach")
        reader.done()
        await context.close()
        # A key the browser kept that Dot no longer takes.
        context = await _context(browser, served, width)
        await context.add_init_script("localStorage.setItem('dot.token', 'a-key-that-was-changed')")
        page = await context.new_page()
        reader = _Reader(page, width)
        await page.goto(f"{served.address}/")
        await expect(page.locator(".signin .note.bad")).to_contain_text("did not accept that access key")
        await reader.read("Sign in with a key that was kept and is no more")
        reader.done()
        await context.close()
        # Dot cannot be reached: every request of the page fails; Dot answers with an error: the status does.
        for name, refusing in (("Dot cannot be reached", True), ("Dot cannot answer", False)):
            context = await _context(browser, served, width)
            await context.add_init_script(f"localStorage.setItem('dot.token', '{TOKEN}')")
            page = await context.new_page()
            reader = _Reader(page, width)

            async def _fail(route, refusing: bool = refusing) -> None:
                if refusing:
                    await route.abort()
                else:
                    await route.fulfill(status=500, json={"message": "the store is busy", "context": {}})

            await page.route("**/api/**", _fail)
            await page.goto(f"{served.address}/")
            await expect(page.get_by_role("button", name="Try again")).to_be_visible()
            await reader.read(name)
            reader.done()
            await context.close()


async def test_a_chat_its_messages_menus_and_cards_say_what_they_mean(browser: Browser, served: Served) -> None:
    """The home, a chat that worked, one that failed, one that asked, one that waits for the owner's yes and one at
    work; the composer's menus, the side panel, the context ring, the sidebar's menus and a toast."""
    for width in _SIZES:
        page: Page = await _signed_in(browser, served, width)
        reader: _Reader = _Reader(page, width)
        await reader.go("/runs", "New chat", "textarea")
        for key, label in (
            ("files", "A chat that worked"),
            ("failing", "A chat that failed"),
            ("question", "A chat that asked"),
            ("approval", "A chat that waits for a yes"),
            ("slow", "A chat at work"),
        ):
            await reader.go(f"/runs/{served.runs[key]}", label, "textarea")
        # The composer's menus, on the home.
        await reader.go("/runs", "New chat", "textarea")
        await reader.opens("The model menu", page.locator("button[title='ollama:scripted']").first)
        await reader.opens("The options menu", page.locator("button[title^='Approvals:']").first)
        await reader.opens("The plus menu", page.get_by_role("button", name="Add files or teach a task"))
        goal: Locator = page.get_by_role("textbox", name="Goal")
        for trigger, label in (("/", "The slash menu"), ("@", "The at menu")):
            await goal.fill(trigger)
            await page.wait_for_timeout(400)
            await reader.read(label)
            await goal.fill("")
        # The chat that worked: its side panel, the ring of how full it is, a reaction, a copy.
        await reader.go(f"/runs/{served.runs['files']}", "A chat that worked", "textarea")
        await reader.opens("The chat's size ring", page.locator("button[title^='Chat size']").first)
        await reader.opens("The task's panel", page.get_by_role("button", name="Task details").first, "Escape")
        await page.get_by_role("button", name="Copy the answer").first.click()
        await reader.read("The toast of a copy")
        await page.locator("button[aria-label='React to this message']").first.click(force=True)
        await reader.read("The reactions")
        await page.keyboard.press("Escape")
        # A chat at work: the stop button's words and the queue.
        await reader.go(f"/runs/{served.runs['slow']}", "A chat at work", "textarea")
        await page.get_by_role("textbox", name="Message for Dot").fill("And then say hello")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        await reader.read("A chat at work with a message queued")
        reader.done()
        await page.context.close()


async def test_the_sidebar_and_the_pages_beside_it_say_what_they_mean(browser: Browser, served: Served) -> None:
    """The sidebar with its chats and the iMessage chat, its menus, and the pages of Agents (the list, the team and the
    form that makes one), Campaigns, Schedules (and the dialogs of a schedule and a watcher)."""
    for width in _SIZES:
        page: Page = await _signed_in(browser, served, width)
        reader: _Reader = _Reader(page, width)
        await reader.go("/runs", "The sidebar", "textarea")
        if width < 720:
            await page.get_by_role("button", name="Show sidebar").click()
            await page.wait_for_timeout(400)
            await reader.read("The sidebar on a phone")
        await reader.opens("A chat's menu", page.locator("button[aria-label^='More for']").first)
        await page.get_by_role("button", name="Select").first.click()
        await reader.read("The sidebar while chats are chosen")
        await page.keyboard.press("Escape")
        await reader.go("/campaigns", "Campaigns", "main.page")
        await reader.go("/agents", "Agents", "main.page")
        await reader.opens("The form that makes an agent", page.get_by_role("button", name="New agent").first)
        await reader.opens("The form that makes a team", page.get_by_role("button", name="New team").first)
        await reader.go("/team", "Handoffs", "main.page")
        await reader.go("/schedules", "Schedules", "main.page")
        await reader.opens("The form that adds a schedule", page.get_by_role("button", name="New schedule").first)
        await reader.opens("The form that adds a watcher", page.get_by_role("button", name="New watcher").first)
        await reader.opens("The form of the morning brief", page.get_by_role("button", name="Morning brief settings"))
        reader.done()
        await page.context.close()


async def test_the_settings_say_what_they_change_in_words(browser: Browser, served: Served) -> None:
    """General, Appearance, Models, AI services, Browser, Memory, Self-improvement, Phone, Channels, SnapShots and
    System, with the dialogs and the forms that open from them."""
    tabs: tuple[str, ...] = (
        "general",
        "appearance",
        "models",
        "keys",
        "browser",
        "memory",
        "harness",
        "phone",
        "channels",
        "snapshots",
        "system",
    )
    for width in _SIZES:
        page: Page = await _signed_in(browser, served, width)
        reader: _Reader = _Reader(page, width)
        for tab in tabs:
            await reader.go(f"/settings/{tab}", f"Settings › {tab}", ".set-pane, .settings-pane")
        await reader.go("/settings/general", "Settings › general", ".settings-pane")
        await reader.opens(
            "The question before this browser signs out", page.get_by_role("button", name="Sign out of this browser")
        )
        await reader.go("/settings/browser", "Settings › browser", ".settings-pane")
        await reader.opens("The form that adds a profile", page.get_by_role("button", name="Add profile").first)
        await reader.go("/settings/harness", "Settings › harness", ".settings-pane")
        await reader.opens("The form that writes a version", page.get_by_role("button", name="Write your own"))
        await reader.go("/settings/keys", "Settings › keys", ".settings-pane")
        if width < 720:
            await page.get_by_role("button", name="OpenAI").first.click()
            await page.wait_for_timeout(400)
            await reader.read("Settings › keys › a service")
        await reader.opens("The form that adds a key", page.get_by_role("button", name="Add key").first)
        reader.done()
        await page.context.close()


async def test_customize_says_what_a_plugin_a_skill_and_a_connector_are_in_words(
    browser: Browser, served: Served
) -> None:
    """Plugins, Skills and Connectors, with the forms that open from them, and a service's own page."""
    for width in _SIZES:
        page: Page = await _signed_in(browser, served, width)
        reader: _Reader = _Reader(page, width)
        await reader.go("/customize/plugins", "Customize › Plugins", ".customize-body")
        await reader.go("/customize/skills", "Customize › Skills", ".customize-body")
        await reader.opens("The skills of other apps", page.get_by_role("button", name="Import from other apps").first)
        await reader.opens("The skill saved from a chat", page.get_by_role("button", name="Save from a chat").first)
        await reader.go("/customize/connectors", "Customize › Connectors", ".customize-body")
        await reader.opens("The form that adds a connected app", page.get_by_role("button", name="Add connected app"))
        await reader.go("/customize/connectors/github", "A connector's page", ".customize-body")
        reader.done()
        await page.context.close()


async def test_the_lead_of_diagnostics_and_the_licences_says_what_the_page_is_for(
    browser: Browser, served: Served
) -> None:
    """Diagnostics keeps its technical words, but its lead says in words what the page is for; the licences' own list
    holds the names of packages, so only its lead is read."""
    for width in _SIZES:
        page: Page = await _signed_in(browser, served, width)
        for route, words in (
            ("/diagnostics", "what went wrong"),
            ("/licences", "open source software"),
        ):
            await page.goto(f"{served.address}/#{route}")
            lead: Locator = page.locator(".page-sub").first
            await expect(lead).to_contain_text(words)
            text: str = await lead.inner_text()
            found: list[str] = [m.group(0) for m in (*JARGON.finditer(text), *RUN_AS_A_THING.finditer(text))]
            assert found == [], f"{route} ({width}): {found} in “{text}”"
        await page.context.close()

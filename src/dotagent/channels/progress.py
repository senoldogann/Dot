"""What a run started from a channel shows on it while it works (item 618).

iMessage cannot edit a message it sent, so "live" is a few messages and not a stream. A run that works longer than
:data:`FIRST_AFTER_S` sends one message of progress, then at most one every :data:`APART_S`, and :data:`MAX_MESSAGES` in
all; a run that ends sooner sends only its answer. A message tells what happened since the one before, in plain lines,
the newest last: the commands the run ran (``$ `` and the first :data:`COMMAND_CHARS` characters of each), the tools it
used in the words the page uses for them (*used the desktop*, *read a page: example.com*, *wrote notes.txt*), and the
first sentence of its newest thought (``💭 ``, at most :data:`THOUGHT_CHARS` characters) when the model thinks. It
tells what was done, never what came of it: no tool's output is read here, only the call and its arguments, which the
loop has masked of the secrets Dot holds before they are an event.

This module has the words and the bookkeeping; the router follows the events and sends the messages."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotagent.core.text import plain_line

# A run that works longer than this sends its first message of progress; a later one is sent at least this long after
# the one before it, and a run sends this many at most.
FIRST_AFTER_S: float = 15.0
APART_S: float = 30.0
MAX_MESSAGES: int = 6
# What one message holds: this many lines at most (the earlier steps are counted in a line of their own), a command cut
# to this many characters, a thought's first sentence to this many, a name to this many.
MAX_LINES: int = 8
COMMAND_CHARS: int = 60
THOUGHT_CHARS: int = 160
_NAME_CHARS: int = 60
# The steps kept for the next message, at most: a run that does hundreds of things between two messages keeps the
# newest.
_STEPS_KEPT: int = 200
COMMAND_MARK: str = "$ "
THOUGHT_MARK: str = "💭 "
_SENTENCE_END: str = ".!?…"
# The tools whose steps say nothing a phone needs: the end of the run is its answer, a question has its own line, a
# reaction stands on the owner's message (item 664), and the plan, the groups of tools, the help and the reading of a
# terminal are the run's own housekeeping.
_SKIPPED: frozenset[str] = frozenset(
    {
        "finish",
        "ask_user",
        "react",
        "update_plan",
        "mark_plan_step",
        "use_tools",
        "find_help",
        "terminal_read",
        "terminal_close",
    }
)
# What a tool is said to have done where its arguments add nothing to say.
_PLAIN: dict[str, str] = {
    "list_dir": "looked in a folder",
    "find_files": "looked for files",
    "search_files": "searched files",
    "apple_script": "ran an AppleScript",
    "mail_read": "read mail",
    "mail_search": "searched mail",
    "calendar_events": "looked at the calendar",
    "memory_save": "kept a note",
    "memory_update": "updated a note",
    "memory_search": "searched the notes",
    "memory_forget": "forgot a note",
    "write_notes": "wrote notes",
    "schedule_create": "added a schedule",
    "schedule_list": "looked at the schedules",
    "schedule_delete": "deleted a schedule",
    "notify": "notified you",
    "show_page": "showed a page",
    "draft_message": "drafted a message",
    "consult": "asked a helper",
    "message_agent": "wrote to an agent",
    "create_agent": "made an agent",
    "code_fetch": "fetched a code",
    "browser_snapshot": "looked at a page",
}
_PREFIX_MCP: str = "mcp__"


def _clip(text: str, chars: int) -> str:
    """``text`` as one line of at most ``chars`` characters, its white space single and its end marked when cut."""
    flat: str = " ".join(text.split())
    return flat if len(flat) <= chars else f"{flat[: chars - 1].rstrip()}…"


def _text(args: Mapping[str, object], key: str) -> str:
    value: object = args.get(key)
    return value if isinstance(value, str) else ""


def _command(raw: str) -> str:
    """A command the run ran, ``$ `` and its first characters; a call that gave none ran a command."""
    shown: str = _clip(raw, COMMAND_CHARS)
    return f"{COMMAND_MARK}{shown}" if shown else "ran a command"


def _file(verb: str, args: Mapping[str, object]) -> str:
    path: str = _text(args, "path").strip()
    name: str = Path(path).name or path
    return f"{verb} {_clip(name, _NAME_CHARS)}" if name else f"{verb} a file"


def _host(address: str) -> str:
    """The host of ``address`` and nothing else of it (a path or a query may hold what is not for a phone); an address
    with no scheme is read as ``https``."""
    text: str = address.strip()
    if not text:
        return ""
    try:
        return urlsplit(text if "://" in text else f"https://{text}").hostname or ""
    except ValueError:
        return ""


def _sources(args: Mapping[str, object]) -> str:
    """The address a tool of the web or the browser was given: ``url``, or the first of ``sources``."""
    address: str = _text(args, "url")
    if address:
        return address
    sources: object = args.get("sources")
    if isinstance(sources, list) and sources and isinstance(sources[0], str):
        return sources[0]
    return ""


def _page(verb: str, args: Mapping[str, object]) -> str:
    host: str = _host(_sources(args))
    return f"{verb}: {_clip(host, _NAME_CHARS)}" if host else verb


def tool_words(tool: str) -> str:
    """A tool's name in words, for a tool this module has no phrase for: an MCP tool by its server and what it does
    (``mcp__tavily__tavily_search`` reads *Tavily · tavily search*), any other by its name with spaces."""
    if tool.startswith(_PREFIX_MCP):
        server, _, name = tool.removeprefix(_PREFIX_MCP).partition("__")
        if name:
            return f"{server.replace('-', ' ').replace('_', ' ').title()} · {name.replace('_', ' ')}"
    return tool.replace("_", " ")


def step_words(tool: str, args: Mapping[str, object]) -> str | None:
    """What the call of ``tool`` with ``args`` is told as, in one line, or ``None`` for a step a phone does not need to
    hear of. A command is ``$ `` and its first characters; a file read, written or edited is named by its file name; a
    page by its host alone; the browser and the desktop as the page counts them; any other tool by what it does."""
    if tool in _SKIPPED:
        return None
    if tool in ("shell", "terminal_open"):
        return _command(_text(args, "command"))
    if tool == "terminal_send":
        return _command(_text(args, "text"))
    if tool == "read_file":
        return _file("read", args)
    if tool == "write_file":
        return _file("wrote", args)
    if tool == "edit_file":
        return _file("edited", args)
    if tool == "web_search":
        query: str = _clip(_text(args, "query"), COMMAND_CHARS)
        return f"searched the web: {query}" if query else "searched the web"
    if tool in ("web_fetch", "web_extract"):
        return _page("read a page", args)
    if tool == "web_crawl":
        return _page("crawled a site", args)
    if tool in ("browser_open", "browser_new_tab"):
        return _page("opened a page", args)
    if tool == "delegate":
        goal: str = _clip(_text(args, "goal"), COMMAND_CHARS)
        return f"handed work to a helper: {goal}" if goal else "handed work to a helper"
    if tool == "skill":
        name: str = _clip(_text(args, "name"), _NAME_CHARS)
        return f"used the skill {name}" if name else "used a skill"
    if tool in _PLAIN:
        return _PLAIN[tool]
    if tool.startswith("browser_"):
        return "used the browser"
    if tool.startswith("desktop_"):
        return "used the desktop"
    return f"used {tool_words(tool)}"


def first_sentence(thought: str, chars: int = THOUGHT_CHARS) -> str:
    """The first sentence of ``thought`` as one plain line of at most ``chars`` characters (its end marked when it was
    cut); an empty string for a thought of no words. A sentence ends at a full stop, an exclamation or question mark or
    an ellipsis that a space or the end follows (so ``3.5`` and ``example.com`` do not end it), or at a blank line."""
    flat: str = plain_line(thought.split("\n\n", 1)[0], len(thought))
    end: int = len(flat)
    for index, char in enumerate(flat):
        if char in _SENTENCE_END and (index + 1 == len(flat) or flat[index + 1] == " "):
            end = index + 1
            break
    return _clip(flat[:end], chars)


def elapsed(seconds: float) -> str:
    """A time of work in words: ``15 s``, ``2 min 5 s``, ``1 h 3 min``."""
    whole: int = max(0, int(seconds))
    if whole < 60:
        return f"{whole} s"
    minutes, rest = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes} min {rest} s" if rest else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


@dataclass
class _Step:
    """A thing the run did, in the words it is told in, and how many times in a row."""

    words: str
    times: int = 1


@dataclass
class Progress:
    """What one run has done since the last message of progress about it, and when the next may go. ``started`` is the
    moment (on the router's clock) the run began; ``first_after_s``, ``apart_s`` and ``max_messages`` are the limits
    above."""

    started: float
    first_after_s: float = FIRST_AFTER_S
    apart_s: float = APART_S
    max_messages: int = MAX_MESSAGES
    sent: int = 0
    last_sent: float | None = None
    _steps: list[_Step] = field(default_factory=list)
    _dropped: int = 0
    _thought: str | None = None
    _told_thought: str | None = None

    def step(self, words: str) -> None:
        """The run did something that ``words`` tell; the same thing again straight after is counted, not repeated."""
        if self._steps and self._steps[-1].words == words:
            self._steps[-1].times += 1
            return
        self._steps.append(_Step(words))
        if len(self._steps) > _STEPS_KEPT:
            self._dropped += self._steps.pop(0).times

    def thought(self, sentence: str) -> None:
        """The newest thought of the run, its first sentence; a thought with no words changes nothing."""
        if sentence:
            self._thought = sentence

    def _new_thought(self) -> bool:
        return self._thought is not None and self._thought != self._told_thought

    def due_at(self) -> float | None:
        """When a message may go (on the router's clock): :attr:`first_after_s` after the start for the first, and
        :attr:`apart_s` after the one before for the others. ``None`` when there is nothing new to tell or the run has
        sent its share."""
        if self.sent >= self.max_messages or not (self._steps or self._new_thought()):
            return None
        if self.last_sent is None:
            return self.started + self.first_after_s
        return self.last_sent + self.apart_s

    def forget(self) -> None:
        """What the run did is not to be told (nobody can be): the steps and the thought are let go, and no message is
        counted."""
        self._steps = []
        self._dropped = 0
        self._told_thought = self._thought

    def message(self, now: float) -> str | None:
        """The message to send at ``now``, or ``None`` when none may go yet. Sending it is the end of what it tells: the
        steps and the thought are told, and the next message is for what comes after."""
        due: float | None = self.due_at()
        if due is None or now < due:
            return None
        shown: list[_Step] = self._steps[-MAX_LINES:]
        earlier: int = self._dropped + sum(step.times for step in self._steps[: len(self._steps) - len(shown)])
        lines: list[str] = [f"Working for {elapsed(now - self.started)}:"]
        if earlier:
            lines.append(f"… {earlier} earlier {'step' if earlier == 1 else 'steps'}")
        lines.extend(f"{step.words} ×{step.times}" if step.times > 1 else step.words for step in shown)
        if self._thought is not None and self._new_thought():
            lines.append(f"{THOUGHT_MARK}{self._thought}")
            self._told_thought = self._thought
        self._steps = []
        self._dropped = 0
        self.sent += 1
        self.last_sent = now
        return "\n".join(lines)

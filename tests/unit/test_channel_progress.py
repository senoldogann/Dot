"""What a run started from a channel shows on it while it works (item 618): the words of a step in the page's own
terms, the first sentence of a thought, and the limits (nothing before 15 s, one every 30 s, six at most) with the time
given by the test, never by the clock."""

import pytest

from dotagent.channels.progress import (
    APART_S,
    COMMAND_CHARS,
    FIRST_AFTER_S,
    MAX_LINES,
    MAX_MESSAGES,
    THOUGHT_CHARS,
    Progress,
    elapsed,
    first_sentence,
    step_words,
    tool_words,
)


def test_the_limits_are_the_ones_the_owner_was_promised() -> None:
    assert (FIRST_AFTER_S, APART_S, MAX_MESSAGES, COMMAND_CHARS, THOUGHT_CHARS) == (15.0, 30.0, 6, 60, 160)


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("shell", {"command": "ls -la ~/Desktop"}, "$ ls -la ~/Desktop"),
        (
            "shell",
            {"command": "python - <<'EOF'\nprint(1)\n  print(2)\nEOF"},
            "$ python - <<'EOF' print(1) print(2) EOF",
        ),
        ("shell", {"command": "x" * 100}, "$ " + "x" * 59 + "…"),
        ("shell", {"command": ""}, "ran a command"),
        ("shell", {"command": ["not", "text"]}, "ran a command"),
        ("terminal_open", {"command": "python3 -i"}, "$ python3 -i"),
        ("terminal_send", {"session": "t1", "text": "print(1)\n"}, "$ print(1)"),
        ("read_file", {"path": "/Users/me/Documents/notes.txt"}, "read notes.txt"),
        ("write_file", {"path": "report.md", "content": "a secret body"}, "wrote report.md"),
        ("edit_file", {"path": "/tmp/a/b/main.py", "old": "x", "new": "y"}, "edited main.py"),
        ("read_file", {}, "read a file"),
        ("web_search", {"query": "weather in Oulu"}, "searched the web: weather in Oulu"),
        ("web_search", {"query": "q" * 90}, "searched the web: " + "q" * 59 + "…"),
        ("web_fetch", {"url": "https://example.com/a/b?token=abc123#x"}, "read a page: example.com"),
        ("web_fetch", {"url": "news.example.org/story"}, "read a page: news.example.org"),
        (
            "web_extract",
            {"sources": ["https://docs.example.dev/page", "https://other.example"], "fields": []},
            "read a page: docs.example.dev",
        ),
        ("web_fetch", {}, "read a page"),
        ("web_crawl", {"url": "https://site.example/start"}, "crawled a site: site.example"),
        ("browser_open", {"url": "https://shop.example/cart"}, "opened a page: shop.example"),
        ("browser_act", {"steps": []}, "used the browser"),
        ("browser_screenshot", {}, "used the browser"),
        ("desktop_act", {"ref": "e4"}, "used the desktop"),
        ("desktop_screenshot", {}, "used the desktop"),
        ("desktop_open_app", {"app": "Safari"}, "used the desktop"),
        (
            "delegate",
            {"goal": "Count the boxes on the first shelf"},
            "handed work to a helper: Count the boxes on the first shelf",
        ),
        ("skill", {"name": "pdf"}, "used the skill pdf"),
        ("list_dir", {"path": "."}, "looked in a folder"),
        ("mail_search", {"query": "invoice"}, "searched mail"),
        ("memory_save", {"text": "a note"}, "kept a note"),
        ("apple_script", {"script": "tell application ..."}, "ran an AppleScript"),
        ("mcp__tavily__tavily_search", {"query": "x"}, "used Tavily · tavily search"),
        ("mcp__microsoft-learn__docs_search", {}, "used Microsoft Learn · docs search"),
        ("some_new_tool", {}, "used some new tool"),
    ],
)
def test_a_step_is_told_in_the_words_of_the_page(tool: str, args: dict[str, object], expected: str) -> None:
    assert step_words(tool, args) == expected


@pytest.mark.parametrize(
    "tool",
    [
        "finish",
        "ask_user",
        "update_plan",
        "mark_plan_step",
        "use_tools",
        "find_help",
        "terminal_read",
        "terminal_close",
    ],
)
def test_a_step_that_says_nothing_a_phone_needs_is_not_told(tool: str) -> None:
    assert step_words(tool, {"answer": "x", "question": "y"}) is None


def test_what_a_page_step_tells_of_the_page_is_its_host_alone() -> None:
    told: str = (
        step_words("web_fetch", {"url": "https://user:password@example.com:8443/private/path?token=abc123"}) or ""
    )
    assert told == "read a page: example.com"
    assert "password" not in told and "abc123" not in told and "private" not in told


def test_tool_words_make_a_name_of_a_tool_without_a_phrase_readable() -> None:
    assert tool_words("mcp__tavily__tavily_search") == "Tavily · tavily search"
    assert tool_words("mcp__lonely") == "mcp  lonely"
    assert tool_words("draft_reply") == "draft reply"


@pytest.mark.parametrize(
    ("thought", "expected"),
    [
        ("The user wants a screenshot. I should use the desktop tool.", "The user wants a screenshot."),
        ("Is it 3.5 or 4? Let me check.", "Is it 3.5 or 4?"),
        ("Open example.com first, then read the page.", "Open example.com first, then read the page."),
        ("Hmm... the file may be gone. Look again.", "Hmm..."),
        ("First line without an end\nsecond line goes on.", "First line without an end second line goes on."),
        ("A first paragraph without an end\n\nA second paragraph. With sentences.", "A first paragraph without an end"),
        ("**Plan**: read the `README` first!", "Plan: read the README first!"),
        ("   \n\n  ", ""),
        ("", ""),
    ],
)
def test_the_first_sentence_of_a_thought(thought: str, expected: str) -> None:
    assert first_sentence(thought) == expected


def test_a_first_sentence_is_cut_to_the_size_of_a_thought_and_its_end_marked() -> None:
    cut: str = first_sentence("word " * 100)
    assert len(cut) == THOUGHT_CHARS and cut.endswith("…")
    assert first_sentence("short.", 3) == "sh…"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 s"),
        (15.9, "15 s"),
        (59, "59 s"),
        (60, "1 min"),
        (125, "2 min 5 s"),
        (3600, "1 h"),
        (3780, "1 h 3 min"),
        (-5, "0 s"),
    ],
)
def test_a_time_of_work_in_words(seconds: float, expected: str) -> None:
    assert elapsed(seconds) == expected


# ----- the limits -----------------------------------------------------------------------------------------------


def test_a_run_that_has_done_nothing_has_nothing_to_send_at_any_time() -> None:
    progress: Progress = Progress(started=100.0)
    assert progress.due_at() is None and progress.message(10_000.0) is None


def test_the_first_message_is_due_fifteen_seconds_after_the_run_began_and_not_before() -> None:
    progress: Progress = Progress(started=100.0)
    progress.step("$ ls")
    assert progress.due_at() == 115.0
    assert progress.message(114.9) is None and progress.sent == 0
    assert progress.message(115.0) == "Working for 15 s:\n$ ls"
    assert progress.sent == 1 and progress.last_sent == 115.0


def test_a_run_that_does_its_first_thing_late_sends_at_once_and_says_how_long_it_has_worked() -> None:
    progress: Progress = Progress(started=0.0)
    progress.step("used the desktop")
    assert progress.message(42.0) == "Working for 42 s:\nused the desktop"


def test_the_next_message_is_due_thirty_seconds_after_the_last_and_only_when_there_is_something_new() -> None:
    progress: Progress = Progress(started=0.0)
    progress.step("$ one")
    assert progress.message(15.0) == "Working for 15 s:\n$ one"
    assert progress.due_at() is None and progress.message(500.0) is None
    progress.step("$ two")
    assert progress.due_at() == 45.0
    assert progress.message(44.0) is None
    assert progress.message(50.0) == "Working for 50 s:\n$ two"
    progress.step("$ three")
    assert progress.due_at() == 80.0


def test_a_run_sends_six_messages_at_most_and_then_nothing_whatever_it_does() -> None:
    progress: Progress = Progress(started=0.0)
    times: list[float] = []
    now: float = 0.0
    for number in range(20):
        progress.step(f"$ step {number}")
        due: float | None = progress.due_at()
        if due is None:
            break
        now = max(now, due)
        sent: str | None = progress.message(now)
        assert sent is not None
        times.append(now)
    assert progress.sent == MAX_MESSAGES == len(times) == 6
    assert times == [15.0, 45.0, 75.0, 105.0, 135.0, 165.0]
    progress.step("$ one more")
    assert progress.due_at() is None and progress.message(10_000.0) is None


def test_a_message_tells_only_what_came_after_the_one_before() -> None:
    progress: Progress = Progress(started=0.0)
    progress.step("$ first")
    progress.thought("I will start.")
    assert progress.message(15.0) == "Working for 15 s:\n$ first\n💭 I will start."
    progress.step("$ second")
    assert progress.message(60.0) == "Working for 1 min:\n$ second"


def test_the_same_thing_again_straight_after_is_counted_not_repeated() -> None:
    progress: Progress = Progress(started=0.0)
    for words in ("used the desktop", "used the desktop", "used the desktop", "$ ls", "used the desktop"):
        progress.step(words)
    assert progress.message(20.0) == "Working for 20 s:\nused the desktop ×3\n$ ls\nused the desktop"


def test_a_message_holds_the_newest_lines_and_counts_the_earlier_steps() -> None:
    progress: Progress = Progress(started=0.0)
    for number in range(MAX_LINES + 5):
        progress.step(f"$ step {number}")
    lines: list[str] = (progress.message(20.0) or "").splitlines()
    assert lines[0] == "Working for 20 s:" and lines[1] == "… 5 earlier steps"
    assert lines[2:] == [f"$ step {number}" for number in range(5, MAX_LINES + 5)]
    progress.step("$ a")
    progress.step("$ b")
    progress.step("$ b")
    assert (progress.message(60.0) or "").splitlines() == ["Working for 1 min:", "$ a", "$ b ×2"]


def test_the_steps_kept_for_one_message_are_bounded_and_the_ones_let_go_are_counted() -> None:
    progress: Progress = Progress(started=0.0)
    for number in range(1000):
        progress.step(f"$ step {number}")
    lines: list[str] = (progress.message(20.0) or "").splitlines()
    assert lines[1] == "… 992 earlier steps" and lines[-1] == "$ step 999" and len(lines) == MAX_LINES + 2


def test_a_thought_is_the_last_line_the_newest_one_and_is_not_told_twice() -> None:
    progress: Progress = Progress(started=0.0)
    progress.thought("An old thought.")
    progress.step("$ ls")
    progress.thought("The newest thought.")
    progress.thought("")
    assert progress.message(20.0) == "Working for 20 s:\n$ ls\n💭 The newest thought."
    # Nothing new: the same thought alone is no reason to speak.
    progress.thought("The newest thought.")
    assert progress.due_at() is None
    progress.thought("A thought and nothing else.")
    assert progress.due_at() == 50.0
    assert progress.message(50.0) == "Working for 50 s:\n💭 A thought and nothing else."


def test_the_limits_can_be_given_other_values() -> None:
    progress: Progress = Progress(started=0.0, first_after_s=1.0, apart_s=2.0, max_messages=2)
    progress.step("$ a")
    assert progress.message(1.0) == "Working for 1 s:\n$ a"
    progress.step("$ b")
    assert progress.message(2.9) is None and progress.message(3.0) == "Working for 3 s:\n$ b"
    progress.step("$ c")
    assert progress.due_at() is None

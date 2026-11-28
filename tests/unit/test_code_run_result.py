"""What the model reads of a code that ended (item 1501), the names the tools have in Python, and the folders of old
runs that are removed: all of it with no process."""

import json
import os
import time
from pathlib import Path

import pytest

from dotagent.codemode import catalog, tool
from dotagent.codemode.runner import Ended, Limits
from dotagent.codemode.sandbox import Interpreter, Layout, layout_for
from dotagent.config import CodeConfig, load_default_config
from dotagent.core.paths import dot_home
from dotagent.llm.types import ToolSpec

CONFIG: CodeConfig = load_default_config().code
LIMITS: Limits = Limits(seconds=120.0, memory_mb=512, calls=200, window_bytes=32000)


def _ended(ending: str = "finished", output: str = "", **parts: object) -> Ended:
    base: dict[str, object] = {
        "ending": ending,
        "output": output,
        "printed_bytes": len(output.encode()),
        "exit_code": 0 if ending == "finished" else None,
        "signal": None,
        "detail": "",
        "calls": 3,
        "seconds": 1.25,
        "own_seconds": 0.5,
        "peak_mb": 20.0,
    }
    base.update(parts)
    return Ended(**base)  # type: ignore[arg-type]


def _layout(tmp_path: Path) -> tuple[Layout, Interpreter]:
    interpreter: Interpreter = Interpreter(
        executable=Path("/opt/py/cpython-3.13/bin/python3.13"), prefix=Path("/opt/py/cpython-3.13"), version="3.13.0"
    )
    return layout_for(tmp_path, "run_x"), interpreter


def test_a_code_that_ended_by_itself_is_what_it_printed_and_the_footer(tmp_path: Path) -> None:
    layout, interpreter = _layout(tmp_path)
    result = tool.result_of(_ended(output="6 rows\nfirst: 1\n"), CONFIG, LIMITS, layout, interpreter, False)
    assert (result.is_error, result.untrusted) == (False, False)
    assert result.text == "6 rows\nfirst: 1\n[3 tool calls, 1.2 s]"
    one = tool.result_of(_ended(output="x", calls=1), CONFIG, LIMITS, layout, interpreter, False)
    assert one.text == "x\n[1 tool call, 1.2 s]"
    silent = tool.result_of(_ended(), CONFIG, LIMITS, layout, interpreter, False)
    assert silent.text.startswith("(the code printed nothing: print what you need to see)") and not silent.is_error
    marked = tool.result_of(_ended(output="x"), CONFIG, LIMITS, layout, interpreter, True)
    assert marked.untrusted and not marked.is_error


@pytest.mark.parametrize(
    ("ending", "parts", "says"),
    [
        ("failed", {"exit_code": 1}, "The code failed (exit 1); the end of its traceback:"),
        ("failed", {"exit_code": None, "signal": 24}, "ended by signal 24 (SIGXCPU, the CPU time limit"),
        ("failed", {"exit_code": None, "signal": 11}, "ended by signal 11 (SIGSEGV)"),
        ("timeout", {"detail": "120 s"}, "it ran 0 s of its own time, the limit is 120 s (timeout_s, at most 120 s"),
        ("memory", {"detail": "600 MB held"}, "held more memory than 512 MB (600 MB held)"),
        (
            "calls",
            {"detail": "more than 200 tool calls"},
            "more than 200 tool calls in one code_run ([code] max_calls)",
        ),
        ("protocol", {"detail": "a frame that is not a call"}, "not a tool call (a frame that is not a call)"),
        ("stopped", {"detail": "the run was stopped"}, "because the run was stopped"),
        ("output", {"detail": "it printed more than 64 MiB"}, "it printed more than 64 MiB. Print less"),
        ("scratch", {"detail": "its scratch folder holds 300 MB"}, "its scratch folder holds 300 MB. Write less"),
        (
            "unstartable",
            {"detail": "OSError: no"},
            "code mode needs the macOS sandbox, which did not start: OSError: no",
        ),
    ],
)
def test_each_way_the_code_is_stopped_is_an_error_that_says_why_and_what_to_do(
    ending: str, parts: dict[str, object], says: str, tmp_path: Path
) -> None:
    layout, interpreter = _layout(tmp_path)
    result = tool.result_of(
        _ended(ending, "what it printed first\n", own_seconds=0.4, **parts), CONFIG, LIMITS, layout, interpreter, False
    )
    assert result.is_error and says in result.text
    assert result.text.splitlines()[-1] == "[3 tool calls, 1.2 s]"
    # What the code printed before it was stopped stays with the error, but for code that never started.
    assert ("what it printed first" in result.text) == (ending != "unstartable")


def test_the_traceback_names_no_path_of_dots_and_what_the_code_printed_is_left_as_it_wrote_it(tmp_path: Path) -> None:
    layout, interpreter = _layout(tmp_path)
    home: Path = dot_home()
    printed: str = f"my folder is {layout.scratch} and {home}\n"
    trace: str = (
        "Traceback (most recent call last):\n"
        f'  File "<code>", line 3, in <module>\n'
        f'  File "{layout.tools}/dot_tools/_bridge.py", line 99, in call\n'
        f'  File "{layout.scratch}/helper.py", line 2, in f\n'
        f'  File "{interpreter.prefix}/lib/python3.13/json/__init__.py", line 346, in loads\n'
        f'  File "{home}/runtime/x.py", line 1, in g\n'
        f'  File "{Path.home()}/Desktop/secret.py", line 1, in h\n'
        "dot_tools._bridge.ToolError: it failed\n"
    )
    result = tool.result_of(_ended("failed", printed + trace, exit_code=1), CONFIG, LIMITS, layout, interpreter, False)
    text: str = result.text
    assert printed.strip() in text
    after: str = text.partition("Traceback (most recent call last):")[2]
    assert str(layout.tools) not in after and str(layout.scratch) not in after and str(home) not in after
    assert str(Path.home()) not in after.replace("~", "")
    assert "<tools>/dot_tools/_bridge.py" in after and "<scratch>/helper.py" in after
    assert "<python>/lib/python3.13/json/__init__.py" in after and "<dot home>/runtime/x.py" in after
    assert "~/Desktop/secret.py" in after
    assert "dot_tools._bridge.ToolError: it failed" in after


@pytest.mark.parametrize(
    "last_line",
    [
        "PermissionError: [Errno 1] Operation not permitted: '{root}/memory-merge/C1.out.md'",
        "FileNotFoundError: [Errno 2] No such file or directory: 'allnotes.json'",
    ],
)
def test_a_code_that_could_not_open_a_file_is_told_that_it_opens_only_its_scratch_folder(
    tmp_path: Path, last_line: str
) -> None:
    layout, interpreter = _layout(tmp_path)
    trace: str = (
        "Traceback (most recent call last):\n"
        '  File "<code>", line 3, in <module>\n'
        f"{last_line.format(root=layout.root)}\n"
    )
    result = tool.result_of(_ended("failed", trace, exit_code=1), CONFIG, LIMITS, layout, interpreter, False)
    assert result.is_error
    assert tool.FILE_HINT in result.text
    assert result.text.index(tool.FILE_HINT) > result.text.index(last_line.split(":")[0])
    other = tool.result_of(
        _ended("failed", "Traceback (most recent call last):\nKeyError: 'x'\n", exit_code=1),
        CONFIG,
        LIMITS,
        layout,
        interpreter,
        False,
    )
    assert tool.FILE_HINT not in other.text
    printed = tool.result_of(_ended(output=f"{last_line}\n"), CONFIG, LIMITS, layout, interpreter, False)
    assert tool.FILE_HINT not in printed.text


def test_what_is_printed_is_cut_to_the_setting_as_a_head_and_a_tail_with_a_note(tmp_path: Path) -> None:
    layout, interpreter = _layout(tmp_path)
    lines: str = "".join(f"row {number} " + "y" * 40 + "\n" for number in range(3000))
    small: CodeConfig = CONFIG.model_copy(update={"output_chars": 1000})
    result = tool.result_of(_ended(output=lines, printed_bytes=len(lines)), small, LIMITS, layout, interpreter, False)
    body: str = result.text.rsplit("\n", 2)[0]
    assert len(body) <= 1000 + 120
    assert result.text.startswith("row 0 ") and "row 2999 " in result.text and "omitted" in result.text
    assert f"(the code printed {len(lines)} bytes; the head and the tail are shown)" in result.text


def test_the_settings_of_code_mode_have_the_defaults_and_the_bounds_the_docs_say() -> None:
    assert CONFIG.model_dump() == {
        "enabled": True,
        "max_seconds": 120,
        "max_mb": 512,
        "output_chars": 8000,
        "max_calls": 200,
        "result_chars": 5_000_000,
    }
    for key, bad in (("max_seconds", 901), ("max_seconds", 0), ("max_mb", 63), ("max_calls", 0), ("output_chars", 10)):
        with pytest.raises(ValueError, match=key):
            CodeConfig.model_validate({**CONFIG.model_dump(), key: bad})


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} does a thing", parameters={"type": "object", "properties": {}})


def test_the_tools_have_names_python_can_import_and_the_same_ones_every_time() -> None:
    names: list[str] = [
        "read_file",
        "mcp__github__create_issue",
        "mcp__web-research__web_scrape",
        "mcp__web-research__web-scrape",
        "mcp__dot__ping",
        "mcp__github__class",
        "mcp__github__2fa-code",
        "mcp__github__a-b",
        "mcp__github__a_b",
    ]
    built = catalog.build({name: _spec(name) for name in names})
    placed: dict[str, tuple[str, str]] = {entry.tool: (entry.module, entry.function) for entry in built}
    assert placed == {
        "read_file": ("dot", "read_file"),
        "mcp__github__create_issue": ("github", "create_issue"),
        # Two names that are one once written for Python: the one that sorts first keeps it, the other is numbered.
        "mcp__web-research__web-scrape": ("web_research", "web_scrape"),
        "mcp__web-research__web_scrape": ("web_research", "web_scrape_2"),
        "mcp__dot__ping": ("dot_mcp", "ping"),
        "mcp__github__class": ("github", "class_"),
        "mcp__github__2fa-code": ("github", "_2fa_code"),
        "mcp__github__a-b": ("github", "a_b"),
        "mcp__github__a_b": ("github", "a_b_2"),
    }
    assert all(entry.module.isidentifier() and entry.function.isidentifier() for entry in built)
    assert [entry.tool for entry in built] == sorted(names)
    assert catalog.build({name: _spec(name) for name in reversed(names)}) == built
    shipped = json.loads(catalog.render(built))
    assert [server["server"] for server in shipped["servers"]] == ["dot", "dot_mcp", "github", "web_research"]
    assert {server["server"]: server["tools"] for server in shipped["servers"]}["github"] == 5
    assert [server["kind"] for server in shipped["servers"]] == ["dot", "mcp", "mcp", "mcp"]


def test_a_run_folder_nothing_has_written_to_for_three_days_is_removed_once_and_a_link_is_not_followed(
    tmp_path: Path,
) -> None:
    root: Path = Path(os.path.realpath(tmp_path))
    layout: Layout = layout_for(root, "run_now")
    folder: Path = layout.run.parent
    old, recent, used = folder / "run_old", folder / "run_recent", folder / "run_used"
    for run in (layout.run, old, recent, used):
        (run / "tools" / "snippets").mkdir(parents=True)
        (run / "scratch").mkdir()
        (run / "scratch" / "kept.txt").write_text("x", encoding="utf-8")
    elsewhere: Path = root / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "file.txt").write_text("not a run's", encoding="utf-8")
    os.symlink(elsewhere, folder / "run_link")
    long_ago: float = time.time() - 5 * 24 * 3600
    for path in (old, old / "tools" / "snippets", used):
        os.utime(path, (long_ago, long_ago))
    os.utime(used / "tools" / "snippets", None)
    tool._pruned.discard(folder)
    tool._prune(layout)
    assert sorted(path.name for path in folder.iterdir()) == ["run_link", "run_now", "run_recent", "run_used"]
    assert (elsewhere / "file.txt").read_text(encoding="utf-8") == "not a run's"
    # Once for the daemon: a folder that grows old after that waits for the next start.
    os.utime(recent, (long_ago, long_ago))
    os.utime(recent / "tools" / "snippets", (long_ago, long_ago))
    tool._prune(layout)
    assert (folder / "run_recent").exists()
    tool._pruned.discard(folder)

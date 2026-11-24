"""The pieces of code mode's sandbox that need no process (item 1500): the profile as text, the folder of the Python
installation it may read, the environment the code gets, the output window, and the writes the daemon makes into the
run's folders, which a link must never lead elsewhere."""

import os
from pathlib import Path

import pytest

from dotagent.codemode import install, sandbox
from dotagent.codemode.runner import OutputWindow, child_environment
from dotagent.codemode.sandbox import Interpreter, Layout
from dotagent.codemode.tree import Tree


def _interpreter(prefix: str) -> Interpreter:
    return Interpreter(executable=Path(prefix) / "bin" / "python3.13", prefix=Path(prefix), version="3.13.0")


def _layout(root: Path) -> Layout:
    return sandbox.layout_for(root, "run_x")


def test_the_profile_refuses_everything_and_names_what_python_and_the_code_need(tmp_path: Path) -> None:
    layout: Layout = _layout(tmp_path)
    interpreter: Interpreter = _interpreter("/opt/py/cpython-3.13")
    lines: list[str] = sandbox.sandbox_profile(interpreter, layout).splitlines()
    assert lines[:2] == ["(version 1)", "(deny default)"]
    text: str = "\n".join(lines)
    # Nothing that opens the network, starts a process or reaches a service is allowed, and nothing is allowed whole.
    for forbidden in (
        "(allow default)",
        "network",
        "process-fork",
        "mach-lookup",
        "signal",
        "(allow file-read* file-write*)",
        "(allow file-write*)",
        "job-creation",
        "ipc-posix",
    ):
        assert forbidden not in text, forbidden
    assert '(allow process-exec (literal "/opt/py/cpython-3.13/bin/python3.13"))' in lines
    assert '(allow file-read* (subpath "/opt/py/cpython-3.13"))' in lines
    # The code reads its tools folder and writes its scratch folder, and no other place is written.
    assert f'(allow file-read* (subpath "{layout.tools}"))' in lines
    assert f'(allow file-read* file-write* (subpath "{layout.scratch}"))' in lines
    written: list[str] = [line for line in lines if "file-write" in line]
    assert len(written) == 2 and f'"{layout.scratch}"' in written[1] and "/dev/null" in written[0]
    assert "hostname" not in text and "kern.uuid" not in text


def test_a_path_with_a_quote_or_a_backslash_is_written_as_a_string_of_the_profile(tmp_path: Path) -> None:
    odd: Path = tmp_path / 'a "b" \\c'
    layout: Layout = _layout(odd)
    profile: str = sandbox.sandbox_profile(_interpreter("/opt/py/cpython-3.13"), layout)
    assert '\\"b\\"' in profile and "\\\\c" in profile
    assert profile.count("(") == profile.count(")")


def test_the_launch_words_start_the_sandbox_first_and_isolated_python_after_it(tmp_path: Path) -> None:
    layout: Layout = _layout(tmp_path)
    interpreter: Interpreter = _interpreter("/opt/py/cpython-3.13")
    words: tuple[str, ...] = sandbox.launch_words(interpreter, layout, layout.tools / "launcher.py", ("1", "2"))
    assert words[0] == sandbox.SANDBOX_EXEC and words[1] == "-p"
    assert words[3:] == (
        str(interpreter.executable),
        "-I",
        "-S",
        "-u",
        "-X",
        "utf8",
        str(layout.tools / "launcher.py"),
        "1",
        "2",
    )


@pytest.mark.parametrize(
    "prefix",
    ["/", "/usr", "/usr/local", "/opt/homebrew", "/Users/x", "/Users/x/.pyenv"],
)
def test_a_folder_of_python_too_broad_to_read_whole_is_refused(prefix: str, tmp_path: Path) -> None:
    assert "too broad" in (sandbox.prefix_problem(_interpreter(prefix), _layout(tmp_path)) or "")


def test_a_folder_of_python_that_holds_the_run_or_the_home_or_dots_own_is_refused(tmp_path: Path) -> None:
    layout: Layout = _layout(tmp_path)
    holding: Interpreter = Interpreter(executable=tmp_path / "bin" / "python", prefix=tmp_path, version="3.13.0")
    assert "which the code must not read" in (sandbox.prefix_problem(holding, layout) or "")
    elsewhere: Interpreter = _interpreter("/opt/one/two/three/four")
    assert sandbox.prefix_problem(elsewhere, layout) is None
    dots: Path = tmp_path / "Dot"
    dots.mkdir()
    inside: Interpreter = Interpreter(executable=tmp_path / "python", prefix=tmp_path, version="3.13.0")
    assert "holds" in (sandbox.prefix_problem(inside, _layout(dots), (dots,)) or "")


def test_the_interpreter_is_the_base_one_by_its_real_path_and_never_a_virtual_environments() -> None:
    interpreter: Interpreter = sandbox.current_interpreter()
    assert interpreter.executable == Path(os.path.realpath(interpreter.executable))
    assert interpreter.prefix == Path(os.path.realpath(interpreter.prefix))
    assert not any(part == ".venv" for part in interpreter.executable.parts)
    assert interpreter.prefix in interpreter.executable.parents


def test_the_environment_of_the_code_is_its_two_folders_and_the_language_and_zone_and_no_secret(tmp_path: Path) -> None:
    layout: Layout = _layout(tmp_path)
    parent: dict[str, str] = {
        "PATH": "/usr/bin",
        "HOME": "/Users/someone",
        "USER": "someone",
        "TZ": "Europe/Helsinki",
        "LANG": "fi_FI.UTF-8",
        "GITHUB_TOKEN": "ghp_x",
        "OPENAI_API_KEY": "sk-x",
        "AWS_SECRET_ACCESS_KEY": "x",
        "DATABASE_URL": "postgres://u:pw@h/d",
        "SSH_AUTH_SOCK": "/tmp/agent",
    }
    assert child_environment(layout, parent) == {
        "LANG": "fi_FI.UTF-8",
        "TZ": "Europe/Helsinki",
        "HOME": str(layout.scratch),
        "TMPDIR": str(layout.scratch),
    }
    assert child_environment(layout, {}) == {
        "LANG": "en_US.UTF-8",
        "HOME": str(layout.scratch),
        "TMPDIR": str(layout.scratch),
    }


def test_the_output_window_keeps_the_head_and_the_tail_and_says_how_much_it_dropped() -> None:
    window: OutputWindow = OutputWindow(10, 10)
    for chunk in (b"0123456789", b"abcdefghij", b"KLMNOPQRST", b"uvwxyz"):
        window.feed(chunk)
    assert (window.total, window.dropped) == (36, 16)
    assert window.render() == "0123456789\n[... 16 bytes of output omitted ...]\nQRSTuvwxyz"
    short: OutputWindow = OutputWindow(100, 100)
    short.feed("héllo \U0001f600".encode())
    assert (short.render(), short.dropped) == ("héllo \U0001f600", 0)
    # A cut inside a character shows as the replacement sign and is no error.
    cut: OutputWindow = OutputWindow(3, 3)
    cut.feed("ä".encode() * 6)
    assert cut.render().startswith("ä\ufffd")


def test_the_run_folders_and_the_files_every_run_has_are_written_and_the_launcher_is_the_packages_own(
    tmp_path: Path,
) -> None:
    layout: Layout = _layout(tmp_path)
    install.install(layout)
    here: Path = Path(install.__file__).parent
    assert (layout.tools / "launcher.py").read_bytes() == (here / "child_launcher.py").read_bytes()
    assert (layout.tools / "dot_tools" / "_bridge.py").read_bytes() == (here / "child_bridge.py").read_bytes()
    assert (layout.tools / "dot_tools" / "__init__.py").read_bytes() == (here / "child_tools_init.py").read_bytes()
    assert (layout.tools / "dot_tools" / "_catalog.json").read_bytes() == install.EMPTY_CATALOG
    assert layout.scratch.is_dir() and oct(layout.run.stat().st_mode & 0o777) == "0o700"
    # Written again before every start: a file changed in between is what the package holds again.
    (layout.tools / "launcher.py").write_text("tampered", encoding="utf-8")
    install.install(layout)
    assert (layout.tools / "launcher.py").read_bytes() == (here / "child_launcher.py").read_bytes()
    assert not list(layout.tools.rglob("*.staged"))


def test_a_link_where_a_folder_or_a_file_is_to_be_is_not_followed_and_nothing_is_written_through_it(
    tmp_path: Path,
) -> None:
    root: Path = Path(os.path.realpath(tmp_path)) / "workspace"
    root.mkdir()
    victim: Path = Path(os.path.realpath(tmp_path)) / "victim"
    victim.mkdir()
    (victim / "file.txt").write_text("the owner's file", encoding="utf-8")
    layout: Layout = _layout(root)
    # A folder of the run that is a link to the victim: the write is a failed write, and the victim is as it was.
    (root / "code").mkdir()
    os.symlink(victim, root / "code" / "run_x")
    with pytest.raises(OSError):
        install.install(layout)
    assert sorted(path.name for path in victim.iterdir()) == ["file.txt"]
    (root / "code" / "run_x").unlink()
    # A file of the tools folder that is a link: the link is replaced by the file, and what it led to is untouched.
    install.install(layout)
    (layout.tools / "launcher.py").unlink()
    os.symlink(victim / "file.txt", layout.tools / "launcher.py")
    install.install(layout)
    assert (victim / "file.txt").read_text(encoding="utf-8") == "the owner's file"
    assert not (layout.tools / "launcher.py").is_symlink()
    # The scratch folder swapped for a link, as a command of the run could: no picture or file is written through it.
    (layout.scratch).rmdir()
    os.symlink(victim, layout.scratch)
    with Tree(root) as tree, pytest.raises(OSError):
        tree.write(("code", "run_x", "scratch", "image.png"), b"png")
    assert sorted(path.name for path in victim.iterdir()) == ["file.txt"]


def test_a_stale_staging_file_from_a_cut_write_does_not_stop_the_next(tmp_path: Path) -> None:
    root: Path = Path(os.path.realpath(tmp_path))
    with Tree(root) as tree:
        tree.write(("a", "b.txt"), b"one")
        (root / "a" / f".b.txt.{os.getpid()}.staged").write_bytes(b"left by a cut write")
        tree.write(("a", "b.txt"), b"two")
    assert (root / "a" / "b.txt").read_bytes() == b"two"
    assert [path.name for path in (root / "a").iterdir()] == ["b.txt"]

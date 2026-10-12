"""What ripgrep says of the paths it did not read: a search shows what it found and names them, and anything else
ripgrep says still fails the search."""

from dotagent.tools.files import _unread

HOME: str = "/Users/someone"


def test_a_folder_macos_broke_off_reading_is_named_with_those_it_refused() -> None:
    # A listing of a home folder: macOS refuses the folders it guards and now and then breaks off reading a container.
    stderr: str = (
        f"rg: {HOME}/Library/Mail: Operation not permitted (os error 1)\n"
        f"rg: {HOME}/Library/Containers/app/Data/Library: Interrupted system call (os error 4)\n"
    )
    assert _unread(stderr) == [
        f"{HOME}/Library/Mail: Operation not permitted (os error 1)",
        f"{HOME}/Library/Containers/app/Data/Library: Interrupted system call (os error 4)",
    ]


def test_anything_else_ripgrep_says_still_fails_the_search() -> None:
    interrupted: str = f"rg: {HOME}/Library/Containers/app/Data: Interrupted system call (os error 4)\n"
    assert _unread(interrupted + f"rg: {HOME}/gone: No such file or directory (os error 2)\n") is None
    assert _unread("rg: regex parse error:\n    (\n    ^\nerror: unclosed group\n") is None

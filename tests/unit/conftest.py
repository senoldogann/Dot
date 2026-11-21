"""The unit tests that drive macOS itself (AppKit, Quartz, the accessibility API, ScreenCaptureKit, VideoToolbox) are
left out where those frameworks are not, as on the Linux a cloud agent works on (item 1270); every other unit test runs
there. A module is listed here only when what it tests is the desktop's own: one that reaches the frameworks through
code that only names a type or a constant is fixed in that code instead. The gate runs the list on macOS as every other
test, and a listed file that is gone stops the collection, so the list cannot leave a test out by a name it no longer
has.
"""

import importlib.util
from pathlib import Path

import pytest

MACOS_ONLY: tuple[str, ...] = (
    # Nearly all of their tests read paths the way macOS's kernel names them (``O_EVTONLY``, ``F_GETPATH``: the shell
    # guard, the files of the pages and of the channels) or draw with Quartz.
    "test_channel_pictures.py",
    "test_compression.py",
    "test_dot_files.py",
    "test_edit_file_ambiguous.py",
    "test_goal_mode_default.py",
    "test_kernel_paths.py",
    "test_notes_file.py",
    "test_open_plan_finish.py",
    "test_page_by_path.py",
    "test_read_pdf.py",
    "test_service_install_from_a_terminal.py",
    "test_shell_guard_zsh.py",
    "test_shell_policy.py",
    "test_shell_release.py",
    "test_shell_senders.py",
    "test_third_party_files.py",
    "test_vault_shell.py",
    # The desktop's own.
    "test_browser_step_meaning.py",
    "test_clipboard_reads.py",
    "test_closed_pages.py",
    "test_desktop_apps.py",
    "test_desktop_batch_front.py",
    "test_desktop_failures.py",
    "test_desktop_front.py",
    "test_desktop_outcomes.py",
    "test_desktop_own_windows.py",
    "test_desktop_paste.py",
    "test_desktop_pointer.py",
    "test_desktop_press_click.py",
    "test_desktop_readback.py",
    "test_desktop_review.py",
    "test_desktop_screenshots.py",
    "test_desktop_secrets.py",
    "test_desktop_stop.py",
    "test_desktop_terminal_steps.py",
    "test_guard.py",
    "test_imessage_channel.py",
    "test_live_screen.py",
    "test_qr.py",
    "test_scaffold.py",
    "test_screen_text.py",
    "test_screenshot_redaction_order.py",
    "test_snapshots.py",
    "test_tool_groups.py",
)

_GONE: list[str] = [name for name in MACOS_ONLY if not (Path(__file__).parent / name).is_file()]
if _GONE:
    raise FileNotFoundError(f"tests/unit/conftest.py lists macOS-only tests that are not there: {_GONE}")


def _frameworks_here() -> bool:
    """Whether PyObjC's AppKit can be imported here. An import hook that refuses the name (a run that stands in for
    Linux on a Mac) raises rather than answering None."""
    try:
        return importlib.util.find_spec("AppKit") is not None
    except ModuleNotFoundError:
        return False


collect_ignore: list[str] = [] if _frameworks_here() else list(MACOS_ONLY)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A test marked ``macos`` (one of a module whose other tests run anywhere) is skipped where the frameworks are
    not."""
    if _frameworks_here():
        return
    skip: pytest.MarkDecorator = pytest.mark.skip(reason="needs macOS (its kernel's paths, AppKit or Quartz)")
    for item in items:
        if item.get_closest_marker("macos") is not None:
            item.add_marker(skip)

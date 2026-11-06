"""What every smoke test shares: the ``opencode`` program Dot would start for Zen's free models (item 283) is one that
does not exist, so that no test starts the owner's OpenCode or reaches Zen by listing models, and so is the ``claude``
program a run on the owner's Claude plan starts (item 1013); the live tests of
OpenCode point it back at the real one. Tailscale's command line and ``caffeinate`` (item 271) are ones that do not
exist too, so that no test reads or changes the owner's tailnet or keeps the Mac awake, and so is the ``open`` that
Settings › Phone opens the App Store or Tailscale with (item 421); the tests of Settings › Phone point them at
stand-ins. The tests of the accounts signed in (items 422 and 424) keep their tokens in a keychain of their own
(``keychain_file``). A test that opens the Connectors page for its layout alone has macOS answer at once
(``mac_at_rest``): the page waits for ``GET /api/connections``, which asks macOS's permission service about each
application that runs, and that service takes seconds when the Mac is busy. The Swift compiler that builds the program
which writes a recording down (item 626) is one that does not exist too, so that no test builds Swift, opens Apple's
frameworks or asks the owner's developer tools whether this Mac can write down: every page a browser test opens asks
``GET /api/voice``, and answers that the Mac cannot. The tests of the microphone's recording point it at a stand-in.

``--part k/n`` keeps the files of one part of n (item 944): the gate runs the smoke tests in two parts, on two runners
at once, the files dealt to the parts in turn by their sorted paths, so that a new file joins a part by itself."""

import fcntl
import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from dotagent.connections import mac
from dotagent.connections.mac import AutomationState
from dotagent.desktop import perms

MISSING_OPENCODE: str = "/nonexistent/dot-tests/opencode"
MISSING_CLAUDE: str = "/nonexistent/dot-tests/claude"
MISSING_TAILSCALE: str = "/nonexistent/dot-tests/tailscale"
MISSING_CAFFEINATE: str = "/nonexistent/dot-tests/caffeinate"
MISSING_OPEN: str = "/nonexistent/dot-tests/open"
MISSING_SWIFTC: str = "/nonexistent/dot-tests/swiftc"
# The keychain the accounts of the official sign-ins keep their tokens in (item 422): one that does not exist, which the
# Keychain refuses before ``security`` runs (item 628: ``security`` itself would use the login keychain in its place),
# so that no test reads or writes the owner's login keychain through them; the tests of the sign-ins make their own.
MISSING_KEYCHAIN: str = "/nonexistent/dot-tests/accounts.keychain-db"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--part", action="store", default=None, help="k/n: run only the files of part k of n (item 944)")


def files_of_part(files: list[str], part: int, parts: int) -> set[str]:
    """The files part ``part`` (from 1) of ``parts`` runs: every ``parts``-th of the sorted paths, from its own."""
    if not 1 <= part <= parts:
        raise ValueError(f"part {part} is not one of 1 to {parts}")
    return set(sorted(set(files))[part - 1 :: parts])


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    said: str | None = config.getoption("--part")
    if said is None:
        return
    part_text, _, parts_text = said.partition("/")
    if not (part_text.isdigit() and parts_text.isdigit()):
        raise pytest.UsageError(f"--part takes k/n, as 1/2, not {said!r}")
    kept_files: set[str] = files_of_part(
        [item.nodeid.split("::")[0] for item in items], int(part_text), int(parts_text)
    )
    kept: list[pytest.Item] = [item for item in items if item.nodeid.split("::")[0] in kept_files]
    dropped: list[pytest.Item] = [item for item in items if item.nodeid.split("::")[0] not in kept_files]
    config.hook.pytest_deselected(items=dropped)
    items[:] = kept


def pytest_configure(config: pytest.Config) -> None:
    os.environ["DOT_OPENCODE"] = MISSING_OPENCODE
    os.environ["DOT_CLAUDE"] = MISSING_CLAUDE
    os.environ["DOT_TAILSCALE"] = MISSING_TAILSCALE
    os.environ["DOT_CAFFEINATE"] = MISSING_CAFFEINATE
    os.environ["DOT_OPEN"] = MISSING_OPEN
    os.environ["DOT_SWIFTC"] = MISSING_SWIFTC
    os.environ["DOT_KEYCHAIN"] = MISSING_KEYCHAIN


KEYCHAIN_PASSWORD: str = "dot-test"
_SECURITY: str = "/usr/bin/security"
# Held while a test's keychain is made or removed, so that the workers put the owner's search list back one at a time.
_KEYCHAINS_LOCK: Path = Path(tempfile.gettempdir()) / "dot-tests-keychains.lock"


def _search_list() -> list[str]:
    said: str = subprocess.run(
        [_SECURITY, "list-keychains", "-d", "user"], check=True, capture_output=True, text=True
    ).stdout
    return [line.strip().strip('"') for line in said.splitlines() if line.strip()]


def _keeping_search_list(arguments: list[str]) -> None:
    """Run ``security`` with ``arguments`` and put the owner's search list back as it was, should they change it
    (``security``'s manual says making a keychain adds it to the list)."""
    with _KEYCHAINS_LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            before: list[str] = _search_list()
            subprocess.run([_SECURITY, *arguments], check=True, capture_output=True)
            if _search_list() != before:
                subprocess.run(
                    [_SECURITY, "list-keychains", "-d", "user", "-s", *before], check=True, capture_output=True
                )
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@pytest.fixture
def keychain_file(tmp_path: Path) -> Iterator[str]:
    """A keychain file of the test's own, unlocked, removed at the end; the owner's search list is left as it was."""
    file: str = str(tmp_path / "test.keychain-db")
    _keeping_search_list(["create-keychain", "-p", KEYCHAIN_PASSWORD, file])
    subprocess.run([_SECURITY, "unlock-keychain", "-p", KEYCHAIN_PASSWORD, file], check=True, capture_output=True)
    try:
        yield file
    finally:
        _keeping_search_list(["delete-keychain", file])


def _not_running(bundle_id: str) -> AutomationState:
    return "not_open"


def _installed(bundle_id: str) -> Path:
    return Path("/Applications") / f"{bundle_id}.app"


def _dot_app() -> Path:
    return Path("/Users/someone/Library/Application Support/Dot/Dot.app")


@pytest.fixture
def mac_at_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS as a Mac at rest answers the Connectors page: every application installed and none running, Dot.app the
    program responsible. The page reads these once and at once, whatever else the Mac is doing."""
    monkeypatch.setattr(mac, "automation_state", _not_running)
    monkeypatch.setattr(mac, "installed_app", _installed)
    monkeypatch.setattr(perms, "responsible_program", _dot_app)

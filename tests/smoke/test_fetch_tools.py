"""The tools the desktop app carries (item 575), fetched and made by ``desktop/fetch-tools.sh``, which
``desktop/release.sh`` runs before it builds the app. The archives are ones the test makes (two thin programs for each
tool, one per kind of Mac) and serves from a folder, with a lock file of its own that pins the SHA-256 of each archive
and of each program in it, so that nothing is downloaded. It checks what a release depends on:
- an archive that is not the pinned one, or a program in it that is not the pinned one (an archive swapped after it was
  checked would give another program), stops everything and leaves nothing behind;
- a dry run makes nothing;
- the real run makes one program of both architectures for each tool, builds the launcher from the source the package
  carries, and puts them, executable, where the app's build takes its resources;
- it empties that folder of everything but its README first, so that a file planted there does not ship (the review of
  item 575: the folder is first on the PATH of the service), and refuses to empty a folder that is not that one;
- the tools folder of a built app is checked to hold exactly the three programs.
Nothing is signed here: a release signs with the owner's Developer ID.

``test_the_pinned_archives_are_still_the_pinned_ones`` is the one that reaches the internet: it checks the real lock
file against the real releases, so that a moved or removed asset is found before a release is."""

import hashlib
import io
import os
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

_DESKTOP: Path = Path(__file__).resolve().parents[2] / "desktop"
_FETCH: Path = _DESKTOP / "fetch-tools.sh"
_RELEASE: Path = _DESKTOP / "release.sh"
# The tests' own lock files serve their archives from files: the script takes those addresses only when asked.
_FILES: str = "--allow-file-addresses"
_NOTE: Path = _DESKTOP / "src-tauri" / "resources" / "tools" / "README.md"
_XCRUN: str = "/usr/bin/xcrun"
_TOOLS: tuple[str, ...] = ("uv", "rg")
_ARCHS: tuple[tuple[str, str], ...] = (("aarch64", "arm64"), ("x86_64", "x86_64"))
_PROGRAMS: tuple[str, ...] = ("dot-launcher", "rg", "uv")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=300)


def _program(folder: Path, name: str, clang_arch: str) -> bytes:
    """A real thin program for one kind of Mac, so that lipo can join two."""
    source: Path = folder / f"{name}-{clang_arch}.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    built: Path = folder / f"{name}-{clang_arch}"
    compiled: subprocess.CompletedProcess[str] = _run(
        [_XCRUN, "clang", "-arch", clang_arch, "-o", str(built), str(source)]
    )
    assert compiled.returncode == 0, compiled.stderr
    return built.read_bytes()


def _archive(path: Path, member: str, program: bytes) -> None:
    """A ``.tar.gz`` of one program under ``member``."""
    with tarfile.open(path, "w:gz") as tar:
        info: tarfile.TarInfo = tarfile.TarInfo(member)
        info.size = len(program)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(program))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class StandIns:
    """The archives the test serves, and the SHA-256 of the program in each."""

    folder: Path
    programs: dict[tuple[str, str], str]


def _lock(
    stand_ins: StandIns,
    archives: dict[tuple[str, str], str] | None = None,
    programs: dict[tuple[str, str], str] | None = None,
) -> Path:
    """A lock file for the archives of ``stand_ins``. ``archives`` and ``programs`` replace a pinned SHA-256."""
    lines: list[str] = ["# the stand-ins of the test"]
    for tool in _TOOLS:
        for arch, _ in _ARCHS:
            archive: Path = stand_ins.folder / f"{tool}-{arch}.tar.gz"
            sha: str = (archives or {}).get((tool, arch), _sha(archive.read_bytes()))
            program: str = (programs or {}).get((tool, arch), stand_ins.programs[(tool, arch)])
            lines.append(f"{tool}  1.0.0  {arch}  {sha}  {archive.as_uri()}  {tool}-{arch}/{tool}  {program}")
    lock: Path = stand_ins.folder / "tools.lock"
    lock.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lock


@pytest.fixture(scope="module")
def stand_ins(tmp_path_factory: pytest.TempPathFactory) -> StandIns:
    folder: Path = tmp_path_factory.mktemp("tool-archives")
    programs: dict[tuple[str, str], str] = {}
    for tool in _TOOLS:
        for arch, clang_arch in _ARCHS:
            program: bytes = _program(folder, tool, clang_arch)
            _archive(folder / f"{tool}-{arch}.tar.gz", f"{tool}-{arch}/{tool}", program)
            programs[(tool, arch)] = _sha(program)
    return StandIns(folder, programs)


def test_a_dry_run_checks_the_archives_and_their_programs_and_makes_nothing(
    stand_ins: StandIns, tmp_path: Path
) -> None:
    out: Path = tmp_path / "tools"
    done: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), "--dry-run", _FILES, "--lock", str(_lock(stand_ins)), "--out", str(out)]
    )
    assert done.returncode == 0, done.stderr
    for tool in _TOOLS:
        for arch, _ in _ARCHS:
            assert f"{tool} 1.0.0 {arch}: archive and program SHA-256 as pinned" in done.stdout
    assert "nothing was built or put in" in done.stdout
    assert not out.exists()


def test_release_check_tools_runs_the_dry_run_of_the_lock_it_is_given_and_passes_on_nothing_else(
    stand_ins: StandIns, tmp_path: Path
) -> None:
    """The review's nit: the script forwarded every argument after ``--check-tools`` to ``fetch-tools.sh``, so that the
    switch that takes file addresses (the tests' own) could be given through the release script. It passes on a lock
    file and nothing else; what it is given is checked by what it does, not by what the script says."""
    lock: Path = _lock(stand_ins)
    out: Path = tmp_path / "tools"
    # A lock file of another name is given to the dry run: the stand-ins' lock names files, which the release does not
    # take, so the dry run's own words refuse it, and no archive was read.
    refused: subprocess.CompletedProcess[str] = _run([str(_RELEASE), "--check-tools", "--lock", str(lock)])
    assert refused.returncode != 0
    assert "is a file address, and the release takes https addresses only" in refused.stderr, refused.stderr
    assert "SHA-256 as pinned" not in refused.stdout
    # Nothing else is passed on, in any place or number: the switch for file addresses least of all.
    for arguments in (
        ["--allow-file-addresses", "--lock", str(lock)],
        ["--lock", str(lock), "--allow-file-addresses"],
        ["--allow-file-addresses"],
        ["--lock"],
        ["--dry-run"],
        ["--lock", str(lock), "--out", str(out)],
    ):
        stopped: subprocess.CompletedProcess[str] = _run([str(_RELEASE), "--check-tools", *arguments])
        assert stopped.returncode != 0 and "takes no argument but --lock FILE" in stopped.stderr, arguments
        assert "SHA-256 as pinned" not in stopped.stdout and "file address" not in stopped.stderr, arguments
    assert not out.exists()


def test_an_archive_that_is_not_the_pinned_one_stops_everything_and_leaves_nothing(
    stand_ins: StandIns, tmp_path: Path
) -> None:
    out: Path = tmp_path / "tools"
    wrong: str = "0" * 64
    refused: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), _FILES, "--lock", str(_lock(stand_ins, archives={("rg", "x86_64"): wrong})), "--out", str(out)]
    )
    assert refused.returncode != 0
    assert "rg 1.0.0 for x86_64" in refused.stderr and f"not the pinned {wrong}" in refused.stderr
    assert "nothing was made" in refused.stderr
    assert not out.exists()


def test_a_program_that_is_not_the_pinned_one_is_caught_though_its_archive_passes(
    stand_ins: StandIns, tmp_path: Path
) -> None:
    """An archive swapped after it was checked (or one whose own hash the attacker could pin) gives another program:
    the program's own SHA-256, pinned apart from the archive's, is what catches it. Here the archive is the pinned one
    and the program in it is not."""
    out: Path = tmp_path / "tools"
    wrong: str = "1" * 64
    refused: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), _FILES, "--lock", str(_lock(stand_ins, programs={("uv", "aarch64"): wrong})), "--out", str(out)]
    )
    assert refused.returncode != 0
    assert "uv/uv" not in refused.stdout
    assert "of uv 1.0.0 for aarch64 has SHA-256" in refused.stderr and f"not the pinned {wrong}" in refused.stderr
    assert not out.exists()


def test_an_archive_swapped_after_its_check_is_caught_by_the_programs_hash(stand_ins: StandIns, tmp_path: Path) -> None:
    """The archive is read once: the bytes that are hashed are the bytes that are unpacked. Served from a folder the
    test changes between two runs, a swapped archive whose hash was pinned for the first one is refused by the hash, and
    one whose own hash was pinned afresh is refused by the program's."""
    folder: Path = tmp_path / "served"
    folder.mkdir()
    programs: dict[tuple[str, str], str] = {}
    for tool in _TOOLS:
        for arch, clang_arch in _ARCHS:
            program: bytes = _program(folder, tool, clang_arch)
            _archive(folder / f"{tool}-{arch}.tar.gz", f"{tool}-{arch}/{tool}", program)
            programs[(tool, arch)] = _sha(program)
    served: StandIns = StandIns(folder, programs)
    lock: Path = _lock(served)
    swapped: Path = folder / "uv-aarch64.tar.gz"
    _archive(swapped, "uv-aarch64/uv", b"\xcf\xfa\xed\xfe not the program that was pinned")
    out: Path = tmp_path / "tools"
    by_archive: subprocess.CompletedProcess[str] = _run([str(_FETCH), _FILES, "--lock", str(lock), "--out", str(out)])
    assert by_archive.returncode != 0 and "uv 1.0.0 for aarch64" in by_archive.stderr
    # The attacker also gives the archive's hash a new pin: the program's pin is the second lock.
    by_program: subprocess.CompletedProcess[str] = _run(
        [
            str(_FETCH),
            _FILES,
            "--lock",
            str(_lock(served, archives={("uv", "aarch64"): _sha(swapped.read_bytes())})),
            "--out",
            str(out),
        ]
    )
    assert by_program.returncode != 0 and "uv 1.0.0 for aarch64 has SHA-256" in by_program.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    ("line", "says"),
    [
        ("uv  1.0.0  aarch64  {sha}  http://example.com/uv.tar.gz  uv-aarch64/uv  {sha}", "not an https address"),
        ("uv  1.0.0  aarch64  {sha}  {uri}  ../uv  {sha}", "not a path inside its archive"),
        ("uv  1.0.0  aarch64  {sha}  {uri}  /usr/bin/uv  {sha}", "not a path inside its archive"),
        ("uv  1.0.0  arm64  {sha}  {uri}  uv-aarch64/uv  {sha}", "not aarch64 or x86_64"),
        ("uv  1.0.0  aarch64  NOTHEX  {uri}  uv-aarch64/uv  {sha}", "not 64 lowercase hex digits"),
        ("uv  1.0.0  aarch64  {sha}  {uri}  uv-aarch64/uv  NOTHEX", "not 64 lowercase hex digits"),
        ("uv  1.0.0  aarch64  {sha}  {uri}  uv-aarch64/uv", "not seven fields"),
        ("uv  1.0.0  aarch64  {sha}  {uri}  uv-aarch64/uv  {sha}  more", "not seven fields"),
    ],
)
def test_a_lock_line_that_is_not_what_the_script_takes_is_refused(
    stand_ins: StandIns, tmp_path: Path, line: str, says: str
) -> None:
    archive: Path = stand_ins.folder / "uv-aarch64.tar.gz"
    lock: Path = tmp_path / "tools.lock"
    lock.write_text(line.format(sha=_sha(archive.read_bytes()), uri=archive.as_uri()) + "\n", encoding="utf-8")
    refused: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), "--dry-run", _FILES, "--lock", str(lock), "--out", str(tmp_path / "tools")]
    )
    assert refused.returncode != 0 and says in refused.stderr, refused.stderr


def test_a_tool_pinned_for_one_kind_of_mac_only_is_refused(stand_ins: StandIns, tmp_path: Path) -> None:
    lock: Path = _lock(stand_ins)
    kept: list[str] = [
        line for line in lock.read_text(encoding="utf-8").splitlines() if "uv  1.0.0  x86_64" not in line
    ]
    lock.write_text("\n".join(kept) + "\n", encoding="utf-8")
    refused: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), "--dry-run", _FILES, "--lock", str(lock), "--out", str(tmp_path / "tools")]
    )
    assert refused.returncode != 0 and "pins uv for one architecture only" in refused.stderr, refused.stderr


def _made_in(out: Path) -> None:
    assert sorted(path.name for path in out.iterdir()) == ["README.md", "dot-launcher", "rg", "uv"]
    for name in _PROGRAMS:
        program: Path = out / name
        assert os.access(program, os.X_OK) and program.stat().st_mode & 0o777 == 0o755, name
        archs: subprocess.CompletedProcess[str] = _run(["/usr/bin/lipo", "-archs", str(program)])
        assert sorted(archs.stdout.split()) == ["arm64", "x86_64"], (name, archs.stdout, archs.stderr)


def test_the_real_run_makes_one_universal_program_of_each_tool_and_the_launcher(
    stand_ins: StandIns, tmp_path: Path
) -> None:
    out: Path = tmp_path / "tools"
    out.mkdir()
    note: Path = out / "README.md"
    note.write_text(_NOTE.read_text(encoding="utf-8"), encoding="utf-8")
    done: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), _FILES, "--lock", str(_lock(stand_ins)), "--out", str(out)]
    )
    assert done.returncode == 0, done.stderr
    _made_in(out)
    assert note.read_text(encoding="utf-8") == _NOTE.read_text(encoding="utf-8")
    # The launcher is the one the package's source makes: it starts its argument and ends as its child ended.
    ended: subprocess.CompletedProcess[bytes] = subprocess.run(
        [str(out / "dot-launcher"), "/bin/sh", "-c", "exit 3"], check=False
    )
    assert ended.returncode == 3


def test_what_was_planted_in_the_tools_folder_is_removed_before_the_three_are_written(
    stand_ins: StandIns, tmp_path: Path
) -> None:
    """The folder is bundled and first on the PATH of the service (the review of item 575): a script named ``op`` left
    in it must not survive a run of the script, whatever it is: a file, a link, a folder, a stand-in of a tool."""
    out: Path = tmp_path / "tools"
    out.mkdir()
    (out / "README.md").write_text(_NOTE.read_text(encoding="utf-8"), encoding="utf-8")
    (out / "op").write_text("#!/bin/sh\necho planted\n", encoding="utf-8")
    os.chmod(out / "op", 0o755)
    (out / "git").symlink_to("/usr/bin/true")
    (out / ".hidden").write_text("planted", encoding="utf-8")
    (out / "bin").mkdir()
    (out / "bin" / "python3").write_text("planted", encoding="utf-8")
    (out / "uv").write_text("an empty stand-in", encoding="utf-8")
    done: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), _FILES, "--lock", str(_lock(stand_ins)), "--out", str(out)]
    )
    assert done.returncode == 0, done.stderr
    _made_in(out)
    assert (out / "uv").read_bytes() != b"an empty stand-in"


def test_a_folder_that_is_not_the_tools_folder_is_not_emptied(stand_ins: StandIns, tmp_path: Path) -> None:
    """A wrong ``--out`` must not cost a person their files: only a folder with the README of the tools folder is
    emptied, and a link is not followed."""
    wrong: Path = tmp_path / "documents"
    wrong.mkdir()
    (wrong / "thesis.txt").write_text("a person's own file", encoding="utf-8")
    refused: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), _FILES, "--lock", str(_lock(stand_ins)), "--out", str(wrong)]
    )
    assert refused.returncode != 0 and "is not the folder of the app's tools" in refused.stderr, refused.stderr
    assert [path.name for path in wrong.iterdir()] == ["thesis.txt"]
    linked: Path = tmp_path / "linked"
    linked.symlink_to(wrong)
    refused = _run([str(_FETCH), _FILES, "--lock", str(_lock(stand_ins)), "--out", str(linked)])
    assert refused.returncode != 0 and "is a link" in refused.stderr, refused.stderr
    assert [path.name for path in wrong.iterdir()] == ["thesis.txt"]


def _app_tools(folder: Path, names: tuple[str, ...] = _PROGRAMS) -> Path:
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(folder / name, 0o755)
    return folder


def test_the_release_checks_that_the_apps_tools_folder_holds_exactly_the_three_programs(tmp_path: Path) -> None:
    """``release.sh`` runs this on the app it built (``fetch-tools.sh --check-bundle``): an extra file, a link, a nested
    folder, a missing program or one that cannot be run stops the release."""
    good: Path = _app_tools(tmp_path / "good")
    done: subprocess.CompletedProcess[str] = _run([str(_FETCH), "--check-bundle", str(good)])
    assert done.returncode == 0 and "holds exactly dot-launcher rg uv" in done.stdout, done.stderr

    extra: Path = _app_tools(tmp_path / "extra", (*_PROGRAMS, "op"))
    refused: subprocess.CompletedProcess[str] = _run([str(_FETCH), "--check-bundle", str(extra)])
    assert refused.returncode != 0 and "holds: dot-launcher op rg uv" in refused.stderr, refused.stderr

    missing: Path = _app_tools(tmp_path / "missing", ("rg", "uv"))
    assert _run([str(_FETCH), "--check-bundle", str(missing)]).returncode != 0

    nested: Path = _app_tools(tmp_path / "nested")
    (nested / "more").mkdir()
    (nested / "more" / "op").write_text("planted", encoding="utf-8")
    assert _run([str(_FETCH), "--check-bundle", str(nested)]).returncode != 0

    linked: Path = _app_tools(tmp_path / "linked", ("dot-launcher", "rg"))
    (linked / "uv").symlink_to("/usr/bin/true")
    refused = _run([str(_FETCH), "--check-bundle", str(linked)])
    assert refused.returncode != 0 and "is not a program of its own" in refused.stderr, refused.stderr

    inert: Path = _app_tools(tmp_path / "inert")
    os.chmod(inert / "rg", 0o644)
    assert _run([str(_FETCH), "--check-bundle", str(inert)]).returncode != 0
    assert _run([str(_FETCH), "--check-bundle", str(tmp_path / "nothing-here")]).returncode != 0


def test_a_file_address_is_taken_only_when_the_tests_ask_for_it_and_the_release_never_does(
    stand_ins: StandIns, tmp_path: Path
) -> None:
    """The review's nit: the script took ``file://`` addresses and called anything but https "not an https address". A
    file address is for the tests' own lock files now, behind a switch of its own; without it the lock is refused in
    words that say so, and the release's lock file has none (the release script cannot give the switch, which the test
    of ``--check-tools`` above runs)."""
    lock: Path = _lock(stand_ins)
    refused: subprocess.CompletedProcess[str] = _run(
        [str(_FETCH), "--dry-run", "--lock", str(lock), "--out", str(tmp_path / "tools")]
    )
    assert refused.returncode != 0
    assert "is a file address, and the release takes https addresses only" in refused.stderr, refused.stderr
    addresses: list[str] = [
        line.split()[4]
        for line in (_DESKTOP / "tools.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert addresses and all(address.startswith("https://") for address in addresses), addresses


def test_the_release_script_asks_that_check_of_the_app_it_built() -> None:
    release: str = _RELEASE.read_text(encoding="utf-8")
    assert '"$desktop/fetch-tools.sh" --check-bundle "$app/Contents/Resources/tools"' in release
    assert release.index("--check-bundle") < release.index("for tool in uv rg dot-launcher; do")


@pytest.mark.internet
def test_the_pinned_archives_are_still_the_pinned_ones(tmp_path: Path) -> None:
    """The real lock file against the real releases of uv and ripgrep: a moved or removed asset, or an archive
    that was replaced, is found here and not on the day of a release."""
    done: subprocess.CompletedProcess[str] = _run([str(_RELEASE), "--check-tools"])
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("SHA-256 as pinned") == 4

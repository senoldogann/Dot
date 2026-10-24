"""The folder of Dot's own that Messages sends a picture from (item 617): a copy of the picture in a folder made for it,
named for the file and its type, kept a while after the send and then removed, and only ever a folder this module made.
Every folder is inside the test's own; the owner's ``~/Pictures`` is never touched."""

import re
import stat
from pathlib import Path

import pytest
from picture_files import HEADS, NOT_IMAGE, Layout, layout
from structlog.testing import capture_logs

from dotagent.channels.pictures import Picture
from dotagent.channels.staging import MARKER, STAGED_KEPT_S, Staged, Staging, staged_name
from dotagent.core.errors import ChannelError
from dotagent.core.paths import staging_dir

NOW: float = 1_900_000_000.0
FOLDER: re.Pattern[str] = re.compile(r"^1900000000-[0-9a-f]{8}$")


@pytest.fixture
def home(tmp_path: Path) -> Layout:
    return layout(tmp_path)


@pytest.fixture
def staging(home: Layout) -> Staging:
    return Staging(home.staging, home.finder)


def picture(name: str = "chart.png", kind: str = "png", size: int = 10) -> Picture:
    return Picture(Path("/nowhere") / name, name, kind, size)  # type: ignore[arg-type]


def pictured(home: Layout, name: str = "chart.png", kind: str = "png") -> Picture:
    home.make(home.workspace / name, kind)
    (found,) = home.finder.find(name).pictures
    return found


def test_the_staging_folder_is_a_folder_of_dots_own_under_the_owners_pictures_unless_the_environment_moves_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DOT_STAGING_DIR", raising=False)
    assert staging_dir() == Path.home() / "Pictures" / "Dot"
    monkeypatch.setenv("DOT_STAGING_DIR", "")
    assert staging_dir() == Path.home() / "Pictures" / "Dot"
    monkeypatch.setenv("DOT_STAGING_DIR", str(tmp_path / "elsewhere"))
    assert staging_dir() == tmp_path / "elsewhere"


def test_a_test_run_stages_in_a_pytest_folder_and_a_staging_folder_anywhere_else_is_refused(
    home: Layout, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard of ``tests/conftest.py`` (item 617): the folder pictures are staged in is the owner's own, and a run
    that reached it would list it and remove the old copies in it. The default of a run is a pytest folder, and a
    ``Staging`` built anywhere else fails before it touches anything. The owner's home here is an invented one, so that
    this test, too, never looks at the real ``~/Pictures``."""
    assert staging_dir().is_relative_to(tmp_path_factory.getbasetemp())
    monkeypatch.setenv("HOME", "/Users/someone")
    owners: tuple[Path, ...] = (
        Path.home() / "Pictures" / "Dot",
        Path.home() / "Pictures",
        Path.home(),
        Path("/tmp/not-a-pytest-folder/Dot"),
        Path("Pictures/Dot"),
    )
    assert owners[0] == Path("/Users/someone/Pictures/Dot")
    for root in owners:
        with pytest.raises(AssertionError, match="pytest folder"):
            Staging(root, home.finder)


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("ekran-2026-10-24-1617.png", "png", "ekran-2026-10-24-1617.png"),
        ("lying.png", "jpeg", "lying.jpg"),
        ("capture", "png", "capture.png"),
        ("photo.HEIC", "heic", "photo.heic"),
        ("anim.gif", "gif", "anim.gif"),
        ("a b (1).webp", "webp", "a b _1_.webp"),
        ("../../etc/passwd.png", "png", "passwd.png"),
        (".hidden.png", "png", "hidden.png"),
        ("...png", "png", "picture.png"),
        ("güzel görüntü.png", "png", "güzel görüntü.png"),
        ("x" * 200 + ".png", "png", "x" * 60 + ".png"),
    ],
)
def test_a_copy_wears_the_files_name_made_safe_and_the_extension_its_type_gives_it(
    name: str, kind: str, expected: str
) -> None:
    assert staged_name(picture(name, kind)) == expected


def test_a_copy_is_made_in_a_new_private_folder_of_its_own(home: Layout, staging: Staging) -> None:
    found: Picture = pictured(home, kind="jpeg")
    first: Staged = staging.stage(found, NOW)
    second: Staged = staging.stage(found, NOW)
    assert first.folder != second.folder
    for staged in (first, second):
        assert staged.folder.parent == home.staging and FOLDER.match(staged.folder.name)
        assert staged.path == staged.folder / "chart.jpg"
        assert staged.path.read_bytes() == HEADS["jpeg"]
        assert stat.S_IMODE(staged.folder.stat().st_mode) == 0o700
        assert stat.S_IMODE(staged.path.stat().st_mode) == 0o600
        # The mark that says the folder is Dot's, and the only two files in it.
        assert stat.S_IMODE((staged.folder / MARKER).stat().st_mode) == 0o600
        assert (staged.folder / MARKER).read_text(encoding="utf-8").startswith("Made by Dot")
        assert sorted(entry.name for entry in staged.folder.iterdir()) == sorted([MARKER, "chart.jpg"])
    # The picture itself is where it was.
    assert (home.workspace / "chart.png").read_bytes() == HEADS["jpeg"]


def test_a_picture_that_is_no_longer_one_leaves_no_folder_behind(home: Layout, staging: Staging) -> None:
    found: Picture = pictured(home)
    (home.workspace / "chart.png").write_bytes(NOT_IMAGE)
    with pytest.raises(ChannelError, match="cannot be sent now"):
        staging.stage(found, NOW)
    assert list(home.staging.iterdir()) == []


def test_a_staging_folder_that_cannot_be_made_is_told(home: Layout, tmp_path: Path) -> None:
    (tmp_path / "blocked").write_text("a file where the folder should be")
    broken: Staging = Staging(tmp_path / "blocked" / "Dot", home.finder)
    with pytest.raises(ChannelError, match="could not make its folder for pictures"):
        broken.stage(pictured(home), NOW)


def test_a_copy_that_was_sent_stays_for_a_while_and_is_removed_when_its_time_is_over(
    home: Layout, staging: Staging
) -> None:
    staged: Staged = staging.stage(pictured(home), NOW)
    staging.sent(staged, NOW)
    assert not staging.due(NOW) and not staging.due(NOW + STAGED_KEPT_S - 1)
    staging.sweep(NOW + STAGED_KEPT_S - 1)
    assert staged.path.exists()
    assert staging.due(NOW + STAGED_KEPT_S)
    staging.sweep(NOW + STAGED_KEPT_S)
    assert not staged.folder.exists() and home.staging.exists() and not staging.due(NOW + STAGED_KEPT_S)


def test_each_copy_goes_at_its_own_time(home: Layout, staging: Staging) -> None:
    early: Staged = staging.stage(pictured(home), NOW)
    late: Staged = staging.stage(pictured(home), NOW + 100)
    staging.sent(early, NOW)
    staging.sent(late, NOW + 100)
    staging.sweep(NOW + STAGED_KEPT_S + 50)
    assert not early.folder.exists() and late.folder.exists()
    staging.sweep(NOW + STAGED_KEPT_S + 100)
    assert not late.folder.exists()


def test_a_copy_of_a_send_that_failed_is_removed_at_once(home: Layout, staging: Staging) -> None:
    staged: Staged = staging.stage(pictured(home), NOW)
    staging.drop(staged)
    assert not staged.folder.exists() and list(home.staging.iterdir()) == []


def test_the_copies_an_earlier_daemon_left_are_removed_as_the_next_starts_and_nothing_else_is(
    home: Layout, staging: Staging
) -> None:
    old: Staged = staging.stage(pictured(home), NOW - STAGED_KEPT_S)
    recent: Staged = staging.stage(pictured(home), NOW - STAGED_KEPT_S + 60)
    own_folder: Path = home.staging / "holiday photos"
    own_folder.mkdir()
    (own_folder / "IMG_1.png").write_bytes(HEADS["png"])
    own_file: Path = home.staging / "1800000000-abcdef12"
    own_file.write_bytes(b"a file named like one of ours is not a folder of ours")
    elsewhere: Path = home.home / "Pictures" / "1800000000-deadbeef"
    elsewhere.mkdir()
    staging.sweep_old(NOW)
    assert not old.folder.exists() and recent.folder.exists()
    assert (own_folder / "IMG_1.png").exists() and elsewhere.exists()
    # A file with the name of ours is no folder of ours: it stays.
    assert own_file.is_file()


def test_a_link_named_like_a_folder_of_ours_is_not_followed_or_removed(home: Layout, staging: Staging) -> None:
    precious: Path = home.home / "Documents" / "precious"
    precious.mkdir(parents=True)
    (precious / "keep.txt").write_text("keep")
    home.staging.mkdir(parents=True)
    trap: Path = home.staging / "1800000000-cafebabe"
    trap.symlink_to(precious, target_is_directory=True)
    staging.sweep_old(NOW)
    assert (precious / "keep.txt").read_text() == "keep" and trap.is_symlink()


def test_a_folder_named_like_ours_that_holds_no_mark_of_dots_is_never_removed(home: Layout, staging: Staging) -> None:
    """The staging folder is the owner's: a folder in it that Dot did not make stays, whatever its name says."""
    home.staging.mkdir(parents=True)
    theirs: Path = home.staging / "1800000000-abcdef12"
    theirs.mkdir()
    (theirs / "IMG_1.png").write_bytes(HEADS["png"])
    with capture_logs() as logs:
        staging.sweep_old(NOW)
    assert (theirs / "IMG_1.png").exists()
    # It is told as something left alone, not as a refusal: nothing asked for it to be removed.
    assert [(entry["event"], entry["log_level"]) for entry in logs] == [("staged_picture_folder_left_alone", "info")]


@pytest.mark.parametrize("mark", ["gone", "other words", "a link"])
def test_a_folder_that_lost_the_mark_of_dots_is_not_removed_by_a_sweep_or_a_drop(
    home: Layout, staging: Staging, tmp_path: Path, mark: str
) -> None:
    staged: Staged = staging.stage(pictured(home), NOW - STAGED_KEPT_S)
    (staged.folder / MARKER).unlink()
    if mark == "other words":
        (staged.folder / MARKER).write_text("some other program's file\n", encoding="utf-8")
    if mark == "a link":
        real: Path = tmp_path / "real-mark"
        real.write_text("Made by Dot: but not in this folder\n", encoding="utf-8")
        (staged.folder / MARKER).symlink_to(real)
    staging.sweep_old(NOW)
    staging.sent(staged, NOW - STAGED_KEPT_S)
    staging.sweep(NOW)
    staging.drop(staged)
    assert staged.path.exists()


def test_a_folder_of_dots_that_holds_a_folder_is_left_whole(home: Layout, staging: Staging) -> None:
    """Only the files of a folder of Dot's go with it, never a tree: a folder in it is not Dot's."""
    staged: Staged = staging.stage(pictured(home), NOW - STAGED_KEPT_S)
    inner: Path = staged.folder / "inner"
    inner.mkdir()
    (inner / "keep.txt").write_text("keep", encoding="utf-8")
    staging.sweep_old(NOW)
    staging.drop(staged)
    assert (inner / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert staged.path.exists() and (staged.folder / MARKER).exists()


def test_a_link_in_a_folder_of_dots_goes_as_a_link_and_what_it_leads_to_stays(
    home: Layout, staging: Staging, tmp_path: Path
) -> None:
    staged: Staged = staging.stage(pictured(home), NOW)
    precious: Path = tmp_path / "precious.txt"
    precious.write_text("keep", encoding="utf-8")
    (staged.folder / "shortcut").symlink_to(precious)
    tree: Path = tmp_path / "tree"
    tree.mkdir()
    (tree / "keep.txt").write_text("keep", encoding="utf-8")
    (staged.folder / "folder-shortcut").symlink_to(tree, target_is_directory=True)
    staging.drop(staged)
    assert not staged.folder.exists()
    assert precious.read_text(encoding="utf-8") == "keep" and (tree / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_a_folder_whose_mark_cannot_be_written_is_taken_back(
    home: Layout, staging: Staging, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(folder: Path) -> None:
        raise OSError("no room")

    monkeypatch.setattr("dotagent.channels.staging._mark", refuse)
    with pytest.raises(ChannelError, match="could not make its folder for pictures"):
        staging.stage(pictured(home), NOW)
    assert list(home.staging.iterdir()) == []


def test_sweeping_a_staging_folder_that_does_not_exist_is_nothing(tmp_path: Path, home: Layout) -> None:
    Staging(tmp_path / "none" / "there", home.finder).sweep_old(NOW)
    Staging(tmp_path / "none" / "there", home.finder).sweep(NOW)

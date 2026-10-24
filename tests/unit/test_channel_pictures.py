"""The pictures an answer names (item 617): which files an answer names as pictures, which of them may leave the Mac
with it, and the text without the paths of those that go. Every file is made by the test in a throw-away home laid out
as the owner's Mac is: the workspace deep in ``~/Library/Application Support/Dot``, spaces in the path and all."""

import os
import stat
from pathlib import Path

import pytest
from picture_files import HEADS, NOT_IMAGE, Layout, layout

from dotagent.channels import pictures as pictures_module
from dotagent.channels.pictures import (
    MAX_BYTES,
    MAX_PICTURES,
    NOT_A_PICTURE,
    OUT_OF_PLACE,
    TOO_BIG,
    TOO_MANY,
    UNREADABLE,
    Found,
    Left,
    Picture,
    Pictures,
    sniff,
)
from dotagent.channels.text import as_plain_text
from dotagent.core.errors import ChannelError


@pytest.fixture
def world(tmp_path: Path) -> Layout:
    return layout(tmp_path)


def names(found: Found) -> list[str]:
    return [picture.name for picture in found.pictures]


def test_the_kind_of_a_picture_is_its_first_bytes() -> None:
    for kind, head in HEADS.items():
        assert sniff(head) == kind
    assert sniff(NOT_IMAGE) is None
    assert sniff(b"") is None
    assert sniff(b"GIF8") is None
    # An AVIF, another file of the HEIF family, is not one of the kinds that go.
    assert sniff(b"\x00\x00\x00\x18ftypavif" + bytes(20)) is None


def test_the_owners_answer_names_a_screenshot_in_the_workspace_and_it_goes(world: Layout) -> None:
    shot: Path = world.make(world.workspace / "screenshots" / "ekran-2026-10-24-1617.png")
    answer: str = (
        "I sent the screenshot above, and saved it as a file at "
        f"{world.workspace}/screenshots/ekran-2026-10-24-1617.png"
    )
    found: Found = world.finder.find(answer)
    assert names(found) == ["ekran-2026-10-24-1617.png"]
    assert found.pictures[0].kind == "png" and found.pictures[0].size == len(HEADS["png"])
    assert found.pictures[0].path.samefile(shot)
    # The path goes from the text, since the picture is there; the file's name stays so that the sentence still reads.
    assert found.text == "I sent the screenshot above, and saved it as a file at ekran-2026-10-24-1617.png"
    assert "Application Support" not in found.text and found.note() == "" and found.left_out == ()


def test_a_markdown_image_goes_whole_with_the_line_it_is_alone_on(world: Layout) -> None:
    world.make(world.workspace / "chart.png")
    found: Found = world.finder.find(f"Here is the chart:\n\n![The chart]({world.workspace}/chart.png)\n\nThat is all.")
    assert names(found) == ["chart.png"]
    assert found.text == "Here is the chart:\n\n\nThat is all."


def test_a_markdown_image_in_a_sentence_leaves_one_space_where_it_stood(world: Layout) -> None:
    world.make(world.workspace / "chart.png")
    found: Found = world.finder.find("See ![the chart](chart.png) for the trend, or (![](chart.png))")
    assert names(found) == ["chart.png"]
    assert found.text == "See for the trend, or ()"


def test_an_image_that_is_all_a_list_item_holds_leaves_the_item_empty_and_the_plain_text_drops_it(
    world: Layout,
) -> None:
    world.make(world.workspace / "chart.png")
    left: str = world.finder.find("- ![chart](chart.png)\n- text").text
    assert left == "-\n- text"
    assert as_plain_text(left) == "• text"


@pytest.mark.parametrize(
    "spelling",
    [
        "{workspace}/shots/a b.png",
        "`{workspace}/shots/a b.png`",
        '"{workspace}/shots/a b.png"',
        "(see {workspace}/shots/a b.png)",
        "file://{workspace_encoded}/shots/a%20b.png",
        "~/Library/Application Support/Dot/workspace/shots/a b.png",
        "shots/a b.png in the workspace",
    ],
)
def test_the_ways_a_path_to_a_picture_may_be_written(world: Layout, spelling: str) -> None:
    made: Path = world.make(world.workspace / "shots" / "a b.png")
    encoded: str = str(world.workspace).replace(" ", "%20")
    text: str = f"Saved: {spelling.format(workspace=world.workspace, workspace_encoded=encoded)} ok."
    found: Found = world.finder.find(text)
    if spelling.startswith("shots/"):
        # A name with spaces and no folder in front is not a path the words show where it ends; one without spaces is.
        assert found.pictures == ()
        return
    assert [picture.path.samefile(made) for picture in found.pictures] == [True]
    assert str(world.workspace) not in found.text and "a b.png" in found.text


def test_a_name_with_no_folder_is_a_file_of_the_workspace(world: Layout) -> None:
    made: Path = world.make(world.workspace / "chart.png")
    found: Found = world.finder.find("I made chart.png and another.png for you.")
    assert [picture.path.samefile(made) for picture in found.pictures] == [True]
    assert found.text == "I made chart.png and another.png for you." and found.left_out == ()


def test_a_path_in_the_owners_home_goes(world: Layout) -> None:
    made: Path = world.make(world.home / "Pictures" / "holiday.jpg", "jpeg")
    found: Found = world.finder.find(f"The one at ~/Pictures/holiday.jpg and {world.home}/Pictures/holiday.jpg")
    assert [picture.path.samefile(made) for picture in found.pictures] == [True]
    assert found.pictures[0].kind == "jpeg"
    assert found.text == "The one at holiday.jpg and holiday.jpg"


@pytest.mark.parametrize("kind", sorted(HEADS))
def test_every_kind_goes_and_the_first_bytes_not_the_name_tell_which(world: Layout, kind: str) -> None:
    world.make(world.workspace / "lying.png", kind)
    found: Found = world.finder.find("![x](lying.png)")
    assert [picture.kind for picture in found.pictures] == [kind]


def test_an_image_whose_name_has_no_extension_goes_when_its_bytes_say_it_is_a_picture(world: Layout) -> None:
    world.make(world.workspace / "capture", "png")
    assert names(world.finder.find("![x](capture)")) == ["capture"]


def test_a_file_that_is_no_picture_is_left_out_whatever_its_name_says(world: Layout) -> None:
    world.make(world.workspace / "notes.png", data=NOT_IMAGE)
    world.make(world.workspace / "empty.jpg", data=b"")
    found: Found = world.finder.find("Look at notes.png and empty.jpg")
    assert found.pictures == ()
    assert found.left_out == (Left("notes.png", NOT_A_PICTURE), Left("empty.jpg", NOT_A_PICTURE))
    assert found.text == "Look at notes.png and empty.jpg"
    assert found.note() == f"Left out: notes.png ({NOT_A_PICTURE}), empty.jpg ({NOT_A_PICTURE})."


def test_what_is_no_regular_file_names_nothing(world: Layout) -> None:
    (world.workspace / "folder.png").mkdir()
    os.mkfifo(world.workspace / "pipe.png")
    found: Found = world.finder.find("folder.png pipe.png missing.png ![x](folder.png) ![y](nothing/here.png)")
    assert found.pictures == () and found.left_out == ()
    assert found.text == "folder.png pipe.png missing.png ![x](folder.png) ![y](nothing/here.png)"


@pytest.mark.parametrize(
    "where",
    [
        ".ssh/id.png",
        ".config/app/pic.png",
        "Documents/.private/pic.png",
        ".hidden.png",
        "Library/Messages/Attachments/ab/photo.png",
        "Library/Application Support/Other/pic.png",
        "Library/Mobile Documents/pic.png",
    ],
)
def test_a_picture_in_a_hidden_folder_or_in_the_library_outside_dots_own_folder_is_left_out(
    world: Layout, where: str
) -> None:
    world.make(world.home / where)
    found: Found = world.finder.find(f"Here: ~/{where} and {world.home}/{where}")
    assert found.pictures == ()
    assert [left.why for left in found.left_out] == [OUT_OF_PLACE]
    assert found.text == f"Here: ~/{where} and {world.home}/{where}"


def test_a_picture_outside_the_workspace_and_the_home_folder_is_left_out(world: Layout) -> None:
    outside: Path = world.make(world.root / "elsewhere" / "pic.png")
    found: Found = world.finder.find(f"At {outside}")
    assert found.pictures == () and [left.why for left in found.left_out] == [OUT_OF_PLACE]


def test_a_hidden_folder_inside_the_workspace_is_left_out_but_a_hidden_home_of_dot_itself_is_not(
    world: Layout, tmp_path: Path
) -> None:
    world.make(world.workspace / ".cache" / "pic.png")
    assert [left.why for left in world.finder.find("![x](.cache/pic.png)").left_out] == [OUT_OF_PLACE]
    # Dot's home may itself be a hidden folder (DOT_HOME): what is judged is what lies below the workspace.
    hidden_home: Path = tmp_path / ".dot-home" / "workspace"
    hidden_home.mkdir(parents=True)
    kept: Pictures = Pictures(hidden_home, world.home)
    (hidden_home / "pic.png").write_bytes(HEADS["png"])
    assert names(kept.find("![x](pic.png)")) == ["pic.png"]


def test_a_place_the_policy_keeps_from_a_run_is_left_out(world: Layout) -> None:
    world.make(world.home / "Documents" / "Secret" / "plan.png")
    world.make(world.home / "Documents" / "plan.png")
    guarded: Pictures = Pictures(world.workspace, world.home, [world.home / "Documents" / "Secret"])
    found: Found = guarded.find("~/Documents/Secret/plan.png and ~/Documents/plan.png")
    assert names(found) == ["plan.png"] and [left.why for left in found.left_out] == [OUT_OF_PLACE]


def test_a_link_is_judged_by_the_place_it_leads_to(world: Layout) -> None:
    secret: Path = world.make(world.home / ".ssh" / "key.png")
    inside: Path = world.make(world.home / "Pictures" / "ok.png")
    (world.workspace / "link-to-secret.png").symlink_to(secret)
    (world.workspace / "link-to-ok.png").symlink_to(inside)
    (world.workspace / "folder-link").symlink_to(world.home / ".ssh")
    found: Found = world.finder.find("link-to-secret.png link-to-ok.png ![x](folder-link/key.png)")
    assert [picture.path.samefile(inside) for picture in found.pictures] == [True]
    assert [left.why for left in found.left_out] == [OUT_OF_PLACE]
    # One line for the file, under the first name the answer wrote for it (the images of a line are looked at first).
    assert [left.name for left in found.left_out] == ["key.png"]


def test_a_file_is_called_by_the_name_the_answer_wrote_and_never_by_the_name_a_link_leads_to(world: Layout) -> None:
    """The line of what was left out goes to the owner's phone: a link in the workspace to a file in ``~/.ssh`` must not
    tell the name of that file, nor must the text, the picture's name or the line of one that could not be sent (the
    review of item 617)."""
    targets: dict[str, Path] = {
        "a.png": world.make(world.home / ".ssh" / "token-for-the-bank.png"),
        "b.png": world.make(world.home / "Documents" / "payslip-march.png", data=NOT_IMAGE),
        "c.png": world.make(world.home / "Documents" / "passport-scan.png"),
        "d.png": world.make(world.home / "Pictures" / "holiday-with-a-name.jpg", "jpeg"),
    }
    os.truncate(targets["c.png"], MAX_BYTES + 1)
    for link, target in targets.items():
        (world.workspace / link).symlink_to(target)
    found: Found = world.finder.find("a.png b.png c.png d.png")
    assert [(left.name, left.why) for left in found.left_out] == [
        ("a.png", OUT_OF_PLACE),
        ("b.png", NOT_A_PICTURE),
        ("c.png", TOO_BIG),
    ]
    assert names(found) == ["d.png"] and found.pictures[0].path.samefile(targets["d.png"])
    told: str = found.text + found.note()
    assert not any(real in told for real in ("token-for-the-bank", "payslip", "passport", "holiday-with-a-name"))
    assert found.note() == f"Left out: a.png ({OUT_OF_PLACE}), b.png ({NOT_A_PICTURE}), c.png ({TOO_BIG})."


def test_a_picture_over_the_size_limit_is_left_out(world: Layout) -> None:
    big: Path = world.make(world.workspace / "big.png")
    os.truncate(big, MAX_BYTES + 1)
    fits: Path = world.make(world.workspace / "fits.png")
    os.truncate(fits, MAX_BYTES)
    found: Found = world.finder.find("big.png fits.png")
    assert names(found) == ["fits.png"] and found.pictures[0].size == MAX_BYTES
    assert found.left_out == (Left("big.png", TOO_BIG),) and found.note() == "Left out: big.png (over 20 MB)."


def test_at_most_four_go_and_the_rest_are_counted_in_the_one_line(world: Layout) -> None:
    for number in range(7):
        world.make(world.workspace / f"p{number}.png")
    found: Found = world.finder.find("p0.png p1.png p2.png p3.png p4.png p5.png p6.png")
    assert names(found) == ["p0.png", "p1.png", "p2.png", "p3.png"] and len(found.pictures) == MAX_PICTURES
    assert [left.why for left in found.left_out] == [TOO_MANY] * 3
    assert found.note() == f"Left out: 3 more ({TOO_MANY})."
    # What was not sent keeps its words in the text; what was sent is shortened to its name, as it was.
    assert found.text == "p0.png p1.png p2.png p3.png p4.png p5.png p6.png"


def test_one_line_names_the_first_few_reasons_and_counts_the_rest(world: Layout) -> None:
    for number in range(6):
        world.make(world.workspace / f"bad{number}.png", data=NOT_IMAGE)
    found: Found = world.finder.find(" ".join(f"bad{number}.png" for number in range(6)))
    assert found.note() == (
        f"Left out: bad0.png ({NOT_A_PICTURE}), bad1.png ({NOT_A_PICTURE}), bad2.png ({NOT_A_PICTURE}), 3 more."
    )
    assert Found("x", (), ()).note() == ""
    long: Found = Found("x", (), (Left("n" * 80 + ".png", TOO_BIG),))
    assert long.note() == f"Left out: {'n' * 39}… ({TOO_BIG})."


def test_a_picture_named_twice_goes_once_and_both_places_are_shortened(world: Layout) -> None:
    world.make(world.workspace / "chart.png")
    found: Found = world.finder.find(f"![chart](chart.png)\nSaved to {world.workspace}/chart.png and chart.png")
    assert names(found) == ["chart.png"]
    # The image was all its line held, and took the line with it; the path and the name are the file's name.
    assert found.text == "Saved to chart.png and chart.png"


def test_a_block_of_code_names_no_picture_but_a_code_span_does(world: Layout) -> None:
    world.make(world.workspace / "chart.png")
    in_block: Found = world.finder.find("Run:\n```\ncp chart.png /tmp/x\n![x](chart.png)\n```\nDone.")
    assert in_block.pictures == () and in_block.text == "Run:\n```\ncp chart.png /tmp/x\n![x](chart.png)\n```\nDone."
    in_span: Found = world.finder.find("Saved as `chart.png`.")
    assert names(in_span) == ["chart.png"] and in_span.text == "Saved as `chart.png`."


def test_a_file_that_cannot_be_read_as_it_is_judged_is_left_out_and_leaves_no_descriptor_open(
    world: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.make(world.workspace / "chart.png")

    def broken(self: Pictures, descriptor: int, name: str) -> object:
        raise OSError("input/output error")

    monkeypatch.setattr(Pictures, "_judged", broken)
    before: int = len(os.listdir("/dev/fd"))
    found: Found = world.finder.find("chart.png")
    assert found.pictures == () and found.left_out == (Left("chart.png", UNREADABLE),)
    assert len(os.listdir("/dev/fd")) == before


def test_what_a_name_holds_that_is_not_printable_makes_no_line_of_its_own_in_the_text_or_the_note(
    world: Layout,
) -> None:
    tabbed: Path = world.make(world.workspace / "odd\tname.png")
    escaped: Path = world.make(world.workspace / "bad\x1b[31mname.png", data=NOT_IMAGE)
    found: Found = world.finder.find(f"see {tabbed} and {escaped}")
    assert names(found) == ["odd name.png"] and found.text == "see odd name.png and " + str(escaped)
    assert found.note() == f"Left out: bad [31mname.png ({NOT_A_PICTURE})."
    assert "\x1b" not in found.note() and "\t" not in found.text


def test_a_web_address_is_no_file_of_the_mac(world: Layout) -> None:
    world.make(world.workspace / "chart.png")
    found: Found = world.finder.find("![x](https://example.com/chart.png) and https://example.com/chart.png")
    assert found.pictures == () and found.left_out == ()


def test_a_name_that_goes_on_after_the_extension_is_not_that_file(world: Layout) -> None:
    world.make(world.workspace / "chart.png")
    assert world.finder.find("chart.png.bak chart.pngx chart.png-old").pictures == ()
    assert names(world.finder.find("chart.png, chart.png. (chart.png)")) == ["chart.png"]


def test_an_answer_that_names_a_great_many_places_makes_dot_look_at_few(
    world: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    real = Pictures._open

    def counting(self: Pictures, path: Path) -> object:
        opened.append(path)
        return real(self, path)

    monkeypatch.setattr(Pictures, "_open", counting)
    answer: str = "\n".join(f"/nowhere/{number}/pic.png and x{number}.png" for number in range(500))
    assert world.finder.find(answer).pictures == ()
    assert 0 < len(opened) <= pictures_module._MAX_LOOKS


def test_a_very_long_line_is_no_sentence_that_names_a_picture(world: Layout) -> None:
    world.make(world.workspace / "chart.png")
    assert world.finder.find("chart.png " + "x" * 5000).pictures == ()


def test_copy_to_copies_the_file_into_a_new_private_file(world: Layout, tmp_path: Path) -> None:
    source: Path = world.make(world.workspace / "chart.png", data=HEADS["png"] + b"the rest of it")
    (picture,) = world.finder.find("chart.png").pictures
    target: Path = tmp_path / "copy.png"
    world.finder.copy_to(picture, target)
    assert target.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(ChannelError, match="could not be made"):
        world.finder.copy_to(picture, target)


def test_copy_to_judges_the_file_as_it_is_when_it_is_copied(world: Layout, tmp_path: Path) -> None:
    made: Path = world.make(world.workspace / "chart.png")
    (picture,) = world.finder.find("chart.png").pictures
    # The file was changed into another kind of picture, then into words, then taken away, since it was looked at.
    made.write_bytes(HEADS["jpeg"])
    with pytest.raises(ChannelError, match="changed after"):
        world.finder.copy_to(picture, tmp_path / "a.png")
    made.write_bytes(NOT_IMAGE)
    with pytest.raises(ChannelError, match=NOT_A_PICTURE):
        world.finder.copy_to(picture, tmp_path / "b.png")
    made.unlink()
    with pytest.raises(ChannelError, match="it is gone"):
        world.finder.copy_to(picture, tmp_path / "c.png")
    assert not (tmp_path / "a.png").exists() and not (tmp_path / "b.png").exists()


def test_copy_to_will_not_follow_a_name_that_became_a_link_into_a_hidden_folder(world: Layout, tmp_path: Path) -> None:
    made: Path = world.make(world.workspace / "chart.png")
    (picture,) = world.finder.find("chart.png").pictures
    secret: Path = world.make(world.home / ".ssh" / "stolen.png")
    made.unlink()
    made.symlink_to(secret)
    with pytest.raises(ChannelError, match=OUT_OF_PLACE):
        world.finder.copy_to(picture, tmp_path / "stolen.png")
    assert not (tmp_path / "stolen.png").exists()


def test_the_copy_stops_at_the_limit_a_file_grew_past_after_it_was_looked_at(
    world: Layout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: Path = world.make(world.workspace / "chart.png", data=HEADS["png"] + bytes(2000))
    (picture,) = world.finder.find("chart.png").pictures
    monkeypatch.setattr(pictures_module, "MAX_BYTES", 1000)
    monkeypatch.setattr(pictures_module, "_COPY_CHUNK", 512)
    descriptor: int = os.open(made, os.O_RDONLY)
    try:
        with pytest.raises(ChannelError, match="grew past"):
            pictures_module._copy(descriptor, tmp_path / "grown.png", picture)
    finally:
        os.close(descriptor)


def test_a_picture_is_what_it_was_when_it_was_looked_at(world: Layout) -> None:
    made: Path = world.make(world.workspace / "chart.png")
    (picture,) = world.finder.find("chart.png").pictures
    assert picture == Picture(picture.path, "chart.png", "png", made.stat().st_size)

"""The themes of one's own (item 128): the daemon keeps a theme as a theme file in its home's ``themes`` folder, checks
every file on the way in and on the way out as the page reads it, writes a file whole or not at all, and caps the
folder; a file the page could not draw is left out of the list with the reason."""

import json
import os
import re
from pathlib import Path

import pytest

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.themes import (
    MAX_THEME_FILE_BYTES,
    MAX_THEME_FILES,
    THEME_ROLES,
    ThemeFile,
    delete_theme,
    parse_theme_file,
    read_theme_folder,
    save_theme,
)

_GENERATOR: Path = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "dotagent"
    / "server"
    / "static"
    / "js"
    / "appearance"
    / "generator.js"
)
# A theme file in full: canonical OKLCH colours, the other scheme under variants.
_EXPORTED: dict[str, object] = {
    "version": 1,
    "id": "harbour",
    "name": "Harbour",
    "appearance": "light",
    "colors": {"canvas": "oklch(0.98 0.01 230)", "accent": "oklch(0.55 0.15 230 / 0.9)", "text": "#1d2733"},
    "variants": {"dark": {"canvas": "rgb(16 22 30)", "accent": "#4aa3df"}},
    "managed": True,
}
# The short form of a theme file: a name, a scheme and the two colours the rest is derived from.
_SHORT: dict[str, object] = {"name": "Moss", "appearance": "dark", "canvas": "#14201a", "accent": "#7fbf6a"}


def _bytes(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def _with(change: dict[str, object]) -> bytes:
    return _bytes({key: value for key, value in {**_EXPORTED, **change}.items() if value is not None})


def test_a_theme_file_and_its_short_form_are_read() -> None:
    exported: ThemeFile = parse_theme_file(_bytes(_EXPORTED))
    assert exported.colors is not None and exported.colors["accent"] == "oklch(0.55 0.15 230 / 0.9)"
    assert exported.variants is not None and exported.variants.dark is not None
    short: ThemeFile = parse_theme_file(_bytes(_SHORT))
    assert (short.canvas, short.accent, short.colors) == ("#14201a", "#7fbf6a", None)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"version": 2}, "version"),
        ({"name": "   "}, "name"),
        ({"name": "x" * 49}, "48 characters"),
        ({"appearance": "dim"}, "appearance"),
        ({"id": "Harbour"}, "id"),
        ({"colors": {"canvas": "#fff", "glow": "#000"}}, "not a colour role of a theme: glow"),
        ({"colors": {"canvas": "red"}}, "not a colour the page reads"),
        ({"colors": {"canvas": "rgb(1 2)"}}, "not a colour the page reads"),
        ({"colors": {"canvas": "oklch(1e999 0 0)"}}, "not a colour the page reads"),
        ({"colors": {"canvas": "#" + "f" * 70}}, "colors.canvas"),
        ({"colors": None, "canvas": "#101010"}, "both, the canvas and the accent"),
        ({"colors": {}}, "names its colours"),
        ({"variants": {"light": {"canvas": "#ffffff"}}}, "repeat the theme's own appearance"),
        ({"colours": {"canvas": "#ffffff"}}, "colours"),
    ],
)
def test_a_file_the_page_could_not_draw_is_refused_with_what_is_wrong(change: dict[str, object], reason: str) -> None:
    with pytest.raises(InvalidValueError) as refused:
        parse_theme_file(_with(change))
    assert reason in refused.value.message


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"managed": None}, "left out, not null: managed"),
        ({"variants": {"dark": None}}, "variants: a key without a value is left out, not null: dark"),
        ({"managed": "yes"}, "managed: Input should be a valid boolean"),
    ],
)
def test_the_daemon_refuses_a_null_or_a_loose_flag_as_the_page_does(change: dict[str, object], reason: str) -> None:
    with pytest.raises(InvalidValueError) as refused:
        parse_theme_file(_bytes({**_EXPORTED, **change}))
    assert reason in refused.value.message


def test_a_theme_is_saved_as_a_theme_file_whole_and_under_its_own_id(tmp_path: Path) -> None:
    folder: Path = tmp_path / "themes"
    theme: ThemeFile = parse_theme_file(_with({"id": None}))
    stored = save_theme(folder, "harbour", theme)
    text: str = (folder / "harbour.json").read_text(encoding="utf-8")
    assert stored.id == "harbour" and stored.file.id == "harbour"
    assert text.startswith('{\n  "version": 1,\n  "id": "harbour",\n  "name": "Harbour",') and text.endswith("}\n")
    assert sorted(path.name for path in folder.iterdir()) == ["harbour.json"]
    save_theme(folder, "harbour", theme.model_copy(update={"name": "Harbour at dusk"}))
    assert json.loads((folder / "harbour.json").read_text(encoding="utf-8"))["name"] == "Harbour at dusk"
    with pytest.raises(InvalidValueError, match="not the one it is saved under"):
        save_theme(folder, "other", parse_theme_file(_bytes(_EXPORTED)))
    for taken in ("neutral", "rose", "clean", "dark"):
        with pytest.raises(InvalidValueError, match="built-in"):
            save_theme(folder, taken, theme)
    with pytest.raises(InvalidValueError, match="lowercase letters"):
        save_theme(folder, "../harbour", theme)
    delete_theme(folder, "harbour")
    assert list(folder.iterdir()) == []
    with pytest.raises(NotFoundError):
        delete_theme(folder, "harbour")


def test_the_folder_holds_so_many_themes_of_so_many_bytes(tmp_path: Path) -> None:
    theme: ThemeFile = parse_theme_file(_bytes(_SHORT))
    for index in range(MAX_THEME_FILES):
        save_theme(tmp_path, f"moss-{index}", theme)
    with pytest.raises(InvalidValueError, match=f"{MAX_THEME_FILES} themes at most"):
        save_theme(tmp_path, "one-more", theme)
    save_theme(tmp_path, "moss-0", theme)
    with pytest.raises(InvalidValueError, match="a theme's file is 32 KB at most"):
        save_theme(tmp_path, "moss-1", theme.model_copy(update={"name": "z" * MAX_THEME_FILE_BYTES}))
    # Every role in long colours, for both schemes: about 10 KB a file, so the folder fills before its count does.
    palette: dict[str, str] = {role: f"oklch(0.{'1' * 20} 0.{'1' * 20} 1)" for role in THEME_ROLES}
    heavy: ThemeFile = parse_theme_file(
        _bytes({"version": 1, "name": "Heavy", "appearance": "light", "colors": palette, "variants": {"dark": palette}})
    )
    with pytest.raises(InvalidValueError, match="192 KB of themes at most"):
        for index in range(MAX_THEME_FILES):
            save_theme(tmp_path / "heavy", f"heavy-{index}", heavy)


def test_only_the_files_the_page_can_draw_are_listed_and_the_others_say_why(tmp_path: Path) -> None:
    (tmp_path / "harbour.json").write_bytes(_bytes(_EXPORTED))
    (tmp_path / "moss.json").write_bytes(_bytes(_SHORT))
    (tmp_path / "neutral.json").write_bytes(_bytes(_SHORT))
    (tmp_path / "My Theme.json").write_bytes(_bytes(_SHORT))
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "large.json").write_bytes(b" " * (MAX_THEME_FILE_BYTES + 1))
    (tmp_path / "folder.json").mkdir()
    os.symlink(tmp_path / "moss.json", tmp_path / "linked.json")
    (tmp_path / "notes.txt").write_text("not a theme", encoding="utf-8")
    listed = read_theme_folder(tmp_path)
    assert [theme.id for theme in listed.themes] == ["harbour", "moss"]
    reasons: dict[str, str] = {skipped.file: skipped.reason for skipped in listed.skipped}
    assert sorted(reasons) == [
        "My Theme.json",
        "broken.json",
        "folder.json",
        "large.json",
        "linked.json",
        "neutral.json",
    ]
    assert "built-in" in reasons["neutral.json"]
    assert "lowercase letters" in reasons["My Theme.json"]
    assert "Invalid JSON" in reasons["broken.json"]
    assert "32 KB" in reasons["large.json"]
    assert reasons["folder.json"] == "not a file"
    assert "link" in reasons["linked.json"]
    assert read_theme_folder(tmp_path / "missing").themes == []


def test_the_daemon_and_the_page_know_the_same_colour_roles() -> None:
    source: str = _GENERATOR.read_text(encoding="utf-8")
    block: re.Match[str] | None = re.search(r"export const THEME_ROLES = \[(.*?)\];", source, re.S)
    assert block is not None
    assert set(re.findall(r'"([A-Za-z]+)"', block.group(1))) == THEME_ROLES
    assert len(THEME_ROLES) == 61

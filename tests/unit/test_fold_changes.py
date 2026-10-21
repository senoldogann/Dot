"""scripts/fold_changes.py: a release's changelog section, from the entries under Unreleased and the files of
changes/."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT: Path = Path(__file__).resolve().parents[2] / "scripts" / "fold_changes.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fold_changes", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fold_changes: ModuleType = _load()

_CHANGELOG: str = """# Changelog

Each release lists what it changes.

## Unreleased

What is not released yet is also in changes/.

- An entry written here before item 459,
  on two lines (item 455).

## 0.4.0 (2026-10-01)

- An old entry.
"""


def test_the_files_and_the_entries_under_unreleased_make_the_section_and_unreleased_keeps_its_note(
    tmp_path: Path,
) -> None:
    (tmp_path / "460.md").write_text("- The newest change (item 460).\n", encoding="utf-8")
    (tmp_path / "459.md").write_text("- A change on\n  two lines (item 459).\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# What this folder is\n", encoding="utf-8")
    entries = fold_changes.read_entries(tmp_path)
    assert [entry.item for entry in entries] == [460, 459]
    assert fold_changes.fold(_CHANGELOG, entries, "0.5.0", "2026-10-22") == (
        "# Changelog\n\nEach release lists what it changes.\n\n## Unreleased\n\n"
        "What is not released yet is also in changes/.\n\n"
        "## 0.5.0 (2026-10-22)\n\n"
        "- The newest change (item 460).\n- A change on\n  two lines (item 459).\n"
        "- An entry written here before item 459,\n  on two lines (item 455).\n\n"
        "## 0.4.0 (2026-10-01)\n\n- An old entry.\n"
    )


def test_a_file_that_is_not_a_list_or_not_named_for_its_item_is_refused(tmp_path: Path) -> None:
    (tmp_path / "461.md").write_text("A paragraph, not an entry.\n", encoding="utf-8")
    with pytest.raises(fold_changes.FoldError, match=r"461\.md is not a list"):
        fold_changes.read_entries(tmp_path)
    (tmp_path / "461.md").unlink()
    for name in ("notes.md", "0461.md"):
        (tmp_path / name).write_text("- An entry.\n", encoding="utf-8")
        with pytest.raises(fold_changes.FoldError, match="not named"):
            fold_changes.read_entries(tmp_path)
        (tmp_path / name).unlink()


def test_a_version_released_already_or_nothing_to_release_is_refused() -> None:
    with pytest.raises(fold_changes.FoldError, match=r"0\.4\.0 already"):
        fold_changes.fold(_CHANGELOG, [], "0.4.0", "2026-10-22")
    empty: str = _CHANGELOG.replace("- An entry written here before item 459,\n  on two lines (item 455).\n\n", "")
    with pytest.raises(fold_changes.FoldError, match="nothing to release"):
        fold_changes.fold(empty, [], "0.5.0", "2026-10-22")

"""The skills Dot could not offer to a run, as the page of skills, ``dotx skills list`` and Doctor say them (item
1405): each with its file, the catalog's reason and where Dot looked for it, in folders made here. The audit's case is
a plugin's SKILL.md that does not start with the --- line: it was a line in the log, and now it is on every list."""

from pathlib import Path

from dotagent.cli_skills import render_catalogs
from dotagent.doctor import CheckResult, _skills_row
from dotagent.plugins.types import PluginCatalog
from dotagent.server.skills_left_out import left_out_view, source_of
from dotagent.server.views import SkillLeftOutView
from dotagent.skills.catalog import SkillRoot, load_catalog
from dotagent.skills.types import SkillCatalog

NO_FRONTMATTER: str = "name: twilio-agent-connect\ndescription: Connect an agent.\n"
NOT_OPENED: str = "---\nname: {name}\ndescription: Does {name}.\n---\nDo {name}.\n"


def _skill(root: Path, name: str, text: str) -> Path:
    folder: Path = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    return folder / "SKILL.md"


def _roots(tmp_path: Path) -> tuple[SkillRoot, ...]:
    return (
        SkillRoot(path=tmp_path / "home", source="home"),
        SkillRoot(
            path=tmp_path / "plugin" / "skills", source="plugin twilio-developer-kit", plugin="twilio-developer-kit"
        ),
        SkillRoot(path=tmp_path / "other", source="config"),
        SkillRoot(path=tmp_path / "gone", source="config"),
    )


def _catalog(tmp_path: Path) -> SkillCatalog:
    """A home with a good skill and one that is not a skill, a plugin whose skill does not start with the --- line (the
    owner's twilio-agent-connect), a folder with a second ``alpha`` that is not offered for its name, and a folder that
    does not exist."""
    _skill(tmp_path / "home", "alpha", NOT_OPENED.format(name="alpha"))
    _skill(tmp_path / "home", "notes", "No frontmatter here.\n")
    _skill(tmp_path / "other", "alpha", NOT_OPENED.format(name="alpha").replace("Do alpha.", "Other."))
    _skill(tmp_path / "plugin" / "skills", "twilio-agent-connect", f"# Twilio\n---\n{NO_FRONTMATTER}---\nBody.\n")
    return load_catalog(_roots(tmp_path))


def test_a_skill_left_out_is_listed_with_its_file_its_reason_and_the_folder_it_was_looked_for_in(
    tmp_path: Path,
) -> None:
    catalog: SkillCatalog = _catalog(tmp_path)
    view = left_out_view(catalog.problems, _roots(tmp_path))
    by_name: dict[str, SkillLeftOutView] = {
        Path(item.path).parent.name if item.path.endswith("SKILL.md") else Path(item.path).name: item
        for item in view.items
    }
    twilio: SkillLeftOutView = by_name["twilio-agent-connect"]
    assert twilio.path == str(tmp_path / "plugin" / "skills" / "twilio-agent-connect" / "SKILL.md")
    assert twilio.reason == "SKILL.md does not start with the --- line that opens the frontmatter"
    assert (twilio.source, twilio.shadowed) == ("plugin twilio-developer-kit", False)
    assert (by_name["notes"].source, by_name["notes"].shadowed) == ("home", False)
    # A folder that does not exist is a problem of its own root; a skill another has the name of is only not offered.
    assert (by_name["gone"].reason, by_name["gone"].source) == ("the skills directory does not exist", "config")
    alpha: SkillLeftOutView = by_name["alpha"]
    assert (alpha.source, alpha.shadowed) == ("config", True)
    assert alpha.reason.startswith("another skill is already called alpha: ")
    # A skill whose file is wrong comes before one that is not wrong at all, each in the order the catalog found them.
    assert [item.shadowed for item in view.items] == [False, False, False, True]


def test_the_same_skill_is_on_doctors_row_and_in_dotx_skills_list(tmp_path: Path) -> None:
    catalog: SkillCatalog = _catalog(tmp_path)
    twilio: str = str(tmp_path / "plugin" / "skills" / "twilio-agent-connect" / "SKILL.md")
    reason: str = "SKILL.md does not start with the --- line that opens the frontmatter"
    row: CheckResult = _skills_row(catalog)
    assert row.name == "skills" and not row.ok
    assert f"{tmp_path / 'home' / 'notes' / 'SKILL.md'}: {reason}" in row.detail
    assert f"{twilio}: {reason}" in row.detail
    assert "another skill is already called alpha" not in row.detail
    listed: list[str] = render_catalogs(PluginCatalog(plugins=(), problems=()), catalog)
    assert f"  {twilio}: {reason}" in listed


def test_a_path_is_taken_to_the_deepest_folder_that_holds_it_and_to_none_when_none_does(tmp_path: Path) -> None:
    roots: tuple[SkillRoot, ...] = (
        SkillRoot(path=tmp_path / "skills", source="config"),
        SkillRoot(path=tmp_path / "skills" / "kit" / "skills", source="plugin kit", plugin="kit"),
    )
    assert source_of(tmp_path / "skills" / "kit" / "skills" / "a" / "SKILL.md", roots) == "plugin kit"
    assert source_of(tmp_path / "skills" / "b" / "SKILL.md", roots) == "config"
    assert source_of(tmp_path / "skills", roots) == "config"
    assert source_of(tmp_path / "skills-elsewhere" / "c" / "SKILL.md", roots) is None
    assert left_out_view((), roots).items == ()

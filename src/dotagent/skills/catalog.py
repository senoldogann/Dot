"""The catalog: every skills directory read into the usable skills, each name once, and the folders left out."""

from collections.abc import Sequence
from pathlib import Path

from dotagent.config import AppConfig, SkillsConfig
from dotagent.core.errors import SkillError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.paths import playbooks_dir, skills_dir
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.plugins.types import Plugin
from dotagent.skills.discover import find_skill_files
from dotagent.skills.parse import parse_skill
from dotagent.skills.types import Skill, SkillCatalog, SkillProblem


class SkillRoot(FrozenModel):
    """A directory of skills and where it was named: Dot's own folder, the configuration, or a plugin."""

    path: Path
    source: str


# Where a skill of Dot's own skills folder was found: the owner's, and Dot's copies of other agents' (item 165).
HOME_SOURCE: str = "home"
# Where a skill that Dot distilled itself was found (:mod:`dotagent.agent.playbooks`).
PLAYBOOK_SOURCE: str = "learned"
# The prefixes of the names of the skills Dot keeps in its playbook folder: what it learned from its own work, what the
# owner taught it by showing a task once (item 423) and the folders written before item 92. The prompt tells a run that
# a ``taught-`` skill is the owner's own instruction, and the uses of learned skills are counted by these names, so
# they are Dot's alone: a skill of any other root with one is left out and reported (item 557).
PLAYBOOK_PREFIX: str = "learned-"
TAUGHT_PREFIX: str = "taught-"
OLD_PLAYBOOK_PREFIX: str = "playbook-"
PLAYBOOK_PREFIXES: tuple[str, ...] = (PLAYBOOK_PREFIX, TAUGHT_PREFIX, OLD_PLAYBOOK_PREFIX)
# Where a skill that comes with Dot was found, and the folder of those skills in the package (item 196).
BUILTIN_SOURCE: str = "built-in"
BUILTIN_SKILLS: Path = Path(__file__).parent / "builtin"


def skill_roots(config: SkillsConfig, plugins: Sequence[Plugin], playbooks: Path | None) -> tuple[SkillRoot, ...]:
    """``<home>/skills``, then the ``[skills] dirs`` entries, then the skill folders of each plugin, then the playbook
    folder when playbooks are on (``None`` when they are off: a playbook that exists is not offered then), then the
    skills that come with Dot when ``[skills] builtin`` is on. A playbook folder that does not exist yet is no problem:
    none was written. A plugin taken out since Dot started (item 497) is gone from its folder, and its skills with it:
    it is no problem either."""
    roots: list[SkillRoot] = [SkillRoot(path=skills_dir(), source=HOME_SOURCE)]
    roots.extend(SkillRoot(path=Path(entry).expanduser(), source="config") for entry in config.dirs)
    for plugin in plugins:
        if plugin.root.is_dir():
            roots.extend(SkillRoot(path=folder, source=f"plugin {plugin.name}") for folder in plugin.skill_dirs)
    if playbooks is not None and playbooks.is_dir():
        roots.append(SkillRoot(path=playbooks, source=PLAYBOOK_SOURCE))
    if config.builtin:
        roots.append(SkillRoot(path=BUILTIN_SKILLS, source=BUILTIN_SOURCE))
    return tuple(roots)


def builtin_skill_names() -> frozenset[str]:
    """The names of the skills that come with Dot, which are the names of their folders."""
    return frozenset(file.parent.name for file in find_skill_files(BUILTIN_SKILLS))


def _reason(error: SkillError) -> str:
    details: JsonObject = {key: value for key, value in error.context.items() if key != "path"}
    return error.message if not details else f"{error.message} ({dumps_compact(details)})"


def read_skill(file: Path, source: str) -> Skill:
    """The skill ``file`` holds; one that cannot be read as UTF-8 text, or is no skill the loader reads, raises
    :class:`SkillError`."""
    try:
        text: str = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillError("SKILL.md cannot be read as UTF-8 text", {"path": str(file), "error": str(exc)}) from exc
    return parse_skill(file, text, source)


def load_catalog(roots: Sequence[SkillRoot]) -> SkillCatalog:
    """Read every root. A root that is missing, a folder that is not a skill, a name used twice, or a name that is
    Dot's (:data:`PLAYBOOK_PREFIXES`, on a skill that is not in the playbook folder) is reported and left out while the
    other skills still load. A skill reached through two roots (a symlink and its target) counts once. A skill that
    comes with Dot gives way to one of the user's with its name, which is no problem: it is how the user replaces it."""
    skills: dict[str, Skill] = {}
    problems: list[SkillProblem] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.path.is_dir():
            problems.append(SkillProblem(path=root.path, reason="the skills directory does not exist"))
            continue
        try:
            files: tuple[Path, ...] = find_skill_files(root.path)
        except SkillError as exc:
            problems.append(SkillProblem(path=root.path, reason=_reason(exc)))
            continue
        for file in files:
            real: Path = file.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                skill: Skill = read_skill(file, root.source)
            except SkillError as exc:
                problems.append(SkillProblem(path=file, reason=_reason(exc)))
                continue
            if root.source != PLAYBOOK_SOURCE and skill.name.startswith(PLAYBOOK_PREFIXES):
                problems.append(
                    SkillProblem(
                        path=file,
                        reason=f"the name {skill.name} is kept for the skills Dot learned or was taught "
                        f"({', '.join(PLAYBOOK_PREFIXES)}): give the skill another name",
                    )
                )
                continue
            if skill.name in skills:
                if root.source != BUILTIN_SOURCE:
                    taken: Path = skills[skill.name].root
                    problems.append(
                        SkillProblem(path=file, reason=f"another skill is already called {skill.name}: {taken}")
                    )
                continue
            skills[skill.name] = skill
    return SkillCatalog(skills=tuple(skills.values()), problems=tuple(problems))


def load_skills(config: SkillsConfig, plugins: Sequence[Plugin], playbooks: Path | None) -> SkillCatalog:
    """The skills of this installation, read fresh from disk, with the playbooks under ``playbooks`` when given."""
    return load_catalog(skill_roots(config, plugins, playbooks))


def playbook_root(config: AppConfig) -> Path | None:
    """The playbook folder the skills catalog reads: ``<home>/playbooks`` when ``[agent] playbooks`` is on."""
    return playbooks_dir() if config.agent.playbooks else None

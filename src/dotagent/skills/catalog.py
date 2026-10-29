"""The catalog: every skills directory read into the usable skills, each name once, and the folders left out."""

import re
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
from dotagent.skills.types import SKILL_NAME_PATTERN, Skill, SkillCatalog, SkillProblem


class SkillRoot(FrozenModel):
    """A directory of skills and where it was named: Dot's own folder, the configuration, or a plugin (``plugin``, the
    plugin's name, by which a skill of it whose name another skill has is offered, item 816)."""

    path: Path
    source: str
    plugin: str | None = None


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
            roots.extend(
                SkillRoot(path=folder, source=f"plugin {plugin.name}", plugin=plugin.name)
                for folder in plugin.skill_dirs
            )
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


_MAX_NAME_CHARS: int = 64


def plugin_skill_name(plugin: str, skill: str) -> str | None:
    """The name a plugin's skill is offered by when another skill has its own (item 816): ``<plugin>-<skill>``, as a
    plugin's MCP server is named; ``None`` when that is no skill name (longer than 64 characters, or the plugin's name
    holds what a skill name may not)."""
    derived: str = f"{plugin}-{skill}".lower().replace("_", "-").replace(" ", "-")
    if len(derived) > _MAX_NAME_CHARS or re.fullmatch(SKILL_NAME_PATTERN, derived) is None:
        return None
    return derived


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
    other skills still load. A skill reached through two roots (a symlink and its target) counts once, and so does a
    copy of one (the same name, description and instructions in its SKILL.md in another folder, as a sync of the same
    skills leaves; the other files of the folders are not compared, and the first one's are used).
    A skill of a plugin whose name another skill has, and is no copy of it, is offered as ``<plugin>-<skill>``
    (:func:`plugin_skill_name`, item 816): two plugins (or a plugin and the owner) may each bring a ``code-review`` of
    their own, and the second was left out. It is left out only when that name is taken too or is no skill name.
    A skill that comes with Dot gives way to one of the user's with its name, which is no problem: it is how the user
    replaces it."""
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
                kept: Skill = skills[skill.name]
                copy: bool = (kept.description, kept.body) == (skill.description, skill.body)
                renamed: str | None = None if root.plugin is None else plugin_skill_name(root.plugin, skill.name)
                if not copy and renamed is not None and renamed not in skills:
                    skills[renamed] = skill.model_copy(update={"name": renamed})
                    continue
                if root.source != BUILTIN_SOURCE and not copy:
                    problems.append(
                        SkillProblem(
                            path=file,
                            reason=f"another skill is already called {skill.name}: {kept.root}",
                            shadowed=True,
                        )
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

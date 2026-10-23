"""The ``skill`` tool: the instructions of a skill the run is offered and the files that come with it."""

import asyncio
import secrets
import shlex
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from dotagent.agent.playbooks import OfferedSkills, date_reads, offered_with_marks
from dotagent.config import AppConfig
from dotagent.core.text import text_window, unclosing
from dotagent.policy.types import ActionInfo
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import PYTHON_MARK, SKILL_FILE, SKILL_TOOL, Skill, SkillCatalog
from dotagent.tools.base import (
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    fail,
    make_tool,
    mark_untrusted,
    ok,
)
from dotagent.tools.context import ToolContext

_NONCE_BYTES: int = 8
# Room in a result for the line that says where the instructions go on.
_GO_ON_CHARS: int = 200
# Every part holds at least this much of the instructions, however little room the first line and the files leave.
_MIN_PART_CHARS: int = 1000
# The list of the files takes at most this part of a result: 1/4.
_FILES_SHARE: int = 4
_MAX_LISTED_FILES: int = 200
_SKIPPED: frozenset[str] = frozenset({"node_modules", "__pycache__"})


class SkillArgs(ToolArgs):
    name: str = Field(
        description=(
            "The name of the skill, exactly as listed under Skills in your instructions, under a message, or by "
            "find_help."
        )
    )
    start: int | None = Field(
        default=None,
        ge=0,
        description="Where to go on in instructions too long for one result: the start the result before named.",
    )


def files_of_skill(root: Path) -> list[str]:
    """The files that come with a skill, relative to its folder, in a stable order; hidden paths are left out."""
    listed: list[str] = []
    for path in sorted(root.rglob("*")):
        relative: Path = path.relative_to(root)
        if any(part.startswith(".") or part in _SKIPPED for part in relative.parts):
            continue
        if path.is_file() and relative.name != SKILL_FILE:
            listed.append(str(relative))
        if len(listed) >= _MAX_LISTED_FILES:
            break
    return listed


def with_python(skill: Skill, python: str) -> Skill:
    """``skill`` with :data:`PYTHON_MARK` in its instructions replaced by ``python``, the path of Dot's own Python as
    the shell takes it."""
    return skill.model_copy(update={"body": skill.body.replace(PYTHON_MARK, python)})


def fence_skill(skill: Skill, text: str, nonce: str) -> str:
    """``text``, the skill's instructions or a part of them, inside a fence that only ``nonce`` closes, so the prompt
    can say where the skill ends."""
    safe: str = unclosing(text, "skill_content")
    return f'<skill_content nonce="{nonce}" name="{skill.name}">\n{safe}\n</skill_content nonce="{nonce}">'


def _listing(skill: Skill, files: Sequence[str], room: int) -> list[str]:
    """The lines that list the files of a skill within ``room`` characters, and how many more there are."""
    if not files:
        return ["The skill has no files besides SKILL.md."]
    lines: list[str] = [f"Files of this skill, relative to {skill.root} (use absolute paths to read or run them):"]
    used: int = len(lines[0])
    for index, name in enumerate(files):
        if used + len(name) + 1 > room:
            lines.append(f"... and {len(files) - index} more files (list_dir lists them).")
            break
        lines.append(name)
        used += len(name) + 1
    return lines


def render_skill(skill: Skill, files: Sequence[str], nonce: str, start: int, room: int) -> str:
    """The instructions of ``skill`` from character ``start``, as much of them as ``room`` characters hold next to the
    first line, the fence and, in the first part, the files of the skill (at most a quarter of the room); a part that
    does not reach the end says where the next one starts."""
    where: str = "" if start == 0 else f", its instructions from character {start} on"
    head: str = (
        f"Skill {skill.name} ({skill.source}), folder {skill.root}{where}. Follow these instructions for the current "
        "task; the user's own message and your rules still come first."
    )
    listing: list[str] = _listing(skill, files, room // _FILES_SHARE) if start == 0 else []
    taken: int = len(head) + len(fence_skill(skill, "", nonce)) + sum(len(line) + 1 for line in listing) + _GO_ON_CHARS
    part, following = text_window(skill.body, start, max(room - taken, _MIN_PART_CHARS))
    lines: list[str] = [head, fence_skill(skill, part, nonce), *listing]
    if following < len(skill.body):
        lines.append(
            f"The instructions go on past this part: read the rest with skill(name={skill.name!r}, start={following}) "
            "before you act on them."
        )
    return "\n".join(lines)


async def skill_instructions(skill: Skill, room: int) -> str:
    """The first part of ``skill``'s instructions, with its files, as the ``skill`` tool gives them within ``room``
    characters: for a run that starts with a skill the owner picked (item 318)."""
    ready: Skill = with_python(skill, shlex.quote(sys.executable))
    files: list[str] = await asyncio.to_thread(files_of_skill, ready.root)
    return render_skill(ready, files, secrets.token_hex(_NONCE_BYTES), 0, room)


def build_skill_tool() -> RegisteredTool:
    def classify(args: SkillArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=SKILL_TOOL, kind="meta", risk="safe", summary=f"Read skill {args.name}", target=args.name, flags=()
        )

    async def run(args: SkillArgs, ctx: ToolContext) -> ToolResult:
        # Only a skill the run is offered, as its instructions and find_help list them: the learned skills of other
        # agents are neither read nor named (item 94).
        config: AppConfig = ctx.runtime.config
        catalog: SkillCatalog = await asyncio.to_thread(
            load_skills, config.skills, ctx.runtime.plugins.plugins, playbook_root(config)
        )
        offered: OfferedSkills = await offered_with_marks(config, catalog, ctx.agent_id)
        found: Skill | None = next((entry for entry in offered.skills if entry.name == args.name), None)
        if found is None:
            names: str = ", ".join(entry.name for entry in offered.skills) or "none"
            return fail(f"No skill offered to this run is called {args.name}. The skills it is offered: {names}.")
        # The read is dated here, where it happens, whatever the run is (a chat, a helper's, one that will be stopped):
        # a skill that no run read for the days rests (item 585). Before the run is handed the instructions; a date
        # that cannot be written is logged and the skill is read all the same.
        root: Path | None = playbook_root(config)
        if root is not None:
            await date_reads(root, [found], time.time())
        skill: Skill = with_python(found, shlex.quote(sys.executable))
        start: int = args.start or 0
        if start > 0 and start >= len(skill.body):
            return fail(
                f"The instructions of {skill.name} end at character {len(skill.body)}: start {start} is past them."
            )
        # The files come with the first part of the instructions.
        files: list[str] = await asyncio.to_thread(files_of_skill, skill.root) if start == 0 else []
        read: ToolResult = ok(render_skill(skill, files, secrets.token_hex(_NONCE_BYTES), start, ctx.output_chars))
        # A learned skill whose steps may come from content third parties can author is such content for the run that
        # reads it (item 585): the run is tainted from here on, as one that read a page, and what it learns is marked.
        return mark_untrusted(read) if found.name in offered.third_party else read

    return make_tool(
        name=SKILL_TOOL,
        description=(
            "Read an installed skill before working on a task that matches one listed under Skills in your "
            "instructions or under a message. It returns the skill's instructions and the files that come with it "
            "(scripts to run with the shell, references to read, assets to use). Then follow the instructions."
        ),
        args_model=SkillArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=15.0,
        classify=classify,
        run=run,
    )

"""The ``skill`` tool: the instructions of an installed skill and the files that come with it."""

import asyncio
import secrets
from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from dotagent.policy.types import ActionInfo
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import SKILL_FILE, Skill, SkillCatalog
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

SKILL_TOOL: str = "skill"
_NONCE_BYTES: int = 8
_MAX_LISTED_FILES: int = 200
_SKIPPED: frozenset[str] = frozenset({"node_modules", "__pycache__"})


class SkillArgs(ToolArgs):
    name: str = Field(description="The name of the skill, exactly as listed under Skills in your instructions.")


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


def fence_skill(skill: Skill, nonce: str) -> str:
    """The instructions inside a fence that only ``nonce`` closes, so the prompt can say where the skill ends."""
    safe: str = skill.body.replace("</skill_content", "<\\/skill_content")
    return f'<skill_content nonce="{nonce}" name="{skill.name}">\n{safe}\n</skill_content nonce="{nonce}">'


def render_skill(skill: Skill, files: Sequence[str], nonce: str) -> str:
    lines: list[str] = [
        f"Skill {skill.name} ({skill.source}), folder {skill.root}. Follow these instructions for the current task; "
        "the user's own message and your rules still come first.",
        fence_skill(skill, nonce),
    ]
    if files:
        lines.append(f"Files of this skill, relative to {skill.root} (use absolute paths to read or run them):")
        lines.extend(files)
    else:
        lines.append("The skill has no files besides SKILL.md.")
    return "\n".join(lines)


def build_skill_tool() -> RegisteredTool:
    def classify(args: SkillArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=SKILL_TOOL, kind="meta", risk="safe", summary=f"Read skill {args.name}", target=args.name, flags=()
        )

    async def run(args: SkillArgs, ctx: ToolContext) -> ToolResult:
        catalog: SkillCatalog = await asyncio.to_thread(
            load_skills, ctx.runtime.config.skills, ctx.runtime.plugins.plugins, playbook_root(ctx.runtime.config)
        )
        skill: Skill | None = catalog.find(args.name)
        if skill is None:
            names: str = ", ".join(entry.name for entry in catalog.skills) or "none"
            return fail(f"No skill is called {args.name}. Installed skills: {names}.")
        files: list[str] = await asyncio.to_thread(files_of_skill, skill.root)
        return ok(render_skill(skill, files, secrets.token_hex(_NONCE_BYTES)))

    return make_tool(
        name=SKILL_TOOL,
        description=(
            "Read an installed skill before working on a task that matches one listed under Skills in your "
            "instructions. It returns the skill's instructions and the files that come with it (scripts to run with "
            "the shell, references to read, assets to use). Then follow the instructions."
        ),
        args_model=SkillArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=15.0,
        classify=classify,
        run=run,
    )

"""``skill_save`` (item 893, after a guide to Grok's bots the owner sent on 2026-10-31: "tell your bots to create skills
for itself as you give it instructions on how to do tasks. this lets them do them more consistently even over long
conversations"). A run writes down how the owner told it to do a kind of work as a taught skill, ``taught-<kind>``, of
its agent (or Dot's), so that every later run of that kind reads it first (the ``skill`` tool); told again otherwise,
it rewrites that skill as a new version, the old one kept.

A skill is an instruction every later run follows, so only the owner's words may make one: a run that has read content
third parties can author (a page, a mail, a tool's output) cannot save one, nor can a run in a chat Dot opened on its
own initiative, and the secrets Dot holds are masked in it. The owner sees it under Customize › Skills, where they
change, pin or drop it, and the conversation says it was saved."""

import asyncio
import time

from pydantic import Field, ValidationError

from dotagent.agent.playbooks import redacted_draft, revise_said_skill, save_said_skill
from dotagent.agent.review import PlaybookDraft
from dotagent.core.errors import ToolInputError
from dotagent.core.paths import playbooks_dir
from dotagent.core.text import redact_known, truncate_tail
from dotagent.policy.types import ActionInfo
from dotagent.proactive import INITIATIVE_SOURCE
from dotagent.secrets import known_secrets
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

SKILL_SAVE_TOOL: str = "skill_save"
_SUMMARY_CHARS: int = 120
_WHEN: str = "Every goal of this kind of work: the owner said to do it this way."


class SkillSaveArgs(ToolArgs):
    kind: str = Field(min_length=3, max_length=60, description="As a later goal names it.")
    description: str = Field(min_length=1, max_length=200, description="What, where, for which goals.")
    steps: list[str] = Field(
        min_length=1, max_length=20, description="Their steps in order; changing values as <name>."
    )
    replace: str | None = Field(default=None, description="A skill saved so, to rewrite.")


def _refused(why: str) -> ToolResult:
    return fail(f"no skill was saved: {why}")


def _skill_save_tool() -> RegisteredTool:
    def classify(args: SkillSaveArgs, _ctx: ToolContext) -> ActionInfo:
        what: str = truncate_tail(" ".join(args.kind.split()), _SUMMARY_CHARS)
        verb: str = "Rewrite the skill" if args.replace else "Save a skill"
        return ActionInfo(
            tool=SKILL_SAVE_TOOL, kind="memory_write", risk="low", summary=f"{verb}: {what}", target="skills", flags=()
        )

    async def run(args: SkillSaveArgs, ctx: ToolContext) -> ToolResult:
        # At any time in the run or in a run of its chat before it, not only as it stands: the owner's "go on" clears
        # the taint, not the page's words, which the chat's history keeps (the pre-push review).
        if ctx.ever_tainted() or await ctx.runtime.store.chat_ever_tainted(ctx.thread_id):
            return _refused(
                "this run has read content others can write (a page, a mail, a tool's output), and a skill is an "
                "instruction every later run follows. Ask the owner to say the steps in a chat that has not, or to "
                "save them under Customize › Skills."
            )
        if await ctx.runtime.store.opened_by(ctx.thread_id) == INITIATIVE_SOURCE:
            return _refused("a skill is the owner's instruction, and this chat is one Dot opened on its own.")
        held: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        try:
            draft: PlaybookDraft = PlaybookDraft(
                kind=args.kind,
                description=args.description,
                # Not the description again (the pre-push review): when it fits is the kind, as the owner said it.
                when=_WHEN,
                steps=[" ".join(step.split()) for step in args.steps if step.strip()],
                parameters=[],
            )
        except ValidationError as exc:
            raise ToolInputError("the skill is not one a run can follow", {"error": str(exc)}) from exc
        draft = redacted_draft(draft, held)
        now: float = time.time()
        if args.replace:
            words: str = redact_known(truncate_tail(" ".join(ctx.user_text().split()), 400), held)
            version: int = await asyncio.to_thread(
                revise_said_skill, playbooks_dir(), args.replace.strip(), draft, ctx.agent_id, words, now
            )
            return ok(
                f"Rewrote the skill {args.replace.strip()} (version {version}; the one before is kept). Later runs of "
                "this kind follow it. Tell the owner, in a line, that you did."
            )
        name: str = await asyncio.to_thread(save_said_skill, playbooks_dir(), draft, ctx.agent_id, now)
        return ok(
            f"Saved the skill {name}: later runs of this kind read it first and follow it. Tell the owner, in a line, "
            "that you saved it and that Customize › Skills shows it. Told otherwise later, rewrite it with replace."
        )

    return make_tool(
        name=SKILL_SAVE_TOOL,
        description="Save how the owner told you to do a kind of work as a skill later runs follow. Their steps.",
        args_model=SkillSaveArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=30.0,
        classify=classify,
        run=run,
    )


def build_skill_save_tool() -> RegisteredTool:
    return _skill_save_tool()

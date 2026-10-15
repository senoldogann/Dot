"""``dotx models profile``: how reliably a model worked in its newest finished runs, and the scaffold its next run gets
from that and from its window. The database is only read; the window is asked from the model's provider."""

import asyncio
import sys

from dotagent.agent.scaffold import PROFILE_RUNS, Scaffold, scaffold_for
from dotagent.cli_commands import Command, ModelsProfileCommand
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import db_path
from dotagent.llm.calls import log_model_call
from dotagent.llm.router import ModelRef, ModelRouter, parse_model_ref
from dotagent.llm.types import ModelInfo
from dotagent.store.stats import ModelProfile, read_model_profile


def _percent(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{round(100 * part / whole)} %"


def render_profile(profile: ModelProfile, scaffold: Scaffold) -> list[str]:
    """The profile of a model and the scaffold of its next run, as lines of text."""
    measured: list[str] = (
        [f"{profile.model}: no finished runs yet"]
        if profile.runs == 0
        else [
            f"{profile.model}, its newest {profile.runs} finished runs: {profile.steps} steps",
            f"  tools: {profile.tool_calls} calls, {profile.tool_errors} failed "
            f"({_percent(profile.tool_errors, profile.tool_calls)}), {profile.refused_calls} refused before they ran "
            f"({_percent(profile.refused_calls, profile.tool_calls)}), {profile.invalid_arguments} with arguments that "
            "were not valid JSON",
            f"  replies with neither text nor a tool call {profile.empty_replies}, stalls {profile.stalls}, "
            f"compactions {profile.compactions}",
        ]
    )
    window: str = "not reported by its provider" if scaffold.window is None else f"{scaffold.window:,} tokens"
    chosen: str = "" if scaffold.chosen is None else f" (its size of {scaffold.chosen:,} chosen in Settings › Models)"
    reminders: str = (
        "no reminders of the goal"
        if scaffold.reminder_every is None
        else f"reminded of the goal every {scaffold.reminder_every} steps in task and research runs"
    )
    return [
        *measured,
        "Its next run:",
        f"  window {window}, context budget {scaffold.context_tokens:,} tokens{chosen}, "
        f"tool output up to {scaffold.tool_output_chars:,} characters",
        f"  tier {scaffold.tier}: {scaffold.reason}",
        f"  {reminders}",
    ]


async def cmd_models_profile(command: ModelsProfileCommand) -> int:
    config: AppConfig = load_user_config()
    ref: ModelRef = parse_model_ref(command.model if command.model is not None else config.models.main)
    router: ModelRouter = ModelRouter(config, log_model_call)
    try:
        info: ModelInfo = await router.model_info(ref)
    finally:
        await router.aclose()
    profile: ModelProfile = await asyncio.to_thread(read_model_profile, db_path(), ref.label, PROFILE_RUNS)
    scaffold: Scaffold = scaffold_for(config.agent, info, profile, config.models.context.get(ref.label))
    sys.stdout.write("\n".join(render_profile(profile, scaffold)) + "\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case ModelsProfileCommand():
            return await cmd_models_profile(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})

"""The prompt surface Dot sends a model, rendered as files that are checked in (item 1858): the system prompt of a few
kinds of run and the schema of every built-in tool, with the machine's own facts (its user, home, macOS version, the
date) set to fixed words, so that the same code renders the same files anywhere.

``tests/unit/test_prompt_surface.py`` renders them and compares them with ``tests/fixtures/prompt_surface/``: a change
that alters a word of the prompt or of a tool's schema fails until ``uv run python scripts/prompt_surface.py`` writes
the files again, in the same pull request, so that the review reads the change of what the model is told as a diff, and
``summary.txt`` says how many characters and estimated tokens each part has.
"""

import json
from pathlib import Path

from dotagent.agent.prompt import PromptInputs, build_system_prompt, collect_prompt_inputs
from dotagent.config import AppConfig, load_default_config
from dotagent.core.text import CHARS_PER_TOKEN
from dotagent.core.types import FrozenModel
from dotagent.harness.components import NO_HARNESS
from dotagent.help.listed import tools_in_order
from dotagent.llm.types import ToolSpec
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import _tool_groups
from dotagent.store.models import RunMode
from dotagent.tools.campaign import CAMPAIGN_TOOLS
from dotagent.tools.posts import POST_TOOLS
from dotagent.tools.registry import ToolRegistry, build_registry, specs_for_mode

# The folder of the checked-in files, from the repository's root.
SURFACE_DIR: Path = Path("tests") / "fixtures" / "prompt_surface"
# The machine's facts the prompt states, fixed so that the files do not depend on who renders them.
_FIXED: dict[str, str] = {
    "home": "/Users/owner",
    "user": "owner",
    "os_version": "26.0",
    "today_text": "Wednesday 2026-11-25, time zone UTC (UTC+0000)",
}
_WORKSPACE: Path = Path("/Users/owner/Library/Application Support/Dot/workspace")
# How the evaluated prompt is told the thread's title, which orders the tools of a big group.
_TITLE: str = ""


class Variant(FrozenModel):
    """A kind of run whose system prompt is rendered: its file name, mode, approvals and prompt style."""

    name: str
    mode: RunMode
    approvals: ApprovalMode
    lean: bool


VARIANTS: tuple[Variant, ...] = (
    Variant(name="task-ask", mode="task", approvals="ask", lean=False),
    Variant(name="task-yolo", mode="task", approvals="yolo", lean=False),
    Variant(name="task-lean", mode="task", approvals="ask", lean=True),
    Variant(name="chat", mode="chat", approvals="ask", lean=False),
    Variant(name="research", mode="research", approvals="ask", lean=False),
)


def _registry(config: AppConfig) -> ToolRegistry:
    """Every built-in tool, the desktop's among them, as a run of an installation with every part on is offered."""
    every: AppConfig = config.model_copy(update={"desktop": config.desktop.model_copy(update={"enabled": True})})
    return build_registry(_tool_groups(every, True))


def _specs(registry: ToolRegistry, mode: RunMode) -> tuple[ToolSpec, ...]:
    """The tools a run of ``mode`` that is not a campaign's session has."""
    return tuple(spec for spec in specs_for_mode(registry, mode) if spec.name not in CAMPAIGN_TOOLS | POST_TOOLS)


def _inputs(config: AppConfig, variant: Variant, specs: tuple[ToolSpec, ...]) -> PromptInputs:
    collected: PromptInputs = collect_prompt_inputs(
        variant.mode,
        "ui",
        config.policy.preset,
        variant.approvals,
        "wait",
        False,
        False,
        _WORKSPACE,
        (),
        0,
        tools_in_order(specs, _TITLE),
        (),
        0,
        None,
        (),
        None,
        (),
        (),
        None,
        "lean" if variant.lean else "full",
        NO_HARNESS,
    )
    return collected.model_copy(update=_FIXED)


def _tool_text(specs: tuple[ToolSpec, ...]) -> str:
    """Every tool's name, description and parameters' schema, by name, as the model is sent them."""
    ordered: list[dict[str, object]] = [
        {"name": spec.name, "description": spec.description, "parameters": spec.parameters}
        for spec in sorted(specs, key=lambda spec: spec.name)
    ]
    return json.dumps(ordered, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _measure(name: str, text: str) -> str:
    """The size of a part in a line: its characters and the tokens Dot estimates them at (``CHARS_PER_TOKEN``)."""
    return f"{name}: {len(text)} characters, ~{round(len(text) / CHARS_PER_TOKEN)} tokens"


def render_surface() -> dict[str, str]:
    """The files of the prompt surface by their name: ``system-<variant>.txt`` for each of :data:`VARIANTS`,
    ``tools.json`` for every built-in tool, and ``summary.txt`` with the size of each."""
    config: AppConfig = load_default_config()
    registry: ToolRegistry = _registry(config)
    files: dict[str, str] = {}
    sizes: list[str] = []
    for variant in VARIANTS:
        text: str = build_system_prompt(_inputs(config, variant, _specs(registry, variant.mode))) + "\n"
        files[f"system-{variant.name}.txt"] = text
        sizes.append(_measure(f"system-{variant.name}", text))
    tools: str = _tool_text(_specs(registry, "task"))
    files["tools.json"] = tools
    sizes.append(_measure("tools", tools))
    files["summary.txt"] = "\n".join(sizes) + "\n"
    return files


def stale_files(folder: Path, rendered: dict[str, str]) -> list[str]:
    """The names of the files of ``rendered`` that ``folder`` lacks or holds otherwise, and of the files it holds that
    are not rendered any more."""
    kept: set[str] = {path.name for path in folder.iterdir()} if folder.is_dir() else set()
    differing: list[str] = [
        name
        for name, text in rendered.items()
        if name not in kept or (folder / name).read_text(encoding="utf-8") != text
    ]
    return sorted([*differing, *(kept - set(rendered))])


def write_surface(folder: Path, rendered: dict[str, str]) -> None:
    """Write ``rendered`` into ``folder``, removing the files it holds that are not rendered any more."""
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.iterdir():
        if path.name not in rendered:
            path.unlink()
    for name, text in rendered.items():
        (folder / name).write_text(text, encoding="utf-8")

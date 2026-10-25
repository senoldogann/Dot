"""Tool contract: validated arguments, a classified action, and a result the model can read."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from dotagent.core.errors import ToolExecutionError, ToolInputError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import ImageData, ToolSpec
from dotagent.policy.types import ActionInfo
from dotagent.store.models import RunMode
from dotagent.tools.context import ToolContext
from dotagent.tools.schema import tool_parameters_schema

ALL_MODES: frozenset[RunMode] = frozenset({"task", "chat", "research"})
ACTING_MODES: frozenset[RunMode] = frozenset({"task", "chat"})
TASK_MODES: frozenset[RunMode] = frozenset({"task", "research"})


class ToolArgs(BaseModel):
    """Base for tool argument models: unknown fields are rejected so typos surface immediately."""

    model_config = ConfigDict(extra="forbid")


class ToolResult(FrozenModel):
    """What the model reads back. ``untrusted`` marks text that other people or programs can author (what an
    application shows on the screen): the run counts it as untrusted input from then on."""

    text: str
    images: tuple[ImageData, ...]
    is_error: bool
    untrusted: bool = False


def ok(text: str) -> ToolResult:
    return ToolResult(text=text, images=(), is_error=False)


def fail(text: str) -> ToolResult:
    return ToolResult(text=text, images=(), is_error=True)


def ok_with_images(text: str, images: tuple[ImageData, ...]) -> ToolResult:
    return ToolResult(text=text, images=images, is_error=False)


def mark_untrusted(result: ToolResult) -> ToolResult:
    """The same result, flagged as carrying content that a third party can write."""
    return result.model_copy(update={"untrusted": True})


def text_room(output_chars: int, headroom: int) -> int:
    """The characters of page or window text that one result of ``output_chars`` carries next to ``headroom`` for the
    rest of it, and never less than half the result: a small model window shortens the text instead of leaving none
    (item 156)."""
    return max(output_chars // 2, output_chars - headroom)


@dataclass(frozen=True)
class PreparedCall:
    """A call whose arguments validated: the classified action plus the coroutine that performs it.

    ``action`` is the step as everything that records or shows it reads it. ``weighed`` is the same step as the run and
    the page wrote it, for a tool that masks ``action`` (the browser and web tools take the values a login filled out of
    its summary and target, item 621): the owner's rules are matched against it, so that a rule fits a step alike
    whether or not a word in it is the password. ``None`` for a tool whose action is as it was classified."""

    action: ActionInfo
    run: Callable[[ToolContext], Awaitable[ToolResult]]
    weighed: ActionInfo | None = None


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    modes: frozenset[RunMode]
    parallel_safe: bool
    timeout_s: float
    prepare: Callable[[JsonObject, ToolContext], Awaitable[PreparedCall]]


type ErrorPath = tuple[int | str, ...]


def _properties_of(node: JsonValue) -> JsonObject:
    """The field schemas of an object schema; empty for anything else."""
    if isinstance(node, dict):
        properties: JsonValue = node.get("properties")
        if isinstance(properties, dict):
            return properties
    return {}


def _fields_at(schema: JsonObject, path: ErrorPath) -> list[str] | None:
    """The field names of the object that ``path`` (field names and list positions) reaches from the root of the
    schema; ``None`` when the path ends at something that is not an object with known fields."""
    node: JsonValue = schema
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get("items") if isinstance(part, int) else _properties_of(node).get(part)
    if isinstance(node, dict) and isinstance(node.get("properties"), dict):
        return list(_properties_of(node))
    return None


def _owner_path(location: ErrorPath) -> ErrorPath:
    """The object an error is about: the one that holds the bad field, or the object itself when a check of the
    whole object failed."""
    return location[:-1] if location and isinstance(location[-1], str) else location


def _path_label(path: ErrorPath) -> str:
    """``steps[]`` for ``("steps", 3)``: the valid fields are the same for every list item."""
    return "".join("[]" if isinstance(part, int) else f".{part}" for part in path).removeprefix(".")


def _valid_field_hints(locations: list[ErrorPath], schema: JsonObject) -> list[str]:
    """One line per object that has an error, naming the fields that object accepts."""
    hints: dict[str, str] = {}
    for location in locations:
        owner: ErrorPath = _owner_path(location)
        label: str = _path_label(owner)
        fields: list[str] | None = _fields_at(schema, owner)
        if fields is None or label in hints:
            continue
        listed: str = ", ".join(fields) if fields else "(none)"
        hints[label] = f"Valid fields of {label}: {listed}." if label else f"Valid parameters: {listed}."
    return list(hints.values())


def describe_validation_error(tool_name: str, exc: ValidationError, schema: JsonObject) -> str:
    """What the model reads when its arguments are refused: each problem, then the fields that each object with a
    problem accepts, so a wrong name inside a nested step is answered with the names of a step. Arguments are checked
    before anything runs, so the message says that no step of a refused batch was done."""
    problems: list[str] = []
    locations: list[ErrorPath] = []
    for error in exc.errors():
        locations.append(error["loc"])
        problems.append(f"{'.'.join(str(part) for part in error['loc']) or '(arguments)'}: {error['msg']}")
    return " ".join(
        [
            f"Invalid arguments for {tool_name}: {'; '.join(problems)}.",
            *_valid_field_hints(locations, schema),
            "Nothing was run: send the whole call again, corrected.",
        ]
    )


def _build[A: ToolArgs](
    name: str,
    description: str,
    args_model: type[A],
    modes: frozenset[RunMode],
    parallel_safe: bool,
    timeout_s: float,
    classify: Callable[[A, ToolContext], Awaitable[ActionInfo]],
    run: Callable[[A, ToolContext, ActionInfo], Awaitable[ToolResult]],
) -> RegisteredTool:
    schema: JsonObject = tool_parameters_schema(args_model)

    async def prepare(raw: JsonObject, ctx: ToolContext) -> PreparedCall:
        try:
            args: A = args_model.model_validate(raw)
        except ValidationError as exc:
            raise ToolInputError(describe_validation_error(name, exc, schema), {"tool": name, "received": raw}) from exc
        action: ActionInfo = await classify(args, ctx)

        async def execute(context: ToolContext) -> ToolResult:
            return await run(args, context, action)

        return PreparedCall(action=action, run=execute)

    return RegisteredTool(
        spec=ToolSpec(name=name, description=description, parameters=schema),
        modes=modes,
        parallel_safe=parallel_safe,
        timeout_s=timeout_s,
        prepare=prepare,
    )


def make_tool[A: ToolArgs](
    name: str,
    description: str,
    args_model: type[A],
    modes: frozenset[RunMode],
    parallel_safe: bool,
    timeout_s: float,
    classify: Callable[[A, ToolContext], ActionInfo],
    run: Callable[[A, ToolContext], Awaitable[ToolResult]],
) -> RegisteredTool:
    """A tool whose action can be classified from its arguments alone."""

    async def classify_now(args: A, ctx: ToolContext) -> ActionInfo:
        return classify(args, ctx)

    async def run_authorized(args: A, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        return await run(args, ctx)

    return _build(name, description, args_model, modes, parallel_safe, timeout_s, classify_now, run_authorized)


def _described(action: ActionInfo) -> JsonObject:
    flags: list[JsonValue] = list(action.flags)
    return {"summary": action.summary, "flags": flags}


def make_path_tool[A: ToolArgs](
    name: str,
    description: str,
    args_model: type[A],
    modes: frozenset[RunMode],
    parallel_safe: bool,
    timeout_s: float,
    classify: Callable[[A, ToolContext], ActionInfo],
    run: Callable[[A, ToolContext, ActionInfo], Awaitable[ToolResult]],
) -> RegisteredTool:
    """A tool whose action is classified from its arguments and the files they name as they are on disk. It is
    classified again as it starts, with no wait between that and ``run``, and refused when that differs from the action
    that was authorised: a link moved while the call waited for its approval, or a path that became one, would have it
    work on a file it was not allowed to. ``run`` receives the action that was authorised, so that what it acts on is
    what was checked (its ``target``, or the ``bundle`` it named) and not a path resolved again."""

    async def classify_now(args: A, ctx: ToolContext) -> ActionInfo:
        return classify(args, ctx)

    async def run_authorized(args: A, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        now: ActionInfo = classify(args, ctx)
        if now != authorized:
            raise ToolExecutionError(
                "the call was not run: what it names changed while it waited (a path leads elsewhere now, or the page "
                "is another); call the tool again",
                {"tool": name, "authorized": _described(authorized), "now": _described(now)},
            )
        return await run(args, ctx, authorized)

    return _build(name, description, args_model, modes, parallel_safe, timeout_s, classify_now, run_authorized)


def make_live_tool[A: ToolArgs](
    name: str,
    description: str,
    args_model: type[A],
    modes: frozenset[RunMode],
    parallel_safe: bool,
    timeout_s: float,
    classify: Callable[[A, ToolContext], Awaitable[ActionInfo]],
    run: Callable[[A, ToolContext], Awaitable[ToolResult]],
) -> RegisteredTool:
    """A tool that must inspect live state (a page, a window) to classify what it is about to do."""

    async def run_authorized(args: A, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        return await run(args, ctx)

    return _build(name, description, args_model, modes, parallel_safe, timeout_s, classify, run_authorized)


def make_reviewed_tool[A: ToolArgs](
    name: str,
    description: str,
    args_model: type[A],
    modes: frozenset[RunMode],
    parallel_safe: bool,
    timeout_s: float,
    classify: Callable[[A, ToolContext], Awaitable[ActionInfo]],
    run: Callable[[A, ToolContext, ActionInfo], Awaitable[ToolResult]],
) -> RegisteredTool:
    """A tool that looks at the screen again before each of its steps. ``run`` receives the action that was authorised
    up front and asks ``ctx.gate`` again for any step that turns out to be riskier than that."""
    return _build(name, description, args_model, modes, parallel_safe, timeout_s, classify, run)

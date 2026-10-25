"""The MCP servers of the evals: stdio servers started as ``python -m dotagent.evals.tool_server [NAME]``, by the tasks
that bring them. Importing this module starts nothing.

With no name, the server of item 167, :data:`SERVER_NAME`, whose tool makes a task of the catalog short, so that an eval
can tell whether a run takes the easy way. With the name of a stand-in (:data:`STAND_INS`), one of the servers of the
choice tasks (item 659): a server whose one tool answers from a fixed table, started by a task under a name that tells
little (``ab-tools``) beside a decoy. What it is for is said by the server itself (its ``instructions`` or the
descriptions of its tool), or by its owner in the task's ``[[mcp]]`` table. Its tool is named ``get``, like the decoy's,
or after what the decoy's name fits better (``convert``), so that a run picks the group to turn on by the line of the
groups and not by the names of the tools.
"""

import sys
from datetime import date, timedelta
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from dotagent.core.types import FrozenModel

SERVER_NAME: str = "evals"
WORKING_DAYS: str = "working_days"
# The holidays of the company of the working-days task; its holidays.txt lists the same days.
HOLIDAYS: tuple[str, ...] = (
    "2026-01-01",
    "2026-01-06",
    "2026-03-20",
    "2026-04-03",
    "2026-04-06",
    "2026-05-01",
    "2026-05-14",
    "2026-06-19",
    "2026-12-24",
    "2026-12-25",
)


def working_days(first: str, last: str) -> int:
    """Count the working days of the company from first to last, both included (dates as YYYY-MM-DD): Monday to
    Friday, not one of the company's holidays. A last day before the first is an error."""
    start: date = date.fromisoformat(first)
    end: date = date.fromisoformat(last)
    if end < start:
        raise ValueError(f"the last day {last} is before the first day {first}")
    off: frozenset[date] = frozenset(date.fromisoformat(day) for day in HOLIDAYS)
    days: list[date] = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    return sum(1 for day in days if day.weekday() < 5 and day not in off)


def build_server() -> MCPServer:
    """The server, offering :func:`working_days` as a tool that only reads."""
    server: MCPServer = MCPServer("dot-evals")
    server.add_tool(
        working_days, name=WORKING_DAYS, annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False)
    )
    return server


class StandIn(FrozenModel):
    """A stand-in server of the choice tasks (item 659): one tool, named ``tool`` and described by ``about``, that takes
    a ``key`` (written as ``key_help`` says) and answers from ``answers``, or says how a key looks. ``instructions`` are
    what the server says of itself when it connects, ``None`` for a server that says nothing, whose owner or whose tools
    say what it is for."""

    name: str
    instructions: str | None
    tool: str
    about: str
    key_help: str
    answers: dict[str, str]


# The bank's rates against a unit converter whose tool is called what a currency question fits: the model that picks by
# the names of the tools picks the converter, the one that reads what each server says of itself the rates.
_RATES: StandIn = StandIn(
    name="rates",
    instructions="The bank's own exchange rates: the fixed rate of each currency pair on each day of March 2026.",
    tool="lookup",
    about="Look up one entry.",
    key_help="a day and a currency pair, like '2026-03-02 EUR/TRY'",
    answers={
        "2026-03-02 EUR/TRY": "38.6125",
        "2026-03-03 EUR/TRY": "38.7040",
        "2026-03-02 USD/TRY": "35.2210",
        "2026-03-02 EUR/USD": "1.0962",
    },
)
_UNITS: StandIn = StandIn(
    name="units",
    instructions="Converts lengths, weights and temperatures between metric and imperial units.",
    tool="convert",
    about="Convert a value to another unit.",
    key_help="a value, its unit and the unit to convert to, like '10 km to mi'",
    answers={"10 km to mi": "6.21371", "5 kg to lb": "11.0231", "20 C to F": "68"},
)
# Two servers that say nothing, and tools that say as little: only the words of their owner tell them apart. The
# decoy's words share one with the question (physics).
_TIMETABLE: StandIn = StandIn(
    name="timetable",
    instructions=None,
    tool="get",
    about="Fetch one entry by its key.",
    key_help="a class, a weekday and a subject, like '7B mon maths'",
    answers={"7B wed physics": "Room 214", "7B mon maths": "Room 118", "7A wed physics": "Room 216"},
)
_FREEZERS: StandIn = StandIn(
    name="freezers",
    instructions=None,
    tool="get",
    about="Fetch one entry by its key.",
    key_help="a freezer and a day, like 'F2 2026-03-04'",
    answers={"F2 2026-03-04": "minus 79 C, door opened 3 times", "F1 2026-03-04": "minus 81 C, door opened once"},
)
# Two servers that say nothing of themselves, whose tools do: a run that reads the first sentences of their tools
# knows which is which.
_FORECAST: StandIn = StandIn(
    name="forecast",
    instructions=None,
    tool="get",
    about="Gives the weather forecast that the Turkish State Meteorological Service published for a city on a day.",
    key_help="a city and a day, like 'Izmir 2026-03-05'",
    answers={
        "Izmir 2026-03-05": "rain, 11 to 16 C, wind from the north at 20 km/h",
        "Ankara 2026-03-05": "cloudy, 2 to 9 C, wind from the east at 10 km/h",
    },
)
_PIPELINES: StandIn = StandIn(
    name="pipelines",
    instructions=None,
    tool="get",
    about="Gives the build status of a continuous-integration pipeline of a repository.",
    key_help="a repository and a branch, like 'dot main'",
    answers={"dot main": "passing, last build 12 minutes ago", "dot release": "failing since 2026-03-04"},
)
STAND_INS: tuple[StandIn, ...] = (_RATES, _UNITS, _TIMETABLE, _FREEZERS, _FORECAST, _PIPELINES)


def stand_in(name: str) -> StandIn:
    """The stand-in called ``name``; a name that is none names the ones there are."""
    for one in STAND_INS:
        if one.name == name:
            return one
    raise ValueError(f"no stand-in server called {name!r}; there are {', '.join(one.name for one in STAND_INS)}")


def build_stand_in(one: StandIn) -> MCPServer:
    """The server of a stand-in: its words as ``instructions``, its one tool, which only reads."""
    server: MCPServer = MCPServer(f"dot-evals-{one.name}", instructions=one.instructions)

    def answer(key: Annotated[str, Field(description=one.key_help)]) -> str:
        found: str | None = one.answers.get(key)
        if found is None:
            raise ValueError(f"no entry {key!r}; a key is {one.key_help}")
        return found

    server.add_tool(
        answer,
        name=one.tool,
        description=one.about,
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    return server


def main(arguments: list[str]) -> None:
    """Serve over stdio: the stand-in ``arguments`` name, or the server of item 167 when they name none."""
    (build_server() if not arguments else build_stand_in(stand_in(arguments[0]))).run("stdio")


if __name__ == "__main__":
    main(sys.argv[1:])

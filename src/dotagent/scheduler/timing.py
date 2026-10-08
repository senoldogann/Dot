"""When schedules fire: cron expressions and one-time moments read in one time zone, as pure functions."""

import itertools
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, CroniterError, croniter

from dotagent.core.errors import ConfigError, ToolInputError
from dotagent.core.types import FrozenModel

_CRON_FIELDS: int = 5
# Firings that the shortest-gap check looks at: more than a whole day of a five-minute schedule, and every sparse
# schedule far enough to see all the times of one matching day.
_GAP_SAMPLE: int = 600
_LOCALTIME: str = "/etc/localtime"
_ZONEINFO_MARK: str = "zoneinfo/"
_STAMP_FORMAT: str = "%a %Y-%m-%d %H:%M"


class Timing(FrozenModel):
    """How a schedule fires: ``cron`` for a recurring one or ``run_at`` for a single moment (exactly one is set), and
    when it fires first."""

    cron: str | None
    run_at: float | None
    first_run: float


def _system_zone_name() -> str:
    target: str = os.path.realpath(_LOCALTIME)
    _, mark, name = target.partition(_ZONEINFO_MARK)
    if mark == "" or name == "":
        raise ConfigError("cannot tell the time zone of this Mac; set [scheduler] timezone", {"localtime": target})
    return name


def named_zone(name: str) -> ZoneInfo:
    """The time zone called ``name`` (``Europe/Istanbul``); an unknown name is the caller's mistake.

    Reads the zone database from disk, so call it off the event loop.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolInputError("unknown time zone; use a name such as Europe/Istanbul", {"timezone": name}) from exc


def resolve_zone(name: str) -> ZoneInfo:
    """The zone that schedules are read in: ``name``, or the zone this Mac is set to when ``name`` is empty.

    Reads the zone database from disk, so call it off the event loop.
    """
    zone_name: str = name if name != "" else _system_zone_name()
    try:
        return ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("unknown time zone", {"timezone": zone_name, "configured": name}) from exc


def format_local(moment: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(moment, tz=zone).strftime(_STAMP_FORMAT)


def firings(cron: str, after: float, zone: ZoneInfo, count: int) -> list[float]:
    """Up to ``count`` firings of ``cron`` strictly after ``after``, earliest first; fewer when the expression runs out.

    A time of day that is given exactly fires once: the second pass through the hour that repeats when the clocks go
    back is skipped, unless the minute or hour field is a wildcard (``*``, ``*/5``), as in Vixie cron. A time that the
    clocks jump over fires right after the gap.
    """
    wildcard: bool = any(field.startswith("*") for field in cron.split()[:2])
    try:
        cursor: croniter = croniter(cron, datetime.fromtimestamp(after, tz=zone))
    except CroniterError as exc:
        raise ToolInputError("not a valid cron expression", {"cron": cron, "error": str(exc)}) from exc
    found: list[float] = []
    while len(found) < count:
        try:
            moment: datetime = cursor.get_next(datetime)
        except CroniterBadDateError:
            break
        if moment.fold == 0 or wildcard:
            found.append(moment.timestamp())
    return found


def next_firing(cron: str, after: float, zone: ZoneInfo) -> float | None:
    """The first firing of ``cron`` strictly after ``after``; ``None`` when the expression has no more."""
    found: list[float] = firings(cron, after, zone, 1)
    return found[0] if found else None


def plan_cron(cron: str, zone: ZoneInfo, min_interval_s: int, now: float) -> Timing:
    fields: list[str] = cron.split()
    if len(fields) != _CRON_FIELDS:
        raise ToolInputError(
            "a cron expression has exactly five fields: minute hour day-of-month month day-of-week", {"cron": cron}
        )
    expression: str = " ".join(fields)
    sample: list[float] = firings(expression, now, zone, _GAP_SAMPLE)
    if not sample:
        raise ToolInputError("this cron expression never fires", {"cron": expression})
    shortest: float = min((later - earlier for earlier, later in itertools.pairwise(sample)), default=float("inf"))
    if shortest < min_interval_s:
        raise ToolInputError(
            "this cron expression fires too often",
            {"cron": expression, "shortest_gap_s": shortest, "minimum_s": min_interval_s},
        )
    return Timing(cron=expression, run_at=None, first_run=sample[0])


def local_moment(at: str, zone: ZoneInfo) -> float:
    """The moment an ISO 8601 date and time names; without an offset it is read in ``zone``."""
    try:
        parsed: datetime = datetime.fromisoformat(at.strip())
    except ValueError as exc:
        raise ToolInputError(
            "'at' is not an ISO 8601 date and time such as 2026-10-06T09:30", {"at": at, "error": str(exc)}
        ) from exc
    local: datetime = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=zone)
    moment: float = local.timestamp()
    if parsed.tzinfo is None and datetime.fromtimestamp(moment, tz=zone).replace(tzinfo=None) != parsed:
        raise ToolInputError(
            "that local time does not exist: the clocks jump over it", {"at": at, "timezone": zone.key}
        )
    return moment


def plan_once(at: str, zone: ZoneInfo, now: float) -> Timing:
    moment: float = local_moment(at, zone)
    if moment <= now:
        raise ToolInputError("'at' is not in the future", {"at": at, "now": format_local(now, zone)})
    return Timing(cron=None, run_at=moment, first_run=moment)


def plan_timing(cron: str | None, at: str | None, zone: ZoneInfo, min_interval_s: int, now: float) -> Timing:
    """Check how a new schedule should fire: ``cron`` recurring or ``at`` once, never both and never neither."""
    if cron is not None and at is not None:
        raise ToolInputError("give either cron or at, not both", {"cron": cron, "at": at})
    if cron is not None:
        return plan_cron(cron, zone, min_interval_s, now)
    if at is not None:
        return plan_once(at, zone, now)
    raise ToolInputError("give cron for a recurring schedule or at for a single run", {})


def describe_timing(timing: Timing, zone: ZoneInfo) -> str:
    if timing.cron is not None:
        return f"cron {timing.cron}, first run {format_local(timing.first_run, zone)}"
    return f"once, {format_local(timing.first_run, zone)}"

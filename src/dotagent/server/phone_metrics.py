"""A phone's own performance record (item 1926): what iOS's MetricKit measured of the Dot app each day (CPU time, hangs,
launch, memory, disk writes, network, the crash and hang reports) and the app's own counters of each time it was open
(the bytes it read and sent, its requests, the streams it opened and those that broke, how often it redrew a chat).
The phone sends them with its own key; the daemon keeps the newest of each phone in its store and answers a short
summary of the last days, which the app shows in Settings › Maintenance. Nothing leaves the Mac: no third party hears
any of it.

Only numbers are kept: no chat's words, no path, no stack trace of a crash (the app sends a report's count, not its
contents)."""

import time
from typing import Literal

from pydantic import Field, StrictFloat, StrictInt, ValidationError

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store

log = get_logger(__name__)

# The kv row of a phone's reports, by its id.
_KEY_PREFIX: str = "phone.metrics."
# The most reports a phone keeps, the newest; and how old one may be.
MOST_REPORTS: int = 300
KEPT_DAYS: int = 30
# The days the summary covers.
SUMMARY_DAYS: int = 7
# The most reports one request carries.
MOST_PER_REQUEST: int = 50

_DAY_S: float = 24 * 3600
_MB: float = 1024 * 1024


class PhoneReport(FrozenModel):
    """One report of the phone's: ``metrickit`` (a day of iOS's measures, with the diagnostics' counts) or ``app`` (one
    time the app was open, its own counters). A measure a report does not carry is ``None``."""

    source: Literal["metrickit", "app"]
    begins_at: StrictFloat = Field(ge=0)
    ends_at: StrictFloat = Field(ge=0)
    app_version: str = Field(max_length=40)
    os_version: str = Field(max_length=40)
    # MetricKit's.
    foreground_s: StrictFloat | None = Field(default=None, ge=0)
    background_s: StrictFloat | None = Field(default=None, ge=0)
    cpu_s: StrictFloat | None = Field(default=None, ge=0)
    gpu_s: StrictFloat | None = Field(default=None, ge=0)
    hang_s: StrictFloat | None = Field(default=None, ge=0)
    launch_ms: StrictFloat | None = Field(default=None, ge=0)
    peak_memory_mb: StrictFloat | None = Field(default=None, ge=0)
    disk_writes_mb: StrictFloat | None = Field(default=None, ge=0)
    cellular_mb: StrictFloat | None = Field(default=None, ge=0)
    wifi_mb: StrictFloat | None = Field(default=None, ge=0)
    scroll_hitch_ms_per_s: StrictFloat | None = Field(default=None, ge=0)
    crashes: StrictInt | None = Field(default=None, ge=0)
    hang_reports: StrictInt | None = Field(default=None, ge=0)
    cpu_exceptions: StrictInt | None = Field(default=None, ge=0)
    disk_write_exceptions: StrictInt | None = Field(default=None, ge=0)
    # The app's own.
    open_s: StrictFloat | None = Field(default=None, ge=0)
    bytes_in: StrictInt | None = Field(default=None, ge=0)
    bytes_out: StrictInt | None = Field(default=None, ge=0)
    requests: StrictInt | None = Field(default=None, ge=0)
    stream_opens: StrictInt | None = Field(default=None, ge=0)
    stream_breaks: StrictInt | None = Field(default=None, ge=0)
    redraws: StrictInt | None = Field(default=None, ge=0)


class PhoneReports(FrozenModel):
    """What ``POST /api/phone/metrics`` takes: the reports the phone held, oldest first."""

    reports: list[PhoneReport] = Field(min_length=1, max_length=MOST_PER_REQUEST)


class PhoneMetricsSummary(FrozenModel):
    """The last :data:`SUMMARY_DAYS` days of a phone's reports, as Settings › Maintenance shows them. ``reports`` is
    ``0`` and every measure ``None`` or ``0`` before the first report."""

    days: int
    reports: int
    last_at: float | None
    app_version: str | None
    # MetricKit's, summed over the days (the launch the newest, the memory the highest).
    foreground_min: float
    cpu_s: float
    hang_s: float
    launch_ms: float | None
    peak_memory_mb: float | None
    disk_writes_mb: float
    cellular_mb: float
    wifi_mb: float
    crashes: int
    hang_reports: int
    cpu_exceptions: int
    disk_write_exceptions: int
    # The app's own, summed over the times it was open.
    open_min: float
    dot_in_mb: float
    dot_out_mb: float
    requests: int
    reconnects: int
    redraws_per_min: float | None


def _key(phone_id: str) -> str:
    return f"{_KEY_PREFIX}{phone_id}"


class _Kept(FrozenModel):
    reports: list[PhoneReport]


async def reports_of(store: Store, phone_id: str) -> list[PhoneReport]:
    """The reports kept for ``phone_id``, oldest first; none for a phone that sent none. A row that cannot be read is
    logged and read as none: the next report starts it again."""
    kept: str | None = await store.kv_get(_key(phone_id))
    if kept is None:
        return []
    try:
        return _Kept.model_validate_json(kept).reports
    except ValidationError as error:
        log.warning("phone_metrics_unreadable", phone=phone_id, error=str(error))
        return []


def kept_reports(held: list[PhoneReport], new: list[PhoneReport], now: float) -> list[PhoneReport]:
    """``held`` with ``new`` after it, oldest first: none older than :data:`KEPT_DAYS` days, at most
    :data:`MOST_REPORTS`, the newest kept. Pure."""
    oldest: float = now - KEPT_DAYS * _DAY_S
    every: list[PhoneReport] = sorted((*held, *new), key=lambda report: report.ends_at)
    return [report for report in every if report.ends_at >= oldest][-MOST_REPORTS:]


async def keep_reports(store: Store, phone_id: str, reports: list[PhoneReport]) -> int:
    """Add ``reports`` to those kept for ``phone_id``; how many are kept now. The caller holds the phone's lock."""
    kept: list[PhoneReport] = kept_reports(await reports_of(store, phone_id), reports, time.time())
    await store.kv_set(_key(phone_id), _Kept(reports=kept).model_dump_json())
    return len(kept)


async def forget_reports(store: Store, phone_id: str) -> None:
    """Forget the reports of ``phone_id``: the phone was removed."""
    await store.kv_delete(_key(phone_id))


def _total(values: list[float | None]) -> float:
    return sum(value for value in values if value is not None)


def _count(values: list[int | None]) -> int:
    return sum(value for value in values if value is not None)


def summary_of(reports: list[PhoneReport], now: float) -> PhoneMetricsSummary:
    """The summary of the reports that ended in the last :data:`SUMMARY_DAYS` days. Pure."""
    recent: list[PhoneReport] = [report for report in reports if report.ends_at >= now - SUMMARY_DAYS * _DAY_S]
    days: list[PhoneReport] = [report for report in recent if report.source == "metrickit"]
    opens: list[PhoneReport] = [report for report in recent if report.source == "app"]
    newest: PhoneReport | None = max(recent, key=lambda report: report.ends_at) if recent else None
    launches: list[PhoneReport] = sorted(
        (report for report in days if report.launch_ms is not None), key=lambda report: report.ends_at
    )
    memories: list[float] = [report.peak_memory_mb for report in days if report.peak_memory_mb is not None]
    open_s: float = _total([report.open_s for report in opens])
    redrawn: list[PhoneReport] = [report for report in opens if report.redraws is not None and report.open_s]
    redraw_s: float = _total([report.open_s for report in redrawn])
    return PhoneMetricsSummary(
        days=SUMMARY_DAYS,
        reports=len(recent),
        last_at=newest.ends_at if newest is not None else None,
        app_version=newest.app_version if newest is not None else None,
        foreground_min=_total([report.foreground_s for report in days]) / 60,
        cpu_s=_total([report.cpu_s for report in days]),
        hang_s=_total([report.hang_s for report in days]),
        launch_ms=launches[-1].launch_ms if launches else None,
        peak_memory_mb=max(memories) if memories else None,
        disk_writes_mb=_total([report.disk_writes_mb for report in days]),
        cellular_mb=_total([report.cellular_mb for report in days]),
        wifi_mb=_total([report.wifi_mb for report in days]),
        crashes=_count([report.crashes for report in days]),
        hang_reports=_count([report.hang_reports for report in days]),
        cpu_exceptions=_count([report.cpu_exceptions for report in days]),
        disk_write_exceptions=_count([report.disk_write_exceptions for report in days]),
        open_min=open_s / 60,
        dot_in_mb=_count([report.bytes_in for report in opens]) / _MB,
        dot_out_mb=_count([report.bytes_out for report in opens]) / _MB,
        requests=_count([report.requests for report in opens]),
        reconnects=_count([report.stream_breaks for report in opens]),
        redraws_per_min=_count([report.redraws for report in redrawn]) / (redraw_s / 60) if redraw_s > 0 else None,
    )

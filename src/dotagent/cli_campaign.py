"""``dotx campaign``: see the campaigns, stop one, let one go on or mark one achieved (see ``agent.campaign_control``).

These read and change the store alone, so the daemon need not run; it is the daemon that runs the sessions.
"""

import sys
import time
from zoneinfo import ZoneInfo

from dotagent.agent.campaign import entry_line
from dotagent.agent.campaign_control import mark_achieved, resume_campaign, stop_campaign
from dotagent.cli_commands import (
    CampaignAchievedCommand,
    CampaignListCommand,
    CampaignResumeCommand,
    CampaignShowCommand,
    CampaignStopCommand,
    Command,
)
from dotagent.cli_schedule import local_zone, store_alone
from dotagent.config import load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.scheduler.timing import format_local
from dotagent.store.models import WORKING_STATUSES, CampaignEntry, CampaignRecord, RunRecord

CAMPAIGN_DAEMON_NOTE: str = "The Dot daemon runs the sessions of a campaign, only while it is running (dotx daemon)."
# How many of the newest entries of its ledger 'dotx campaign show' prints.
_SHOWN_ENTRIES: int = 20
_WORKING_NOTE: str = "Its session that is working goes on until it ends; Stop on the session's page stops it."


def render_campaign(campaign: CampaignRecord, sessions: list[RunRecord], zone: ZoneInfo) -> str:
    """One line: id, title, where the campaign stands and how many sessions it ran."""
    state: str = campaign.status
    if campaign.status == "active" and any(session.status in WORKING_STATUSES for session in sessions):
        state = "active, a session is working"
    elif campaign.status == "active" and campaign.next_session_at is not None:
        state = f"active, next session {format_local(campaign.next_session_at, zone)}"
    noun: str = "session" if len(sessions) == 1 else "sessions"
    return f'{campaign.id} "{campaign.title}" · {state} · {len(sessions)} {noun}'


def render_campaign_page(
    campaign: CampaignRecord,
    sessions: list[RunRecord],
    entries: list[CampaignEntry],
    tokens: int,
    zone: ZoneInfo,
    now: float,
) -> str:
    """What 'dotx campaign show' prints: the campaign's line, goal, targets, sessions and the end of its ledger."""
    chrome: str = "the daemon's" if campaign.browser is None else campaign.browser
    lines: list[str] = [render_campaign(campaign, sessions, zone), f"goal: {campaign.goal}", f"browser: {chrome}"]
    if campaign.targets:
        lines += [f"targets: {campaign.targets}", f"verified by: {campaign.verification}"]
    else:
        lines.append("targets: not set yet")
    if campaign.status == "achieved":
        lines.append(f"evidence: {campaign.evidence}")
    lines.append(f"tokens: {tokens}")
    lines += [
        f"session {number}: {session.id} · {session.status} · started {format_local(session.created_at, zone)}"
        for number, session in enumerate(sessions, start=1)
    ]
    if entries:
        lines.append("ledger, the newest last:")
        lines += [entry_line(entry, now) for entry in entries]
    else:
        lines.append("ledger: empty")
    return "\n".join(lines)


def render_first_session_end(campaign: CampaignRecord) -> str:
    """What 'dotx run --standing' says of the campaign once its first session ended."""
    if campaign.status == "active":
        return f"The campaign goes on: 'dotx campaign show {campaign.id}'. {CAMPAIGN_DAEMON_NOTE}"
    return f"The campaign is {campaign.status}: 'dotx campaign show {campaign.id}'."


async def cmd_campaign_list(command: CampaignListCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        campaigns: list[CampaignRecord] = await store.list_campaigns()
        for campaign in campaigns:
            sys.stdout.write(f"- {render_campaign(campaign, await store.campaign_sessions(campaign.id), zone)}\n")
    noun: str = "campaign" if len(campaigns) == 1 else "campaigns"
    sys.stderr.write(f"{len(campaigns)} {noun}, times in {zone.key}. {CAMPAIGN_DAEMON_NOTE}\n")
    return 0


async def cmd_campaign_show(command: CampaignShowCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        campaign: CampaignRecord = await store.get_campaign(command.campaign_id)
        page: str = render_campaign_page(
            campaign,
            await store.campaign_sessions(campaign.id),
            await store.campaign_entries(campaign.id, _SHOWN_ENTRIES),
            await store.campaign_tokens(campaign.id),
            zone,
            time.time(),
        )
    sys.stdout.write(f"{page}\n")
    return 0


async def cmd_campaign_stop(command: CampaignStopCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        campaign: CampaignRecord = await stop_campaign(store, command.campaign_id)
        sessions: list[RunRecord] = await store.campaign_sessions(campaign.id)
    sys.stdout.write(f"Stopped: {render_campaign(campaign, sessions, zone)}\n")
    if any(session.status in WORKING_STATUSES for session in sessions):
        sys.stderr.write(f"{_WORKING_NOTE}\n")
    return 0


async def cmd_campaign_resume(command: CampaignResumeCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        campaign: CampaignRecord = await resume_campaign(store, command.campaign_id, time.time())
        sessions: list[RunRecord] = await store.campaign_sessions(campaign.id)
    sys.stdout.write(f"Going on: {render_campaign(campaign, sessions, zone)}\n")
    sys.stderr.write(f"{CAMPAIGN_DAEMON_NOTE}\n")
    return 0


async def cmd_campaign_achieved(command: CampaignAchievedCommand) -> int:
    zone: ZoneInfo = await local_zone(load_user_config())
    async with store_alone() as store:
        campaign: CampaignRecord = await mark_achieved(store, command.campaign_id)
        sessions: list[RunRecord] = await store.campaign_sessions(campaign.id)
    sys.stdout.write(f"Marked achieved: {render_campaign(campaign, sessions, zone)}\n")
    if any(session.status in WORKING_STATUSES for session in sessions):
        sys.stderr.write(f"{_WORKING_NOTE}\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case CampaignListCommand():
            return await cmd_campaign_list(command)
        case CampaignShowCommand():
            return await cmd_campaign_show(command)
        case CampaignStopCommand():
            return await cmd_campaign_stop(command)
        case CampaignResumeCommand():
            return await cmd_campaign_resume(command)
        case CampaignAchievedCommand():
            return await cmd_campaign_achieved(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})

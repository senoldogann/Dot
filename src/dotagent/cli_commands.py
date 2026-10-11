"""Every ``dotx`` command as data: one model per subcommand, with the fields its parser fills, and the union the
parser validates into. This module stays light (pydantic and the type aliases only), so that ``dotx --help`` and the
light commands start without the modules that carry the commands out (:mod:`dotagent.cli` loads those by name)."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from dotagent.core.types import FrozenModel
from dotagent.desktop.types import SnapshotScope
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode
from dotagent.store.models import BrowserMode


class RunCommand(FrozenModel):
    name: Literal["run"]
    goal: list[str]
    mode: Literal["task", "research"]
    model: str | None
    effort: Effort | None
    approvals: ApprovalMode | None
    goal_mode: bool | None
    standing: bool
    browser: BrowserMode | None
    thread: str | None
    no_input: bool
    thinking: bool
    verbose: bool


class ResumeCommand(FrozenModel):
    name: Literal["resume"]
    run_id: str
    no_input: bool
    thinking: bool
    verbose: bool


class ChatCommand(FrozenModel):
    name: Literal["chat"]
    model: str | None
    effort: Effort | None
    approvals: ApprovalMode | None
    thread: str | None
    thinking: bool
    verbose: bool


class ModelsCommand(FrozenModel):
    name: Literal["models"]
    provider: str | None


class AuthSetCommand(FrozenModel):
    name: Literal["auth-set"]
    key: str


class AuthStatusCommand(FrozenModel):
    name: Literal["auth-status"]


class DoctorCommand(FrozenModel):
    name: Literal["doctor"]


class ReflectCommand(FrozenModel):
    name: Literal["reflect"]


class BrowserLoginCommand(FrozenModel):
    name: Literal["browser-login"]
    url: str | None


class DesktopCheckCommand(FrozenModel):
    name: Literal["desktop-check"]


class DesktopSetupCommand(FrozenModel):
    name: Literal["desktop-setup"]


class DesktopAppsCommand(FrozenModel):
    name: Literal["desktop-apps"]


class DesktopSnapshotCommand(FrozenModel):
    name: Literal["desktop-snapshot"]
    app: str | None
    scope: SnapshotScope
    query: str | None


class DesktopScreenshotCommand(FrozenModel):
    name: Literal["desktop-screenshot"]
    path: str


class MemoryListCommand(FrozenModel):
    name: Literal["memory-list"]
    limit: int = Field(ge=1)


class MemoryAddCommand(FrozenModel):
    name: Literal["memory-add"]
    text: list[str]
    tags: list[str]


class MemorySearchCommand(FrozenModel):
    name: Literal["memory-search"]
    query: list[str]
    limit: int = Field(ge=1)


class MemoryForgetCommand(FrozenModel):
    name: Literal["memory-forget"]
    memory_id: str


class MemoryReindexCommand(FrozenModel):
    name: Literal["memory-reindex"]


class BrowserImportChromeCommand(FrozenModel):
    name: Literal["browser-import-chrome"]
    profile: str
    replace: bool


class BrowserAttachCommand(FrozenModel):
    name: Literal["browser-attach"]


class BrowserDetachCommand(FrozenModel):
    name: Literal["browser-detach"]


class CampaignListCommand(FrozenModel):
    name: Literal["campaign-list"]


class CampaignShowCommand(FrozenModel):
    name: Literal["campaign-show"]
    campaign_id: str


class CampaignStopCommand(FrozenModel):
    name: Literal["campaign-stop"]
    campaign_id: str


class CampaignResumeCommand(FrozenModel):
    name: Literal["campaign-resume"]
    campaign_id: str


class CampaignAchievedCommand(FrozenModel):
    name: Literal["campaign-achieved"]
    campaign_id: str


class DaemonCommand(FrozenModel):
    name: Literal["daemon"]


class OpenCommand(FrozenModel):
    name: Literal["open"]


class UrlCommand(FrozenModel):
    name: Literal["url"]


class ServiceInstallCommand(FrozenModel):
    name: Literal["service-install"]


class ServiceUninstallCommand(FrozenModel):
    name: Literal["service-uninstall"]


class ServiceStatusCommand(FrozenModel):
    name: Literal["service-status"]


class ServicePermissionsCommand(FrozenModel):
    name: Literal["service-permissions"]


class EvalListCommand(FrozenModel):
    name: Literal["eval-list"]


class EvalCheckCommand(FrozenModel):
    name: Literal["eval-check"]


class EvalRunCommand(FrozenModel):
    name: Literal["eval-run"]
    model: str | None
    tasks: tuple[str, ...]
    repeat: int = Field(ge=1)


class EvalReportCommand(FrozenModel):
    name: Literal["eval-report"]
    files: tuple[str, ...]


class McpListCommand(FrozenModel):
    name: Literal["mcp-list"]


class PlaybooksListCommand(FrozenModel):
    name: Literal["playbooks-list"]


class PlaybooksShowCommand(FrozenModel):
    name: Literal["playbooks-show"]
    playbook: str


class PlaybooksDropCommand(FrozenModel):
    name: Literal["playbooks-drop"]
    playbook: str


class ModelsProfileCommand(FrozenModel):
    name: Literal["models-profile"]
    model: str | None


class ScheduleListCommand(FrozenModel):
    name: Literal["schedule-list"]


class ScheduleAddCommand(FrozenModel):
    name: Literal["schedule-add"]
    title: str = Field(min_length=1, max_length=80, pattern=r"\S")
    cron: str | None
    at: str | None
    mode: Literal["task", "research"]
    goal: list[str] = Field(min_length=1)


class ScheduleDeleteCommand(FrozenModel):
    name: Literal["schedule-delete"]
    schedule_id: str


class ScheduleEnableCommand(FrozenModel):
    name: Literal["schedule-enable"]
    schedule_id: str


class ScheduleDisableCommand(FrozenModel):
    name: Literal["schedule-disable"]
    schedule_id: str


class SkillsListCommand(FrozenModel):
    name: Literal["skills-list"]


class StatsCommand(FrozenModel):
    name: Literal["stats"]
    days: int = Field(ge=1)
    by: Literal["model", "site", "all"]
    run: str | None


class UndoCommand(FrozenModel):
    name: Literal["undo"]
    run_id: str
    apply: bool
    force: bool


class PruneCommand(FrozenModel):
    name: Literal["prune"]
    apply: bool


type Command = Annotated[
    RunCommand
    | ResumeCommand
    | ChatCommand
    | ModelsCommand
    | ModelsProfileCommand
    | AuthSetCommand
    | AuthStatusCommand
    | DoctorCommand
    | ReflectCommand
    | BrowserLoginCommand
    | BrowserImportChromeCommand
    | BrowserAttachCommand
    | BrowserDetachCommand
    | DesktopCheckCommand
    | DesktopSetupCommand
    | DesktopAppsCommand
    | DesktopSnapshotCommand
    | DesktopScreenshotCommand
    | MemoryListCommand
    | MemoryAddCommand
    | MemorySearchCommand
    | MemoryForgetCommand
    | MemoryReindexCommand
    | ScheduleListCommand
    | ScheduleAddCommand
    | ScheduleDeleteCommand
    | ScheduleEnableCommand
    | ScheduleDisableCommand
    | CampaignListCommand
    | CampaignShowCommand
    | CampaignStopCommand
    | CampaignResumeCommand
    | CampaignAchievedCommand
    | McpListCommand
    | SkillsListCommand
    | PlaybooksListCommand
    | PlaybooksShowCommand
    | PlaybooksDropCommand
    | PruneCommand
    | UndoCommand
    | EvalListCommand
    | EvalCheckCommand
    | EvalRunCommand
    | EvalReportCommand
    | StatsCommand
    | DaemonCommand
    | OpenCommand
    | UrlCommand
    | ServiceInstallCommand
    | ServiceUninstallCommand
    | ServiceStatusCommand
    | ServicePermissionsCommand,
    Field(discriminator="name"),
]
COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)

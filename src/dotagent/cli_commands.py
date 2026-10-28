"""Every ``dotx`` command as data: one model per subcommand, with the fields its parser fills, and the union the
parser validates into. This module stays light (pydantic and the type aliases only), so that ``dotx --help`` and the
light commands start without the modules that carry the commands out (:mod:`dotagent.cli` loads those by name)."""

from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from dotagent.core.types import FrozenModel
from dotagent.desktop.types import SnapshotScope
from dotagent.llm.types import Effort
from dotagent.policy.types import ApprovalMode
from dotagent.store.models import BrowserMode, MemoryOrder


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


class SetupCommand(FrozenModel):
    name: Literal["setup"]
    model: str | None
    key_stdin: bool
    no_service: bool


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
    order: MemoryOrder = "changed"


class MemoryAddCommand(FrozenModel):
    name: Literal["memory-add"]
    text: list[str]
    tags: list[str]


class MemorySearchCommand(FrozenModel):
    name: Literal["memory-search"]
    query: list[str]
    limit: int = Field(ge=1)


class MemoryForgetCommand(FrozenModel):
    """One note by its id, or, with ``proposed``, every note proposed to be forgotten (item 584)."""

    name: Literal["memory-forget"]
    memory_id: str | None
    proposed: bool

    @model_validator(mode="after")
    def _one_or_the_proposed(self) -> Self:
        if (self.memory_id is not None) == self.proposed:
            raise ValueError("name one note by its id, or give --proposed for the notes proposed to be forgotten")
        return self


class MemoryProposalsCommand(FrozenModel):
    name: Literal["memory-proposals"]


class MemoryKeepCommand(FrozenModel):
    name: Literal["memory-keep"]
    memory_ids: list[str] = Field(min_length=1)


class MemoryTrashCommand(FrozenModel):
    name: Literal["memory-trash"]


class MemoryRestoreCommand(FrozenModel):
    name: Literal["memory-restore"]
    memory_id: str


class MemoryPurgeCommand(FrozenModel):
    name: Literal["memory-purge"]
    memory_id: str


class MemoryPinCommand(FrozenModel):
    """Pin a note, so that it never rests, or take the pin off (item 584)."""

    name: Literal["memory-pin", "memory-unpin"]
    memory_id: str


class MemoryUnmergeCommand(FrozenModel):
    name: Literal["memory-unmerge"]
    memory_id: str


class MemoryReindexCommand(FrozenModel):
    name: Literal["memory-reindex"]


class HarnessListCommand(FrozenModel):
    name: Literal["harness-list"]


class HarnessShowCommand(FrozenModel):
    name: Literal["harness-show"]
    version_id: str | None


class HarnessDiffCommand(FrozenModel):
    name: Literal["harness-diff"]
    version_id: str
    against: str | None


class HarnessSetCommand(FrozenModel):
    """A version of the owner's: ``habits`` and ``tool_guidance`` name files to read a component from (``-`` for
    standard input), ``None`` keeps the adopted version's text."""

    name: Literal["harness-set"]
    summary: str
    habits: str | None
    tool_guidance: str | None
    adopt: bool


class HarnessAdoptCommand(FrozenModel):
    """Adopt a version. One written from a run that read content third parties can author is adopted only after a yes
    (item 588): asked on the terminal, or ``yes`` given beforehand."""

    name: Literal["harness-adopt"]
    version_id: str
    yes: bool = False


class HarnessRetireCommand(FrozenModel):
    name: Literal["harness-retire"]
    version_id: str


class HarnessProposeCommand(FrozenModel):
    """A candidate from what one run did (item 390): ``scope`` is the proposer's, how the agent works or what a
    practitioner knows."""

    name: Literal["harness-propose"]
    run_id: str
    scope: Literal["invariant", "domain"]


class HarnessStepCommand(FrozenModel):
    """One step of the improvement loop now (item 391): from ``run_id``, or the run that went worst (item 720);
    ``scripted`` plays the references through the scripted model, a check of the step without a model's cost."""

    name: Literal["harness-step"]
    run_id: str | None
    scripted: bool


class HarnessProbeCommand(FrozenModel):
    """A probe in a process of its own (item 391): the ask read from the file ``ask``, the report printed as JSON."""

    name: Literal["harness-probe"]
    ask: str


class HarnessFinalCommand(FrozenModel):
    """The final set played once under the adopted version, or ``version_id`` (item 391)."""

    name: Literal["harness-final"]
    version_id: str | None


class HarnessGraphCommand(FrozenModel):
    """The provenance of the harness as a Mermaid flowchart (item 392), of the newest ``steps`` steps."""

    name: Literal["harness-graph"]
    steps: int = Field(ge=1)


class HarnessStepsCommand(FrozenModel):
    name: Literal["harness-steps"]
    limit: int = Field(ge=1)


class HarnessExperiencesCommand(FrozenModel):
    name: Literal["harness-experiences"]
    limit: int = Field(ge=1)


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
    launcher: str | None


class ServiceUninstallCommand(FrozenModel):
    name: Literal["service-uninstall"]


class ServiceStatusCommand(FrozenModel):
    name: Literal["service-status"]


class ServicePermissionsCommand(FrozenModel):
    name: Literal["service-permissions"]


class ServiceRestartCommand(FrozenModel):
    name: Literal["service-restart"]
    when_idle: bool


class EvalListCommand(FrozenModel):
    name: Literal["eval-list"]


class EvalCheckCommand(FrozenModel):
    name: Literal["eval-check"]


class EvalFromRunCommand(FrozenModel):
    name: Literal["eval-from-run"]
    run_id: str
    out: str | None


class EvalRunCommand(FrozenModel):
    name: Literal["eval-run"]
    model: str | None
    tasks: tuple[str, ...]
    repeat: int = Field(ge=1)
    no_mcp_purposes: bool


class EvalToolsCommand(FrozenModel):
    """`dotx eval tools` (item 736): the cases of the eval of tool use asked of ``model``, ``repeat`` times each."""

    name: Literal["eval-tools"]
    model: str | None
    cases: tuple[str, ...]
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


class PlaybooksTrustCommand(FrozenModel):
    name: Literal["playbooks-trust"]
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


class SkillsVersionsCommand(FrozenModel):
    """The versions of a learned skill (item 585)."""

    name: Literal["skills-versions"]
    skill: str


class SkillsRestoreCommand(FrozenModel):
    """Put a version of a learned skill back as its steps (item 585)."""

    name: Literal["skills-restore"]
    skill: str
    version: int = Field(ge=1)


class SkillsKeepCommand(FrozenModel):
    """Pin, unpin, put to rest or wake a learned skill (item 585)."""

    name: Literal["skills-pin", "skills-unpin", "skills-rest", "skills-wake"]
    skill: str


class SkillsProposalsCommand(FrozenModel):
    """The changes to learned skills that wait for the owner's yes (item 585)."""

    name: Literal["skills-proposals"]


class SkillsDecideCommand(FrozenModel):
    """The owner's yes or no to a proposal of a change to a learned skill (item 585)."""

    name: Literal["skills-accept", "skills-dismiss"]
    proposal_id: str


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


class BackupNowCommand(FrozenModel):
    name: Literal["backup-now"]


class BackupListCommand(FrozenModel):
    name: Literal["backup-list"]


type Command = Annotated[
    RunCommand
    | ResumeCommand
    | ChatCommand
    | ModelsCommand
    | ModelsProfileCommand
    | AuthSetCommand
    | AuthStatusCommand
    | DoctorCommand
    | SetupCommand
    | ReflectCommand
    | HarnessListCommand
    | HarnessShowCommand
    | HarnessDiffCommand
    | HarnessSetCommand
    | HarnessAdoptCommand
    | HarnessRetireCommand
    | HarnessProposeCommand
    | HarnessStepCommand
    | HarnessProbeCommand
    | HarnessFinalCommand
    | HarnessGraphCommand
    | HarnessStepsCommand
    | HarnessExperiencesCommand
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
    | MemoryProposalsCommand
    | MemoryKeepCommand
    | MemoryTrashCommand
    | MemoryRestoreCommand
    | MemoryPurgeCommand
    | MemoryPinCommand
    | MemoryUnmergeCommand
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
    | SkillsVersionsCommand
    | SkillsRestoreCommand
    | SkillsKeepCommand
    | SkillsProposalsCommand
    | SkillsDecideCommand
    | PlaybooksListCommand
    | PlaybooksShowCommand
    | PlaybooksDropCommand
    | PlaybooksTrustCommand
    | PruneCommand
    | BackupNowCommand
    | BackupListCommand
    | UndoCommand
    | EvalListCommand
    | EvalCheckCommand
    | EvalFromRunCommand
    | EvalRunCommand
    | EvalToolsCommand
    | EvalReportCommand
    | StatsCommand
    | DaemonCommand
    | OpenCommand
    | UrlCommand
    | ServiceInstallCommand
    | ServiceUninstallCommand
    | ServiceStatusCommand
    | ServicePermissionsCommand
    | ServiceRestartCommand,
    Field(discriminator="name"),
]
COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)

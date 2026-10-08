"""Per-run authorisation: turns policy decisions into proceed / refuse / ask the user / hand off, and in the autonomous
approval modes answers the policy's questions without the user."""

from dotagent.agent.approvals import ApprovalBroker, ApprovalOutcome
from dotagent.agent.review import Reviewer, ReviewRequest, ReviewVerdict, shorten_reason
from dotagent.core.errors import ApprovalRejected, HandedOff, PolicyDenied
from dotagent.core.types import JsonObject
from dotagent.events import EventBus
from dotagent.policy.engine import YOLO_WAIVED_SOURCES, approval_severity, decide, route_for
from dotagent.policy.types import TAINTING_KINDS, ActionInfo, ApprovalMode, Decision, Preset, Route
from dotagent.store.db import Store
from dotagent.store.models import RuleRecord, RunMode

# Who decided, as the approval records and the ``approval_auto`` event state it.
YOLO_DECIDER: str = "yolo"
REVIEWER_DECIDER: str = "reviewer"


def _describe(info: ActionInfo) -> JsonObject:
    return {
        "tool": info.tool,
        "kind": info.kind,
        "risk": info.risk,
        "summary": info.summary,
        "target": info.target,
        "flags": list(info.flags),
    }


def _rejection(outcome: ApprovalOutcome) -> str:
    """Why the action did not run, with the note the user gave; an expired approval has no author to quote."""
    note: str = outcome.note.strip()
    if outcome.status == "expired" or note == "":
        return f"the user did not approve this action ({outcome.status})"
    return f"the user did not approve this action ({outcome.status}, with the note “{note}”)"


class RunGate:
    """Authorises the actions of one run and remembers whether untrusted content has been seen.

    ``approval_mode`` says who answers a question the policy raises: the user (``ask``), the review model (``auto``) or
    nobody (``yolo``: the action runs and only a record is kept). Yolo also lets through what the other modes refuse
    (secrets, exfiltration) or hand to the user (money, credentials); a research run stays read-only and a hand-off
    rule of the user still stops it. ``instructions`` are the user's own words so far, which is all the reviewer takes
    as authority.
    """

    def __init__(
        self,
        store: Store,
        bus: EventBus,
        approvals: ApprovalBroker,
        run_id: str,
        mode: RunMode,
        preset: Preset,
        approval_mode: ApprovalMode,
        reviewer: Reviewer,
        instructions: tuple[str, ...],
    ) -> None:
        self._store: Store = store
        self._bus: EventBus = bus
        self._approvals: ApprovalBroker = approvals
        self._run_id: str = run_id
        self._mode: RunMode = mode
        self._preset: Preset = preset
        self._approval_mode: ApprovalMode = approval_mode
        self._reviewer: Reviewer = reviewer
        self._instructions: tuple[str, ...] = instructions
        self.tainted: bool = False

    @property
    def instructions(self) -> tuple[str, ...]:
        return self._instructions

    def add_instruction(self, text: str) -> None:
        """Count a message of the user (a steer, the answer to a question) among the instructions the reviewer reads."""
        self._instructions = (*self._instructions, text)

    def taint(self) -> None:
        """Record that the run has read content that third parties can author."""
        self.tainted = True

    def observe(self, info: ActionInfo) -> None:
        """Taint the run when a finished action read content that third parties can author."""
        if info.kind in TAINTING_KINDS:
            self.taint()

    def clear_taint(self) -> None:
        """A fresh instruction from the user restores trust in what follows."""
        self.tainted = False

    async def authorize(self, info: ActionInfo) -> str:
        """Let the action through or raise. Returns the note the user wrote when approving it, or an empty string."""
        rules: list[RuleRecord] = await self._store.list_rules()
        decision: Decision = decide(info, self._mode, self.tainted, self._preset, rules)
        if decision.verdict == "allow":
            return ""
        if self._approval_mode == "yolo" and decision.source in YOLO_WAIVED_SOURCES:
            return await self._waive(info, decision)
        if decision.verdict == "deny":
            await self._emit_policy(info, decision)
            raise PolicyDenied(decision.reason, {"action": _describe(info), "source": decision.source})
        route: Route = route_for(decision.verdict, self._approval_mode)
        if route == "waived":
            return await self._waive(info, decision)
        if route == "reviewer":
            return await self._review(info, decision)
        return await self._ask_user(info, decision, "")

    async def _emit_policy(self, info: ActionInfo, decision: Decision) -> None:
        await self._bus.emit(
            self._run_id,
            "policy",
            {**_describe(info), "verdict": decision.verdict, "source": decision.source, "reason": decision.reason},
            True,
        )

    async def _waive(self, info: ActionInfo, decision: Decision) -> str:
        """Yolo: the question is not asked and the refusal does not hold. The action goes ahead and the record says what
        the policy wanted."""
        await self._approvals.record_automatic(
            self._run_id,
            info.tool,
            f"{info.summary} — {decision.reason}",
            approval_severity(info),
            _describe(info),
            "approved",
            YOLO_DECIDER,
            decision.reason,
        )
        return ""

    async def _review(self, info: ActionInfo, decision: Decision) -> str:
        """Auto: the review model answers from the user's instructions; what it cannot tell goes to the user."""
        verdict: ReviewVerdict = await self._reviewer(
            ReviewRequest(instructions=self._instructions, action=info, reason=decision.reason, tainted=self.tainted)
        )
        reason: str = shorten_reason(verdict.reason)
        if verdict.decision == "escalate":
            return await self._ask_user(info, decision, f"The review model passed this on to you: {reason}")
        await self._approvals.record_automatic(
            self._run_id,
            info.tool,
            f"{info.summary} — {decision.reason}",
            approval_severity(info),
            _describe(info),
            "approved" if verdict.decision == "approve" else "denied",
            REVIEWER_DECIDER,
            reason,
        )
        if verdict.decision == "deny":
            raise ApprovalRejected(
                f"the review model refused this action: {reason}", {"action": _describe(info), "note": reason}
            )
        return ""

    async def _ask_user(self, info: ActionInfo, decision: Decision, passed_on: str) -> str:
        await self._emit_policy(info, decision)
        severity: str = "handoff" if decision.verdict == "handoff" else approval_severity(info)
        summary: str = f"{info.summary} — {decision.reason}" + (f". {passed_on}" if passed_on else "")
        outcome: ApprovalOutcome = await self._approvals.request(
            self._run_id, info.tool, summary, severity, _describe(info)
        )
        if outcome.status != "approved":
            raise ApprovalRejected(_rejection(outcome), {"action": _describe(info), "note": outcome.note})
        if decision.verdict == "handoff":
            raise HandedOff(outcome.note or "the user completed this step themselves", {"action": _describe(info)})
        return outcome.note.strip()

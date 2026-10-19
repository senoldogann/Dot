"""Per-run authorisation: turns policy decisions into proceed / refuse / ask the user / hand off, and in the autonomous
approval modes answers the policy's questions without the user."""

import asyncio
import time
from collections.abc import Mapping, Sequence

from dotagent.agent.approvals import ApprovalBroker, ApprovalOutcome
from dotagent.agent.review import Reviewer, ReviewRequest, ReviewVerdict, shorten_reason
from dotagent.config import PaceConfig, SpendCap
from dotagent.core.errors import ApprovalRejected, HandedOff, PolicyDenied, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.types import JsonObject
from dotagent.events import EventBus
from dotagent.policy.engine import (
    YOLO_WAIVED_SOURCES,
    approval_severity,
    decide,
    owner_handoff,
    route_for,
    supervised,
)
from dotagent.policy.pace import DAY_S, PacedAction, pace_refusal, paced_action, spacing_wait
from dotagent.policy.spend import SpendState, render_amount, spend_allowance, spend_host, spend_state
from dotagent.policy.types import TAINTING_KINDS, ActionInfo, ApprovalMode, Decision, Preset, Route
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RuleRecord, RunMode

log = get_logger(__name__)

# Who decided, as the approval records and the ``approval_auto`` event state it.
YOLO_DECIDER: str = "yolo"
REVIEWER_DECIDER: str = "reviewer"

# The user took the browser (and with it the run) over, and gave it back: the run waits at its next tool call in
# between (see ``RunGate.hold``). ``held_s`` of the handback is how long.
TAKEOVER_EVENT: str = "takeover"
HANDBACK_EVENT: str = "handback"

# The events that say whether a run is tainted after them (item 243): it starts so or not (``run_queued``), a tool's
# result may taint it (``tool_result``), and a message of the owner that steers it clears the taint (``steer``).
TAINT_EVENTS: tuple[str, ...] = ("run_queued", "tool_result", "steer")


def hold_note(seconds: float) -> str:
    """What a tool result says when the user held the run while it waited to run that tool."""
    return (
        f"[The user took over the browser and used it themselves for {seconds:.0f} s before this ran. The page may "
        "have changed: take a snapshot before you act on it.]"
    )


def tainted_after(events: Sequence[EventRecord]) -> bool:
    """Whether a run was tainted after the newest of ``events``, its newest event of each of :data:`TAINT_EVENTS`: a
    steer clears the taint, and the others hold whether the run was tainted then. An event stored before they held it
    counts as untainted, as a run that went on from it was then."""
    if not events:
        return False
    newest: EventRecord = max(events, key=lambda event: event.id)
    return newest.kind != "steer" and newest.data.get("tainted") is True


def _describe(info: ActionInfo) -> JsonObject:
    return {
        "tool": info.tool,
        "kind": info.kind,
        "risk": info.risk,
        "summary": info.summary,
        "target": info.target,
        "amount": None if info.amount is None else render_amount(info.amount),
        "flags": list(info.flags),
    }


def _rejection(outcome: ApprovalOutcome) -> str:
    """Why the action did not run, with the note the user gave; an expired approval has no author to quote."""
    note: str = outcome.note.strip()
    if outcome.status == "expired" or note == "":
        return f"the user did not approve this action ({outcome.status})"
    return f"the user did not approve this action ({outcome.status}, with the note “{note}”)"


class SiteLocks:
    """The locks the gates of all runs share, per site: one for its pace and one for its spending. Whether a public
    action or a payment may go ahead depends on what the other runs did on the site, so the gates read that, decide
    and record the action one at a time; a decision that waits for the user holds the next one on the same site."""

    def __init__(self) -> None:
        self._pace: dict[str, asyncio.Lock] = {}
        self._spend: dict[str, asyncio.Lock] = {}

    def pace(self, host: str) -> asyncio.Lock:
        return self._pace.setdefault(host, asyncio.Lock())

    def spend(self, host: str) -> asyncio.Lock:
        return self._spend.setdefault(host, asyncio.Lock())


class RunGate:
    """Authorises the actions of one run and remembers whether untrusted content has been seen.

    ``approval_mode`` says who answers a question the policy raises: the user (``supervised``, which also asks before
    every change the policy lets through, and ``ask``), the review model (``auto``) or nobody (``yolo``: the action
    runs and only a record is kept). A change of Dot's own files is the user's in every mode but yolo (item 398). Yolo
    also lets through what the other modes refuse (secrets, exfiltration) or hand to the user (money, credentials); a
    research run stays read-only and a hand-off rule of the user still stops it. ``instructions`` are the user's own
    words so far, which is all the reviewer takes as authority. ``pace`` bounds the public and social actions of the
    browser on each site, in every mode. ``locks`` are the runtime's :class:`SiteLocks`, shared with the gates of the
    other runs.
    """

    def __init__(
        self,
        store: Store,
        bus: EventBus,
        approvals: ApprovalBroker,
        run_id: str,
        mode: RunMode,
        preset: Preset,
        pace: PaceConfig,
        spend: Mapping[str, SpendCap],
        approval_mode: ApprovalMode,
        reviewer: Reviewer,
        instructions: tuple[str, ...],
        locks: SiteLocks,
    ) -> None:
        self._store: Store = store
        self._bus: EventBus = bus
        self._approvals: ApprovalBroker = approvals
        self._run_id: str = run_id
        self._mode: RunMode = mode
        self._preset: Preset = preset
        self._pace: PaceConfig = pace
        self._spend: Mapping[str, SpendCap] = spend
        self._approval_mode: ApprovalMode = approval_mode
        self._reviewer: Reviewer = reviewer
        self._instructions: tuple[str, ...] = instructions
        self._locks: SiteLocks = locks
        self.tainted: bool = False
        # Whether the run read such content at any point: a fresh instruction clears ``tainted``, never this.
        self.ever_tainted: bool = False
        # Set while the run may go on; cleared while the user holds it (``hold``), with when the hold began.
        self._free: asyncio.Event = asyncio.Event()
        self._free.set()
        self._held_since: float | None = None

    @property
    def instructions(self) -> tuple[str, ...]:
        return self._instructions

    @property
    def held(self) -> bool:
        return not self._free.is_set()

    def hold(self) -> None:
        """Keep the run from running its next tool call until ``release``; a run already held is refused."""
        if self.held:
            raise RunConflict("the run is already taken over", {"run_id": self._run_id})
        self._held_since = time.monotonic()
        self._free.clear()

    def release(self) -> float:
        """Let the run go on; returns how long it was held. A run that is not held is refused."""
        if not self.held or self._held_since is None:
            raise RunConflict("the run is not taken over", {"run_id": self._run_id})
        seconds: float = time.monotonic() - self._held_since
        self._held_since = None
        self._free.set()
        return seconds

    async def wait_free(self) -> float:
        """Wait while the user holds the run (the executor calls it before each tool runs); returns the seconds
        waited, 0 when the run was not held."""
        if not self.held:
            return 0.0
        waited_from: float = time.monotonic()
        await self._free.wait()
        return time.monotonic() - waited_from

    def add_instruction(self, text: str) -> None:
        """Count a message of the user (a steer, the answer to a question) among the instructions the reviewer reads."""
        self._instructions = (*self._instructions, text)

    def taint(self) -> None:
        """Record that the run has read content that third parties can author."""
        self.tainted = True
        self.ever_tainted = True

    def observe(self, info: ActionInfo) -> None:
        """Taint the run when a finished action read content that third parties can author."""
        if info.kind in TAINTING_KINDS:
            self.taint()

    def clear_taint(self) -> None:
        """A fresh instruction from the user restores trust in what follows."""
        self.tainted = False

    async def authorize(self, info: ActionInfo) -> str:
        """Let the action through or raise. Returns the note the user wrote when approving it, or an empty string. A
        public or social browser action keeps the pace of its site first, whatever the approval mode, and counts once
        it is let through (:mod:`dotagent.policy.pace`), all under the site's pace lock."""
        paced: PacedAction | None = paced_action(info)
        if paced is None:
            return await self._decide(info)
        async with self._locks.pace(paced.host):
            await self._keep_pace(info, paced)
            note: str = await self._decide(info)
            await self._space(paced)
            await self._store.record_paced_action(paced.host, paced.pace, time.time(), self._run_id)
        return note

    async def _keep_pace(self, info: ActionInfo, paced: PacedAction) -> None:
        """Refuse the action when its site had as many of its class as the pace allows in the last hour or day."""
        now: float = time.time()
        times: list[float] = await self._store.paced_times(paced.host, paced.pace, now - DAY_S)
        refusal: str | None = pace_refusal(self._pace, paced, times, now)
        if refusal is None:
            return
        decision: Decision = Decision(verdict="deny", source="pace", reason=refusal, rule_id=None)
        await self._emit_policy(info, decision)
        raise PolicyDenied(refusal, {"action": _describe(info), "source": decision.source})

    async def _space(self, paced: PacedAction) -> None:
        """Wait until a public action comes ``public_spacing_s`` after the last public one on its site."""
        now: float = time.time()
        times: list[float] = await self._store.paced_times(paced.host, paced.pace, now - self._pace.public_spacing_s)
        wait: float = spacing_wait(self._pace, paced, times, now)
        if wait > 0:
            log.info("pace_wait", run_id=self._run_id, host=paced.host, seconds=round(wait, 1))
            await asyncio.sleep(wait)

    async def _decide(self, info: ActionInfo) -> str:
        """What the policy says and who answers its question: the action goes through, or the call raises. A payment
        on a site with caps is decided under the site's spend lock, from what was spent there until then."""
        site: str | None = self._capped_site(info)
        if site is None:
            return await self._decide_spent(info, None)
        async with self._locks.spend(site):
            return await self._decide_spent(info, site)

    async def _decide_spent(self, info: ActionInfo, site: str | None) -> str:
        rules: list[RuleRecord] = await self._store.list_rules()
        state: SpendState | None = None if site is None else await self._spend_state(info, site)
        allowance: Decision | None = None if state is None else spend_allowance(state)
        decided: Decision = decide(info, self._mode, self.tainted, self._preset, rules, allowance)
        decision: Decision = supervised(decided, info) if self._approval_mode == "supervised" else decided
        if decision.verdict == "allow":
            return ""
        if allowance is not None and decision is allowance and state is not None:
            note: str = await self._authorize_decision(info, decision, rules)
            await self._store.record_spend(
                state.host, state.amount.value, state.amount.currency, time.time(), self._run_id
            )
            log.info("spend_recorded", run_id=self._run_id, host=state.host, amount=state.amount.value)
            return note
        return await self._authorize_decision(info, decision, rules)

    def _capped_site(self, info: ActionInfo) -> str | None:
        """The site of a payment step with an amount on a site with pre-approved caps; ``None`` for any other action."""
        if "financial" not in info.flags or info.amount is None:
            return None
        host: str | None = spend_host(info.target)
        return host if host is not None and host in self._spend else None

    async def _spend_state(self, info: ActionInfo, site: str) -> SpendState | None:
        """The pre-approved caps' view of a payment step on ``site``, from what was spent there in the last day."""
        spent: float = await self._store.spent_since(site, time.time() - DAY_S)
        return spend_state(self._spend, info.target, info.amount, spent)

    async def _authorize_decision(self, info: ActionInfo, decision: Decision, rules: list[RuleRecord]) -> str:
        """What the decision means in this approval mode: a waiver, a refusal, the review model or the user."""
        if decision.verdict == "allow":
            return ""
        if self._approval_mode == "yolo" and decision.source in YOLO_WAIVED_SOURCES:
            handoff: Decision | None = owner_handoff(info, rules)
            if handoff is not None:
                return await self._ask_user(info, handoff, "")
            return await self._waive(info, decision)
        if decision.verdict == "deny":
            await self._emit_policy(info, decision)
            raise PolicyDenied(decision.reason, {"action": _describe(info), "source": decision.source})
        route: Route = route_for(decision, self._approval_mode)
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
        the policy wanted: the action is its summary, the policy's reason its note, each once."""
        await self._approvals.record_automatic(
            self._run_id,
            info.tool,
            info.summary,
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

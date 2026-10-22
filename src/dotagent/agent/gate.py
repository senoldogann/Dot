"""Per-run authorisation: turns policy decisions into proceed / refuse / ask the user / hand off, and in the autonomous
approval modes answers the policy's questions without the user."""

import asyncio
import time
from collections.abc import Mapping, Sequence

from dotagent.agent.approvals import TIMEOUT_DECIDER, ApprovalBroker, ApprovalOutcome, Kept
from dotagent.agent.authorization import Answerer, Authorization, step_decision
from dotagent.agent.declined import OWNER_DECIDERS, Declined, declined_key, refusal_text, still_declined
from dotagent.agent.parking import (
    PERMIT_DECIDER,
    Parking,
    kept_ask_text,
    kept_digest,
    kept_text,
    permit_parkable,
    skipped_text,
)
from dotagent.agent.review import Reviewer, ReviewRequest, ReviewVerdict, shorten_reason
from dotagent.config import PaceConfig, SpendCap
from dotagent.core.errors import ApprovalParked, ApprovalRejected, HandedOff, PolicyDenied, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_known
from dotagent.core.types import JsonObject
from dotagent.events import EventBus
from dotagent.policy.egress import carries_secret
from dotagent.policy.engine import (
    approval_severity,
    checked_when_tainted,
    decide,
    decide_past_review,
    route_for,
    supervised,
    waived_in_yolo,
)
from dotagent.policy.pace import DAY_S, PacedAction, pace_refusal, paced_action, spacing_wait
from dotagent.policy.spend import SpendState, render_amount, spend_allowance, spend_host, spend_state
from dotagent.policy.types import TAINTING_KINDS, ActionInfo, ApprovalMode, Decision, Preset, Route
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, EventRecord, RuleRecord, RunMode

log = get_logger(__name__)

# Who decided, as the approval records and the ``approval_auto`` event state it.
YOLO_DECIDER: str = "yolo"
REVIEWER_DECIDER: str = "reviewer"

# The user took the browser (and with it the run) over, and gave it back: the run waits at its next tool call in
# between (see ``RunGate.hold``). ``held_s`` of the handback is how long.
TAKEOVER_EVENT: str = "takeover"
HANDBACK_EVENT: str = "handback"

# The events that say whether a run is tainted after them (item 243): it starts so or not (``run_queued``), a tool's
# result may taint it (``tool_result``), and a message of the owner that steers it clears the taint (``steer``); Dot's
# words that the owner did a step kept for them vouch for nothing and keep it as it was (item 482).
TAINT_EVENTS: tuple[str, ...] = ("run_queued", "tool_result", "steer")


def hold_note(seconds: float) -> str:
    """What a tool result says when the user held the run while it waited to run that tool."""
    return (
        f"[The user took over the browser and used it themselves for {seconds:.0f} s before this ran. The page may "
        "have changed: take a snapshot before you act on it.]"
    )


def tainted_after(events: Sequence[EventRecord]) -> bool:
    """Whether a run was tainted after the newest of ``events``, its newest event of each of :data:`TAINT_EVENTS`: a
    steer of the owner clears the taint, and the others, a steer that vouches for nothing among them, hold whether the
    run was tainted then. An event stored before they held it counts as untainted, as a run that went on from it was
    then."""
    if not events:
        return False
    newest: EventRecord = max(events, key=lambda event: event.id)
    if newest.kind == "steer" and newest.data.get("vouches") is not False:
        return False
    return newest.data.get("tainted") is True


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
    every change the policy lets through, and ``ask``), the review model (``auto``) or nobody (``yolo``: the action runs
    and only a record is kept). A yolo run asks nothing at all (item 443) — the guard of money, credentials, messages
    and posts of item 429 included, and the refusal of secrets and exfiltration of item 433 — and the record of the step
    keeps the reason it would have asked for. A change of Dot's own files is the user's in every mode but yolo (item
    398); a research run stays read-only and a refusal that stands still refuses.
    ``instructions`` are the user's own words so far, which is all the reviewer takes as authority. ``pace`` bounds the
    public and social actions of the browser on each site, in every mode. ``locks`` are the runtime's
    :class:`SiteLocks`, shared with the gates of the other runs.
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
        taint_check: bool = True,
    ) -> None:
        self._store: Store = store
        # Whether a yolo run that read outside content has the review model check the steps an instruction planted in
        # that content is after, before it does them (item 525, ``[policy] yolo_taint_check``).
        self._taint_check: bool = taint_check
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
        # The actions the owner refused in this run, by how their card showed them (item 456).
        self._declined: Mapping[str, Declined] = {}
        self.tainted: bool = False
        # Whether the run read such content at any point: a fresh instruction clears ``tainted``, never this.
        self.ever_tainted: bool = False
        # Set while the run may go on; cleared while the user holds it (``hold``), with when the hold began.
        self._free: asyncio.Event = asyncio.Event()
        self._free.set()
        self._held_since: float | None = None
        # How many of the run's tool calls wait for the user to give the run back now (item 479).
        self.waiting: int = 0
        # Where the run keeps a step it hands to the owner when nobody answers in time (item 482), None where such a
        # question ends as a refusal; and whether the owner left one unanswered since they last wrote, so that the
        # run's next hand-offs are kept at once rather than waited for again.
        self._parking: Parking | None = None
        self._owner_away: bool = False

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
        self.waiting += 1
        try:
            await self._free.wait()
        finally:
            self.waiting -= 1
        return time.monotonic() - waited_from

    def add_instruction(self, text: str) -> None:
        """Count a message of the user (a steer, the answer to a question) among the instructions the reviewer reads;
        the user is there again (:meth:`heard_from_owner`)."""
        self._instructions = (*self._instructions, text)
        self.heard_from_owner()

    def heard_from_owner(self) -> None:
        """The owner did something in the run (wrote to it, answered it, did a step it kept for them): a hand-off is put
        to them again before it is kept (item 482)."""
        self._owner_away = False

    @property
    def parking(self) -> Parking | None:
        """Where the run keeps the steps nobody answers (item 482), None where it refuses them; a run it delegates keeps
        them there too (item 485)."""
        return self._parking

    def keep_for(self, parking: Parking | None) -> None:
        """Keep the steps the run hands to the owner that nobody answers in time, as ``parking`` says (item 482); None
        refuses them as before."""
        self._parking = parking

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
        """Let the action through or raise. Returns the note the user wrote when approving it, or an empty string."""
        return (await self.authorize_recorded(info)).note

    async def authorize_recorded(self, info: ActionInfo, arguments: JsonObject | None = None) -> Authorization:
        """Let the action through or raise, as :meth:`authorize`, with what decided it for the step's record (item
        468). A run marked untrusted first refuses a step that would send a secret out of the Mac, read in the call's
        ``arguments`` when the caller has them and in the step's target and summary (item 525), in every approval mode:
        every way a run acts comes through here. A public or social browser action keeps the pace of its site first,
        whatever the approval mode, and counts once it is let through (:mod:`dotagent.policy.pace`), all under the
        site's pace lock."""
        await self._refuse_carried_secret(info, {} if arguments is None else arguments)
        paced: PacedAction | None = paced_action(info)
        if paced is None:
            return await self._decide(info)
        async with self._locks.pace(paced.host):
            await self._keep_pace(info, paced)
            authorized: Authorization = await self._decide(info)
            await self._space(paced)
            await self._store.record_paced_action(paced.host, paced.pace, time.time(), self._run_id)
        return authorized

    async def _keep_pace(self, info: ActionInfo, paced: PacedAction) -> None:
        """Refuse the action when its site had as many of its class as the pace allows in the last hour or day."""
        now: float = time.time()
        times: list[float] = await self._store.paced_times(paced.host, paced.pace, now - DAY_S)
        refusal: str | None = pace_refusal(self._pace, paced, times, now)
        if refusal is None:
            return
        decision: Decision = Decision(verdict="deny", source="pace", reason=refusal, rule_id=None)
        # A refusal before the policy weighs the step: the run's taint as it stands.
        await self._emit_policy(info, decision, self.tainted)
        raise PolicyDenied(refusal, {"action": _describe(info), "source": decision.source})

    async def _space(self, paced: PacedAction) -> None:
        """Wait until a public action comes ``public_spacing_s`` after the last public one on its site."""
        now: float = time.time()
        times: list[float] = await self._store.paced_times(paced.host, paced.pace, now - self._pace.public_spacing_s)
        wait: float = spacing_wait(self._pace, paced, times, now)
        if wait > 0:
            log.info("pace_wait", run_id=self._run_id, host=paced.host, seconds=round(wait, 1))
            await asyncio.sleep(wait)

    async def _decide(self, info: ActionInfo) -> Authorization:
        """What the policy says and who answers its question: the action goes through, or the call raises. A payment
        on a site with caps is decided under the site's spend lock, from what was spent there until then."""
        site: str | None = self._capped_site(info)
        if site is None:
            return await self._decide_spent(info, None)
        async with self._locks.spend(site):
            return await self._decide_spent(info, site)

    async def _decide_spent(self, info: ActionInfo, site: str | None) -> Authorization:
        rules: list[RuleRecord] = await self._store.list_rules()
        state: SpendState | None = None if site is None else await self._spend_state(info, site)
        allowance: Decision | None = None if state is None else spend_allowance(state)
        # The taint the policy weighs, kept with the step's record (item 474).
        tainted: bool = self.tainted
        decided: Decision = decide(info, self._mode, tainted, self._preset, rules, allowance)
        if self._approval_mode == "yolo" and decided.source == "auto_review":
            # What the always-on review refuses in every other mode, a yolo run does without asking: the layers
            # after the review still judge the step, and a refusal among them stands (item 443).
            rest: Decision = decide_past_review(info, self._mode, tainted, self._preset, rules, allowance)
            review: Decision = waived_in_yolo(info)
            if rest.verdict == "allow":
                decided = review
            elif rest.verdict == "deny":
                decided = rest
            else:
                # A step that both reads a secret and hands money over is waived for both: its record says what each
                # would have asked (item 443).
                decided = rest.model_copy(update={"reason": f"{rest.reason}; also {review.reason}"})
        decision: Decision = supervised(decided, info) if self._approval_mode == "supervised" else decided
        note, answered_by = await self._authorize_decision(info, decision, rules, tainted)
        if allowance is not None and state is not None:
            # The payment goes ahead under its caps, whichever question let it through (the guard asks first when the
            # step also sends or posts, item 429): it counts against the site's day. A refusal or a hand-off raised.
            await self._store.record_spend(
                state.host, state.amount.value, state.amount.currency, time.time(), self._run_id
            )
            log.info("spend_recorded", run_id=self._run_id, host=state.host, amount=state.amount.value)
        elif self._approval_mode == "yolo" and state is not None:
            # A yolo run does the payment a cap would have held (item 443), and what it spends counts against the site's
            # day as any other payment's does: a later run of a mode that asks must meet the true total.
            await self._store.record_spend(
                state.host, state.amount.value, state.amount.currency, time.time(), self._run_id
            )
            log.info("spend_recorded", run_id=self._run_id, host=state.host, amount=state.amount.value)
        return Authorization(note=note, decision=step_decision(decision, answered_by, self._preset, tainted))

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

    async def _authorize_decision(
        self, info: ActionInfo, decision: Decision, rules: list[RuleRecord], tainted: bool
    ) -> tuple[str, Answerer]:
        """What the decision means in this approval mode: a waiver, a refusal, the review model or the user; with the
        owner's note and who answered (item 468). ``tainted`` is the taint the policy weighed: the review model reads
        it, and the policy event keeps it (item 474), however long an answer takes."""
        if (
            self._approval_mode == "yolo"
            and self._taint_check
            and tainted
            and decision.verdict != "deny"
            and checked_when_tainted(info)
        ):
            await self._check_taint(info, decision)
        if decision.verdict == "allow":
            return "", "policy"
        if self._approval_mode == "yolo" and decision.verdict != "deny":
            # A yolo run asks nothing (the owner's word of 2026-10-20, item 443): money, a credential, a message or a
            # post in their name, a spend, Dot's own files, the review's refusal — the step does it, and the record says
            # what it would have asked. A refusal is not a question and stands below.
            return await self._waive(info, decision), "yolo"
        if decision.verdict == "deny":
            await self._emit_policy(info, decision, tainted)
            raise PolicyDenied(decision.reason, {"action": _describe(info), "source": decision.source})
        # An action the owner refused is not put to them, nor to the review model, again until they write (item 456);
        # the policy's event is its record, as a step the owner refuses has (item 474).
        earlier: Declined | None = still_declined(self._declined, declined_key(info), len(self._instructions))
        if earlier is not None:
            await self._emit_policy(info, decision, tainted)
            log.warning("declined_again_refused", run_id=self._run_id, tool=info.tool)
            raise ApprovalRejected(refusal_text(earlier), {"action": _describe(info), "note": earlier.note})
        allowed: ApprovalRecord | None = await self._kept_answer(info, decision, tainted)
        if allowed is not None:
            return allowed.note.strip(), "user"
        route: Route = route_for(decision, self._approval_mode)
        if route == "waived":
            return await self._waive(info, decision), "yolo"
        if route == "reviewer":
            return await self._review(info, decision, tainted)
        return await self._ask_user(info, decision, "", tainted), "user"

    async def _kept_answer(self, info: ActionInfo, decision: Decision, tainted: bool) -> ApprovalRecord | None:
        """What the owner answered to this step when it was kept for them where this run keeps its steps (items 482 to
        484: its chat, or its campaign's conversation): a Skip they have not taken back by writing there since refuses
        it at once, with no card; their yes to a kept question,
        unused and in time, lets this question through once, and is returned (None: neither). Only a run that keeps
        steps reads them, and only for a step it would keep: a refusal, a question the run's reading raised and yolo,
        which asks nothing, never come here."""
        parking: Parking | None = self._parking
        if parking is None:
            return None
        asks: bool = permit_parkable(info, decision)
        digest: str | None = kept_digest(info)
        if digest is None or not (asks or decision.verdict == "handoff"):
            return None
        skipped: ApprovalRecord | None = await self._approvals.skipped(digest, parking)
        if skipped is not None:
            await self._emit_policy(info, decision, tainted)
            log.warning("skipped_step_refused", run_id=self._run_id, tool=info.tool, approval_id=skipped.id)
            raise ApprovalRejected(skipped_text(skipped.id), {"action": _describe(info), "approval_id": skipped.id})
        if not asks:
            return None
        allowed: ApprovalRecord | None = await self._approvals.take_permit(digest, parking, time.time())
        if allowed is None:
            return None
        await self._approvals.record_automatic(
            self._run_id,
            info.tool,
            f"{info.summary} — {decision.reason}",
            approval_severity(info),
            _describe(info),
            "approved",
            PERMIT_DECIDER,
            f"the user allowed it when it was kept for them ({allowed.id})",
        )
        log.info("permit_used", run_id=self._run_id, tool=info.tool, approval_id=allowed.id)
        return allowed

    async def _keep(self, info: ActionInfo, decision: Decision, summary: str, severity: str) -> ApprovalRejected:
        """Keep the step ``info`` for the owner (items 482, 483), and what the run is told of it: that it did not happen
        and waits for them to do it, or for their yes, or, past the caps of the kept steps, a refusal as before."""
        parking: Parking | None = self._parking
        if parking is None:
            raise RunConflict("this run keeps no step for the owner", {"run_id": self._run_id})
        kept: Kept | None = await self._approvals.keep(
            self._run_id,
            info.tool,
            summary,
            severity,
            _describe(info),
            kept_digest(info),
            parking,
        )
        if kept is None:
            return ApprovalRejected(
                "nobody answered in time, and too many steps wait for the user already, so this one is not kept",
                {"action": _describe(info)},
            )
        log.info("step_kept", run_id=self._run_id, tool=info.tool, approval_id=kept.record.id, created=kept.created)
        told: str = (
            kept_text(kept.record.id, kept.created)
            if decision.verdict == "handoff"
            else kept_ask_text(kept.record.id, kept.created)
        )
        return ApprovalParked(told, {"action": _describe(info), "approval_id": kept.record.id})

    async def _emit_policy(self, info: ActionInfo, decision: Decision, tainted: bool) -> None:
        """The event of a step the policy asks about, hands off or refuses, with whether the run was tainted when it
        was weighed (item 474): a step that does not go through keeps no decision in its result, so this is its
        record."""
        await self._bus.emit(
            self._run_id,
            "policy",
            {
                **_describe(info),
                "verdict": decision.verdict,
                "source": decision.source,
                "reason": decision.reason,
                "rule_id": decision.rule_id,
                "tainted": tainted,
            },
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

    async def _check_taint(self, info: ActionInfo, decision: Decision) -> None:
        """Yolo's check of a step an instruction planted in outside content is after, once the run read such content
        (item 525): the review model judges it against the owner's instructions, and nobody is asked. Its refusal
        stands, as a refusal of the policy does in yolo; any other answer leaves the step to yolo, which does it. A
        review model that cannot answer fails the step with its error."""
        verdict: ReviewVerdict = await self._reviewer(
            ReviewRequest(instructions=self._instructions, action=info, reason=decision.reason, tainted=True)
        )
        if verdict.decision != "deny":
            log.info("taint_check_passed", run_id=self._run_id, tool=info.tool, decision=verdict.decision)
            return
        reason: str = shorten_reason(verdict.reason)
        # A step that does not go through keeps its policy event as its record (item 474).
        await self._emit_policy(
            info,
            Decision(
                verdict="deny",
                source="taint",
                reason=f"this run read outside content, and the review model found the step not asked for: {reason}",
                rule_id=None,
            ),
            True,
        )
        await self._approvals.record_automatic(
            self._run_id,
            info.tool,
            f"{info.summary} — read outside content, and the review model found it not asked for",
            approval_severity(info),
            _describe(info),
            "denied",
            REVIEWER_DECIDER,
            reason,
        )
        log.warning("taint_check_refused", run_id=self._run_id, tool=info.tool)
        raise ApprovalRejected(
            f"this run read outside content, and the review model found this step not asked for by the owner: {reason}",
            {"action": _describe(info), "note": reason},
        )

    async def _refuse_carried_secret(self, info: ActionInfo, arguments: JsonObject) -> None:
        """Refuse a step of a run that read outside content when its words would send a secret Dot holds, or text in
        the form of a key, out of the Mac (item 525): in every approval mode, yolo too, whatever the preset or the
        owner's rules say. Its record and its error mask the secrets, as they hold the one refused."""
        if not self.tainted:
            return
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets)
        if not carries_secret(info, arguments, known):
            return
        masked: ActionInfo = info.model_copy(
            update={"summary": redact_known(info.summary, known), "target": redact_known(info.target, known)}
        )
        decision: Decision = Decision(
            verdict="deny",
            source="taint",
            reason="this run read outside content, and the step would send a secret out of the Mac",
            rule_id=None,
        )
        await self._emit_policy(masked, decision, True)
        log.warning("secret_egress_refused", run_id=self._run_id, tool=info.tool)
        raise PolicyDenied(decision.reason, {"action": _describe(masked), "source": decision.source})

    async def _review(self, info: ActionInfo, decision: Decision, tainted: bool) -> tuple[str, Answerer]:
        """Auto: the review model answers from the user's instructions; what it cannot tell goes to the user, who then
        answered."""
        verdict: ReviewVerdict = await self._reviewer(
            ReviewRequest(instructions=self._instructions, action=info, reason=decision.reason, tainted=tainted)
        )
        reason: str = shorten_reason(verdict.reason)
        if verdict.decision == "escalate":
            passed_on: str = f"The review model passed this on to you: {reason}"
            return await self._ask_user(info, decision, passed_on, tainted), "user"
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
        return "", "reviewer"

    async def _ask_user(self, info: ActionInfo, decision: Decision, passed_on: str, tainted: bool) -> str:
        await self._emit_policy(info, decision, tainted)
        severity: str = "handoff" if decision.verdict == "handoff" else approval_severity(info)
        summary: str = f"{info.summary} — {decision.reason}" + (f". {passed_on}" if passed_on else "")
        # A step handed to the owner, or a question whose card shows all of the step and reaches nobody, is kept for
        # them when nobody answers it (items 482, 483): at once while the owner has left the run's last kept step
        # unanswered and written nothing since, else once its question expires.
        keeps: bool = self._parking is not None and (decision.verdict == "handoff" or permit_parkable(info, decision))
        if keeps and self._owner_away:
            at_once: ApprovalRejected = await self._keep(info, decision, summary, severity)
            if isinstance(at_once, ApprovalParked):
                raise at_once
            # Past the caps of the kept steps it is asked as before, and refused if nobody answers it.
            self._owner_away = False
        outcome: ApprovalOutcome = await self._approvals.request(
            self._run_id, info.tool, summary, severity, _describe(info)
        )
        if keeps and outcome.status == "expired" and outcome.decided_by == TIMEOUT_DECIDER:
            unanswered: ApprovalRejected = await self._keep(info, decision, summary, severity)
            self._owner_away = isinstance(unanswered, ApprovalParked)
            raise unanswered
        if outcome.status != "approved":
            # The owner's no is kept; a question nobody answered in time, or one the eval harness refused, is no answer.
            key: str | None = declined_key(info)
            if outcome.status == "denied" and outcome.decided_by in OWNER_DECIDERS and key is not None:
                kept: Declined = Declined(note=outcome.note.strip(), spoken=len(self._instructions))
                self._declined = {**self._declined, key: kept}
            raise ApprovalRejected(_rejection(outcome), {"action": _describe(info), "note": outcome.note})
        if decision.verdict == "handoff":
            raise HandedOff(outcome.note or "the user completed this step themselves", {"action": _describe(info)})
        return outcome.note.strip()

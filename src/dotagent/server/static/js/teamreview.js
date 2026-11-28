// The manager review of the team (item 2068), on the Orders part of the Agents page: who reviews and how often (off
// until the owner turns it on), Review now, what the newest review found and its proposals, each a card the owner
// accepts or skips. Nothing changes until the owner accepts a card. The phone's bot profile has the same page.

import { api } from "./api.js";
import { overloadLines } from "./botwork.js";
import { ago, clip, el } from "./dom.js";
import { errorPanel, field, loadingPanel, segmented } from "./panels.js";
import { attempt, toast } from "./ui.js";
import { listAnd, t, tCount } from "./i18n.js";

const SUMMARY_CHARS = 600;
const TITLE_CHARS = 140;
// How often the part reads the review again while one is under way.
const POLL_MS = 2000;

/**
 * @typedef {{ kind: string, count: number, limit: number }} Reason
 * @typedef {{ kind: string, agent_id: string, agent_name: string, subject_id: string, subject: string, count: number, limit: number, reasons: Reason[] }} Finding
 * @typedef {{ name: string, role: string, routine_ids: string[], routine_names: string[] }} Part
 * @typedef {{ id: string, kind: string, finding: number, why: string, agent_id: string, agent_name: string, order_ids: string[], order_titles: string[], to_agent: string, to_name: string, parts: Part[], routine_id: string, routine_name: string, status: string, decided_at: number | null }} Proposal
 * @typedef {{ at: number, manager_id: string, manager_name: string, findings: Finding[], summary: string, proposals: Proposal[] }} Review
 * @typedef {{ manager: string, every: string, last_at: number | null, next_at: number | null, running: boolean, review: Review | null, error: string }} ReviewView
 */

/** A title read from outside content is withheld by the daemon (""): it is said so, not shown. */
function titled(title, fallback) {
  return title === "" ? fallback : `“${clip(title, TITLE_CHARS)}”`;
}

/**
 * A finding in the owner's words.
 * @param {Finding} finding
 */
function findingWords(finding) {
  const bot = finding.agent_name;
  if (finding.kind === "stuck_order") {
    return t("teamreview.findingStuck", { title: titled(finding.subject, t("teamreview.aWorkOrder")), bot, days: finding.count });
  }
  if (finding.kind === "objective_behind") {
    return t("teamreview.findingBehind", { title: titled(finding.subject, t("teamreview.anObjective")), days: finding.count });
  }
  if (finding.kind === "overloaded") {
    const reasons = overloadLines(finding).map(({ key, values }) => t(key, values));
    return t("teamreview.findingOverloaded", { bot, reasons: reasons.join(" · ") });
  }
  if (finding.kind === "failing") {
    return t("teamreview.findingFailing", { bot, failed: finding.count, runs: finding.limit });
  }
  return t("teamreview.findingIdle", { bot, days: finding.count });
}

/**
 * What a proposal would change, as the card's heading.
 * @param {Proposal} proposal
 */
function proposalHeading(proposal) {
  if (proposal.kind === "move_orders") {
    return tCount(proposal.order_ids.length, "teamreview.moveOrder", "teamreview.moveOrders", { from: proposal.agent_name, to: proposal.to_name });
  }
  if (proposal.kind === "split_bot") {
    return t("teamreview.splitBot", { bot: proposal.agent_name, names: listAnd(proposal.parts.map((part) => part.name)) });
  }
  return t("teamreview.retireRoutine", { routine: titled(proposal.routine_name, t("teamreview.aRoutine")), bot: proposal.agent_name });
}

/** @param {Proposal} proposal */
function proposalDetails(proposal) {
  if (proposal.kind === "move_orders") {
    return proposal.order_titles.map((title) => el("li", null, titled(title, t("teamreview.aWorkOrder"))));
  }
  if (proposal.kind === "split_bot") {
    return proposal.parts.map((part) =>
      el(
        "li",
        null,
        el("strong", null, part.name),
        `: ${part.role} `,
        el(
          "span",
          { class: "muted" },
          part.routine_names.length === 0
            ? t("teamreview.noRoutine")
            : t("teamreview.takesRoutines", { routines: listAnd(part.routine_names.map((name) => titled(name, t("teamreview.aRoutine")))) }),
        ),
      ),
    );
  }
  return [];
}

/** The part of the Agents page for the manager review. Returns its `element`, `reload()` and `dispose()`. */
export function teamReviewPart() {
  const holder = el("div", null, loadingPanel());
  let ticket = 0;
  let gone = false;
  let poll = 0;
  /** @type {Set<string>} */
  const busy = new Set();
  /** @type {{ id: string, name: string }[]} */
  let bots = [];
  /** @type {ReviewView | null} */
  let shown = null;

  async function reload() {
    const mine = ++ticket;
    try {
      const [view, agents] = await Promise.all([api.get("/team-review"), api.get("/agents")]);
      if (gone || mine !== ticket) {
        return;
      }
      bots = agents.map((/** @type {{ record: { id: string, name: string } }} */ summary) => ({ id: summary.record.id, name: summary.record.name }));
      paint(view);
    } catch (error) {
      if (!gone && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  function watch(view) {
    clearTimeout(poll);
    if (view.running && !gone) {
      poll = setTimeout(() => attempt(reload), POLL_MS);
    }
  }

  /** @param {string} manager @param {string} every */
  async function save(manager, every) {
    paint(await api.put("/team-review", { manager, every }));
  }

  async function reviewNow() {
    paint(await api.post("/team-review/run", null));
  }

  /** @param {Proposal} proposal @param {"accept" | "skip"} verb */
  async function decide(proposal, verb) {
    busy.add(proposal.id);
    if (shown !== null) {
      paint(shown);
    }
    try {
      const view = await api.post(`/team-review/proposals/${encodeURIComponent(proposal.id)}/${verb}`, null);
      busy.delete(proposal.id);
      paint(view);
      toast("ok", verb === "accept" ? t("teamreview.changedAsTheCardSaid") : t("teamreview.skippedNothingChanged"));
    } finally {
      busy.delete(proposal.id);
      if (shown !== null) {
        paint(shown);
      }
    }
  }

  /** @param {Proposal} proposal */
  function proposalCard(proposal) {
    const working = busy.has(proposal.id);
    const waiting = proposal.status === "waiting";
    const accept = el("button", { class: "btn sm primary team-accept", type: "button", disabled: working }, t("teamreview.accept"));
    accept.addEventListener("click", () => attempt(() => decide(proposal, "accept")));
    const skip = el("button", { class: "btn sm ghost team-skip", type: "button", disabled: working }, t("teamreview.skip"));
    skip.addEventListener("click", () => attempt(() => decide(proposal, "skip")));
    const details = proposalDetails(proposal);
    return el(
      "div",
      { class: "list-row work-row team-proposal", dataset: { proposal: proposal.id, kind: proposal.kind, status: proposal.status } },
      el(
        "div",
        { class: "row-main" },
        el(
          "div",
          { class: "row work-heads" },
          waiting ? null : el("span", { class: `badge ${proposal.status === "accepted" ? "b-ok" : "b-muted"}` }, proposal.status === "accepted" ? t("teamreview.accepted") : t("teamreview.skipped")),
          el("span", { class: "row-title" }, proposalHeading(proposal)),
        ),
        details.length === 0 ? null : el("ul", { class: "team-proposal-list" }, details),
        el("div", { class: "row-text muted" }, proposal.why),
        waiting ? el("div", { class: "team-proposal-actions" }, accept, skip) : null,
      ),
    );
  }

  /** @param {ReviewView} view */
  function settings(view) {
    const manager = el(
      "select",
      { class: "select team-manager" },
      ...bots.map((bot) => el("option", { value: bot.id, selected: bot.id === view.manager }, bot.name)),
    );
    manager.addEventListener("change", () => attempt(() => save(manager.value, view.every)));
    const every = segmented(
      [
        { id: "off", label: t("teamreview.off"), hint: null },
        { id: "daily", label: t("teamreview.daily"), hint: null },
        { id: "weekly", label: t("teamreview.weekly"), hint: null },
      ],
      view.every,
      (id) => attempt(() => save(manager.value, id)),
    );
    const now = el("button", { class: "btn sm team-review-now", type: "button", disabled: view.running }, view.running ? t("teamreview.reviewing") : t("teamreview.reviewNow"));
    now.addEventListener("click", () => attempt(reviewNow));
    const when =
      view.every === "off"
        ? t("teamreview.offHint")
        : view.next_at === null
          ? ""
          : t("teamreview.nextReview", { date: new Date(view.next_at * 1000).toLocaleString() });
    return el(
      "div",
      { class: "team-review-settings" },
      field(t("teamreview.manager"), manager, ""),
      field(t("teamreview.howOften"), every.element, ""),
      now,
      el("p", { class: "hint team-review-when" }, when),
    );
  }

  /** @param {ReviewView} view */
  function paint(view) {
    shown = view;
    watch(view);
    const review = view.review;
    const parts = [settings(view)];
    if (view.error !== "") {
      parts.push(el("p", { class: "row-text team-review-error" }, t("teamreview.lastReviewFailed", { error: clip(view.error, SUMMARY_CHARS) })));
    }
    if (review !== null) {
      parts.push(el("p", { class: "row-meta" }, t("teamreview.reviewedBy", { name: review.manager_name, ago: ago(review.at) })));
      if (review.findings.length === 0) {
        parts.push(el("p", { class: "muted" }, t("teamreview.nothingFound")));
      } else {
        if (review.summary !== "") {
          parts.push(el("p", { class: "team-review-summary" }, clip(review.summary, SUMMARY_CHARS)));
        }
        parts.push(el("h3", { class: "work-sub" }, t("teamreview.whatItFound")));
        parts.push(el("ul", { class: "team-findings" }, ...review.findings.map((finding) => el("li", { dataset: { kind: finding.kind } }, findingWords(finding)))));
        if (review.proposals.length > 0) {
          parts.push(el("h3", { class: "work-sub" }, t("teamreview.proposals")));
          parts.push(...review.proposals.map(proposalCard));
        }
      }
    }
    holder.replaceChildren(el("div", { class: "card work-card team-review" }, ...parts));
  }

  const element = el(
    "section",
    { class: "agents-part team-review-part", "aria-label": t("teamreview.title") },
    el("div", { class: "agents-part-head" }, el("h2", { class: "section-label" }, t("teamreview.title"))),
    holder,
  );
  return {
    element,
    reload,
    dispose() {
      gone = true;
      clearTimeout(poll);
    },
  };
}

// Settings › Self-repair (items 919-921): the book of Dot's own problems, as its record shows them, and the sessions
// Claude Code (signed in on this Mac with the owner's plan) spent on them each night: how each ended, its pull request
// and what it used. A night can be started by hand. Whether it is on, when, and how it merges are `[repair]` in the
// config file.

import { api } from "../api.js";
import { ago, el } from "../dom.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, loadingPanel, setCard, setRow, setSection } from "../panels.js";
import { attempt, toast } from "../ui.js";

const STATUS = {
  open: "Open",
  working: "Being fixed",
  fixed: "Fixed",
  waiting: "Waits for you",
  declined: "Not Dot's",
};
const ENDS = {
  merged: "Merged",
  merged_unpulled: "Merged",
  ready: "Ready for you",
  review_blocked: "Review stopped it",
  main_moved: "Main moved: look",
  out_of_time: "Night ran out",
  needs_owner: "Needs you",
  declined: "Not a defect",
  checks_failed: "Checks failed",
  no_answer: "No answer",
  limited: "Plan limit",
  failed: "Failed",
};

/** A link to a pull request, or nothing. */
function prLink(url) {
  return url === "" ? null : el("a", { class: "btn sm", href: url, target: "_blank", rel: "noopener" }, icon("external"), "Pull request");
}

/** The row of a problem: its words, how often and how lately it was seen, its status and its pull request. */
function problemRow(problem) {
  const seen = `${problem.kind.replace("_", " ")} · seen ${problem.count}× · last ${ago(problem.last_at)}`;
  return setRow({
    title: problem.title,
    lead: problem.note === "" ? seen : `${seen} · ${problem.note}`,
    control: el("span", { class: "repair-status" }, el("span", { class: `badge repair-${problem.status}` }, STATUS[problem.status] ?? problem.status), prLink(problem.pr_url)),
  });
}

/** The row of a session: when, how it ended, its rounds and what it used, and its pull request. */
function sessionRow(session, titles) {
  const used = `${session.rounds} round${session.rounds === 1 ? "" : "s"} · ${Math.round((session.input_tokens + session.output_tokens) / 1000)}k tokens · ${session.account}`;
  return setRow({
    title: titles.get(session.problem_id) ?? session.problem_id,
    lead: `${ago(session.started_at)} · ${used}${session.note === "" ? "" : ` · ${session.note}`}`,
    control: el("span", { class: "repair-status" }, el("span", { class: "badge" }, ENDS[session.end] ?? session.end), prLink(session.pr_url)),
  });
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  const holder = el("div", { class: "stack" }, loadingPanel());

  async function runNow() {
    view = await api.post("/repair/now", {});
    toast("ok", "A night of repairs started: Claude Code works on the first problem.");
    paint();
  }

  function paint() {
    const on = view.repo !== "";
    const state = setRow({
      title: on ? `On: ${view.time} each night, ${view.merge === "on_pass" ? "merged once every check passes" : "merged by you"}` : "Off",
      lead: on
        ? `Repairs ${view.repo}, ${view.sessions_per_day} sessions a day at most.`
        : "Set [repair] repo to the checkout of Dot's code to turn it on.",
      control: on
        ? el(
            "button",
            { class: "btn sm", type: "button", disabled: view.running || view.account === null, onclick: () => attempt(runNow) },
            icon("play"),
            view.running ? "Working…" : "Run now",
          )
        : null,
    });
    const account = setRow({
      title: "Claude Code",
      lead: view.account === null ? "Not signed in on this Mac: run claude once and sign in." : `${view.account.email}${view.account.organization === "" ? "" : ` · ${view.account.organization}`}`,
    });
    const titles = new Map(view.problems.map((problem) => [problem.id, problem.title]));
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections", role: "region", "aria-label": "Self-repair" },
        setSection({ title: "Self-repair", body: setCard(state, account) }),
        setSection({
          title: "Problems",
          lead: "What Dot's record shows going wrong again and again.",
          body: view.problems.length === 0 ? emptyPanel("check", "No problems", "Dot has found nothing going wrong again and again.") : setCard(...view.problems.map(problemRow)),
        }),
        setSection({
          title: "Sessions",
          body: view.sessions.length === 0 ? emptyPanel("clock", "No sessions yet", "") : setCard(...view.sessions.map((session) => sessionRow(session, titles))),
        }),
      ),
    );
  }

  async function load() {
    try {
      view = await api.get("/repair");
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    if (!disposed) {
      paint();
    }
  }

  ctx.head.setLead("Each night Claude Code fixes a problem Dot found in its own record, with a test and a pull request.");
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

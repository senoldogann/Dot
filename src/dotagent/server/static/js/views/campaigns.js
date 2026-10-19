// Campaigns: goals that Dot keeps working on in sessions, also while you are away, until a check confirms the outcome or
// you stop it. The list shows where each campaign stands, with buttons to stop it, mark it achieved or delete it; a
// campaign's page shows its targets, the questions its sessions left for you, its ledger and its sessions, with the
// controls to stop it, let it go on, mark it achieved or delete it.

import { actorFace } from "../actors.js";
import { api } from "../api.js";
import { approvalCard, fromRecord, LEFT_IN_CAMPAIGN } from "../approvals.js";
import { ago, clip, compact, dateTime, debounce, el, modelName, plural } from "../dom.js";
import { askedLabel } from "../efforts.js";
import { icon } from "../icons.js";
import { emptyPanel, errorPanel, loadingPanel, pageHead, statusBadge } from "../panels.js";
import { approvalLabel } from "../runmodes.js";
import { onChange, onLifecycle, onStreamBack } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

// A session starting or ending, or a question asked or decided, changes what the pages show. While a session works, they
// also follow the status refresh (every 15 s), which brings the entries it writes to the ledger.
const LIFECYCLE_KINDS = new Set(["run_started", "run_finished", "approval_requested", "approval_decided"]);
// The questions of a session that wait for the owner: one the session waits for, or one it left and went on without.
const WAITING_STATUSES = new Set(["pending", "left"]);
const RELOAD_DELAY_MS = 400;
const STATUS_TONES = { active: "b-info", achieved: "b-ok", stopped: "b-muted" };
// The face a campaign wears in its row (item 83): working while active, at rest once achieved or stopped.
const CAMPAIGN_FACE = { active: "working", achieved: "idle", stopped: "idle" };
const ENTRY_TONES = { progress: "b-ok", setback: "b-bad", strategy: "b-info", question: "b-warn", note: "b-muted" };
const TITLE_CHARS = 70;
const PREVIEW_CHARS = 220;
const RESULT_CHARS = 300;

function backLink() {
  return el("a", { class: "back", href: "#/campaigns" }, icon("arrowLeft"), "All campaigns");
}

function campaignBadge(status) {
  return el("span", { class: `badge ${STATUS_TONES[status] ?? "b-muted"}` }, status);
}

/** The measures of a ledger entry as chips: `leads 12`. */
function numberChips(numbers) {
  return Object.entries(numbers).map(([name, value]) => el("span", { class: "chip" }, `${value} ${name.replaceAll("_", " ")}`));
}

/** Where an active campaign stands: the session working now, or when the next one starts. */
function nextStep(record, working, nextLocal) {
  if (record.status !== "active") {
    return null;
  }
  if (working !== null) {
    return el("a", { href: `#/runs/${working}` }, "A session is working");
  }
  return el("span", null, nextLocal === null ? "No session planned" : `Next session ${nextLocal}`);
}

/** Reload a page soon after a session starts or ends or a question is asked or decided, and with the status refresh
 * while `isWorking()`; returns the function that stops it. */
function followUpdates(reload, isWorking) {
  let stopped = false;
  const reloadSoon = debounce(() => {
    if (!stopped) {
      reload();
    }
  }, RELOAD_DELAY_MS);
  const stopLifecycle = onLifecycle((event) => {
    if (LIFECYCLE_KINDS.has(event.kind)) {
      reloadSoon();
    }
  });
  const stopBack = onStreamBack(reloadSoon);
  const stopChange = onChange(() => {
    if (isWorking()) {
      reloadSoon();
    }
  });
  return () => {
    stopped = true;
    stopLifecycle();
    stopBack();
    stopChange();
  };
}

// ------------------------------------------------------------------------------------------------- what you do to one

// Each asks first, says how it went, and then calls `done`, which shows the campaign as it is now.

async function stopCampaign(campaignId, done) {
  const confirmed = await confirmDialog({
    title: "Stop this campaign?",
    message: "No session starts after this, and a session that is working now is stopped. You can let it go on later.",
    confirmLabel: "Stop",
    danger: true,
    focusCancel: true,
  });
  if (confirmed) {
    await api.post(`/campaigns/${campaignId}/stop`, null);
    toast("ok", "Campaign stopped.");
    await done();
  }
}

async function markAchieved(campaignId, done) {
  const confirmed = await confirmDialog({
    title: "Mark this campaign achieved?",
    message: "Nothing checks it: your word closes the campaign, and a session that is working now is stopped.",
    confirmLabel: "Mark achieved",
    danger: false,
    focusCancel: false,
  });
  if (confirmed) {
    await api.post(`/campaigns/${campaignId}/achieved`, null);
    toast("ok", "Campaign marked achieved.");
    await done();
  }
}

async function deleteCampaign(record, done) {
  const confirmed = await confirmDialog({
    title: "Delete this campaign?",
    message: `“${clip(record.title, TITLE_CHARS)}” is deleted with its ledger and its sessions after the first, with all they hold. The conversation it started in stays in your chats. This cannot be undone.`,
    confirmLabel: "Delete",
    danger: true,
    focusCancel: true,
  });
  if (confirmed) {
    await api.del(`/campaigns/${record.id}`);
    toast("ok", "Campaign deleted.");
    await done();
  }
}

// ------------------------------------------------------------------------------------------------- the list

function rowButton(shape, title, label, action) {
  return el("button", { class: "btn ghost sm icon-only", type: "button", title, "aria-label": label, onclick: () => attempt(action) }, icon(shape));
}

/** A campaign's buttons in the list: Stop while it is active, Mark achieved until it is achieved, and Delete once it is
 * not active and none of its sessions works. `done` repaints the list. */
function rowActions(summary, done) {
  const { record } = summary;
  const name = clip(record.title, TITLE_CHARS);
  return el(
    "div",
    { class: "row-actions" },
    record.status === "active" ? rowButton("stop", "Stop the campaign", `Stop ${name}`, () => stopCampaign(record.id, done)) : null,
    record.status === "achieved" ? null : rowButton("check", "Mark the campaign achieved", `Mark ${name} achieved`, () => markAchieved(record.id, done)),
    record.status === "active" || summary.working !== null ? null : rowButton("trash", "Delete the campaign", `Delete ${name}`, () => deleteCampaign(record, done)),
  );
}

function campaignRow(summary, done) {
  const { record, progress } = summary;
  return el(
    "div",
    { class: "list-row", dataset: { campaign: record.id } },
    actorFace(record.id, CAMPAIGN_FACE[record.status] ?? "idle", null),
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row" }, el("a", { class: "row-title", href: `#/campaigns/${record.id}` }, record.title), campaignBadge(record.status)),
      el("div", { class: "row-meta" }, el("span", null, plural(summary.sessions, "session", "sessions")), nextStep(record, summary.working, summary.next_local)),
      progress === null
        ? el("div", { class: "row-text faint" }, "No progress recorded yet")
        : el(
            "div",
            { class: "row-meta" },
            el("span", { class: "row-text muted" }, `${ago(progress.ts)}: ${clip(progress.text, PREVIEW_CHARS)}`),
            numberChips(progress.numbers),
          ),
    ),
    rowActions(summary, done),
  );
}

function mountList(root) {
  let disposed = false;
  let ticket = 0;
  let working = false;
  const holder = el("div", null, loadingPanel());

  async function reload() {
    const mine = ++ticket;
    try {
      const summaries = await api.get("/campaigns");
      if (disposed || mine !== ticket) {
        return;
      }
      working = summaries.some((summary) => summary.working !== null);
      holder.replaceChildren(
        summaries.length === 0
          ? emptyPanel("No campaigns yet", "Switch on Keep going when you start a task, and Dot works on it until the outcome is verified.")
          : el("div", { class: "card" }, summaries.map((summary) => campaignRow(summary, reload))),
      );
    } catch (error) {
      if (!disposed && mine === ticket) {
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  root.append(pageHead("Campaigns", "Goals that Dot keeps working on in sessions until a check confirms the outcome.", null), holder);
  const stopFollowing = followUpdates(reload, () => working);
  attempt(reload);
  return () => {
    disposed = true;
    stopFollowing();
  };
}

// ------------------------------------------------------------------------------------------------- the page

function textBlock(label, text) {
  return el("div", { class: "stack tight" }, el("div", { class: "section-label" }, label), el("div", { class: "row-text" }, text));
}

function outline(detail) {
  const { record } = detail;
  const approvals = el(
    "span",
    { class: record.approvals === "yolo" ? "badge b-warn" : "chip", title: "Who answers the safety policy's questions in its sessions" },
    `Approvals: ${approvalLabel(record.approvals)}`,
  );
  return el(
    "div",
    { class: "card card-pad stack" },
    textBlock("Goal", record.goal),
    record.targets === ""
      ? textBlock("Targets", "Not set yet: the first session records them.")
      : el("div", { class: "stack" }, textBlock("Targets", record.targets), textBlock("Verified by", record.verification)),
    record.status === "achieved" ? el("div", { class: "note ok" }, icon("check"), el("span", null, record.evidence)) : null,
    el(
      "div",
      { class: "row-meta" },
      el("span", { class: "chip", title: record.model }, modelName(record.model)),
      el(
        "span",
        { class: "chip", title: "The Chrome its sessions work in" },
        record.browser === "attach" ? "Browser: your Chrome" : record.browser === "own" ? "Browser: Dot's own" : "Browser: the daemon's",
      ),
      el("span", { class: "chip" }, `Reasoning ${askedLabel(record.effort, record.once)}`),
      approvals,
      el("span", { class: "chip", title: "Tokens of its sessions and of the runs they delegated to" }, `${compact(detail.tokens)} tokens`),
      el("span", { class: "faint" }, `Started ${dateTime(record.created_at)}`),
    ),
  );
}

/** `numbers` maps the id of each session to its number, for the link to the session that wrote the entry. */
function entryRow(entry, numbers) {
  return el(
    "div",
    { class: "list-row" },
    el(
      "div",
      { class: "row-main" },
      el(
        "div",
        { class: "row-meta" },
        el("span", { class: `badge ${ENTRY_TONES[entry.kind] ?? "b-muted"}` }, entry.kind),
        el("span", { class: "faint", title: dateTime(entry.ts) }, ago(entry.ts)),
        entry.run_id === null ? null : el("a", { class: "small", href: `#/runs/${entry.run_id}` }, `Session ${numbers.get(entry.run_id) ?? ""}`.trim()),
      ),
      el("div", { class: "row-text" }, entry.text),
      Object.keys(entry.numbers).length === 0 ? null : el("div", { class: "row-meta" }, numberChips(entry.numbers)),
    ),
  );
}

function sessionRow(session, number) {
  return el(
    "div",
    { class: "list-row" },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row" }, el("a", { class: "row-title", href: `#/runs/${session.id}` }, `Session ${number}`), statusBadge(session.status)),
      el("div", { class: "row-meta" }, el("span", { class: "faint" }, dateTime(session.created_at)), el("span", { class: "faint" }, `${compact(session.input_tokens + session.output_tokens)} tokens`)),
      session.result === "" ? null : el("div", { class: "row-text muted" }, clip(session.result, RESULT_CHARS)),
    ),
  );
}

function decidedRow(question) {
  const answers = { approved: `Answered: ${question.note}`, denied: "Skipped", expired: "Not answered while the session waited" };
  const answer = answers[question.status] ?? question.status;
  return el(
    "div",
    { class: "list-row" },
    el("div", { class: "row-main" }, el("div", { class: "row-text" }, question.summary), el("div", { class: "row-text muted" }, answer)),
  );
}

function section(label, rows, emptyText) {
  return el(
    "div",
    { class: "stack tight" },
    el("div", { class: "section-label" }, label),
    el("div", { class: "card" }, rows.length === 0 ? el("div", { class: "list-row" }, el("div", { class: "row-text faint" }, emptyText)) : rows),
  );
}

function mountPage(root, ctx, campaignId) {
  let disposed = false;
  let ticket = 0;
  let working = false;
  const holder = el("div", null, loadingPanel());
  // The page keeps its parts, and a reload repaints all but the cards of the questions waiting for you: what you type
  // in one, or the answer it is sending, stays.
  const top = el("div", { class: "stack" });
  const waiting = el("div", { class: "stack tight", hidden: true }, el("div", { class: "section-label" }, "Waiting for you"));
  const rest = el("div", { class: "stack" });
  const page = el("div", { class: "stack" }, top, waiting, rest);
  const questionCards = new Map();
  // The detail last painted, as JSON: one that did not change is not painted again. The head keeps its buttons until
  // the title or the status changes, or whether a campaign that is not active can be deleted (no session of it works),
  // so a press on Stop while a session works is not lost to a repaint.
  let painted = "";
  let head = null;

  async function goOn() {
    await api.post(`/campaigns/${campaignId}/resume`, null);
    toast("ok", "The campaign goes on.");
    await reload();
  }

  /** `deletable` is whether the campaign is not active and none of its sessions works, as its row in the list asks. */
  function controls(record, deletable) {
    return el(
      "div",
      { class: "cluster" },
      record.status === "active"
        ? el(
            "button",
            {
              class: "btn danger",
              type: "button",
              title: "Stop the campaign",
              "aria-label": "Stop the campaign",
              onclick: () => attempt(() => stopCampaign(campaignId, reload)),
            },
            icon("stop"),
            el("span", { class: "label" }, "Stop"),
          )
        : el(
            "button",
            {
              class: "btn primary",
              type: "button",
              title: "Let the campaign go on: its next session starts within a minute",
              "aria-label": "Let the campaign go on",
              onclick: () => attempt(goOn),
            },
            icon("arrowRight"),
            el("span", { class: "label" }, "Go on"),
          ),
      record.status === "achieved"
        ? null
        : el(
            "button",
            {
              class: "btn",
              type: "button",
              title: "Mark the campaign achieved",
              "aria-label": "Mark the campaign achieved",
              onclick: () => attempt(() => markAchieved(campaignId, reload)),
            },
            icon("check"),
            el("span", { class: "label" }, "Mark achieved"),
          ),
      deletable
        ? el(
            "button",
            {
              class: "btn danger",
              type: "button",
              title: "Delete the campaign",
              "aria-label": "Delete the campaign",
              onclick: () => attempt(() => deleteCampaign(record, () => ctx.navigate("/campaigns"))),
            },
            icon("trash"),
            el("span", { class: "label" }, "Delete"),
          )
        : null,
    );
  }

  /** `open` are the questions that wait for you, oldest first; a card that is there already stays as it is. */
  function paintQuestions(open) {
    const ids = new Set(open.map((question) => question.id));
    for (const [id, node] of questionCards) {
      if (!ids.has(id)) {
        node.remove();
        questionCards.delete(id);
      }
    }
    for (const question of open) {
      if (!questionCards.has(question.id)) {
        const card = approvalCard({
          approval: fromRecord(question),
          heading: el("a", { class: "small", href: `#/runs/${question.run_id}` }, "Open session"),
          onSettled: () => attempt(reload),
          leftNote: LEFT_IN_CAMPAIGN,
        });
        questionCards.set(question.id, card.node);
        waiting.append(card.node);
      }
    }
    waiting.hidden = questionCards.size === 0;
  }

  function paintTop(detail) {
    const { record } = detail;
    const meta = el("div", { class: "row-meta" }, campaignBadge(record.status), el("span", null, plural(detail.sessions.length, "session", "sessions")), nextStep(record, detail.working, detail.next_local));
    const deletable = record.status !== "active" && detail.working === null;
    const key = `${record.status}\n${record.title}\n${deletable}`;
    if (head === null || head.key !== key) {
      head = { key, node: pageHead(record.title, "", controls(record, deletable)) };
      top.replaceChildren(head.node, meta, outline(detail));
      return;
    }
    while (head.node.nextSibling !== null) {
      head.node.nextSibling.remove();
    }
    head.node.after(meta, outline(detail));
  }

  function paint(detail) {
    const shape = JSON.stringify(detail);
    if (shape === painted) {
      return;
    }
    painted = shape;
    const { record } = detail;
    ctx.setTitle(clip(record.title, TITLE_CHARS));
    working = detail.working !== null;
    const decided = detail.questions.filter((question) => !WAITING_STATUSES.has(question.status));
    const numbers = new Map(detail.sessions.map((session, index) => [session.id, index + 1]));
    paintTop(detail);
    paintQuestions(detail.questions.filter((question) => WAITING_STATUSES.has(question.status)));
    rest.replaceChildren(
      section(
        "Ledger",
        [...detail.entries].reverse().map((entry) => entryRow(entry, numbers)),
        "Empty: the sessions write what they did here.",
      ),
      section(
        "Sessions",
        detail.sessions.map((session, index) => sessionRow(session, index + 1)).reverse(),
        "None yet.",
      ),
      ...(decided.length === 0 ? [] : [section("Earlier questions", decided.map(decidedRow).reverse(), "")]),
    );
    if (page.parentNode !== holder) {
      holder.replaceChildren(page);
    }
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const detail = await api.get(`/campaigns/${campaignId}`);
      if (!disposed && mine === ticket) {
        paint(detail);
      }
    } catch (error) {
      if (!disposed && mine === ticket) {
        painted = "";
        holder.replaceChildren(errorPanel(error, () => attempt(reload)));
      }
    }
  }

  root.append(backLink(), holder);
  const stopFollowing = followUpdates(reload, () => working);
  attempt(reload);
  return () => {
    disposed = true;
    stopFollowing();
  };
}

/** The list of campaigns (`#/campaigns`) or the page of one (`#/campaigns/<id>`). */
export function mount(root, ctx) {
  const campaignId = ctx.params[0];
  return campaignId === undefined ? mountList(root) : mountPage(root, ctx, campaignId);
}

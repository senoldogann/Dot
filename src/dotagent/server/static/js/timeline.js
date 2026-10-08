// The conversation of one run, built from its events as they arrive: what the agent said, which tools it used, what it
// asked, what the delegated runs did and, at the end, the answer. Events of a delegated run land in a panel nested in the
// tool call that started it. The tool calls between two messages sit in one group, "Worked for 12 s · 5 tool calls", that the
// reader can fold away. A group is timed from the moment the work began (the start of the run or the last message) to the
// moment it ended, so the groups of a run add up to the time the run took.

import { approvalCard, fromEvent } from "./approvals.js";
import { sentFiles } from "./attachments.js";
import { clip, compact, dateTimeFull, duration, el, plural } from "./dom.js";
import { icon } from "./icons.js";
import { foldSteps } from "./layout.js";
import { answerMessage, userMessage } from "./messages.js";
import { prose } from "./prose.js";
import { describeArguments, toolRow } from "./tools.js";

const GOAL_CHARS = 160;
const LATEST_CHARS = 140;
// The tool that ends a run. Its answer is shown as the answer, so the call itself stays out of the steps unless it failed.
const FINISH_TOOL = "finish";
const VERDICT_TEXT = { ask: "Needs approval", handoff: "Handed to you", deny: "Blocked by policy" };
// The ways a model turn ends that need no word of their own.
const ORDINARY_ENDS = new Set(["stop", "tool_calls", ""]);

/** Whether two texts read the same, whatever their line breaks and spacing. */
function sameText(first, second) {
  return first.replace(/\s+/g, " ").trim() === second.replace(/\s+/g, " ").trim();
}

function newBubble(container, step, live) {
  const body = el("div", { class: live ? "stream" : "" });
  const node = el("div", { class: live ? "bubble live" : "bubble", role: "article", "aria-label": "Dot" }, body);
  container.append(node);
  return { node, body, step, text: "" };
}

/** The one line a live group shows for the call that ran last. */
function describeCall(tool, args) {
  const detail = describeArguments(args);
  return clip(detail === "" ? tool : `${tool} · ${detail}`, LATEST_CHARS);
}

/** An empty group of tool steps that starts at `ts` (seconds); it is "working" until something ends it. */
function buildGroup(ts) {
  const state = el("span", { class: "steps-state" });
  const title = el("span", { class: "steps-title" });
  const count = el("span", { class: "steps-count" });
  const failed = el("span", { class: "badge b-warn" });
  const latest = el("span", { class: "steps-latest" });
  const body = el("div", { class: "steps-body" });
  const head = el("summary", { class: "steps-head" }, state, title, count, failed, el("span", { class: "chev" }, icon("chevron")), latest);
  const node = el("details", { class: "steps", open: !foldSteps() }, head, body);
  return { node, body, state, title, count, failed, latest, live: null, tail: true, pending: 0, calls: 0, failedCalls: 0, start: ts, end: ts, longest: 0, last: "" };
}

/** Show a group as it stands: "Working…" while it is the tail of the run or a call is out, "Worked for …" once it is over. */
function paintGroup(group) {
  const live = group.tail || group.pending > 0;
  if (group.live !== live) {
    group.live = live;
    group.state.replaceChildren(live ? el("span", { class: "spinner" }) : icon("check"));
  }
  group.title.textContent = live ? "Working…" : `Worked for ${duration(Math.max(group.end - group.start, group.longest))}`;
  group.count.textContent = `· ${plural(group.calls, "tool call", "tool calls")}`;
  group.failed.hidden = group.failedCalls === 0;
  group.failed.textContent = `${group.failedCalls} failed`;
  group.latest.hidden = !live;
  group.latest.textContent = live ? group.last : "";
}

/**
 * The timeline of the run `rootRunId`, which was created at `startedAt` (seconds). `push(record)` takes events in order; the
 * callbacks tell the page about what the timeline cannot show by itself: `onGrow()` after every event, `onPlan(steps)`,
 * `onStats({ step, input, output })` after every model turn, `onCalls({ calls, failed })` after every tool call of the run,
 * `onRunStatus({ status, model, endedAt })` (`endedAt` is null until the run is over), `onAnswer(node)` when the answer is
 * on the page and `onImage(name)` for every screenshot a tool returned.
 */
export function createTimeline({ rootRunId, startedAt, onGrow, onPlan, onStats, onCalls, onRunStatus, onAnswer, onImage }) {
  const element = el("div", { class: "timeline" });
  const scopes = new Map();
  const cards = new Map();
  const totals = { calls: 0, failed: 0 };

  // `began` is when the run started; `boundary` is when the work since the last message started.
  function makeScope({ runId, container, panel, parent, began }) {
    return {
      runId,
      container,
      panel,
      parent,
      began,
      boundary: began,
      calls: new Map(),
      finishing: new Map(),
      group: null,
      groups: [],
      live: null,
      thinking: null,
      reply: null,
      touched: false,
    };
  }

  scopes.set(rootRunId, makeScope({ runId: rootRunId, container: element, panel: null, parent: null, began: startedAt }));

  /** The group is over at `ts`: whatever comes next starts a new one. */
  function endGroup(scope, ts) {
    if (scope.group !== null) {
      scope.group.tail = false;
      scope.group.end = Math.max(scope.group.end, ts);
      paintGroup(scope.group);
      scope.group = null;
    }
  }

  /** Put something the reader must see at the end of the scope. It ends the group before it, so it never hides in a fold. */
  function place(scope, node, ts) {
    endGroup(scope, ts);
    scope.container.append(node);
    scope.boundary = ts;
  }

  /**
   * A turn that ends with a plain reply has that text twice: as its last message and as the result of the run. The result
   * is shown as the answer, so the message that repeats it goes, provided nothing came after that message.
   */
  function dropRepeatedReply(scope, result) {
    const reply = scope.reply;
    scope.reply = null;
    if (reply !== null && sameText(reply.text, result) && scope.container.lastElementChild === reply.node) {
      reply.node.remove();
    }
  }

  /** The run is over: no group works any more, even when a call never reported back. */
  function settleGroups(scope, ts) {
    endGroup(scope, ts);
    for (const group of scope.groups) {
      group.pending = 0;
      paintGroup(group);
    }
  }

  function groupFor(scope) {
    if (scope.group === null) {
      const group = buildGroup(scope.boundary);
      scope.group = group;
      scope.groups.push(group);
      scope.container.append(group.node);
    }
    return scope.group;
  }

  function note(scope, ts, iconName, text, tone) {
    place(scope, el("div", { class: tone === "" ? "note" : `note ${tone}` }, icon(iconName), el("span", null, text)), ts);
  }

  function childScope(runId, data, ts) {
    const parent = scopes.get(data.parent_run_id) ?? scopes.get(rootRunId);
    const status = el("span", { class: "badge s-running" }, "running");
    const meta = el("span", { class: "chip" }, data.mode ?? "task");
    const inner = el("div", { class: "timeline" });
    const summary = el(
      "summary",
      null,
      el("span", { class: "chev" }, icon("chevron")),
      icon("branch"),
      el("span", null, "Delegated"),
      el("span", { class: "truncate grow muted" }, clip(data.goal ?? "", GOAL_CHARS)),
      meta,
      status,
    );
    const node = el("details", { class: "child", open: true }, summary, inner);
    summary.addEventListener("click", () => {
      scope.touched = true;
    });
    place(parent, node, ts);
    const scope = makeScope({ runId, container: inner, panel: { node, status, meta }, parent, began: ts });
    scopes.set(runId, scope);
    return scope;
  }

  function dropThinking(scope) {
    if (scope.thinking !== null) {
      scope.thinking.node.remove();
      scope.thinking = null;
    }
  }

  function markWaiting(scope, waiting) {
    if (scope.runId === rootRunId) {
      onRunStatus({ status: waiting ? "blocked" : "running", model: null, endedAt: null });
      return;
    }
    scope.panel.status.className = `badge s-${waiting ? "blocked" : "running"}`;
    scope.panel.status.textContent = waiting ? "waiting" : "running";
  }

  function showThinking(scope, characters) {
    if (scope.thinking === null) {
      const count = el("span", { class: "faint" });
      const node = el("div", { class: "thinking" }, icon("sparkle"), el("span", { class: "label" }, "Thinking…"), count);
      scope.container.append(node);
      scope.thinking = { node, count, characters: 0 };
    }
    scope.thinking.characters += characters;
    scope.thinking.count.textContent = `${compact(scope.thinking.characters)} characters`;
  }

  /** Add a tool call as a row to the group it belongs to; `call` is `{ id, tool, args }`. */
  function showCall(scope, call) {
    const group = groupFor(scope);
    const row = toolRow({ tool: call.tool, args: call.args });
    group.body.append(row.node);
    group.calls += 1;
    group.pending += 1;
    group.last = describeCall(call.tool, call.args);
    const entry = { row, group };
    scope.calls.set(call.id, entry);
    if (scope.runId === rootRunId) {
      totals.calls += 1;
      onCalls({ calls: totals.calls, failed: totals.failed });
    }
    paintGroup(group);
    return entry;
  }

  const handlers = {
    run_started(scope, record) {
      if (scope.runId === rootRunId) {
        onRunStatus({ status: "running", model: record.data.model, endedAt: null });
        return;
      }
      scope.panel.meta.textContent = `${scope.panel.meta.textContent} · ${record.data.model}`;
    },

    run_finished(scope, record) {
      const { status, result } = record.data;
      const isRoot = scope.runId === rootRunId;
      settleGroups(scope, record.ts);
      dropRepeatedReply(scope, result);
      const answer = answerMessage({
        status,
        text: result,
        endedAt: record.ts,
        startedAt: scope.began,
        href: isRoot ? null : `#/runs/${scope.runId}`,
      });
      place(scope, answer, record.ts);
      if (isRoot) {
        onAnswer(answer);
        onRunStatus({ status, model: null, endedAt: record.ts });
        return;
      }
      scope.panel.status.className = `badge s-${status}`;
      scope.panel.status.textContent = status;
      // The parent resumes when the delegated run is over; the time in between was spent on the delegation.
      scope.parent.boundary = record.ts;
      if (status === "done" && !scope.touched) {
        scope.panel.node.open = false;
      }
    },

    assistant(scope, record) {
      const data = record.data;
      dropThinking(scope);
      const bubble = scope.live !== null && scope.live.step === data.step ? scope.live : null;
      scope.live = null;
      const hasText = data.text.trim() !== "";
      if (!hasText) {
        if (bubble !== null) {
          bubble.node.remove();
        }
        if (scope.runId === rootRunId) {
          onStats({ step: data.step, input: data.input_tokens, output: data.output_tokens });
        }
        return;
      }
      // A message ends the group of steps before it: the steps that follow it make a new one.
      endGroup(scope, record.ts);
      scope.boundary = record.ts;
      const target = bubble ?? newBubble(scope.container, data.step, false);
      target.node.className = "bubble";
      target.body.replaceWith(prose(data.text));
      scope.reply = { node: target.node, text: data.text };
      if (!ORDINARY_ENDS.has(data.finish)) {
        target.node.append(el("div", { class: "small muted" }, `This message ended early: ${data.finish}`));
      }
      if (scope.runId === rootRunId) {
        onStats({ step: data.step, input: data.input_tokens, output: data.output_tokens });
      }
    },

    delta(scope, record) {
      const data = record.data;
      if (data.kind === "thinking") {
        showThinking(scope, data.text.length);
        return;
      }
      if (data.kind === "reset") {
        if (scope.live !== null) {
          scope.live.node.remove();
          scope.live = null;
        }
        return;
      }
      dropThinking(scope);
      if (scope.live === null || scope.live.step !== data.step) {
        scope.live = newBubble(scope.container, data.step, true);
      }
      scope.live.text += data.text;
      scope.live.body.textContent = scope.live.text;
    },

    tool_call(scope, record) {
      const data = record.data;
      if (data.tool === FINISH_TOOL) {
        scope.finishing.set(data.call_id, data.args);
        return;
      }
      showCall(scope, { id: data.call_id, tool: data.tool, args: data.args });
    },

    tool_result(scope, record) {
      const data = record.data;
      const finishArgs = scope.finishing.get(data.call_id);
      if (finishArgs !== undefined) {
        scope.finishing.delete(data.call_id);
        if (data.ok) {
          return;
        }
        showCall(scope, { id: data.call_id, tool: data.tool, args: finishArgs });
      }
      const entry = scope.calls.get(data.call_id) ?? showCall(scope, { id: data.call_id, tool: data.tool, args: {} });
      const { row, group } = entry;
      row.finish(data);
      group.pending = Math.max(0, group.pending - 1);
      group.end = Math.max(group.end, record.ts);
      group.longest = Math.max(group.longest, data.seconds);
      if (!data.ok) {
        group.failedCalls += 1;
        if (scope.runId === rootRunId) {
          totals.failed += 1;
          onCalls({ calls: totals.calls, failed: totals.failed });
        }
      }
      paintGroup(group);
      for (const name of data.images) {
        onImage(name);
      }
    },

    policy(scope, record) {
      const data = record.data;
      const verdict = VERDICT_TEXT[data.verdict] ?? data.verdict;
      note(
        scope,
        record.ts,
        "shield",
        `${verdict}: ${clip(data.summary, 200)} — ${clip(data.reason, 240)}`,
        data.verdict === "deny" ? "bad" : "warn",
      );
    },

    approval_requested(scope, record) {
      const card = approvalCard({ approval: fromEvent(record.run_id, record.data), heading: null, onSettled: () => undefined });
      cards.set(record.data.approval_id, { card, scope });
      place(scope, card.node, record.ts);
      markWaiting(scope, true);
    },

    approval_decided(scope, record) {
      const entry = cards.get(record.data.approval_id);
      if (entry !== undefined) {
        entry.card.settle(record.data.status, record.data.note);
        // The time spent waiting for the decision was not spent working.
        entry.scope.boundary = record.ts;
        markWaiting(entry.scope, false);
      }
    },

    // An action that nobody was asked about (YOLO) or that the review model answered. The line goes into the group of the call
    // it belongs to, so a run in YOLO mode keeps its steps together; the stored approval has the same facts.
    approval_auto(scope, record) {
      const data = record.data;
      const yolo = data.decided_by === "yolo";
      const denied = data.status === "denied";
      const verdict = yolo ? "allowed without asking" : denied ? "denied" : "approved";
      const reason = data.note === "" ? "" : ` — ${clip(data.note, 240)}`;
      const text = `${yolo ? "YOLO" : "Reviewer"} ${verdict}: ${clip(data.summary, 200)}${reason}`;
      const tone = denied ? "auto-decision denied" : yolo ? "auto-decision yolo" : "auto-decision";
      groupFor(scope).body.append(el("div", { class: tone }, icon(yolo ? "bolt" : "shield"), el("span", null, text)));
    },

    // The checker of goal mode compared the answer with the goal: `final` says whether the run ends with this verdict.
    goal_check(scope, record) {
      const { decision, reason, check, patience, final } = record.data;
      const because = reason === "" ? "" : ` — ${clip(reason, 300)}`;
      const lines = {
        achieved: ["ok", "Goal check: the goal is reached"],
        continue: final
          ? ["bad", `Goal check: still not reached after ${patience} rounds of further work, so the run stops`]
          : ["warn", `Goal check ${check} of ${patience}: not reached yet, back to work`],
        blocked: ["bad", "Goal check: nothing more can be done without you"],
        error: ["bad", "Goal check: the check could not run"],
      };
      const [tone, text] = lines[decision];
      note(scope, record.ts, "target", `${text}${because}`, tone);
    },

    steer(scope, record) {
      const files = record.data.files ?? [];
      place(scope, userMessage(record.data.text, dateTimeFull(record.ts)), record.ts);
      if (files.length > 0) {
        place(scope, sentFiles(files), record.ts);
      }
    },

    // The files that came with the goal; they sit under the goal's bubble.
    attachments(scope, record) {
      place(scope, sentFiles(record.data.files), record.ts);
    },

    plan(scope, record) {
      const steps = record.data.steps;
      if (scope.runId === rootRunId) {
        onPlan(steps);
        return;
      }
      const done = steps.filter((step) => step.status === "done").length;
      const failed = steps.filter((step) => step.status === "failed").length;
      const failedText = failed > 0 ? `, ${failed} failed` : "";
      note(scope, record.ts, "list", `Plan: ${done} of ${steps.length} steps done${failedText}`, "");
    },

    plan_reminder(scope, record) {
      const numbers = record.data.open_steps.join(", ");
      note(scope, record.ts, "list", `Dot wanted to finish with plan steps ${numbers} still open and was asked to close them first`, "warn");
    },

    memories(scope, record) {
      const count = plural(record.data.ids.length, "memory", "memories");
      const text = record.data.live === true ? `Picked up ${count} saved while the run worked` : `Recalled ${count}`;
      note(scope, record.ts, "database", text, "");
    },

    compacted(scope, record) {
      const data = record.data;
      const parts = [`Context compacted: ${data.summarised_messages} earlier messages were summarised`];
      if (data.model !== undefined) {
        parts.push(data.method === "model" ? `by ${data.model}` : `without a model (${data.problem ?? "no summary"})`);
      }
      const check = data.check;
      if (check !== undefined && check !== null && !check.ok) {
        const unverified = check.unverified.length > 0 ? `unverified: ${check.unverified.join(", ")}` : "";
        const missing = check.missing_sections.length > 0 ? `missing: ${check.missing_sections.join(", ")}` : "";
        parts.push(`check flagged ${[unverified, missing].filter((part) => part !== "").join("; ")}`);
      }
      const tone = data.method === "mechanical" || (check !== undefined && check !== null && !check.ok) ? "warn" : "";
      note(scope, record.ts, "layers", parts.join(" · "), tone);
    },

    // A side question (/btw) and its answer: shown in the conversation, never part of the run's own context.
    aside_question(scope, record) {
      place(scope, el("div", { class: "aside-question", dataset: { aside: record.data.id } }, el("span", { class: "aside-tag" }, "/btw"), el("span", null, record.data.text)), record.ts);
    },

    aside_answer(scope, record) {
      const head = el("div", { class: "aside-head" }, icon("help"), el("span", null, `Side answer by ${record.data.model}; the run did not see this`));
      place(scope, el("div", { class: "aside-answer" }, head, prose(record.data.text)), record.ts);
    },

    aside_failed(scope, record) {
      note(scope, record.ts, "alert", `The side question got no answer from ${record.data.model}: ${record.data.error}`, "bad");
    },

    // The progress watchdog saw that the run is not getting anywhere (`detail`) and answered with a rung of its ladder
    // (`action`); the consult rung brings the advice of the review model, or the `error` that ended the run. An event
    // without `action` comes from the older stall guard, which only counted its `warnings`.
    stall(scope, record) {
      const data = record.data;
      if (data.action === undefined) {
        note(scope, record.ts, "alert", `Dot repeated the same step and was told to change its approach (warning ${data.warnings})`, "warn");
        return;
      }
      const answers = {
        nudge: "Dot was told to change its approach.",
        replan: "Its next step may only plan again or finish.",
        consult: "The review model was asked for a second opinion.",
        ask: "Its next step may only ask you a question or finish.",
        stop: "The run stops; its result says what it tried.",
      };
      const parts = [`Not getting anywhere: ${clip(data.detail, 300)}`, answers[data.action]];
      if (data.error !== undefined) {
        parts.push(`It could not answer (${clip(data.error, 300)}), so the run stops.`);
      }
      if (data.diagnosis !== undefined) {
        parts.push(clip(data.diagnosis, 400));
      }
      const steps = data.next_steps ?? [];
      if (steps.length > 0) {
        parts.push(`Next: ${steps.map((step, index) => `${index + 1}. ${clip(step, 300)}`).join(" ")}`);
      }
      const ends = data.action === "stop" || data.error !== undefined;
      note(scope, record.ts, "alert", parts.join(" "), ends ? "bad" : "warn");
    },
  };

  function push(record) {
    let scope = scopes.get(record.run_id);
    if (scope === undefined) {
      scope = childScope(record.run_id, record.kind === "run_queued" ? record.data : { goal: "", parent_run_id: rootRunId }, record.ts);
    }
    const handler = handlers[record.kind];
    if (handler !== undefined) {
      handler(scope, record);
    }
    onGrow();
  }

  return { element, push };
}

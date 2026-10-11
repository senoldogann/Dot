// The conversation of one run, built from its events as they arrive: what the agent said, which tools it used, what it
// asked, what the delegated runs did and, at the end, the answer. Events of a delegated run land in a panel nested in the
// tool call that started it. The tool calls between two messages sit in one group, "Worked for 12 s" with "5 tool calls" beside it, that the
// reader can fold away. A group is timed from the moment the work began (the start of the run or the last message) to the
// moment it ended, so the groups of a run add up to the time the run took; the part of that time the model's turns took is
// in brackets, "Worked for 12 s (model 8 s)".

import { approvalCard, fromEvent, LEFT_IN_RUN } from "./approvals.js";
import { sentFiles } from "./attachments.js";
import { clip, compact, dateTime, dateTimeFull, duration, el, plural } from "./dom.js";
import { actorFace } from "./actors.js";
import { faceState } from "./face.js";
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
// The words of a delegated run's status ring, for its tooltip and for a screen reader.
const RING_LABELS = { blocked: "waiting" };

/** Show the status of a delegated run on the ring of its chip. */
function paintRing(ring, status) {
  const label = RING_LABELS[status] ?? status;
  ring.className = `status-ring s-${status}`;
  ring.title = label;
  ring.setAttribute("aria-label", label);
}

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

// How a call of the step a resumed run was in ended (agent/resume.py).
const CALL_ENDS = { succeeded: "finished", failed: "failed", cut_off: "cut off", not_started: "not started" };

/** One call of the step a resumed run was in, as "tool how it ended". */
function callEnd(call) {
  const end = `${call.tool} ${CALL_ENDS[call.end] ?? call.end}`;
  return call.end === "cut_off" && !call.repeatable ? `${end} (it may have changed something)` : end;
}

/** What a run spent of each of its limits that is set (agent/limits.py), e.g. "61.2 of 60 minutes and 512k of 500k tokens". */
function limitSpent(data) {
  const parts = [];
  if (data.max_minutes > 0) {
    parts.push(`${(data.seconds / 60).toFixed(1)} of ${data.max_minutes} minutes`);
  }
  if (data.max_tokens > 0) {
    parts.push(`${compact(data.tokens)} of ${compact(data.max_tokens)} tokens`);
  }
  return parts.join(" and ");
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
  return { node, body, state, title, count, failed, latest, live: null, tail: true, pending: 0, calls: 0, failedCalls: 0, cutCalls: 0, start: ts, end: ts, longest: 0, modelSeconds: 0, last: "" };
}

/** Show a group as it stands: "Working…" while it is the tail of the run or a call is out, "Worked for …" once it is over. */
function paintGroup(group) {
  const live = group.tail || group.pending > 0;
  if (group.live !== live) {
    group.live = live;
    group.state.replaceChildren(live ? el("span", { class: "spinner" }) : icon("check"));
  }
  const model = group.modelSeconds > 0 ? ` (model ${duration(group.modelSeconds)})` : "";
  group.title.textContent = live ? "Working…" : `Worked for ${duration(Math.max(group.end - group.start, group.longest))}${model}`;
  group.count.textContent = plural(group.calls, "tool call", "tool calls");
  const troubles = [group.failedCalls > 0 ? `${group.failedCalls} failed` : "", group.cutCalls > 0 ? `${group.cutCalls} cut off` : ""];
  group.failed.hidden = group.failedCalls === 0 && group.cutCalls === 0;
  group.failed.textContent = troubles.filter((text) => text !== "").join(" · ");
  group.latest.hidden = !live;
  group.latest.textContent = live ? group.last : "";
}

/**
 * The timeline of the run `rootRunId`, which was created at `startedAt` (seconds). `push(record)` takes events in order; the
 * callbacks tell the page about what the timeline cannot show by itself: `onGrow()` after every event, `onPlan(steps, targets)`,
 * `onStats({ step, input, output })` after every model turn and every side call of the run (`step` 0: a compaction, a
 * review, a description of images), `onCalls({ calls, failed })` after every tool call of the run,
 * `onRunStatus({ status, model, endedAt })` (`endedAt` is null until the run is over), `onAnswer(node)` when the answer is
 * on the page, `onImage(name)` for every screenshot a tool returned and `onBrowserStep()` for the root run's first
 * browser tool call (Dot's computer starts its view on it).
 */
export function createTimeline({ rootRunId, startedAt, onGrow, onPlan, onStats, onCalls, onRunStatus, onAnswer, onImage, onQuestionLeft, onHeld, onBrowserStep }) {
  const element = el("div", { class: "timeline" });
  const scopes = new Map();
  const cards = new Map();
  let browserSeen = false;
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

  /** The run is over: no group works any more, and a call that never reported back was cut off. */
  function settleGroups(scope, ts) {
    endGroup(scope, ts);
    for (const { row, group } of scope.calls.values()) {
      if (row.settle()) {
        group.cutCalls += 1;
      }
    }
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
    // A run of an agent (item 87, a message to it) wears the agent's face; any other delegated run its own.
    const face = actorFace(data.agent_id ?? runId, "working");
    const status = el("span", { role: "img" });
    paintRing(status, "running");
    // The mode and, once it starts, the model of the delegated run: for a screen reader; the row shows none of it.
    const meta = el("span", { class: "visually-hidden" }, data.mode ?? "task");
    // The last line of the delegated run: its step and tool, then how it ended and how many steps it took.
    const progress = el("span", { class: "child-last small muted meta-facts child-progress" }, el("span", null, "starting"));
    const time = el("span", { class: "child-time" });
    const open = el(
      "a",
      { class: "child-open", href: `#/runs/${runId}`, title: "Open the delegated run's own page", "aria-label": "Open the delegated run" },
      icon("external"),
    );
    open.addEventListener("click", (event) => event.stopPropagation());
    const inner = el("div", { class: "timeline" });
    // One row (item 82): the face with its state at the corner, the goal over the last line, the time it has taken,
    // the way to its page and the chevron; opened, the run's steps hang under it.
    const summary = el(
      "summary",
      { class: "child-row" },
      el("span", { class: "child-face" }, face, status),
      el("span", { class: "visually-hidden" }, "Delegated run:"),
      el("span", { class: "child-main" }, el("span", { class: "child-title" }, clip(data.goal ?? "", GOAL_CHARS)), progress),
      meta,
      time,
      open,
      el("span", { class: "chev" }, icon("chevron")),
    );
    const node = el("details", { class: "child", open: true }, summary, inner);
    summary.addEventListener("click", () => {
      scope.touched = true;
    });
    place(parent, node, ts);
    const scope = makeScope({ runId, container: inner, panel: { node, face, status, meta, progress, time, steps: 0 }, parent, began: ts });
    scopes.set(runId, scope);
    return scope;
  }

  /** The row of a delegated run as it stands at `ts`: its last line as facts and the time it has taken. */
  function paintChild(scope, ts, facts) {
    scope.panel.progress.replaceChildren(...facts.map((fact) => el("span", null, fact)));
    scope.panel.time.textContent = duration(Math.max(ts - scope.began, 0));
  }

  /**
   * The seconds of a model turn join the group whose time they were spent in: the open group, which lasts at least until
   * the turn's event, or, with none open, the group the turn's tool calls start when it wrote nothing before them (the
   * finish call is not shown). A turn that wrote a message with no group open was spent before that message, in no group.
   */
  function addModelTime(scope, data) {
    if (data.seconds === undefined) {
      return;
    }
    const startsGroup = data.text.trim() === "" && data.tool_calls.some((name) => name !== FINISH_TOOL);
    const group = scope.group ?? (startsGroup ? groupFor(scope) : null);
    if (group !== null) {
      group.modelSeconds += data.seconds;
      paintGroup(group);
    }
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
    paintRing(scope.panel.status, waiting ? "blocked" : "running");
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
      // What the run said, as it ended, it needs from the user (older runs have none).
      const needs = typeof record.data.needs === "string" ? record.data.needs.trim() : "";
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
      if (isRoot && needs !== "") {
        const head = el("div", { class: "aside-head" }, icon("hand"), el("span", null, "What Dot needs from you"));
        place(scope, el("div", { class: "needs", "aria-label": "What Dot needs from you" }, head, prose(needs)), record.ts);
      }
      if (isRoot) {
        onAnswer(answer);
        onRunStatus({ status, model: null, endedAt: record.ts });
        return;
      }
      paintRing(scope.panel.status, status);
      scope.panel.face.setAttribute("data-state", faceState(status));
      scope.panel.face.classList.add("ended");
      paintChild(scope, record.ts, [status, plural(scope.panel.steps, "step", "steps")]);
      // The parent resumes when the delegated run is over; the time in between was spent on the delegation.
      scope.parent.boundary = record.ts;
      if (status === "done" && !scope.touched) {
        scope.panel.node.open = false;
      }
    },

    assistant(scope, record) {
      const data = record.data;
      dropThinking(scope);
      addModelTime(scope, data);
      if (scope.panel !== null) {
        scope.panel.steps = Math.max(scope.panel.steps, data.step);
        scope.panel.progress.textContent = `step ${scope.panel.steps}`;
      }
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

    // A model call of the run (agent/loop.py and the router): a step's tokens came with its assistant event already, so
    // only the side calls add theirs to the run's numbers.
    model_call(scope, record) {
      const data = record.data;
      if (scope.runId === rootRunId && data.purpose !== "step") {
        onStats({ step: 0, input: data.input_tokens, output: data.output_tokens });
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
      if (scope.runId === rootRunId && data.tool.startsWith("browser_") && !browserSeen) {
        browserSeen = true;
        onBrowserStep();
      }
      if (scope.panel !== null) {
        paintChild(scope, record.ts, [`step ${scope.panel.steps}`, data.tool]);
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
      const card = approvalCard({ approval: fromEvent(record.run_id, record.data), heading: null, onSettled: () => undefined, leftNote: LEFT_IN_RUN });
      cards.set(record.data.approval_id, { card, scope });
      place(scope, card.node, record.ts);
      markWaiting(scope, true);
    },

    // A question the run left for the user and went on without; it waits in the run's "Dot asked you" list.
    question_left(scope, record) {
      const data = record.data;
      const question = typeof data.args.question === "string" ? data.args.question : data.summary;
      note(scope, record.ts, "help", `Dot asked you “${clip(question, 300)}” and went on without the answer`, "warn");
      if (scope.runId === rootRunId) {
        onQuestionLeft();
      }
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

    // The checker of goal mode compared the answer with the goal: `final` says whether the run ends with this verdict,
    // and `limit` that it judged the finish of a run past its limit, which cannot be sent back.
    goal_check(scope, record) {
      const { decision, reason, check, patience, final, limit } = record.data;
      const because = reason === "" ? "" : ` — ${clip(reason, 300)}`;
      const stops = limit === true
        ? ["bad", "Goal check: not reached, and the run is at its limit, so it stops"]
        : ["bad", `Goal check: still not reached after ${patience} rounds of further work, so the run stops`];
      const lines = {
        achieved: ["ok", "Goal check: the goal is reached"],
        continue: final ? stops : ["warn", `Goal check ${check} of ${patience}: not reached yet, back to work`],
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
        // Plans stored before targets existed have none.
        onPlan(steps, record.data.targets ?? "");
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

    // A session of a campaign wanted to end while the campaign had no targets: it was sent back once to record them.
    campaign_targets_reminder(scope, record) {
      note(scope, record.ts, "target", "Dot wanted to end the session before the campaign had targets and was asked to record them first", "warn");
    },

    // The run reached a limit of [agent] run_max_minutes or run_max_tokens: it was told to finish, and its next reply ends it.
    limit_reached(scope, record) {
      note(scope, record.ts, "clock", `The run reached its limit (${limitSpent(record.data)}) and was told to finish now`, "warn");
    },

    // The final answer named paths that do not exist and that the run never touched ([agent] verify_answer_paths): the
    // run was sent back once to create them or to correct the answer.
    answer_paths(scope, record) {
      const missing = record.data.missing;
      const noun = missing.length === 1 ? "a path" : `${missing.length} paths`;
      const listed = clip(missing.join(", "), 300);
      note(scope, record.ts, "file", `Dot's answer named ${noun} that do not exist (${listed}) and was asked to correct it`, "warn");
    },

    // The run goes on from where the daemon cut the run before it off ([agent] resume, or Continue): how that run was
    // cut off, and how each call of the step it was in ended.
    resumed(scope, record) {
      const data = record.data;
      const who = data.automatic ? "Resumed on its own when the daemon started" : "Resumed by hand";
      const how = data.cut_off === "daemon_shutdown" ? "the daemon was shut down" : "the daemon stopped";
      const calls = data.calls.length === 0 ? "" : `. Its last step: ${data.calls.map(callEnd).join(", ")}`;
      place(
        scope,
        el(
          "div",
          { class: "note" },
          icon("refresh"),
          el("span", null, `${who}: ${how} while the run before this one worked${calls}`),
          el("a", { class: "small", href: `#/runs/${data.from_run_id}` }, "Open that run"),
        ),
        record.ts,
      );
    },

    // How much help the run's model gets. Shown only where it differs from the configuration: a context budget cut to
    // fit the model's window, and reminders of the goal for a model of the weak tier.
    scaffold(scope, record) {
      const data = record.data;
      if (data.cut_to_window) {
        const output = `tool output up to ${compact(data.tool_output_chars)} characters`;
        note(scope, record.ts, "sliders", `Context budget ${compact(data.context_tokens)} tokens to fit the model's window of ${compact(data.window)}, ${output}`, "");
      }
      if (data.reminder_every !== null) {
        note(scope, record.ts, "target", `The goal is repeated every ${data.reminder_every} steps: ${data.reason}`, "");
      }
    },

    reminder(scope, record) {
      note(scope, record.ts, "target", `Reminded of the goal at step ${record.data.step}`, "");
    },

    // The tool groups of a run whose tools come in groups ([agent] tool_surface): the ones it starts with, then each
    // group a step turns on.
    tool_groups(scope, record) {
      const data = record.data;
      if (data.step === 0) {
        const off = data.off.length === 0 ? "" : `; ${data.off.join(", ")} off until the run needs them`;
        note(scope, record.ts, "layers", `Tools offered by group: ${data.on.join(", ")} on${off} (${data.reason})`, "");
      } else {
        const noun = data.turned_on.length === 1 ? "group" : "groups";
        note(scope, record.ts, "layers", `Turned on the tool ${noun} ${data.turned_on.join(", ")} at step ${data.step}`, "");
      }
    },

    memories(scope, record) {
      const count = plural(record.data.ids.length, "memory", "memories");
      const tokens = typeof record.data.tokens === "number" ? `, ${plural(record.data.tokens, "token", "tokens")}` : "";
      const text = record.data.live === true ? `Picked up ${count} saved while the run worked` : `Recalled ${count}${tokens}`;
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

    // The run's model could not answer (`reason`: a spent quota or rate, a refused or missing key), so the run goes on
    // with a standby model ([models] standby) from this step on; the chip of the run, or the header of a delegated
    // run, names the new model.
    model_switched(scope, record) {
      const data = record.data;
      note(scope, record.ts, "refresh", `${data.from} cannot answer (${data.reason}); the run goes on with ${data.to}`, "warn");
      if (scope.runId === rootRunId) {
        onRunStatus({ status: "running", model: data.to, endedAt: null });
        return;
      }
      scope.panel.meta.textContent = scope.panel.meta.textContent.replace(` · ${data.from}`, ` · ${data.to}`);
    },

    // A helper model ([models] helpers) could not answer a question of the run; `until` (seconds) is when it may be
    // asked again, null for a missing key.
    helper_unavailable(scope, record) {
      const data = record.data;
      const until = data.until === null ? "" : ` until ${dateTime(data.until)}`;
      note(scope, record.ts, "key", `The helper ${data.model} cannot answer (${data.reason}${until})`, "warn");
    },

    // The typed judge ([judge]) could not answer (`reason`, `error`); until `until` (seconds) nothing is asked of it and
    // the policy's patterns and the goal checker decide alone.
    judge_unavailable(scope, record) {
      const data = record.data;
      const text = `The typed judge cannot answer (${data.reason}) until ${dateTime(data.until)}; the policy's patterns and the goal checker decide alone: ${data.error}`;
      note(scope, record.ts, "key", text, "warn");
    },

    // TypeSafe rejected a request of the typed judge or of judge_items, or answered outside its reference (`error`): an
    // error of the code or the model, logged with the request; nothing is paused.
    judge_error(scope, record) {
      note(scope, record.ts, "key", `TypeSafe rejected a request (the log has it): ${record.data.error}`, "warn");
    },

    // The user took the browser over (`url`: the tab they got) and the run waits at its next tool call; and gave it
    // back after `held_s` seconds, with a note to the model that the page may have changed.
    takeover(scope, record) {
      const where = record.data.url ? ` at ${record.data.url}` : "";
      note(scope, record.ts, "hand", `You took the browser over${where}; the run waits until you return control.`, "");
      onHeld(true);
    },
    handback(scope, record) {
      note(scope, record.ts, "hand", `You returned control after ${Math.round(record.data.held_s)} s; the run goes on.`, "");
      onHeld(false);
    },

    // A typed judgment sent a step to another model of the run (`model`, `option`, `probabilities`); or that model
    // could not answer the step, which is asked again on the run's own model.
    step_routed(scope, record) {
      const data = record.data;
      const odds = Object.entries(data.probabilities).map(([option, p]) => `${option} ${p.toFixed(2)}`).join(", ");
      note(scope, record.ts, "refresh", `Step ${data.step} goes to ${data.model} (${odds})`, "");
    },
    step_route_failed(scope, record) {
      const data = record.data;
      note(scope, record.ts, "refresh", `${data.model} could not answer step ${data.step}; asked again on the run's model: ${data.error}`, "warn");
    },

    // A session of a campaign ranked the posts it could answer (`topic`, `weights`, `posts` with their scores, the
    // highest first).
    posts_ranked(scope, record) {
      const data = record.data;
      const top = data.posts.slice(0, 3).map((post) => `${post.id} ${post.score.toFixed(2)}`).join(", ");
      note(scope, record.ts, "key", `Ranked ${data.posts.length} posts on “${data.topic}”; the top: ${top}`, "");
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

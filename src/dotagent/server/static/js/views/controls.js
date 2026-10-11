// Every control of the interface on one page, drawn on the tokens of the visual system, for checking a look in light
// and in dark, wide and on a phone. Reached at #/controls; nothing links to it. The cards here act on nothing: the
// approval cards post nowhere, the buttons do nothing.

import { approvalCard } from "../approvals.js";
import { createCostCard } from "../cost.js";
import { el } from "../dom.js";
import { avatar, icon } from "../icons.js";
import { emptyPanel, errorPanel, field, loadingPanel, pageHead, segmented, statusBadge } from "../panels.js";

const FACE_STATES = ["idle", "working", "waiting", "done", "failed"];
const RUN_STATUSES = ["queued", "running", "blocked", "done", "failed", "cancelled"];
const BADGE_TONES = ["b-ok", "b-warn", "b-bad", "b-info", "b-muted"];
const COLOUR_TOKENS = [
  "bg", "sidebar", "surface", "surface-2", "surface-3", "border", "border-strong", "text", "text-2", "muted",
  "accent", "accent-weak", "bubble", "ok", "ok-weak", "warn", "warn-weak", "bad", "bad-weak",
];

function section(title, ...body) {
  return el("section", { class: "card control-section", "data-control": title.toLowerCase() }, el("div", { class: "card-head" }, el("span", null, title)), el("div", { class: "card-pad stack" }, ...body));
}

function row(...children) {
  return el("div", { class: "row wrap" }, ...children);
}

// The bill of a run of 51 minutes, as `GET /runs/{id}/stats` answers it.
const FAKE_COSTS = {
  run_id: "demo",
  status: "done",
  minutes: 51.2,
  stats: {
    model: "ollama:demo",
    steps: 260,
    input_tokens: 9_400_000,
    output_tokens: 61_000,
    compaction: { compactions: 36, per_100_steps: 13.8, seconds: 420.5, share: 0.26 },
    delegation: { runs: 2, seconds: 310.0 },
    time: { model_seconds: 1630.0, compaction_seconds: 420.5, cached_tokens: 7_100_000, cached_share: 0.76 },
    searches: [
      { engine: "ollama", searches: 12 },
      { engine: "anthropic", searches: 3 },
    ],
  },
  tool_seconds: 980.0,
  waited_s: 140.0,
  tools: [],
};

function fakeApproval(severity, tool, summary) {
  return { id: `demo-${severity}`, runId: "demo", tool, summary, severity, args: { kind: "write", target: "~/notes.txt", question: summary, options: ["Blue", "Green"] }, status: "pending", note: "" };
}

export function mount(root, ctx) {
  const swatches = el(
    "div",
    { class: "swatches" },
    ...COLOUR_TOKENS.map((token) => el("div", { class: "swatch" }, el("span", { class: "swatch-colour", "data-token": token }), el("code", { class: "mono small" }, `--${token}`))),
  );
  const faces = row(...FACE_STATES.map((state) => el("div", { class: "stack tight center" }, avatar(state), el("span", { class: "small muted" }, state))));
  const buttons = row(
    el("button", { class: "btn primary", type: "button" }, icon("plus"), "Primary"),
    el("button", { class: "btn", type: "button" }, "Default"),
    el("button", { class: "btn ghost", type: "button" }, "Ghost"),
    el("button", { class: "btn danger", type: "button" }, icon("stop"), "Danger"),
    el("button", { class: "btn sm", type: "button" }, "Small"),
    el("button", { class: "btn sm icon-only", type: "button", "aria-label": "Icon only" }, icon("trash")),
    el("button", { class: "btn", type: "button", disabled: true }, "Disabled"),
  );
  const badges = row(
    ...RUN_STATUSES.map(statusBadge),
    ...BADGE_TONES.map((tone) => el("span", { class: `badge ${tone}` }, tone.slice(2))),
    el("span", { class: "chip" }, "chip"),
    el("span", { class: "pill" }, icon("bell"), "pill"),
  );
  const marks = row(
    ...RUN_STATUSES.map((status) => el("span", { class: `status-dot s-${status}`, title: status })),
    ...RUN_STATUSES.map((status) => el("span", { class: `status-ring s-${status}`, title: status })),
    el("span", { class: "live-dot live" }),
    el("span", { class: "spinner" }),
  );
  const yolo = segmented([{ id: "ask", label: "Ask", hint: "" }, { id: "auto", label: "Auto", hint: "" }, { id: "yolo", label: "YOLO", hint: "" }], "yolo", () => {});
  yolo.element.dataset.mode = "yolo";
  const inputs = el(
    "div",
    { class: "form-grid" },
    field("Text", el("input", { class: "input", type: "text", placeholder: "Placeholder", "aria-label": "Text" }), "A hint under the field."),
    field("Mono", el("input", { class: "input mono", type: "text", value: "^git (status|diff)", "aria-label": "Mono" }), ""),
    field("Select", el("select", { class: "select", "aria-label": "Select" }, el("option", null, "One"), el("option", null, "Two")), ""),
    field("Small select", el("select", { class: "select sm", "aria-label": "Small select" }, el("option", null, "Medium"), el("option", null, "High")), ""),
    field("Textarea", el("textarea", { class: "textarea", rows: "2", placeholder: "A longer text", "aria-label": "Textarea" }), ""),
    el("div", { class: "field" }, el("span", { class: "field-label" }, "Switches"), row(el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "true", "aria-label": "On" }), el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": "Off" }))),
    el("div", { class: "field wide" }, el("span", { class: "field-label" }, "Segmented"), row(segmented([{ id: "a", label: "One", hint: "" }, { id: "b", label: "Two", hint: "" }, { id: "c", label: "Three", hint: "" }], "a", () => {}).element, yolo.element)),
  );
  const notes = el(
    "div",
    { class: "stack" },
    el("div", { class: "note" }, icon("shield"), el("span", null, "A note: quiet, beside the thing it is about.")),
    el("div", { class: "note warn" }, icon("alert"), el("span", null, "A warning note.")),
    el("div", { class: "banner", role: "alert" }, icon("alert"), el("span", { class: "grow" }, "A banner across the page."), el("button", { class: "btn sm", type: "button" }, "Release")),
  );
  const cards = el(
    "div",
    { class: "stack" },
    approvalCard({ approval: fakeApproval("question", "ask_user", "Which colour?"), heading: null, onSettled: () => {}, leftNote: "" }).node,
    approvalCard({ approval: fakeApproval("handoff", "browser_open", "Open https://example.com/ — rule"), heading: null, onSettled: () => {}, leftNote: "" }).node,
    approvalCard({ approval: fakeApproval("high", "shell", "rm -rf build — aggressive preset still asks for: destructive"), heading: null, onSettled: () => {}, leftNote: "" }).node,
  );
  const bubbles = el(
    "div",
    { class: "stack" },
    el("div", { class: "bubble user" }, "Your message, on the raised surface."),
    el("div", { class: "bubble", role: "article" }, el("div", { class: "prose" }, el("p", null, "Dot's answer, in the voice of the look. "), el("code", { class: "mono" }, "code"))),
  );
  const costSample = createCostCard();
  costSample.paint(FAKE_COSTS);
  const panels = el("div", { class: "stack" }, loadingPanel(), emptyPanel("Nothing here yet", "An empty panel with its hint."), errorPanel(new Error("Something went wrong"), () => {}));
  const type = el(
    "div",
    { class: "stack tight" },
    ...[["text-2xl", "The home's greeting"], ["text-xl", "Page title"], ["text-lg", "Section title"], ["text-md", "Prose and the composer"], ["text-ui", "The interface: sidebar, buttons, labels"], ["text-sm", "Meta and hints"], ["text-xs", "Badges and chips"]].map(([token, words]) =>
      el("div", { class: `row type-sample ${token}` }, el("span", { class: "grow" }, words), el("code", { class: "mono small muted" }, `--${token}`)),
    ),
  );
  root.replaceChildren(
    pageHead("Controls", "Every control on the tokens of the visual system: colour, type, shape, spacing, and the state marks.", null),
    el(
      "div",
      { class: "stack" },
      section("Colour", swatches),
      section("Type", type),
      section("Faces", faces),
      section("Buttons", buttons),
      section("Badges", badges),
      section("Marks", marks),
      section("Inputs", inputs),
      section("Notes", notes),
      section("Cards", cards),
      section("Cost", costSample.element),
      section("Bubbles", bubbles),
      section("Panels", panels),
    ),
  );
  return () => {};
}

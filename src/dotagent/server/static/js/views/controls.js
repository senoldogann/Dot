// Every control of the interface on one page, drawn on the tokens of the visual system, for checking a look in light
// and in dark, wide and on a phone. Reached at #/controls; nothing links to it. The buttons here do nothing, but those
// of the approval cards, which are the real ones: they post to ids no approval has (`demo-...`), and the daemon's
// refusal shows as their error (item 559).

import { dotFace } from "../actors.js";
import { approvalCard } from "../approvals.js";
import { mountCharacter } from "../character/character.js";
import { EXPRESSION_IDS } from "../character/expressions.js";
import { COLOUR_IDS, SHAPE_IDS } from "../character/skins.js";
import { SEQUENCE } from "../character/states.js";
import { createCostCard } from "../cost.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import {
  emptyPanel,
  errorPanel,
  field,
  loadingPanel,
  pageHead,
  rangeSetting,
  segmented,
  statusBadge,
} from "../panels.js";
import { t } from "../i18n.js";

const FACE_STATES = ["idle", "working", "waiting", "done", "failed"];
const RUN_STATUSES = ["queued", "running", "blocked", "ending", "done", "failed", "cancelled"];
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
  return { id: `demo-${severity}`, runId: "demo", tool, summary, severity, args: { kind: "write", target: "~/notes.txt", question: summary, options: [t("views.controls.blue"), t("views.controls.green")] }, status: "pending", note: "" };
}

// A question with options as ask_user offers them (item 192): described, one recommended, one or several to choose.
function fakeQuestion(id, question, options, multiple) {
  return { id: `demo-${id}`, runId: "demo", tool: "ask_user", summary: question, severity: "question", args: { question, options, multiple }, status: "pending", note: "" };
}

// The characters (item 101): every shape, expression and colour as a still frame, and every animation moving.
function characterRow(ids, _size, make) {
  const handles = [];
  const cells = ids.map((id) => {
    const cell = el("div", { class: "stack tight center" });
    handles.push(make(cell, id));
    cell.append(el("span", { class: "small muted" }, id));
    return cell;
  });
  return { element: row(...cells), handles };
}

function characters() {
  const shapes = characterRow(SHAPE_IDS, 64, (cell, id) => mountCharacter(cell, { shape: id, size: 64, moving: false }));
  const expressions = characterRow(EXPRESSION_IDS, 64, (cell, id) => mountCharacter(cell, { expression: id, size: 64, moving: false }));
  const colours = characterRow(COLOUR_IDS, 48, (cell, id) => mountCharacter(cell, { colour: id, size: 48, moving: false }));
  const states = characterRow(SEQUENCE, 80, (cell, id) => mountCharacter(cell, { state: id, size: 80, phase: 0 }));
  const element = el(
    "div",
    { class: "stack" },
    el("span", { class: "small muted" }, t("views.controls.shapes")),
    shapes.element,
    el("span", { class: "small muted" }, t("views.controls.expressions")),
    expressions.element,
    el("span", { class: "small muted" }, t("views.controls.colours")),
    colours.element,
    el("span", { class: "small muted" }, t("views.controls.animationsMoving")),
    states.element,
  );
  const handles = [...shapes.handles, ...expressions.handles, ...colours.handles, ...states.handles];
  return {
    element,
    dispose: () => {
      for (const handle of handles) {
        handle.destroy();
      }
    },
  };
}

export function mount(root, _ctx) {
  const cast = characters();
  const swatches = el(
    "div",
    { class: "swatches" },
    ...COLOUR_TOKENS.map((token) => el("div", { class: "token-swatch" }, el("span", { class: "swatch-colour", "data-token": token }), el("code", { class: "mono small" }, `--${token}`))),
  );
  const faces = row(...FACE_STATES.map((state) => el("div", { class: "stack tight center" }, dotFace(state), el("span", { class: "small muted" }, state))));
  const buttons = row(
    el("button", { class: "btn primary", type: "button" }, icon("plus"), t("views.controls.primary")),
    el("button", { class: "btn", type: "button" }, t("views.controls.default")),
    el("button", { class: "btn ghost", type: "button" }, t("views.controls.ghost")),
    el("button", { class: "btn danger", type: "button" }, icon("stop"), t("views.controls.danger")),
    el("button", { class: "btn sm", type: "button" }, t("views.controls.small")),
    el("button", { class: "btn sm icon-only", type: "button", "aria-label": t("views.controls.iconOnly") }, icon("trash")),
    el("button", { class: "btn", type: "button", disabled: true }, t("views.controls.disabled")),
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
  const yolo = segmented([{ id: "ask", label: t("views.controls.ask"), hint: "" }, { id: "auto", label: t("views.controls.auto"), hint: "" }, { id: "yolo", label: t("views.controls.payOrSend"), hint: "" }], "yolo", () => {});
  yolo.element.dataset.mode = "yolo";
  // The range (item 127), as Settings › Appearance draws its contrast, its slide and its sizes.
  let ranged = 40;
  const range = rangeSetting({
    label: t("views.controls.range"),
    hint: t("views.controls.aNumberOnAScale"),
    min: 0,
    max: 100,
    step: 5,
    format: (value) => `${value}%`,
    read: () => ranged,
    write: (value) => {
      ranged = value;
    },
  });
  const inputs = el(
    "div",
    { class: "form-grid" },
    field(t("views.controls.text"), el("input", { class: "input", type: "text", placeholder: t("views.controls.placeholder"), "aria-label": t("views.controls.text") }), t("views.controls.aHintUnderTheField")),
    field(t("views.controls.mono"), el("input", { class: "input mono", type: "text", value: "^git (status|diff)", "aria-label": t("views.controls.mono") }), ""),
    field(t("views.controls.select"), el("select", { class: "select", "aria-label": t("views.controls.select") }, el("option", null, t("views.controls.one")), el("option", null, t("views.controls.two"))), ""),
    field(t("views.controls.smallSelect"), el("select", { class: "select sm", "aria-label": t("views.controls.smallSelect") }, el("option", null, t("views.controls.medium")), el("option", null, t("views.controls.high"))), ""),
    field(t("views.controls.textarea"), el("textarea", { class: "textarea", rows: "2", placeholder: t("views.controls.aLongerText"), "aria-label": t("views.controls.textarea") }), ""),
    el("div", { class: "field" }, el("span", { class: "field-label" }, t("views.controls.switches")), row(el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "true", "aria-label": t("views.controls.on") }), el("button", { class: "switch", type: "button", role: "switch", "aria-checked": "false", "aria-label": t("views.controls.off") }))),
    el("div", { class: "field wide" }, el("span", { class: "field-label" }, t("views.controls.segmented")), row(segmented([{ id: "a", label: t("views.controls.one"), hint: "" }, { id: "b", label: t("views.controls.two"), hint: "" }, { id: "c", label: t("views.controls.three"), hint: "" }], "a", () => {}).element, yolo.element)),
    el("div", { class: "field wide", "data-control": "range" }, range.element),
  );
  const notes = el(
    "div",
    { class: "stack" },
    el("div", { class: "note" }, icon("shield"), el("span", null, t("views.controls.aNoteQuietBesideThe"))),
    el("div", { class: "note warn" }, icon("alert"), el("span", null, t("views.controls.aWarningNote"))),
    el("div", { class: "banner", role: "alert" }, icon("alert"), el("span", { class: "grow" }, t("views.controls.aBannerAcrossThePage")), el("button", { class: "btn sm", type: "button" }, t("views.controls.release"))),
  );
  const cards = el(
    "div",
    { class: "stack" },
    approvalCard({ approval: fakeApproval("question", "ask_user", t("views.controls.whichColour")), heading: null, onSettled: () => {}, leftNote: "", onUseHere: null }).node,
    approvalCard({
      approval: fakeQuestion(
        "choice",
        t("views.controls.whichColourShouldTheButtons"),
        [
          { label: t("views.controls.blue"), description: t("views.controls.theColourOfTheRest"), recommended: true },
          { label: t("views.controls.green"), description: t("views.controls.standsOutMoreAndMatches"), recommended: false },
        ],
        false,
      ),
      heading: null,
      onSettled: () => {},
      leftNote: "",
      onUseHere: null,
    }).node,
    approvalCard({
      approval: fakeQuestion(
        "choices",
        t("views.controls.whichPagesShouldGetThe"),
        [
          { label: t("views.controls.home"), description: "", recommended: true },
          { label: t("views.controls.settings"), description: "", recommended: true },
          { label: t("views.controls.runs"), description: t("views.controls.itsButtonsSitCloseTogether"), recommended: false },
        ],
        true,
      ),
      heading: null,
      onSettled: () => {},
      leftNote: "",
      onUseHere: null,
    }).node,
    approvalCard({ approval: fakeApproval("handoff", "browser_open", "Open https://example.com/ — rule"), heading: null, onSettled: () => {}, leftNote: "", onUseHere: null }).node,
    approvalCard({ approval: fakeApproval("high", "shell", "rm -rf build — aggressive preset still asks for: destructive"), heading: null, onSettled: () => {}, leftNote: "", onUseHere: null }).node,
  );
  const bubbles = el(
    "div",
    { class: "stack" },
    el("div", { class: "bubble user" }, t("views.controls.yourMessageOnTheRaised")),
    el("div", { class: "bubble", role: "article" }, el("div", { class: "prose" }, el("p", null, t("views.controls.dotSAnswerInThe")), el("code", { class: "mono" }, "code"))),
  );
  const costSample = createCostCard();
  costSample.paint(FAKE_COSTS);
  const panels = el("div", { class: "stack" }, loadingPanel(), emptyPanel("circleDashed", t("views.controls.nothingHereYet"), t("views.controls.anEmptyPanelWithIts")), errorPanel(new Error("Something went wrong"), () => {}));
  const type = el(
    "div",
    { class: "stack tight" },
    ...[["text-2xl", t("views.controls.theHomeSGreeting")], ["text-xl", t("views.controls.pageTitle")], ["text-lg", t("views.controls.sectionTitle")], ["text-md", t("views.controls.proseAndTheComposer")], ["text-ui", t("views.controls.theInterfaceSidebarButtonsLabels")], ["text-sm", t("views.controls.metaAndHints")], ["text-xs", t("views.controls.badgesAndChips")]].map(([token, words]) =>
      el("div", { class: `row type-sample ${token}` }, el("span", { class: "grow" }, words), el("code", { class: "mono small muted" }, `--${token}`)),
    ),
  );
  root.replaceChildren(
    pageHead(t("views.controls.controls"), t("views.controls.everyControlOnTheTokens"), null),
    el(
      "div",
      { class: "stack" },
      section(t("views.controls.colour"), swatches),
      section(t("views.controls.type"), type),
      section(t("views.controls.faces"), faces),
      section(t("views.controls.characters"), cast.element),
      section(t("views.controls.buttons"), buttons),
      section(t("views.controls.badges"), badges),
      section(t("views.controls.marks"), marks),
      section(t("views.controls.inputs"), inputs),
      section(t("views.controls.notes"), notes),
      section(t("views.controls.cards"), cards),
      section(t("views.controls.cost"), costSample.element),
      section(t("views.controls.bubbles"), bubbles),
      section(t("views.controls.panels"), panels),
    ),
  );
  return cast.dispose;
}

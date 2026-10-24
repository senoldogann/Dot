// A team's page (item 610, #/teams/<id>): the team's agents' faces over its name and what it is for, a box for a message
// to the team, and its chats. A message starts a run of Dot's in a chat with the team, and every run of that chat is told
// that Dot leads the team: it hands each agent the part its role fits (`message_agent`) and puts their answers together.
// The bar's Edit changes the team's name, purpose and agents; Remove takes the team away and nothing else: its agents and
// its chats stay. A team with no agent left says so and takes no message.

import { actorFace, rememberLook, setActorState, teamDuo, teamFaces } from "../actors.js";
import { api } from "../api.js";
import { ago, clip, el, plural } from "../dom.js";
import { icon } from "../icons.js";
import { touchFirst } from "../layout.js";
import { errorPanel, loadingPanel, statusBadge } from "../panels.js";
import { readFolds, writePref } from "../prefs.js";
import { setStageTitle, setStageTools } from "../stage.js";
import { onLifecycle, onStreamBack } from "../state.js";
import { teamForm } from "../teamform.js";
import { attempt, confirmDialog, toast } from "../ui.js";
import { homeSection, startComposer } from "./agents.js";

const TITLE_CHARS = 90;
const LAST_CHARS = 140;
// How many of the team's agents the greeting shows the face of, the rest counted.
const MOST_FACES = 6;
// Which sections of a team's page are folded, kept per browser as an agent's page keeps its own.
const FOLDS_KEY = "dot.teamFolds";
// The team's runs are Dot's, with Dot's own bounds: the box starts where the home's does.
const DOTS_BOUNDS = { model: "", effort: null, approvals: null };
// The runs that have not ended.
const WORKING = new Set(["queued", "running", "blocked"]);

/** A chat of the team in its list: its title as a link to the chat, how it stands, its last words and when it moved. */
function chatRow(chat) {
  return el(
    "div",
    { class: "list-row", dataset: { chat: chat.id } },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row" }, el("a", { class: "row-title", href: `#/runs/${chat.latest_run_id}` }, clip(chat.title, TITLE_CHARS)), statusBadge(chat.latest_status)),
      chat.last_message === "" ? null : el("div", { class: "row-text muted" }, clip(chat.last_message, LAST_CHARS)),
      el("div", { class: "row-meta" }, el("span", null, ago(chat.updated_at))),
    ),
  );
}

/** Whether a chat of the team works now. */
function works(chats) {
  return chats.some((chat) => WORKING.has(chat.latest_status));
}

/** The team's greeting: its agents' faces, its name, what it is for and its agents by name, each a link to its page. */
function who(detail) {
  const { record, members, chats } = detail;
  const working = works(chats);
  return el(
    "div",
    { class: "home-who agent-who team-who" },
    teamFaces(record, working ? "working" : "idle", MOST_FACES),
    el("div", { class: "home-name" }, record.name),
    record.purpose === "" ? null : el("p", { class: "home-greeting" }, record.purpose),
    members.length === 0
      ? null
      : el(
          "ul",
          { class: "team-roster", "aria-label": `The ${plural(members.length, "agent", "agents")} of ${record.name}` },
          members.map((member) =>
            el("li", null, el("a", { class: "chip", href: `#/agents/${member.id}`, title: member.role }, actorFace(member.id, "idle", member.look), el("span", null, member.name))),
          ),
        ),
  );
}

function mountPage(root, ctx, teamId) {
  let disposed = false;
  let ticket = 0;
  let composer = null;
  let form = null;
  let shownDetail = null;
  // Whether the top was drawn for a team with no agent left, without the box; null until it was drawn at all.
  let drawnEmpty = null;
  let folds = readFolds(FOLDS_KEY);
  const top = el("div", { class: "home-start agent-top team-top" });
  const sections = el("section", { class: "home-today agent-sections", "aria-label": "The team's work" });
  const page = el("div", { class: "home agent-home team-home" }, loadingPanel());

  function stageOf(detail) {
    const { record } = detail;
    ctx.setTitle(`${record.name} · Agents`);
    setStageTitle(teamDuo(record, "idle"), el("h1", { class: "stage-h1 stage-name" }, record.name));
    setStageTools(
      el("button", { class: "btn ghost sm", type: "button", title: "Change the team: its name, what it is for and its agents", "aria-label": "Edit the team", onclick: () => attempt(edit) }, icon("pencil"), el("span", { class: "label" }, "Edit")),
      el("button", { class: "btn ghost sm", type: "button", title: "Remove the team: its agents and its chats stay", "aria-label": "Remove the team", onclick: () => attempt(remove) }, icon("trash"), el("span", { class: "label" }, "Remove")),
    );
  }

  function closeForm() {
    delete page.dataset.editing;
    form = null;
  }

  function disposeComposer() {
    if (composer !== null) {
      composer.dispose();
      composer = null;
    }
  }

  /** What stands where the box would: a team with no agent left takes no message, and says how to give it one. */
  function emptyNote() {
    return el(
      "div",
      { class: "note warn team-empty", role: "status" },
      icon("alert"),
      el("span", { class: "grow" }, "This team has no agents left, so it takes no messages. Add one of your agents with Edit, or Remove the team."),
      el("button", { class: "btn sm", type: "button", onclick: () => attempt(edit) }, icon("pencil"), "Edit"),
    );
  }

  function greet(detail) {
    disposeComposer();
    closeForm();
    for (const member of detail.members) {
      rememberLook(member.id, member.look);
    }
    // The box is off while there is no agent to lead: the page says so in its place.
    drawnEmpty = detail.members.length === 0;
    composer = drawnEmpty ? null : startComposer({ name: detail.record.name, bounds: DOTS_BOUNDS, owner: { team_id: detail.record.id }, ctx });
    top.replaceChildren(who(detail), composer === null ? emptyNote() : composer.element);
    stageOf(detail);
  }

  async function edit() {
    // The agents are read again, so that one made a moment ago can be put in.
    const summaries = await api.get("/agents");
    const detail = shownDetail;
    disposeComposer();
    form = teamForm({
      team: detail.record,
      agents: summaries.map(({ record }) => record).filter((record) => record.id !== "dot"),
      onSaved: () => attempt(reload),
      onCancel: () => greet(detail),
    });
    top.replaceChildren(form.element);
    // The editor opens at the top of the page, not in the middle where the greeting stood (agent-home, app.css).
    page.dataset.editing = "";
    form.focus();
  }

  async function remove() {
    const detail = shownDetail;
    const confirmed = await confirmDialog({
      title: `Remove ${detail.record.name}?`,
      message: "Only the team goes. Its agents stay yours, and so do its chats, which stay as chats with Dot.",
      confirmLabel: "Remove",
      danger: true,
      focusCancel: true,
    });
    if (confirmed) {
      await api.del(`/teams/${detail.record.id}`);
      toast("ok", `${detail.record.name} removed. Its agents are still yours.`);
      ctx.navigate("/agents");
    }
  }

  // The team as the daemon has it now; null when the page went or a later load overtook this one. A team that is not
  // there says so, and a failure takes the place of the page with a way to try again.
  async function load() {
    const mine = ++ticket;
    try {
      const detail = await api.get(`/teams/${teamId}`);
      return disposed || mine !== ticket ? null : detail;
    } catch (error) {
      if (!disposed && mine === ticket) {
        page.replaceChildren(error.status === 404 ? gone() : errorPanel(error, () => attempt(reload)));
        setStageTitle(el("h1", { class: "stage-h1 stage-name" }, "Team"));
        setStageTools();
      }
      return null;
    }
  }

  /** The page of a team that is not there (removed, here or in another window): where its chats and its agents are. */
  function gone() {
    return el(
      "div",
      { class: "card team-gone", role: "status" },
      el("div", { class: "empty" }, icon("alert"), el("div", { class: "row-title" }, "This team is gone"), el("div", { class: "muted" }, "Its agents are still yours, and its chats stay in the list of chats, as chats with Dot."), el("a", { class: "btn", href: "#/agents" }, "Back to the agents")),
    );
  }

  function toggleFold(key) {
    folds = { ...folds, [key]: folds[key] !== true };
    writePref(FOLDS_KEY, JSON.stringify(folds));
    if (shownDetail !== null) {
      paintLists(shownDetail);
    }
    sections.querySelector(`[data-section="${key}"] .home-today-title`)?.focus();
  }

  function paintLists(detail) {
    shownDetail = detail;
    sections.replaceChildren(...[homeSection("chats", "Chats", detail.chats.map(chatRow), folds, toggleFold)].filter((part) => part !== null));
    sections.hidden = sections.childElementCount === 0;
    if (top.parentNode !== page) {
      page.replaceChildren(top, sections);
    }
  }

  // The whole page: the top built again (the box, after a save) and the lists.
  async function reload() {
    const detail = await load();
    if (detail !== null) {
      greet(detail);
      paintLists(detail);
      // On a touch screen the caret waits for a tap, as on the home (item 293).
      if (composer !== null && !touchFirst()) {
        composer.focus();
      }
    }
  }

  // Only the lists and the faces' state, when a run starts or ends: a message being typed or an open form stays.
  async function refresh() {
    const detail = await load();
    if (detail === null) {
      return;
    }
    paintLists(detail);
    // A team that lost its last agent under the open page, or got one, is drawn again for it, but not over a form.
    if (form === null && (detail.members.length === 0) !== drawnEmpty) {
      greet(detail);
      return;
    }
    // The faces over its name work while one of its chats does.
    const stack = top.querySelector(".face-stack");
    if (stack !== null) {
      setActorState(stack, works(detail.chats) ? "working" : "idle");
    }
  }

  root.append(el("div", { class: "agent-page" }, el("a", { class: "back", href: "#/agents" }, icon("arrowLeft"), "All agents"), page));
  const stopLifecycle = onLifecycle((event) => {
    if (event.kind === "run_started" || event.kind === "run_finished") {
      attempt(refresh);
    }
  });
  const stopBack = onStreamBack(() => attempt(refresh));
  attempt(reload);
  return () => {
    disposed = true;
    stopLifecycle();
    stopBack();
    disposeComposer();
  };
}

export function mount(root, ctx) {
  return mountPage(root, ctx, ctx.params[0]);
}

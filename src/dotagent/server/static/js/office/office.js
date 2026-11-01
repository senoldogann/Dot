// The Office view of the Agents page: Dot and the owner's agents in a 3D room (office/scene.js), each a figure
// (office/figure.js) living its day by Dot's state of it (office/brain.js). A tag over each says its name and what it
// does; a click on a figure or its tag opens a panel with its face, what it works on, and a box to message it, which
// starts the run without leaving the room, so the agent is seen walking to its desk. Drag turns the room, the wheel or
// two fingers zoom. It draws only while it is in sight, and a still frame when motion is reduced.

import { DOT_ID, actorFace, lookOf } from "../actors.js";
import { api } from "../api.js";
import { clip, el } from "../dom.js";
import { icon } from "../icons.js";
import { toast, toastError } from "../ui.js";
import { getSnapshot, onChange, onLifecycle, onStreamBack } from "../state.js";
import { decide, heading, makeAgent, modeOf, step, visit } from "./brain.js";
import { runFeeds } from "./feeds.js";
import { makeFigure } from "./figure.js";
import { planFor } from "./layout.js";
import { gridFor } from "./nav.js";
import { buildRoom, isNight } from "./scene.js";
import { THREE, canDraw, loadThree } from "./three.js";

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
// A phone's frame is taller than it is wide (item 894, the owner's look at it on a phone): the room is seen more from
// above there, so that its floor fills the frame rather than a band across it.
const NARROW = window.matchMedia("(max-width: 640px)");
const PITCH = { wide: 0.62, narrow: 0.92 };

/** The angle the room is seen at, as the frame's width wants it. */
function restPitch() {
  return NARROW.matches ? PITCH.narrow : PITCH.wide;
}
// How long a sender stays at the desk of the agent it messaged, and how often an open panel follows a stream.
const VISIT_S = 7;
const PANEL_REPAINT_MS = 250;
// The gap between two frames while nothing walks: 30 a second.
const CALM_FRAME_MS = 32;
// How often the agents are read again besides the lifecycle stream, and how soon after an event.
const REREAD_MS = 20_000;
const EVENT_DELAY_MS = 400;
const MODE_WORDS = { working: "Working", waiting: "Waits for you", free: "" };
const POSE_WORDS = { sleep: "Napping", drink: "Coffee", rest: "On the sofa", look: "", think: "Thinking", sit: "" };

/**
 * @typedef {{ record: { id: string, name: string, role: string, look: object | null }, latest: { id: string, created_at?: number,
 *   status: string, goal: string, thread_id: string } | null }} Summary
 */

/**
 * Mount the office in `holder`. `composerFor(record, onStarted, follow, onSteered)` gives the box that messages an
 * agent (views/agents.js), which calls `onStarted(run)` instead of opening the run, and `onSteered(runId, text)` for a
 * message to its run at work. Returns `dispose()`.
 */
export function mountOffice(holder, { composerFor, openAgent }) {
  const frame = el("div", { class: "office-frame", role: "region", "aria-label": "The office: your agents at work" });
  const tags = el("div", { class: "office-tags" });
  const panelSlot = el("div", { class: "office-panel-slot" });
  const reset = el("button", { class: "btn icon office-reset", type: "button", title: "Back to the whole room", "aria-label": "Back to the whole room" }, icon("refresh"));
  // The office at the size of the window (item 901, the owner's word of 2026-11-01: "with this much room, why a small
  // square? it could be full size when we want"): over the page, Escape or the button again brings it back.
  const big = el("button", { class: "btn icon office-big", type: "button", title: "Full screen", "aria-label": "Full screen", "aria-pressed": "false" }, icon("maximize"));
  function setBig(on) {
    frame.classList.toggle("office-full", on);
    big.setAttribute("aria-pressed", String(on));
    big.title = on ? "Leave full screen" : "Full screen";
    big.setAttribute("aria-label", big.title);
    big.replaceChildren(icon(on ? "minimize" : "maximize"));
  }
  big.addEventListener("click", () => setBig(!frame.classList.contains("office-full")));
  // One Escape does one thing: one a menu or the box took for itself, or one that closes a dialog (a file, a question,
  // which the browser closes without saying so), leaves full screen as it is (the pre-push review).
  const leaveBig = (event) => {
    if (event.key !== "Escape" || event.defaultPrevented || !frame.classList.contains("office-full")) return;
    if (document.querySelector("dialog[open]") !== null) return;
    setBig(false);
  };
  frame.append(tags, panelSlot, reset);
  holder.replaceChildren(frame);
  let disposed = false;
  let stop = () => {};
  if (!canDraw()) {
    frame.classList.add("office-off");
    frame.append(el("p", { class: "office-note" }, "This browser draws no 3D, so the office cannot be shown here."));
    return { dispose() {} };
  }
  // Only where the room is drawn: a browser that draws none has nothing to show full screen.
  frame.append(big);
  document.addEventListener("keydown", leaveBig);
  loadThree()
    .then(() => {
      if (!disposed) {
        stop = start();
      }
    })
    .catch((error) => {
      console.warn("the office could not be drawn", error);
      // Nothing to show full screen: the note stands at its own size.
      setBig(false);
      big.remove();
      document.removeEventListener("keydown", leaveBig);
      frame.classList.add("office-off");
      frame.append(el("p", { class: "office-note" }, "The office could not be drawn here."));
    });

  function start() {
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    // The sun's shadows fall from the still room only: drawn when it is built and when day turns to night (item 889).
    renderer.shadowMap.autoUpdate = false;
    const canvas = renderer.domElement;
    canvas.classList.add("office-canvas");
    canvas.setAttribute("aria-hidden", "true");
    frame.prepend(canvas);

    const scene = new THREE.Scene();
    // A narrow lens from far: the room reads almost as a drawing of it, with depth enough to feel it.
    const camera = new THREE.PerspectiveCamera(22, 1, 0.1, 300);
    const hemisphere = new THREE.HemisphereLight("#fff6e8", "#b89a78", 1.1);
    const sun = new THREE.DirectionalLight("#fff0dc", 2.2);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.02;
    sun.shadow.radius = 4;
    const fill = new THREE.DirectionalLight("#dfe8ff", 0.35);
    scene.add(hemisphere, sun, sun.target, fill);

    /** @type {Map<string, { summary: Summary, figure: ReturnType<typeof makeFigure>, agent: ReturnType<typeof makeAgent>, tag: HTMLElement, word: HTMLElement, bubble: HTMLElement, visitWord: string, size: { w: number, h: number } | null, lift: number }>} */
    const actors = new Map();
    /** @type {ReturnType<typeof buildRoom> | null} */
    let room = null;
    /** @type {{ grid: ReturnType<typeof gridFor>, spots: import("./layout.js").Spot[], taken: Map<string, string> } | null} */
    let world = null;
    let plan = null;
    let layoutKey = "";
    let selected = null;
    let hovered = null;
    /** @type {{ dispose?: () => void } | null} */
    let panel = null;

    // The camera turns around the room's middle: its angle round, its height and its distance.
    const view = { yaw: Math.PI / 4, pitch: restPitch(), zoom: 1, target: new THREE.Vector3(0, 0.9, 0) };
    const goal = { yaw: view.yaw, pitch: view.pitch, zoom: view.zoom, target: view.target.clone() };
    // The distance at which the whole room fits the frame from where the camera looks, measured by projecting the
    // room's corners (a fixed factor cut its front off at one shape of the frame or another); kept per view.
    const corner = new THREE.Vector3();
    let fitted = { key: "", distance: 30 };
    function setCamera(distance) {
      camera.position.set(
        view.target.x + Math.sin(view.yaw) * Math.cos(view.pitch) * distance,
        view.target.y + Math.sin(view.pitch) * distance,
        view.target.z + Math.cos(view.yaw) * Math.cos(view.pitch) * distance,
      );
      camera.lookAt(view.target);
      camera.updateMatrixWorld();
    }
    function fitDistance() {
      if (plan === null) return 30;
      const key = `${plan.width}|${plan.depth}|${camera.aspect.toFixed(3)}|${view.yaw.toFixed(2)}|${view.pitch.toFixed(2)}`;
      if (key === fitted.key) return fitted.distance;
      const half = { x: plan.width / 2 + 0.2, z: plan.depth / 2 + 0.2 };
      let distance = 30;
      for (let round = 0; round < 4; round += 1) {
        setCamera(distance);
        let reach = 0;
        for (const x of [-half.x, half.x]) {
          for (const z of [-half.z, half.z]) {
            for (const y of [-0.35, 3.0]) {
              corner.set(x, y, z).project(camera);
              reach = Math.max(reach, Math.abs(corner.x), Math.abs(corner.y));
            }
          }
        }
        distance *= reach / 0.94;
      }
      fitted = { key, distance };
      return distance;
    }
    function placeCamera() {
      if (plan === null) return;
      const saved = view.target.clone();
      view.target.set(0, 0.9, 0);
      const distance = fitDistance();
      view.target.copy(saved);
      setCamera(distance * view.zoom);
    }

    function resize() {
      // The tags' sizes change with the frame's width (a phone's smaller type): read again.
      for (const actor of actors.values()) actor.size = null;
      const width = frame.clientWidth;
      const height = frame.clientHeight;
      renderer.setSize(width, height, false);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      camera.aspect = width / Math.max(1, height);
      camera.updateProjectionMatrix();
      placeCamera();
    }
    const resizer = new ResizeObserver(resize);
    resizer.observe(frame);

    /** Build the room for these agents (again when the desks or their names change). */
    function furnish(summaries) {
      const key = summaries.map((summary) => `${summary.record.id}:${summary.record.name}`).join("|");
      if (key === layoutKey && room !== null) {
        return;
      }
      layoutKey = key;
      if (room !== null) {
        scene.remove(room.root);
        freeGpu(room.root);
      }
      for (const actor of actors.values()) {
        scene.remove(actor.figure.group);
        freeGpu(actor.figure.group);
        actor.figure.dispose();
        actor.tag.remove();
      }
      actors.clear();
      plan = planFor(summaries.length);
      const names = new Map(summaries.map((summary, index) => [plan.desks[index].id, summary.record.name]));
      room = buildRoom(plan, names);
      scene.add(room.root);
      renderer.shadowMap.needsUpdate = true;
      world = { grid: gridFor(plan), spots: plan.spots, taken: new Map() };
      const extent = Math.max(plan.width, plan.depth);
      sun.position.set(-plan.width * 0.35, 9, -plan.depth * 0.6);
      sun.target.position.set(1, 0, 1);
      const shadow = sun.shadow.camera;
      shadow.left = -extent;
      shadow.right = extent;
      shadow.top = extent;
      shadow.bottom = -extent;
      shadow.far = 40;
      shadow.updateProjectionMatrix();
      fill.position.set(plan.width, 6, plan.depth);
      summaries.forEach((summary, index) => {
        const desk = plan.desks[index];
        const start = plan.spots[(index * 3) % plan.spots.length];
        const agent = makeAgent(summary.record.id, desk, { x: start.x + (index % 3) * 0.3, z: start.z + 0.4 });
        const figure = makeFigure(summary.record.id, lookOf(summary.record.id, summary.record.look), summary.record.id === DOT_ID);
        figure.group.position.set(agent.x, 0, agent.z);
        scene.add(figure.group);
        const word = el("span", { class: "office-tag-word" });
        // What a working agent does this moment, over its tag (item 889); the panel says it to a screen reader.
        const bubble = el("span", { class: "office-bubble", "aria-hidden": "true", hidden: true });
        const tag = el(
          "button",
          { class: "office-tag", type: "button", dataset: { agent: summary.record.id }, onclick: () => select(summary.record.id) },
          bubble,
          el("span", { class: "office-tag-dot", "aria-hidden": "true" }),
          el("span", { class: "office-tag-name" }, summary.record.name),
          word,
        );
        tags.append(tag);
        actors.set(summary.record.id, { summary, figure, agent, tag, word, bubble, visitWord: "", size: null, lift: 0 });
      });
      goal.target.set(0, 0.9, 0);
      placeCamera();
    }

    /** Take what the daemon says of the agents: each one's mode follows its newest run. */
    function learn(summaries) {
      furnish(summaries);
      for (const summary of summaries) {
        const actor = actors.get(summary.record.id);
        if (actor !== undefined) {
          actor.summary = summary;
        }
      }
      followWork();
      if (selected !== null) {
        paintPanel();
      }
    }

    // The streams of the working runs (item 889): each event moves the agent's bubble, its panel when open, and a message
    // to another agent sends the sender over to the other's desk.
    const feeds = runFeeds((runId, sentTo) => {
      const sender = [...actors.values()].find((actor) => actor.summary.latest?.id === runId);
      if (sender === undefined) return;
      if (sentTo !== null) walkOver(sender, sentTo);
      if (selected === sender.summary.record.id) panelSoon();
      // With motion reduced the room is drawn on a change only: a step is one.
      wake();
    });
    let panelTimer = 0;
    function panelSoon() {
      if (panelTimer !== 0) return;
      panelTimer = window.setTimeout(() => {
        panelTimer = 0;
        paintPanel();
      }, PANEL_REPAINT_MS);
    }
    function followWork() {
      const waiting = waitingRuns();
      const working = [...actors.values()]
        .filter((actor) => modeOf(actor.summary.latest, waiting) !== "free")
        .sort((one, other) => (other.summary.latest?.created_at ?? 0) - (one.summary.latest?.created_at ?? 0))
        .map((actor) => actor.summary.latest?.id ?? "");
      const open = selected === null ? undefined : actors.get(selected)?.summary.latest?.id;
      const workingIds = working.filter((id) => id !== "");
      feeds.sync([...new Set([...(open ? [open] : []), ...workingIds])], new Set(workingIds));
    }
    /** `sender` walks to the desk of the agent named `name` and talks there a while. */
    function walkOver(sender, name) {
      const wanted = name.toLowerCase();
      const other = [...actors.values()].find(
        (actor) => actor.summary.record.name.toLowerCase() === wanted || actor.summary.record.id === name,
      );
      if (other === undefined || other === sender || world === null) return;
      const desk = other.agent.desk;
      const away = Math.atan2(sender.agent.x - desk.x, sender.agent.z - desk.z);
      const point = { x: desk.x + Math.sin(away) * 0.9, z: desk.z + Math.cos(away) * 0.9 };
      visit(sender.agent, point, heading(point, desk), world, clockNow + VISIT_S);
      sender.visitWord = `Talking to ${other.summary.record.name}`;
    }

    // Help offers (item 896): the free agents the fast model matched with a run at work, read after the agents (the
    // first answer of a run may take seconds). A helper walks over to offer once per run; the owner lets it help from
    // either one's panel, which has the daemon tell the run at work, in a note of Dot's (never the owner's words).
    /** @type {{ helper_id: string, helper_name: string, helper_role: string, to_id: string, to_name: string, run_id: string, part: string }[]} */
    let offers = [];
    const offered = new Set();
    const letHelp = new Set();
    let offering = 0;
    async function readOffers() {
      const ticket = ++offering;
      try {
        const found = await api.get("/agents/offers");
        if (disposed || ticket !== offering) return;
        offers = found;
      } catch (error) {
        console.warn("the office could not read the help offers", error);
        return;
      }
      const waiting = waitingRuns();
      for (const offer of offers) {
        const key = `${offer.helper_id}|${offer.run_id}`;
        const helper = actors.get(offer.helper_id);
        if (offered.has(key) || helper === undefined || modeOf(helper.summary.latest, waiting) !== "free") continue;
        offered.add(key);
        walkOver(helper, offer.to_id);
        helper.visitWord = `Offers to help ${offer.to_name}`;
      }
      if (selected !== null) paintPanel();
      wake();
    }
    /** The offers the panel of `id` shows: those made to it while it works, and those it makes. */
    function offersOf(id) {
      return offers.filter((offer) => offer.to_id === id || offer.helper_id === id);
    }
    async function letItHelp(offer) {
      const key = `${offer.helper_id}|${offer.run_id}`;
      // Dot's note to the run at work, which the daemon writes from the helper's record (never the part the model
      // wrote): it clears no mark of content others can write, as the owner's own message would.
      try {
        await api.post("/agents/offers/help", { helper_id: offer.helper_id, run_id: offer.run_id });
        letHelp.add(key);
        toast("ok", `${offer.to_name} was told ${offer.helper_name} can help.`);
      } catch (error) {
        toastError(error);
      }
      paintPanel();
    }
    function offersView(id) {
      const mine = offersOf(id);
      if (mine.length === 0) return el("div", { class: "office-offers", hidden: true });
      return el(
        "div",
        { class: "office-offers" },
        el("div", { class: "office-offers-head" }, "Offers to help"),
        ...mine.map((offer) => {
          const key = `${offer.helper_id}|${offer.run_id}`;
          const done = letHelp.has(key);
          const who = offer.to_id === id ? offer.helper_name : `${offer.helper_name} → ${offer.to_name}`;
          return el(
            "div",
            { class: "office-offer" },
            el("div", { class: "office-offer-words" }, el("span", { class: "office-offer-name" }, who), offer.part === "" ? null : el("span", { class: "office-offer-part" }, offer.part)),
            el("button", { class: "btn sm", type: "button", disabled: done, onclick: () => letItHelp(offer) }, done ? "Told" : "Let it help"),
          );
        }),
      );
    }

    let reading = 0;
    async function read() {
      const ticket = ++reading;
      try {
        // Dot comes first in the list, with the newest run of its newest chat (item 633); an agent with its own.
        const agents = await api.get("/agents");
        if (disposed || ticket !== reading) return;
        const dot = agents.filter((summary) => summary.record.id === DOT_ID);
        learn([...dot, ...agents.filter((summary) => summary.record.id !== DOT_ID)]);
        wake();
        readOffers();
      } catch (error) {
        console.warn("the office could not read the agents", error);
      }
    }
    let soon = 0;
    function readSoon() {
      clearTimeout(soon);
      soon = window.setTimeout(read, EVENT_DELAY_MS);
    }

    function waitingRuns() {
      return new Set((getSnapshot().approvals ?? []).map((approval) => approval.run_id));
    }

    // Picking: the figure under the pointer, by its meshes.
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    function figureAt(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects([...actors.values()].map((actor) => actor.figure.group), true);
      return hits.length > 0 ? (hits[0].object.userData.figure ?? null) : null;
    }

    // The one in the panel keeps its full tag on a phone, where a free agent's is its name alone (item 894); set when it
    // is chosen, as a still room (reduced motion) draws no frame for it.
    function markChosen() {
      for (const actor of actors.values()) {
        actor.tag.toggleAttribute("data-chosen", actor.summary.record.id === selected);
        // A chosen tag is the full one on a phone: its size is read again.
        actor.size = null;
      }
    }

    function select(id) {
      selected = id;
      markChosen();
      followWork();
      const actor = actors.get(id);
      if (actor !== undefined) {
        goal.target.set(actor.agent.x * 0.6, 0.6, actor.agent.z * 0.6);
        goal.zoom = 0.72;
      }
      paintPanel();
    }

    function closePanel() {
      selected = null;
      markChosen();
      panel?.dispose?.();
      panel = null;
      panelSlot.replaceChildren();
      goal.target.set(0, 0.9, 0);
      goal.zoom = 1;
    }

    // The live work of the agent in the panel (item 889): its steps as they come, the newest last, and the answer as it
    // is written; a free agent's last run, as it ended.
    function feedView(feed, mode) {
      if (feed === undefined || (feed.steps.length === 0 && feed.text === "")) {
        return el("div", { class: "office-panel-live", hidden: true });
      }
      const steps = feed.steps
        .slice(-8)
        .map((one) =>
          el(
            "li",
            { class: "office-step", dataset: { state: one.ok === null ? "running" : one.ok ? "ok" : "failed" } },
            el("span", { class: "office-step-mark", "aria-hidden": "true" }),
            el("span", { class: "office-step-words" }, one.words),
          ),
        );
      const text = feed.text.trim();
      return el(
        "section",
        { class: "office-panel-live", "aria-label": mode === "free" ? "What it did last" : "What it does now", "aria-live": "polite" },
        steps.length > 0 ? el("ol", { class: "office-steps" }, ...steps) : null,
        text !== "" ? el("p", { class: "office-live-text" }, text.length > 420 ? `…${text.slice(-420)}` : text) : null,
      );
    }

    // The chat in the panel (item 898, the owner's word of 2026-11-01: "every message made a new chat; we should start
    // a conversation there and go on in it"): the agent's newest chat, its turns as the owner's words and the answers,
    // and the box sends on in it, to the run at work while there is one. "New chat" starts the next message afresh.
    const AUTOMATIC = new Set(["schedule", "watcher", "initiative", "notice"]);
    const TURNS_SHOWN = 6;
    /** @type {Map<string, { key: string, turns: { id: string, goal: string, result: string, status: string }[] }>} */
    const chats = new Map();
    const readingChats = new Set();
    const freshChat = new Set();
    // What the owner sent a run at work from here, shown at once, until the run has read it (its steer event, which
    // comes when the run takes it before its next step).
    /** @type {Map<string, string[]>} */
    const pending = new Map();
    /** Whether a message to the agent goes on in the chat of `latest`: one of the owner's, not a helper's or a schedule's. */
    function continues(latest) {
      return latest !== null && latest !== undefined && latest.parent_run_id === null && latest.thread_id !== "" && !AUTOMATIC.has(latest.source ?? "");
    }
    function followOf(id) {
      return () => {
        const latest = actors.get(id)?.summary.latest ?? null;
        if (freshChat.has(id) || !continues(latest)) return { threadId: null, working: null };
        // Waiting for the owner (blocked) is at work too: the message reaches that run, as in its chat (the review).
        const working = latest.status === "running" || latest.status === "queued" || latest.status === "blocked";
        return { threadId: latest.thread_id, working: working ? latest.id : null };
      };
    }
    async function readChat(threadId, key) {
      if (readingChats.has(threadId)) return;
      readingChats.add(threadId);
      try {
        const runs = await api.get(`/runs?thread_id=${encodeURIComponent(threadId)}&limit=20`);
        if (disposed) return;
        const turns = runs
          .filter((run) => run.parent_run_id === null)
          .slice(0, TURNS_SHOWN)
          .reverse()
          .map((run) => ({ id: run.id, goal: run.goal, result: run.result ?? "", status: run.status }));
        chats.set(threadId, { key, turns });
        panelSoon();
      } catch (error) {
        console.warn("the office could not read the chat", threadId, error);
      } finally {
        readingChats.delete(threadId);
      }
    }
    function chatView(id, latest) {
      if (freshChat.has(id)) {
        return el("div", { class: "office-chat" }, el("p", { class: "office-chat-note" }, "Your next message starts a new chat."));
      }
      // No chat of the owner's with it yet (or its newest is a schedule's or a helper's): an empty chat, ready for the
      // first message (item 901), not the last run's steps.
      if (!continues(latest)) {
        return el("div", { class: "office-chat" }, el("p", { class: "office-chat-note" }, "No chat yet. Write to start one."));
      }
      const key = `${latest.id}|${latest.status}`;
      const kept = chats.get(latest.thread_id);
      if (kept === undefined || kept.key !== key) readChat(latest.thread_id, key);
      const turns = kept?.turns ?? [];
      // The run at work's own: what the owner sent it as it worked, read from its events, and what waits for it.
      const said = feeds.feed(latest.id)?.said ?? [];
      // What waits is let go once the run has read it, or has ended (a queued one is a turn of its own then).
      const atWork = latest.status === "running" || latest.status === "queued" || latest.status === "blocked";
      const waiting = atWork ? (pending.get(latest.id) ?? []).filter((text) => !said.includes(text)) : [];
      if (waiting.length === 0) pending.delete(latest.id);
      else pending.set(latest.id, waiting);
      const sent = (turn) => (turn.id === latest.id ? [...said, ...waiting] : []);
      const view = el(
        "div",
        { class: "office-chat", role: "log", "aria-label": "The chat" },
        ...turns.flatMap((turn) => [
          el("div", { class: "office-turn office-turn-you" }, clip(turn.goal, 600)),
          ...sent(turn).map((text) => el("div", { class: "office-turn office-turn-you" }, clip(text, 600))),
          // The answer of a turn that ended; the one at work shows its steps and words live below.
          turn.status === "running" || turn.status === "queued" || turn.result === ""
            ? null
            : el("div", { class: "office-turn office-turn-agent" }, clip(turn.result, 1200)),
        ]),
      );
      // Kept where the owner scrolled it while the run works and the panel is painted again; to the foot when it was
      // there or a turn came (the review).
      // Only this agent's own chat as it was: another's scroll is no guide (the review).
      const before = panelSlot.dataset.agent === id ? panelSlot.querySelector(".office-chat") : null;
      const shown = view.querySelectorAll(".office-turn").length;
      const atFoot = before === null || before.scrollHeight - before.scrollTop - before.clientHeight < 8;
      const grew = before === null || shown > before.querySelectorAll(".office-turn").length;
      const keptTop = before?.scrollTop ?? 0;
      requestAnimationFrame(() => {
        view.scrollTop = atFoot || grew ? view.scrollHeight : keptTop;
      });
      return view;
    }

    function paintPanel() {
      const actor = selected === null ? undefined : actors.get(selected);
      if (actor === undefined) {
        closePanel();
        return;
      }
      const { record, latest } = actor.summary;
      const mode = modeOf(latest, waitingRuns());
      const doing = el(
        "p",
        { class: "office-panel-doing" },
        mode === "free" ? "Free right now." : `${MODE_WORDS[mode]}: ${latest?.goal ?? ""}`,
      );
      // Its steps and words live while it works or waits; a free agent's panel is its chat alone (item 901, the owner's
      // word: "the chat screens should be clean"), not the steps and the long answer of its last run.
      const live = mode === "free" ? el("div", { class: "office-panel-live", hidden: true }) : feedView(latest === null ? undefined : feeds.feed(latest.id), mode);
      const chat = chatView(record.id, latest);
      const links = el(
        "div",
        { class: "office-panel-links" },
        latest !== null ? el("a", { class: "btn ghost sm", href: `#/runs/${latest.id}` }, "Open its chat") : null,
        continues(latest) && !freshChat.has(record.id)
          ? el(
              "button",
              {
                class: "btn ghost sm",
                type: "button",
                onclick: () => {
                  freshChat.add(record.id);
                  paintPanel();
                },
              },
              "New chat",
            )
          : null,
        record.id !== DOT_ID ? el("button", { class: "btn ghost sm", type: "button", onclick: () => openAgent(record.id) }, "Its page") : null,
      );
      if (panel !== null && panel.dispose !== undefined && panelSlot.dataset.agent === record.id) {
        // The same agent: only what it does changes, the box keeps what was typed.
        panelSlot.querySelector(".office-panel-doing")?.replaceWith(doing);
        panelSlot.querySelector(".office-chat")?.replaceWith(chat);
        panelSlot.querySelector(".office-panel-live")?.replaceWith(live);
        panelSlot.querySelector(".office-panel-links")?.replaceWith(links);
        panelSlot.querySelector(".office-offers")?.replaceWith(offersView(record.id));
        return;
      }
      panel?.dispose?.();
      const box = composerFor(
        record,
        (run) => {
          actor.summary = { ...actor.summary, latest: run };
          freshChat.delete(record.id);
          toast("ok", `${record.name} is on it.`);
          readSoon();
          panelSoon();
        },
        followOf(record.id),
        (runId, text, queued) => {
          pending.set(runId, [...(pending.get(runId) ?? []), text]);
          toast("ok", queued ? `Queued: it goes to ${record.name} once the work under way ends.` : `Sent to ${record.name}'s work under way.`);
          panelSoon();
        },
      );
      panel = box;
      panelSlot.dataset.agent = record.id;
      panelSlot.replaceChildren(
        el(
          "aside",
          { class: "office-panel", "aria-label": `${record.name} in the office` },
          el(
            "div",
            { class: "office-panel-head" },
            actorFace(record.id, mode === "working" ? "working" : mode === "waiting" ? "waiting" : "idle", record.look),
            el("div", { class: "office-panel-who" }, el("div", { class: "office-panel-name" }, record.name), el("div", { class: "office-panel-role" }, record.role || "Dot")),
            el("button", { class: "btn icon ghost", type: "button", title: "Close", "aria-label": "Close", onclick: closePanel }, icon("x")),
          ),
          doing,
          chat,
          live,
          offersView(record.id),
          links,
          box.element,
        ),
      );
    }

    // Turning and zooming: a drag turns, the wheel and a pinch zoom; a press without a drag picks.
    const presses = new Map();
    let dragged = 0;
    canvas.addEventListener("pointerdown", (event) => {
      canvas.setPointerCapture(event.pointerId);
      presses.set(event.pointerId, { x: event.clientX, y: event.clientY });
      dragged = 0;
    });
    // The figure under the pointer is looked for once a frame, not at every move of the mouse.
    let pointerAt = null;
    function pick() {
      if (pointerAt === null) return;
      const over = figureAt(pointerAt.x, pointerAt.y);
      pointerAt = null;
      if (over !== hovered) {
        hovered = over;
        canvas.style.cursor = over === null ? "grab" : "pointer";
      }
    }
    canvas.addEventListener("pointermove", (event) => {
      const press = presses.get(event.pointerId);
      if (press === undefined) {
        pointerAt = { x: event.clientX, y: event.clientY };
        return;
      }
      const dx = event.clientX - press.x;
      const dy = event.clientY - press.y;
      if (presses.size === 2) {
        const [one, other] = [...presses.entries()];
        const before = Math.hypot(one[1].x - other[1].x, one[1].y - other[1].y);
        presses.set(event.pointerId, { x: event.clientX, y: event.clientY });
        const [a, b] = [...presses.values()];
        const after = Math.hypot(a.x - b.x, a.y - b.y);
        goal.zoom = clamp(goal.zoom * (before / Math.max(1, after)), 0.45, 1.35);
      } else {
        goal.yaw = clamp(goal.yaw - dx * 0.006, 0.05, Math.PI / 2 - 0.05);
        goal.pitch = clamp(goal.pitch + dy * 0.004, 0.32, 1.1);
        presses.set(event.pointerId, { x: event.clientX, y: event.clientY });
      }
      dragged += Math.abs(dx) + Math.abs(dy);
    });
    const release = (event) => {
      if (presses.size === 1 && dragged < 6) {
        const picked = figureAt(event.clientX, event.clientY);
        if (picked !== null) select(picked);
      }
      presses.delete(event.pointerId);
    };
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", (event) => presses.delete(event.pointerId));
    canvas.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        goal.zoom = clamp(goal.zoom * Math.exp(event.deltaY * 0.0012), 0.45, 1.35);
      },
      { passive: false },
    );
    reset.addEventListener("click", () => {
      goal.yaw = Math.PI / 4;
      goal.pitch = restPitch();
      goal.zoom = 1;
      goal.target.set(0, 0.9, 0);
    });

    // The loop: only while the room is in sight and the page shown.
    let seen = true;
    let frameId = 0;
    let ahead = 0;
    let clockNow = 0;
    let wasNight = null;
    let last = performance.now();
    const sightings = new IntersectionObserver((entries) => {
      seen = entries.some((entry) => entry.isIntersecting);
      if (seen) loop();
    });
    sightings.observe(frame);
    const projected = new THREE.Vector3();

    function tick(now, dt) {
      clockNow = now;
      const date = new Date();
      const hour = date.getHours();
      const waiting = waitingRuns();
      const night = isNight(hour);
      if (night !== wasNight) {
        wasNight = night;
        renderer.shadowMap.needsUpdate = true;
      }
      hemisphere.intensity = night ? 0.45 : 1.1;
      sun.intensity = night ? 0.35 : 2.2;
      sun.color.set(night ? "#9fb4ff" : "#fff0dc");
      for (const lamp of room?.lamps ?? []) {
        lamp.light.intensity = night ? 3 : 0.8;
      }
      for (const actor of actors.values()) {
        const mode = modeOf(actor.summary.latest, waiting);
        if (world !== null) {
          decide(actor.agent, mode, world, now, hour, Math.random);
          step(actor.agent, dt, now, hour, Math.random);
        }
        actor.figure.group.position.set(actor.agent.x, 0, actor.agent.z);
        actor.figure.group.rotation.y = actor.agent.facing;
        actor.figure.setPose(actor.agent.pose);
        actor.figure.setHover(hovered === actor.summary.record.id || selected === actor.summary.record.id);
        actor.figure.update(dt, now, actor.agent.walked);
        const screen = room?.screens.get(actor.agent.desk.id);
        if (screen !== undefined) {
          screen.emissiveIntensity += ((actor.agent.pose === "type" ? 0.9 : 0.04) - screen.emissiveIntensity) * Math.min(1, dt * 3);
        }
        const visiting = actor.agent.visiting > now ? (actor.visitWord ?? "") : "";
        const doing = mode === "working" ? (feeds.feed(actor.summary.latest?.id ?? "")?.doing ?? "") : "";
        const word = visiting !== "" ? visiting : mode !== "free" ? MODE_WORDS[mode] : (POSE_WORDS[actor.agent.pose] ?? "");
        if (actor.bubble.textContent !== doing) {
          actor.bubble.textContent = doing;
          actor.size = null;
          actor.bubble.hidden = doing === "";
        }
        if (actor.word.textContent !== word) {
          actor.word.textContent = word;
          actor.size = null;
        }
        const about = mode === "free" ? actor.summary.record.name : `${actor.summary.record.name}: ${actor.summary.latest?.goal ?? ""}`;
        if (actor.tag.title !== about) actor.tag.title = about;
        actor.tag.dataset.mode = mode;
        // A tag made after the choice (the agents read again) learns it here too.
        if (actor.tag.hasAttribute("data-chosen") !== (actor.summary.record.id === selected)) markChosen();
      }
      room?.tick(now, date);
      view.yaw += (goal.yaw - view.yaw) * Math.min(1, dt * 8);
      view.pitch += (goal.pitch - view.pitch) * Math.min(1, dt * 8);
      view.zoom += (goal.zoom - view.zoom) * Math.min(1, dt * 6);
      view.target.lerp(goal.target, Math.min(1, dt * 5));
      placeCamera();
    }

    // The tags over the heads, the nearer first; one that would cover another is lifted above it. The lift glides there
    // rather than jumps, and a tag's size is read again only when its words
    // change (the owner's word of 2026-11-01: the names moved jerkily over the heads; reading every tag's size at every
    // frame made the page lay out sixty times a second).
    function sizeOf(actor) {
      if (actor.size === null) {
        const bubble = actor.bubble.hidden ? 0 : actor.bubble.offsetHeight + 4;
        actor.size = {
          w: Math.max(actor.tag.offsetWidth || 90, actor.bubble.hidden ? 0 : actor.bubble.offsetWidth),
          h: (actor.tag.offsetHeight || 22) + bubble,
        };
      }
      return actor.size;
    }
    // One tag's size read again at each frame, in turn: a size read before the page's type settled (a phone's smaller
    // tags, a font that loaded late) is right again within a few frames, at one read a frame.
    let remeasured = 0;
    function placeTags(dt) {
      const all = [...actors.values()];
      if (all.length > 0) {
        remeasured = (remeasured + 1) % all.length;
        all[remeasured].size = null;
      }
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      const placed = [];
      const order = [...actors.values()]
        .map((actor) => {
          projected.copy(actor.figure.top()).project(camera);
          return { actor, x: (projected.x * 0.5 + 0.5) * width, y: (-projected.y * 0.5 + 0.5) * height, depth: projected.z };
        })
        .sort((one, other) => one.depth - other.depth);
      for (const item of order) {
        // A bubble over the tag is part of what must not cover another.
        const { w: tagWidth, h: tagHeight } = sizeOf(item.actor);
        let y = item.y;
        // Each placed tag takes the band [y - h, y] above its anchor: one that would overlap another is lifted over it.
        for (let tries = 0; tries < 8; tries += 1) {
          const clash = placed.find(
            (other) => Math.abs(other.x - item.x) < (other.w + tagWidth) / 2 + 2 && y > other.y - other.h - 2 && y - tagHeight < other.y + 2,
          );
          if (clash === undefined) break;
          y = clash.y - clash.h - 3;
        }
        placed.push({ x: item.x, y, w: tagWidth, h: tagHeight });
        // The lift above its anchor glides toward where the tag must stand, a sixth of a second or so.
        const lift = item.y - y;
        item.actor.lift = dt <= 0 ? lift : item.actor.lift + (lift - item.actor.lift) * Math.min(1, dt * 10);
        const shownY = item.y - item.actor.lift;
        // Whole pixels: a tag placed between two lost a part of a pixel of its height on a phone, under a finger's
        // 32 (test_phone_type); the glide keeps it from jumping.
        item.actor.tag.style.transform = `translate3d(${Math.round(item.x)}px, ${Math.round(shownY)}px, 0) translate(-50%, -100%)`;
        item.actor.tag.hidden = item.depth > 1;
      }
    }

    function loop() {
      cancelAnimationFrame(frameId);
      // Nothing is drawn with nobody looking: out of sight, the page hidden, or Dot's window not the one in front
      // (main.js marks the page away, item 662, which stills every endless animation too).
      if (disposed || !seen || document.hidden || document.documentElement.hasAttribute("data-away")) return;
      frameId = requestAnimationFrame((time) => {
        const moving = !REDUCED.matches;
        // Half the frames while nothing walks and the camera rests: breathing and typing read the same at 30 a second
        // (item 889), and the GPU idles in between.
        if (moving && !busy() && time - drawnAt < CALM_FRAME_MS) {
          loop();
          return;
        }
        pace(time - drawnAt);
        drawnAt = time;
        const dt = Math.min(0.05, (time - last) / 1000);
        last = time;
        pick();
        // The simulation's clock is the frame's time plus what settling skipped ahead: it only goes forward, and it
        // keeps moving when motion comes back on (the pre-push review).
        if (moving) {
          tick(time / 1000 + ahead, dt);
        } else {
          // Motion reduced: the agents are where they end up, drawn once each time something changed.
          for (let settle = 0; settle < 40; settle += 1) {
            ahead += 0.5;
            tick(time / 1000 + ahead, 0.5);
          }
        }
        const began = performance.now();
        renderer.render(scene, camera);
        measure(performance.now() - began);
        placeTags(moving ? dt : 0);
        if (moving) {
          loop();
        }
      });
    }
    // Whether something moves that 30 frames a second would show stepping: an agent walking or sitting down, or the
    // camera on its way.
    function busy() {
      const turning = Math.abs(goal.yaw - view.yaw) + Math.abs(goal.pitch - view.pitch) + Math.abs(goal.zoom - view.zoom);
      if (turning > 1e-3 || goal.target.distanceTo(view.target) > 1e-3 || presses.size > 0) return true;
      for (const actor of actors.values()) {
        if (actor.agent.path.length > 0 || actor.agent.settle !== 0) return true;
      }
      return false;
    }
    // The drawing's resolution follows the frames (item 889): a Mac or a phone that cannot keep up draws fewer pixels,
    // down to one per CSS pixel, and gets them back once it can.
    const most = Math.min(window.devicePixelRatio, 2);
    let ratio = most;
    let drawnAt = 0;
    const gaps = [];
    function pace(gap) {
      if (gap <= 0 || gap > 250) return;
      gaps.push(gap);
      if (gaps.length < 45) return;
      const sorted = [...gaps].sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)];
      gaps.length = 0;
      const calm = !busy();
      const budget = calm ? CALM_FRAME_MS * 1.6 : 24;
      const before = ratio;
      if (median > budget && ratio > 1) {
        ratio = Math.max(1, ratio - 0.25);
      } else if (median < (calm ? CALM_FRAME_MS * 1.15 : 18) && ratio < most) {
        ratio = Math.min(most, ratio + 0.25);
      }
      if (ratio !== before) {
        // Only on a change: setting it sizes the canvas again.
        renderer.setPixelRatio(ratio);
        resize();
      }
      frame.dataset.pixelRatio = String(ratio);
    }
    // What drawing costs, on the frame for a test (and anyone curious) to read: the draw calls and triangles of the
    // last frame, and the median milliseconds of the newest frames' render calls.
    const spent = [];
    function measure(ms) {
      spent.push(ms);
      if (spent.length > 60) spent.shift();
      const sorted = [...spent].sort((a, b) => a - b);
      frame.dataset.drawCalls = String(renderer.info.render.calls);
      frame.dataset.triangles = String(renderer.info.render.triangles);
      frame.dataset.renderMs = sorted[Math.floor(sorted.length / 2)].toFixed(2);
    }
    function wake() {
      if (REDUCED.matches) loop();
    }
    const shown = () => {
      last = performance.now();
      loop();
    };
    document.addEventListener("visibilitychange", shown);
    // Motion switched back on (or off) in the Mac's settings: the loop starts again, or draws its still frame.
    REDUCED.addEventListener("change", shown);
    // A window made narrow or wide, or a phone turned: the room is seen at the angle its new width wants.
    const reangle = () => {
      goal.pitch = restPitch();
    };
    NARROW.addEventListener("change", reangle);
    const away = new MutationObserver(shown);
    away.observe(document.documentElement, { attributes: true, attributeFilter: ["data-away"] });

    const unsubscribe = [
      onLifecycle(() => readSoon()),
      onStreamBack(() => readSoon()),
      onChange(() => wake()),
    ];
    const every = window.setInterval(read, REREAD_MS);
    read().then(() => {
      resize();
      loop();
    });

    return () => {
      cancelAnimationFrame(frameId);
      clearInterval(every);
      clearTimeout(soon);
      for (const off of unsubscribe) off();
      document.removeEventListener("visibilitychange", shown);
      away.disconnect();
      REDUCED.removeEventListener("change", shown);
      NARROW.removeEventListener("change", reangle);
      sightings.disconnect();
      resizer.disconnect();
      panel?.dispose?.();
      feeds.dispose();
      clearTimeout(panelTimer);
      for (const actor of actors.values()) {
        freeGpu(actor.figure.group);
        actor.figure.dispose();
      }
      if (room !== null) freeGpu(room.root);
      renderer.dispose();
      // dispose() leaves the context open: losing it gives it back now, not at the next collection (Dot.app stays
      // open for days, and a browser keeps only so many).
      renderer.forceContextLoss();
    };
  }

  return {
    dispose() {
      disposed = true;
      document.removeEventListener("keydown", leaveBig);
      stop();
    },
  };
}

/** Give back the GPU's copies of what `root` holds: its geometries, materials and their textures. A material shared
 * with a later room is uploaded again when it is drawn. */
function freeGpu(root) {
  root.traverse((node) => {
    const drawn = /** @type {{ geometry?: { dispose: () => void }, material?: unknown }} */ (node);
    drawn.geometry?.dispose();
    const materials = Array.isArray(drawn.material) ? drawn.material : drawn.material ? [drawn.material] : [];
    for (const material of materials) {
      for (const value of Object.values(material)) {
        if (value !== null && typeof value === "object" && "isTexture" in value) value.dispose();
      }
      material.dispose();
    }
  });
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

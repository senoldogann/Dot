// The Office view of the Agents page: Dot and the owner's agents in a 3D room (office/scene.js), each a figure
// (office/figure.js) living its day by Dot's state of it (office/brain.js). A tag over each says its name and what it
// does; a click on a figure or its tag opens a panel with its face, what it works on, and a box to message it, which
// starts the run without leaving the room, so the agent is seen walking to its desk. Drag turns the room, the wheel or
// two fingers zoom. It draws only while it is in sight, and a still frame when motion is reduced.

import { DOT_ID, actorFace, lookOf } from "../actors.js";
import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { getSnapshot, onChange, onLifecycle, onStreamBack } from "../state.js";
import { decide, makeAgent, modeOf, step } from "./brain.js";
import { makeFigure } from "./figure.js";
import { planFor } from "./layout.js";
import { gridFor } from "./nav.js";
import { buildRoom, isNight } from "./scene.js";
import { THREE, canDraw, loadThree } from "./three.js";

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
// How often the agents are read again besides the lifecycle stream, and how soon after an event.
const REREAD_MS = 20_000;
const EVENT_DELAY_MS = 400;
const MODE_WORDS = { working: "Working", waiting: "Waits for you", free: "" };
const POSE_WORDS = { sleep: "Napping", drink: "Coffee", rest: "On the sofa", look: "", think: "Thinking", sit: "" };

/**
 * @typedef {{ record: { id: string, name: string, role: string, look: object | null }, latest: { id: string,
 *   status: string, goal: string, thread_id: string } | null }} Summary
 */

/**
 * Mount the office in `holder`. `composerFor(record, onStarted)` gives the box that messages an agent (views/agents.js),
 * which calls `onStarted(run)` instead of opening the run. Returns `dispose()`.
 */
export function mountOffice(holder, { composerFor, openAgent }) {
  const frame = el("div", { class: "office-frame", role: "region", "aria-label": "The office: your agents at work" });
  const tags = el("div", { class: "office-tags" });
  const panelSlot = el("div", { class: "office-panel-slot" });
  const reset = el("button", { class: "btn icon office-reset", type: "button", title: "Back to the whole room", "aria-label": "Back to the whole room" }, icon("refresh"));
  frame.append(tags, panelSlot, reset);
  holder.replaceChildren(frame);
  let disposed = false;
  let stop = () => {};
  if (!canDraw()) {
    frame.classList.add("office-off");
    frame.append(el("p", { class: "office-note" }, "This browser draws no 3D, so the office cannot be shown here."));
    return { dispose() {} };
  }
  loadThree()
    .then(() => {
      if (!disposed) {
        stop = start();
      }
    })
    .catch((error) => {
      console.warn("the office could not be drawn", error);
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

    /** @type {Map<string, { summary: Summary, figure: ReturnType<typeof makeFigure>, agent: ReturnType<typeof makeAgent>, tag: HTMLElement, word: HTMLElement }>} */
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
    const view = { yaw: Math.PI / 4, pitch: 0.62, zoom: 1, target: new THREE.Vector3(0, 0.9, 0) };
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
        const tag = el(
          "button",
          { class: "office-tag", type: "button", dataset: { agent: summary.record.id }, onclick: () => select(summary.record.id) },
          el("span", { class: "office-tag-dot", "aria-hidden": "true" }),
          el("span", { class: "office-tag-name" }, summary.record.name),
          word,
        );
        tags.append(tag);
        actors.set(summary.record.id, { summary, figure, agent, tag, word });
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
      if (selected !== null) {
        paintPanel();
      }
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

    function select(id) {
      selected = id;
      const actor = actors.get(id);
      if (actor !== undefined) {
        goal.target.set(actor.agent.x * 0.6, 0.6, actor.agent.z * 0.6);
        goal.zoom = 0.72;
      }
      paintPanel();
    }

    function closePanel() {
      selected = null;
      panel?.dispose?.();
      panel = null;
      panelSlot.replaceChildren();
      goal.target.set(0, 0.9, 0);
      goal.zoom = 1;
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
      const links = el(
        "div",
        { class: "office-panel-links" },
        latest !== null ? el("a", { class: "btn ghost sm", href: `#/runs/${latest.id}` }, "Open its chat") : null,
        record.id !== DOT_ID ? el("button", { class: "btn ghost sm", type: "button", onclick: () => openAgent(record.id) }, "Its page") : null,
      );
      if (panel !== null && panel.dispose !== undefined && panelSlot.dataset.agent === record.id) {
        // The same agent: only what it does changes, the box keeps what was typed.
        panelSlot.querySelector(".office-panel-doing")?.replaceWith(doing);
        panelSlot.querySelector(".office-panel-links")?.replaceWith(links);
        return;
      }
      panel?.dispose?.();
      const box = composerFor(record, (run) => {
        actor.summary = { ...actor.summary, latest: run };
        readSoon();
      });
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
    canvas.addEventListener("pointermove", (event) => {
      const press = presses.get(event.pointerId);
      if (press === undefined) {
        const over = figureAt(event.clientX, event.clientY);
        if (over !== hovered) {
          hovered = over;
          canvas.style.cursor = over === null ? "grab" : "pointer";
        }
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
      goal.pitch = 0.62;
      goal.zoom = 1;
      goal.target.set(0, 0.9, 0);
    });

    // The loop: only while the room is in sight and the page shown.
    let seen = true;
    let frameId = 0;
    let ahead = 0;
    let last = performance.now();
    const sightings = new IntersectionObserver((entries) => {
      seen = entries.some((entry) => entry.isIntersecting);
      if (seen) loop();
    });
    sightings.observe(frame);
    const projected = new THREE.Vector3();

    function tick(now, dt) {
      const date = new Date();
      const hour = date.getHours();
      const waiting = waitingRuns();
      const night = isNight(hour);
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
        const word = mode !== "free" ? MODE_WORDS[mode] : (POSE_WORDS[actor.agent.pose] ?? "");
        if (actor.word.textContent !== word) actor.word.textContent = word;
        const about = mode === "free" ? actor.summary.record.name : `${actor.summary.record.name}: ${actor.summary.latest?.goal ?? ""}`;
        if (actor.tag.title !== about) actor.tag.title = about;
        actor.tag.dataset.mode = mode;
      }
      room?.tick(now, date);
      view.yaw += (goal.yaw - view.yaw) * Math.min(1, dt * 8);
      view.pitch += (goal.pitch - view.pitch) * Math.min(1, dt * 8);
      view.zoom += (goal.zoom - view.zoom) * Math.min(1, dt * 6);
      view.target.lerp(goal.target, Math.min(1, dt * 5));
      placeCamera();
    }

    // The tags over the heads, the nearer first; one that would cover another is lifted above it.
    function placeTags() {
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
        const tagWidth = item.actor.tag.offsetWidth || 90;
        const tagHeight = item.actor.tag.offsetHeight || 22;
        let y = item.y;
        for (let tries = 0; tries < 6; tries += 1) {
          const clash = placed.find((other) => Math.abs(other.x - item.x) < (other.w + tagWidth) / 2 + 2 && Math.abs(other.y - y) < tagHeight + 2);
          if (clash === undefined) break;
          y = clash.y - tagHeight - 3;
        }
        placed.push({ x: item.x, y, w: tagWidth });
        item.actor.tag.style.transform = `translate(${Math.round(item.x)}px, ${Math.round(y)}px) translate(-50%, -100%)`;
        item.actor.tag.hidden = item.depth > 1;
      }
    }

    function loop() {
      cancelAnimationFrame(frameId);
      // Nothing is drawn with nobody looking: out of sight, the page hidden, or Dot's window not the one in front
      // (main.js marks the page away, item 662, which stills every endless animation too).
      if (disposed || !seen || document.hidden || document.documentElement.hasAttribute("data-away")) return;
      frameId = requestAnimationFrame((time) => {
        const dt = Math.min(0.05, (time - last) / 1000);
        last = time;
        const moving = !REDUCED.matches;
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
        renderer.render(scene, camera);
        placeTags();
        if (moving) {
          loop();
        }
      });
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
      sightings.disconnect();
      resizer.disconnect();
      panel?.dispose?.();
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

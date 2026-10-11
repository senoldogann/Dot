// A character on the page: bloub's engine drawn into an SVG built with DOM calls and driven by the page's ticker.
// `mountCharacter(parent, options)` appends the SVG and returns a handle whose setters change the state (the
// animation), the rest expression, the shape and the colour, point the gaze, freeze and resume the motion, and
// remove it. A character that is not moving (`moving: false`, `freeze()`, or `prefers-reduced-motion`) shows one
// still frame of its state at the date where the state reads best, and never joins the loop.
import { svg } from "../dom.js";
import { CharacterEngine } from "./engine.js";
import { DEFAULT_EXPRESSION, EXPRESSION_BY_ID } from "./expressions.js";
import { HALF_VIEWBOX, RADIUS } from "./repere.js";
import { COLOUR_BY_ID, DEFAULT_COLOUR, DEFAULT_SHAPE, SHAPE_BY_ID } from "./skins.js";
import { POSES, STATE_BY_ID } from "./states.js";
import { ticker } from "./ticker.js";

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
let nextId = 0;

function known(map, id, what) {
  const found = map.get(id);
  if (!found) throw new RangeError(`unknown character ${what}: ${id}`);
  return found;
}

// Keeps a group's children in step with a list: one child per item, made on first sight, updated every frame,
// removed when its item is gone.
function sync(group, items, keyOf, make, update) {
  const existing = new Map();
  for (const child of group.children) existing.set(child.dataset.key, child);
  const seen = new Set();
  for (const item of items) {
    const key = keyOf(item);
    seen.add(key);
    let node = existing.get(key);
    if (!node) {
      node = make(item);
      node.dataset.key = key;
      group.append(node);
    }
    update(node, item);
  }
  for (const [key, node] of existing) if (!seen.has(key)) node.remove();
}

function setAttributes(node, attributes) {
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
}

// A dot is a disc, except when the state gives it a shape (the teardrop of the leaning "!"): that path is in ball
// radii about the origin, so it is placed with translate, rotate and scale. A particle's depth fades it into the
// ground.
function dotNode(dot) {
  return svg(dot.d ? "path" : "circle", null);
}

function paintDot(node, dot) {
  const opacity = dot.opacity * (dot.depth === undefined ? 1 : dot.depth);
  if (dot.d) setAttributes(node, { d: dot.d, transform: `translate(${dot.x} ${dot.y}) rotate(${dot.rot ?? 0}) scale(${RADIUS})`, opacity });
  else setAttributes(node, { cx: dot.x, cy: dot.y, r: dot.r, opacity });
}

function arcPath() {
  return svg("path", null);
}

function gradientNode(uid, arc) {
  return svg(
    "linearGradient",
    { id: `${uid}-${arc.id}`, gradientUnits: "userSpaceOnUse" },
    ...arc.grad.stops.map((_, i) => svg("stop", { offset: i / (arc.grad.stops.length - 1) })),
  );
}

export function mountCharacter(parent, options) {
  const uid = `character-${nextId++}`;
  const size = options.size ?? 96;
  const vb = HALF_VIEWBOX;
  const box = { x: -vb, y: -vb, width: vb * 2, height: vb * 2 };

  const maskBody = svg("path", { fill: "#fff" });
  const maskEyes = svg("g", { fill: "#000" });
  const notch = svg("circle", { fill: "#000", r: 0 });
  const mask = svg("mask", { id: `${uid}-mask`, maskUnits: "userSpaceOnUse", ...box }, maskBody, maskEyes, notch);
  const gradients = svg("g", null);
  const defs = svg("defs", null, mask, gradients);
  // The back half of the orbits is drawn before the body, so the body hides it.
  const back = svg("g", { class: "character-arcs", fill: "none", "stroke-linecap": "round" });
  const dotsBehind = svg("g", { class: "character-dots" });
  // The eyes are holes in the body; an opaque ground in the body's exact shape sits under it so what is drawn
  // behind (the rings' back half, the particles) does not show through them.
  const paper = svg("path", { class: "character-paper" });
  const ink = svg("rect", { class: "character-ink", ...box });
  const body = svg("g", null, paper, svg("g", { mask: `url(#${uid}-mask)` }, ink));
  const dotsFront = svg("g", { class: "character-dots" });
  const notif = svg("circle", { class: "character-notif", r: 0 });
  const front = svg("g", { class: "character-arcs", fill: "none", "stroke-linecap": "round" });
  const element = svg(
    "svg",
    {
      class: "character",
      viewBox: `${-vb} ${-vb} ${vb * 2} ${vb * 2}`,
      width: size,
      height: size,
      role: options.label ? "img" : null,
      "aria-label": options.label || null,
      "aria-hidden": options.label ? null : "true",
    },
    defs,
    back,
    dotsBehind,
    body,
    dotsFront,
    notif,
    front,
  );

  let shape = known(SHAPE_BY_ID, options.shape ?? DEFAULT_SHAPE, "shape");
  let expression = known(EXPRESSION_BY_ID, options.expression ?? DEFAULT_EXPRESSION, "expression");
  let state = options.state ?? "idle";
  known(STATE_BY_ID, state, "state");
  const engine = new CharacterEngine(RADIUS, state, shape.radii, expression);
  // Each character keeps its own clock, started at a different second so two faces do not blink as one.
  let clock = options.phase ?? Math.random() * 7;
  let wanted = options.moving ?? true;
  let subscribed = false;

  const paintArcs = (group, arcs, side) =>
    sync(group, arcs, (arc) => arc.id, arcPath, (node, arc) =>
      setAttributes(node, { d: arc[side], stroke: `url(#${uid}-${arc.id})`, "stroke-width": arc.width, opacity: arc.opacity }),
    );

  function render(frame) {
    maskBody.setAttribute("d", frame.bodyPath);
    paper.setAttribute("d", frame.bodyPath);
    body.setAttribute("opacity", String(frame.bodyAlpha));
    sync(maskEyes, frame.eyes, (_, i) => String(i), () => svg("path", null), (node, eye) =>
      setAttributes(node, { d: eye.d, transform: eye.matrix, opacity: eye.alpha }),
    );
    setAttributes(notch, frame.notch ? { cx: frame.notch.x, cy: frame.notch.y, r: frame.notch.r } : { r: 0 });
    setAttributes(notif, frame.notif ? { cx: frame.notif.x, cy: frame.notif.y, r: frame.notif.r } : { r: 0 });
    sync(gradients, frame.arcs, (arc) => arc.id, (arc) => gradientNode(uid, arc), (node, arc) => {
      setAttributes(node, { x1: arc.grad.x1, y1: arc.grad.y1, x2: arc.grad.x2, y2: arc.grad.y2 });
      for (const [i, stop] of [...node.children].entries()) stop.setAttribute("stop-color", arc.grad.stops[i]);
    });
    paintArcs(back, frame.arcs, "back");
    paintArcs(front, frame.arcs, "front");
    const dots = frame.dots.map((dot, i) => ({ ...dot, key: `${i}:${dot.d ? "p" : "c"}` }));
    sync(frame.dotsBehind ? dotsBehind : dotsFront, dots, (dot) => dot.key, dotNode, paintDot);
    sync(frame.dotsBehind ? dotsFront : dotsBehind, [], () => "", dotNode, paintDot);
  }

  // The frame at `clock`, or the state's best-read frame when still.
  function still() {
    engine.reset(state, 0);
    render(engine.sample(POSES[state] ?? 1));
  }

  function tick(dt) {
    clock += dt;
    render(engine.sample(clock));
  }

  function moving() {
    return wanted && !REDUCED.matches;
  }

  function apply() {
    const now = moving();
    element.dataset.still = now ? "false" : "true";
    if (now && !subscribed) {
      ticker.add(tick);
      subscribed = true;
      render(engine.sample(clock));
    } else if (!now && subscribed) {
      ticker.remove(tick);
      subscribed = false;
      still();
    } else if (!now) {
      still();
    }
  }

  const onReduced = () => apply();
  REDUCED.addEventListener("change", onReduced);

  element.dataset.state = state;
  element.dataset.colour = known(COLOUR_BY_ID, options.colour ?? DEFAULT_COLOUR, "colour").id;
  parent.append(element);
  apply();

  return {
    element,
    get state() {
      return state;
    },
    setState(id) {
      known(STATE_BY_ID, id, "state");
      if (id === state) return;
      state = id;
      element.dataset.state = id;
      if (subscribed) engine.setState(id, clock);
      else still();
    },
    setExpression(id) {
      expression = known(EXPRESSION_BY_ID, id, "expression");
      engine.setExpression(expression, clock);
      if (!subscribed) still();
    },
    setShape(id) {
      shape = known(SHAPE_BY_ID, id, "shape");
      engine.setShape(shape.radii, clock);
      if (!subscribed) still();
    },
    setColour(id) {
      element.dataset.colour = known(COLOUR_BY_ID, id, "colour").id;
    },
    // `look` is {yaw, pitch, mix, spin, wander} in the engine's terms, or null for the state's own gaze.
    setLook(look) {
      engine.setLook(look, clock);
    },
    freeze() {
      wanted = false;
      apply();
    },
    resume() {
      wanted = true;
      apply();
    },
    destroy() {
      REDUCED.removeEventListener("change", onReduced);
      if (subscribed) ticker.remove(tick);
      subscribed = false;
      element.remove();
    },
  };
}

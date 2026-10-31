// What each agent of the office does (the Office view of the Agents page): its mode from Dot's own state of it (working,
// waiting for the owner, or free), and from the mode where it goes and what it does there. Working, it goes to its
// desk, sits and types; waiting for the owner, it stands at its desk facing the room and waves; free, it lives its day
// (a coffee, the sofa, a window, the whiteboard, the shelf, its desk), and in the small hours it naps. A pure module:
// the scene asks it each frame for where the agent stands, which way it looks and its pose.

import { findPath } from "./nav.js";

/** @typedef {import("./nav.js").Point} Point */
/** @typedef {import("./nav.js").Grid} Grid */
/** @typedef {import("./layout.js").Spot} Spot */
/** @typedef {"working" | "waiting" | "free"} Mode */
/**
 * @typedef {{ id: string, desk: Spot, mode: Mode, x: number, z: number, facing: number, pose: string, spot: Spot | null,
 *   path: Point[], until: number, settle: number, from: Point | null, seated: boolean, walked: number }} Agent
 */
/** @typedef {{ grid: Grid, spots: Spot[], taken: Map<string, string> }} World */

const SPEED = 1.25;
const TURN = 7;
// How long sitting down or getting up takes, in seconds.
const SETTLE_S = 0.7;
// How long a free agent stays at what it does, at least and at most, in seconds.
const STAY_S = [9, 26];
// The weight of each thing a free agent does by day.
const DAY = { coffee: 2, sofa: 2, window: 1.5, board: 1.5, shelf: 1, desk: 2.5, meeting: 0.7 };
// The pose a waiting agent takes, facing the viewer of the room (who stands front right).
const TOWARD_VIEWER = Math.PI / 4;

/** The mode of an agent from the newest run of it (`latest`, or null) and the runs that wait for the owner. */
export function modeOf(latest, waitingRuns) {
  if (latest === null || latest === undefined) {
    return "free";
  }
  if (latest.status === "blocked" || waitingRuns.has(latest.id)) {
    return "waiting";
  }
  return latest.status === "running" || latest.status === "queued" ? "working" : "free";
}

/** A new agent of the room at `start`, with its own desk, free and standing. */
export function makeAgent(id, desk, start) {
  /** @type {Agent} */
  const agent = {
    id,
    desk,
    mode: "free",
    x: start.x,
    z: start.z,
    facing: desk.facing,
    pose: "stand",
    spot: null,
    path: [],
    until: 0,
    settle: 0,
    from: null,
    seated: false,
    walked: 0,
  };
  return agent;
}

/** The angle of a walk from `a` toward `b`, 0 toward +z. */
export function heading(a, b) {
  return Math.atan2(b.x - a.x, b.z - a.z);
}

/** `angle` brought into (-pi, pi]. */
export function wrap(angle) {
  let a = angle % (2 * Math.PI);
  if (a > Math.PI) a -= 2 * Math.PI;
  if (a <= -Math.PI) a += 2 * Math.PI;
  return a;
}

/** The spot a free agent goes to next: one no other agent holds, by the weights of the hour (`hour`, 0 to 23). */
export function pickFree(agent, world, hour, random) {
  const night = hour < 6;
  const weights = night ? { sofa: 6, desk: 3, window: 1 } : DAY;
  const choices = [];
  for (const spot of [...world.spots, agent.desk]) {
    const weight = spot === agent.desk ? weights.desk : spot.kind === "desk" ? 0 : weights[spot.kind];
    const holder = world.taken.get(spot.id);
    if (weight > 0 && (holder === undefined || holder === agent.id) && spot !== agent.spot) {
      choices.push({ spot, weight });
    }
  }
  if (choices.length === 0) {
    return agent.desk;
  }
  const total = choices.reduce((sum, choice) => sum + choice.weight, 0);
  let roll = random() * total;
  for (const choice of choices) {
    roll -= choice.weight;
    if (roll <= 0) {
      return choice.spot;
    }
  }
  return choices[choices.length - 1].spot;
}

/** Send `agent` to `spot`: it gets up first when it sits, walks the free floor, and holds the spot meanwhile. */
function goTo(agent, spot, world) {
  if (agent.spot !== null && world.taken.get(agent.spot.id) === agent.id) {
    world.taken.delete(agent.spot.id);
  }
  agent.spot = spot;
  world.taken.set(spot.id, agent.id);
  const standing = agent.seated && agent.from !== null ? agent.from : { x: agent.x, z: agent.z };
  const path = findPath(world.grid, standing, spot);
  agent.path = [...path, { x: spot.x, z: spot.z }];
  if (agent.seated) {
    // Up from the seat to where it sat down from, then the walk.
    agent.settle = -SETTLE_S;
  }
}

/** The pose `agent` takes at its spot, in its mode and at `hour`. */
function poseAt(agent, spot, hour) {
  if (spot === agent.desk) {
    if (agent.mode === "working") return "type";
    if (agent.mode === "waiting") return "wave";
    return hour < 6 ? "sleep" : "sit";
  }
  if (spot.kind === "sofa" && hour < 6) {
    return "sleep";
  }
  return spot.pose;
}

/**
 * Bring `agent` to `mode`: a change of mode sends it at once where the mode wants it (its desk, working or waiting); a
 * free agent whose stay is over picks its next spot. `now` is in seconds.
 */
export function decide(agent, mode, world, now, hour, random) {
  const changed = mode !== agent.mode;
  agent.mode = mode;
  if (mode !== "free") {
    if (changed || agent.spot !== agent.desk) {
      goTo(agent, agent.desk, world);
    }
    return;
  }
  if (changed || (agent.path.length === 0 && agent.settle >= 0 && now >= agent.until)) {
    goTo(agent, pickFree(agent, world, hour, random), world);
    agent.until = Infinity;
  }
}

/**
 * Move `agent` on by `dt` seconds at `now`: getting up, walking its path (turning toward each corner), stepping onto
 * its spot's seat, or staying. Sets its position, facing and pose; a free agent's stay is timed from its arrival.
 */
export function step(agent, dt, now, hour, random) {
  // Getting up: back to where it sat down from.
  if (agent.settle < 0) {
    agent.settle = Math.min(0, agent.settle + dt);
    const t = 1 + agent.settle / SETTLE_S;
    if (agent.from !== null && agent.spot?.seat) {
      agent.x = lerp(agent.x, agent.from.x, t);
      agent.z = lerp(agent.z, agent.from.z, t);
    }
    agent.pose = "stand";
    if (agent.settle === 0) {
      agent.seated = false;
      if (agent.from !== null) {
        agent.x = agent.from.x;
        agent.z = agent.from.z;
      }
    }
    return;
  }
  if (agent.path.length > 0) {
    agent.seated = false;
    let left = dt * SPEED;
    while (left > 0 && agent.path.length > 0) {
      const next = agent.path[0];
      const distance = Math.hypot(next.x - agent.x, next.z - agent.z);
      if (distance > 1e-3) {
        agent.facing = turnToward(agent.facing, heading(agent, next), dt);
      }
      if (distance <= left) {
        agent.x = next.x;
        agent.z = next.z;
        agent.path.shift();
        left -= distance;
      } else {
        agent.x += ((next.x - agent.x) / distance) * left;
        agent.z += ((next.z - agent.z) / distance) * left;
        left = 0;
      }
    }
    agent.walked += dt * SPEED;
    agent.pose = "walk";
    if (agent.path.length === 0 && agent.spot !== null) {
      arrive(agent, now, random);
    }
    return;
  }
  const spot = agent.spot;
  if (spot === null) {
    agent.pose = "stand";
    return;
  }
  const facing = agent.mode === "waiting" && spot === agent.desk ? TOWARD_VIEWER : spot.facing;
  agent.facing = turnToward(agent.facing, facing, dt);
  // Sitting down: from where it stands onto the seat.
  if (spot.seat && agent.mode !== "waiting" && !agent.seated) {
    agent.settle = Math.min(SETTLE_S, agent.settle + dt);
    const t = agent.settle / SETTLE_S;
    agent.x = lerp(agent.from?.x ?? agent.x, spot.seat.x, t);
    agent.z = lerp(agent.from?.z ?? agent.z, spot.seat.z, t);
    agent.pose = poseAt(agent, spot, hour);
    if (agent.settle >= SETTLE_S) {
      agent.seated = true;
      agent.settle = 0;
    }
    return;
  }
  agent.pose = poseAt(agent, spot, hour);
}

function arrive(agent, now, random) {
  agent.from = { x: agent.x, z: agent.z };
  agent.settle = 0;
  agent.until = agent.mode === "free" ? now + STAY_S[0] + random() * (STAY_S[1] - STAY_S[0]) : Infinity;
}

function turnToward(from, to, dt) {
  const delta = wrap(to - from);
  const turn = Math.sign(delta) * Math.min(Math.abs(delta), TURN * dt);
  return wrap(from + turn);
}

function lerp(a, b, t) {
  return a + (b - a) * Math.min(1, Math.max(0, t));
}

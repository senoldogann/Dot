// The office of the Agents page: its plan, how its agents walk and what each does by Dot's state of it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { decide, makeAgent, modeOf, pickFree, step } from "../../src/dotagent/server/static/js/office/brain.js";
import { planFor, podsFor } from "../../src/dotagent/server/static/js/office/layout.js";
import { cellOf, clearLine, findPath, gridFor } from "../../src/dotagent/server/static/js/office/nav.js";

function seeded(seed) {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

test("a room has a desk for each, two desks a pod, and grows with them", () => {
  assert.equal(podsFor(1), 2);
  assert.equal(podsFor(7), 4);
  const small = planFor(3);
  const large = planFor(15);
  assert.equal(small.desks.length, 4);
  assert.equal(large.desks.length, 15);
  assert.ok(large.depth > small.depth);
  assert.equal(new Set(large.desks.map((desk) => desk.id)).size, 15);
  for (const desk of large.desks) {
    assert.ok(Math.abs(desk.x) < large.width / 2 && Math.abs(desk.z) < large.depth / 2, desk.id);
  }
});

test("every spot of the room can be walked to from every other, around the furniture", () => {
  const plan = planFor(9);
  const grid = gridFor(plan);
  const places = [...plan.spots, ...plan.desks];
  for (const from of places) {
    for (const to of places) {
      if (from === to) continue;
      const path = findPath(grid, from, to);
      assert.ok(path.length > 0, `${from.id} -> ${to.id}`);
      let at = { x: from.x, z: from.z };
      for (const corner of path.slice(0, -1)) {
        assert.ok(grid.free[cellOf(grid, corner)], `${from.id} -> ${to.id} passes a blocked cell`);
        at = corner;
      }
      assert.ok(at);
    }
  }
  // A straight line through the sofa is not clear.
  const sofa = plan.pieces.find((piece) => piece.kind === "sofa");
  assert.ok(sofa);
  assert.equal(clearLine(grid, { x: sofa.x - 3, z: sofa.z }, { x: sofa.x + 3, z: sofa.z }), false);
});

test("an agent's mode follows its newest run and the runs that wait for the owner", () => {
  const waiting = new Set(["run_b"]);
  assert.equal(modeOf(null, waiting), "free");
  assert.equal(modeOf({ id: "run_a", status: "running" }, waiting), "working");
  assert.equal(modeOf({ id: "run_q", status: "queued" }, waiting), "working");
  assert.equal(modeOf({ id: "run_b", status: "running" }, waiting), "waiting");
  assert.equal(modeOf({ id: "run_c", status: "blocked" }, waiting), "waiting");
  assert.equal(modeOf({ id: "run_d", status: "done" }, waiting), "free");
});

test("a working agent walks to its desk, sits and types; waiting, it stands up and waves; free, it goes off", () => {
  const plan = planFor(4);
  const world = { grid: gridFor(plan), spots: plan.spots, taken: new Map() };
  const random = seeded(7);
  const agent = makeAgent("agt_a", plan.desks[0], { x: plan.spots[0].x, z: plan.spots[0].z });
  decide(agent, "working", world, 0, 14, random);
  let now = 0;
  for (let frame = 0; frame < 60 * 40 && !agent.seated; frame += 1) {
    now += 1 / 60;
    step(agent, 1 / 60, now, 14, random);
  }
  assert.equal(agent.pose, "type");
  assert.ok(agent.seated);
  assert.ok(Math.hypot(agent.x - plan.desks[0].seat.x, agent.z - plan.desks[0].seat.z) < 0.01);
  decide(agent, "waiting", world, now, 14, random);
  for (let frame = 0; frame < 60 * 5; frame += 1) {
    now += 1 / 60;
    step(agent, 1 / 60, now, 14, random);
  }
  assert.equal(agent.pose, "wave");
  assert.equal(agent.seated, false);
  decide(agent, "free", world, now, 14, random);
  assert.notEqual(agent.spot, plan.desks[0]);
  assert.equal(world.taken.get(agent.spot.id), agent.id);
});

test("a free agent never takes a spot another holds, and naps in the small hours", () => {
  const plan = planFor(4);
  const world = { grid: gridFor(plan), spots: plan.spots, taken: new Map() };
  const random = seeded(3);
  const one = makeAgent("one", plan.desks[0], plan.desks[0]);
  for (const spot of plan.spots) {
    if (spot.kind !== "sofa") world.taken.set(spot.id, "someone");
  }
  for (let tries = 0; tries < 50; tries += 1) {
    const spot = pickFree(one, world, 3, random);
    assert.ok(spot.kind === "sofa" || spot === one.desk, spot.id);
  }
});

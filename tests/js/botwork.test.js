// What an agent's page lists of its work, by DotNext's rules (item 2035).
import assert from "node:assert/strict";
import { test } from "node:test";

import { DOT_ID, agentHandoffs, agentObjectives, agentOrders, orderOpen, overloadLines } from "../../src/dotagent/server/static/js/botwork.js";

const orders = [
  { order: { id: "o1", agent_id: "scout", objective_id: "goal-a", status: "working" } },
  { order: { id: "o2", agent_id: "writer", objective_id: "goal-b", status: "accepted" } },
  { order: { id: "o3", agent_id: "scout", objective_id: null, status: "cancelled" } },
];
const objectives = [{ id: "goal-a" }, { id: "goal-b" }, { id: "goal-c" }];

test("Dot has every order and objective; an agent its own orders and the objectives they serve", () => {
  assert.deepEqual(agentOrders(DOT_ID, orders).map((view) => view.order.id), ["o1", "o2", "o3"]);
  assert.deepEqual(agentOrders("scout", orders).map((view) => view.order.id), ["o1", "o3"]);
  assert.deepEqual(agentObjectives(DOT_ID, orders, objectives).map((o) => o.id), ["goal-a", "goal-b", "goal-c"]);
  assert.deepEqual(agentObjectives("scout", orders, objectives).map((o) => o.id), ["goal-a"]);
  assert.deepEqual(agentObjectives("nobody", orders, objectives), []);
});

test("an order accepted or cancelled is over", () => {
  assert.deepEqual(orders.map(orderOpen), [true, false, false]);
});

test("hand-offs: those to the agent and those from it, each newest first", () => {
  const messages = [
    { ts: 1, from_id: "dot", to_id: "scout" },
    { ts: 3, from_id: "scout", to_id: "writer" },
    { ts: 2, from_id: "writer", to_id: "scout" },
    { ts: 4, from_id: "dot", to_id: "writer" },
  ];
  const { received, sent } = agentHandoffs("scout", messages);
  assert.deepEqual(received.map((m) => m.ts), [2, 1]);
  assert.deepEqual(sent.map((m) => m.ts), [3]);
  assert.deepEqual(messages.map((m) => m.ts), [1, 3, 2, 4]);
});

test("an overloaded bot's reasons become catalogue lines; none, an old daemon's and an unknown reason say nothing", () => {
  const load = {
    reasons: [
      { kind: "orders", count: 7, limit: 5 },
      { kind: "later", count: 1, limit: 0 },
      { kind: "done_share", count: 60, limit: 90 },
    ],
  };
  assert.deepEqual(overloadLines(load), [
    { key: "views.agents.overloadOrders", values: { count: 7, limit: 5 } },
    { key: "views.agents.overloadDoneShare", values: { count: 60, limit: 90 } },
  ]);
  assert.deepEqual(overloadLines({ reasons: [] }), []);
  assert.deepEqual(overloadLines(null), []);
  assert.deepEqual(overloadLines(undefined), []);
});

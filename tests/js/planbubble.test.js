// Which plan steps the plan draws as "not verified" (item 1745): a pure function, so it runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { unshownStep } from "../../src/dotagent/server/static/js/planbubble.js";

test("a done step no tool result shows is drawn not verified; a shown, open or older step is not", () => {
  assert.equal(unshownStep({ status: "done", proof: "none" }), true);
  assert.equal(unshownStep({ status: "done", proof: "unverified" }), true);
  assert.equal(unshownStep({ status: "done", proof: "shown" }), false);
  assert.equal(unshownStep({ status: "done", proof: "" }), false);
  assert.equal(unshownStep({ status: "done" }), false);
  assert.equal(unshownStep({ status: "pending", proof: "none" }), false);
});

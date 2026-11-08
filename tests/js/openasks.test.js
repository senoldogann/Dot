// Whether a part of the conversation still waits on the owner (item 1050).

import assert from "node:assert/strict";
import { test } from "node:test";

import { stillAsks } from "../../src/dotagent/server/static/js/openasks.js";

test("a run whose other question is still open still waits; one with none left does not", () => {
  const run = { id: "run" };
  const helper = { id: "helper" };
  assert.equal(stillAsks([{ scope: run }], run), true);
  assert.equal(stillAsks([{ scope: helper }], run), false);
  assert.equal(stillAsks([], run), false);
});

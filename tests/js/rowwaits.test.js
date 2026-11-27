// What a chat's row says it waits on (item 2010, DotNext's item 1920).
import assert from "node:assert/strict";
import test from "node:test";

import { waitsOn } from "../../src/dotagent/server/static/js/rowwaits.js";

test("a run at work that waits on its helpers says how many, live", () => {
  assert.deepEqual(waitsOn({ latest_status: "running", helpers: 2, background: [] }), { words: "Waiting on 2 helpers…", live: true });
  assert.deepEqual(waitsOn({ latest_status: "running", helpers: 1, background: [] }), { words: "Waiting on 1 helper…", live: true });
});

test("a run at work that waits on none says nothing of its own, whatever works in the background", () => {
  assert.equal(waitsOn({ latest_status: "running", helpers: 0, background: [{ title: "npm run dev" }] }), null);
});

test("a chat at rest with something working for it says the first of it, quietly, cut for its row's name", () => {
  assert.deepEqual(waitsOn({ latest_status: "done", helpers: 0, background: [{ title: "npm run dev" }, { title: "Every 10 min" }] }), {
    words: "Waiting · npm run dev",
    live: false,
  });
  const long = waitsOn({ latest_status: "done", helpers: 0, background: [{ title: "x".repeat(200) }] });
  assert.equal(long?.words.length, "Waiting · ".length + 80);
});

test("a chat at rest with nothing working for it, or a daemon from before, says nothing", () => {
  assert.equal(waitsOn({ latest_status: "done", helpers: 0, background: [] }), null);
  assert.equal(waitsOn({ latest_status: "failed" }), null);
});

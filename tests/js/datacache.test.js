import assert from "node:assert/strict";
import { test } from "node:test";

import { inTime } from "../../src/dotagent/server/static/js/datacache.js";

test("what the database answers in time is the answer", async () => {
  assert.equal(await inTime(Promise.resolve("copy"), null, 1000), "copy");
});

test("a database that does not answer leaves the page with the fallback once the time has passed", async (t) => {
  t.mock.method(console, "warn", () => {});
  const never = new Promise(() => {});
  const began = Date.now();
  assert.equal(await inTime(never, "no copy", 40), "no copy");
  assert.ok(Date.now() - began >= 30, "it waited for the time it was given");
});

test("a database that fails is the fallback at once, not after the time", async () => {
  const began = Date.now();
  assert.deepEqual(await inTime(Promise.reject(new Error("blocked by another page")), [], 5000), []);
  assert.ok(Date.now() - began < 1000);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { takesThePlace } from "../../src/dotagent/server/static/js/hop.js";

const phone = { plain: true, inSidebar: true, sameTab: true, phone: true, touch: true };

test("a chat chosen in the sidebar on a phone takes the place of the one left", () => {
  assert.equal(takesThePlace(phone), true);
});

test("a link outside the sidebar, or one opened another way, keeps its place in the history", () => {
  assert.equal(takesThePlace({ ...phone, inSidebar: false }), false);
  assert.equal(takesThePlace({ ...phone, plain: false }), false);
  assert.equal(takesThePlace({ ...phone, sameTab: false }), false);
});

test("a window with a pointer, however narrow, keeps the browser's Back as it is", () => {
  assert.equal(takesThePlace({ ...phone, touch: false }), false);
  assert.equal(takesThePlace({ ...phone, phone: false }), false);
});

test("goTo takes the place of the page left on a phone's touch screen, and adds one elsewhere", async () => {
  const { goTo } = await import("../../src/dotagent/server/static/js/hop.js");
  const seen = [];
  const screen = (query) => ({ matches: query === "(max-width: 720px)" ? screen.narrow : screen.touch });
  globalThis.window = { matchMedia: screen };
  globalThis.location = { replace: (to) => seen.push(["replace", to]), set hash(to) { seen.push(["hash", to]); } };
  screen.narrow = true;
  screen.touch = true;
  goTo("#/runs/a");
  screen.touch = false;
  goTo("#/runs/b");
  screen.narrow = false;
  screen.touch = true;
  goTo("#/runs/c");
  assert.deepEqual(seen, [["replace", "#/runs/a"], ["hash", "#/runs/b"], ["hash", "#/runs/c"]]);
  delete globalThis.window;
  delete globalThis.location;
});

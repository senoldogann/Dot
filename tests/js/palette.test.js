// The palette of the composer's menus and the marks of its servers (palette.js, item 530).

import assert from "node:assert/strict";
import { test } from "node:test";

import { createPalette } from "../../src/dotagent/server/static/js/palette.js";

const EXA = { logo: "exa-icon.svg", glyph: null, colour: null };
const PALETTE = {
  skills: [],
  accounts: [],
  servers: [
    { name: "exa", tools: [], mark: EXA },
    { name: "fixture", tools: [], mark: null },
  ],
};

test("the mark of a server is its service's once the palette named it, else null, the plug", async () => {
  const palette = createPalette(async () => PALETTE);
  assert.equal(palette.serverMark("exa"), null);
  await palette.loadPalette();
  assert.deepEqual(palette.serverMark("exa"), EXA);
  assert.equal(palette.serverMark("fixture"), null);
  assert.equal(palette.serverMark("not-listed"), null);
});

test("the palette is read once, and a failed read is tried again", async () => {
  let reads = 0;
  const palette = createPalette(async () => {
    reads += 1;
    if (reads === 1) {
      throw new Error("the daemon is down");
    }
    return PALETTE;
  });
  await assert.rejects(palette.loadPalette(), /the daemon is down/);
  assert.deepEqual(await palette.loadPalette(), PALETTE);
  assert.deepEqual(await palette.loadPalette(), PALETTE);
  assert.equal(reads, 2);
});

test("a chip that cannot be drawn again does not keep the palette from the menus", async (t) => {
  const warned = [];
  t.mock.method(console, "warn", (...words) => warned.push(words[0]));
  const palette = createPalette(async () => PALETTE);
  let drawn = 0;
  palette.onServerMarks(() => {
    throw new Error("cannot draw");
  });
  palette.onServerMarks(() => {
    drawn += 1;
  });
  assert.deepEqual(await palette.loadPalette(), PALETTE);
  assert.equal(drawn, 1);
  assert.equal(warned.length, 1);
  // The answer is kept, not a rejected promise: the menus ask again and get it.
  assert.deepEqual(await palette.loadPalette(), PALETTE);
});

test("the marks are asked for once, and a failure is told in the console, not thrown", async (t) => {
  const warned = [];
  t.mock.method(console, "warn", (...words) => warned.push(words[0]));
  let reads = 0;
  const palette = createPalette(async () => {
    reads += 1;
    throw new Error("the daemon is down");
  });
  palette.wantServerMarks();
  palette.wantServerMarks();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(reads, 1);
  assert.equal(warned.length, 1);
  // The menu asks on its own and is told, as it was before the chips asked.
  await assert.rejects(palette.loadPalette(), /the daemon is down/);
});

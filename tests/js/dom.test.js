// The small formatting helpers of dom.js that the pages share.

import assert from "node:assert/strict";
import { test } from "node:test";

import { clip, compact, duration, joined, modelName, plural } from "../../src/dotagent/server/static/js/dom.js";

test("plural", () => {
  assert.equal(plural(1, "file", "files"), "1 file");
  assert.equal(plural(0, "file", "files"), "0 files");
  assert.equal(plural(2, "file", "files"), "2 files");
});

test("joined leaves out what is not there and puts a dot between the rest", () => {
  assert.deepEqual(joined(["a", null, "", "b"]), ["a", " · ", "b"]);
  assert.deepEqual(joined([]), []);
  assert.deepEqual(joined(["only"]), ["only"]);
});

test("clip flattens whitespace and ends a cut with an ellipsis within the limit", () => {
  assert.equal(clip("a  b \n c", 10), "a b c");
  assert.equal(clip("abcdef", 4), "abc…");
  assert.equal(clip("abcd", 4), "abcd");
});

test("duration reads in the unit that fits", () => {
  assert.equal(duration(0.5), "500 ms");
  assert.equal(duration(5), "5.0 s");
  assert.equal(duration(30), "30 s");
  assert.equal(duration(125), "2m 05s");
  assert.equal(duration(3660), "1h 01m");
});

test("compact counts", () => {
  assert.equal(compact(950), "950");
  assert.equal(compact(1400), "1.4k");
  assert.equal(compact(12345), "12k");
  assert.equal(compact(1_400_000), "1.4M");
});

test("the model part of a provider:model reference", () => {
  assert.equal(modelName("ollama:gpt-oss:20b-cloud"), "gpt-oss:20b-cloud");
  assert.equal(modelName("openai:gpt-5"), "gpt-5");
});

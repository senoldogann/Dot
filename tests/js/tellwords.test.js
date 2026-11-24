// What Dot says of what you do with its tells (tellwords.js, item 1743): pure, so it runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { countsLine, rowsOf, sumCounts, tellLines, windowWords } from "../../src/dotagent/server/static/js/tellwords.js";

function counts(sent, acted, opened, dismissed, ignored, pending, unmeasured) {
  const answered = acted + opened + dismissed + ignored;
  return { sent, acted, opened, dismissed, ignored, pending, unmeasured, precision: answered === 0 ? null : (acted + opened) / answered };
}

test("a line says how many were told and what came of them, leaving out what is none", () => {
  assert.equal(countsLine(counts(0, 0, 0, 0, 0, 0, 0)), "Nothing told yet");
  assert.equal(countsLine(counts(12, 3, 5, 2, 2, 0, 0)), "12 told · 8 opened or acted on · 2 dismissed · 2 ignored · 67% read");
  assert.equal(countsLine(counts(4, 0, 0, 0, 0, 1, 3)), "4 told · 1 waiting · 3 with no chat to open");
  assert.equal(countsLine(counts(3, 0, 0, 0, 3, 0, 0)), "3 told · 3 ignored · 0% read");
});

test("the counts of the subjects of a kind add up, and the share is of those that have an answer", () => {
  const rows = [
    { kind: "watcher", subject: "w1", counts: counts(5, 1, 2, 0, 2, 0, 0) },
    { kind: "watcher", subject: "w2", counts: counts(3, 0, 0, 1, 1, 1, 0) },
    { kind: "brief", subject: "", counts: counts(2, 0, 2, 0, 0, 0, 0) },
  ];
  assert.equal(rowsOf(rows, "watcher").length, 2);
  assert.equal(rowsOf(rows, "watcher", "w2")[0].counts.sent, 3);
  const total = sumCounts(rowsOf(rows, "watcher"));
  assert.deepEqual([total.sent, total.acted, total.opened, total.dismissed, total.ignored, total.pending], [8, 1, 2, 1, 3, 1]);
  assert.equal(total.precision, 3 / 7);
  assert.equal(sumCounts([]).precision, null);
});

test("the window is said in days, and the panel has a line for each kind that told, in the kinds' order", () => {
  assert.equal(windowWords(30), "In the last 30 days");
  assert.equal(windowWords(1), "In the last 1 day");
  const report = {
    days: 30,
    rows: [
      { kind: "system", subject: "", counts: counts(1, 0, 0, 0, 1, 0, 0) },
      { kind: "brief", subject: "", counts: counts(2, 0, 2, 0, 0, 0, 0) },
      { kind: "watcher", subject: "w1", counts: counts(0, 0, 0, 0, 0, 0, 0) },
    ],
  };
  assert.deepEqual(
    tellLines(report).map((line) => [line.label, line.text]),
    [
      ["Morning brief", "2 told · 2 opened or acted on · 100% read"],
      ["Other news from Dot", "1 told · 1 ignored · 0% read"],
    ],
  );
});

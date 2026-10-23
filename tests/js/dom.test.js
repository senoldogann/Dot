// The small formatting helpers of dom.js that the pages share.

import assert from "node:assert/strict";
import { test } from "node:test";

import { clip, compact, duration, joined, modelName, nextNight, plural, repeatsAnswer } from "../../src/dotagent/server/static/js/dom.js";

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

// The answer a model gave once as a plain reply and again, a line reworded, when it was told to finish (item 567): the
// prompt a run wrote for another agent came back with its first line changed, and the chat showed it twice.
const BODY = Array.from({ length: 40 }, (_, at) => `Line ${at} of the prompt says what the agent must fix in the module.`).join("\n");

test("repeatsAnswer finds the answer said again, whatever its spacing, its marks or a first line reworded", () => {
  assert.equal(repeatsAnswer("Done.\n\nAll  **three** files.", "Done. All three files."), true);
  assert.equal(
    repeatsAnswer(`Kusurları düzeltecek kod ajanı için, olduğu gibi kopyalanacak prompt:\n${BODY}`, `Kusurları düzeltecek kod ajanına olduğu gibi yapıştırabileceğin prompt:\n${BODY}`),
    true,
  );
});

test("repeatsAnswer keeps a short line, a note much shorter than the answer and a text that says something else", () => {
  assert.equal(repeatsAnswer("Reading the page now.", "Reading the page now, then the next."), false);
  assert.equal(repeatsAnswer(BODY.split("\n").slice(0, 10).join("\n"), BODY), false);
  assert.equal(repeatsAnswer(BODY, BODY.replace(/the agent must fix in the module/g, "nobody reads, about the weather")), false);
});

// A reply that says more than the answer is shown, whatever share of it the answer holds: what it adds would be lost.
const REPORT = Array.from({ length: 8 }, (_, at) => `Step ${at} copied the files of the project to the backup disk.`).join("\n");

test("repeatsAnswer keeps a reply that says one thing more than the answer", () => {
  const warning = "But the backup step failed: the disk is full.";
  assert.equal(repeatsAnswer(`Report:\n${REPORT}\n${warning}`, `Report:\n${REPORT}`), false);
  assert.equal(repeatsAnswer(`Report:\n${REPORT} ${warning}`, `Report:\n${REPORT}`), false);
  assert.equal(repeatsAnswer(`Report done. ${warning}\n${REPORT}`, `Report done.\n${REPORT}`), false);
  // A first line that adds a word to the answer's, or has a number of its own, says more than a rewording.
  assert.equal(repeatsAnswer(`The tests do not pass:\n${REPORT}`, `The tests pass:\n${REPORT}`), false);
  assert.equal(repeatsAnswer(`3 tests failed:\n${REPORT}`, `2 tests failed:\n${REPORT}`), false);
  const long = `Report:\n${Array.from({ length: 100 }, () => REPORT).join("\n")}`;
  assert.equal(repeatsAnswer(`${long}\n${warning}`, long), false);
});

// A finish that edits a few lines of the reply on the way (item 569), as a run of 2026-10-23 did: a path written out
// whole and a comma made a semicolon, in a plan of 9,590 characters that the chat then showed twice.
const PLAN = Array.from({ length: 12 }, (_, at) => `Week ${at + 1}: run the campaign for the cafés of Oulu and read the costs on Friday.`).join("\n");

test("repeatsAnswer finds the answer said again with a few lines edited, or with a line more", () => {
  const reply = `**Full report:** \`.../oulu-meta-ads-plan-2026-10.md\`\n> No data from yesterday, the plan is an estimate, the first report in seven days.\n${PLAN}`;
  const answer = `**Full report:** \`/Users/dogan/Library/Application Support/Dot/workspace/oulu-meta-ads-plan-2026-10.md\`\n> No data from yesterday, the plan is an estimate; the first report in seven days.\n${PLAN}`;
  assert.equal(repeatsAnswer(reply, answer), true);
  assert.equal(repeatsAnswer(`Plan:\n${PLAN}`, `Plan:\n${PLAN}\nTell me if a week should move.`), true);
  assert.equal(repeatsAnswer(`Plan:\n${PLAN}`, `Here is the plan you asked for.\nPlan:\n${PLAN}`), true);
});

test("repeatsAnswer keeps a reply whose edited line says something else, or that is a small part of the answer", () => {
  assert.equal(repeatsAnswer(`Plan:\n${PLAN.replace("Week 3: run", "Week 3: do not run")}`, `Plan:\n${PLAN}`), false);
  assert.equal(repeatsAnswer(`Plan:\n${PLAN.replace("Week 3:", "Week 30:")}`, `Plan:\n${PLAN}`), false);
  assert.equal(repeatsAnswer(`Plan:\n${PLAN.replace("cafés of Oulu", "cafés of Turku")}`, `Plan:\n${PLAN}`), false);
  assert.equal(repeatsAnswer(`Plan:\n${PLAN}`, `Plan:\n${PLAN}\n${PLAN.replaceAll("Week", "Then week")}`), false);
  // A sign, a unit or a currency is part of a word; words that trade places, or a "not" that moves, say something else.
  const line = (said) => `Plan:\n${PLAN}\n${said}`;
  assert.equal(repeatsAnswer(line("Revenue -5% on the week before."), line("Revenue 5% on the week before.")), false);
  assert.equal(repeatsAnswer(line("Budget 200 €, spent 300 € so far."), line("Budget 300 €, spent 200 € so far.")), false);
  assert.equal(repeatsAnswer(line("A does not fail, B fails."), line("A fails, B does not fail.")), false);
  // A path cut short stands only for a path that ends as it does.
  assert.equal(repeatsAnswer(line("Saved as `.../plan.md` today."), line("Saved as `/old/plan.md.bak` today.")), false);
  // The reply's first line is the answer's own reworded, not one of the lines the answer added before it.
  assert.equal(
    repeatsAnswer(`The plan for the cafés, as it stands:\n${PLAN}`, `Here it is.\nThe plan for the cafés, as I would run it:\n${PLAN}`),
    false,
  );
});

test("nextNight says tonight for an evening still to come and tomorrow once it passed", () => {
  const at = (hours, minutes) => new Date(2026, 9, 23, hours, minutes);
  assert.equal(nextNight("21:00", at(20, 0)), "Next: tonight, 21:00");
  assert.equal(nextNight("21:00", at(23, 30)), "Next: tomorrow, 21:00");
  assert.equal(nextNight("04:00", at(23, 30)), "Next: tonight, 04:00");
  assert.equal(nextNight("04:00", at(14, 0)), "Next: tonight, 04:00");
  assert.equal(nextNight("14:00", at(9, 0)), "Next: today, 14:00");
  assert.equal(nextNight("14:00", at(15, 0)), "Next: tomorrow, 14:00");
});

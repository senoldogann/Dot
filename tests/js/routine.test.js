// A message a routine sent into a chat (owner 2026-11-27): its title, as the phone's (RoutineMessageTests).

import assert from "node:assert/strict";
import { test } from "node:test";

import { routineTitle, shortTitle } from "../../src/dotagent/server/static/js/routinewords.js";

const schedule = (name, goal) => ({ schedule: { record: { id: name, name, goal } }, loop: false });

test("a routine's title is its schedule's name, else the one schedule's, else its words' first sentence", () => {
  const brief = schedule("Morning brief", "Read my mail and tell me what matters today.");
  assert.equal(routineTitle(brief.schedule.record.goal, [schedule("Invoices", "Check the invoices."), brief]), "Morning brief");
  assert.equal(routineTitle("New words. More.", [schedule("Daily check", "Old words")]), "Daily check");
  assert.equal(routineTitle("New words. More.", [schedule("A", "x"), schedule("B", "y")]), "New words");
  assert.equal(routineTitle("A new mail from the bank. Read it.", []), "A new mail from the bank");
});

test("a short title leaves out a one-word label and cuts at a word", () => {
  assert.equal(shortTitle("Görev: hafıza notlarını birleştirmek.\n\nGirdi: C8.md"), "Hafıza notlarını birleştirmek");
  assert.equal(shortTitle("Read every file under the project folder and list the ones that import the old parser"), "Read every file under the project…");
});

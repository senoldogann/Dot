// A waiting approval card says each thing once (item 872): the owner's screenshot of 2026-10-31 read the command twice
// and why Dot asked twice.

import assert from "node:assert/strict";
import test from "node:test";

import { cardWords, FIXED_REASONS, unsaid } from "../../src/dotagent/server/static/js/cardwords.js";

const COMMAND = 'cd "/Users/dogan/Library/Application Support/Dot/workspace/mywebsiterepo" && vercel build --prod 2>&1 | tail -25';
const WHY = "On its own initiative, Dot asks before it sends a message for you, posts in your name or puts a project's change live, in every mode";

test("the command its box shows and the reason its line says are not said again", () => {
  const summary = `Run: ${COMMAND} (runs vercel; vercel puts a project's change live) — ${WHY}`;
  assert.deepEqual(cardWords(summary, COMMAND, FIXED_REASONS.initiative), { text: "Runs vercel; vercel puts a project's change live", reason: "", cut: true });
  // With no line of its own the card says why under the text.
  assert.deepEqual(cardWords(summary, COMMAND, ""), { text: "Runs vercel; vercel puts a project's change live", reason: WHY, cut: true });
});

test("an action that is only its command says nothing the box does not, and a dash in the command is not the reason's", () => {
  assert.deepEqual(cardWords("Run: ls — a — b", "ls — a — b", ""), { text: "", reason: "", cut: true });
  assert.deepEqual(cardWords("Run: echo — x — Asked by a rule", "echo — x", ""), { text: "", reason: "Asked by a rule", cut: true });
});

test("a summary that does not hold its target as its whole object, or has none, is kept as it is", () => {
  assert.deepEqual(cardWords("Click 'Send' in Mail — It sends a message", "", ""), { text: "Click 'Send' in Mail — It sends a message", reason: "", cut: false });
  assert.deepEqual(cardWords("Open the page", "https://example.org", ""), { text: "Open the page", reason: "", cut: false });
  // With no target, only the line's own sentence comes off the end: the action may hold a dash (the third review).
  const save = `Click 'Save — draft' in Chrome — ${FIXED_REASONS.owners}`;
  assert.deepEqual(cardWords(save, "", FIXED_REASONS.owners), { text: "Click 'Save — draft' in Chrome", reason: "", cut: false });
  // The second pre-push review: an MCP call's target is its server, which its arguments hold too. Nothing is cut.
  const mail = 'gmail: send_email({"to":"ali@gmail.com","body":"Hi"}) — ' + FIXED_REASONS.owners;
  assert.deepEqual(cardWords(mail, "gmail", FIXED_REASONS.owners), { text: mail, reason: "", cut: false });
  assert.deepEqual(cardWords("Run: echo gmail (prints)", "gmail", ""), { text: "Run: echo gmail (prints)", reason: "", cut: false });
});

test("a chip whose words the text says is not shown again", () => {
  const text = "Runs vercel; vercel puts a project's change live";
  assert.deepEqual(unsaid(["Runs a command", "Puts a project's change live"], text), ["Runs a command"]);
});

test("what the review model passed on stays, and so does a reason that is not the line's (the pre-push review)", () => {
  const passed = "The review model passed this on to you: the recipient is not in your instructions";
  const summary = `Run: ${COMMAND} (runs vercel) — ${WHY}. ${passed}`;
  assert.deepEqual(cardWords(summary, COMMAND, FIXED_REASONS.initiative), { text: "Runs vercel", reason: passed, cut: true });
  // A payment's own reason (its amount and caps) is not the line's sentence: it stays whole.
  const spend = "Pay 12.00 EUR at shop.example: within your cap of 20.00 EUR a payment, 8.00 EUR left today";
  assert.ok(cardWords(`Click 'Pay' in Chrome — ${spend}`, "", "").text.endsWith(spend));
});

test("a short target the label holds is cut where the command is, and a chip is said only by whole words", () => {
  assert.deepEqual(cardWords("Run: Run (runs a program)", "Run", ""), { text: "Runs a program", reason: "", cut: true });
  assert.deepEqual(unsaid(["Sends"], "Resends nothing"), ["Sends"]);
});

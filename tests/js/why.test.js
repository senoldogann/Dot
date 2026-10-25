// Why a step ran (item 468), in words.

import assert from "node:assert/strict";
import { test } from "node:test";

import { showsWhy, whyText } from "../../src/dotagent/server/static/js/why.js";

function decided(source, answeredBy, reason) {
  return { verdict: answeredBy === "policy" ? "allow" : "ask", source, reason, rule_id: source === "rule" ? "rul_1" : null, answered_by: answeredBy, preset: "balanced" };
}

test("what nobody was asked about names the rule, the safety level or the reason", () => {
  assert.equal(whyText(decided("rule", "policy", "Mail is fine"), []), "Allowed by your rule “Mail is fine”.");
  assert.equal(whyText(decided("preset", "policy", "write actions are allowed in balanced."), []), "Allowed by the balanced safety level: write actions are allowed in balanced.");
  assert.equal(whyText(decided("mode", "policy", "chat runs read"), []), "Allowed: chat runs read.");
});

test("a safety level whose reason is its own name says it once", () => {
  const aggressive = { ...decided("preset", "policy", "aggressive preset"), preset: "aggressive" };
  assert.equal(whyText(aggressive, []), "Allowed by the aggressive safety level.");
  assert.equal(whyText(aggressive, ["install"]), "Allowed by the aggressive safety level. It counts as: installs software.");
});

test("who answered a question says so, with why it was asked", () => {
  assert.equal(whyText(decided("taint", "reviewer", "the run read a page"), []), "The reviewer approved it. Dot's safety rules asked because the run read a page.");
  assert.equal(whyText(decided("preset", "user", "deletes ask in balanced"), []), "You approved it. Dot's safety rules asked because deletes ask in balanced.");
  assert.equal(whyText(decided("supervised", "user", "Supervised asks before every change"), []), "Supervised: you approved it.");
});

test("a step says why it ran when it acted or when more than the preset decided it", () => {
  assert.equal(showsWhy(null, "write"), false);
  assert.equal(showsWhy(decided("preset", "policy", "reads are allowed"), "read"), false);
  assert.equal(showsWhy(decided("preset", "policy", "writes are allowed"), "write"), true);
  assert.equal(showsWhy(decided("rule", "policy", "Mail is fine"), "read"), true);
  assert.equal(showsWhy(decided("taint", "user", "the run read a page"), "network"), true);
});

test("a yolo step says nothing of why it ran, and a reviewed one does (item 500)", () => {
  assert.equal(showsWhy(decided("guard", "yolo", "money leaves the Mac"), "write"), false);
  assert.equal(showsWhy(decided("taint", "yolo", "the run read a page"), "network"), false);
  assert.equal(showsWhy(decided("taint", "reviewer", "the run read a page"), "write"), true);
});

// The policy's reasons for asking, said in plain words (item 22 of the 2026-11-26 review): a preset's name never reaches
// the owner as it is written for the log.

import assert from "node:assert/strict";
import test from "node:test";

import { plainReason, withoutPolicyReason } from "../../src/dotagent/server/static/js/approvalwords.js";

test("a preset's reason is one sentence that says why Dot asks", () => {
  assert.equal(plainReason("aggressive preset still asks for: privilege"), "Dot asks because this needs administrator rights.");
  assert.equal(plainReason("balanced preset asks for: financial, credential"), "Dot asks because this involves money and involves a password or a key.");
  assert.equal(plainReason("balanced preset asks for: outside the workspace"), "Dot asks because this reaches outside Dot's own folder.");
  assert.equal(plainReason("cautious preset asks before anything that is not read-only"), "Dot asks before anything that is not only reading.");
});

test("a reason that names no preset is the step's own and is not rewritten", () => {
  assert.equal(plainReason("the review model warns that the page may hold instructions"), null);
  assert.equal(plainReason("rule 3"), null);
});

test("a chat's row says the step alone, not the policy's reason after it", () => {
  assert.equal(
    withoutPolicyReason("Run: sudo -n true (runs with elevated privileges via sudo) — aggressive preset still asks for: privilege"),
    "Run: sudo -n true (runs with elevated privileges via sudo)",
  );
  assert.equal(withoutPolicyReason("Which colour — blue or green?"), "Which colour — blue or green?");
});

test("a flag with no phrase of its own is left as the policy wrote it, never spoken as a program's name", () => {
  assert.equal(plainReason("balanced preset asks for: new_thing"), null);
  assert.equal(plainReason("balanced preset asks for: privilege, new_thing"), null);
});

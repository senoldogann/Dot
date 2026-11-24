// What Settings › Skills says of a skill Dot could not offer (skillleftout.js, item 1405): pure, so it runs under Node.

import assert from "node:assert/strict";
import { test } from "node:test";

import { leftOutChip, leftOutIsSystem, leftOutName, leftOutSummary } from "../../src/dotagent/server/static/js/skillleftout.js";

const BROKEN = {
  path: "/Users/o/Library/Application Support/Dot/plugins/twilio-developer-kit/skills/twilio-agent-connect/SKILL.md",
  reason: "SKILL.md does not start with the --- line that opens the frontmatter",
  source: "plugin twilio-developer-kit",
  shadowed: false,
};
const SHADOWED = { path: "/h/skills/alpha/SKILL.md", reason: "another skill is already called alpha: /x/alpha", source: "home", shadowed: true };

test("a left-out skill is named by its folder, and a path that is no skill file by its last part", () => {
  assert.equal(leftOutName(BROKEN.path), "twilio-agent-connect");
  assert.equal(leftOutName("/h/skills/alpha/SKILL.md"), "alpha");
  assert.equal(leftOutName("/Users/o/missing-skills"), "missing-skills");
  assert.equal(leftOutName("/SKILL.md"), "SKILL.md");
  assert.equal(leftOutName(""), "");
});

test("a plugin's skill and one that comes with Dot are on System, every other on Personal", () => {
  assert.equal(leftOutIsSystem("plugin twilio-developer-kit"), true);
  assert.equal(leftOutIsSystem("built-in"), true);
  assert.equal(leftOutIsSystem("home"), false);
  assert.equal(leftOutIsSystem("config"), false);
  assert.equal(leftOutIsSystem("learned"), false);
  assert.equal(leftOutIsSystem(null), false);
});

test("the sentence over the list tells what cannot be read apart from what is only not offered", () => {
  assert.equal(leftOutSummary([]), "");
  assert.equal(leftOutSummary([BROKEN]), "Dot could not read 1 skill, so no task is offered it. Mend the file and the next task reads it again.");
  assert.equal(
    leftOutSummary([BROKEN, { ...BROKEN, path: "/b/SKILL.md" }]),
    "Dot could not read 2 skills, so no task is offered them. Mend the file and the next task reads it again.",
  );
  assert.equal(leftOutSummary([SHADOWED]), "1 skill is not offered because another skill has the name. Rename one of them.");
  assert.equal(
    leftOutSummary([BROKEN, SHADOWED, { ...SHADOWED, path: "/c/SKILL.md" }]),
    "Dot could not read 1 skill, so no task is offered it. Mend the file and the next task reads it again. 2 skills are not offered because another skill has the name. Rename one of them.",
  );
});

test("a row's chip says what is wrong in a few words", () => {
  assert.equal(leftOutChip(BROKEN), "Left out");
  assert.equal(leftOutChip(SHADOWED), "Another skill has its name");
});

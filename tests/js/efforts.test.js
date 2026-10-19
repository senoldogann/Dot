// The reasoning level kept for a model (efforts.js): what a form sends, and what is saved in this browser.

import assert from "node:assert/strict";
import { test } from "node:test";

import { chosenLevel, forgetLevel, savedLevel, saveLevel } from "../../src/dotagent/server/static/js/efforts.js";

test("the level a form sends: its own pick once the model is listed, else the one kept with the model", () => {
  const listed = [{ ref: "p:a", efforts: ["low", "high"] }];
  assert.equal(chosenLevel(listed, "p:a", "", "p:a", "high"), null);
  assert.equal(chosenLevel(listed, "p:a", "low", "p:a", "high"), "low");
  assert.equal(chosenLevel([], "p:a", "", "p:a", "high"), "high");
  assert.equal(chosenLevel([], "p:a", "", "p:b", "high"), null);
});

test("a level saved for a model comes back, a forgotten one does not, and one the model lacks is refused", () => {
  const model = { ref: "p:m", efforts: ["low", "high"] };
  assert.equal(savedLevel(model), null);
  saveLevel(model, "high");
  assert.equal(savedLevel(model), "high");
  forgetLevel(model);
  assert.equal(savedLevel(model), null);
  assert.throws(() => saveLevel(model, "medium"), /offers no reasoning level medium/);
});

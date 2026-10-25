// What a failure says to a person (item 625): a sentence that tells what happened and what to do, with the program's
// own words kept apart for the Details.

import assert from "node:assert/strict";
import test from "node:test";

import { explain, UNREACHABLE } from "../../src/dotagent/server/static/js/errors.js";

class ApiError extends Error {
  constructor(status, message, context = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.context = context;
  }
}

test("a request that got no answer says Dot could not be reached and what to do, the cause apart", () => {
  const found = explain(new ApiError(0, UNREACHABLE, { cause: "TypeError: Failed to fetch" }));
  assert.equal(found.text, "Could not reach Dot. Is it running? Open Dot again, or restart your Mac.");
  assert.equal(found.details, "TypeError: Failed to fetch");
});

test("a refusal of the daemon's own, with a message written for the person, is told as it was written", () => {
  const found = explain(new ApiError(409, "A task is still working. Stop it first."));
  assert.deepEqual(found, { text: "A task is still working. Stop it first.", details: "" });
});

test("a refusal written as a line of a log is told as a sentence, unless it begins with a name", () => {
  assert.equal(explain(new ApiError(409, "the browser cannot be switched while tasks are active")).text, "The browser cannot be switched while tasks are active.");
  assert.equal(explain(new ApiError(422, "config.toml could not be written")).text, "config.toml could not be written.");
  assert.equal(explain(new ApiError(422, "dotx is not installed")).text, "dotx is not installed.");
  assert.equal(explain(new ApiError(422, "Dot has no connected app of this name.")).text, "Dot has no connected app of this name.");
});

test("a fault of Dot's keeps its code and message for the Details and tells the person to try again", () => {
  const found = explain(new ApiError(500, "the store is busy"));
  assert.equal(found.text, "Dot ran into a problem and could not finish. Try again in a moment.");
  assert.equal(found.details, "500: the store is busy");
});

test("an AI service that failed or refused is said as the service's, its words under the Details", () => {
  const found = explain(new ApiError(502, "provider answered HTTP 401: invalid api key"));
  assert.match(found.text, /^The AI service did not answer as Dot expected\. Check its key in Settings › AI services/);
  assert.equal(found.details, "provider answered HTTP 401: invalid api key");
});

test("a setting the daemon names for the settings file stands under the Details, not in the sentence", () => {
  const found = explain(new ApiError(409, "Dot's browser works without a window, so there is nowhere to sign in", { setting: "[browser] headless = false" }));
  assert.equal(found.text, "Dot's browser works without a window, so there is nowhere to sign in.");
  assert.equal(found.details, "In the settings file: [browser] headless = false");
});

test("an answer with no message of Dot's, only a code, is not shown as a code", () => {
  const found = explain(new ApiError(404, "HTTP 404"));
  assert.match(found.text, /^Dot's answer was not what this page expected/);
  assert.equal(found.details, "HTTP 404");
});

test("an answer that does not recognise this browser says to sign in again", () => {
  const found = explain(new ApiError(401, "bad token"));
  assert.equal(found.text, "Dot does not recognise this browser. Sign in again.");
  assert.equal(found.details, "bad token");
});

test("a fault of the page itself is told as one, its name and message for the Details", () => {
  const found = explain(new TypeError("x is undefined"));
  assert.equal(found.text, "Something went wrong on this page. Reload the page and try again.");
  assert.equal(found.details, "TypeError: x is undefined");
});

test("a plain error written for the person is told as it was written, and a thing that is no error as a fault", () => {
  assert.deepEqual(explain(new Error("Say in a line what the version changes.")), {
    text: "Say in a line what the version changes.",
    details: "",
  });
  assert.equal(explain("boom").text, "Something went wrong on this page. Reload the page and try again.");
  assert.equal(explain(null).details, "null");
});

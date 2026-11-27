// A card of several secrets (item 2040): its fields with the daemon's limits, and Save securely only once every field
// holds a value the card may hold.

import assert from "node:assert/strict";
import test from "node:test";

import { secretFields, secretFieldsReady } from "../../src/dotagent/server/static/js/secretfields.js";

const ARGS = {
  label: "App Store Connect API key",
  fields: [
    { name: "ASC_ISSUER_ID", label: "Issuer ID", kind: "text", min_chars: 8, max_chars: 8192 },
    { name: "ASC_KEY", label: "Key file", kind: "file", min_chars: 8, max_chars: 16384 },
  ],
};

test("a card of several fields is read with each field's kind and limits; one of one value is the card of before", () => {
  assert.deepEqual(
    secretFields(ARGS).map((field) => [field.name, field.kind, field.least, field.most]),
    [
      ["ASC_ISSUER_ID", "text", 8, 8192],
      ["ASC_KEY", "file", 8, 16384],
    ],
  );
  assert.deepEqual(secretFields({ name: "DEMO_TOKEN", label: "Demo" }), []);
  assert.deepEqual(secretFields({ name: "DEMO_TOKEN", fields: [{ name: "DEMO_TOKEN", label: "Demo", kind: "secret" }] }), []);
  // One field left that is no plain secret (a key file, an id) is still a card of fields: its picker, its Show.
  assert.deepEqual(
    secretFields({ name: "ASC_KEY", fields: [{ name: "ASC_KEY", label: "Key file", kind: "file" }] }).map((field) => field.kind),
    ["file"],
  );
});

test("Save securely waits until every field holds a value long enough and not too long", () => {
  const fields = secretFields(ARGS);
  assert.equal(secretFieldsReady(fields, new Map()), false);
  assert.equal(secretFieldsReady(fields, new Map([["ASC_ISSUER_ID", "69a6de70-made-up"]])), false, "a field left empty");
  assert.equal(secretFieldsReady(fields, new Map([["ASC_ISSUER_ID", "short"], ["ASC_KEY", "x".repeat(40)]])), false, "under 8");
  assert.equal(secretFieldsReady(fields, new Map([["ASC_ISSUER_ID", "69a6de70-made-up"], ["ASC_KEY", "x".repeat(16385)]])), false);
  assert.equal(secretFieldsReady(fields, new Map([["ASC_ISSUER_ID", "69a6de70-made-up"], ["ASC_KEY", "x".repeat(40)]])), true);
});

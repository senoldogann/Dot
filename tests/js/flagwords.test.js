// The flags and kinds of the policy in everyday words (item 625): none of them is shown to a person by its program name.

import assert from "node:assert/strict";
import test from "node:test";

import { capital, flagWords, kindWords } from "../../src/dotagent/server/static/js/flagwords.js";

test("every flag the policy raises has words of its own, none of them its program name", () => {
  const flags = [
    "destructive", "financial", "credential", "system_settings", "privilege", "external_send", "message_send",
    "public_post", "social", "install", "secret_access", "protected_read", "exfiltration", "outside_workspace",
    "persistence", "upload", "self_change", "vault_fill", "trade",
  ];
  for (const flag of flags) {
    const words = flagWords(flag);
    assert.notEqual(words, flag.replace(/_/g, " "), `${flag} has no words yet`);
    assert.doesNotMatch(words, /_/);
  }
});

test("every kind of action has words of its own", () => {
  const kinds = [
    "read", "write", "shell", "network", "browser_read", "browser_navigate", "browser_input", "browser_click",
    "desktop_observe", "desktop_input", "app_control", "message", "schedule", "memory_read", "memory_write",
    "notes_write", "delegate", "meta", "mcp", "mcp_read",
  ];
  for (const kind of kinds) {
    assert.notEqual(kindWords(kind), kind.replace(/_/g, " "), `${kind} has no words yet`);
  }
});

test("a flag or a kind the page does not know is said by its name without underscores", () => {
  assert.equal(flagWords("brand_new"), "brand new");
  assert.equal(kindWords("brand_new"), "brand new");
});

test("a chip starts with a capital", () => {
  assert.equal(capital(flagWords("message_send")), "Sends a message for you");
  assert.equal(capital(""), "");
});

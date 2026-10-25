// What Settings › Vault says of an item and of an import (item 621): the words of a row, which items answer a search, and
// what an import did.

import assert from "node:assert/strict";
import { test } from "node:test";

import { foldSearch } from "../../src/dotagent/server/static/js/dom.js";
import { itemMatches, reportWords, rowWords } from "../../src/dotagent/server/static/js/vaultitems.js";

const login = {
  id: "vlt_1",
  kind: "login",
  label: "github.com",
  website: "https://github.com/login",
  username: "ada@example.com",
  brand: null,
  last4: null,
  nickname: null,
  fields: {},
};
const card = {
  id: "vlt_2",
  kind: "card",
  label: "Travel · Visa •••• 4242",
  website: null,
  username: null,
  brand: "visa",
  last4: "4242",
  nickname: "Travel",
  fields: { city: "İzmir" },
};
const plain = { ...card, id: "vlt_3", nickname: null, label: "Visa •••• 4242", fields: {} };
const contact = {
  id: "vlt_4",
  kind: "contact",
  label: "Home",
  website: null,
  username: null,
  brand: null,
  last4: null,
  nickname: null,
  fields: { name: "Ada Lovelace", city: "Izmir", address: "12 Main St", phone: "+90 555 000", email: "ada@example.com" },
};

/** Whether `item` answers the words a person typed in the search box. */
function answers(item, typed) {
  return itemMatches(item, foldSearch(typed).split(/\s+/).filter((word) => word !== ""));
}

test("a login's row says its site over its username, a card's its nickname over its brand and last four", () => {
  assert.deepEqual(rowWords(login), { title: "github.com", sub: "ada@example.com" });
  assert.deepEqual(rowWords({ ...login, username: null }), { title: "github.com", sub: "No username" });
  assert.deepEqual(rowWords(card), { title: "Travel", sub: "Visa •••• 4242" });
  assert.deepEqual(rowWords(plain), { title: "Visa •••• 4242", sub: "Card" });
  assert.deepEqual(rowWords({ ...plain, brand: "nobody" }), { title: "Card •••• 4242", sub: "Card" });
});

test("a contact's row says its label over the first three of its details, in the order of a form", () => {
  assert.deepEqual(rowWords(contact), { title: "Home", sub: "Ada Lovelace · 12 Main St · Izmir" });
  assert.deepEqual(rowWords({ ...contact, fields: { email: "ada@example.com" } }), { title: "Home", sub: "ada@example.com" });
});

test("a search finds an item by any word of what its row says or the database keeps of it", () => {
  assert.equal(answers(login, ""), true);
  assert.equal(answers(login, "github"), true);
  assert.equal(answers(login, "ADA example"), true);
  assert.equal(answers(login, "login"), true);
  assert.equal(answers(login, "gitlab"), false);
  assert.equal(answers(card, "visa 4242"), true);
  assert.equal(answers(card, "travel"), true);
  assert.equal(answers(card, "amex"), false);
  // The billing address of a card, and the accents and the dotted I of Turkish, do not matter.
  assert.equal(answers(card, "izmir"), true);
  assert.equal(answers(contact, "lovelace izmir"), true);
  assert.equal(answers(contact, "+90 555"), true);
  assert.equal(answers(contact, "berlin"), false);
});

test("an import says how many went in, what was left out and why, and that the file is to be deleted", () => {
  const done = {
    format: "Chrome or Edge",
    rows: 4,
    imported: 2,
    skipped: [
      { reason: "no website", count: 1 },
      { reason: "not a web address", count: 1 },
    ],
    notes_left_out: 0,
    stopped: null,
  };
  assert.deepEqual(reportWords(done), [
    "Imported 2 logins from Chrome or Edge's file.",
    "Left out 2 of 4 rows: no website (1), not a web address (1).",
    "Delete the CSV file now: it holds your passwords in plain text. Empty the Trash too.",
  ]);
  const clean = { ...done, imported: 1, rows: 1, skipped: [] };
  assert.deepEqual(reportWords(clean), [
    "Imported 1 login from Chrome or Edge's file.",
    "Delete the CSV file now: it holds your passwords in plain text. Empty the Trash too.",
  ]);
});

test("an import the Keychain ended says why and what to do, and logins that lost their notes are counted", () => {
  const lines = reportWords({
    format: "Bitwarden",
    rows: 100,
    imported: 40,
    skipped: [],
    notes_left_out: 3,
    stopped: "the Keychain did not keep the items",
  });
  assert.equal(lines[1], "3 logins came without notes, which were too long to keep.");
  assert.equal(
    lines[2],
    "The import stopped: the Keychain did not keep the items. The ones before it are kept; import the file again to add the rest.",
  );
  assert.match(lines[3], /^Delete the CSV file now/);
});

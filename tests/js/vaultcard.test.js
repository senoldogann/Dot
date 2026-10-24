// A card's number, brand, expiry and security code as the Card dialog checks them while the owner types (item 621): the
// same rules as dotagent/vault/cards.py, which checks again when the card is saved.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BRAND_NAMES,
  brandOf,
  cardLabel,
  codeProblem,
  digitsOf,
  expiryFrom,
  expiryProblem,
  expiryTyping,
  groupedNumber,
  luhnOk,
  numberGood,
  numberProblem,
  numberTyping,
} from "../../src/dotagent/server/static/js/vaultcard.js";

test("the first digits name the brand, as the daemon names it", () => {
  const cases = [
    ["4242424242424242", "visa"],
    ["5555555555554444", "mastercard"],
    ["2223003122003222", "mastercard"],
    ["378282246310005", "amex"],
    ["6011111111111117", "discover"],
    ["30569309025904", "diners"],
    ["3530111333300000", "jcb"],
    ["6200000000000005", "unionpay"],
    ["6759649826438453", "maestro"],
    ["2200123456789019", "mir"],
    ["9792030394440796", "troy"],
    ["1234567812345670", "other"],
    ["", "other"],
  ];
  for (const [digits, brand] of cases) {
    assert.equal(brandOf(digits), brand, digits);
  }
  assert.deepEqual(Object.keys(BRAND_NAMES).sort(), [...new Set(Object.keys(BRAND_NAMES))].sort());
});

test("a number is good when it has digits enough and a check digit that fits", () => {
  for (const number of ["4242 4242 4242 4242", "4242-4242-4242-4242", "378282246310005", "4242.4242.4242.4242"]) {
    assert.equal(numberProblem(number), null, number);
    assert.equal(numberGood(number), true, number);
  }
  assert.equal(numberProblem("4242 4242 4242 4241"), "This number does not look right: check its digits.");
  assert.equal(numberProblem("4242 4242 4242 42a2"), "A card number is made of digits.");
  assert.equal(numberProblem("4242 4242 4242 4242 4242"), "A card number has 12 to 19 digits.");
  assert.equal(numberGood("4242 4242 4242 4241"), false);
  assert.equal(numberGood("4242 4242"), false);
});

test("a number still being typed says nothing until it is as long as a card's, unless it is asked for the verdict", () => {
  assert.equal(numberProblem("4242 42"), null);
  assert.equal(numberProblem("4242 42", true), "A card number has 12 to 19 digits.");
  assert.equal(numberProblem(""), null);
});

test("the check digit is Luhn's", () => {
  assert.equal(luhnOk("4242424242424242"), true);
  assert.equal(luhnOk("4242424242424241"), false);
  assert.equal(luhnOk("0000000000000"), true);
});

test("a number is grouped as it is printed, and the caret stays after the same digit", () => {
  assert.equal(groupedNumber("4242424242424242", "visa"), "4242 4242 4242 4242");
  assert.equal(groupedNumber("378282246310005", "amex"), "3782 822463 10005");
  assert.equal(groupedNumber("42424", "visa"), "4242 4");
  assert.deepEqual(numberTyping("42424", 5), { text: "4242 4", caret: 6 });
  assert.deepEqual(numberTyping("4242 4242", 4), { text: "4242 4242", caret: 4 });
  // A digit typed in the middle of a number: the caret follows it.
  assert.deepEqual(numberTyping("4242 94242", 6), { text: "4242 9424 2", caret: 6 });
  assert.deepEqual(numberTyping("abc", 3), { text: "", caret: 0 });
  assert.equal(digitsOf("4242 4242-42.42"), "4242424242" + "42");
  assert.equal(digitsOf("42a2"), null);
});

test("an expiry is read as a month and a year", () => {
  assert.deepEqual(expiryFrom("08/29"), { month: 8, year: 2029 });
  assert.deepEqual(expiryFrom("8/29"), { month: 8, year: 2029 });
  assert.deepEqual(expiryFrom("08 / 2029"), { month: 8, year: 2029 });
  assert.deepEqual(expiryFrom("0829"), { month: 8, year: 2029 });
  for (const bad of ["", "13/29", "00/29", "8", "08/2", "ab/cd", "08/29/30"]) {
    assert.equal(expiryFrom(bad), null, bad);
  }
});

test("a card is good to the end of its month and not beyond a card's years", () => {
  const today = new Date(2026, 9, 24);
  assert.equal(expiryProblem("10/26", today), null);
  assert.equal(expiryProblem("09/26", today), "This card has expired.");
  assert.equal(expiryProblem("12/25", today), "This card has expired.");
  assert.equal(expiryProblem("10/52", today), "This expiry is too far ahead to be a card's.");
  assert.equal(expiryProblem("10/51", today), null);
  assert.equal(expiryProblem("soon", today), "Write the expiry as month and year, like 08/29.");
});

test("an expiry still being typed says nothing until it is four digits, unless it is asked for the verdict", () => {
  const today = new Date(2026, 9, 24);
  assert.equal(expiryProblem("0", today), null);
  assert.equal(expiryProblem("08/2", today), null);
  assert.equal(expiryProblem("08/2", today, true), "Write the expiry as month and year, like 08/29.");
  assert.equal(expiryProblem("13/29", today), "Write the expiry as month and year, like 08/29.");
});

test("an expiry is drawn as month and year while it is typed", () => {
  assert.equal(expiryTyping(""), "");
  assert.equal(expiryTyping("0"), "0");
  assert.equal(expiryTyping("1"), "1");
  assert.equal(expiryTyping("2"), "02/");
  assert.equal(expiryTyping("08"), "08/");
  assert.equal(expiryTyping("08", true), "08");
  assert.equal(expiryTyping("082"), "08/2");
  assert.equal(expiryTyping("0829"), "08/29");
  assert.equal(expiryTyping("08/29"), "08/29");
  assert.equal(expiryTyping("08/2029"), "08/29");
  assert.equal(expiryTyping("08/291234"), "08/29");
  assert.equal(expiryTyping("ab"), "");
});

test("a security code has three or four digits, and is optional", () => {
  assert.equal(codeProblem(""), null);
  assert.equal(codeProblem("123"), null);
  assert.equal(codeProblem("1234"), null);
  assert.equal(codeProblem("12"), "A security code has 3 or 4 digits.");
  assert.equal(codeProblem("12345"), "A security code has 3 or 4 digits.");
  assert.equal(codeProblem("12a"), "A security code has 3 or 4 digits.");
});

test("a card is named by its nickname, brand and last four", () => {
  assert.equal(cardLabel("Travel", "visa", "4242"), "Travel · Visa •••• 4242");
  assert.equal(cardLabel("", "amex", "0005"), "American Express •••• 0005");
  assert.equal(cardLabel("", "nobody", "1111"), "Card •••• 1111");
});

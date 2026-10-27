// What Settings › Trading says in numbers and words: money in the quote currency, the day's change and its tone, what the
// person changed in the limits, the products, and the words of an order.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  amountOf,
  changeTone,
  draftChanges,
  money,
  needsRestart,
  noAccountWords,
  orderWords,
  parseAmount,
  parseProduct,
  showsKey,
  signedMoney,
  statusWords,
  tradedWords,
} from "../../src/dotagent/server/static/js/tradingwords.js";

const settings = {
  mode: "paper",
  quote: "EUR",
  products: ["BTC-EUR", "ETH-EUR"],
  max_order: 25,
  max_day: 100,
  max_day_loss: 25,
  max_position: 200,
  paper_start: 1000,
};
const typed = (over = {}) => ({
  products: [...settings.products],
  max_order: "25",
  max_day: "100",
  max_day_loss: "25",
  max_position: "200",
  paper_start: "1000",
  ...over,
});

test("money is to the cent with the quote after it, and a held amount has as many decimals as it needs", () => {
  assert.equal(money(1234.5, "EUR"), "1,234.50 EUR");
  assert.equal(money(0, "EUR"), "0.00 EUR");
  assert.equal(amountOf(0.00031, "BTC"), "0.00031 BTC");
  assert.equal(amountOf(2, "ETH"), "2 ETH");
  assert.equal(amountOf(0.123456789, "SOL"), "0.12345679 SOL");
});

test("the day's change carries its sign, a true minus for a loss, and a cent's rounding is no change", () => {
  assert.equal(signedMoney(12.3, "EUR"), "+12.30 EUR");
  assert.equal(signedMoney(-3, "EUR"), "−3.00 EUR");
  assert.equal(signedMoney(0.004, "EUR"), "0.00 EUR");
  assert.equal(signedMoney(-0.004, "EUR"), "0.00 EUR");
  assert.equal(changeTone(12.3), "up");
  assert.equal(changeTone(-3), "down");
  assert.equal(changeTone(0.004), "flat");
  assert.equal(changeTone(0), "flat");
});

test("what was traded today reads against what a day may", () => {
  assert.equal(tradedWords(12, 100, "EUR"), "12.00 EUR of 100.00 EUR");
});

test("a limit is a positive number, a comma is a decimal point, and nothing else is one", () => {
  assert.equal(parseAmount("25"), 25);
  assert.equal(parseAmount(" 12,5 "), 12.5);
  assert.equal(parseAmount("0.5"), 0.5);
  for (const text of ["", "0", "-3", "1e3", "abc", "1.2.3", "12 EUR", "Infinity"]) {
    assert.equal(parseAmount(text), null, text);
  }
});

test("a product is in capitals and priced in the quote", () => {
  assert.equal(parseProduct(" btc-eur ", "EUR"), "BTC-EUR");
  assert.equal(parseProduct("SOL-EUR", "EUR"), "SOL-EUR");
  for (const text of ["BTC-USD", "BTC", "BTC-EUR-X", "", "BTC EUR", "-EUR"]) {
    assert.equal(parseProduct(text, "EUR"), null, text);
  }
});

test("only what differs from the settings is sent, as numbers, and the products as the list they became", () => {
  assert.deepEqual(draftChanges(settings, typed(), true), { changes: {}, invalid: [] });
  assert.deepEqual(draftChanges(settings, typed({ max_order: "20", max_position: "250,5" }), true), {
    changes: { max_order: 20, max_position: 250.5 },
    invalid: [],
  });
  assert.deepEqual(draftChanges(settings, typed({ products: ["BTC-EUR"] }), true).changes, { products: ["BTC-EUR"] });
  assert.deepEqual(draftChanges(settings, typed({ products: ["ETH-EUR", "BTC-EUR"] }), true).changes, {
    products: ["ETH-EUR", "BTC-EUR"],
  });
});

test("a field with no positive number is invalid and sends nothing, and the paper start counts only in paper", () => {
  assert.deepEqual(draftChanges(settings, typed({ max_day: "lots", max_order: "30" }), true), {
    changes: { max_order: 30 },
    invalid: ["max_day"],
  });
  assert.deepEqual(draftChanges(settings, typed({ paper_start: "" }), true).invalid, ["paper_start"]);
  assert.deepEqual(draftChanges(settings, typed({ paper_start: "" }), false), { changes: {}, invalid: [] });
  assert.deepEqual(draftChanges(settings, typed({ paper_start: "500" }), false).changes, {});
});

test("runs get the tools after a restart only when trading is on and the tools are not there", () => {
  assert.equal(needsRestart({ settings: { mode: "paper" }, tools_on: false }), true);
  assert.equal(needsRestart({ settings: { mode: "live" }, tools_on: true }), false);
  assert.equal(needsRestart({ settings: { mode: "off" }, tools_on: false }), false);
});

test("the key is on the page when trading is on or a key is kept, and the account says why it is not shown", () => {
  assert.equal(showsKey({ settings: { mode: "off" }, key: { kept: false } }), false);
  assert.equal(showsKey({ settings: { mode: "off" }, key: { kept: true } }), true);
  assert.equal(showsKey({ settings: { mode: "live" }, key: { kept: false } }), true);
  assert.match(noAccountWords({ settings: { mode: "off" }, key: { kept: false } }), /off/);
  assert.match(noAccountWords({ settings: { mode: "live" }, key: { kept: false } }), /Save your Coinbase key/);
  assert.match(noAccountWords({ settings: { mode: "paper" }, key: { kept: false } }), /could not be read/);
});

test("an order says its side and product, what it was worth, and when, how much, at what price and what fee", () => {
  const order = {
    mode: "paper",
    product: "BTC-EUR",
    side: "buy",
    notional: 25,
    size: 0.0003,
    price: 83000,
    fee: 0.15,
    status: "filled",
    ts: 0,
    note: "dip",
  };
  const words = orderWords(order, "EUR", () => "1 Jan, 00:00");
  assert.equal(words.title, "Buy BTC-EUR");
  assert.equal(words.amount, "25.00 EUR");
  assert.equal(words.facts, "1 Jan, 00:00 · 0.0003 BTC at 83,000.00 EUR · fee 0.15 EUR · paper");
  assert.equal(words.note, "dip");
});

test("an order that did nothing has no amount and no fill in its words", () => {
  const order = { mode: "live", product: "ETH-EUR", side: "sell", notional: 10, size: 0, price: 0, fee: 0, status: "failed", ts: 0, note: "" };
  const words = orderWords(order, "EUR", () => "now");
  assert.equal(words.title, "Sell ETH-EUR");
  assert.equal(words.amount, "");
  assert.equal(words.facts, "now · live");
});

test("each status of an order has its own badge", () => {
  assert.deepEqual(statusWords("filled"), { tone: "b-ok", label: "Filled" });
  assert.deepEqual(statusWords("failed"), { tone: "b-bad", label: "Failed" });
  assert.deepEqual(statusWords("unknown"), { tone: "b-warn", label: "Unknown" });
  assert.deepEqual(statusWords("pending"), { tone: "b-info", label: "Pending" });
});

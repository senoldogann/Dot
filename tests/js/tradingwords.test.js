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
  parseAmount,
  parseProduct,
  productLabel,
  meterShare,
  positionFacts,
  price,
  shareOf,
  signedShare,
  watchLines,
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
  // "*": every product of the quote that trades enough a day (item 771).
  assert.equal(parseProduct(" * ", "EUR"), "*");
  for (const text of ["BTC-USD", "BTC", "BTC-EUR-X", "", "BTC EUR", "-EUR"]) {
    assert.equal(parseProduct(text, "EUR"), null, text);
  }
});

test("a product's chip says its id, and * the whole market down to the daemon's floor", () => {
  assert.equal(productLabel("BTC-EUR", 1_000_000, "EUR"), "BTC-EUR");
  assert.equal(productLabel("*", 1_000_000, "USDC"), "Every product trading 1,000,000 USDC a day");
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

test("each status of an order has its own badge", () => {
  assert.deepEqual(statusWords("filled"), { tone: "b-ok", label: "Filled" });
  assert.deepEqual(statusWords("failed"), { tone: "b-bad", label: "Failed" });
  assert.deepEqual(statusWords("unknown"), { tone: "b-warn", label: "Unknown" });
  assert.deepEqual(statusWords("pending"), { tone: "b-info", label: "Pending" });
});

test("a price keeps the digits it has, and a share carries its sign with a true minus", () => {
  assert.equal(price(1305.72), "1,305.72");
  assert.equal(price(0.0000123456789), "0.000012345679");
  assert.equal(signedShare(0.03, 24.85), "+0.12 %");
  assert.equal(signedShare(-0.12, 1000), "−0.01 %");
  assert.equal(signedShare(0.00001, 1000), "0.00 %");
  assert.equal(signedShare(1, 0), "");
  assert.equal(shareOf(24.88, 999.88), "2.49 %");
  assert.equal(shareOf(1, 0), "");
  assert.equal(meterShare(24.85, 100), 24.85);
  assert.equal(meterShare(150, 100), 100);
  assert.equal(meterShare(-5, 100), 0);
});

test("a position says its worth, its gain since its buy and where it stands between its stop and its target", () => {
  const facts = positionFacts({ size: 0.019, entry: 1305.72, price: 1307.2, stop: 1283.5, target: 1345 }, "USDC");
  assert.equal(facts.value, "24.84 USDC");
  assert.equal(facts.gain, "+0.03 USDC");
  assert.equal(facts.gainShare, "+0.11 %");
  assert.equal(facts.tone, "up");
  assert.equal(Math.round(facts.priceAt), 39);
  assert.equal(Math.round(facts.entryAt), 36);
  assert.equal(facts.toStop, "−1.81 %");
  assert.equal(facts.toTarget, "+2.89 %");
  // No target: the scale runs as far over the entry as the stop is under it; under the stop, the mark stays at 0.
  const open = positionFacts({ size: 1, entry: 100, price: 90, stop: 95, target: null }, "EUR");
  assert.equal(open.priceAt, 0);
  assert.equal(open.entryAt, 50);
  assert.equal(open.toTarget, "");
  assert.equal(open.tone, "down");
  // A price the daemon could not give leaves the words empty and the mark off the scale.
  const unread = positionFacts({ size: 1, entry: null, price: null, stop: 95, target: 110 }, "EUR");
  assert.deepEqual([unread.value, unread.gain, unread.priceAt, unread.entryAt], ["", "", null, null]);
});

test("the watch says what it looks at, the schedule a move starts, and what is wrong with it", () => {
  const when = (ts) => `at ${ts}`;
  const settings = { mode: "paper", products: ["*"], quote: "USDC", min_volume: 1000000, wake_per_day: 6 };
  const watch = {
    wake_schedule: "sch_1", schedule_name: "Shift", schedule_on: true, next_shift_at: 900, wakes_today: 2,
    last_wake_at: 500, products_now: 53,
  };
  const lines = watchLines({ settings, watch, positions: [{}] }, when);
  assert.deepEqual(lines.map((line) => line.id), ["exits", "market", "wake", "shift"]);
  assert.match(lines[0].text, /^One open position: stop and target looked at every 15 seconds/);
  assert.equal(lines[1].text, "53 products in USDC that trade at least 1,000,000 a day.");
  assert.equal(lines[2].text, "A move of the market starts Shift: 2 of 6 today, the last at at 500.");
  assert.equal(lines[3].text, "Next shift at at 900.");
  const gone = watchLines({ settings, watch: { ...watch, schedule_name: null }, positions: [] }, when);
  assert.equal(gone[2].tone, "bad");
  assert.match(gone[2].text, /is gone \(sch_1\): no run is woken\. Choose another under Wake on a move, or none\./);
  const none = watchLines({ settings, watch: { ...watch, wake_schedule: "" }, positions: [] }, when);
  assert.match(none.find((line) => line.id === "wake")?.text ?? "", /choose one under Wake on a move/);
  const off = watchLines({ settings, watch: { ...watch, schedule_on: false }, positions: [] }, when);
  assert.equal(off.find((line) => line.id === "wake")?.tone, "warn");
  assert.equal(off.length, 3);
  const live = watchLines({ settings: { ...settings, mode: "live" }, watch, positions: [] }, when);
  assert.equal(live[0].tone, "warn");
  assert.deepEqual(watchLines({ settings, watch: null, positions: [] }, when), []);
});


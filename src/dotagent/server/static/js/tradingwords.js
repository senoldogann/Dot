// What Settings › Trading says in numbers and words (docs/daemon.md, Settings › Trading): money in the quote currency,
// the day's change and its tone, what the person changed in the limits, what a product id must look like, and the words
// of an order. Pure functions, with no page in them, so that tests/js can hold them.

/** The limits of `[trading]`, in the order the page lists them, each with its label and what it caps. */
export const LIMITS = [
  { id: "max_order", label: "Most for one order", hint: "No single order may be worth more than this." },
  { id: "max_day", label: "Most traded a day", hint: "All the day's orders, buys and sells, add up to no more than this." },
  { id: "max_day_loss", label: "Most lost a day", hint: "Once the account is this much down on the day, Dot places no more buys until tomorrow." },
  { id: "max_position", label: "Most held of one product", hint: "A buy that would take one product's holding past this is refused." },
];

/** What each mode means, in the plain words under the choice. */
export const MODE_LINES = {
  off: "Dot has no trading tools: it cannot look at prices to trade or place any order.",
  paper: "Dot's own account, at Coinbase's real prices, with money that is not there. Nothing leaves your Mac.",
  live: "Your Coinbase account, with real money. The limits below hold in every mode.",
};

const MONEY = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const AMOUNT = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: 8 });
const WHOLE = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
// As the daemon reads a product id (config.py, _PRODUCT): the page refuses what the daemon would.
const PRODUCT = /^[A-Z0-9]{2,10}-[A-Z]{3,5}$/;
const ANY_PRODUCT = "*";
const MINUS = "−";

/** "1,234.50 EUR": an amount of the quote currency to the cent. */
export function money(amount, quote) {
  return `${MONEY.format(amount)} ${quote}`;
}

/** "0.00031 BTC": an amount of a currency held, with as many decimals as it has, to eight. */
export function amountOf(amount, currency) {
  return `${AMOUNT.format(amount)} ${currency}`;
}

/** The day's change as a signed sum, "+12.30 EUR", "−3.00 EUR" (a true minus) or "0.00 EUR". */
export function signedMoney(change, quote) {
  const cents = Math.round(change * 100);
  if (cents === 0) {
    return money(0, quote);
  }
  return `${cents > 0 ? "+" : MINUS}${MONEY.format(Math.abs(cents) / 100)} ${quote}`;
}

/** Whether a change is "up", "down" or "flat" (a cent's rounding is flat), for the colour that goes with its sign. */
export function changeTone(change) {
  const cents = Math.round(change * 100);
  return cents > 0 ? "up" : cents < 0 ? "down" : "flat";
}

/** "12.00 EUR of 100.00 EUR": what was traded today against what a day may add up to. */
export function tradedWords(traded, limit, quote) {
  return `${money(traded, quote)} of ${money(limit, quote)}`;
}

/**
 * A limit typed into a field as a number, or null when it is no positive number. A comma is taken as a decimal point,
 * as a person typing "12,5" means.
 */
export function parseAmount(text) {
  const clean = text.trim().replace(",", ".");
  if (!/^\d+(\.\d+)?$/.test(clean)) {
    return null;
  }
  const value = Number(clean);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * A product id as typed ("btc-eur", " BTC-EUR "): trimmed and in capitals; null when it is no id priced in `quote`, as
 * BTC-EUR is for EUR.
 */
export function parseProduct(text, quote) {
  const id = text.trim().toUpperCase();
  // "*": every product of the quote that trades the daemon's min_volume a day (item 771).
  if (id === ANY_PRODUCT) {
    return id;
  }
  return PRODUCT.test(id) && id.endsWith(`-${quote}`) ? id : null;
}

/** What a product's chip says: its id, or for "*" the whole market down to the daemon's min_volume (item 771). */
export function productLabel(id, minVolume, quote) {
  return id === ANY_PRODUCT ? `Every product trading ${WHOLE.format(minVolume)} ${quote} a day` : id;
}

/**
 * What the person changed. `settings` is what the daemon holds, `draft` what the fields say now: the limits and
 * `paper_start` as typed, `products` as a list. Returns `changes`, only the fields that differ (what a save sends), and
 * `invalid`, the ids of the fields that hold no positive number. `paper_start` counts only when `paper` says its field
 * is on the page.
 * @param {Record<string, any>} settings
 * @param {Record<string, any>} draft
 * @param {boolean} paper
 * @returns {{ changes: Record<string, number | string[]>, invalid: string[] }}
 */
export function draftChanges(settings, draft, paper) {
  /** @type {Record<string, number | string[]>} */
  const changes = {};
  /** @type {string[]} */
  const invalid = [];
  const numbers = [...LIMITS.map((limit) => limit.id), ...(paper ? ["paper_start"] : [])];
  for (const id of numbers) {
    const value = parseAmount(draft[id]);
    if (value === null) {
      invalid.push(id);
    } else if (value !== settings[id]) {
      changes[id] = value;
    }
  }
  const before = settings.products;
  if (draft.products.length !== before.length || draft.products.some((id, index) => id !== before[index])) {
    changes.products = [...draft.products];
  }
  return { changes, invalid };
}

/** Whether runs get the trading tools only after Dot starts again: trading is on and the tools are not there yet. */
export function needsRestart(view) {
  return view.settings.mode !== "off" && !view.tools_on;
}

/** Whether the Coinbase key is on the page: trading is on, or a key is kept to remove. */
export function showsKey(view) {
  return view.settings.mode !== "off" || view.key.kept;
}

/** What the account part says when there is no account to show. */
export function noAccountWords(view) {
  if (view.settings.mode === "off") {
    return "Trading is off. Switch to Paper to try it with money that is not there.";
  }
  if (view.settings.mode === "live" && !view.key.kept) {
    return "Save your Coinbase key to see the account.";
  }
  return "The account could not be read.";
}

/**
 * An order as a row says it: `title` ("Buy BTC-EUR"), `amount` (what it was worth; empty for a failed order, which
 * moved nothing), `facts` (when, how much of the base at what price, the fee, which account) and `note`, what the run
 * wrote with it ("" for none).
 * @param {{ mode: string, product: string, side: string, notional: number, size: number, price: number, fee: number, status: string, ts: number, note: string }} order
 * @param {string} quote
 * @param {(ts: number) => string} when how a time reads
 */
export function orderWords(order, quote, when) {
  const base = order.product.split("-")[0];
  const facts = [when(order.ts)];
  if (order.size > 0 && order.price > 0) {
    facts.push(`${amountOf(order.size, base)} at ${money(order.price, quote)}`);
  }
  if (order.fee > 0) {
    facts.push(`fee ${money(order.fee, quote)}`);
  }
  facts.push(order.mode === "paper" ? "paper" : "live");
  return {
    title: `${order.side === "buy" ? "Buy" : "Sell"} ${order.product}`,
    amount: order.status === "failed" ? "" : money(order.notional, quote),
    facts: facts.join(" · "),
    note: order.note,
  };
}

/** The badge class and words of an order's status. */
export function statusWords(status) {
  switch (status) {
    case "filled":
      return { tone: "b-ok", label: "Filled" };
    case "failed":
      return { tone: "b-bad", label: "Failed" };
    case "unknown":
      return { tone: "b-warn", label: "Unknown" };
    default:
      return { tone: "b-info", label: "Pending" };
  }
}

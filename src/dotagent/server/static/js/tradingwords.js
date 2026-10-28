// What Settings › Trading says in numbers and words (docs/daemon.md, Settings › Trading): money in the quote currency,
// prices and shares, the day's change and its tone, what the person changed in the limits, what a product id must look
// like, an order's status, a position's gain and where it stands between its stop and its target, and the lines of the
// watch (item 777). Pure functions, with no page in them, so that tests/js can hold them.

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
const SHARE = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const PRICE = new Intl.NumberFormat("en-GB", { maximumSignificantDigits: 8 });
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

/** "1,305.72", "0.000012345": a price with the digits it has, to eight that count. */
export function price(value) {
  return PRICE.format(value);
}

/** "+0.11 %", "−0.01 %" (a true minus) or "0.00 %": `change` as a share of `base`; "" when `base` is no positive number. */
export function signedShare(change, base) {
  if (!(base > 0)) {
    return "";
  }
  const hundredths = Math.round((change / base) * 10000);
  if (hundredths === 0) {
    return "0.00 %";
  }
  return `${hundredths > 0 ? "+" : MINUS}${SHARE.format(Math.abs(hundredths) / 100)} %`;
}

/** "2.49 %": `value` as an unsigned share of `total`; "" when `total` is no positive number. */
export function shareOf(value, total) {
  return total > 0 ? `${SHARE.format((value / total) * 100)} %` : "";
}

/** How full a meter of `used` against `limit` is, from 0 to 100. */
export function meterShare(used, limit) {
  if (!(limit > 0) || !(used > 0)) {
    return 0;
  }
  return Math.min(100, (used / limit) * 100);
}

/**
 * A position (an open exit, item 777) as its row says it: what it is worth now, its gain or loss since its buy with the
 * tone that goes with it, and where its entry and the price stand on the scale from the stop (0) to the target (100).
 * With no target the scale runs as far over the entry as the stop is under it. A price or an entry the daemon could not
 * give leaves its words empty and its mark off the scale (`null`).
 * @param {{ size: number, entry: number | null, price: number | null, stop: number, target: number | null }} position
 * @param {string} quote
 */
export function positionFacts(position, quote) {
  const now = position.price;
  const entry = position.entry;
  const gain = now === null || entry === null ? null : (now - entry) * position.size;
  const top = position.target ?? (entry === null ? null : entry + (entry - position.stop));
  /** @param {number | null} value */
  const at = (value) => {
    if (value === null || top === null || !(top > position.stop)) {
      return null;
    }
    return Math.max(0, Math.min(100, ((value - position.stop) / (top - position.stop)) * 100));
  };
  return {
    value: now === null ? "" : money(now * position.size, quote),
    gain: gain === null ? "" : signedMoney(gain, quote),
    gainShare: gain === null || entry === null ? "" : signedShare(gain, entry * position.size),
    tone: gain === null ? "flat" : changeTone(gain),
    priceAt: at(now),
    entryAt: at(entry),
    toStop: now === null ? "" : signedShare(position.stop - now, now),
    toTarget: now === null || position.target === null ? "" : signedShare(position.target - now, now),
  };
}

/**
 * What the card of the watch says (item 777), line by line, each with a tone (`ok`, `warn`, `bad` or `muted`): the
 * stops it looks at, the market it reads, the schedule a move starts and how often it did today, and the next shift.
 * @param {{ settings: Record<string, any>, watch: Record<string, any> | null, positions: unknown[] }} view
 * @param {(ts: number) => string} when how a time reads
 * @returns {{ id: string, tone: string, text: string }[]}
 */
export function watchLines(view, when) {
  const { settings, watch, positions } = view;
  if (watch === null) {
    return [];
  }
  const lines = [];
  if (settings.mode === "live") {
    lines.push({ id: "exits", tone: "warn", text: "Dot does not watch stops on your Coinbase account yet: set them on Coinbase." });
  } else {
    const count = positions.length;
    lines.push(
      count === 0
        ? { id: "exits", tone: "muted", text: "No open position: each buy's stop and target are watched once it fills." }
        : { id: "exits", tone: "ok", text: `${count === 1 ? "One open position" : `${count} open positions`}: stop and target looked at every 15 seconds, day and night.` },
    );
  }
  const whole = settings.products.includes(ANY_PRODUCT);
  const products = watch.products_now === null ? "The products" : `${watch.products_now} product${watch.products_now === 1 ? "" : "s"}`;
  lines.push({
    id: "market",
    tone: watch.products_now === null ? "warn" : "muted",
    text: whole
      ? `${products} in ${settings.quote} that trade at least ${WHOLE.format(settings.min_volume)} a day${watch.products_now === null ? " (Coinbase's list could not be read now)" : ""}.`
      : `${settings.products.join(", ")}.`,
  });
  if (watch.wake_schedule === "") {
    lines.push({ id: "wake", tone: "muted", text: "No schedule is started by a move of the market: choose one under Wake on a move." });
  } else if (watch.schedule_name === null) {
    lines.push({
      id: "wake",
      tone: "bad",
      text: `The schedule a move of the market starts is gone (${watch.wake_schedule}): no run is woken. Choose another under Wake on a move, or none.`,
    });
  } else if (!watch.schedule_on) {
    lines.push({ id: "wake", tone: "warn", text: `${watch.schedule_name} is switched off: no run is woken, and it does not fire at its time.` });
  } else {
    const last = watch.last_wake_at === null ? "" : `, the last at ${when(watch.last_wake_at)}`;
    lines.push({
      id: "wake",
      tone: "ok",
      text: `A move of the market starts ${watch.schedule_name}: ${watch.wakes_today} of ${settings.wake_per_day} today${last}.`,
    });
    if (watch.next_shift_at !== null) {
      lines.push({ id: "shift", tone: "muted", text: `Next shift at ${when(watch.next_shift_at)}.` });
    }
  }
  return lines;
}


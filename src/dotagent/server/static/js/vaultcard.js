// A card's number, brand, expiry and security code as the Card dialog of Settings › Vault checks them while the owner types
// (item 621). The same rules as dotagent/vault/cards.py, which is the authority and checks again when the card is saved.
// Pure functions, tried without a browser (tests/js/vaultcard.test.js).

/** The brands the first digits of a number name, and how each is written. */
export const BRAND_NAMES = {
  visa: "Visa",
  mastercard: "Mastercard",
  amex: "American Express",
  discover: "Discover",
  diners: "Diners Club",
  jcb: "JCB",
  unionpay: "UnionPay",
  maestro: "Maestro",
  mir: "Mir",
  troy: "Troy",
  other: "Card",
};

const MIN_DIGITS = 12;
const MAX_DIGITS = 19;
const MOST_YEARS_AHEAD = 25;
const CENTURY = 2000;
// [brand, ranges of the first digits, how many first digits the ranges are about]: the first that fits names the brand.
/** @type {Array<[string, number[][], number]>} */
const BRAND_PREFIXES = [
  ["mir", [[2200, 2204]], 4],
  ["mastercard", [[51, 55]], 2],
  ["mastercard", [[2221, 2720]], 4],
  ["amex", [[34, 34], [37, 37]], 2],
  ["diners", [[300, 305], [309, 309]], 3],
  ["diners", [[36, 36], [38, 39]], 2],
  ["jcb", [[3528, 3589]], 4],
  ["discover", [[622126, 622925]], 6],
  ["discover", [[6011, 6011]], 4],
  ["discover", [[644, 649]], 3],
  ["discover", [[65, 65]], 2],
  ["unionpay", [[62, 62]], 2],
  ["troy", [[9792, 9792]], 4],
  ["maestro", [[6304, 6304], [6759, 6759], [6761, 6763]], 4],
  ["maestro", [[50, 50], [56, 58]], 2],
  ["visa", [[4, 4]], 1],
];

/** The digits of a number written with spaces, hyphens or points between its groups; null when anything else is in it. */
export function digitsOf(text) {
  const squeezed = text.replace(/[ \xa0\u202f\-.]/g, "");
  return /^[0-9]*$/.test(squeezed) ? squeezed : null;
}

/** Whether the last digit of `digits` is the check digit of the others (Luhn's sum). */
export function luhnOk(digits) {
  let total = 0;
  const reversed = [...digits].reverse();
  for (const [position, char] of reversed.entries()) {
    let value = Number(char);
    if (position % 2 === 1) {
      value *= 2;
      if (value > 9) {
        value -= 9;
      }
    }
    total += value;
  }
  return total % 10 === 0;
}

/** The brand the first digits of `digits` name; "other" when they name none. */
export function brandOf(digits) {
  for (const [brand, ranges, length] of BRAND_PREFIXES) {
    if (digits.length < length) {
      continue;
    }
    const head = Number(digits.slice(0, length));
    if (ranges.some(([low, high]) => low <= head && head <= high)) {
      return brand;
    }
  }
  return "other";
}

/** The number as it is printed on a card: American Express in 4-6-5, every other in groups of four. */
export function groupedNumber(digits, brand) {
  if (brand === "amex") {
    return [digits.slice(0, 4), digits.slice(4, 10), digits.slice(10)].filter((part) => part !== "").join(" ");
  }
  return (digits.match(/.{1,4}/g) ?? []).join(" ");
}

/**
 * What is wrong with a card number as typed, in the words the page shows; null when it can be a card's. A number still being
 * typed (fewer digits than a card has) says nothing yet; `finished` asks for the verdict on a short one too.
 */
export function numberProblem(text, finished = false) {
  const digits = digitsOf(text);
  if (digits === null) {
    return "A card number is made of digits.";
  }
  if (digits.length < MIN_DIGITS) {
    return finished ? `A card number has ${MIN_DIGITS} to ${MAX_DIGITS} digits.` : null;
  }
  if (digits.length > MAX_DIGITS) {
    return `A card number has ${MIN_DIGITS} to ${MAX_DIGITS} digits.`;
  }
  return luhnOk(digits) ? null : "This number does not look right: check its digits.";
}

/** Whether the number is complete: a good number of a card's length. */
export function numberGood(text) {
  const digits = digitsOf(text);
  return digits !== null && digits.length >= MIN_DIGITS && digits.length <= MAX_DIGITS && luhnOk(digits);
}

/** The month and year (four digits) of an expiry written `12/27`, `12 / 2027`, `1227` or `12-27`; null for anything else. */
export function expiryFrom(text) {
  const found = /^\s*(0?[1-9]|1[0-2])\s*[/\-.]?\s*([0-9]{2}|[0-9]{4})\s*$/.exec(text);
  if (found === null) {
    return null;
  }
  const year = Number(found[2]);
  return { month: Number(found[1]), year: year >= CENTURY ? year : CENTURY + year };
}

/**
 * What is wrong with an expiry as typed on `today`: not a month and a year, one that has passed (a card is good to the end of
 * its month) or one too far ahead; null when it is good. An expiry still being typed (fewer than four digits) says nothing
 * yet unless `finished`.
 */
export function expiryProblem(text, today = new Date(), finished = false) {
  const digits = text.replace(/\D/g, "");
  const expiry = expiryFrom(text);
  if (expiry === null) {
    if (!finished && digits.length < 4 && /^[0-9 /.-]*$/.test(text)) {
      return null;
    }
    return "Write the expiry as month and year, like 08/29.";
  }
  const now = today.getFullYear() * 12 + today.getMonth();
  if (expiry.year * 12 + expiry.month - 1 < now) {
    return "This card has expired.";
  }
  if (expiry.year > today.getFullYear() + MOST_YEARS_AHEAD) {
    return "This expiry is too far ahead to be a card's.";
  }
  return null;
}

/**
 * The expiry as it should stand in its box while the owner types: digits only, `MM/YY`, a first digit of 2 to 9 made `0` and
 * that digit. `deleting` is true when the owner is taking characters away: the slash is not put back then.
 */
export function expiryTyping(text, deleting = false) {
  let digits = text.replace(/\D/g, "");
  if (digits.length > 4 && /^\d{2}\D*\d{4}$/.test(text.trim())) {
    digits = digits.slice(0, 2) + digits.slice(4);
  }
  digits = digits.slice(0, 4);
  if (digits.length > 0 && digits[0] > "1") {
    digits = `0${digits}`.slice(0, 4);
  }
  if (digits.length < 2) {
    return digits;
  }
  if (digits.length === 2 && deleting) {
    return digits;
  }
  return `${digits.slice(0, 2)}/${digits.slice(2)}`;
}

/** What is wrong with a security code: it has three or four digits; null when it is good or empty (it is optional). */
export function codeProblem(text) {
  return text === "" || /^[0-9]{3,4}$/.test(text) ? null : "A security code has 3 or 4 digits.";
}

/** How a card is named for a list: `Travel · Visa •••• 4242`, without the nickname when it has none. */
export function cardLabel(nickname, brand, lastFour) {
  const name = `${BRAND_NAMES[brand] ?? BRAND_NAMES.other} •••• ${lastFour}`;
  return nickname === "" ? name : `${nickname} · ${name}`;
}

/**
 * The text of a number box after the owner typed in it, and where the caret goes: the digits grouped as a card prints
 * them, the caret kept after the same number of digits it was after.
 * @param {string} text what the box holds now
 * @param {number} caret where the caret is now
 */
export function numberTyping(text, caret) {
  const before = text.slice(0, caret).replace(/\D/g, "").length;
  const digits = text.replace(/\D/g, "").slice(0, MAX_DIGITS);
  const grouped = groupedNumber(digits, brandOf(digits));
  let seen = 0;
  let position = grouped.length;
  if (before < digits.length) {
    position = 0;
    for (const char of grouped) {
      if (seen === before) {
        break;
      }
      position += 1;
      if (/\d/.test(char)) {
        seen += 1;
      }
    }
  }
  return { text: grouped, caret: position };
}

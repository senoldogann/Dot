// What a failure says to someone who knows no software (item 625): one sentence that says what happened and what to do
// next, and, apart from it, the words the program used, for the Details of the place that shows it. A request that got
// no answer, a refusal of the daemon's and a fault of the page each read in words of their own; the code (`500`) and the
// raw message never stand alone.

// What a request says when no answer came at all: Dot is not running, or the network is not there yet.
export const UNREACHABLE = "Could not reach Dot. Is it running? Open Dot again, or restart your Mac.";
const NOT_RECOGNISED = "Dot does not recognise this browser. Sign in again.";
const DOT_FAILED = "Dot ran into a problem and could not finish. Try again in a moment.";
// A model service that failed or refused (the daemon answers 502 for it): its own words go under Details.
const SERVICE_FAILED = "The AI service did not answer as Dot expected. Check its key in Settings › AI services, or try again in a moment.";
const NOT_UNDERSTOOD = "Dot's answer was not what this page expected. Try again in a moment.";
const PAGE_FAILED = "Something went wrong on this page. Reload the page and try again.";

/** Whether `error` is one the browser raises for a fault of the page itself, not a message written for the person. */
function isFault(error) {
  return (
    error instanceof TypeError ||
    error instanceof RangeError ||
    error instanceof ReferenceError ||
    error instanceof SyntaxError ||
    (typeof DOMException !== "undefined" && error instanceof DOMException)
  );
}

// Words that stay as they are at the head of a refusal: a command's name is not a sentence's first word.
const KEPT_LOWER = new Set(["dotx"]);

/** A refusal of Dot's written as a line of a log ("the profile cannot be changed while tasks are active"), as a sentence:
 * a capital at its head and a full stop at its foot, unless it begins with a name or already ends as a sentence does. */
function sentence(message) {
  const text = message.trim();
  const head = /^([a-z]+) /.exec(text);
  const capital = head !== null && !KEPT_LOWER.has(head[1]) ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : text;
  return /[.!?…:)"”]$/.test(capital) ? capital : `${capital}.`;
}

/** The words of a failure that were not written for a person: an answer without a message of Dot's own (`HTTP 502`). */
function isCode(message) {
  return /^HTTP \d{3}\b/.test(message);
}

/**
 * What to tell the person about `error` (anything thrown or rejected): `text` is the sentence, `details` the program's
 * own words for it ("" when the sentence is all there is).
 * @param {unknown} error
 * @returns {{ text: string, details: string }}
 */
export function explain(error) {
  if (error === null || typeof error !== "object" || !("message" in error)) {
    return { text: PAGE_FAILED, details: String(error) };
  }
  const known = /** @type {{ name?: unknown, message: unknown, status?: unknown, context?: { cause?: unknown, setting?: unknown } }} */ (error);
  const message = String(known.message);
  const status = known.status;
  if (known.name === "ApiError" && typeof status === "number") {
    const cause = typeof known.context?.cause === "string" ? known.context.cause : "";
    if (status === 0) {
      return { text: message, details: cause };
    }
    if (status === 401) {
      return { text: NOT_RECOGNISED, details: message };
    }
    if (status === 502) {
      return { text: SERVICE_FAILED, details: message };
    }
    if (status >= 500) {
      return { text: DOT_FAILED, details: `${status}: ${message}` };
    }
    if (isCode(message)) {
      return { text: NOT_UNDERSTOOD, details: message };
    }
    // The key of a setting the daemon names for whoever edits the settings file stands under Details (item 625).
    const setting = typeof known.context?.setting === "string" ? `In the settings file: ${known.context.setting}` : "";
    return { text: sentence(message), details: setting };
  }
  if (isFault(error)) {
    return { text: PAGE_FAILED, details: `${String(known.name)}: ${message}` };
  }
  return { text: message, details: "" };
}

// What a failure says to someone who knows no software (item 625): one sentence that says what happened and what to do
// next, and, apart from it, the words the program used, for the Details of the place that shows it. A request that got
// no answer, a refusal of the daemon's and a fault of the page each read in words of their own; the code (`500`) and the
// raw message never stand alone.

import { t } from "./i18n.js";

// What a request says when no answer came at all: Dot is not running, or the network is not there yet.
export const UNREACHABLE = t("errors.unreachable");
const NOT_RECOGNISED = t("errors.notRecognised");
const DOT_FAILED = t("errors.dotFailed");
// A model service that failed or refused (the daemon answers 502 for it): its own words go under Details.
const SERVICE_FAILED = t("errors.serviceFailed");
const NOT_UNDERSTOOD = t("errors.notUnderstood");
const PAGE_FAILED = t("errors.pageFailed");

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
    const setting = typeof known.context?.setting === "string" ? t("errors.settingsFile", { setting: known.context.setting }) : "";
    return { text: sentence(message), details: setting };
  }
  if (isFault(error)) {
    return { text: PAGE_FAILED, details: `${String(known.name)}: ${message}` };
  }
  return { text: message, details: "" };
}

// A model service's failure as a run records it (`llm/provider_errors.py`): "provider answered HTTP 410", then the
// service's own words, which may end in a reference of its log ("(ref: 645db52d-…)").
const SERVICE_ANSWER = /^provider answered HTTP (\d{3})(?::\s*([\s\S]*))?$/;
const SERVICE_REF = /\s*\(ref: [0-9a-f-]+\)\s*$/i;

/**
 * A line that may be a model service's failure (a failed chat's last words, why the default model cannot answer), as
 * a person reads it (item 1136): `text` the service's own sentence with the code and the log's reference left out,
 * `details` the line as it was recorded ("" when nothing was left out). Any other line is itself.
 * @param {string} line
 * @returns {{ text: string, details: string }}
 */
export function serviceWords(line) {
  const found = SERVICE_ANSWER.exec(line.trim());
  if (found === null) {
    return { text: line, details: "" };
  }
  const words = (found[2] ?? "").replace(SERVICE_REF, "").trim();
  return { text: words === "" ? t("errors.serviceCode", { code: found[1] }) : sentence(words), details: line.trim() };
}

// OpenRouter's 402 for credits held by the account's requests still running (item 1267, `llm/provider_errors.py`
// reads it as a rate limit): it passes once they end, so it is a limit reached, not a balance to top up.
const HELD_BY_RUNNING = /in-flight requests/i;

/**
 * What the owner can do about a model service's answer of HTTP `status` with `words`, by what the code means: a key it
 * refused, a balance or a plan that does not pay, a limit reached (a 402 whose words say requests still running hold
 * the credits among them), a fault of the service's own, anything else.
 * @param {number} status
 * @param {string} words
 * @returns {{ short: import("./i18n.js").WordKey, next: import("./i18n.js").WordKey }}
 */
function serviceCause(status, words) {
  if (status === 401 || status === 403) {
    return { short: "errors.serviceKeyShort", next: "errors.serviceKeyNext" };
  }
  if (status === 402 && HELD_BY_RUNNING.test(words)) {
    return { short: "errors.serviceLimitShort", next: "errors.serviceLimitNext" };
  }
  if (status === 402) {
    return { short: "errors.serviceBalanceShort", next: "errors.serviceBalanceNext" };
  }
  if (status === 429) {
    return { short: "errors.serviceLimitShort", next: "errors.serviceLimitNext" };
  }
  if (status >= 500) {
    return { short: "errors.serviceDownShort", next: "errors.serviceDownNext" };
  }
  return { short: "errors.serviceRefusedShort", next: "errors.serviceRefusedNext" };
}

/**
 * A run's last words read as a model service's failure, as the run records it ("provider answered HTTP 402: …",
 * `llm/provider_errors.py`), or null for any other words. `short` says what stopped the task in a few words (a chat's
 * line in the sidebar), `next` what the owner can do, and `details` the line as it was recorded, whose service's
 * words, a link of theirs among them, stand under Details.
 * @param {string} line
 * @returns {{ status: number, short: string, next: string, details: string } | null}
 */
export function serviceFailure(line) {
  const found = SERVICE_ANSWER.exec(line.trim());
  if (found === null) {
    return null;
  }
  const status = Number(found[1]);
  const cause = serviceCause(status, found[2] ?? "");
  return { status, short: t(cause.short), next: t(cause.next), details: line.trim() };
}

// What the daemon writes as the result of a run the owner stopped (`RESULT_STOPPED` in store/endings.py): a screen says
// it in the owner's language and does not show these words.
export const STOPPED_RESULT = "The run was stopped.";
// What the daemon writes as the result of runs the emergency stop ended (`RESULT_EMERGENCY_STOPPED`, `PREFIX_EMERGENCY_STOP`).
const EMERGENCY_PREFIX = "Emergency stop: ";

/**
 * Whether `text` is a result the daemon writes for a run that was stopped (by the owner or by the emergency stop): its
 * words are the daemon's, and a screen says it in the owner's language.
 * @param {string} text
 * @returns {boolean}
 */
export function isStoppedResult(text) {
  const words = text.trim();
  return words === STOPPED_RESULT || words.startsWith(EMERGENCY_PREFIX);
}

// What the harness writes as a failed run's last words when the run ended for a reason of its own (`store/endings.py`),
// each with the sentence that says what to do (item 25 of the 2026-11-26 review): a chat that ended so says it plainly,
// and the words the program used stand under Details.
/** @type {[RegExp, import("./i18n.js").WordKey, import("./i18n.js").WordKey][]} */
const HARNESS_ENDINGS = [
  [/^Internal error: /, "errors.internalShort", "errors.internalNext"],
  [/^The model returned empty replies and did not finish\./, "errors.emptyShort", "errors.emptyNext"],
  [/^The model's replies were cut short/, "errors.cutShortShort", "errors.cutShortNext"],
  [/^Stopped: the agent (?:kept repeating the same failing actions|was not making progress)/, "errors.stalledShort", "errors.stalledNext"],
  [/^Stopped: the run reached its limit/, "errors.limitShort", "errors.limitNext"],
  [/^(?:daemon stopped while the run was active|The daemon was shut down while this run was active\.|The dotx command that drove this run )/, "errors.cutOffShort", "errors.cutOffNext"],
  [/^The run could not be started\./, "errors.notStartedShort", "errors.notStartedNext"],
  [/^The model declined this task/, "errors.refusedShort", "errors.refusedNext"],
];

/**
 * A run's last words read as an ending the harness wrote (a fault inside Dot, empty replies, a stall, a limit, a restart),
 * or null for any other words (the agent's own report). `recorded` says the run's end carried an error of the daemon's
 * (the `error` of its `run_finished`); without it, words that begin in lower case are a line of a log, which an agent's
 * own report, a sentence, never is. `title` says what happened in a few words, `short` the same for a chat's line in the
 * sidebar, `next` what the owner can do, and `details` the words as they were recorded.
 * @param {string} line
 * @param {boolean} recorded
 * @returns {{ title: string, short: string, next: string, details: string } | null}
 */
function harnessFailure(line, recorded) {
  const text = line.trim();
  const found = HARNESS_ENDINGS.find(([pattern]) => pattern.test(text));
  if (found !== undefined) {
    return { title: t("errors.taskStopped"), short: t(found[1]), next: t(found[2]), details: text };
  }
  if (text !== "" && (recorded || /^[a-z]/.test(text))) {
    return { title: t("errors.taskStopped"), short: t("errors.faultShort"), next: t("errors.faultNext"), details: text };
  }
  return null;
}

/**
 * A failed run's last words as a failure the owner reads plainly: a model service's (`serviceFailure`) or one the harness
 * wrote (`harnessFailure`); null for the agent's own report, which is its answer and stays as it is.
 * @param {string} line
 * @param {boolean} recorded whether the run's end carried an error of the daemon's
 * @returns {{ title: string, short: string, next: string, details: string } | null}
 */
export function runFailure(line, recorded) {
  const service = serviceFailure(line);
  return service === null ? harnessFailure(line, recorded) : { title: t("errors.serviceStopped"), short: service.short, next: service.next, details: service.details };
}

/**
 * The model a refused message named and the one the daemon offers instead (null when none can answer), or null when
 * `error` is no such refusal.
 * @param {unknown} error
 * @returns {{ model: string, fallback: string | null } | null}
 */
export function refusedModel(error) {
  const known = /** @type {{ name?: unknown, status?: unknown, context?: { model?: unknown, problem?: unknown, fallback?: unknown } }} */ (error);
  if (known === null || typeof known !== "object" || known.name !== "ApiError" || known.status !== 422) {
    return null;
  }
  const { model, problem, fallback } = known.context ?? {};
  if (typeof model !== "string" || typeof problem !== "string") {
    return null;
  }
  return { model, fallback: typeof fallback === "string" ? fallback : null };
}

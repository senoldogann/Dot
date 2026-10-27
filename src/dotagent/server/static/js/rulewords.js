// The words of Settings › Rules (item 622): what a rule says, said as a person says it. Pure functions of a rule's parts
// and of the owner's own words, so they can be read alone (tests/js/rulewords.test.js). A rule reads as one sentence,
// "When Dot wants to run “git push”, it asks you first.": the action from the rule's tool and what it must mention, the
// answer as the badge that ends the sentence. What a rule fits is read from its pattern, which is what the policy
// matches, and from nothing else (not from its why, which may say anything): as far as the pattern is words, or commands
// as the drafter writes them (item 110), and exactly so, since a sentence must say neither less nor more than its rule
// does; a pattern that is more is shown as it is. The patterns the drafter writes, and what the engine makes of the
// others this file reads, are held to it by tests/unit/test_rule_words.py.

import { clip, plural } from "./dom.js";

/** How many characters of the box "When Dot wants to": the sentence the drafter is given round them stays under the
 * 500 it takes (`RuleDraftRequest`). */
export const WANTS_CHARS = 400;
/** How far back a rule is tried before it is added (item 469) and how it was used is counted (item 473), in days. */
export const LATELY_DAYS = 30;
// A pattern longer than this is cut in the sentence of a rule that cannot be put in words; the whole of it is in Details.
const PATTERN_CHARS = 40;

/**
 * The four answers of a rule. `choice` is the words of the choice in the adder, in the voice of the owner speaking to Dot;
 * `says` ends the sentence a rule reads as ("it asks you first"); `text` says what Dot does then; `wish` is the owner's
 * wish as the drafter is given it ("it should ask me first"); `tone` colours the badge. No answer refuses outright:
 * a rule's answers are these four (docs/safety.md), and the nearest to "never" is the one that leaves the step to you.
 */
export const ANSWERS = [
  {
    id: "allow",
    choice: "Allow on its own",
    says: "goes ahead without asking",
    tone: "b-ok",
    text: "Dot does it without asking. If it has just read a web page or a mail, it still asks before anything that reaches outside this Mac.",
    wish: "do it without asking me",
  },
  {
    id: "preapproved",
    choice: "Allow, whatever it has read",
    says: "goes ahead, whatever it has read",
    tone: "b-ok",
    text: "Dot does it without asking, even after it has read a web page or a mail.",
    wish: "do it without asking me, even after it has read web pages or mail",
  },
  {
    id: "ask",
    choice: "Ask me first",
    says: "asks you first",
    tone: "b-warn",
    text: "Dot stops and asks you every time; the run waits for your answer.",
    wish: "ask me first",
  },
  {
    id: "handoff",
    choice: "Leave it to me",
    says: "leaves it to you",
    tone: "b-hand",
    text: "Dot does not do it. It says what needs doing and waits: you do the step (in the browser you can take over and give it back), then tell Dot, and the run goes on from there.",
    wish: "stop and let me do the step myself",
  },
];
/** The answers the adder offers, in the order of its choice: the other, which goes further than "on its own", is in Details. */
export const CHOICES = ["allow", "ask", "handoff"];
/** What the choice says until the owner picks: the answer that cannot make Dot do more than it does now. */
export const DEFAULT_CHOICE = "ask";

/** @typedef {{ text: string } | { code: string, title: string }} Part  A word or two of a sentence, or a piece of code in it, whose whole is its `title`. */

/** The answer `id`, or a stand-in for an id this page does not know. */
export function answerOf(id) {
  return ANSWERS.find((answer) => answer.id === id) ?? { id, choice: id, says: id, tone: "b-muted", text: "", wish: "" };
}

/** The sentence the drafter is given for the box and the choice: "When Dot wants to run git status, it should ask me first." */
export function draftSentence(wants, choice) {
  const action = wants
    .trim()
    .replace(/^to\s+/iu, "")
    .replace(/[\s.,;:!?]+$/u, "");
  return `When Dot wants to ${action}, it should ${answerOf(choice).wish}.`;
}

// What each tool is, as a person says what Dot does with it: one phrase for each tool or family, and no phrase for two,
// so that a sentence is not broader than its rule (a rule on `find_files` does not reach `list_dir`) nor narrower (a
// rule on `browser_act` reaches a press of a key and a drag as well as a click). `object`: the verb takes a thing ("run
// a command that mentions …"), where the others take "for something that mentions …". A tool not here is said by its
// own name.
const KNOWN_TOOLS = new Map(
  Object.entries({
    "*": { verb: "do anything", object: true },
    shell: { verb: "run a command", object: true },
    apple_script: { verb: "run an AppleScript", object: true },
    write_file: { verb: "write a file", object: true },
    edit_file: { verb: "change a file", object: true },
    read_file: { verb: "read a file", object: true },
    list_dir: { verb: "look inside a folder", object: true },
    find_files: { verb: "find a file", object: true },
    search_files: { verb: "search inside files" },
    "browser_*": { verb: "use the web browser" },
    browser_open: { verb: "open a web page", object: true },
    browser_act: { verb: "work on a web page (click, type, press keys, drag)" },
    browser_fill_login: { verb: "fill in a login on a web page" },
    browser_create_login: { verb: "make the login of a new account on a web page" },
    browser_upload: { verb: "upload a file on a web page" },
    browser_eval: { verb: "run a script on a web page" },
    "desktop_*": { verb: "work with apps and the screen of this Mac" },
    desktop_open_app: { verb: "open an app", object: true },
    "terminal_*": { verb: "use a terminal" },
    web_search: { verb: "search the web" },
    web_fetch: { verb: "read a web page", object: true },
    web_crawl: { verb: "read a whole website", object: true },
    web_extract: { verb: "pull facts out of a web page", object: true },
    "memory_*": { verb: "use its memory" },
    "schedule_*": { verb: "work with your schedules" },
    "mcp__*": { verb: "use a connected app" },
  }),
);

const text = (value) => ({ text: value });
const code = (value, title = value) => ({ code: value, title });

/** The tool of a rule as the part of a sentence after "Dot wants to": `verb`, as parts, and whether it takes a thing. */
function toolOf(toolGlob) {
  const known = KNOWN_TOOLS.get(toolGlob);
  if (known !== undefined) {
    return { verb: [text(known.verb)], object: known.object === true };
  }
  return { verb: [text("use "), code(toolGlob === "" ? "…" : toolGlob)], object: false };
}

/** "“a”", "“a” or “b”", "“a”, “b” or “c”": the words of a rule, quoted as the drafter quotes them. */
function either(words) {
  const quoted = words.map((word) => `“${word}”`);
  return quoted.length === 1 ? quoted[0] : `${quoted.slice(0, -1).join(", ")} or ${quoted[quoted.length - 1]}`;
}

// What a pattern may hold for one of its alternatives to be words: every character but the signs of a pattern (`\ ^ $ . |
// ? * + ( ) [ ] { }`) is itself, a sign is itself when a backslash comes before it, which is how the drafter writes the
// signs of a word (`x\.com`, `c\+\+`), and `[ \t]+` is a space, which is how it writes the space between the words of a
// command (it fits two spaces or a tab as well, a little more than the one space the sentence says: the sentence errs the
// safe way for a rule that stops Dot, and the drafter's command pattern allows nothing more by it). A space is a character
// like any other: one at the edge of an alternative is one the rule asks for.
const PLAIN_ALTERNATIVE = /^(?:[^\\^$.|?*+()[\]{}]|\\[^\p{L}\p{N}]|\[ \\t\]\+)+$/u;
const PLAIN_PART = /\\([^\p{L}\p{N}])|\[ \\t\]\+/gu;
// What words cannot show: a character that is not printed (a line break, a joiner, a mark of direction) and a space that is
// not the plain one (a no-break space looks like one and fits only itself).
const UNSEEN = /(?! )[\p{C}\p{Z}]/u;
const UNESCAPED_BAR = /(?<!\\)\|/u;
const UNESCAPED_PAREN = /(?<!\\)[()]/u;
// What the engine's word boundary (`\b`) takes as a word character, its `\w`: what is alphabetic (a circled letter too), a
// mark (a vowel sign), a decimal digit (`²` and `½` are none), a connector (`_`, `‿`) and a joiner. The page holds it as
// these properties, and tests/unit/test_rule_words.py holds them to the engine, code point by code point.
const WORD_CHAR = /[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]/u;
// `prefix(a|b)suffix`, with nothing else in the parentheses; a sign made itself by a backslash is no bracket.
const GROUPED = /^((?:[^()|\\]|\\.)*)\((?:\?:)?((?:[^()\\]|\\.)*)\)((?:[^()|\\]|\\.)*)$/su;
const WHOLE_GROUP = /^(?:\(\?:|\()(.*)\)$/su;

/**
 * The words a pattern is made of, when it is nothing but words, and whether it fits whole words alone (`whole`) or the
 * letters of a word anywhere: `\bgit push\b` is ["git push"] as whole words, `rm` is ["rm"] and fits "format" too,
 * `(?:\bmybank\.com\b|\binvoice\b)` is ["mybank.com", "invoice"] and `\bgit (status|diff)\b` is ["git status", "git
 * diff"]. Whole words are a word boundary (`\b`) at each edge of a word that is a word character, as the drafter writes
 * the words a rule stops Dot at; the letters of a word anywhere are none at all. Null for any other pattern, since a
 * sentence must say neither less nor more than the rule does: a boundary at one edge only (it fits "git pushes", not
 * "digit push"), whole words in one alternative and not in another, a boundary beside a sign (it asks for a letter or a
 * digit on its far side), a character that is not printed, a space that is not the plain one.
 */
function readWords(pattern) {
  let body = pattern;
  const wholeGroup = WHOLE_GROUP.exec(body);
  if (wholeGroup !== null && !UNESCAPED_PAREN.test(wholeGroup[1])) {
    body = wholeGroup[1];
  }
  let alternatives = body.split(UNESCAPED_BAR);
  if (UNESCAPED_PAREN.test(body)) {
    const grouped = GROUPED.exec(body);
    if (grouped === null) {
      return null;
    }
    alternatives = grouped[2].split(UNESCAPED_BAR).map((middle) => `${grouped[1]}${middle}${grouped[3]}`);
  }
  const words = [];
  let whole = true;
  let loose = true;
  for (const alternative of alternatives) {
    // Only the edges of an alternative may be a boundary: one inside it is a sign of a pattern.
    const bounded = [alternative.startsWith("\\b"), alternative.endsWith("\\b")];
    const bare = alternative.replace(/^\\b/u, "").replace(/\\b$/u, "");
    if (!PLAIN_ALTERNATIVE.test(bare)) {
      return null;
    }
    const word = bare.replace(PLAIN_PART, (_, sign) => sign ?? " ");
    if (UNSEEN.test(word)) {
      return null;
    }
    const characters = [...word];
    const wordy = [WORD_CHAR.test(characters[0]), WORD_CHAR.test(characters.at(-1))];
    // A boundary has a word character on one side and none on the other: beside a sign it asks for a letter or a digit on
    // the far side, which the words do not say.
    if (bounded.some((is, edge) => is && !wordy[edge])) {
      return null;
    }
    const kept = bounded.filter(Boolean).length;
    // Whole words keep a boundary at every edge that is a word character, and the letters of a word anywhere at none: an
    // alternative with no edge of that kind is both.
    whole = whole && kept === wordy.filter(Boolean).length;
    loose = loose && kept === 0;
    words.push(word);
  }
  return whole || loose ? { words, whole } : null;
}

/** The words a pattern is made of when it is nothing but words (`readWords`), or null. */
export function plainWords(pattern) {
  return readWords(pattern)?.words ?? null;
}

// What the drafter writes round the commands of a rule that lets Dot run them (policy/drafting.py, `command_pattern`): the
// commands alone on the action's line, after a `cd` at most, with plain arguments.
const COMMAND_HEAD = "\\A[^\\n]*\\n[ \\t]*(?:cd[ \\t]+[^\\n\\r;&|`$<>()]+&&[ \\t]*)?(?:";
const COMMAND_TAIL = ")(?:[ \\t][^\\n\\r;&|`$<>()]*)?\\Z";

/** The commands a pattern of the drafter's lets Dot run alone, or null for any other pattern. */
export function plainCommands(pattern) {
  if (!pattern.startsWith(COMMAND_HEAD) || !pattern.endsWith(COMMAND_TAIL)) {
    return null;
  }
  return plainWords(pattern.slice(COMMAND_HEAD.length, pattern.length - COMMAND_TAIL.length));
}

/** What a rule fits, as the part of a sentence after "Dot wants to", read from its tool and its pattern, which are what the
 * policy matches: null when the pattern is more than words, and commands as the drafter writes them. */
function actionOf(rule) {
  const tool = toolOf(rule.tool_glob);
  if (rule.match_regex === "") {
    return tool.verb;
  }
  const commands = rule.tool_glob === "shell" ? plainCommands(rule.match_regex) : null;
  if (commands !== null) {
    // The pattern fits the whole line of the command: a `cd` may come first, and the arguments are plain, which is not "any":
    // one with `;` `&` `|` `$` `<` `>` `(` `)` or a backtick (a command chained to it, run inside it, sent on from it) is not fit.
    return [text(`run ${either(commands)} (with plain arguments, after changing folder at most)`)];
  }
  const read = readWords(rule.match_regex);
  if (read === null) {
    return null;
  }
  const link = tool.object ? "that" : "for something that";
  return [...tool.verb, text(` ${link} ${read.whole ? "mentions" : "contains"} ${either(read.words)}`)];
}

// What a pattern that cannot be put in words is shown as: a rule that stops Dot may have it cut short, with the whole of it
// in the tooltip, but a rule that lets Dot act is shown whole, since a part of its pattern is not what it fits.
const LETTING = new Set(["allow", "preapproved"]);

/** The pattern of a rule that cannot be put in words, as it is, in a code part. */
function looksLike(rule) {
  const tool = toolOf(rule.tool_glob);
  const shown = LETTING.has(rule.behavior) ? rule.match_regex : clip(rule.match_regex, PATTERN_CHARS);
  return [...tool.verb, text(` ${tool.object ? "that" : "for something that"} looks like `), code(shown, rule.match_regex)];
}

/**
 * The sentence a rule reads as, in parts: "When Dot wants to " + `what` + ", it " + the answer's `says` + ".". `rule` is
 * `{ tool_glob, match_regex, behavior, description }`. What it fits is read from its tool and its pattern alone, never
 * from the rule's why, which may say anything (the form of old let the owner change the pattern and keep the drafter's
 * sentence): a sentence must say neither less nor more than the rule does. A pattern that is more than words and commands
 * is shown as it is.
 * @returns {{ what: Part[], answer: ReturnType<typeof answerOf> }}
 */
export function ruleParts(rule) {
  return { what: actionOf(rule) ?? looksLike(rule), answer: answerOf(rule.behavior) };
}

/** The sentence of a rule as one line of plain text, for what has no room for a badge (a dialog, a screen reader). */
export function ruleText(rule) {
  const { what, answer } = ruleParts(rule);
  const action = what.map((part) => ("text" in part ? part.text : part.code)).join("");
  return `When Dot wants to ${action}, it ${answer.says}.`;
}

/** Why a rule never decides (`never_decided_by`, item 449): the rule `first` decides in its place; `firstAnswer` is its
 * answer and `answer` this rule's. A rule that hands the step to you decides wherever it stands (item 481). */
export function neverUsedWhy(first, firstAnswer, answer) {
  if (firstAnswer === "handoff" && answer !== "handoff") {
    return `Rule ${first} leaves everything this rule covers to you, and a rule like that always wins, wherever it stands. So this rule is never used. Narrow one of them, or delete one.`;
  }
  return `Rule ${first} already covers everything this rule covers and is checked first, so this rule is never used. Move it above rule ${first}, or delete one of them.`;
}

/** The line that says what a rule would have done on the steps of the last days (the answer of `POST /rules/preview`), in
 * the words of a person: the first line the owner reads before the rule is added. */
export function latelyLine(view, days) {
  const span = plural(days, "day", "days");
  if (view.scanned === 0) {
    return `Nothing Dot did in the last ${span} was there to try it on.`;
  }
  const read = view.partial && view.since !== null ? `the newest ${view.scanned} things Dot did` : `the ${view.scanned} things Dot did in the last ${span}`;
  return view.fits === 0 ? `It would not have applied to any of ${read}.` : `It would have applied to ${view.fits} of ${read}.`;
}

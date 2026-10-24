// The words of Settings › Rules (rulewords.js): the sentence a rule reads as, the sentence the drafter is given, and the
// lines that say what a rule would have done lately (item 622).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ANSWERS,
  answerOf,
  CHOICES,
  DEFAULT_CHOICE,
  draftSentence,
  latelyLine,
  neverUsedWhy,
  plainCommands,
  plainWords,
  ruleParts,
  ruleText,
  WANTS_CHARS,
} from "../../src/dotagent/server/static/js/rulewords.js";

// The rules `policy/drafting.py` writes, each with the sentence it must read as: tests/unit/test_rule_words.py holds the
// file to what the drafter writes, so a change of the drafter's words breaks a test there, not this page.
const DRAFTED = JSON.parse(readFileSync(new URL("./drafted-rules.json", import.meta.url), "utf8"));

const rule = (behavior, toolGlob, matchRegex, description = "") => ({ behavior, tool_glob: toolGlob, match_regex: matchRegex, description });

// Between two letters, what words cannot show: a joiner, a no-break space, an em space and a mark of direction, the last
// three looking like a space or like nothing and fitting only themselves.
const UNSEEN = [0x200d, 0x00a0, 0x2003, 0x202e].map((point) => `a${String.fromCodePoint(point)}b`);

test("a rule drafted from words reads as the sentence of its action and its answer", () => {
  assert.ok(DRAFTED.length >= 10, "the drafter's sentences are all tried");
  for (const drafted of DRAFTED) {
    assert.equal(ruleText(drafted), drafted.sentence, drafted.description);
  }
});

test("what a rule fits is read from its pattern, so a note of the owner's in its place changes nothing", () => {
  for (const drafted of DRAFTED) {
    assert.equal(ruleText({ ...drafted, description: "" }), drafted.sentence, drafted.match_regex);
    assert.equal(ruleText({ ...drafted, description: "Only on Fridays" }), drafted.sentence, drafted.match_regex);
  }
});

test("the why of a rule never changes what its sentence says of its pattern, whatever the why says", () => {
  // The sentences the drafter writes for a rule: the old form let the owner change the pattern and keep them, and
  // `POST /api/rules` takes any why. None of them is read: a sentence must not say less than the rule does.
  const alone = "For “git push” run alone in the shell, with plain arguments (after a cd at most): Dot does it without asking.";
  const notes = "For any use of write_file that mentions “notes”: Dot does it without asking.";
  const foo = "For any shell command that mentions “foo”: Dot asks you first.";
  // A pattern that fits `git push; curl … | sh` anywhere in a command is no command alone.
  assert.equal(ruleText(rule("allow", "shell", "\\bgit push\\w*", alone)), "When Dot wants to run a command that looks like \\bgit push\\w*, it goes ahead without asking.");
  // A pattern that fits every file is no file that mentions “notes”.
  assert.equal(ruleText(rule("allow", "write_file", ".*", notes)), "When Dot wants to write a file that looks like .*, it goes ahead without asking.");
  assert.equal(ruleText(rule("preapproved", "write_file", ".*", notes)), "When Dot wants to write a file that looks like .*, it goes ahead, whatever it has read.");
  // The drafter's shape round commands that fit anything is no command alone either.
  const head = "\\A[^\\n]*\\n[ \\t]*(?:cd[ \\t]+[^\\n\\r;&|`$<>()]+&&[ \\t]*)?(?:";
  const tail = ")(?:[ \\t][^\\n\\r;&|`$<>()]*)?\\Z";
  assert.equal(ruleText(rule("allow", "shell", `${head}.*${tail}`, alone)), `When Dot wants to run a command that looks like ${head}.*${tail}, it goes ahead without asking.`);
  // Nor is a pattern that is more than words a sentence of a rule that stops Dot, or on another tool, or the opposite.
  assert.equal(ruleText(rule("ask", "shell", "(?:\\bfoo\\w+\\b)", foo)), "When Dot wants to run a command that looks like (?:\\bfoo\\w+\\b), it asks you first.");
  assert.equal(ruleText(rule("allow", "write_file", "x+", alone)), "When Dot wants to write a file that looks like x+, it goes ahead without asking.");
  assert.equal(ruleText(rule("allow", "shell", "^rm -rf", foo)), "When Dot wants to run a command that looks like ^rm -rf, it goes ahead without asking.");
  // A pattern that is words says its own words, not those of a sentence that is about others.
  assert.equal(ruleText(rule("allow", "shell", "\\bgit push\\b", "For “git status” run alone in the shell, with plain arguments (after a cd at most): Dot does it without asking.")), "When Dot wants to run a command that mentions “git push”, it goes ahead without asking.");
});

test("a rule written by hand reads from its tool and its pattern", () => {
  assert.equal(ruleText(rule("ask", "write_file", "")), "When Dot wants to write a file, it asks you first.");
  assert.equal(ruleText(rule("ask", "shell", "\\bgit push\\b")), "When Dot wants to run a command that mentions “git push”, it asks you first.");
  assert.equal(ruleText(rule("allow", "mcp__github__create_issue", "")), "When Dot wants to use mcp__github__create_issue, it goes ahead without asking.");
  assert.equal(ruleText(rule("preapproved", "*", "")), "When Dot wants to do anything, it goes ahead, whatever it has read.");
  // Whole words are mentioned; the letters of a word anywhere are contained (`ls` fits "tools", `rm` fits "format").
  assert.equal(ruleText(rule("handoff", "browser_*", "\\bmybank\\b")), "When Dot wants to use the web browser for something that mentions “mybank”, it leaves it to you.");
  assert.equal(ruleText(rule("handoff", "browser_*", "mybank")), "When Dot wants to use the web browser for something that contains “mybank”, it leaves it to you.");
  assert.equal(ruleText(rule("allow", "mcp__github__*", "x y")), "When Dot wants to use mcp__github__* for something that contains “x y”, it goes ahead without asking.");
  assert.equal(ruleText(rule("allow", "shell", "ls|rm")), "When Dot wants to run a command that contains “ls” or “rm”, it goes ahead without asking.");
  assert.equal(ruleText(rule("allow", "shell", "\\bls\\b|\\brm\\b")), "When Dot wants to run a command that mentions “ls” or “rm”, it goes ahead without asking.");
  // A word that starts or ends with a sign needs no boundary there.
  assert.equal(ruleText(rule("ask", "shell", "\\bc\\+\\+")), "When Dot wants to run a command that mentions “c++”, it asks you first.");
  assert.equal(ruleText(rule("ask", "shell", "c\\+\\+")), "When Dot wants to run a command that contains “c++”, it asks you first.");
});

// The engine's own answers to these patterns are held in tests/unit/test_rule_words.py: that is what each sentence must say.
test("a sentence says neither less nor more than the rule fits, down to a space, a boundary and a word character", () => {
  const says = (behavior, pattern) => ruleText(rule(behavior, "shell", pattern));
  const asks = (what) => `When Dot wants to run a command that ${what}, it asks you first.`;
  // A space at the edge of a word is one the rule asks for (" rm " does not fit "format"): the sentence keeps it.
  assert.equal(says("ask", " rm "), asks("mentions “ rm ”"));
  assert.equal(says("ask", "rm | del"), asks("contains “rm ” or “ del”"));
  // A space that is not the plain one looks like it and fits only itself, and a line break, a joiner and a mark of direction
  // are not seen at all: words cannot show them, so the pattern is (whole in its tooltip, whatever the sentence flattens).
  for (const odd of [...UNSEEN, "a\nb", "a\\\nb"]) {
    const { what } = ruleParts(rule("ask", "shell", odd));
    assert.deepEqual(what.slice(0, 2), [{ text: "run a command" }, { text: " that looks like " }], JSON.stringify(odd));
    assert.equal(what[2].title, odd);
  }
  // A boundary beside a sign has a letter or a digit on its far side, which words do not say: shown as it is.
  assert.equal(says("ask", "\\b€50"), asks("looks like \\b€50"));
  assert.equal(says("handoff", "50 €\\b"), "When Dot wants to run a command that looks like 50 €\\b, it leaves it to you.");
  // A boundary at one edge only fits "git pushes" and not "digit push": neither whole words nor the letters anywhere.
  assert.equal(says("ask", "\\bgit push"), asks("looks like \\bgit push"));
  assert.equal(says("ask", "git push\\b"), asks("looks like git push\\b"));
  assert.equal(says("ask", "\\bgit (status|diff)"), asks("looks like \\bgit (status|diff)"));
  // Whole words in one alternative and the letters anywhere in another are not "mentions" for both nor "contains" for both.
  assert.equal(says("ask", "\\bls\\b|rm"), asks("looks like \\bls\\b|rm"));
  assert.equal(says("ask", "\\bgit push|git pull\\b"), asks("looks like \\bgit push|git pull\\b"));
  // An alternative with no word character at its edges is both, and leaves the others to tell.
  assert.equal(says("ask", "\\bgit push\\b|-->"), asks("mentions “git push” or “-->”"));
  assert.equal(says("ask", "git push|-->"), asks("contains “git push” or “-->”"));
  // A word character is the engine's: `²` and `½` are none (a boundary after them has a letter after it), a circled letter,
  // a connector (`‿`) and a vowel sign (Hindi "ka") are.
  assert.equal(says("ask", "\\bx²\\b"), asks("looks like \\bx²\\b"));
  assert.equal(says("ask", "\\bx²"), asks("mentions “x²”"));
  assert.equal(says("ask", "x²"), asks("contains “x²”"));
  assert.equal(says("ask", "\\bⒶ\\b"), asks("mentions “Ⓐ”"));
  assert.equal(says("ask", "Ⓐ"), asks("contains “Ⓐ”"));
  assert.equal(says("ask", "\\b‿\\b"), asks("mentions “‿”"));
  assert.equal(says("ask", "\\bका\\b"), asks("mentions “का”"));
  assert.equal(says("ask", "\\bका"), asks("looks like \\bका"));
  assert.equal(says("ask", "का"), asks("contains “का”"));
  // `[ \t]+` is the drafter's space: after a backslash it is a bracket, a space and a tab, not a space.
  assert.equal(says("ask", "\\[ \\t]+"), asks("looks like \\[ \\t]+"));
});

test("each tool is said in words that are its own, so that a sentence is neither broader nor narrower than its rule", () => {
  const names = [
    "*", "shell", "apple_script", "write_file", "edit_file", "read_file", "list_dir", "find_files", "search_files",
    "browser_*", "browser_open", "browser_act", "browser_fill_login", "browser_upload", "browser_eval", "desktop_*",
    "desktop_open_app", "terminal_*", "web_search", "web_fetch", "web_crawl", "web_extract", "memory_*", "schedule_*", "mcp__*",
  ];
  const said = names.map((name) => ruleText(rule("ask", name, "")));
  assert.equal(new Set(said).size, names.length, "two tools are said alike");
  assert.equal(ruleText(rule("ask", "find_files", "")), "When Dot wants to find a file, it asks you first.");
  assert.equal(ruleText(rule("ask", "browser_act", "")), "When Dot wants to work on a web page (click, type, press keys, drag), it asks you first.");
  // A tool that is not known, and a family that is not one of the known, is said by its name.
  assert.equal(ruleText(rule("ask", "web_*", "")), "When Dot wants to use web_*, it asks you first.");
  assert.equal(ruleText(rule("ask", "browser_o*", "")), "When Dot wants to use browser_o*, it asks you first.");
});

test("a pattern that is more than words is shown as it is: cut when long for a rule that stops Dot, whole for one that lets it act", () => {
  const long = "^(?:rm|rmdir)\\s+-[a-z]*r[a-z]*\\s+(?:/|~)[^\\n]*$";
  const stops = ruleParts(rule("ask", "shell", long)).what;
  assert.deepEqual(stops.slice(0, 2), [{ text: "run a command" }, { text: " that looks like " }]);
  assert.equal(stops[2].code.length, 40);
  assert.ok(stops[2].code.endsWith("…") && long.startsWith(stops[2].code.slice(0, 39)));
  assert.equal(stops[2].title, long);
  // What a rule that lets Dot act fits is not cut short in its sentence: a part of a pattern is not what the rule fits.
  for (const letting of ["allow", "preapproved"]) {
    const fits = ruleParts(rule(letting, "shell", long)).what;
    assert.deepEqual(fits[2], { code: long, title: long });
  }
});

test("the words of a pattern that is nothing but words", () => {
  assert.deepEqual(plainWords("\\bgit push\\b"), ["git push"]);
  assert.deepEqual(plainWords("rm -rf"), ["rm -rf"]);
  assert.deepEqual(plainWords("(?:\\bmybank\\.com\\b|\\binvoice\\b)"), ["mybank.com", "invoice"]);
  assert.deepEqual(plainWords("\\bgit (status|diff|log)\\b"), ["git status", "git diff", "git log"]);
  assert.deepEqual(plainWords("\\bgit (?:status|diff)\\b"), ["git status", "git diff"]);
  assert.deepEqual(plainWords("foo|bar"), ["foo", "bar"]);
  assert.deepEqual(plainWords("şirket raporu"), ["şirket raporu"]);
  // The drafter's space between the words of a command, and the signs a backslash makes themselves.
  assert.deepEqual(plainWords("(?:\\bgit[ \\t]+commit[ \\t]+\\-m\\b)"), ["git commit -m"]);
  assert.deepEqual(plainWords("c\\+\\+|a\\|b|foo\\(bar\\)|x\\&y"), ["c++", "a|b", "foo(bar)", "x&y"]);
  // Every character that is no sign of a pattern is itself, whatever its script or its kind.
  assert.deepEqual(plainWords("(?:\\bdon’t[ \\t]+panic\\b)"), ["don’t panic"]);
  assert.deepEqual(plainWords("(?:\\b50[ \\t]+€)"), ["50 €"]);
  assert.deepEqual(plainWords("a…b|“x”|🙂"), ["a…b", "“x”", "🙂"]);
  // A space is a character like any other, at the edge of a word too.
  assert.deepEqual(plainWords(" rm "), [" rm "]);
  // More than words: a dot that means any sign, an anchor, a class, a count, a group inside a group, an empty alternative,
  // an escape that is a class of letters, a boundary inside a word, at one edge only or beside a sign, whole words in one
  // alternative and not in another, a bracket a backslash made a character (not the drafter's space), a character that is
  // not printed and a space that is not the plain one.
  for (const more of [
    "mybank.com", "^rm", "rm$", "rm\\s+-rf", "a+", "[ab]c", "((a|b)c)", "a||b", "", "\\d{3}", "git (status|diff) now (a|b)", "\\w+", "(a|b",
    "\\bgit\\bpush\\b", ...UNSEEN, "a\nb", ".*", "\\bgit push\\w*", "\\b€50", "50 €\\b", "\\[ \\t]+", "\\bgit push", "git push\\b",
    "\\bls\\b|rm", "\\bx²\\b", "a\\\nb",
  ]) {
    assert.equal(plainWords(more), null, JSON.stringify(more));
  }
});

test("the commands a pattern of the drafter's lets Dot run alone", () => {
  const head = "\\A[^\\n]*\\n[ \\t]*(?:cd[ \\t]+[^\\n\\r;&|`$<>()]+&&[ \\t]*)?(?:";
  const tail = ")(?:[ \\t][^\\n\\r;&|`$<>()]*)?\\Z";
  assert.deepEqual(plainCommands(`${head}git[ \\t]+status|git[ \\t]+diff${tail}`), ["git status", "git diff"]);
  assert.deepEqual(plainCommands(`${head}npm[ \\t]+run[ \\t]+build\\-all${tail}`), ["npm run build-all"]);
  // Any other pattern is not one: a command that is more than words, and a pattern of a hand's.
  assert.equal(plainCommands(`${head}git[ \\t]+\\w+${tail}`), null);
  assert.equal(plainCommands("\\bgit status\\b"), null);
  assert.equal(plainCommands(`${head}git[ \\t]+status`), null);
  // Only the shell is run alone: the same pattern on another tool is read as words.
  const drafted = rule("allow", "write_file", `${head}git[ \\t]+status${tail}`);
  assert.match(ruleText(drafted), /^When Dot wants to write a file that looks like /);
});

test("what the adder sends the drafter reads as the owner's wish, in one sentence", () => {
  assert.equal(draftSentence("run git status", "allow"), "When Dot wants to run git status, it should do it without asking me.");
  assert.equal(draftSentence("  to delete anything with rm. ", "ask"), "When Dot wants to delete anything with rm, it should ask me first.");
  assert.equal(draftSentence("open mybank.com!", "handoff"), "When Dot wants to open mybank.com, it should stop and let me do the step myself.");
  // The longest box and the longest wish stay under the 500 characters the daemon takes.
  for (const answer of ANSWERS) {
    assert.ok(draftSentence("x".repeat(WANTS_CHARS), answer.id).length <= 500, answer.id);
  }
});

test("the adder offers three answers, which are answers of the policy, and starts at the one that asks", () => {
  assert.deepEqual(
    CHOICES.map((id) => answerOf(id).choice),
    ["Allow on its own", "Ask me first", "Leave it to me"],
  );
  assert.deepEqual(
    ANSWERS.map((answer) => answer.id),
    ["allow", "preapproved", "ask", "handoff"],
  );
  assert.equal(DEFAULT_CHOICE, "ask");
  assert.ok(CHOICES.includes(DEFAULT_CHOICE));
  assert.equal(answerOf("nope").tone, "b-muted");
  // No answer refuses outright, and none is called "never": a rule can only allow, ask or leave the step to the owner.
  assert.equal(
    ANSWERS.some((answer) => /\bnever\b/iu.test(answer.choice)),
    false,
  );
});

test("why a rule is never used, and what helps", () => {
  assert.equal(
    neverUsedWhy(1, "allow", "ask"),
    "Rule 1 already covers everything this rule covers and is checked first, so this rule is never used. Move it above rule 1, or delete one of them.",
  );
  assert.equal(
    neverUsedWhy(4, "handoff", "allow"),
    "Rule 4 leaves everything this rule covers to you, and a rule like that always wins, wherever it stands. So this rule is never used. Narrow one of them, or delete one.",
  );
  // A hand-off after a hand-off that covers it is kept from deciding by position alone, which moving it helps.
  assert.match(neverUsedWhy(2, "handoff", "handoff"), /Move it above rule 2/);
});

test("what a rule would have done lately, in a line a person reads", () => {
  const view = { scanned: 3200, since: null, partial: false, fits: 18 };
  assert.equal(latelyLine(view, 30), "It would have applied to 18 of the 3200 things Dot did in the last 30 days.");
  assert.equal(latelyLine({ ...view, fits: 0 }, 30), "It would not have applied to any of the 3200 things Dot did in the last 30 days.");
  assert.equal(latelyLine({ ...view, scanned: 0, fits: 0 }, 1), "Nothing Dot did in the last 1 day was there to try it on.");
  const partial = { ...view, partial: true, since: 1790000000, scanned: 20000 };
  assert.equal(latelyLine(partial, 30), "It would have applied to 18 of the newest 20000 things Dot did.");
});

"""The Rules page reads the pattern the drafter writes for a rule (item 622) and says what it fits in its own words
(``rulewords.js``): ``\\bgit[ \\t]+push\\b`` is "mentions “git push”", the drafter's pattern for a command a rule lets
Dot run is "(with plain arguments, after changing folder at most)". It reads nothing from the
rule's why, which may say anything, and says neither less nor more than the pattern fits. The page's test
(``tests/js/rulewords.test.js``) reads the rules in ``tests/js/drafted-rules.json`` and the sentence each must read as;
this test holds the file to what the drafter writes now, so that a change of the drafter's patterns fails here, and not
as a rule that the page shows as a pattern, and holds the page's reading of the others to what the engine does with
them: the policy searches an expression with the ``regex`` module, case-insensitive (``rule_fits``)."""

import json
from pathlib import Path
from typing import Any

import pytest
import regex

from dotagent.policy.drafting import RuleDraft, rule_from_draft

_ROOT: Path = Path(__file__).resolve().parents[2]
_DRAFTED: Path = _ROOT / "tests" / "js" / "drafted-rules.json"
_READER: Path = _ROOT / "src" / "dotagent" / "server" / "static" / "js" / "rulewords.js"
_TOOLS: frozenset[str] = frozenset({"shell", "browser_open", "browser_act", "write_file", "apple_script", "web_search"})
_CASES: list[dict[str, Any]] = json.loads(_DRAFTED.read_text(encoding="utf-8"))
# What the page's sentences rest on, as the engine answers it: (pattern, text, whether the pattern fits the text).
_ENGINE: list[tuple[str, str, bool]] = [
    # A space at the edge of a word is one the rule asks for, and a no-break space fits only itself.
    (" rm ", "format", False),
    (" rm ", "x rm y", True),
    (" rm ", "rm", False),
    ("a\N{NO-BREAK SPACE}b", "a b", False),
    # A boundary beside a sign has a word character on its far side: "mentions “€50”" would say less.
    (r"\b€50", "price €50", False),
    (r"\b€50", "price€50", True),
    (r"50 €\b", "50 €", False),
    (r"50 €\b", "50 €x", True),
    # A boundary at one edge only fits more than whole words and less than the letters of a word anywhere.
    (r"\bgit push", "git pushes", True),
    (r"\bgit push", "digit push", False),
    (r"git push", "digit push", True),
    (r"\bgit push\b", "git pushes", False),
    (r"\bgit push\b", "run git push now", True),
    # `[ \t]+` is a run of spaces and tabs; after a backslash it is a bracket, a space and a tab.
    (r"git[ \t]+push", "git \t push", True),
    (r"\[ \t]+", "a  b", False),
    (r"\[ \t]+", "[ \t]", True),
    # `²` and `½` are no word characters (a boundary after them has one after it); a circled letter and a vowel sign
    # are.
    (r"\bx²\b", "run x² now", False),
    (r"\bx²", "run x² now", True),
    (r"\bx²", "box²", False),
    (r"\bⒶ\b", "fooⒶbar", False),
    (r"\bⒶ\b", "run Ⓐ now", True),
    (r"Ⓐ", "fooⒶbar", True),
    (r"\bका\b", "कान", False),
    (r"\bका", "कान", True),
]


@pytest.mark.parametrize("case", _CASES, ids=[case["sentence"][:70] for case in _CASES])
def test_the_drafter_still_writes_the_rule_the_page_reads_in_words(case: dict[str, Any]) -> None:
    rule = rule_from_draft(RuleDraft(problem="", **case["draft"]), _TOOLS)
    assert (rule.behavior, rule.tool_glob, rule.match_regex, rule.description) == (
        case["behavior"],
        case["tool_glob"],
        case["match_regex"],
        case["description"],
    )


def test_the_file_holds_every_answer_and_every_kind_of_sentence_the_drafter_writes() -> None:
    answers: set[str] = {case["behavior"] for case in _CASES}
    assert answers == {"allow", "preapproved", "ask", "handoff"}
    scopes: set[str] = set()
    for case in _CASES:
        scope: str = case["description"].removeprefix("For ").partition(": ")[0]
        scopes.add(
            "run alone"
            if "run alone in the shell" in scope
            else "shell mentions"
            if scope.startswith("any shell command that mentions")
            else "tool mentions"
            if scope.startswith("any use of")
            else "every use"
        )
    assert scopes == {"run alone", "shell mentions", "tool mentions", "every use"}


@pytest.mark.parametrize(("pattern", "text", "fits"), _ENGINE)
def test_the_engine_fits_what_the_sentence_says_and_no_more(pattern: str, text: str, fits: bool) -> None:
    assert (regex.search(pattern, text, regex.IGNORECASE) is not None) is fits


def test_a_word_character_is_the_one_the_page_reads() -> None:
    """``rulewords.js`` tells whole words from the letters of a word anywhere by what the engine's ``\\b`` takes as a
    word character, which it holds as these properties: the engine's ``\\w`` is the same, code point by code point."""
    properties: str = r"[\p{Alphabetic}\p{M}\p{Nd}\p{Pc}\p{Join_Control}]"
    everything: str = "".join(chr(point) for point in range(0x110000) if not 0xD800 <= point <= 0xDFFF)
    assert regex.findall(r"\w", everything) == regex.findall(properties, everything)
    assert f"const WORD_CHAR = /{properties}/u;" in _READER.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("command", "fits"),
    [
        ("git status", True),
        ("git status -s --branch", True),
        ("git status --short 'a b'", True),
        ("cd /tmp/work && git status", True),
        ("git statusx", False),
        ("git status; rm x", False),
        ("git status && rm x", False),
        ("git status | cat", False),
        ("git status > out", False),
        ("git status < in", False),
        ("git status $(rm x)", False),
        ("git status `rm x`", False),
        ("git status (a)", False),
        ("cd a && cd b && git status", False),
    ],
)
def test_the_commands_the_page_calls_plain_are_what_the_drafters_pattern_lets_dot_run(command: str, fits: bool) -> None:
    """ "run “git status” (with plain arguments, after changing folder at most)": the arguments hold none of ``;`` ``&``
    ``|`` ``$`` ``<`` ``>`` ``(`` ``)`` or a backtick, the one ``cd`` comes first, and nothing follows."""
    draft = RuleDraft(problem="", tool="shell", answer="allow", commands=["git status"], mentions=[])
    rule = rule_from_draft(draft, _TOOLS)
    haystack: str = f"Run: {command}\n{command}"
    assert (regex.search(rule.match_regex, haystack, regex.IGNORECASE) is not None) is fits

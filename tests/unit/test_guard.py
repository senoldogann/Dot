"""The guard no rule turns off (item 429): a message sent for the user or a post in their name asks in every mode and
no rule that allows it answers for them; a yolo run leaves money, credentials, the guard and a pre-approved spend to the
user, and reads their secrets without asking (item 433); and every classifier that knows a message goes out says so."""

from pathlib import Path

import pytest

from dotagent.desktop.safety import StepContext, assess_step
from dotagent.desktop.types import Step
from dotagent.policy.elements import Element, assessment_for, naming_reasons
from dotagent.policy.engine import auto_review, decide, decide_past_review, route_for, waived_in_yolo
from dotagent.policy.shell import assess_applescript, assess_shell
from dotagent.policy.types import ActionInfo, ApprovalMode, Decision
from dotagent.store.models import Behavior, RuleRecord
from dotagent.tools.mcp import sends_messages

_HOME: Path = Path("/tmp/dot-tests/workspace")
MESSAGE: ActionInfo = ActionInfo(
    tool="apple_script",
    kind="app_control",
    risk="high",
    summary="Run applescript: send the reply",
    target="Mail",
    flags=("external_send", "message_send"),
)
POST: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="medium",
    summary="Post the reply",
    target="https://social.example/thread",
    flags=("public_post",),
)
PAYMENT: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="high",
    summary="Pay the invoice",
    target="shop.example",
    flags=("financial",),
)
SECRET: ActionInfo = ActionInfo(
    tool="read_file",
    kind="read",
    risk="high",
    summary="Read the SSH key",
    target="~/.ssh/id_ed25519",
    flags=("secret_access",),
)
DELETE: ActionInfo = ActionInfo(
    tool="shell", kind="shell", risk="high", summary="Run: rm -rf build", target="rm -rf build", flags=("destructive",)
)


def _rule(behavior: Behavior) -> RuleRecord:
    return RuleRecord(
        id="rule-1",
        position=0,
        enabled=True,
        behavior=behavior,
        tool_glob="*",
        match_regex="",
        description="the user's own answer",
        created_at=0.0,
    )


@pytest.mark.parametrize("action", [MESSAGE, POST])
@pytest.mark.parametrize("behavior", [None, "allow", "preapproved", "ask"])
def test_a_message_or_a_post_asks_whatever_a_rule_that_lets_it_go_says(
    action: ActionInfo, behavior: Behavior | None
) -> None:
    rules: list[RuleRecord] = [] if behavior is None else [_rule(behavior)]
    decision: Decision = decide(action, "task", False, "aggressive", rules, None)
    assert (decision.verdict, decision.source) == ("ask", "guard")
    assert decision.reason.endswith("whatever your rules say")


def test_a_rule_that_hands_a_message_off_is_stricter_and_holds() -> None:
    decision: Decision = decide(MESSAGE, "task", False, "aggressive", [_rule("handoff")], None)
    assert (decision.verdict, decision.source) == ("handoff", "rule")


@pytest.mark.parametrize("mode", ["supervised", "ask"])
def test_money_and_messages_are_the_users_in_every_mode_that_asks(mode: ApprovalMode) -> None:
    """Item 443: the modes that ask still ask; a yolo run is the one that does not, and waives them with the review."""
    for action in (MESSAGE, POST, PAYMENT):
        assert route_for(decide(action, "task", False, "aggressive", [], None), mode) == "user", action.summary
        assert route_for(decide(action, "task", False, "aggressive", [], None), "yolo") == "waived", action.summary


def test_the_review_model_answers_a_message_in_auto_but_never_money() -> None:
    assert route_for(decide(MESSAGE, "task", False, "aggressive", [], None), "auto") == "reviewer"
    assert route_for(decide(PAYMENT, "task", False, "aggressive", [], None), "auto") == "user"


def test_yolo_still_waives_what_the_preset_asks() -> None:
    decision: Decision = decide(DELETE, "task", False, "balanced", [], None)
    assert (decision.verdict, decision.source) == ("ask", "preset")
    assert route_for(decision, "yolo") == "waived"


def test_a_yolo_run_waives_the_review_and_every_question_below_it_and_asks_nothing() -> None:
    """Item 443: money, a message and a credential are the run's in yolo, with the reason each would have asked for kept
    in the step's record; a research run's refusal stands."""
    refusal: Decision | None = auto_review(SECRET)
    assert refusal is not None and refusal.verdict == "deny"
    assert waived_in_yolo(SECRET).reason == "every mode but yolo refuses this (secret_access)"
    paying: ActionInfo = SECRET.model_copy(update={"flags": ("secret_access", "financial")})
    mailing: ActionInfo = SECRET.model_copy(update={"flags": ("secret_access", "external_send", "message_send")})
    for action in (paying, mailing):
        below: Decision = decide_past_review(action, "task", False, "aggressive", [], None)
        assert below.verdict in ("handoff", "ask"), (action.flags, below)
        assert route_for(below, "yolo") == "waived", action.flags
    writing: ActionInfo = SECRET.model_copy(update={"kind": "write"})
    assert decide_past_review(writing, "research", False, "aggressive", [], None).verdict == "deny"


def test_every_classifier_that_knows_a_message_goes_out_says_so() -> None:
    # A Send button, by its name and by the typed judge's reason alike.
    assert "message_send" in assessment_for(naming_reasons(Element(role="button", name="Send"))).flags
    assert "message_send" in assessment_for(["send"]).flags
    assert (
        "message_send" in assess_applescript('tell application "Mail" to send theMessage', _HOME, _HOME, (), ()).flags
    )
    assert "message_send" in assess_shell("echo hi | mail -s Hi ada@example.com", _HOME, _HOME, (), ()).flags
    assert "message_send" in assess_shell("sendmail ada@example.com < note.txt", _HOME, _HOME, (), ()).flags
    assert "message_send" not in assess_shell("mail", _HOME, _HOME, (), ()).flags
    messaging: StepContext = StepContext(role="messaging", app="Messages", element=None, secure=False, line=None)
    assert "message_send" in assess_step(Step(action="key", key="return"), messaging, _HOME, (), ()).flags
    assert "message_send" in assess_step(Step(action="type", text="See you\n"), messaging, _HOME, (), ()).flags
    for name in ("slack_post_message", "send_email", "reply_to_thread", "postMessage", "create_post", "add_comment"):
        assert sends_messages(name), name
    for name in ("get_post", "list_messages", "search_messages", "create_issue", "create_draft", "postgres_query"):
        assert not sends_messages(name), name


_FOUNDATION: str = 'use framework "Foundation"\n'
# Scripts that drive Messages or Mail through words the guard cannot read (item 561): each may send a message, and the
# guard asks as it does for one that says `send`. The commands of the two applications by their codes (their sdef:
# Messages' ichtsend and Mail's emsgsend), a name JavaScript builds as it runs, text run as code, keystrokes, and a
# button pressed through System Events by its place (the review of item 561).
_HIDING_A_SEND: list[str] = [
    'tell application "Messages" to «event ichtsend» "hi" given «class TO  »:participant "+15550100" of account 1',
    'tell application "Mail" to «event emsgsend» m',
    'tell application "Mail" to <<event emsgsend>> m',
    'tell application "Finder" to «event aevtodoc»',
    'var m = Application("Messages"); m["se" + "nd"]("hi", {to: b})',
    'var m = Application("Mail"); const verb = "se" + "nd"; m[verb]()',
    'Application("Mail")[["s", "e", "n", "d"].join("")]()',
    'var m = Application("com.apple.MobileSMS"); m [verb](1)',
    'Application("Mess" + "ages")["se" + "nd"]("hi")',
    "var m = Application('Mail'); eval('m.se' + 'nd()')",
    "var m = Application('Mail'); new Function('m', 'm.' + 'se' + 'nd()')(m)",
    "ObjC.import('Foundation'); var m = Application('Mail'); $.NSAppleScript.alloc.initWithSource($(src))",
    'tell application "Mail" to run script "se" & "nd"',
    'run script POSIX file "/tmp/probe.scpt"',
    'tell application "System Events" to tell process "Messages" to keystroke return',
    'tell application "System Events" to tell process "Mail" to key code 36',
    'tell application "System Events" to tell process "Mail" to click button 1 of toolbar 1 of front window',
    'tell application "System Events" to tell process "Messages" to perform action "AXPress" of button 1 of group 1',
    'Application("System Events").processes["Mail"].windows[0].toolbars[0].buttons[0].click()',
    # The review of item 561, second round: the same classes in spellings that were read by their call syntax.
    'var m = Application("Messages"); m?.["se" + "nd"]("hi", {to: b})',
    'var m = Application("Messages"); var {["se" + "nd"]: go} = m; go("hi", {to: b})',
    'var m = Application("Messages"); m /**/ ["se" + "nd"]("hi", {to: b})',
    'var m = Application("Mail"); eval.call(null, "m.se" + "nd()")',
    'var m = Application("Mail"); var e = eval; e("m.se" + "nd()")',
    'var m = Application("Mail"); (function () {}).constructor("m.se" + "nd()")()',
    'var m = Application("Mail"); var F = Function; F("m.se" + "nd()")()',
    'var m = Application("Mail"); Reflect.apply(m["se" + "nd"], m, [])',
    'var m = Application("Mail"); new Proxy(m, {}).sen\\u0064()',
    'Application("System Events").processes.byName("Messages").windows[0].buttons[0].actions[0].perform()',
    'var se = Application("System Events"); const k = se.keyCode; k.call(se, 36); Application("Messages")',
    'var se = Application("System Events"); Application("Mail"); se.keystroke.apply(se, ["\\r"])',
    'var m = Application("Mail"); var go = m.reply.bind(m); go(1)',
    # A bracket is read where the comments and strings are left out, which a script cannot use to hide one: a comment
    # that ends in an array literal's character, a line break JavaScript ends a comment at, a quote in a regular
    # expression, an escape that writes a quote, an HTML-like comment.
    'var m = Application("Mail"); m //=\n["se" + "nd"]()',
    'var m = Application("Mail"); m //=\u2028["se" + "nd"]()',
    'var m = Application("Mail"); m /* ( */ ["se" + "nd"]()',
    "var m = Application('Mail'); var r = /'/; m['se' + 'nd'](); var t = 'x'",
    'var m = Application("Mail"); var x = "\\u0022"; m["se" + "nd"](); var y = "\\u0022"',
    'var m = Application("Mail"); m <!-- =\n["se" + "nd"]()',
    'var m = Application("Mail"); var d = 4 / 2; m["se" + "nd"]()',
    # A string JavaScript reads as one: it may hold U+2028, and go on past a line break (a carriage return and a line
    # feed are one).
    'var m = Application("Mail"); var s = "a\u2028b"; m["se" + "nd"]()',
    'var m = Application("Mail"); var s = "a\\\r\nb"; m["se" + "nd"]()',
    # AppleScriptObjC runs text as code and sends raw events (the review of item 561, second round): these ask whichever
    # application they drive, as ``run script`` does.
    _FOUNDATION
    + "set q to quote\n"
    + 'set c to current application\'s NSClassFromString("NSApple" & "Script")\n'
    + 'set s to c\'s alloc()\'s initWithSource_("tell application " & q & "Mail" & q & " to se" & "nd m")\n'
    + "s's executeAndReturnError_(missing value)",
    _FOUNDATION
    + "set d to current application's NSAppleEventDescriptor's "
    + "appleEventWithEventClass_eventID_targetDescriptor_returnID_transactionID_("
    + '"emsg", "se" & "nd", missing value, -1, 0)\n'
    + "d's sendEventWithOptions_timeout_error_(0, 30, missing value)",
    _FOUNDATION
    + "set t to current application's NSTask's alloc()'s init()\nt's setLaunchPath_(\"/usr/bin/osascript\")",
    _FOUNDATION + "tell current application\nset t to NSTask's alloc()'s init()\nend tell",
    _FOUNDATION + "set p to current application's NSProcessInfo's processInfo()",
    "ObjC.import('Foundation'); $.NSTask.launchedTaskWithLaunchPath_arguments('/bin/ls', [])",
    _FOUNDATION
    + "set m to current application's NSString's stringWithString_(\"x\")\n"
    + 'm\'s performSelector_("se" & "nd")',
    "ObjC.import('Foundation'); $.NSClassFromString('NSApple' + 'Script').alloc.initWithSource($(src))",
    # The bridge asks when the script can reach Mail or Messages.
    _FOUNDATION + 'tell application "Mail" to get name of every account',
    "ObjC.import('Foundation'); Application('Messages').chats()",
    "var m = Application('Messages'); var c = ObjC.classes; m.chats()",
    "var m = Application('Messages'); ObjC['import']('Foundation'); m.chats()",
    # Mail's rules send later, when a message arrives.
    'tell application "Mail" to make new rule at end of rules with properties {name:"fw", forward message:"a@b.c"}',
    'tell application "Mail" to make new rule at end of rules with properties {name:"fw", redirect message:"a@b.c"}',
    'tell application "Mail" to make new rule at end of rules with properties {name:"re", reply text:"away"}',
    'Application("Mail").make({new: "rule", withProperties: {name: "fw", forwardMessage: "a@b.c"}})',
    # An application or a process named by anything but one string (the review of the second push): the name is built
    # as the script runs, so the script may drive Messages without a word of theirs.
    'tell application ("Mess" & "ages") to activate\ntell application "System Events" to keystroke return',
    'set a to "Mess" & "ages"\ntell application a to activate\ntell application "System Events" to keystroke return',
    'tell application id ("com.apple." & "MobileSMS") to activate\n'
    'tell application "System Events" to keystroke return',
    'tell application "System Events"\nset frontmost of process ("Mess" & "ages") to true\nkeystroke return\nend tell',
    'var se = Application("System Events"); se.processes.byName("Mess" + "ages").frontmost = true; se.keystroke("x")',
    # A key or a button pressed through System Events asks whatever application it is pressed in (the structural
    # option after the hook's third review): a filter, a built or a localized name, or the application in front, can
    # all be Messages, which no reading of the script tells.
    'tell application "System Events" to tell process "Finder" to click button 1 of window 1',
    'tell application "System Events" to click button 1 of window 1 of (first process whose name is "Mess" & "ages")',
    'tell application "System Events" to click button 1 of window 1 of (first process whose name starts with "Mes")',
    'Application("System Events").processes.whose({name: "Mess" + "ages"})[0].windows[0].buttons[0].click()',
    'tell application "System Events" to keystroke "c" using command down',
]
# The same applications read, and the scripts of others: nothing the guard cannot read, so no question.
_READING: list[str] = [
    'tell application "Mail" to get subject of messages of inbox',
    'tell application "Messages" to get name of every chat',
    'tell application id "com.apple.mail" to get name of every account',
    'Application("Mail").inbox.messages[0].subject()',
    'const M = Application("Mail"); const r = [M.name(), 1]; r',
    'const N = Application("Notes"); const n = N.notes(); n[i].name()',
    'tell application "Finder" to get name of every item of desktop',
    # The review of item 561, second round: what is not an array literal's bracket is a question, and an array literal's
    # is none (a script of another application with the bridge is high, but no message: below).
    'const M = Application("Mail"); const r = [M.name(), 1]; r',
    'const M = Application("Mail"); const f = (x) => [x, 1]; f(M.name())',
    'const M = Application("Mail"); const o = {names: [M.name(), "a"]}; o',
    'const M = Application("Mail"); M.accounts[0].name() // [x] in a comment',
    'const M = Application("Mail"); const s = "[x] in a string"; M.name()',
    # A filter of processes, and the current application, with nothing hidden: no question.
    'tell application "System Events" to get name of every application process whose visible is true',
    'tell current application to display dialog "the application is done"',
]


@pytest.mark.parametrize("script", _HIDING_A_SEND)
def test_a_script_that_may_send_through_words_the_guard_cannot_read_asks_as_a_message(script: str) -> None:
    """The guard reads a script's words: a script that says nothing it can read of what it sends is asked about as a
    send, whatever spelling it hides the verb in, in the tool of a run and in an osascript line alike."""
    assessment = assess_applescript(script, _HOME, _HOME, (), ())
    assert {"external_send", "message_send"} <= set(assessment.flags), assessment
    assert assessment.risk == "high"
    info: ActionInfo = ActionInfo(
        tool="apple_script",
        kind="app_control",
        risk=assessment.risk,
        summary=f"Run: {script}",
        target=script,
        flags=assessment.flags,
    )
    # Every preset asks, a rule that lets it go does not answer for the owner, and a taint changes nothing.
    for preset in ("aggressive", "balanced", "cautious"):
        for rules in ([], [_rule("allow"), _rule("preapproved")]):
            for tainted in (False, True):
                decision: Decision = decide(info, "task", tainted, preset, rules, None)  # type: ignore[arg-type]
                assert decision.verdict == "ask", (preset, rules, tainted, decision)
                assert decision.source in ("guard", "preset"), decision
    assert decide(info, "task", False, "aggressive", [_rule("allow")], None).source == "guard"
    quoted: str = script.replace("'", "'\\''")
    in_a_line = assess_shell(f"osascript -e '{quoted}'", _HOME, _HOME, (), ())
    assert {"external_send", "message_send"} <= set(in_a_line.flags), in_a_line


@pytest.mark.parametrize("script", _READING)
def test_a_plain_read_of_mail_and_messages_goes_on_without_a_question(script: str) -> None:
    assessment = assess_applescript(script, _HOME, _HOME, (), ())
    assert "message_send" not in assessment.flags and assessment.risk == "medium", assessment
    info: ActionInfo = ActionInfo(
        tool="apple_script",
        kind="app_control",
        risk=assessment.risk,
        summary=f"Run: {script}",
        target=script,
        flags=assessment.flags,
    )
    assert decide(info, "task", False, "aggressive", [], None).verdict == "allow"


@pytest.mark.parametrize(
    "script",
    [
        'use framework "Foundation"\nset s to current application\'s NSString\'s stringWithString_("x")',
        "ObjC.import('Foundation'); $.NSString.stringWithString('x')",
    ],
)
def test_a_script_of_the_bridge_that_reaches_neither_mail_nor_messages_is_no_message(script: str) -> None:
    """The bridge is how a script calls the system by any name, so it is high risk, but only a script that can reach the
    two applications may send through it."""
    assessment = assess_applescript(script, _HOME, _HOME, (), ())
    assert "message_send" not in assessment.flags and assessment.risk == "high", assessment


@pytest.mark.parametrize("line", ["osascript send.scpt", "osascript -l JavaScript send.js", "osascript"])
def test_a_script_file_the_guard_cannot_read_asks_as_a_message_in_every_preset(line: str) -> None:
    """A script in a file says nothing of what it sends (item 561): it was high risk with no flag, which the aggressive
    preset let through."""
    assessment = assess_shell(line, _HOME, _HOME, (), ())
    assert {"external_send", "message_send"} <= set(assessment.flags), assessment
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags
    )
    for preset in ("aggressive", "balanced", "cautious"):
        assert decide(info, "task", False, preset, [], None).verdict == "ask", preset  # type: ignore[arg-type]


def _asks_in_every_preset(info: ActionInfo) -> None:
    """Every preset asks, a rule that lets it go does not answer for the owner, and a taint changes nothing."""
    for preset in ("aggressive", "balanced", "cautious"):
        for rules in ([], [_rule("allow"), _rule("preapproved")]):
            for tainted in (False, True):
                decision: Decision = decide(info, "task", tainted, preset, rules, None)  # type: ignore[arg-type]
                assert (decision.verdict, decision.source) == ("ask", "guard"), (preset, rules, tainted, decision)


# What puts the text of an osascript line together as the line runs (the review of item 561, second round): the guard
# reads a line's words, and the script's are not among them, so each asks as a script that may send, as a file does.
_BUILT_BY_THE_SHELL: list[str] = [
    'S=\'tell application "Messages" to send "hi" to buddy "x"\'; osascript -e "$S"',
    'osascript -e "tell application \\"Messages\\" to $(printf se)nd \\"hi\\" to buddy \\"x\\""',
    'osascript -e "tell application \\"Messages\\" to `printf se`nd"',
    'V=send\nosascript <<EOF\ntell application "Messages" to $V "hi" to buddy "x"\nEOF',
    'V=send; osascript <<< "tell application \\"Messages\\" to $V \\"hi\\" to buddy \\"x\\""',
    'echo "$S" | osascript',
    "osascript -e \"$S\" -e 'return 1'",
    'osascript -l JavaScript -e "$S"',
    'env osascript -e "$S"',
    "bash -c 'osascript -e \"$S\"'",
    'printf %s "$S" | xargs -0 osascript -e',
    'echo \'tell application "Messages" to send "hi"\' | xargs -0 osascript -e',
    "cat note.txt | xargs -I{} osascript -e {}",
    "cat note.txt | xargs -0 env osascript -e",
    'find . -name "*.txt" -exec osascript -e {} \\;',
    "fd txt -x osascript -e {}",
]
# And the lines that show the script they run, or hand osascript words that are its arguments, not its script.
_SHOWING_ITS_SCRIPT: list[str] = [
    'osascript -e \'display notification "done" with title "Dot"\'',
    "osascript -l JavaScript -e 'ObjC.import(\"Cocoa\"); $.NSBeep()'",
    "osascript -l JavaScript -e 'Application(\"Mail\").inbox.messages[0].subject()'",
    "osascript -e 'on run argv' -e 'return item 1 of argv' \"$S\"",
    "osascript -e 'tell application \"Mail\" to get name of every account'",
    "ls | xargs osascript -e 'tell application \"Finder\" to activate'",
    "find . -name x -exec osascript -e 'display notification \"hi\"' \\;",
]


@pytest.mark.parametrize("line", _BUILT_BY_THE_SHELL)
def test_an_osascript_line_whose_script_the_shell_puts_together_asks_as_a_message_in_every_preset(line: str) -> None:
    assessment = assess_shell(line, _HOME, _HOME, (), ())
    assert {"external_send", "message_send"} <= set(assessment.flags), assessment
    assert assessment.risk == "high"
    _asks_in_every_preset(
        ActionInfo(tool="shell", kind="shell", risk=assessment.risk, summary=line, target=line, flags=assessment.flags)
    )


@pytest.mark.parametrize("line", _SHOWING_ITS_SCRIPT)
def test_an_osascript_line_that_shows_its_script_is_read_as_it_is_written(line: str) -> None:
    assessment = assess_shell(line, _HOME, _HOME, (), ())
    assert "message_send" not in assessment.flags, assessment


# A script that runs a shell line it builds as it runs (the review of item 561, second round): ``do shell script``
# with anything but literals, JavaScript's ``doShellScript`` and the line a script types into a terminal. The line may
# be ``osascript`` with a script that sends, so it is code the guard cannot read, whatever the preset.
_BUILDING_A_SHELL_LINE: list[str] = [
    'set q to quote\nset v to "se" & "nd"\ndo shell script "osascript -e " & quoted form of ("tell application " & q & '
    '"Messages" & q & " to " & v & " " & q & "hi" & q & " to buddy " & q & "x" & q)',
    "do shell script cmd",
    'do shell script "cat " & quoted form of f',
    "var a = Application.currentApplication(); a.includeStandardAdditions = true; a.doShellScript('osascript -e ' + c)",
    'tell application "Terminal" to do script "osascript -e " & quoted form of t',
    'Application("Terminal").doScript("osascript -e " + t)',
]


@pytest.mark.parametrize("script", _BUILDING_A_SHELL_LINE)
def test_a_script_that_runs_a_shell_line_it_builds_is_code_the_guard_cannot_read(script: str) -> None:
    assessment = assess_applescript(script, _HOME, _HOME, (), ())
    assert {"destructive", "outside_workspace", "external_send", "message_send"} <= set(assessment.flags), assessment
    assert assessment.risk == "high"
    _asks_in_every_preset(
        ActionInfo(
            tool="apple_script",
            kind="shell",
            risk=assessment.risk,
            summary=f"Run: {script}",
            target=script,
            flags=assessment.flags,
        )
    )


@pytest.mark.parametrize(
    "script",
    [
        'do shell script "ls"',
        'do shell script "ls " & "-la"',
        'tell application "Terminal" to do script "ls"',
        "var a = Application.currentApplication(); a.includeStandardAdditions = true; a.doShellScript('ls')",
    ],
)
def test_a_script_that_runs_a_shell_line_it_shows_is_judged_by_the_line(script: str) -> None:
    assessment = assess_applescript(script, _HOME, _HOME, (), ())
    assert "message_send" not in assessment.flags and assessment.risk == "medium", assessment


def test_a_name_javascript_writes_with_escapes_is_read_as_the_letters_it_stands_for() -> None:
    """JXA's escapes were read for the clipboard's names alone: ``m["s\\x65nd"]`` is ``m["send"]`` to the guard too."""
    for script in ('m["s\\x65nd"]("hi")', 'm["\\u0073end"]("hi")', 'm["\\u{73}end"]("hi")'):
        assert "message_send" in assess_applescript(script, _HOME, _HOME, (), ()).flags, script

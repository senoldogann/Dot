"""The cases of the eval of tool use (item 736), each from a failure the owner's runs met between 2026-10-13 and
2026-10-27 (`failure` says which), told with made-up pages and files so that nothing of theirs is in it."""

from dotagent.core.outcomes import OutcomeCheck
from dotagent.desktop.render import render_act_outcome
from dotagent.desktop.types import ActOutcome, AppView, SnapshotView, StepReport
from dotagent.evals.toolcalls import Expected, PriorStep, ToolCase
from dotagent.tools.web import NEXT_RUNG as REFUSED_NEXT

_PAGE_OPENED: str = (
    "Tab 1 of 1 · HTTP 200 · full view · https://shop.example/orders/4411\n"
    "<web_content>\n# Order 4411\nStatus: Confirmed\nItems: 2 × oat milk, 1 × bread\nTotal: 7.40 EUR\n"
    "[e1] link 'Back to orders'\n</web_content>"
)
_TODO: str = "1\t# To do\n2\t- buy milk\n3\t- call the bank\n"
_NOT_FOUND: str = (
    'the text to replace was not found; re-read the file and copy it exactly | context={"path": "notes/todo.md"}'
)

# Safari as desktop_open_app returns it: its window, its tab bar and the page as one web area (item 1960).
_SAFARI_OPENED: str = (
    "Snapshot · pid 4411 · full view · 25 elements\n"
    '<screen_content nonce="5f0c" source="Safari">\n'
    'view: window "Account - Developer"\n'
    '[a1] window "Account - Developer" @695,429 1343x795\n'
    "  [a2] tabs @695,429 1343x795\n"
    '    [a3] button "Back" @720,150 28x28\n'
    '    [a4] textfield "Address" "developer.example.invalid/account" @1100,150 500x28\n'
    '    [a26] webarea "Account - Developer" @695,520 1343x700\n'
    '      [a27] link "Certificates, Identifiers & Profiles" @800,400 260x20\n'
    '</screen_content nonce="5f0c">'
)
# A batch whose last press the window shows done, as desktop_act renders it: the result's view holds the change
# (item 1960).
_REGISTERED: str = render_act_outcome(
    ActOutcome(
        steps=(
            StepReport(
                index=1,
                description="set_value a12 'Domain' (11 chars)",
                outcome="done",
                detail="set the value",
                dispatched="sent",
                checks=(
                    OutcomeCheck(
                        condition="the field holds the text written",
                        status="verified",
                        evidence="read back",
                        reason="",
                    ),
                ),
            ),
            StepReport(
                index=2,
                description="press a15 'Register'",
                outcome="done",
                detail='pressed button "Register" a15',
                dispatched="sent",
            ),
        ),
        stopped=None,
        view=SnapshotView(
            app=AppView(pid=5120, name="Mail Sources", bundle_id="com.example.mailsources", frontmost=True, windows=()),
            heading='window "Mail Sources"',
            mode="delta",
            body=(
                '  [a20] statictext "example.org" @420,310 120x18\n'
                '  [a21] statictext "Registered · SPF verified" @560,310 180x18\n'
                '  [a22] button "Add another source" @420,360 160x24'
            ),
            nodes=41,
            shown=3,
            omitted=0,
            removed=2,
            truncated=False,
            notes=(),
        ),
        view_note=None,
    ),
    "Mail Sources",
    "9a1e",
)

CASES: tuple[ToolCase, ...] = (
    ToolCase(
        id="read-what-the-page-shows",
        failure="browser_wait_text failed 72 of 237 calls: it waited for text the page never showed",
        goal="Open my order 4411 on shop.example and tell me whether it is confirmed.",
        prior=(
            PriorStep(tool="browser_open", arguments={"url": "https://shop.example/orders/4411"}, result=_PAGE_OPENED),
        ),
        expect=Expected(
            tools=("finish", "browser_read_text", "browser_snapshot", "browser_find"), forbid=("browser_wait_text",)
        ),
    ),
    ToolCase(
        id="edit-with-dots-own-arguments",
        failure="edit_file was called with file_path and new_string, the names of another agent's tool",
        goal="In notes/todo.md, change 'buy milk' to 'buy oat milk'.",
        prior=(PriorStep(tool="read_file", arguments={"path": "notes/todo.md"}, result=_TODO),),
        expect=Expected(
            tools=("edit_file",), present=("path", "old", "new"), match={"old": r"buy milk", "new": r"oat milk"}
        ),
    ),
    ToolCase(
        id="read-again-after-a-missed-edit",
        failure="edit_file failed 12 times in two weeks with 'the text to replace was not found'",
        goal="In notes/todo.md, change 'call the bank' to 'call the bank about the card'.",
        prior=(
            PriorStep(
                tool="edit_file",
                arguments={"path": "notes/todo.md", "old": "- Call the bank", "new": "- call the bank about the card"},
                result=_NOT_FOUND,
            ),
        ),
        expect=Expected(tools=("read_file",), present=("path",), forbid=("edit_file",)),
    ),
    ToolCase(
        id="search-before-guessing-an-address",
        failure="web_fetch got HTTP 404 from guessed addresses (a newsletter's issue, a catalogue page) 33 times",
        goal="Find the TLDR AI newsletter issue of 24 October 2026 and tell me its first story.",
        expect=Expected(tools=("web_search",), present=("query",), forbid=("web_fetch",)),
    ),
    ToolCase(
        id="another-way-after-a-refusal",
        failure="web_fetch got HTTP 403 from reddit.com 17 times, asked again the same way",
        goal="Read the top comments of https://www.reddit.com/r/appdev/comments/1rc8pqf and summarise them.",
        prior=(
            PriorStep(
                tool="web_fetch",
                arguments={"url": "https://www.reddit.com/r/appdev/comments/1rc8pqf.json"},
                # As Dot says it: the plain request and its quiet read in the browser both refused (item 98).
                result=(
                    "web_fetch got HTTP 403 from https://www.reddit.com/r/appdev/comments/1rc8pqf.json in Dot's "
                    f"browser too (the plain request got HTTP 403).\n{REFUSED_NEXT}"
                ),
            ),
        ),
        expect=Expected(tools=("browser_open", "web_search", "web_extract", "web_crawl"), forbid=("web_fetch",)),
    ),
    ToolCase(
        id="long-work-in-a-terminal",
        failure="shell commands were killed at their 120 s limit (installs, test suites, deploys)",
        goal="Run the whole test suite of ~/code/shop (it takes about ten minutes) and tell me what failed.",
        prior=(
            PriorStep(
                tool="shell",
                arguments={"command": "cd ~/code/shop && cat package.json"},
                result='[exit 0 in 0.1s]\n{"name": "shop", "scripts": {"test": "jest --runInBand"}}',
            ),
        ),
        expect=Expected(
            tools=("terminal_open", "shell"), match={"command": r"test|jest"}, present_for={"shell": ("timeout_s",)}
        ),
    ),
    ToolCase(
        id="read-a-page-whose-scripts-refuse",
        failure="browser_eval threw on webmail and job application pages 40 times",
        goal="Tell me the subject of the newest mail in the inbox that is open in the browser.",
        prior=(
            PriorStep(
                tool="browser_eval",
                arguments={"script": "document.querySelector('.mail-row').innerText"},
                result="The script threw an error after 0.1 s: TypeError: null is not an object",
            ),
        ),
        expect=Expected(tools=("browser_read_text", "browser_snapshot", "browser_find"), forbid=("browser_eval",)),
    ),
    ToolCase(
        id="safari-page-by-its-elements",
        failure=(
            "a run drove a signed-in Safari with 28 desktop_act calls, 16 screenshots and 10 snapshots, a look after "
            "nearly every act (2026-11-26, the Apple Developer run)"
        ),
        goal=(
            "In Safari, where I am signed in, open https://developer.example.invalid/account/services and press "
            "Configure under Email Communication."
        ),
        prior=(PriorStep(tool="desktop_open_app", arguments={"app": "Safari"}, result=_SAFARI_OPENED),),
        expect=Expected(
            tools=("safari_open", "safari_read"), forbid=("desktop_screenshot", "desktop_snapshot", "desktop_act")
        ),
        desktop=True,
    ),
    ToolCase(
        id="no-look-after-a-change-the-result-shows",
        failure=(
            "12 of 16 desktop_screenshot calls of one run came right after a desktop_act whose result already showed "
            "the window's change (2026-11-26, the Apple Developer run)"
        ),
        goal="In the Mail Sources app on my Mac, register example.org as a source and tell me once it is registered.",
        prior=(
            PriorStep(
                tool="desktop_act",
                arguments={
                    "app": "Mail Sources",
                    "steps": [
                        {"action": "set_value", "ref": "a12", "text": "example.org"},
                        {"action": "press", "ref": "a15"},
                    ],
                },
                result=_REGISTERED,
            ),
        ),
        expect=Expected(tools=("finish",), forbid=("desktop_screenshot", "desktop_snapshot")),
        desktop=True,
    ),
    ToolCase(
        id="search-code-with-the-search-tool",
        failure="shell grep and find lines whose glob zsh expanded to nothing aborted the whole command",
        goal="Which Python files under ~/code/shop mention TODO?",
        expect=Expected(tools=("search_files", "find_files"), present=("pattern",)),
    ),
)

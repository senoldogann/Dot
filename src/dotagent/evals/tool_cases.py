"""The cases of the eval of tool use (item 736), each from a failure the owner's runs met between 2026-10-13 and
2026-10-27 (`failure` says which), told with made-up pages and files so that nothing of theirs is in it."""

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
        id="search-code-with-the-search-tool",
        failure="shell grep and find lines whose glob zsh expanded to nothing aborted the whole command",
        goal="Which Python files under ~/code/shop mention TODO?",
        expect=Expected(tools=("search_files", "find_files"), present=("pattern",)),
    ),
)

"""The speed set (items 1037 and 1040): five browser tasks on a local site, with no network and no account, that
measure how long Dot's browser use takes to finish the kind of work the owner gives it, not whether it can: a form over
several pages, a search and the result it opens, a long list read to the end, a daily check over a few pages, and a job
application in a dialog whose steps show one after the other. Each is ordinary work a person does in a browser, its
answer judged by what the pages say; `dotx eval speed` plays them and prints, per task, what a change to Dot's browser
use is measured against (docs/development.md).

The pages are plain HTML whose ``{name}`` placeholders the site fills from the query string
(:mod:`dotagent.evals.site`), so a form submitted with GET carries what was typed to the next page. No page holds a
brace of its own."""

from dotagent.evals.types import (
    AnswerContains,
    AnswerLacks,
    AnswerMatches,
    Check,
    EvalTask,
    ScriptedCall,
    ScriptedTurn,
    SitePage,
)
from dotagent.tools.meta import FINISH_TOOL

# The task ids of the speed set, in the order they are played and reported.
SPEED_TASKS: tuple[str, ...] = (
    "speed-signup-wizard",
    "speed-shop-search",
    "speed-stock-list",
    "speed-daily-check",
    "speed-apply-dialog",
)
# Generous for the slowest model the owner runs: a run past it is cut and counts as failed, which the table shows.
SPEED_TIME_LIMIT_S: float = 420.0
SPEED_TOKEN_BUDGET: int = 900_000


def _page(path: str, body: str) -> SitePage:
    return SitePage(path=path, status=200, body=body, location=None)


def _says(text: str) -> AnswerContains:
    return AnswerContains(kind="answer_contains", text=text)


def _matches(pattern: str) -> AnswerMatches:
    return AnswerMatches(kind="answer_matches", pattern=pattern)


def _open(url: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="browser_open", arguments={"url": url}),))


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


def _task(
    task_id: str, goal: str, site: tuple[SitePage, ...], checks: tuple[Check, ...], reference: tuple[ScriptedTurn, ...]
) -> EvalTask:
    """A browser task of the speed set: a task run, ended done, the workspace empty, judged by the answer."""
    return EvalTask(
        id=task_id,
        category="browser",
        goal=goal,
        mode="task",
        files=(),
        checks=checks,
        injection_checks=(),
        ending="done",
        reference=reference,
        time_limit_s=SPEED_TIME_LIMIT_S,
        token_budget=SPEED_TOKEN_BUDGET,
        site=site,
        goal_mode=False,
        skills=(),
        servers=(),
        easy_way=None,
        grouped=False,
    )


def _html(title: str, *body: str) -> str:
    return f"<!doctype html><html><head><title>{title}</title></head><body>{''.join(body)}</body></html>"


# A sign-up over three pages and a confirmation: the account, the plan, the terms. Each page carries what the ones
# before it took as hidden fields of its GET form.
_SIGNUP: tuple[SitePage, ...] = (
    _page(
        "/signup",
        _html(
            "Sign up: account",
            "<h1>Create your account</h1><p>Step 1 of 3</p>",
            '<form action="/signup/plan" method="get">',
            '<label>Full name <input name="name" required></label>',
            '<label>Email <input name="email" type="email" required></label>',
            '<label>City <input name="city"></label>',
            '<button type="submit">Continue</button></form>',
        ),
    ),
    _page(
        "/signup/plan",
        _html(
            "Sign up: plan",
            "<h1>Choose a plan</h1><p>Step 2 of 3, for {name}</p>",
            '<form action="/signup/terms" method="get">',
            '<input type="hidden" name="name" value="{name}"><input type="hidden" name="email" value="{email}">',
            '<input type="hidden" name="city" value="{city}">',
            "<fieldset><legend>Plan</legend>",
            '<label><input type="radio" name="plan" value="basic" checked> Basic, free</label>',
            '<label><input type="radio" name="plan" value="pro"> Pro, 9 euros a month</label>',
            '<label><input type="radio" name="plan" value="team"> Team, 29 euros a month</label></fieldset>',
            '<label>Billing <select name="billing"><option value="monthly">Monthly</option>',
            '<option value="yearly">Yearly</option></select></label>',
            '<button type="submit">Continue</button></form>',
        ),
    ),
    _page(
        "/signup/terms",
        _html(
            "Sign up: terms",
            "<h1>Terms</h1><p>Step 3 of 3: {name}, the {plan} plan, billed {billing}</p>",
            '<form action="/signup/done" method="get">',
            '<input type="hidden" name="name" value="{name}"><input type="hidden" name="plan" value="{plan}">',
            '<input type="hidden" name="billing" value="{billing}"><input type="hidden" name="email" value="{email}">',
            '<input type="hidden" name="city" value="{city}">',
            '<label><input type="checkbox" name="terms" value="yes"> I accept the terms</label>',
            '<label><input type="checkbox" name="news" value="yes"> Send me the newsletter</label>',
            '<button type="submit">Create account</button></form>',
        ),
    ),
    _page(
        "/signup/done",
        _html(
            "Account created",
            "<h1>Account ready</h1><p id='done'>Account ready: {name} on the {plan} plan, billed {billing}, "
            "terms {terms}.</p><p>Email: {email}. City: {city}. Newsletter: {news}.</p>",
        ),
    ),
)

# A shop: a search box, a page of results, and the page of each result. Only one of the lamps is the brass desk lamp.
_PRODUCTS: tuple[tuple[str, str, str, str], ...] = (
    ("1", "Brass floor lamp", "149.00", "BF-2010"),
    ("2", "Steel desk lamp", "39.90", "SD-3302"),
    ("3", "Brass desk lamp", "84.50", "BD-4410"),
    ("4", "Brass desk lamp shade", "19.00", "BS-4411"),
    ("5", "Clip-on reading lamp", "24.00", "CR-1200"),
    ("6", "Brass wall lamp", "112.00", "BW-5005"),
    ("7", "Paper lantern lamp", "29.50", "PL-0707"),
    ("8", "Oak desk lamp", "74.00", "OD-4412"),
)
_SHOP: tuple[SitePage, ...] = (
    _page(
        "/shop",
        _html(
            "Lamp shop",
            "<h1>Lamp shop</h1>",
            '<form action="/shop/search" method="get"><label>Search <input name="q"></label>',
            '<button type="submit">Search</button></form>',
            "<p>Free delivery on orders over 50 euros.</p>",
        ),
    ),
    _page(
        "/shop/search",
        _html(
            "Search results",
            "<h1>Results for {q}</h1><ul>",
            *(f'<li><a href="/shop/item/{number}">{name}</a></li>' for number, name, _, _ in _PRODUCTS),
            "</ul>",
        ),
    ),
    *(
        _page(
            f"/shop/item/{number}",
            _html(
                name,
                f"<h1>{name}</h1><p>Price: {price} euros</p><p>SKU: {sku}</p>",
                "<p>In stock. Ships in two days.</p><a href='/shop'>Back to the shop</a>",
            ),
        )
        for number, name, price, sku in _PRODUCTS
    ),
)

# A stock list of 120 products in four categories: every seventh is out of stock.
_CATEGORIES: tuple[str, ...] = ("Garden", "Kitchen", "Office", "Bath")
_STOCK: tuple[tuple[str, str, int], ...] = tuple(
    (f"Item {number:03d}", _CATEGORIES[number % 4], 0 if number % 7 == 0 else (number * 13) % 40 + 1)
    for number in range(1, 121)
)
_OUT: tuple[tuple[str, str, int], ...] = tuple(row for row in _STOCK if row[2] == 0)
_GARDEN_OUT: tuple[str, ...] = tuple(name for name, category, _ in _OUT if category == "Garden")
_STOCK_PAGE: SitePage = _page(
    "/stock",
    _html(
        "Stock list",
        "<h1>Stock list</h1><table><thead><tr><th>Product</th><th>Category</th><th>In stock</th></tr></thead><tbody>",
        *(f"<tr><td>{name}</td><td>{category}</td><td>{count}</td></tr>" for name, category, count in _STOCK),
        "</tbody></table>",
    ),
)

# A daily check: a status page that links to the open alerts and to today's orders.
_ORDERS: tuple[tuple[str, str, str], ...] = (
    ("A-1041", "07:12", "shipped"),
    ("A-1042", "07:55", "pending"),
    ("A-1043", "08:30", "shipped"),
    ("A-1044", "09:05", "pending"),
    ("A-1045", "10:40", "cancelled"),
    ("A-1046", "11:15", "pending"),
    ("A-1047", "12:02", "shipped"),
)
_ALERTS: tuple[tuple[str, str], ...] = (
    ("Payment provider slow", "open"),
    ("Disk 80 percent full on the web server", "open"),
    ("Newsletter bounced for 3 addresses", "closed"),
)
_DAILY: tuple[SitePage, ...] = (
    _page(
        "/status",
        _html(
            "Shop status",
            "<h1>Shop status</h1><p>All services answer.</p>",
            '<nav><a href="/alerts">Alerts</a> <a href="/orders?day=yesterday">Yesterday\'s orders</a> ',
            '<a href="/orders?day=today">Today\'s orders</a></nav>',
        ),
    ),
    _page(
        "/alerts",
        _html(
            "Alerts",
            "<h1>Alerts</h1><table><tr><th>Alert</th><th>State</th></tr>",
            *(f"<tr><td>{title}</td><td>{state}</td></tr>" for title, state in _ALERTS),
            "</table>",
        ),
    ),
    _page(
        "/orders",
        _html(
            "Orders",
            "<h1>Orders of {day}</h1><table><tr><th>Order</th><th>Time</th><th>State</th></tr>",
            *(f"<tr><td>{order}</td><td>{at}</td><td>{state}</td></tr>" for order, at, state in _ORDERS),
            "</table>",
        ),
    ),
)


# A job page whose Easy apply opens a dialog of three steps that replace one another in place, as a job board's does:
# contact, questions, review; the last says the application's number (item 1040). The page never navigates. The number
# is made from what the steps took (7700, the email's length, ten times the years' choice), so that no source of the
# page holds it before the application is sent with the answers the goal asks for.
_APPLY_EMAIL: str = "ada@example.org"
_APPLY_ID: str = str(7700 + len(_APPLY_EMAIL) + 10 * 1)
# No brace: the site fills the page's placeholders (item 1037). Each step is a part of the dialog that the one before it
# shows: hidden, it is not in the page's accessibility tree, as a job board's next step is not there yet.
_GO: str = (
    "window.go = (n) => document.querySelectorAll('[data-step]')"
    ".forEach((part) => part.hidden = part.dataset.step != n)"
)
_APPLY: tuple[SitePage, ...] = (
    _page(
        "/jobs/42",
        _html(
            "Backend developer at Acme",
            "<h1>Backend developer</h1><p>Acme, Oulu. Python, Postgres.</p>",
            "<button onclick=\"document.getElementById('apply').hidden = false\">Easy apply</button>",
            '<div role="dialog" aria-label="Apply to Acme" id="apply" hidden>',
            '<div data-step="1"><h2>Contact</h2><label>Email <input id="email"></label>',
            '<label>Phone <input id="phone"></label><button onclick="go(2)">Next</button></div>',
            '<div data-step="2" hidden><h2>Questions</h2><label>Years of experience <select id="years">',
            "<option>0-2</option><option>3-5</option><option>6 or more</option></select></label>",
            '<label><input type="checkbox" id="fi"> I can work in Finland</label>',
            "<button onclick=\"document.getElementById('sum').textContent = email.value + ', ' + years.value + "
            "' years, Finland: ' + fi.checked, go(3)\">Review</button></div>",
            '<div data-step="3" hidden><h2>Review</h2><p id="sum"></p>',
            "<button onclick=\"document.getElementById('sent').textContent = 'Application ' + "
            "(7700 + email.value.length + 10 * years.selectedIndex) + ' sent for ' + email.value + ' (' + "
            "years.value + ' years, Finland: ' + fi.checked + ').', go(4)\">Submit application</button></div>",
            '<div data-step="4" hidden><p id="sent"></p></div></div>',
            f"<script>{_GO}</script>",
        ),
    ),
)


def speed_tasks() -> tuple[EvalTask, ...]:
    """The five tasks of the speed set, in :data:`SPEED_TASKS`'s order."""
    pending: tuple[str, ...] = tuple(order for order, _, state in _ORDERS if state == "pending")
    return (
        _task(
            "speed-signup-wizard",
            "Open {site}/signup in the browser and create an account: full name Ada Lovelace, email ada@example.org, "
            "city London; the Pro plan billed yearly; accept the terms, and leave the newsletter unticked. Report the "
            "line the last page shows.",
            _SIGNUP,
            (
                _says("Account ready: Ada Lovelace on the pro plan, billed yearly, terms yes"),
                _says("ada@example.org"),
                _says("London"),
                AnswerLacks(kind="answer_lacks", text="Newsletter: yes"),
            ),
            (
                _open(
                    "{site}/signup/done?name=Ada%20Lovelace&plan=pro&billing=yearly&terms=yes"
                    "&email=ada%40example.org&city=London",
                ),
                _finish(
                    "Account ready: Ada Lovelace on the pro plan, billed yearly, terms yes. Email: ada@example.org. "
                    "City: London. Newsletter: ."
                ),
            ),
        ),
        _task(
            "speed-shop-search",
            "In the browser, search {site}/shop for lamps, open the brass desk lamp (not its shade) and report its "
            "price and its SKU.",
            _SHOP,
            (_says("84.50"), _says("BD-4410")),
            (_open("{site}/shop/item/3"), _finish("The brass desk lamp costs 84.50 euros; its SKU is BD-4410.")),
        ),
        _task(
            "speed-stock-list",
            "Open {site}/stock in the browser and read the whole list. Report how many products are out of stock "
            "(0 in stock), and name the out-of-stock products of the Garden category.",
            (_STOCK_PAGE,),
            (_matches(rf"\b{len(_OUT)}\b"), *(_says(name) for name in _GARDEN_OUT)),
            (
                _open("{site}/stock"),
                _finish(f"{len(_OUT)} products are out of stock; in Garden: {', '.join(_GARDEN_OUT)}."),
            ),
        ),
        _task(
            "speed-daily-check",
            "Do the shop's daily check in the browser, starting at {site}/status: report the title of each open "
            "alert, how many of today's orders are pending, and the id of the oldest pending order of today.",
            _DAILY,
            (
                *(_says(title) for title, state in _ALERTS if state == "open"),
                _matches(rf"\b{len(pending)}\b"),
                # An order's id as a model may write it, with a non-breaking hyphen (one attempt of 2026-11-07).
                _matches(pending[0].replace("-", "[-\u2010\u2011]")),
            ),
            (
                _open("{site}/alerts"),
                _open("{site}/orders?day=today"),
                _finish(
                    "Open alerts: "
                    + "; ".join(title for title, state in _ALERTS if state == "open")
                    + f". Today {len(pending)} orders are pending; the oldest is {pending[0]}."
                ),
            ),
        ),
        _task(
            "speed-apply-dialog",
            "Apply to the job at {site}/jobs/42 in the browser with its Easy apply: email ada@example.org, phone "
            "+358 40 123 4567, 3-5 years of experience, able to work in Finland; review and submit the application. "
            "Report the line the page shows once it is sent.",
            _APPLY,
            # The line only a submitted application shows, with what its steps chose: a run that skipped a choice or
            # never pressed Submit does not have it.
            (_says(f"Application {_APPLY_ID} sent for {_APPLY_EMAIL} (3-5 years, Finland: true)"),),
            (
                _open("{site}/jobs/42"),
                _finish(f"Application {_APPLY_ID} sent for {_APPLY_EMAIL} (3-5 years, Finland: true)."),
            ),
        ),
    )

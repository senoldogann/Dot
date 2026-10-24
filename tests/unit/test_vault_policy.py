"""What the policy and the tools make of Dot's own vault (item 621): the sites a login may be filled on and the origin a
value is scoped to; which tools of the vaults a run has; how the logins of 1Password and of the vault are one list; how
a contact and the billing address of a card are listed."""

import pytest

from dotagent.core.errors import ToolInputError
from dotagent.store.models import VaultRecord
from dotagent.tools.browser import LOGIN_TOOLS, VAULT_TOOLS, withheld_vault_tools
from dotagent.tools.contacts import CONTACTS_TOOL, _line
from dotagent.vault.choices import find_login, login_choices
from dotagent.vault.hosts import USERINFO_PROBLEM, SameOrigin, fills_on, site_label, website_hosts, website_problem
from dotagent.vault.onepassword import Login

UNREADABLE: str = "This does not look like the address of a site: write it like github.com."


@pytest.mark.parametrize(
    ("website", "problem"),
    [
        ("github.com", None),
        ("https://www.github.com/login", None),
        ("localhost:3000", None),
        ("http://127.0.0.1:8000/login", None),
        ("", "Write the site's address, like github.com."),
        ("not a site", "Write the site's address, like github.com."),
        ("ftp://files.example", "Dot fills logins on web pages: write the site's name, like github.com."),
        ("github", "This does not look like the address of a site: write it like github.com."),
        ("example.com:port", "The port in this address is not a number."),
        ("a" * 2001 + ".example", "This address is too long (2000 characters at most)."),
        # Python cannot read these, and what it says of them would quote the address, a password with it.
        ("http://[::1", UNREADABLE),
        ("https://[::1/login", UNREADABLE),
        ("http://\N{CARE OF}.com", UNREADABLE),
        ("https://user:hunter2-Zq9@host\N{CARE OF}.com/", UNREADABLE),
        # A user name or a password in the address is no part of a site: the login has boxes for them.
        ("https://admin:pw@192.168.1.1/", USERINFO_PROBLEM),
        ("admin:pw@192.168.1.1", USERINFO_PROBLEM),
        ("http://ada@github.com", USERINFO_PROBLEM),
        ("https://evil.example\\@github.com/", "Write the site's address, like github.com."),
        ("https://github.com/login?next=@x", None),
        ("https://github.com/@ada", None),
    ],
)
def test_a_website_of_a_login_is_a_site_a_page_can_be_of(website: str, problem: str | None) -> None:
    said: str | None = website_problem(website)
    assert said == problem
    assert said is None or ("hunter2" not in said and "admin" not in said)


def test_a_website_that_cannot_be_read_names_no_host_and_no_site_and_is_not_repeated() -> None:
    quoted: str = "https://user:hunter2-Zq9@host\N{CARE OF}.com/"
    assert website_hosts((quoted, "http://[::1", "github.com")) == ("github.com",)
    assert site_label(quoted) == "an unreadable address" == site_label("http://[::1")
    assert not fills_on("https://github.com/login", (quoted, "http://[::1"))
    assert not fills_on("http://[::1", ("github.com",))
    assert SameOrigin.of("https://[::1") is None and SameOrigin.of(quoted) is None


def test_a_login_is_named_by_its_host_and_a_port_that_is_not_its_schemes_own() -> None:
    assert site_label("https://www.GitHub.com/login") == "github.com"
    assert site_label("localhost:3000") == "localhost:3000"
    assert site_label("http://example.org:80") == "example.org"
    assert site_label("https://example.org:8443/a") == "example.org:8443"


@pytest.mark.parametrize(
    ("url", "same"),
    [
        ("https://shop.example.com/pay?x=1", True),
        ("https://SHOP.example.com/pay", True),
        ("https://shop.example.com./pay", False),
        ("https://Shop.Example.com./pay", False),
        ("https://shop.example.com:443/other#frag", True),
        ("https://user@shop.example.com/pay", True),
        ("https://shop.example.com:8443/pay", False),
        ("http://shop.example.com/pay", False),
        ("https://www.shop.example.com/pay", False),
        ("https://shop.example.com.evil.test/pay", False),
        ("https://example.com/pay", False),
        ("about:blank", False),
        ("blob:https://shop.example.com/3f2a", False),
        ("data:text/html,x", False),
        ("https://shop.example.com:port/pay", False),
        ("", False),
    ],
)
def test_the_documents_of_one_origin_are_those_of_its_scheme_host_and_port(url: str, same: bool) -> None:
    """A value filled into a field is scoped to the origin of the document that holds the field, not to every site its
    login names: the host of another origin, a port, a scheme or a subdomain is another document."""
    assert SameOrigin.of("https://shop.example.com/login")(url) is same


def test_a_host_with_a_dot_at_its_end_is_another_origin_as_it_is_to_chrome_and_the_same_site_to_the_site_rule() -> None:
    """Chrome keeps ``example.com.`` and ``example.com`` as two origins, so a value typed into one is scoped to that one
    and not to the other. Both are the one site the login names, where it may be filled."""
    dotted: SameOrigin | None = SameOrigin.of("https://shop.example.com./login")
    plain: SameOrigin | None = SameOrigin.of("https://shop.example.com/login")
    assert dotted is not None and plain is not None and dotted != plain
    assert dotted("https://shop.example.com./other") and not dotted("https://shop.example.com/other")
    assert plain("https://shop.example.com/other") and not plain("https://shop.example.com./other")
    assert fills_on("https://shop.example.com./login", ("shop.example.com",))
    assert fills_on("https://shop.example.com/login", ("shop.example.com",))


def test_an_origin_is_a_value_that_compares_and_is_none_for_what_is_no_origin() -> None:
    origin: SameOrigin | None = SameOrigin.of("https://shop.example.com/a")
    assert origin == SameOrigin.of("https://SHOP.example.com:443/b") and hash(origin) == hash(origin)
    assert origin != SameOrigin.of("https://shop.example.com:8443/a")
    assert SameOrigin.of("http://127.0.0.1:8000/x") == SameOrigin("http", "127.0.0.1", 8000)
    assert SameOrigin.of("about:blank") is None and SameOrigin.of("file:///tmp/a.html") is None
    assert SameOrigin.of("https://./x") is None and SameOrigin.of("https:///x") is None


def _record(kind: str, **fields: object) -> VaultRecord:
    return VaultRecord(id=f"vlt_{kind}", kind=kind, label=kind, created_at=0.0, updated_at=0.0, **fields)  # type: ignore[arg-type]


def test_a_run_has_the_tools_of_the_vaults_that_hold_something_to_fill() -> None:
    nothing: frozenset[str] = withheld_vault_tools([], False)
    assert nothing == LOGIN_TOOLS | {CONTACTS_TOOL} == VAULT_TOOLS
    # 1Password alone gives the logins; Dot's vault gives each its own.
    assert withheld_vault_tools([], True) == {CONTACTS_TOOL}
    assert withheld_vault_tools([_record("login", website="github.com")], False) == {CONTACTS_TOOL}
    # A card is kept, not filled: it gives a run no tool of its own.
    assert withheld_vault_tools([_record("card")], False) == VAULT_TOOLS
    assert withheld_vault_tools([_record("contact", fields={"name": "Ada"})], False) == LOGIN_TOOLS
    # A card with a billing address gives the contacts' tool its address.
    with_billing = _record("card", fields={"city": "Izmir"})
    assert withheld_vault_tools([with_billing], False) == LOGIN_TOOLS
    everything = [_record("login"), _record("card", fields={"city": "Izmir"}), _record("contact", fields={"name": "A"})]
    assert withheld_vault_tools(everything, True) == frozenset()


def test_the_logins_of_1password_and_of_the_vault_are_one_list_found_by_id_or_title() -> None:
    onepassword = [Login(id="op1", vault_id="v", title="GitHub", websites=("https://github.com",))]
    vault = [
        _record("login", website="github.com", username="ada@example.com").model_copy(
            update={"id": "vlt_a", "label": "github.com"}
        ),
        _record("login", website="github.com", username="bob@example.com").model_copy(
            update={"id": "vlt_b", "label": "github.com"}
        ),
        _record("card"),
    ]
    choices = login_choices(onepassword, vault)
    assert [(one.id, one.title, one.source) for one in choices] == [
        ("op1", "GitHub", "1password"),
        ("vlt_a", "github.com · ada@example.com", "dot"),
        ("vlt_b", "github.com · bob@example.com", "dot"),
    ]
    assert find_login(choices, "vlt_b").username == "bob@example.com"
    assert find_login(choices, "github").id == "op1"
    assert find_login(choices, "GITHUB.COM · ADA@EXAMPLE.COM").id == "vlt_a"
    with pytest.raises(ToolInputError, match="no such item"):
        find_login(choices, "nobody")
    twins = login_choices(
        [],
        [
            _record("login", website="a.example").model_copy(update={"id": f"vlt_{n}", "label": "a.example"})
            for n in (1, 2)
        ],
    )
    with pytest.raises(ToolInputError, match=r"2 logins are called 'a\.example': name one by its id"):
        find_login(twins, "a.example")


def test_a_contact_and_the_billing_address_of_a_card_are_listed_by_id_with_their_details_in_the_owners_words() -> None:
    contact = _record(
        "contact", fields={"city": "Izmir", "name": "Ada Lovelace", "address2": "Flat 4", "region": "Izmir"}
    )
    assert (
        _line(contact)
        == "- vlt_contact · contact: name Ada Lovelace; address line 2 Flat 4; city Izmir; state or region Izmir"
    )
    card = _record("card", fields={"address": "12 Main St"}).model_copy(update={"label": "Travel · Visa •••• 4242"})
    assert _line(card) == "- vlt_card · billing address of the card Travel · Visa •••• 4242: address 12 Main St"

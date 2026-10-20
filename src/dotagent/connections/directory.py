"""The Plugins page of Customize (item 316): every service Dot works with, one listing each, in the section the page
shows it in. A listing names the ways Dot reaches its service. Each tried way is an entry of the connections'
catalogues: an MCP service of ``mcp/catalogue.py``, or an application of the Mac, a synced folder or an application of
the web of ``connections/catalogue.py``; the page reads where it stands from ``GET /connections`` and ``GET
/mcp/servers`` and takes its step there (items 278 and 279). A way not tried yet has no entry and says what it would
take. A listing whose ways are all untried is shown as "Not tried yet" and offers no step: "Works" is written only of a
way that was tried.

A service's mark is its own logo, a file of ``static/icons/plugins`` (full colour, from gilbarbara/logos, CC0; the
wordmark-only brands keep Simple Icons' symbol in the brand's colour), drawn on a light tile in both colour schemes. An
application of the Mac wears the icon of the application the owner has installed, read from its bundle when the page is
drawn (``app``; ``connections/app_icons.py``), since Apple's and Microsoft's artwork is not Dot's to ship. A service
with no mark anywhere, or whose application is not installed and has no logo, is drawn with one of the interface's icons
(``glyph``) or a letter or two (``monogram``) in ``colour``. A brand's mark identifies its service, nothing more."""

from typing import Literal
from urllib.parse import urlsplit

from dotagent.connections.catalogue import FOLDER_APPS, MAC_APPS, WEB_APPS
from dotagent.core.types import FrozenModel
from dotagent.mcp.catalogue import MCP_SERVICES

type PluginSection = Literal["popular", "communication", "development", "business", "productivity", "mac"]
type WayKind = Literal["mcp", "mac", "folder", "web", "untried"]


class PluginWay(FrozenModel):
    """One way Dot reaches a service: ``ref`` is its entry's id in the catalogue of ``kind``; an untried way has none,
    and ``how`` says what it would take."""

    kind: WayKind
    ref: str | None
    how: str | None


class PluginListing(FrozenModel):
    """One service of the Plugins page. ``does`` says what Dot does with it once connected; ``None`` takes its first
    way's. Its mark is ``logo`` (a file of ``static/icons/plugins``), else ``glyph`` (an icon of the interface), else
    ``monogram``; ``colour`` is the hex of the colour a glyph or the letters are drawn in, without the ``#``, and
    ``None`` draws them in the ink. An application of the Mac wears its own icon when it is installed."""

    id: str
    name: str
    section: PluginSection
    does: str | None
    logo: str | None
    colour: str | None
    glyph: str | None
    monogram: str | None
    ways: tuple[PluginWay, ...]


class PluginWayView(FrozenModel):
    """A way as the page shows it: how it connects, where the service's data goes once it does (``None`` for an
    untried way) and what it lets Dot do (its catalogue entry's words; ``None`` for an untried way)."""

    kind: WayKind
    ref: str | None
    how: str
    data: str | None
    does: str | None


class PluginView(FrozenModel):
    """A listing as ``GET /plugins`` gives it: ``tried`` when one of its ways was tried. ``app`` is the id of the
    application of the Mac (a ``mac`` or ``folder`` way's ``ref``) whose own icon the mark wears once it is installed,
    which ``GET /connections`` tells (``icon``); ``None`` for a service with no such application."""

    id: str
    name: str
    section: PluginSection
    does: str
    logo: str | None
    colour: str | None
    glyph: str | None
    monogram: str | None
    app: str | None
    tried: bool
    ways: tuple[PluginWayView, ...]


def _mcp(ref: str) -> PluginWay:
    return PluginWay(kind="mcp", ref=ref, how=None)


def _mac(ref: str) -> PluginWay:
    return PluginWay(kind="mac", ref=ref, how=None)


def _web(ref: str) -> PluginWay:
    return PluginWay(kind="web", ref=ref, how=None)


def _untried(how: str) -> PluginWay:
    return PluginWay(kind="untried", ref=None, how=how)


# What an untried way of each sort would take, as the catalogue's docstring found on 2026-10-15.
_OWN_CLIENT: str = (
    "{name}'s MCP server signs in only a client you register with {name} yourself, which Dot cannot do yet."
)
_APPROVED_ONLY: str = (
    "{name}'s MCP server lets in approved clients alone, so Dot cannot connect to it; Dot can still work in {site} in "
    "its own browser once you sign in there."
)


def _own_client(name: str) -> PluginWay:
    return _untried(_OWN_CLIENT.format(name=name))


def _approved_only(name: str, site: str) -> PluginWay:
    return _untried(_APPROVED_ONLY.format(name=name, site=site))


PLUGINS: tuple[PluginListing, ...] = (
    PluginListing(
        id="google",
        name="Google",
        section="popular",
        does=None,
        logo="google-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_web("google"), _own_client("Google")),
    ),
    PluginListing(
        id="notion",
        name="Notion",
        section="popular",
        does=None,
        logo="notion-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("notion"), _web("notion")),
    ),
    PluginListing(
        id="github",
        name="GitHub",
        section="popular",
        does=None,
        logo="github-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("github"), _web("github")),
    ),
    PluginListing(
        id="slack",
        name="Slack",
        section="popular",
        does=None,
        logo="slack-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_web("slack"), _own_client("Slack")),
    ),
    PluginListing(
        id="linear",
        name="Linear",
        section="popular",
        does=None,
        logo="linear-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("linear"),),
    ),
    PluginListing(
        id="microsoft-365",
        name="Microsoft 365",
        section="popular",
        does=None,
        logo="microsoft-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_web("microsoft"),),
    ),
    PluginListing(
        id="mail",
        name="Mail",
        section="popular",
        does=None,
        logo=None,
        colour="1E7CF2",
        glyph="mail",
        monogram=None,
        ways=(_mac("mail"),),
    ),
    PluginListing(
        id="calendar",
        name="Calendar",
        section="popular",
        does=None,
        logo=None,
        colour="E5484D",
        glyph="calendar",
        monogram=None,
        ways=(_mac("calendar"),),
    ),
    PluginListing(
        id="linkedin",
        name="LinkedIn",
        section="communication",
        does=None,
        logo="linkedin-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_web("linkedin"),),
    ),
    PluginListing(
        id="x",
        name="X",
        section="communication",
        does=None,
        logo="x.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_web("x"),),
    ),
    PluginListing(
        id="instagram",
        name="Instagram",
        section="communication",
        does=None,
        logo="instagram-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_web("instagram"),),
    ),
    PluginListing(
        id="sentry",
        name="Sentry",
        section="development",
        does=None,
        logo="sentry-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("sentry"),),
    ),
    PluginListing(
        id="cloudflare",
        name="Cloudflare",
        section="development",
        does=None,
        logo="cloudflare-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("cloudflare"),),
    ),
    PluginListing(
        id="deepwiki",
        name="DeepWiki",
        section="development",
        does=None,
        logo=None,
        colour=None,
        glyph="search",
        monogram=None,
        ways=(_mcp("deepwiki"),),
    ),
    PluginListing(
        id="microsoft-learn",
        name="Microsoft Learn",
        section="development",
        does=None,
        logo="microsoft-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("microsoft-learn"),),
    ),
    PluginListing(
        id="vercel",
        name="Vercel",
        section="development",
        does="Look into your Vercel projects, deployments and their logs.",
        logo="vercel-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_approved_only("Vercel", "vercel.com"),),
    ),
    PluginListing(
        id="supabase",
        name="Supabase",
        section="development",
        does="Query and manage your Supabase projects, databases and tables.",
        logo="supabase-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("supabase"),),
    ),
    PluginListing(
        id="hugging-face",
        name="Hugging Face",
        section="development",
        does="Search models, datasets and Spaces on Hugging Face.",
        logo="hugging-face-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("huggingface"),),
    ),
    PluginListing(
        id="figma",
        name="Figma",
        section="development",
        does="Read your Figma files and their frames, components and comments.",
        logo="figma.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("figma"),),
    ),
    PluginListing(
        id="neon",
        name="Neon",
        section="development",
        does=None,
        logo="neon-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("neon"),),
    ),
    PluginListing(
        id="context7",
        name="Context7",
        section="development",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="C7",
        ways=(_mcp("context7"),),
    ),
    PluginListing(
        id="exa",
        name="Exa",
        section="development",
        does=None,
        logo="exa-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("exa"),),
    ),
    PluginListing(
        id="firecrawl",
        name="Firecrawl",
        section="development",
        does=None,
        logo="firecrawl-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("firecrawl"),),
    ),
    PluginListing(
        id="tavily",
        name="Tavily",
        section="development",
        does=None,
        logo="tavily.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("tavily"),),
    ),
    PluginListing(
        id="brave-search",
        name="Brave Search",
        section="development",
        does=None,
        logo="brave.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("brave-search"),),
    ),
    PluginListing(
        id="playwright",
        name="Playwright",
        section="development",
        does=None,
        logo="playwright.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("playwright"),),
    ),
    PluginListing(
        id="fetch",
        name="Fetch",
        section="development",
        does=None,
        logo="model-context-protocol-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("fetch"),),
    ),
    PluginListing(
        id="sequential-thinking",
        name="Sequential Thinking",
        section="development",
        does=None,
        logo="model-context-protocol-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("sequential-thinking"),),
    ),
    PluginListing(
        id="gitlab",
        name="GitLab",
        section="development",
        does=None,
        logo="gitlab-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("gitlab"),),
    ),
    PluginListing(
        id="posthog",
        name="PostHog",
        section="development",
        does=None,
        logo="posthog-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("posthog"),),
    ),
    PluginListing(
        id="postman",
        name="Postman",
        section="development",
        does=None,
        logo="postman-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("postman"),),
    ),
    PluginListing(
        id="netlify",
        name="Netlify",
        section="development",
        does=None,
        logo="netlify-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("netlify"),),
    ),
    PluginListing(
        id="heroku",
        name="Heroku",
        section="development",
        does=None,
        logo="heroku-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("heroku"),),
    ),
    PluginListing(
        id="grafana",
        name="Grafana",
        section="development",
        does=None,
        logo="grafana.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("grafana"),),
    ),
    PluginListing(
        id="chrome-devtools",
        name="Chrome DevTools",
        section="development",
        does=None,
        logo="chrome.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("chrome-devtools"),),
    ),
    PluginListing(
        id="aws-docs",
        name="AWS Documentation",
        section="development",
        does=None,
        logo="aws.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("aws-docs"),),
    ),
    PluginListing(
        id="cloudflare-docs",
        name="Cloudflare Docs",
        section="development",
        does=None,
        logo="cloudflare-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("cloudflare-docs"),),
    ),
    PluginListing(
        id="apify",
        name="Apify",
        section="development",
        does=None,
        logo="apify-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("apify"),),
    ),
    PluginListing(
        id="jina",
        name="Jina",
        section="development",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="J",
        ways=(_mcp("jina"),),
    ),
    PluginListing(
        id="mapbox",
        name="Mapbox",
        section="development",
        does=None,
        logo="mapbox-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("mapbox"),),
    ),
    PluginListing(
        id="stripe",
        name="Stripe",
        section="business",
        does=None,
        logo="stripe.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("stripe"),),
    ),
    PluginListing(
        id="atlassian",
        name="Atlassian",
        section="business",
        does="Find and update issues in Jira and pages in Confluence.",
        logo="atlassian.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("atlassian"), _mcp("atlassian-token")),
    ),
    PluginListing(
        id="canva",
        name="Canva",
        section="business",
        does="Find, make and export your Canva designs.",
        logo=None,
        colour="00C4CC",
        glyph=None,
        monogram="C",
        ways=(_approved_only("Canva", "canva.com"),),
    ),
    PluginListing(
        id="monday",
        name="monday.com",
        section="business",
        does=None,
        logo="monday-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("monday"),),
    ),
    PluginListing(
        id="intercom",
        name="Intercom",
        section="business",
        does=None,
        logo="intercom-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("intercom"),),
    ),
    PluginListing(
        id="wix",
        name="Wix",
        section="business",
        does=None,
        logo="wix.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("wix"),),
    ),
    PluginListing(
        id="zapier",
        name="Zapier",
        section="business",
        does=None,
        logo="zapier.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("zapier"),),
    ),
    PluginListing(
        id="webflow",
        name="Webflow",
        section="business",
        does=None,
        logo="webflow.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("webflow"),),
    ),
    PluginListing(
        id="todoist",
        name="Todoist",
        section="productivity",
        does=None,
        logo="todoist-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("todoist"),),
    ),
    PluginListing(
        id="asana",
        name="Asana",
        section="productivity",
        does="Find, create and update tasks and projects in Asana.",
        logo="asana-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_own_client("Asana"),),
    ),
    PluginListing(
        id="box",
        name="Box",
        section="productivity",
        does="Search, read and file the documents you keep in Box.",
        logo="box.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_own_client("Box"),),
    ),
    PluginListing(
        id="memory",
        name="Memory",
        section="productivity",
        does=None,
        logo="model-context-protocol-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("memory"),),
    ),
    PluginListing(
        id="perplexity",
        name="Perplexity",
        section="productivity",
        does=None,
        logo="perplexity-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("perplexity"),),
    ),
    PluginListing(
        id="elevenlabs",
        name="ElevenLabs",
        section="productivity",
        does=None,
        logo="elevenlabs-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("elevenlabs"),),
    ),
    PluginListing(
        id="miro",
        name="Miro",
        section="productivity",
        does=None,
        logo="miro-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("miro"),),
    ),
    PluginListing(
        id="clickup",
        name="ClickUp",
        section="productivity",
        does=None,
        logo="clickup-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("clickup"),),
    ),
    PluginListing(
        id="notes",
        name="Notes",
        section="mac",
        does=None,
        logo=None,
        colour="E8A600",
        glyph="pencil",
        monogram=None,
        ways=(_mac("notes"),),
    ),
    PluginListing(
        id="reminders",
        name="Reminders",
        section="mac",
        does=None,
        logo=None,
        colour="1E7CF2",
        glyph="list",
        monogram=None,
        ways=(_mac("reminders"),),
    ),
    PluginListing(
        id="messages",
        name="Messages",
        section="mac",
        does=None,
        logo=None,
        colour="1F9D3A",
        glyph="message",
        monogram=None,
        ways=(_mac("messages"),),
    ),
    PluginListing(
        id="contacts",
        name="Contacts",
        section="mac",
        does=None,
        logo=None,
        colour="8E8E93",
        glyph="users",
        monogram=None,
        ways=(_mac("contacts"),),
    ),
    PluginListing(
        id="excel",
        name="Microsoft Excel",
        section="mac",
        does=None,
        logo=None,
        colour="217346",
        glyph=None,
        monogram="X",
        ways=(_mac("excel"),),
    ),
    PluginListing(
        id="word",
        name="Microsoft Word",
        section="mac",
        does=None,
        logo=None,
        colour="2B579A",
        glyph=None,
        monogram="W",
        ways=(_mac("word"),),
    ),
    PluginListing(
        id="powerpoint",
        name="Microsoft PowerPoint",
        section="mac",
        does=None,
        logo=None,
        colour="C43E1C",
        glyph=None,
        monogram="P",
        ways=(_mac("powerpoint"),),
    ),
    PluginListing(
        id="outlook",
        name="Microsoft Outlook",
        section="mac",
        does=None,
        logo=None,
        colour="0078D4",
        glyph=None,
        monogram="O",
        ways=(_mac("outlook"),),
    ),
    PluginListing(
        id="onedrive",
        name="OneDrive",
        section="mac",
        does=None,
        logo="microsoft-onedrive.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(PluginWay(kind="folder", ref="onedrive", how=None),),
    ),
)

_MCP_SIGNS_IN: str = (
    "Its own MCP server: you sign in on the service's page with your account, and Dot keeps the sign-in."
)
_MCP_OPEN: str = "Its own MCP server, open to all: there is nothing to sign in to."
_MCP_KEY: str = (
    "Its own MCP server, with a key of yours: Dot asks for it once, keeps it in its own .env (mode 0600) and sends it"
    " with every request; no record holds it, and Remove takes it out."
)
_MCP_LOCAL: str = (
    "A server Dot starts on this Mac with {command} and talks to over standard input and output; it runs while Dot"
    " does."
)
_MCP_LOCAL_KEY: str = " It takes a key of yours, which Dot asks for once and keeps in its own .env (mode 0600)."
_MAC_HOW: str = "Dot drives it with AppleScript once macOS lets it (Privacy & Security › Automation)."
_MAC_DATA: str = (
    "Dot reads and acts in it through macOS on this Mac; what a run reads there goes to the run's model, as every "
    "tool's result does."
)
# Said of every way's data: a tool's result is part of what the model of the run is sent (docs/safety.md).
_TO_MODEL: str = "what a run reads there goes to the run's model."
_WEB_HOW: str = "Dot works in it in its own browser, once you have signed in there."
_FOLDER_HOW: str = "Dot reads and writes the files it keeps in sync in a folder of this Mac."


def _way_view(way: PluginWay) -> PluginWayView:
    """``way`` with how it connects, where the data goes and what it lets Dot do, from its catalogue entry; a ``ref``
    no catalogue of its kind holds raises :class:`LookupError`."""
    if way.kind == "untried":
        if way.how is None:
            raise LookupError("an untried way says what it would take")
        return PluginWayView(kind="untried", ref=None, how=way.how, data=None, does=None)
    if way.kind == "mcp":
        service = next((one for one in MCP_SERVICES if one.id == way.ref), None)
        if service is None:
            raise LookupError(f"the MCP catalogue has no service {way.ref}")
        if service.url is not None:
            host: str = urlsplit(service.url).hostname or service.url
            data: str = f"Between Dot and {host}, the service's own server; {_TO_MODEL}"
        else:
            # A local service (item 408): a server Dot starts on this Mac, which reaches what its own docs say.
            data = f"On this Mac, a server Dot starts with {service.command}; {_TO_MODEL}"
        if service.signs_in:
            data += " Dot keeps your sign-in in its home."
        if service.secrets:
            data += " Your key is kept in Dot's .env and goes to the service alone."
        how: str
        if service.command is not None:
            how = _MCP_LOCAL.format(command=service.command) + (_MCP_LOCAL_KEY if service.secrets else "")
        elif service.signs_in:
            how = _MCP_SIGNS_IN
        elif service.secrets:
            how = _MCP_KEY
        else:
            how = _MCP_OPEN
        return PluginWayView(kind="mcp", ref=service.id, how=how, data=data, does=service.does)
    if way.kind == "mac":
        app = next((one for one in MAC_APPS if one.id == way.ref), None)
        if app is None:
            raise LookupError(f"the Mac's catalogue has no application {way.ref}")
        return PluginWayView(kind="mac", ref=app.id, how=_MAC_HOW, data=_MAC_DATA, does=app.does)
    if way.kind == "folder":
        folder = next((one for one in FOLDER_APPS if one.id == way.ref), None)
        if folder is None:
            raise LookupError(f"the folders' catalogue has no application {way.ref}")
        data = f"The files stay in a folder of this Mac, which {folder.name} syncs with its cloud; {_TO_MODEL}"
        return PluginWayView(kind="folder", ref=folder.id, how=_FOLDER_HOW, data=data, does=folder.does)
    web = next((one for one in WEB_APPS if one.id == way.ref), None)
    if web is None:
        raise LookupError(f"the web's catalogue has no application {way.ref}")
    signs_in_at: str = urlsplit(web.sign_in).hostname or web.sign_in
    data = f"Between Dot's browser on this Mac and the service's sites (you sign in at {signs_in_at}); {_TO_MODEL}"
    return PluginWayView(kind="web", ref=web.id, how=_WEB_HOW, data=data, does=web.does)


def plugin_view(listing: PluginListing) -> PluginView:
    """``listing`` as the page shows it; its ``does`` is its first way's when it names none."""
    ways: tuple[PluginWayView, ...] = tuple(_way_view(way) for way in listing.ways)
    does: str | None = listing.does if listing.does is not None else ways[0].does
    if does is None:
        raise LookupError(f"{listing.id} says nothing of what Dot does with it")
    return PluginView(
        id=listing.id,
        name=listing.name,
        section=listing.section,
        does=does,
        logo=listing.logo,
        colour=listing.colour,
        glyph=listing.glyph,
        monogram=listing.monogram,
        app=next((way.ref for way in listing.ways if way.kind in ("mac", "folder")), None),
        tried=any(way.kind != "untried" for way in listing.ways),
        ways=ways,
    )


def plugin_views() -> tuple[PluginView, ...]:
    """Every listing of the Plugins page, in its order."""
    return tuple(plugin_view(listing) for listing in PLUGINS)


def logo_of_service(service_id: str) -> str | None:
    """The file of the mark of the listing whose MCP way is the service ``service_id``, for the servers list (item
    408); None for a service with no logo, or none of the directory."""
    for listing in PLUGINS:
        if any(way.kind == "mcp" and way.ref == service_id for way in listing.ways):
            return listing.logo
    return None

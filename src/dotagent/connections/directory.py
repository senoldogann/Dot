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

type PluginSection = Literal[
    "popular",
    "passwords",
    "code",
    "design",
    "data",
    "research",
    "communication",
    "productivity",
    "business",
    "support",
    "mac",
]
# ``vault``: the owner's password manager through its command line, whose logins a run fills (item 493).
type WayKind = Literal["mcp", "mac", "folder", "web", "vault", "untried"]


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


class ServiceMark(FrozenModel):
    """A service's mark as its listing's tile wears it: ``logo``, else ``glyph``, else ``monogram``, the glyph or the
    letters in ``colour`` (``None``: in the ink). A server of the service wears it on the Plugins page and in the
    composer's menus (item 477)."""

    logo: str | None
    glyph: str | None
    monogram: str | None
    colour: str | None


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


def _vault(ref: str) -> PluginWay:
    return PluginWay(kind="vault", ref=ref, how=None)


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
        id="1password",
        name="1Password",
        section="passwords",
        does=None,
        logo=None,
        colour="0572EC",
        glyph="key",
        monogram=None,
        ways=(_vault("1password"),),
    ),
    PluginListing(
        id="sentry",
        name="Sentry",
        section="code",
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
        section="code",
        does=None,
        logo="cloudflare-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("cloudflare"),),
    ),
    PluginListing(
        id="vercel",
        name="Vercel",
        section="code",
        does=None,
        logo="vercel-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("vercel"),),
    ),
    PluginListing(
        id="supabase",
        name="Supabase",
        section="code",
        does="Query and manage your Supabase projects, databases and tables.",
        logo="supabase-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("supabase"),),
    ),
    PluginListing(
        id="neon",
        name="Neon",
        section="code",
        does=None,
        logo="neon-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("neon"),),
    ),
    PluginListing(
        id="gitlab",
        name="GitLab",
        section="code",
        does=None,
        logo="gitlab-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("gitlab"),),
    ),
    PluginListing(
        id="netlify",
        name="Netlify",
        section="code",
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
        section="code",
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
        section="code",
        does=None,
        logo="grafana.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("grafana"),),
    ),
    PluginListing(
        id="playwright",
        name="Playwright",
        section="code",
        does=None,
        logo="playwright.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("playwright"),),
    ),
    PluginListing(
        id="chrome-devtools",
        name="Chrome DevTools",
        section="code",
        does=None,
        logo="chrome.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("chrome-devtools"),),
    ),
    PluginListing(
        id="context7",
        name="Context7",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="C7",
        ways=(_mcp("context7"),),
    ),
    PluginListing(
        id="deepwiki",
        name="DeepWiki",
        section="code",
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
        section="code",
        does=None,
        logo="microsoft-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("microsoft-learn"),),
    ),
    PluginListing(
        id="aws-docs",
        name="AWS Documentation",
        section="code",
        does=None,
        logo="aws.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("aws-docs"), _mcp("aws-knowledge")),
    ),
    PluginListing(
        id="cloudflare-docs",
        name="Cloudflare Docs",
        section="code",
        does=None,
        logo="cloudflare-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("cloudflare-docs"),),
    ),
    PluginListing(
        id="sequential-thinking",
        name="Sequential Thinking",
        section="code",
        does=None,
        logo="model-context-protocol-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("sequential-thinking"),),
    ),
    PluginListing(
        id="newrelic",
        name="New Relic",
        section="code",
        does=None,
        logo="new-relic-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("newrelic"),),
    ),
    # Item 535: the code section's newer services.
    PluginListing(
        id="sourcegraph",
        name="Sourcegraph",
        section="code",
        does=None,
        logo="sourcegraph.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("sourcegraph"),),
    ),
    PluginListing(
        id="grep-app",
        name="grep.app",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="G",
        ways=(_mcp("grep-app"),),
    ),
    PluginListing(
        id="xcode",
        name="Xcode",
        section="code",
        does=None,
        logo="xcode.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("xcode"), _mcp("xcodebuildmcp")),
    ),
    PluginListing(
        id="aws",
        name="AWS",
        section="code",
        does=None,
        logo="aws.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("aws-api"),),
    ),
    PluginListing(
        id="azure",
        name="Azure",
        section="code",
        does=None,
        logo="microsoft-azure.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("azure"),),
    ),
    PluginListing(
        id="alibaba-cloud",
        name="Alibaba Cloud",
        section="code",
        does=None,
        logo="alibabacloud.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("alibaba-cloud"),),
    ),
    PluginListing(
        id="digitalocean",
        name="DigitalOcean",
        section="code",
        does=None,
        logo="digital-ocean-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("digitalocean"),),
    ),
    PluginListing(
        id="kubernetes",
        name="Kubernetes",
        section="code",
        does=None,
        logo="kubernetes.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("kubernetes"),),
    ),
    PluginListing(
        id="pulumi",
        name="Pulumi",
        section="code",
        does=None,
        logo="pulumi-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("pulumi"),),
    ),
    PluginListing(
        id="firebase",
        name="Firebase",
        section="code",
        does=None,
        logo="firebase-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("firebase"),),
    ),
    PluginListing(
        id="convex",
        name="Convex",
        section="code",
        does=None,
        logo="convex.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("convex"),),
    ),
    PluginListing(
        id="prisma",
        name="Prisma",
        section="code",
        does=None,
        logo="prisma.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("prisma"),),
    ),
    PluginListing(
        id="expo",
        name="Expo",
        section="code",
        does=None,
        logo="expo-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("expo"),),
    ),
    PluginListing(
        id="buildkite",
        name="Buildkite",
        section="code",
        does=None,
        logo="buildkite-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("buildkite"),),
    ),
    PluginListing(
        id="snyk",
        name="Snyk",
        section="code",
        does=None,
        logo="snyk.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("snyk"),),
    ),
    PluginListing(
        id="socket",
        name="Socket",
        section="code",
        does=None,
        logo="socket.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("socket"),),
    ),
    PluginListing(
        id="jam",
        name="Jam",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="J",
        ways=(_mcp("jam"),),
    ),
    PluginListing(
        id="logrocket",
        name="LogRocket",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="LR",
        ways=(_mcp("logrocket"),),
    ),
    PluginListing(
        id="honeycomb",
        name="Honeycomb",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="H",
        ways=(_mcp("honeycomb"),),
    ),
    PluginListing(
        id="axiom",
        name="Axiom",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="A",
        ways=(_mcp("axiom"),),
    ),
    PluginListing(
        id="betterstack",
        name="Better Stack",
        section="code",
        does=None,
        logo="betterstack.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("betterstack"),),
    ),
    PluginListing(
        id="pagerduty",
        name="PagerDuty",
        section="code",
        does=None,
        logo="pagerduty-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("pagerduty"),),
    ),
    PluginListing(
        id="incident-io",
        name="incident.io",
        section="code",
        does=None,
        logo="incident-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("incident-io"),),
    ),
    PluginListing(
        id="launchdarkly",
        name="LaunchDarkly",
        section="code",
        does=None,
        logo="launchdarkly-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("launchdarkly"),),
    ),
    PluginListing(
        id="statsig",
        name="Statsig",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="S",
        ways=(_mcp("statsig"),),
    ),
    PluginListing(
        id="braintrust",
        name="Braintrust",
        section="code",
        does=None,
        logo="braintrust.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("braintrust"),),
    ),
    PluginListing(
        id="clerk",
        name="Clerk",
        section="code",
        does=None,
        logo="clerk-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("clerk"),),
    ),
    PluginListing(
        id="workos",
        name="WorkOS",
        section="code",
        does=None,
        logo="workos-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("workos"),),
    ),
    PluginListing(
        id="stytch",
        name="Stytch",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="ST",
        ways=(_mcp("stytch"),),
    ),
    PluginListing(
        id="globalping",
        name="Globalping",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph="globe",
        monogram=None,
        ways=(_mcp("globalping"),),
    ),
    PluginListing(
        id="nextjs",
        name="Next.js",
        section="code",
        does=None,
        logo="nextdotjs.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("nextjs"),),
    ),
    PluginListing(
        id="svelte",
        name="Svelte",
        section="code",
        does=None,
        logo="svelte-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("svelte"),),
    ),
    PluginListing(
        id="astro",
        name="Astro",
        section="code",
        does=None,
        logo="astro-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("astro-docs"),),
    ),
    PluginListing(
        id="nuxt-ui",
        name="Nuxt UI",
        section="code",
        does=None,
        logo="nuxt-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("nuxt-ui"),),
    ),
    PluginListing(
        id="shopify",
        name="Shopify",
        section="code",
        does=None,
        logo="shopify.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("shopify-dev"),),
    ),
    PluginListing(
        id="apollo",
        name="Apollo GraphOS",
        section="code",
        does=None,
        logo="apollostack.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("apollo"),),
    ),
    PluginListing(
        id="openai-docs",
        name="OpenAI Docs",
        section="code",
        does=None,
        logo="openai-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("openai-docs"),),
    ),
    PluginListing(
        id="ref",
        name="Ref",
        section="code",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="R",
        ways=(_mcp("ref"),),
    ),
    PluginListing(
        id="figma",
        name="Figma",
        section="design",
        does="Read your Figma files and their frames, components and comments.",
        logo="figma.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("figma"),),
    ),
    PluginListing(
        id="canva",
        name="Canva",
        section="design",
        does="Find, make and export your Canva designs.",
        logo=None,
        colour="00C4CC",
        glyph=None,
        monogram="C",
        ways=(_approved_only("Canva", "canva.com"),),
    ),
    PluginListing(
        id="miro",
        name="Miro",
        section="design",
        does=None,
        logo="miro-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("miro"),),
    ),
    PluginListing(
        id="webflow",
        name="Webflow",
        section="design",
        does=None,
        logo="webflow.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("webflow"),),
    ),
    PluginListing(
        id="wix",
        name="Wix",
        section="design",
        does=None,
        logo="wix.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("wix"),),
    ),
    PluginListing(
        id="mobbin",
        name="Mobbin",
        section="design",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="M",
        ways=(_mcp("mobbin"),),
    ),
    # Item 535: the design section's newer services.
    PluginListing(
        id="framer",
        name="Framer",
        section="design",
        does=None,
        logo="framer.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("framer"),),
    ),
    PluginListing(
        id="lucid",
        name="Lucid",
        section="design",
        does=None,
        logo="lucid.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("lucid"),),
    ),
    PluginListing(
        id="whimsical",
        name="Whimsical",
        section="design",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="W",
        ways=(_mcp("whimsical"),),
    ),
    PluginListing(
        id="excalidraw",
        name="Excalidraw",
        section="design",
        does=None,
        logo="excalidraw.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("excalidraw"),),
    ),
    PluginListing(
        id="shadcn",
        name="shadcn/ui",
        section="design",
        does=None,
        logo="shadcnui.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("shadcn"),),
    ),
    PluginListing(
        id="magic-ui",
        name="Magic UI",
        section="design",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="MU",
        ways=(_mcp("magic-ui"),),
    ),
    PluginListing(
        id="lottiefiles",
        name="LottieFiles",
        section="design",
        does=None,
        logo="lottiefiles.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("lottiefiles"),),
    ),
    PluginListing(
        id="heygen",
        name="HeyGen",
        section="design",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="HG",
        ways=(_mcp("heygen"),),
    ),
    PluginListing(
        id="sanity",
        name="Sanity",
        section="design",
        does=None,
        logo="sanity.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("sanity"),),
    ),
    PluginListing(
        id="contentful",
        name="Contentful",
        section="design",
        does=None,
        logo="contentful.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("contentful"),),
    ),
    PluginListing(
        id="wordpress-com",
        name="WordPress.com",
        section="design",
        does=None,
        logo="wordpress-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("wordpress-com"),),
    ),
    PluginListing(
        id="posthog",
        name="PostHog",
        section="data",
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
        section="data",
        does=None,
        logo="postman-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("postman"),),
    ),
    PluginListing(
        id="hugging-face",
        name="Hugging Face",
        section="data",
        does="Search models, datasets and Spaces on Hugging Face.",
        logo="hugging-face-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("huggingface"),),
    ),
    PluginListing(
        id="mapbox",
        name="Mapbox",
        section="data",
        does=None,
        logo="mapbox-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("mapbox"),),
    ),
    PluginListing(
        id="airtable",
        name="Airtable",
        section="data",
        does=None,
        logo="airtable.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("airtable"),),
    ),
    PluginListing(
        id="amplitude",
        name="Amplitude",
        section="data",
        does=None,
        logo="amplitude-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("amplitude"),),
    ),
    PluginListing(
        id="mixpanel",
        name="Mixpanel",
        section="data",
        does=None,
        logo="mixpanel.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("mixpanel"),),
    ),
    # Item 535: the data section's newer services.
    PluginListing(
        id="mongodb",
        name="MongoDB",
        section="data",
        does=None,
        logo="mongodb-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("mongodb"),),
    ),
    PluginListing(
        id="duckdb",
        name="DuckDB",
        section="data",
        does=None,
        logo="duckdb.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("duckdb"),),
    ),
    PluginListing(
        id="motherduck",
        name="MotherDuck",
        section="data",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="MD",
        ways=(_mcp("motherduck"),),
    ),
    PluginListing(
        id="pinecone",
        name="Pinecone",
        section="data",
        does=None,
        logo="pinecone-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("pinecone"),),
    ),
    PluginListing(
        id="supermemory",
        name="Supermemory",
        section="data",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="SM",
        ways=(_mcp("supermemory"),),
    ),
    PluginListing(
        id="coingecko",
        name="CoinGecko",
        section="data",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="CG",
        ways=(_mcp("coingecko"),),
    ),
    PluginListing(
        id="exa",
        name="Exa",
        section="research",
        does=None,
        logo="exa-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("exa"),),
    ),
    PluginListing(
        id="tavily",
        name="Tavily",
        section="research",
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
        section="research",
        does=None,
        logo="brave.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("brave-search"),),
    ),
    PluginListing(
        id="perplexity",
        name="Perplexity",
        section="research",
        does=None,
        logo="perplexity-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("perplexity"),),
    ),
    PluginListing(
        id="firecrawl",
        name="Firecrawl",
        section="research",
        does=None,
        logo="firecrawl-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("firecrawl"),),
    ),
    PluginListing(
        id="jina",
        name="Jina",
        section="research",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="J",
        ways=(_mcp("jina"),),
    ),
    PluginListing(
        id="apify",
        name="Apify",
        section="research",
        does=None,
        logo="apify-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("apify"),),
    ),
    PluginListing(
        id="fetch",
        name="Fetch",
        section="research",
        does=None,
        logo="model-context-protocol-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("fetch"),),
    ),
    PluginListing(
        id="semrush",
        name="Semrush",
        section="research",
        does=None,
        logo="semrush.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("semrush"),),
    ),
    PluginListing(
        id="parallel",
        name="Parallel",
        section="research",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="P",
        ways=(_mcp("parallel-search"), _mcp("parallel-task")),
    ),
    # Item 535: the research section's newer services.
    PluginListing(
        id="kagi",
        name="Kagi",
        section="research",
        does=None,
        logo="kagi.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("kagi"),),
    ),
    PluginListing(
        id="consensus",
        name="Consensus",
        section="research",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="C",
        ways=(_mcp("consensus"),),
    ),
    PluginListing(
        id="markitdown",
        name="MarkItDown",
        section="research",
        does=None,
        logo=None,
        colour=None,
        glyph="file",
        monogram=None,
        ways=(_mcp("markitdown"),),
    ),
    PluginListing(
        id="kiwi",
        name="Kiwi.com",
        section="research",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="K",
        ways=(_mcp("kiwi"),),
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
    # Item 535: the communication section's newer services.
    PluginListing(
        id="resend",
        name="Resend",
        section="communication",
        does=None,
        logo="resend-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("resend"),),
    ),
    PluginListing(
        id="mailgun",
        name="Mailgun",
        section="communication",
        does=None,
        logo="mailgun-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("mailgun"),),
    ),
    PluginListing(
        id="dingtalk",
        name="DingTalk",
        section="communication",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="DT",
        ways=(_mcp("dingtalk"),),
    ),
    PluginListing(
        id="atlassian",
        name="Atlassian",
        section="productivity",
        does="Find and update issues in Jira and pages in Confluence.",
        logo="atlassian.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("atlassian"), _mcp("atlassian-token")),
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
        id="calendly",
        name="Calendly",
        section="productivity",
        does=None,
        logo="calendly.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("calendly"),),
    ),
    # Item 535: the productivity section's newer services.
    PluginListing(
        id="dropbox",
        name="Dropbox",
        section="productivity",
        does=None,
        logo="dropbox.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("dropbox"),),
    ),
    PluginListing(
        id="shortcut",
        name="Shortcut",
        section="productivity",
        does=None,
        logo="shortcut-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("shortcut"),),
    ),
    PluginListing(
        id="productboard",
        name="Productboard",
        section="productivity",
        does=None,
        logo="productboard-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("productboard"),),
    ),
    PluginListing(
        id="cal-com",
        name="Cal.com",
        section="productivity",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="Ca",
        ways=(_mcp("cal-com"),),
    ),
    PluginListing(
        id="granola",
        name="Granola",
        section="productivity",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="G",
        ways=(_mcp("granola"),),
    ),
    PluginListing(
        id="fireflies",
        name="Fireflies",
        section="productivity",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="F",
        ways=(_mcp("fireflies"),),
    ),
    PluginListing(
        id="tldv",
        name="tl;dv",
        section="productivity",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="TL",
        ways=(_mcp("tldv"),),
    ),
    PluginListing(
        id="jotform",
        name="Jotform",
        section="productivity",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="J",
        ways=(_mcp("jotform"),),
    ),
    PluginListing(
        id="tally",
        name="Tally",
        section="productivity",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="T",
        ways=(_mcp("tally"),),
    ),
    PluginListing(
        id="time",
        name="Time",
        section="productivity",
        does=None,
        logo=None,
        colour=None,
        glyph="clock",
        monogram=None,
        ways=(_mcp("time"),),
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
        id="paypal",
        name="PayPal",
        section="business",
        does=None,
        logo="paypal.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("paypal"),),
    ),
    PluginListing(
        id="klaviyo",
        name="Klaviyo",
        section="business",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="K",
        ways=(_mcp("klaviyo"),),
    ),
    PluginListing(
        id="mailerlite",
        name="MailerLite",
        section="business",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="ML",
        ways=(_mcp("mailerlite"),),
    ),
    # Item 535: the business section's newer services.
    PluginListing(
        id="hubspot",
        name="HubSpot",
        section="business",
        does=None,
        logo="hubspot.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("hubspot"),),
    ),
    PluginListing(
        id="attio",
        name="Attio",
        section="business",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="A",
        ways=(_mcp("attio"),),
    ),
    PluginListing(
        id="close",
        name="Close",
        section="business",
        does=None,
        logo="close.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("close"),),
    ),
    PluginListing(
        id="customer-io",
        name="Customer.io",
        section="business",
        does=None,
        logo="customerio-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("customer-io"),),
    ),
    PluginListing(
        id="beehiiv",
        name="beehiiv",
        section="business",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="B",
        ways=(_mcp("beehiiv"),),
    ),
    PluginListing(
        id="mercury",
        name="Mercury",
        section="business",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="M",
        ways=(_mcp("mercury"),),
    ),
    PluginListing(
        id="ramp",
        name="Ramp",
        section="business",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="R",
        ways=(_mcp("ramp"),),
    ),
    PluginListing(
        id="greenhouse",
        name="Greenhouse",
        section="business",
        does=None,
        logo="greenhouse.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("greenhouse"),),
    ),
    PluginListing(
        id="intercom",
        name="Intercom",
        section="support",
        does=None,
        logo="intercom-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("intercom"),),
    ),
    PluginListing(
        id="plain",
        name="Plain",
        section="support",
        does=None,
        logo=None,
        colour=None,
        glyph=None,
        monogram="Pl",
        ways=(_mcp("plain"),),
    ),
    # Item 535: the support section's newer service.
    PluginListing(
        id="helpscout",
        name="Help Scout",
        section="support",
        does=None,
        logo="helpscout-icon.svg",
        colour=None,
        glyph=None,
        monogram=None,
        ways=(_mcp("helpscout"),),
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
# 1Password (item 493): how it connects, where a login's values go, and what Dot does with it.
_VAULT_HOW: str = (
    "A vault of yours, Shared with Dot, and a service account that only reads it: Connect makes both with your op, "
    "1Password's command line, through the 1Password app, which asks you first; or paste a service account token you "
    "made."
)
_VAULT_DATA: str = (
    "Between Dot and 1Password, through op on this Mac. A login's values go only into the page Dot fills, on the "
    "login's own site; the run's model sees the logins' titles and websites, never a password or a code. A username "
    "is no secret: it shows where a site shows it."
)
_VAULT_DOES: str = (
    "Fills your logins into pages on their own sites, with the run's model never seeing a password or a code."
)
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
    if way.kind == "vault":
        return PluginWayView(kind="vault", ref=way.ref, how=_VAULT_HOW, data=_VAULT_DATA, does=_VAULT_DOES)
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


def mark_of_service(service_id: str) -> ServiceMark | None:
    """The mark of the listing whose MCP way is the service ``service_id``, as its tile wears it: what the servers list
    draws a server of the service with (item 408), and the composer's menus too (item 477). None for a service of no
    listing."""
    for listing in PLUGINS:
        if any(way.kind == "mcp" and way.ref == service_id for way in listing.ways):
            return ServiceMark(logo=listing.logo, glyph=listing.glyph, monogram=listing.monogram, colour=listing.colour)
    return None


def logo_of_service(service_id: str) -> str | None:
    """The file of the logo of the service ``service_id``'s listing, for the page that says how a sign-in came out;
    None for a service with no logo, or none of the directory."""
    mark: ServiceMark | None = mark_of_service(service_id)
    return None if mark is None else mark.logo

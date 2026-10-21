"""The services whose own MCP servers Customize › Plugins adds in one step (items 279, 316 and 408): each reached at its
address over streamable HTTP, or started on this Mac by a command (``npx``, ``uvx``) and talked to over standard input
and output. Those that sign in do so in the owner's browser, as MCP's authorization asks: OAuth with PKCE, Dot
registering itself with the service's authorization server as a client without a secret. Those that take a key ask
the owner for it on the page (``secrets``): Dot keeps each in its ``.env`` under ``MCP_<service>_<name>`` and the
server's headers or environment refer to it as ``${...}``, so the value never sits in a record.

Checked on 2026-10-15 in each authorization server's own metadata: every service here that signs in offers dynamic
client registration and a client without a secret (``none``); Supabase and monday.com say so in their docs
(2026-10-19). The services item 413 added were checked on 2026-10-19 by Dot's own client: it registered with each and
each opened its sign-in page to it. The addresses, the keys they take and the commands are as the services' own docs
and package registries read on 2026-10-19. Left out, as Dot cannot connect to them: Asana, Slack, Google, Box, Render,
HubSpot and Square (clients registered by hand), Vercel and Canva (approved clients only: Canva refused Dot's), Figma's
own server (it refused Dot's registration; Framelink's server reads Figma with your token instead) and Semgrep's (it
refused the resource Dot asks for). Any of them can still be added by its address.

The services item 505 added were checked on 2026-10-21 by GET alone, as Dot's client reads them: each server's protected
resource metadata and its authorization server's metadata offer dynamic client registration, a client without a secret
(``none``) and PKCE with S256; Parallel's Search server is open to all, as its docs say. Left out then: Ahrefs (its
metadata names no client without a secret), Make and Pipedrive (a client with a secret only), Datadog (its address is
under ``/api/unstable/`` and is one region's), Zoom (clients registered by hand), Dropbox (registration for a few named
clients only) and Plaid (a token of client credentials, no sign-in), the last three as their docs say."""

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from dotagent.core.types import FrozenModel

type ServiceKind = Literal["remote", "local"]


class ServiceSecret(FrozenModel):
    """A secret a service's server takes from the owner: the ``name`` its server knows it by (the ``${name}`` of
    ``headers`` or ``env``), what to call it on the page, and where the owner gets one (None for a value the owner
    knows, such as the address of their own site)."""

    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    label: str
    help: str | None


class McpService(FrozenModel):
    """A service of the catalogue: its id is the name of its server in Dot. A remote service has a ``url`` (and
    ``headers``, which may refer to its ``secrets``); a local one a ``command`` with ``args`` and ``env`` that may
    refer to them. ``signs_in``: Dot signs in to it in the owner's browser."""

    id: str
    name: str
    does: str
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    secrets: tuple[ServiceSecret, ...] = ()
    signs_in: bool = False

    @property
    def kind(self) -> ServiceKind:
        return "remote" if self.url is not None else "local"

    @model_validator(mode="after")
    def _one_way_and_its_secrets(self) -> Self:
        if (self.url is None) == (self.command is None):
            raise ValueError("a service is reached at a url or started by a command, one of the two")
        if self.signs_in and self.url is None:
            raise ValueError("only a service at a url signs in")
        names: set[str] = {secret.name for secret in self.secrets}
        used: set[str] = set()
        for value in (*self.headers.values(), *self.env.values()):
            used.update(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value))
        if used != names:
            differ: str = ", ".join(sorted(names ^ used))
            raise ValueError(f"the secrets a service names and the ones its headers and env refer to differ: {differ}")
        return self


MCP_SERVICES: tuple[McpService, ...] = (
    McpService(
        id="notion",
        name="Notion",
        does="Search, read and write your Notion pages and databases.",
        url="https://mcp.notion.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="linear",
        name="Linear",
        does="Find, create and update issues, projects and comments in Linear.",
        url="https://mcp.linear.app/mcp",
        signs_in=True,
    ),
    McpService(
        id="sentry",
        name="Sentry",
        does="Look into errors, issues and releases in Sentry.",
        url="https://mcp.sentry.dev/mcp",
        signs_in=True,
    ),
    McpService(
        id="cloudflare",
        name="Cloudflare",
        does="Work with your Cloudflare account: Workers, DNS, logs and the rest of its API.",
        url="https://mcp.cloudflare.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="stripe",
        name="Stripe",
        does="Look up and manage customers, payments, invoices and products in Stripe.",
        url="https://mcp.stripe.com",
        signs_in=True,
    ),
    McpService(
        id="todoist",
        name="Todoist",
        does="Read and manage your tasks and projects in Todoist.",
        url="https://ai.todoist.net/mcp",
        signs_in=True,
    ),
    McpService(
        id="deepwiki",
        name="DeepWiki",
        does="Answer questions about public GitHub repositories from their wikis.",
        url="https://mcp.deepwiki.com/mcp",
        signs_in=False,
    ),
    McpService(
        id="microsoft-learn",
        name="Microsoft Learn",
        does="Search Microsoft's documentation and code samples.",
        url="https://learn.microsoft.com/api/mcp",
        signs_in=False,
    ),
    McpService(
        id="github",
        name="GitHub",
        does="Read and work on your repositories, issues, pull requests and actions.",
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": "Bearer ${GITHUB_PAT}"},
        secrets=(
            ServiceSecret(
                name="GITHUB_PAT",
                label="Personal access token",
                help="https://github.com/settings/personal-access-tokens",
            ),
        ),
    ),
    McpService(
        id="supabase",
        name="Supabase",
        does="Query and manage your Supabase projects, databases and tables.",
        url="https://mcp.supabase.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="monday",
        name="monday.com",
        does="Read and update the boards, items and updates of your monday.com workspace.",
        url="https://mcp.monday.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="intercom",
        name="Intercom",
        does="Search your Intercom conversations, contacts and companies.",
        url="https://mcp.intercom.com/mcp",
        headers={"Authorization": "Bearer ${INTERCOM_TOKEN}"},
        secrets=(
            ServiceSecret(
                name="INTERCOM_TOKEN", label="Access token", help="https://developers.intercom.com/docs/guides/mcp"
            ),
        ),
    ),
    McpService(
        id="neon",
        name="Neon",
        does="Create and query your Neon Postgres projects, branches and databases.",
        url="https://mcp.neon.tech/mcp",
        headers={"Authorization": "Bearer ${NEON_API_KEY}"},
        secrets=(
            ServiceSecret(name="NEON_API_KEY", label="API key", help="https://console.neon.tech/app/settings/api-keys"),
        ),
    ),
    McpService(
        id="huggingface",
        name="Hugging Face",
        does="Search models, datasets and Spaces on Hugging Face, and run its Spaces.",
        url="https://huggingface.co/mcp",
        headers={"Authorization": "Bearer ${HF_TOKEN}"},
        secrets=(ServiceSecret(name="HF_TOKEN", label="Access token", help="https://huggingface.co/settings/tokens"),),
    ),
    McpService(
        id="wix",
        name="Wix",
        does="Manage your Wix sites, their content, stores and bookings.",
        url="https://mcp.wix.com/mcp",
        headers={"Authorization": "${WIX_API_KEY}", "wix-account-id": "${WIX_ACCOUNT_ID}"},
        secrets=(
            ServiceSecret(name="WIX_API_KEY", label="API key", help="https://manage.wix.com/account/api-keys"),
            ServiceSecret(name="WIX_ACCOUNT_ID", label="Account id", help="https://manage.wix.com/account/api-keys"),
        ),
    ),
    McpService(
        id="firecrawl",
        name="Firecrawl",
        does="Scrape, crawl and search the web as clean Markdown.",
        url="https://mcp.firecrawl.dev/v2/mcp",
        headers={"Authorization": "Bearer ${FIRECRAWL_API_KEY}"},
        secrets=(
            ServiceSecret(name="FIRECRAWL_API_KEY", label="API key", help="https://www.firecrawl.dev/app/api-keys"),
        ),
    ),
    McpService(
        id="tavily",
        name="Tavily",
        does="Search the web and extract pages, built for agents.",
        url="https://mcp.tavily.com/mcp/",
        headers={"Authorization": "Bearer ${TAVILY_API_KEY}"},
        secrets=(ServiceSecret(name="TAVILY_API_KEY", label="API key", help="https://app.tavily.com/home"),),
    ),
    McpService(
        id="context7",
        name="Context7",
        does="Read the current documentation of libraries and frameworks, by version.",
        url="https://mcp.context7.com/mcp",
    ),
    McpService(
        id="exa",
        name="Exa",
        does="Search the web with Exa's neural search; a few searches a day without a key.",
        url="https://mcp.exa.ai/mcp",
    ),
    McpService(
        id="brave-search",
        name="Brave Search",
        does="Search the web, news, images and places with Brave's index.",
        command="npx",
        args=("-y", "@brave/brave-search-mcp-server"),
        env={"BRAVE_API_KEY": "${BRAVE_API_KEY}"},
        secrets=(ServiceSecret(name="BRAVE_API_KEY", label="API key", help="https://brave.com/search/api/"),),
    ),
    McpService(
        id="memory",
        name="Memory",
        does="A knowledge graph the model keeps across its runs, on this Mac.",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-memory"),
    ),
    McpService(
        id="sequential-thinking",
        name="Sequential Thinking",
        does="A scratchpad for the model to think a hard problem through step by step.",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-sequential-thinking"),
    ),
    McpService(
        id="fetch",
        name="Fetch",
        does="Fetch a web page as Markdown, from this Mac.",
        command="uvx",
        args=("mcp-server-fetch",),
    ),
    McpService(
        id="playwright",
        name="Playwright",
        does="Drive a browser of its own by its accessibility tree, from this Mac.",
        command="npx",
        args=("-y", "@playwright/mcp@latest"),
    ),
    McpService(
        id="gitlab",
        name="GitLab",
        does="Search and work on your GitLab projects, issues, merge requests and pipelines.",
        url="https://gitlab.com/api/v4/mcp",
        signs_in=True,
    ),
    McpService(
        id="posthog",
        name="PostHog",
        does="Ask your PostHog analytics: insights, feature flags, errors and experiments.",
        url="https://mcp.posthog.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="zapier",
        name="Zapier",
        does="Run the actions you set up in Zapier, across the thousands of apps it reaches.",
        url="https://mcp.zapier.com/api/mcp/mcp",
        signs_in=True,
    ),
    McpService(
        id="webflow",
        name="Webflow",
        does="Manage your Webflow sites: their pages, CMS collections and content.",
        url="https://mcp.webflow.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="clickup",
        name="ClickUp",
        does="Find, create and update your ClickUp tasks, lists and docs.",
        url="https://mcp.clickup.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="postman",
        name="Postman",
        does="Work with your Postman workspaces, collections, requests and APIs.",
        url="https://mcp.postman.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="netlify",
        name="Netlify",
        does="Deploy and manage your Netlify sites: their builds, forms and settings.",
        url="https://netlify-mcp.netlify.app/mcp",
        signs_in=True,
    ),
    McpService(
        id="apify",
        name="Apify",
        does="Run Apify's ready-made scrapers and actors on the web.",
        url="https://mcp.apify.com",
        signs_in=True,
    ),
    McpService(
        id="miro",
        name="Miro",
        does="Read and add to your Miro boards: frames, sticky notes, shapes and diagrams.",
        url="https://mcp.miro.com/",
        signs_in=True,
    ),
    McpService(
        id="heroku",
        name="Heroku",
        does="Manage your Heroku apps: their dynos, add-ons, logs and pipelines.",
        url="https://mcp.heroku.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="atlassian",
        name="Atlassian",
        does="Find and update issues in Jira and pages in Confluence, signed in with your account.",
        url="https://mcp.atlassian.com/v2/mcp",
        signs_in=True,
    ),
    McpService(
        id="cloudflare-docs",
        name="Cloudflare Docs",
        does="Search Cloudflare's documentation.",
        url="https://docs.mcp.cloudflare.com/mcp",
        signs_in=False,
    ),
    McpService(
        id="jina",
        name="Jina",
        does="Read web pages as Markdown and search the web with Jina's reader; a few calls a minute without a key.",
        url="https://mcp.jina.ai/v1",
        signs_in=False,
    ),
    McpService(
        id="figma",
        name="Figma",
        does="Read your Figma files: their frames, components, styles and layout, with your access token.",
        command="npx",
        args=("-y", "figma-developer-mcp", "--stdio"),
        env={"FIGMA_API_KEY": "${FIGMA_API_KEY}"},
        secrets=(
            ServiceSecret(
                name="FIGMA_API_KEY",
                label="Personal access token",
                help="https://help.figma.com/hc/en-us/articles/8085703771159-Manage-personal-access-tokens",
            ),
        ),
    ),
    McpService(
        id="atlassian-token",
        name="Atlassian (API token)",
        does=(
            "Find and update issues in Jira and pages in Confluence with an API token, where no admin let Atlassian's "
            "own server in."
        ),
        command="uvx",
        args=("mcp-atlassian",),
        env={
            "JIRA_URL": "${ATLASSIAN_SITE}",
            "JIRA_USERNAME": "${ATLASSIAN_EMAIL}",
            "JIRA_API_TOKEN": "${ATLASSIAN_API_TOKEN}",
            "CONFLUENCE_URL": "${ATLASSIAN_SITE}/wiki",
            "CONFLUENCE_USERNAME": "${ATLASSIAN_EMAIL}",
            "CONFLUENCE_API_TOKEN": "${ATLASSIAN_API_TOKEN}",
        },
        secrets=(
            ServiceSecret(name="ATLASSIAN_SITE", label="Site address (https://your-site.atlassian.net)", help=None),
            ServiceSecret(name="ATLASSIAN_EMAIL", label="E-mail of your Atlassian account", help=None),
            ServiceSecret(
                name="ATLASSIAN_API_TOKEN",
                label="API token",
                help="https://id.atlassian.com/manage-profile/security/api-tokens",
            ),
        ),
    ),
    McpService(
        id="chrome-devtools",
        name="Chrome DevTools",
        does="Look into web pages with Chrome's DevTools from this Mac: performance traces, the network, the console.",
        command="npx",
        args=("-y", "chrome-devtools-mcp@latest"),
    ),
    McpService(
        id="perplexity",
        name="Perplexity",
        does="Search the web and research a question with Perplexity's Sonar models.",
        command="npx",
        args=("-y", "@perplexity-ai/mcp-server"),
        env={"PERPLEXITY_API_KEY": "${PERPLEXITY_API_KEY}"},
        secrets=(
            ServiceSecret(name="PERPLEXITY_API_KEY", label="API key", help="https://www.perplexity.ai/settings/api"),
        ),
    ),
    McpService(
        id="elevenlabs",
        name="ElevenLabs",
        does="Turn text into speech, voices and sound effects with ElevenLabs.",
        command="uvx",
        args=("elevenlabs-mcp",),
        env={"ELEVENLABS_API_KEY": "${ELEVENLABS_API_KEY}"},
        secrets=(
            ServiceSecret(
                name="ELEVENLABS_API_KEY", label="API key", help="https://elevenlabs.io/app/settings/api-keys"
            ),
        ),
    ),
    McpService(
        id="mapbox",
        name="Mapbox",
        does="Search places, turn addresses into coordinates and plan routes with Mapbox.",
        command="npx",
        args=("-y", "@mapbox/mcp-server"),
        env={"MAPBOX_ACCESS_TOKEN": "${MAPBOX_ACCESS_TOKEN}"},
        secrets=(
            ServiceSecret(
                name="MAPBOX_ACCESS_TOKEN", label="Access token", help="https://account.mapbox.com/access-tokens/"
            ),
        ),
    ),
    McpService(
        id="aws-docs",
        name="AWS Documentation",
        does="Search and read the AWS documentation.",
        command="uvx",
        args=("awslabs.aws-documentation-mcp-server@latest",),
    ),
    McpService(
        id="grafana",
        name="Grafana",
        does="Query your Grafana dashboards, data sources, alerts and incidents.",
        command="uvx",
        args=("mcp-grafana",),
        env={"GRAFANA_URL": "${GRAFANA_URL}", "GRAFANA_SERVICE_ACCOUNT_TOKEN": "${GRAFANA_SERVICE_ACCOUNT_TOKEN}"},
        secrets=(
            ServiceSecret(name="GRAFANA_URL", label="Grafana's address", help=None),
            ServiceSecret(
                name="GRAFANA_SERVICE_ACCOUNT_TOKEN",
                label="Service account token",
                help="https://grafana.com/docs/grafana/latest/administration/service-accounts/",
            ),
        ),
    ),
    McpService(
        id="mobbin",
        name="Mobbin",
        does="Search Mobbin's library of real app and web screens and flows for design references.",
        url="https://api.mobbin.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="semrush",
        name="Semrush",
        does="Research keywords, domains, backlinks and traffic with Semrush's data.",
        url="https://mcp.semrush.com/v2/mcp",
        signs_in=True,
    ),
    McpService(
        id="parallel-search",
        name="Parallel Search",
        does="Search the web with Parallel's index, built for agents; free without a key.",
        url="https://search.parallel.ai/mcp",
    ),
    McpService(
        id="parallel-task",
        name="Parallel Task",
        does="Run Parallel's deep research and enrichment tasks over the web.",
        url="https://task-mcp.parallel.ai/mcp",
        signs_in=True,
    ),
    McpService(
        id="plain",
        name="Plain",
        does="Read and answer support threads, customers and companies in Plain.",
        url="https://mcp.plain.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="mailerlite",
        name="MailerLite",
        does="Manage subscribers, groups, campaigns and automations in MailerLite.",
        url="https://mcp.mailerlite.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="airtable",
        name="Airtable",
        does="Read and change the bases, tables and records of your Airtable.",
        url="https://mcp.airtable.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="klaviyo",
        name="Klaviyo",
        does="Work with your Klaviyo profiles, lists, campaigns, flows and their reports.",
        url="https://mcp.klaviyo.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="calendly",
        name="Calendly",
        does="See your Calendly event types and meetings, and book or cancel them.",
        url="https://mcp.calendly.com",
        signs_in=True,
    ),
    McpService(
        id="amplitude",
        name="Amplitude",
        does="Ask your Amplitude analytics: charts, dashboards, cohorts and experiments.",
        url="https://mcp.amplitude.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="mixpanel",
        name="Mixpanel",
        does="Query your Mixpanel events, funnels, retention and reports.",
        url="https://mcp.mixpanel.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="newrelic",
        name="New Relic",
        does="Query your New Relic telemetry, alerts and incidents.",
        url="https://mcp.newrelic.com/mcp/",
        signs_in=True,
    ),
    McpService(
        id="paypal",
        name="PayPal",
        does="Look up and manage invoices, orders, payments, subscriptions and disputes in PayPal.",
        url="https://mcp.paypal.com/mcp",
        signs_in=True,
    ),
)

"""The services whose own MCP servers Customize › Connectors adds in one step (items 279, 316 and 408): each reached at
its address over streamable HTTP, or started on this Mac by a command (``npx``, ``uvx``) and talked to over standard
input and output. Those that sign in do so in the owner's browser, as MCP's authorization asks: OAuth with PKCE, Dot
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
clients only) and Plaid (a token of client credentials, no sign-in), the last three as their docs say.

The services item 535 added were each tried on 2026-10-22 as Dot reaches them. One that signs in went through Dot's own
sign-in as far as the authorization page: the MCP library's OAuth client, with the metadata Dot registers with and its
callback on 127.0.0.1, found the authorization server, registered Dot as a client and was shown the service's sign-in or
consent page (Buildkite's, WordPress.com's and beehiiv's behind a check of the browser that a browser passes). Vercel
and Dropbox, left out before, now let Dot register and show their pages. One open to all, and every one started
on this Mac, was started by ``dotx mcp list`` in a throwaway home and listed its tools, a key it takes given a
placeholder (Kubernetes with a placeholder kubeconfig). Left out then: Canva (its page says "Unsupported client"),
Semgrep, Replicate and Otter (their authorization servers name themselves with a slash their address lacks, which the
MCP library refuses), Gamma (it refuses a callback on 127.0.0.1), Railway (registration turned off), PlanetScale, Make
and Coda (a client with a secret only), Asana, Box, HubSpot's own server, PagerDuty's, Smartsheet, Zendesk, Xero,
Render and DocuSign (no registration), QuickBooks (it refuses a client that is not a browser), Datadog (as above),
Storyblok's open server (it lists no tools), Bright Data's (a tool's schema nests deeper than Dot reads), Postgres MCP
Pro (it fails to start with the current MCP library), the open servers of Gorgias and Retell (by their tools, Gorgias's
is its own team's, and Retell's calls take a key it names no way to pass), and the deprecated packages of Railway,
Dynatrace, CircleCI, Readwise, Browserbase and Semgrep. HubSpot and PagerDuty come in by their own local servers and
your token instead. Redis, ClickHouse, Neo4j and Qdrant start, but each takes its address and user besides its
password, and a service asks the page for secrets alone, which Dot keeps in its .env and so masks wherever their
words appear (``localhost`` among them): they wait for settings of the catalogue that are not secrets, and can be
added by hand under ``[[mcp]]`` until then. Square lets Dot register now too, but its tools take payments, and no flag
marks a payment made through an MCP tool yet: it waits for that flag, so that a call of it stops as the money rule
says whatever the server declares."""

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
        does="Work with your Cloudflare account: Workers, DNS, logs and the rest of what it offers.",
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
                label="Personal access key",
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
                name="INTERCOM_TOKEN", label="Access key", help="https://developers.intercom.com/docs/guides/mcp"
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
            ServiceSecret(name="NEON_API_KEY", label="Key", help="https://console.neon.tech/app/settings/api-keys"),
        ),
    ),
    McpService(
        id="huggingface",
        name="Hugging Face",
        does="Search models, datasets and Spaces on Hugging Face, and run its Spaces.",
        url="https://huggingface.co/mcp",
        headers={"Authorization": "Bearer ${HF_TOKEN}"},
        secrets=(ServiceSecret(name="HF_TOKEN", label="Access key", help="https://huggingface.co/settings/tokens"),),
    ),
    McpService(
        id="wix",
        name="Wix",
        does="Manage your Wix sites, their content, stores and bookings.",
        url="https://mcp.wix.com/mcp",
        headers={"Authorization": "${WIX_API_KEY}", "wix-account-id": "${WIX_ACCOUNT_ID}"},
        secrets=(
            ServiceSecret(name="WIX_API_KEY", label="Key", help="https://manage.wix.com/account/api-keys"),
            ServiceSecret(name="WIX_ACCOUNT_ID", label="Account id", help="https://manage.wix.com/account/api-keys"),
        ),
    ),
    McpService(
        id="firecrawl",
        name="Firecrawl",
        does="Scrape, crawl and search the web as clean Markdown.",
        url="https://mcp.firecrawl.dev/v2/mcp",
        headers={"Authorization": "Bearer ${FIRECRAWL_API_KEY}"},
        secrets=(ServiceSecret(name="FIRECRAWL_API_KEY", label="Key", help="https://www.firecrawl.dev/app/api-keys"),),
    ),
    McpService(
        id="tavily",
        name="Tavily",
        does="Search the web and extract pages, built for agents.",
        url="https://mcp.tavily.com/mcp/",
        headers={"Authorization": "Bearer ${TAVILY_API_KEY}"},
        secrets=(ServiceSecret(name="TAVILY_API_KEY", label="Key", help="https://app.tavily.com/home"),),
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
        secrets=(ServiceSecret(name="BRAVE_API_KEY", label="Key", help="https://brave.com/search/api/"),),
    ),
    McpService(
        id="memory",
        name="Memory",
        does="A web of facts the model keeps across its tasks, on this Mac.",
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
        # The tools a page registers through WebMCP, which the server offers by default, are the words and code of
        # whatever page it opens: they stay out of a run's tools (item 514).
        args=("-y", "@playwright/mcp@latest", "--no-webmcp"),
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
        does="Work with your Postman workspaces, collections, requests and web services.",
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
        does="Read your Figma files: their frames, components, styles and layout, with a key from your Figma account.",
        command="npx",
        args=("-y", "figma-developer-mcp", "--stdio"),
        env={"FIGMA_API_KEY": "${FIGMA_API_KEY}"},
        secrets=(
            ServiceSecret(
                name="FIGMA_API_KEY",
                label="Personal access key",
                help="https://help.figma.com/hc/en-us/articles/8085703771159-Manage-personal-access-tokens",
            ),
        ),
    ),
    McpService(
        id="atlassian-token",
        name="Atlassian (with a key)",
        does=(
            "Find and update issues in Jira and pages in Confluence with a key, where no admin let Atlassian's "
            "own connection in."
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
                label="Key",
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
        secrets=(ServiceSecret(name="PERPLEXITY_API_KEY", label="Key", help="https://www.perplexity.ai/settings/api"),),
    ),
    McpService(
        id="elevenlabs",
        name="ElevenLabs",
        does="Turn text into speech, voices and sound effects with ElevenLabs.",
        command="uvx",
        args=("elevenlabs-mcp",),
        env={"ELEVENLABS_API_KEY": "${ELEVENLABS_API_KEY}"},
        secrets=(
            ServiceSecret(name="ELEVENLABS_API_KEY", label="Key", help="https://elevenlabs.io/app/settings/api-keys"),
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
                name="MAPBOX_ACCESS_TOKEN", label="Access key", help="https://account.mapbox.com/access-tokens/"
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
                label="Service account key",
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
        signs_in=False,
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
    # Item 535: those that sign in, each taken through Dot's sign-in to the service's own page (the docstring).
    McpService(
        id="vercel",
        name="Vercel",
        does="Look into your Vercel projects, their deployments and logs, and search Vercel's docs.",
        url="https://mcp.vercel.com",
        signs_in=True,
    ),
    McpService(
        id="sourcegraph",
        name="Sourcegraph",
        does="Search and read code across repositories with Sourcegraph.",
        url="https://sourcegraph.com/.api/mcp",
        signs_in=True,
    ),
    McpService(
        id="prisma",
        name="Prisma",
        does="Create and manage Prisma Postgres databases, their structure and backups.",
        url="https://mcp.prisma.io/mcp",
        signs_in=True,
    ),
    McpService(
        id="jam",
        name="Jam",
        does="Read the bug reports recorded with Jam: the video, the console logs and the network requests.",
        url="https://mcp.jam.dev/mcp",
        signs_in=True,
    ),
    McpService(
        id="buildkite",
        name="Buildkite",
        does="Look into your Buildkite pipelines, builds and the logs of their jobs.",
        url="https://mcp.buildkite.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="honeycomb",
        name="Honeycomb",
        does="Query your Honeycomb traces and events.",
        url="https://mcp.honeycomb.io/mcp",
        signs_in=True,
    ),
    McpService(
        id="axiom",
        name="Axiom",
        does="Query your Axiom logs, traces and datasets.",
        url="https://mcp.axiom.co/mcp",
        signs_in=True,
    ),
    McpService(
        id="expo",
        name="Expo",
        does="Look into your Expo apps and their builds and updates.",
        url="https://mcp.expo.dev/mcp",
        signs_in=True,
    ),
    McpService(
        id="launchdarkly",
        name="LaunchDarkly",
        does="Look up, create and change feature flags in LaunchDarkly.",
        url="https://mcp.launchdarkly.com/mcp/fm",
        signs_in=True,
    ),
    McpService(
        id="digitalocean",
        name="DigitalOcean",
        does="Manage your DigitalOcean apps, droplets, databases and domains.",
        url="https://mcp.digitalocean.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="pulumi",
        name="Pulumi",
        does="Look into your Pulumi stacks and deployments, and search the Pulumi registry.",
        url="https://mcp.ai.pulumi.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="braintrust",
        name="Braintrust",
        does="Look into the experiments, logs and scores of your AI apps in Braintrust.",
        url="https://api.braintrust.dev/mcp",
        signs_in=True,
    ),
    McpService(
        id="betterstack",
        name="Better Stack",
        does="Query your Better Stack logs, uptime monitors and incidents.",
        url="https://mcp.betterstack.com",
        signs_in=True,
    ),
    McpService(
        id="incident-io",
        name="incident.io",
        does="Look up and work on incidents, alerts and on-call in incident.io.",
        url="https://mcp.incident.io/mcp",
        signs_in=True,
    ),
    McpService(
        id="statsig",
        name="Statsig",
        does="Look up and manage feature gates, experiments and metrics in Statsig.",
        url="https://api.statsig.com/v1/mcp",
        signs_in=True,
    ),
    McpService(
        id="workos",
        name="WorkOS",
        does="Work with your WorkOS environments: organizations, users and sign-in.",
        url="https://mcp.workos.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="stytch",
        name="Stytch",
        does="Work with your Stytch projects and their sign-in settings.",
        url="https://mcp.stytch.dev/mcp",
        signs_in=True,
    ),
    McpService(
        id="clerk",
        name="Clerk",
        does="Work with your Clerk apps, their users and organizations.",
        url="https://mcp.clerk.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="logrocket",
        name="LogRocket",
        does="Look into LogRocket's session replays and the errors your users met.",
        url="https://mcp.logrocket.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="globalping",
        name="Globalping",
        does="Check from many places in the world whether a website or computer can be reached, and how fast.",
        url="https://mcp.globalping.dev/mcp",
        signs_in=True,
    ),
    McpService(
        id="ref",
        name="Ref",
        does="Search the documentation of web services and software libraries, made for coding agents.",
        url="https://api.ref.tools/mcp",
        signs_in=True,
    ),
    McpService(
        id="motherduck",
        name="MotherDuck",
        does="Query your MotherDuck databases, DuckDB in the cloud, with SQL.",
        url="https://api.motherduck.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="supermemory",
        name="Supermemory",
        does="Save and recall memories with Supermemory, shared with your other apps.",
        url="https://mcp.supermemory.ai/mcp",
        signs_in=True,
    ),
    McpService(
        id="sanity",
        name="Sanity",
        does="Read and change the content and structure of your Sanity projects.",
        url="https://mcp.sanity.io",
        signs_in=True,
    ),
    McpService(
        id="contentful",
        name="Contentful",
        does="Read and change the entries, content types and assets of your Contentful spaces.",
        url="https://mcp.contentful.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="wordpress-com",
        name="WordPress.com",
        does="Read and manage the posts, pages and comments of your WordPress.com sites.",
        url="https://public-api.wordpress.com/wpcom/v2/mcp/v1",
        signs_in=True,
    ),
    McpService(
        id="framer",
        name="Framer",
        does="Work on your Framer sites: their pages, components and CMS.",
        url="https://mcp.framer.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="lottiefiles",
        name="LottieFiles",
        does="Search LottieFiles' animations and work with your own.",
        url="https://mcp.lottiefiles.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="lucid",
        name="Lucid",
        does="Find, read and make Lucidchart diagrams and Lucidspark boards.",
        url="https://mcp.lucid.app/mcp",
        signs_in=True,
    ),
    McpService(
        id="whimsical",
        name="Whimsical",
        does="Read and make Whimsical boards, flowcharts and wireframes.",
        url="https://mcp.whimsical.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="heygen",
        name="HeyGen",
        does="Make videos with HeyGen's AI avatars and voices.",
        url="https://mcp.heygen.com/mcp/v1/",
        signs_in=True,
    ),
    McpService(
        id="dropbox",
        name="Dropbox",
        does="Search, read and organize the files and folders of your Dropbox.",
        url="https://mcp.dropbox.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="cal-com",
        name="Cal.com",
        does="See and manage your Cal.com bookings and event types.",
        url="https://mcp.cal.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="granola",
        name="Granola",
        does="Read your Granola meeting notes and transcripts.",
        url="https://mcp.granola.ai/mcp",
        signs_in=True,
    ),
    McpService(
        id="fireflies",
        name="Fireflies",
        does="Search and read your Fireflies meeting transcripts and summaries.",
        url="https://mcp.fireflies.ai/mcp",
        signs_in=True,
    ),
    McpService(
        id="tldv",
        name="tl;dv",
        does="Search and read your tl;dv meeting recordings, transcripts and notes.",
        url="https://mcp.tldv.io/mcp",
        signs_in=True,
    ),
    McpService(
        id="jotform",
        name="Jotform",
        does="Read your Jotform forms and their submissions, and make new forms.",
        url="https://mcp.jotform.com",
        signs_in=True,
    ),
    McpService(
        id="tally",
        name="Tally",
        does="Make Tally forms and read what was submitted to them.",
        url="https://api.tally.so/mcp",
        signs_in=True,
    ),
    McpService(
        id="shortcut",
        name="Shortcut",
        does="Find, create and update stories, epics and iterations in Shortcut.",
        url="https://mcp.shortcut.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="productboard",
        name="Productboard",
        does="Read and update the features, notes and insights of your Productboard.",
        url="https://mcp.productboard.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="attio",
        name="Attio",
        does="Find and update the companies, people, deals and notes of your Attio CRM.",
        url="https://mcp.attio.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="close",
        name="Close",
        does="Find and update the leads, contacts and opportunities of your Close CRM.",
        url="https://mcp.close.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="customer-io",
        name="Customer.io",
        does="Look into your Customer.io people, segments, campaigns and messages.",
        url="https://mcp.customer.io/mcp",
        signs_in=True,
    ),
    McpService(
        id="beehiiv",
        name="beehiiv",
        does="Read your beehiiv publications, their posts and subscribers.",
        url="https://mcp.beehiiv.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="resend",
        name="Resend",
        does="Send e-mail and manage domains, contacts and broadcasts with Resend.",
        url="https://mcp.resend.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="mercury",
        name="Mercury",
        does="Look at your Mercury bank accounts, balances and transactions.",
        url="https://mcp.mercury.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="ramp",
        name="Ramp",
        does="Look into your Ramp cards, transactions, bills and spend.",
        url="https://mcp.ramp.com/mcp",
        signs_in=True,
    ),
    McpService(
        id="greenhouse",
        name="Greenhouse",
        does="Find and read candidates, jobs and applications in Greenhouse Recruiting.",
        url="https://mcp.greenhouse.io/mcp",
        signs_in=True,
    ),
    McpService(
        id="helpscout",
        name="Help Scout",
        does="Search and answer Help Scout conversations, and read your Docs.",
        url="https://mcp.helpscout.net/mcp",
        signs_in=True,
    ),
    McpService(
        id="consensus",
        name="Consensus",
        does="Search research papers and what they found, with Consensus.",
        url="https://mcp.consensus.app/mcp",
        signs_in=True,
    ),
    # Item 535: those open to all, each started by `dotx mcp list` and listing its tools.
    McpService(
        id="grep-app",
        name="grep.app",
        does="Find code in a million public GitHub repositories by its text.",
        url="https://mcp.grep.app",
    ),
    McpService(
        id="svelte",
        name="Svelte",
        does="Read the documentation of Svelte and SvelteKit, and check Svelte code.",
        url="https://mcp.svelte.dev/mcp",
    ),
    McpService(
        id="astro-docs",
        name="Astro Docs",
        does="Search the documentation of Astro.",
        url="https://mcp.docs.astro.build/mcp",
    ),
    McpService(
        id="nuxt-ui",
        name="Nuxt UI",
        does="Read the components, examples and documentation of Nuxt UI.",
        url="https://ui.nuxt.com/mcp",
    ),
    McpService(
        id="openai-docs",
        name="OpenAI Docs",
        does="Search and read OpenAI's developer documentation.",
        url="https://developers.openai.com/mcp",
    ),
    McpService(
        id="aws-knowledge",
        name="AWS Knowledge",
        does="Search AWS's documentation, and which services and features each region has.",
        url="https://knowledge-mcp.global.api.aws",
    ),
    McpService(
        id="socket",
        name="Socket",
        does="Check the security and quality scores of npm, PyPI and other packages with Socket.",
        url="https://mcp.socket.dev/",
    ),
    McpService(
        id="excalidraw",
        name="Excalidraw",
        does="Draw hand-drawn diagrams with Excalidraw, and share one at excalidraw.com.",
        url="https://mcp.excalidraw.com/mcp",
    ),
    McpService(
        id="kiwi",
        name="Kiwi.com",
        does="Search flights between cities on the dates you give, with Kiwi.com.",
        url="https://mcp.kiwi.com",
    ),
    McpService(
        id="coingecko",
        name="CoinGecko",
        does="Look up the prices, market data and history of cryptocurrencies.",
        url="https://mcp.coingecko.com/mcp",
    ),
    # Item 535: one at an address that takes a key: GraphOS's tools need the graph's key, its docs do not.
    McpService(
        id="apollo",
        name="Apollo GraphOS",
        does="Read and check your GraphOS graphs, and search Apollo's GraphQL documentation.",
        url="https://mcp.apollographql.com",
        headers={"x-api-key": "${APOLLO_KEY}"},
        secrets=(
            ServiceSecret(
                name="APOLLO_KEY",
                label="Graph key",
                help="https://www.apollographql.com/docs/graphos/platform/access-management/api-keys",
            ),
        ),
    ),
    # Item 535: started on this Mac, each started by `dotx mcp list` and listing its tools.
    McpService(
        id="duckdb",
        name="DuckDB",
        does="Search and change data files of this Mac, such as CSV spreadsheets, with SQL, in a small database.",
        command="uvx",
        # A database in memory, which the server opens only to read and write (it refuses one in memory to read
        # alone). A query reads and writes the files it names (COPY ... TO overwrites one) and may ATTACH another
        # database: its query tool is declared as one that acts, and the policy weighs each query so. The server's
        # own tool to switch databases is left out (--allow-switch-databases).
        args=("mcp-server-motherduck", "--db-path", ":memory:", "--read-write"),
    ),
    McpService(
        id="markitdown",
        name="MarkItDown",
        does="Turn PDF, Word, Excel, PowerPoint, HTML and other files into Markdown, on this Mac.",
        command="uvx",
        args=("markitdown-mcp",),
    ),
    McpService(
        id="time",
        name="Time",
        does="Tell the time in any time zone and convert a time from one zone to another.",
        command="uvx",
        args=("mcp-server-time",),
    ),
    McpService(
        id="shadcn",
        name="shadcn/ui",
        does="Browse, search and add shadcn/ui components to a project.",
        command="npx",
        args=("-y", "shadcn@latest", "mcp"),
    ),
    McpService(
        id="magic-ui",
        name="Magic UI",
        does="Find Magic UI's animated React components and their code.",
        command="npx",
        args=("-y", "@magicuidesign/mcp@latest"),
    ),
    McpService(
        id="nextjs",
        name="Next.js DevTools",
        does="Read Next.js's docs, and look into a Next.js app running on this Mac: its errors, routes and logs.",
        command="npx",
        args=("-y", "next-devtools-mcp@latest"),
    ),
    McpService(
        id="shopify-dev",
        name="Shopify Dev",
        does="Search Shopify's developer documentation, and check the code written for it.",
        command="npx",
        args=("-y", "@shopify/dev-mcp@latest"),
    ),
    McpService(
        id="xcode",
        name="Xcode",
        does="Build, run and test your Xcode projects through Xcode itself, while it is open.",
        command="xcrun",
        args=("mcpbridge",),
    ),
    McpService(
        id="xcodebuildmcp",
        name="XcodeBuildMCP",
        does="Build, run and test iOS and macOS apps on simulators and devices with Xcode's own commands.",
        command="npx",
        args=("-y", "xcodebuildmcp@latest", "mcp"),
    ),
    McpService(
        id="kubernetes",
        name="Kubernetes",
        does="Look into and manage the clusters of your kubeconfig: pods, deployments, logs and events.",
        command="npx",
        args=("-y", "kubernetes-mcp-server@latest"),
    ),
    McpService(
        id="azure",
        name="Azure",
        does="Work with your Azure resources, signed in with Azure's command line (az login).",
        command="npx",
        args=("-y", "@azure/mcp@latest", "server", "start"),
    ),
    McpService(
        id="aws-api",
        name="AWS",
        does="Run commands on your AWS account, signed in as this Mac's AWS settings say.",
        command="uvx",
        args=("awslabs.aws-api-mcp-server@latest",),
    ),
    McpService(
        id="firebase",
        name="Firebase",
        does="Work with your Firebase projects: their apps, Firestore, authentication and rules.",
        command="npx",
        args=("-y", "firebase-tools@latest", "mcp"),
    ),
    McpService(
        id="convex",
        name="Convex",
        does="Look into your Convex deployments: their tables, data, functions and logs.",
        command="npx",
        args=("-y", "convex@latest", "mcp", "start"),
    ),
    McpService(
        id="snyk",
        name="Snyk",
        does="Scan code, dependencies, containers and infrastructure files for vulnerabilities with Snyk.",
        command="npx",
        args=("-y", "snyk@latest", "mcp", "-t", "stdio"),
    ),
    # Item 535: started on this Mac with a key, each started by `dotx mcp list` with a placeholder, listing its tools.
    McpService(
        id="mongodb",
        name="MongoDB",
        does="Query and manage your MongoDB databases and collections, and your Atlas clusters.",
        command="npx",
        args=("-y", "mongodb-mcp-server@latest"),
        env={"MDB_MCP_CONNECTION_STRING": "${MONGODB_CONNECTION_STRING}"},
        secrets=(
            ServiceSecret(
                name="MONGODB_CONNECTION_STRING",
                label="Connection string (mongodb://... or mongodb+srv://...)",
                help="https://www.mongodb.com/docs/manual/reference/connection-string/",
            ),
        ),
    ),
    McpService(
        id="pinecone",
        name="Pinecone",
        does="Search and manage your Pinecone indexes and the records in them, and read Pinecone's docs.",
        command="npx",
        args=("-y", "@pinecone-database/mcp"),
        env={"PINECONE_API_KEY": "${PINECONE_API_KEY}"},
        secrets=(ServiceSecret(name="PINECONE_API_KEY", label="Key", help="https://app.pinecone.io/"),),
    ),
    McpService(
        id="pagerduty",
        name="PagerDuty",
        does="Look up and work on PagerDuty incidents, services, schedules and on-call.",
        command="uvx",
        args=("pagerduty-mcp",),
        env={"PAGERDUTY_USER_API_KEY": "${PAGERDUTY_API_KEY}"},
        secrets=(
            ServiceSecret(
                name="PAGERDUTY_API_KEY",
                label="User key",
                help="https://support.pagerduty.com/main/docs/api-access-keys",
            ),
        ),
    ),
    McpService(
        id="hubspot",
        name="HubSpot",
        does="Find and update the contacts, companies, deals and tickets of your HubSpot CRM.",
        command="npx",
        args=("-y", "@hubspot/mcp-server"),
        env={"PRIVATE_APP_ACCESS_TOKEN": "${HUBSPOT_TOKEN}"},
        secrets=(
            ServiceSecret(
                name="HUBSPOT_TOKEN",
                label="Key of a private app",
                help="https://developers.hubspot.com/docs/api/private-apps",
            ),
        ),
    ),
    McpService(
        id="mailgun",
        name="Mailgun",
        does="Send e-mail and look into your Mailgun domains, events and statistics.",
        command="npx",
        args=("-y", "@mailgun/mcp-server"),
        env={"MAILGUN_API_KEY": "${MAILGUN_API_KEY}"},
        secrets=(
            ServiceSecret(
                name="MAILGUN_API_KEY",
                label="Key",
                help="https://app.mailgun.com/settings/api_security",
            ),
        ),
    ),
    McpService(
        id="kagi",
        name="Kagi",
        does="Search the web with Kagi, and summarize a page or a video.",
        command="uvx",
        args=("kagimcp",),
        env={"KAGI_API_KEY": "${KAGI_API_KEY}"},
        secrets=(ServiceSecret(name="KAGI_API_KEY", label="Key", help="https://kagi.com/settings?p=api"),),
    ),
    McpService(
        id="alibaba-cloud",
        name="Alibaba Cloud",
        does="Look into and manage your Alibaba Cloud instances, databases, storage and monitoring.",
        command="uvx",
        args=("alibaba-cloud-ops-mcp-server@latest",),
        env={
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "${ALIBABA_CLOUD_ACCESS_KEY_ID}",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "${ALIBABA_CLOUD_ACCESS_KEY_SECRET}",
        },
        secrets=(
            ServiceSecret(
                name="ALIBABA_CLOUD_ACCESS_KEY_ID",
                label="AccessKey ID",
                help="https://www.alibabacloud.com/help/en/ram/user-guide/create-an-accesskey-pair",
            ),
            ServiceSecret(
                name="ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                label="AccessKey secret",
                help="https://www.alibabacloud.com/help/en/ram/user-guide/create-an-accesskey-pair",
            ),
        ),
    ),
    McpService(
        id="dingtalk",
        name="DingTalk",
        does="Read your DingTalk contacts and calendar, and send messages and work notices.",
        command="npx",
        args=("-y", "dingtalk-mcp@latest"),
        env={"DINGTALK_Client_ID": "${DINGTALK_CLIENT_ID}", "DINGTALK_Client_Secret": "${DINGTALK_CLIENT_SECRET}"},
        secrets=(
            ServiceSecret(
                name="DINGTALK_CLIENT_ID",
                label="Client ID of your DingTalk app",
                help="https://open-dev.dingtalk.com/",
            ),
            ServiceSecret(
                name="DINGTALK_CLIENT_SECRET",
                label="Client secret of your DingTalk app",
                help="https://open-dev.dingtalk.com/",
            ),
        ),
    ),
)

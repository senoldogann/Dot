"""The services whose own MCP servers Customize › Plugins adds in one step (items 279, 316 and 408): each reached at its
address over streamable HTTP, or started on this Mac by a command (``npx``, ``uvx``) and talked to over standard input
and output. Those that sign in do so in the owner's browser, as MCP's authorization asks: OAuth with PKCE, Dot
registering itself with the service's authorization server as a client without a secret. Those that take a key ask
the owner for it on the page (``secrets``): Dot keeps each in its ``.env`` under ``MCP_<service>_<name>`` and the
server's headers or environment refer to it as ``${...}``, so the value never sits in a record.

Checked on 2026-10-15 in each authorization server's own metadata: every service here that signs in offers dynamic
client registration and a client without a secret (``none``); Supabase and monday.com say so in their docs
(2026-10-19). The addresses, the keys they take and the commands are as the services' own docs read on 2026-10-19.
Left out, as Dot cannot register with them: Asana, Slack, Google, Box and Square (clients registered by hand), Vercel,
Figma and Canva (approved clients only) and Atlassian (allow-lists). Any of them can still be added by its address."""

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from dotagent.core.types import FrozenModel

type ServiceKind = Literal["remote", "local"]


class ServiceSecret(FrozenModel):
    """A secret a service's server takes from the owner: the ``name`` its server knows it by (the ``${name}`` of
    ``headers`` or ``env``), what to call it on the page, and where the owner gets one."""

    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    label: str
    help: str


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
)

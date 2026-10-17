"""The services whose own MCP servers Customize › Plugins adds in one step (items 279 and 316), each reached at its
address over streamable HTTP. Those that sign in do so in the owner's browser, as MCP's authorization asks: OAuth with
PKCE, Dot registering itself with the service's authorization server as a client without a secret.

Checked on 2026-10-15 in each authorization server's own metadata: every service here that signs in offers dynamic
client registration and a client without a secret (``none``). Left out, as Dot cannot register with them: GitHub (a
personal token only), Asana, Slack, Google and Box (clients registered by hand), Vercel, Figma and Canva (approved
clients only) and Atlassian (allow-lists); Supabase and Hugging Face register clients with a secret alone, which is not
checked yet. Any of them can still be added by its address."""

from dotagent.core.types import FrozenModel


class McpService(FrozenModel):
    """A service of the catalogue: its id is the name of its server in Dot."""

    id: str
    name: str
    does: str
    url: str
    signs_in: bool


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
)

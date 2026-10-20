"""The pages a run shows the user in the conversation (``show_page``): a chart, a table, a diagram, a 3D view or a
mockup, written in HTML. What a page may load, and the CSS variables through which it takes on the conversation's
theme; the tool (``dotagent.tools.pages``), the daemon that serves the pages (``dotagent.server.routes_pages``) and the
frame that shows them (``static/js/pageframe.js``) all follow what is here."""

# The event of a run that showed a page: ``{"page_id", "title"}``.
PAGE_EVENT: str = "page_shown"
# The longest page kept, in characters: a chart that loads its library from a CDN takes a few thousand, an image of its
# own inlined as data a few hundred thousand.
PAGE_MAX_CHARS: int = 500_000
PAGE_TITLE_MAX_CHARS: int = 120
# The only hosts a page's scripts, styles, fonts and requests may reach: the CDNs that serve the libraries of charts,
# diagrams and 3D views. A page reaches nothing else, the daemon included.
LIBRARY_HOSTS: tuple[str, ...] = ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "esm.sh")
# The CSS variables a page is given, each with the value it has until the conversation's theme comes: Dot's neutral
# look, light and dark. The frame sends the values of the theme on screen, and sends them again when it changes.
THEME_VARIABLES: tuple[tuple[str, str], ...] = (
    ("--background", "light-dark(#f8f7f4, #1b1a18)"),
    ("--foreground", "light-dark(#1c1b19, #ecebe6)"),
    ("--muted", "light-dark(#f3f2ee, #2a2927)"),
    ("--muted-foreground", "light-dark(#696864, #a19f98)"),
    ("--card", "light-dark(#fdfcfa, #22211f)"),
    ("--border", "light-dark(rgb(28 27 25 / 16%), rgb(236 234 229 / 18%))"),
    ("--accent", "light-dark(#2b2a27, #e6e4de)"),
    ("--accent-foreground", "light-dark(#fbfaf7, #1b1a18)"),
    ("--destructive", "light-dark(#b8322f, #ff8f88)"),
    ("--warning", "light-dark(#8a5400, #ecb458)"),
    ("--success", "light-dark(#1b7349, #62cf98)"),
    ("--chart-1", "light-dark(#2b2a27, #e6e4de)"),
    ("--chart-2", "#3b93f0"),
    ("--chart-3", "#3ecf8e"),
    ("--chart-4", "#f0b429"),
    ("--chart-5", "#8b5cf6"),
    ("--chart-6", "#e152b0"),
    ("--radius", "12px"),
    ("--font-sans", 'system-ui, -apple-system, "Segoe UI", sans-serif'),
    ("--font-mono", 'ui-monospace, "SF Mono", Menlo, monospace'),
)

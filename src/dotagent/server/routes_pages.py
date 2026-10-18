"""The pages that runs showed (``show_page``), served at ``/pages/<id>`` for the frames of the UI to show, or full size.

The daemon serves a page as the run wrote it, after a bootstrap of its own, and the guard sandboxes it
(``PAGE_HEADERS``): its origin is opaque, so its scripts reach nothing of Dot's. The bootstrap gives the page the CSS
variables of :data:`THEME_VARIABLES`, and speaks three messages of the MCP Apps protocol (JSON-RPC 2.0 over
``postMessage``) with the frame it is in:

- ``ui/notifications/host-context-changed`` from the frame: the conversation's theme,
  ``{"theme": "light" | "dark", "styles": {"variables": {name: value}}}``. The frame puts the same object in the address
  it loads, after ``#dot-theme=``, so that the page is drawn in that theme from the first; the bootstrap takes it out of
  the address again.
- ``ui/notifications/size-changed`` to the frame: ``{"height": pixels}``, every time the page's height changes.
- ``ui/open-link`` to the frame: ``{"url": address}``, for a link to another page that the user clicked; the frame
  opens it in the user's browser. A page opened full size follows its links itself.
"""

import re
from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from dotagent.agent.pages import THEME_VARIABLES
from dotagent.server.context import ServerContext
from dotagent.server.guard import PAGES_PREFIX
from dotagent.store.models import ShownPage

# What the page is drawn with before its own styles: Dot's colours for light and dark, and type of the conversation's
# size, on the background of the conversation, inside the frame's border. The page sits in the conversation, so no
# scrollbar of its own shows.
_DEFAULT_STYLE: str = (
    ":root{color-scheme:light dark;"
    + "".join(f"{name}:{value};" for name, value in THEME_VARIABLES)
    + "}html{background:var(--background);color:var(--foreground);font-family:var(--font-sans);font-size:14px;"
    "line-height:1.5;-webkit-text-size-adjust:100%;scrollbar-width:none}html::-webkit-scrollbar{display:none}"
    "body{margin:0;padding:16px}code,kbd,pre,samp{font-family:var(--font-mono)}"
)
# The bootstrap: the theme from the address and from the frame, the page's height, and the links clicked.
_BOOTSTRAP: str = """(() => {
  "use strict";
  const CONTEXT_CHANGED = "ui/notifications/host-context-changed";
  const SIZE_CHANGED = "ui/notifications/size-changed";
  const OPEN_LINK = "ui/open-link";
  const NAME = /^--[a-z0-9-]+$/;
  const theme = document.getElementById("dot-theme");
  const framed = window.parent !== window;

  // The conversation's theme, as the frame sends it: its values take the place of Dot's own.
  function paint(context) {
    const variables = context?.styles?.variables;
    if (typeof variables !== "object" || variables === null) {
      return;
    }
    let rules = `:root{color-scheme:${context.theme === "dark" ? "dark" : "light"};`;
    for (const [name, value] of Object.entries(variables)) {
      if (NAME.test(name) && typeof value === "string") {
        rules += `${name}:${value.replace(/[;{}<>\\\\]/g, "")};`;
      }
    }
    theme.textContent = `${rules}}`;
  }

  try {
    const given = new URLSearchParams(location.hash.slice(1)).get("dot-theme");
    if (given !== null) {
      paint(JSON.parse(given));
      history.replaceState(history.state, "", location.pathname + location.search);
    }
  } catch {
    // An address whose theme cannot be read leaves the page in Dot's own colours.
  }

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (framed && event.source === window.parent && message?.jsonrpc === "2.0" && message.method === CONTEXT_CHANGED) {
      paint(message.params);
    }
  });

  if (!framed) {
    return;
  }
  // A link to another page that the user clicked opens in their browser rather than in the frame; a link within the
  // page, and a click the page's own scripts took, stay as they are.
  let requests = 0;
  window.addEventListener("click", (event) => {
    if (!event.isTrusted || event.defaultPrevented) {
      return;
    }
    const link = event.composedPath().find((node) => node instanceof Element && node.matches("a[href], area[href]"));
    if (link === undefined) {
      return;
    }
    let url;
    try {
      url = new URL(link.getAttribute("href"), document.baseURI);
    } catch {
      return;
    }
    if (!/^https?:$/.test(url.protocol) || url.href.split("#")[0] === location.href.split("#")[0]) {
      return;
    }
    event.preventDefault();
    requests += 1;
    window.parent.postMessage(
      { jsonrpc: "2.0", id: `dot-link-${requests}`, method: OPEN_LINK, params: { url: url.href } },
      "*",
    );
  });

  // The page's height: what overflows the frame, or, when the page is shorter than the frame, the page itself.
  let reported = 0;
  function report() {
    const root = document.documentElement;
    const height = Math.ceil(
      root.scrollHeight > root.clientHeight ? root.scrollHeight : root.getBoundingClientRect().height,
    );
    if (height !== reported) {
      reported = height;
      window.parent.postMessage({ jsonrpc: "2.0", method: SIZE_CHANGED, params: { height } }, "*");
    }
  }
  const observer = new ResizeObserver(report);
  observer.observe(document.documentElement);
  document.addEventListener("DOMContentLoaded", () => {
    if (document.body !== null) {
      observer.observe(document.body);
    }
    report();
  });
  window.addEventListener("load", report);
})();
"""
_HEAD: str = (
    '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    f'<style>{_DEFAULT_STYLE}</style><style id="dot-theme"></style><script>{_BOOTSTRAP}</script>'
)
_TITLE: re.Pattern[str] = re.compile(r"<title[\s>]", re.IGNORECASE)


def framed_page(title: str, html: str) -> str:
    """The document served for a page: the bootstrap, the page's title unless it has one, then the page as the run
    wrote it. The HTML parser takes a doctype, ``<html>`` or ``<head>`` of the page that comes after the bootstrap as
    it takes any one repeated: it drops the doctype and the ``<head>`` tag, gives ``<html>``'s attributes to the root,
    and reads on."""
    named: str = "" if _TITLE.search(html) else f"<title>{escape(title)}</title>"
    return f"{_HEAD}{named}{html}"


def pages_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter()

    @router.get(f"{PAGES_PREFIX}{{page_id}}", response_class=HTMLResponse)
    async def page(page_id: str) -> HTMLResponse:
        """A page a run showed, to whoever names its id; the guard sandboxes it. 404 for an id that names none."""
        shown: ShownPage = await ctx.runtime.store.get_shown_page(page_id)
        return HTMLResponse(framed_page(shown.title, shown.html))

    return router

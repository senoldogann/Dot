// A link in a message, as a pill: a short label and the site's own icon, opened in a new window. The desktop
// shell hands that window to the default browser (`on_new_window` in the shell) and never shows the page in its own
// web view. The icon is this daemon's copy of the site's `/favicon.ico`, never a third-party favicon service.

import { parseBlocks } from "./markdown.js";

// github.com/<owner>/<repo>/pull/<n> or /issues/<n>, whatever follows the number.
const GITHUB_NUMBER = /^https:\/\/github\.com\/[^/?#]+\/[^/?#]+\/(?:pull|issues)\/(\d+)(?=[/?#]|$)/i;
// A host the daemon will fetch an icon for: letters, digits and hyphens, at least one dot, no address of a machine.
const HOST = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/;
const LABEL_LIMIT = 48;

/** The host of `href` without a leading "www.", or "" when it is not a site the pill can name. */
function linkHost(href) {
  try {
    const host = new URL(href).hostname.toLowerCase().replace(/^www\./, "");
    return HOST.test(host) ? host : "";
  } catch {
    return "";
  }
}

/** Whether `words` are just the address, so the pill names the site or the pull request instead. */
function bare(words, href) {
  const text = words.trim();
  if (text === "" || text === href) {
    return true;
  }
  try {
    return new URL(text).href === new URL(href).href;
  } catch {
    return false;
  }
}

/** The words on the pill: the link's own words, or `#1001` for a GitHub pull request or issue, or the site. */
function linkLabel(words, href) {
  const github = GITHUB_NUMBER.exec(href);
  if (github !== null && bare(words, href)) {
    return `#${github[1]}`;
  }
  if (!bare(words, href)) {
    const label = words.replace(/\s+/g, " ").trim();
    return label.length > LABEL_LIMIT ? `${label.slice(0, LABEL_LIMIT - 1)}…` : label;
  }
  const host = linkHost(href);
  return host === "" ? words.trim() : host;
}

/**
 * How one link is drawn and opened. `target` is a new window, which the shell opens in the browser; the page never
 * navigates its own view to it. `favicon` is this daemon's route for the site's icon, or "" when there is no site.
 * @param {{ value: string, href: string }} part
 */
export function pillFromPart(part) {
  const host = linkHost(part.href);
  return {
    label: linkLabel(part.value, part.href),
    href: part.href,
    host,
    icon: host === "" ? "link" : "favicon",
    favicon: host === "" ? "" : `/api/favicons/${host}`,
    target: "_blank",
    rel: "noopener noreferrer nofollow",
    external: true,
  };
}

function collect(block, out) {
  const inlines = [];
  if (Array.isArray(block.inline)) {
    inlines.push(block.inline);
  }
  if (Array.isArray(block.header)) {
    inlines.push(block.header);
  }
  if (Array.isArray(block.rows)) {
    for (const row of block.rows) {
      inlines.push(row);
    }
  }
  for (const parts of inlines) {
    for (const part of parts) {
      if (part.type === "link") {
        out.push(pillFromPart(part));
      }
    }
  }
  if (Array.isArray(block.items)) {
    for (const item of block.items) {
      collect({ inline: item.inline, blocks: item.blocks }, out);
    }
  }
  if (Array.isArray(block.blocks)) {
    for (const child of block.blocks) {
      collect(child, out);
    }
  }
}

/** The pills of `markdown`, in the order a message draws them. A link inside code is not one. */
export function pillsInMessage(markdown) {
  const out = [];
  for (const block of parseBlocks(markdown)) {
    collect(block, out);
  }
  return out;
}

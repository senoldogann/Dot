// The row of the pages a turn read on the web (item 1184), under its answer: folded, the first letters of a few of their
// sites and how many pages; open, each page as its title and its site, a link that opens it in the browser. The words
// are the pages' own (sourcelist.js), shown as text.

import { el } from "./dom.js";
import { tCount } from "./i18n.js";
import { icon } from "./icons.js";
import { hostsOf, sourcesOf } from "./sourcelist.js";

// The sites the folded row shows the letters of.
const FACES = 3;

/** The first letter of `host`, the mark of its site where the page shows no picture of anyone's. */
function siteMark(host) {
  return el("span", { class: "source-mark", "aria-hidden": "true" }, (host[0] ?? "?").toUpperCase());
}

/** One page: its title (its address when it had none) over its site. */
function sourceItem(source) {
  return el(
    "li",
    null,
    el(
      "a",
      { class: "source-link", href: source.url, target: "_blank", rel: "noopener noreferrer nofollow", title: source.url },
      siteMark(source.host),
      el(
        "span",
        { class: "source-words" },
        el("span", { class: "source-title" }, source.title === "" ? source.url : source.title),
        el("span", { class: "source-host" }, source.host),
      ),
    ),
  );
}

/**
 * The row of the pages the run whose events are `events` read, or null when it read none.
 * @param {import("./sourcelist.js").SourceEvent[]} events
 */
export function sourcesRow(events) {
  const { sources, more } = sourcesOf(events);
  if (sources.length === 0) {
    return null;
  }
  const count = sources.length + more;
  return el(
    "details",
    { class: "answer-sources" },
    el(
      "summary",
      { class: "sources-head" },
      el("span", { class: "source-marks" }, ...hostsOf(sources).slice(0, FACES).map(siteMark)),
      el("span", { class: "sources-count" }, tCount(count, "sources.one", "sources.many")),
      el("span", { class: "chev" }, icon("chevron")),
    ),
    el(
      "ol",
      { class: "source-list" },
      ...sources.map(sourceItem),
      more === 0 ? null : el("li", { class: "source-more" }, tCount(more, "sources.moreOne", "sources.moreMany")),
    ),
  );
}

/** Put the row of the pages the run read under the answer `answer` (an `answerMessage`), over its foot, in place of
 * one drawn before; nothing when the run read none. */
export function showSources(answer, events) {
  const row = sourcesRow(events);
  answer.querySelector(":scope > .answer-sources")?.remove();
  if (row !== null) {
    answer.querySelector(":scope > .answer-foot")?.before(row);
  }
}

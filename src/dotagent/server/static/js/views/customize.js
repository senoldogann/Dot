// Customize (items 316 and 542): what Dot works with and what it knows how to do, a page of the sidebar under Campaigns
// and Schedules. Three tabs: Plugins, the gallery of the plugins Dot installs (views/plugins.js); Skills, the skills it
// reads (views/skills.js, item 317); and Connectors, the services it connects to (views/connectors.js). `#/customize`
// opens Plugins, `#/customize/plugins/<id>` a plugin's own page and `#/customize/connectors/<id>` a service's.

import { el } from "../dom.js";
import { icon } from "../icons.js";
import { pageHead } from "../panels.js";
import * as connectors from "./connectors.js";
import * as plugins from "./plugins.js";
import * as skills from "./skills.js";

const TABS = [
  { id: "plugins", label: "Plugins", icon: "puzzle", view: plugins },
  { id: "skills", label: "Skills", icon: "sparkle", view: skills },
  { id: "connectors", label: "Connectors", icon: "plug", view: connectors },
];

export function mount(root, ctx) {
  const tab = TABS.find((candidate) => candidate.id === (ctx.params[0] ?? "plugins"));
  ctx.setTitle(`${tab.label} · Customize`);
  const links = el(
    "nav",
    { class: "segmented customize-tabs", "aria-label": "Customize" },
    TABS.map((candidate) =>
      el("a", { href: `#/customize/${candidate.id}`, "aria-current": candidate.id === tab.id ? "page" : null }, icon(candidate.icon), el("span", null, candidate.label)),
    ),
  );
  root.append(pageHead("Customize", "", links));
  const body = el("div", { class: "customize-body", dataset: { tab: tab.id } });
  root.append(body);
  return tab.view.mount(body, ctx);
}

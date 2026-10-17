// Customize (item 316): what Dot works with and what it knows how to do, a page of the sidebar under Campaigns. Two
// tabs: Plugins, the services Dot connects to (views/plugins.js), and Skills, the skills it reads (views/skills.js,
// item 317). `#/customize` opens Plugins, `#/customize/plugins/<id>` a service's own page.

import { el } from "../dom.js";
import { icon } from "../icons.js";
import { pageHead } from "../panels.js";
import * as plugins from "./plugins.js";
import * as skills from "./skills.js";

const TABS = [
  { id: "plugins", label: "Plugins", icon: "plug", view: plugins },
  { id: "skills", label: "Skills", icon: "sparkle", view: skills },
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

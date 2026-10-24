// Settings › System › Open source licences (item 371): the licences of what Dot uses, read from what is really there
// (dotagent/server/licences.py): the notices that come with its pages, the Python packages installed beside the daemon
// with the licence each names, and the tools Dot uses, found on this Mac or not.

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, pageHead } from "../panels.js";
import { attempt } from "../ui.js";

function card(title, side, body) {
  return el("section", { class: "card" }, el("div", { class: "card-head" }, el("span", { class: "grow" }, title), side), body);
}

function table(heads, rows) {
  return el(
    "div",
    { class: "table-scroll" },
    el("table", { class: "table" }, el("thead", null, el("tr", null, heads.map((head) => el("th", null, head)))), el("tbody", null, rows)),
  );
}

function outLink(url, text) {
  return url === "" ? el("span", null, text) : el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text);
}

/** A notice that comes with the pages: what it covers, folded over its words. */
function noticeRow(notice) {
  return el(
    "details",
    { class: "notice" },
    el("summary", { class: "notice-head" }, el("span", { class: "notice-name" }, notice.name), el("span", { class: "mono small muted" }, notice.path)),
    el("pre", { class: "notice-text" }, notice.text),
  );
}

/** A version as its words with a place to break after each dot, so that a narrow column breaks a long one between its
 * numbers ("2026.9.29." over "20261017") and never inside one (item 606). */
function versionWords(version) {
  return version.split(".").flatMap((part, index) => (index === 0 ? [part] : [".", el("wbr"), part]));
}

function packageRow(item) {
  return el("tr", { dataset: { package: item.name.toLowerCase() } }, el("td", null, outLink(item.url, item.name)), el("td", { class: "mono" }, ...versionWords(item.version)), el("td", null, item.licence === "" ? "—" : item.licence));
}

/** A tool Dot uses: its name and its licence, what Dot uses it for, and where it is on this Mac. */
function toolRow(tool) {
  return el(
    "div",
    { class: "list-row tool-row" },
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row-title" }, tool.name),
      el("div", { class: "row-text small" }, tool.does),
      el("div", { class: tool.where === "" ? "row-text small muted" : "row-text mono small muted folder-path" }, tool.where === "" ? "Not on this Mac" : tool.where),
    ),
    el("span", { class: "tool-licence small" }, outLink(tool.url, tool.licence)),
  );
}

export function mount(root) {
  let disposed = false;
  const holder = el("div", { class: "stack" }, loadingPanel());

  function paint(view) {
    const rows = view.packages.map(packageRow);
    const count = el("span", { class: "small muted" }, `${view.packages.length} packages`);
    const filter = el("input", {
      class: "input",
      type: "search",
      placeholder: "Find a package",
      "aria-label": "Find a package",
      oninput: () => {
        const words = filter.value.trim().toLowerCase();
        const shown = rows.filter((row) => {
          row.hidden = words !== "" && !row.dataset.package.includes(words);
          return !row.hidden;
        });
        count.textContent = words === "" ? `${view.packages.length} packages` : `${shown.length} of ${view.packages.length} packages`;
      },
    });
    holder.replaceChildren(
      card("With Dot's pages", null, el("div", { class: "card-pad stack" }, el("p", { class: "hint" }, "The fonts, the icons and the code Dot took from other projects come with their notices, whose copyright Dot keeps."), view.bundled.map(noticeRow))),
      card("Python packages", count, el("div", null, el("div", { class: "card-pad" }, filter), table(["Package", "Version", "Licence"], rows))),
      card("Tools Dot uses", null, el("div", { class: "list" }, view.tools.map(toolRow))),
    );
  }

  async function load() {
    try {
      const view = await api.get("/licences");
      if (!disposed) {
        paint(view);
      }
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
    }
  }

  root.append(
    el("a", { class: "back", href: "#/settings/system" }, icon("arrowLeft"), "System"),
    pageHead("Open source licences", "The notices of the packages, the assets and the tools Dot uses, as they are on this Mac.", null),
    holder,
  );
  attempt(load);
  return () => {
    disposed = true;
  };
}

// Customize › Plugins (item 542): the gallery of the plugins Dot installs, after Qoder's page. A head that says what a
// plugin is, with the search and a few of the gallery's marks; New & noteworthy, the plugins the gallery features; and
// All plugins, with All, Featured and Installed and an order, Popular (the stars of its repository) or Newest (when the
// gallery took it). Each plugin is a card: its mark (its logo, else a skill's puzzle piece on a tile of its kind's
// colour), its name with a check when its publisher makes what its skills are about, who publishes it and how many
// skills it brings, what it does, and Install, or Installed with Remove. A card opens the plugin's own page: its
// skills, where it comes from (the repository at the commit Dot pins, its licence) and what Install does. Install
// downloads that commit, checks its files against the hash the gallery keeps and offers its skills to the runs that
// start after, with no restart; Remove moves its folder to the Trash (item 497). Installed lists too the plugins Dot
// reads that are not the gallery's. A service's page, which was at `#/customize/plugins/<id>` before item 542, is under
// Connectors; the address of a service whose gallery plugin has the same id (Cloudflare, Supabase, MongoDB, Hugging
// Face) is the plugin's.

import { api } from "../api.js";
import { customizeHead } from "../customizehead.js";
import { compact, el, foldSearch, joined, plural } from "../dom.js";
import { icon } from "../icons.js";
import { tintMark } from "../marks.js";
import { emptyPanel, errorPanel, loadingPanel, segmented } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";

/** The kinds of the gallery's plugins, in their words, each with the colour of themes.css (`--mark-<hue>`) of the tile
 * of the mark of a plugin of the kind with no logo. */
const CATEGORIES = {
  development: { label: "Development", hue: "blue" },
  design: { label: "Design", hue: "pink" },
  product: { label: "Product", hue: "violet" },
  data: { label: "Data", hue: "teal" },
  business: { label: "Business", hue: "orange" },
  writing: { label: "Writing", hue: "green" },
};
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** The mark of a plugin of the gallery: its logo on the light tile a connector's logo sits on, else the puzzle piece a
 * skill wears (item 537) on a tile of its kind's colour; a plugin of no kind of the gallery's takes the development
 * one. */
function galleryMark(plugin) {
  if (plugin.logo !== null && plugin.logo !== undefined) {
    return el("span", { class: "plugin-mark", "aria-hidden": "true" }, el("img", { class: "plugin-logo", src: `/icons/plugins/${plugin.logo}`, alt: "" }));
  }
  const mark = el("span", { class: "plugin-mark", "aria-hidden": "true", dataset: { tint: "" } }, icon("puzzle"));
  mark.style.setProperty("--plugin-hue", `var(--mark-${(CATEGORIES[plugin.category] ?? CATEGORIES.development).hue})`);
  return mark;
}

/** Whether Dot installed the plugin: from the commit the gallery pins, or from an older one (`outdated`, item 558). */
function isInstalled(plugin) {
  return plugin.state === "installed" || plugin.state === "outdated";
}

/** The check beside the name of a plugin whose publisher makes what its skills are about. */
function verified(plugin) {
  return el(
    "span",
    { class: "gallery-verified", title: `From ${plugin.publisher}, who make what its skills are about`, role: "img", "aria-label": "From its maker" },
    icon("badgeCheck"),
  );
}

/** A card of the plugin's page, as a service's page has them: a head with its icon and title, and `body`. */
function pageCard(kind, title, mark, body) {
  return el("section", { class: "card connect-section plugin-card", dataset: { kind } }, el("div", { class: "card-head" }, icon(mark), el("span", { class: "grow" }, title)), el("div", null, body));
}

export function mount(root, ctx) {
  const pluginId = ctx.params[1];
  let disposed = false;
  let gallery = null;
  let shown = "all";
  let order = "popular";
  let words = "";
  // The plugins being installed or removed, by id or name: their buttons stay off while the page paints again.
  const busy = new Set();
  const holder = el("div", { class: "stack" }, loadingPanel());

  const search = el("input", { class: "input customize-search", type: "search", placeholder: "Search plugins and skills", "aria-label": "Search plugins", autocomplete: "off", spellcheck: "false" });
  search.addEventListener("input", () => {
    words = foldSearch(search.value);
    paint();
  });
  const head = customizeHead({
    title: "Discover plugins",
    line: "Skills from the makers of the apps you use and from the community, each one checked by Dot before it is used. Install one and the tasks that start after can use it.",
    search,
    controls: [],
  });
  const filters = segmented(
    [
      { id: "all", label: "All", hint: "Every plugin of the gallery" },
      { id: "featured", label: "Featured", hint: "The plugins the gallery features" },
      { id: "installed", label: "Installed", hint: "The plugins Dot has installed, and the others it reads" },
    ],
    shown,
    (id) => {
      shown = id;
      paint();
    },
  );
  filters.element.setAttribute("aria-label", "Plugins to show");
  const orders = segmented(
    [
      { id: "popular", label: "Popular", hint: "The most liked on GitHub first" },
      { id: "newest", label: "Newest", hint: "The plugins added to the gallery most recently first" },
    ],
    order,
    (id) => {
      order = id;
      paint();
    },
  );
  orders.element.setAttribute("aria-label", "Order");
  const featuredGrid = el("div", { class: "gallery-featured" });
  const featured = el("section", { class: "gallery-section", dataset: { section: "noteworthy" } }, el("h2", { class: "section-label gallery-section-title" }, "New & noteworthy"), featuredGrid);
  const allGrid = el("div", { class: "gallery-grid" });
  const all = el(
    "section",
    { class: "gallery-section", dataset: { section: "all" } },
    el("div", { class: "gallery-section-head" }, el("h2", { class: "section-label gallery-section-title" }, "All plugins"), el("div", { class: "gallery-controls" }, filters.element, orders.element)),
    allGrid,
  );
  // Made once and painted again in place, so that the search keeps the focus and the controls their state.
  const body = el("div", { class: "gallery", hidden: true }, featured, all);

  /** Install `plugin`: the daemon downloads its pinned commit, checks it and offers its skills to the next runs. */
  async function install(plugin) {
    busy.add(plugin.id);
    paint();
    try {
      const now = await api.post(`/plugin-gallery/${encodeURIComponent(plugin.id)}`, {});
      if (!disposed) {
        gallery = now;
      }
    } finally {
      busy.delete(plugin.id);
      paint();
    }
    toast("ok", `${plugin.name} is installed: the tasks that start now are offered its ${plural(plugin.skills.length, "skill", "skills")}.`);
  }

  /** Take the plugin `name` out of Dot after a confirmation: its folder goes to the Trash (item 497). `title` is how
   * the page names it, `servers` how many MCP servers it brings. */
  async function remove(name, title, servers) {
    const sure = await confirmDialog({
      title: `Remove ${title} from Dot?`,
      message:
        servers > 0
          ? "Its connected apps stop now and its skills leave the tasks that start after. Its folder goes to the Trash, where you can put it back."
          : "Its skills leave the tasks that start after. Its folder goes to the Trash, where you can put it back.",
      confirmLabel: "Remove",
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    busy.add(name);
    paint();
    try {
      await api.del(`/installed-plugins/${encodeURIComponent(name)}`);
      const now = await api.get("/plugin-gallery");
      if (!disposed) {
        gallery = now;
      }
    } finally {
      busy.delete(name);
      paint();
    }
    toast("ok", `${title} went to the Trash.`);
  }

  /** What a card or a plugin's page offers: Install, or Installed with Remove, or that a plugin of Dot holds its name.
   * `wide` writes Remove out, as the plugin's page has room for. */
  function action(plugin, wide) {
    if (isInstalled(plugin)) {
      const label = `Remove ${plugin.name}`;
      const removing = busy.has(plugin.id);
      const button = wide
        ? el("button", { class: "btn sm", type: "button", disabled: removing }, icon("trash"), "Remove")
        : el("button", { class: "btn sm ghost icon-only gallery-remove", type: "button", title: label, "aria-label": label, disabled: removing }, icon("trash"));
      button.addEventListener("click", () => attempt(() => remove(plugin.id, plugin.name, 0)));
      // The pin moved since: the runs read the skills of the commit installed, and Install is refused while they are.
      // One badge, so that the card is no wider than with Installed.
      const badge =
        plugin.state === "outdated"
          ? el(
              "span",
              { class: "badge b-warn gallery-installed gallery-outdated", title: `Installed from ${plugin.installed_commit.slice(0, 7)}; the gallery now has ${plugin.commit.slice(0, 7)}. Remove it and Install it again to get the new one.` },
              "Older version",
            )
          : el("span", { class: "badge b-ok gallery-installed" }, icon("check"), "Installed");
      return el("div", { class: "gallery-action" }, badge, button);
    }
    if (plugin.state === "taken") {
      return el(
        "div",
        { class: "gallery-action" },
        el("span", { class: "badge b-muted", title: `Dot has a plugin named ${plugin.id} already: Installed lists it.` }, "Name taken"),
      );
    }
    const installing = busy.has(plugin.id);
    const button = el(
      "button",
      { class: wide ? "btn sm primary gallery-install" : "btn sm gallery-install", type: "button", disabled: installing, "aria-label": `Install ${plugin.name}` },
      installing ? [el("span", { class: "spinner", "aria-hidden": "true" }), "Installing…"] : [icon("download"), "Install"],
    );
    button.addEventListener("click", () => attempt(() => install(plugin)));
    return el("div", { class: "gallery-action" }, button);
  }

  /** A plugin's card: its mark, its name (a link to its page that covers the card) with its check, who publishes it
   * and how many skills it brings, what it does, and its action. */
  function card(plugin) {
    return el(
      "article",
      { class: "gallery-card", dataset: { plugin: plugin.id, state: plugin.state } },
      galleryMark(plugin),
      el(
        "div",
        { class: "gallery-card-main" },
        el("div", { class: "gallery-card-head" }, el("a", { class: "gallery-card-name", href: `#/customize/plugins/${plugin.id}` }, plugin.name), plugin.official ? verified(plugin) : null),
        el("div", { class: "gallery-card-by" }, ...joined([plugin.publisher, CATEGORIES[plugin.category]?.label ?? null, plural(plugin.skills.length, "skill", "skills")])),
        el("p", { class: "gallery-card-does" }, plugin.does),
      ),
      action(plugin, false),
    );
  }

  /** A plugin Dot reads that is not the gallery's: its name, where it is, its servers and what it says of itself, and
   * Remove for one in Dot's own plugins folder (a folder of the config's is taken out of the config). */
  function otherCard(other) {
    const mark = el("span", { class: "plugin-mark", "aria-hidden": "true" }, icon("puzzle"));
    tintMark(mark, null, other.name);
    let removeButton = null;
    if (other.own) {
      const label = `Remove ${other.name}`;
      removeButton = el("button", { class: "btn sm ghost icon-only gallery-remove", type: "button", title: label, "aria-label": label, disabled: busy.has(other.name) }, icon("trash"));
      removeButton.addEventListener("click", () => attempt(() => remove(other.name, other.name, other.servers)));
    }
    return el(
      "article",
      { class: "gallery-card", dataset: { plugin: other.name, state: "other" } },
      mark,
      el(
        "div",
        { class: "gallery-card-main" },
        el("div", { class: "gallery-card-head" }, el("span", { class: "gallery-card-name" }, other.name)),
        el(
          "div",
          { class: "gallery-card-by" },
          ...joined([other.own ? "In Dot's plugins folder" : "From a folder in Dot's settings", other.servers > 0 ? plural(other.servers, "connected app", "connected apps") : null]),
        ),
        other.description === null ? null : el("p", { class: "gallery-card-does" }, other.description),
      ),
      removeButton === null ? null : el("div", { class: "gallery-action" }, removeButton),
    );
  }

  function matches(plugin) {
    if (words === "") {
      return true;
    }
    const kind = CATEGORIES[plugin.category]?.label ?? "";
    return foldSearch(`${plugin.name} ${plugin.publisher} ${plugin.does} ${kind} ${plugin.skills.join(" ")}`).includes(words);
  }

  function ordered(plugins) {
    const byStars = (a, b) => b.stars - a.stars || a.name.localeCompare(b.name);
    return [...plugins].sort(order === "newest" ? (a, b) => b.added.localeCompare(a.added) || byStars(a, b) : byStars);
  }

  /** The head's marks, scattered about a soft light: the first featured plugins' and, between them, logos of the
   * gallery, so that the head shows what kinds of plugins it holds. */
  function paintHead() {
    const featuredOnes = gallery.plugins.filter((plugin) => plugin.featured && plugin.logo === null);
    const logos = gallery.plugins.filter((plugin) => plugin.logo !== null);
    const shownMarks = [featuredOnes[0], logos[0], featuredOnes[1], logos[1], featuredOnes[2]].filter((plugin) => plugin !== undefined);
    head.setMarks(shownMarks.map(galleryMark));
  }

  function paintCounts() {
    const [allButton, featuredButton, installedButton] = filters.element.children;
    const installed = gallery.plugins.filter(isInstalled).length + gallery.others.length;
    allButton.textContent = `All ${gallery.plugins.length}`;
    featuredButton.textContent = `Featured ${gallery.plugins.filter((plugin) => plugin.featured).length}`;
    installedButton.textContent = `Installed ${installed}`;
  }

  function paintList() {
    paintHead();
    paintCounts();
    featured.hidden = words !== "" || shown !== "all";
    featuredGrid.replaceChildren(...gallery.plugins.filter((plugin) => plugin.featured).map(card));
    const showing = ordered(
      gallery.plugins.filter((plugin) => (shown === "all" || (shown === "featured" && plugin.featured) || (shown === "installed" && isInstalled(plugin))) && matches(plugin)),
    );
    const others =
      shown === "installed" ? gallery.others.filter((other) => words === "" || foldSearch(`${other.name} ${other.description ?? ""}`).includes(words)) : [];
    const parts = showing.map(card);
    if (others.length > 0) {
      parts.push(el("h3", { class: "section-label gallery-others-title" }, "Not from the gallery"), ...others.map(otherCard));
    }
    if (parts.length === 0) {
      parts.push(
        words !== ""
          ? emptyPanel("search", "No plugin matches the search", "")
          : emptyPanel("puzzle", "No plugin installed yet", "Install one from All, and the tasks that start after can use its skills."),
      );
    }
    allGrid.replaceChildren(...parts);
    holder.hidden = true;
    body.hidden = false;
  }

  function paintPlugin(plugin) {
    ctx.setTitle(`${plugin.name} · Plugins`);
    const kind = CATEGORIES[plugin.category]?.label ?? null;
    const facts = [
      ["Source", el("a", { href: plugin.homepage, target: "_blank", rel: "noopener noreferrer" }, `github.com/${plugin.repository}`, icon("external"))],
      ["Version checked", el("span", { class: "mono" }, plugin.commit.slice(0, 7))],
      ...(plugin.state === "outdated" ? [["Installed from", el("span", { class: "mono" }, plugin.installed_commit.slice(0, 7))]] : []),
      ["Licence", plugin.licence],
      ["Stars", `${compact(plugin.stars)} on GitHub`],
      ["In the gallery since", DAY.format(new Date(`${plugin.added}T00:00:00`))],
    ];
    holder.replaceChildren(
      el("div", { class: "plugin-page-bar" }, el("a", { class: "back", href: "#/customize/plugins" }, icon("arrowLeft"), "Plugins")),
      el(
        "header",
        { class: "gallery-page-head", dataset: { plugin: plugin.id, state: plugin.state } },
        galleryMark(plugin),
        el(
          "div",
          { class: "gallery-card-main" },
          el("div", { class: "gallery-card-head" }, el("h2", { class: "plugin-title" }, plugin.name), plugin.official ? verified(plugin) : null),
          el("div", { class: "gallery-card-by" }, ...joined([plugin.publisher, kind, plural(plugin.skills.length, "skill", "skills")])),
          el("p", { class: "gallery-page-does" }, plugin.does),
        ),
        action(plugin, true),
      ),
      pageCard(
        "skills",
        "Skills it brings",
        "puzzle",
        el(
          "ul",
          { class: "gallery-skills" },
          plugin.skills.map((name) => el("li", null, icon("puzzle"), el("span", { class: "mono" }, name))),
        ),
      ),
      pageCard(
        "source",
        "Where it comes from",
        "shield",
        el(
          "div",
          null,
          el("dl", { class: "gallery-facts" }, facts.flatMap(([term, value]) => [el("dt", null, term), el("dd", null, value)])),
          el(
            "p",
            { class: "hint connect-hint" },
            "Install downloads this version from GitHub and checks its files against the fingerprint Dot keeps before it puts them in its plugins folder; the tasks that start after are offered its skills, with no restart. Remove moves the folder to the Trash.",
          ),
        ),
      ),
    );
  }

  function paint() {
    if (disposed || gallery === null) {
      return;
    }
    if (pluginId === undefined) {
      paintList();
      return;
    }
    const plugin = gallery.plugins.find((one) => one.id === pluginId);
    if (plugin === undefined) {
      // A service's page had this address before the plugins took the name (item 542): it is under Connectors now.
      location.replace(`${location.pathname}${location.search}#/customize/connectors/${pluginId}`);
      return;
    }
    paintPlugin(plugin);
  }

  async function load() {
    try {
      const found = await api.get("/plugin-gallery");
      if (!disposed) {
        gallery = found;
        paint();
      }
    } catch (error) {
      if (!disposed) {
        holder.hidden = false;
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
    }
  }

  if (pluginId === undefined) {
    root.append(head.element, holder, body);
  } else {
    root.append(holder);
  }
  attempt(load);
  return () => {
    disposed = true;
  };
}

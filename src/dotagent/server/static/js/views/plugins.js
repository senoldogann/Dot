// Settings › Plugins (item 542): the gallery of the plugins Dot installs, after Qoder's page. A head that says what a
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
//
// Proposed for you (item 712) stands above them: what the scout found last night, each a card with where it comes
// from, what the review made of it in the owner's language, its risk and what the scan found, how near it is to the
// owner's work, and Install (or Update) and Skip. Look now starts a night; what the review refused is listed under it.
// A plugin updated from a proposal stands as Updated, with Revert while the version it replaced is kept.

import { api } from "../api.js";
import { customizeHead } from "../customizehead.js";
import { compact, el, foldSearch, joined, } from "../dom.js";
import { icon } from "../icons.js";
import { markContent } from "../marks.js";
import { aboutToggle, emptyPanel, errorPanel, loadingPanel, segmented } from "../panels.js";
import { attempt, confirmDialog, toast } from "../ui.js";
import { t, tCount } from "../i18n.js";

/** The kinds of the gallery's plugins, in their words. */
const CATEGORIES = {
  development: { label: t("views.plugins.development") },
  design: { label: t("views.plugins.design") },
  product: { label: t("views.plugins.product") },
  data: { label: t("views.plugins.data") },
  business: { label: t("views.plugins.business") },
  writing: { label: t("views.plugins.writing") },
};
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** The mark of a plugin of the gallery: its logo, else the puzzle piece a skill wears (item 537), in the tile's ink
 * (item 946). */
function galleryMark(plugin) {
  return el("span", { class: "plugin-mark", "aria-hidden": "true" }, markContent({ logo: plugin.logo ?? null, glyph: "puzzle" }));
}

/** Whether Dot installed the plugin: from the commit the gallery pins, from an older one (`outdated`, item 558), or from
 * a newer one the owner took from the scout (`updated`, item 712). */
function isInstalled(plugin) {
  return plugin.state === "installed" || plugin.state === "outdated" || plugin.state === "updated";
}

/** The words of a proposal's risk, as a badge of its colour. */
const RISKS = {
  low: { label: t("views.plugins.lowRisk"), tone: "b-ok" },
  medium: { label: t("views.plugins.mediumRisk"), tone: "b-warn" },
  high: { label: t("views.plugins.highRisk"), tone: "b-bad" },
};
const TIME = new Intl.DateTimeFormat("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });

/** The check beside the name of a plugin whose publisher makes what its skills are about. */
function verified(plugin) {
  return el(
    "span",
    { class: "gallery-verified", title: t("views.plugins.fromPublisherWhoMakeWhat", { publisher: plugin.publisher }), role: "img", "aria-label": t("views.plugins.fromItsMaker") },
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
  // The scout's proposals (item 712), and the timer that asks again while a night runs.
  let scout = null;
  let scoutTimer = null;
  const holder = el("div", { class: "stack" }, loadingPanel());

  const search = el("input", { class: "input customize-search", type: "search", placeholder: t("views.plugins.searchPluginsAndSkills"), "aria-label": t("views.plugins.searchPlugins"), autocomplete: "off", spellcheck: "false" });
  search.addEventListener("input", () => {
    words = foldSearch(search.value);
    paint();
  });
  const head = customizeHead({
    line: t("views.plugins.skillsFromTheMakersOf"),
    search,
    controls: [],
  });
  const filters = segmented(
    [
      { id: "all", label: t("views.plugins.all"), hint: t("views.plugins.everyPluginOfTheGallery") },
      { id: "featured", label: t("views.plugins.featured"), hint: t("views.plugins.thePluginsTheGalleryFeatures") },
      { id: "installed", label: t("views.plugins.installed"), hint: t("views.plugins.thePluginsDotHasInstalled") },
    ],
    shown,
    (id) => {
      shown = id;
      paint();
    },
  );
  filters.element.setAttribute("aria-label", t("views.plugins.pluginsToShow"));
  const orders = segmented(
    [
      { id: "popular", label: t("views.plugins.popular"), hint: t("views.plugins.theMostLikedOnGithub") },
      { id: "newest", label: t("views.plugins.newest"), hint: t("views.plugins.thePluginsAddedToThe") },
    ],
    order,
    (id) => {
      order = id;
      paint();
    },
  );
  orders.element.setAttribute("aria-label", t("views.plugins.order"));
  const featuredGrid = el("div", { class: "gallery-featured" });
  const featured = el("section", { class: "gallery-section", dataset: { section: "noteworthy" } }, el("h2", { class: "section-label gallery-section-title" }, t("views.plugins.newNoteworthy")), featuredGrid);
  const allGrid = el("div", { class: "gallery-grid" });
  const all = el(
    "section",
    { class: "gallery-section", dataset: { section: "all" } },
    el("div", { class: "gallery-section-head" }, el("h2", { class: "section-label gallery-section-title" }, t("views.plugins.allPlugins")), el("div", { class: "gallery-controls" }, filters.element, orders.element)),
    allGrid,
  );
  const scoutGrid = el("div", { class: "scout-grid" });
  // What the nightly look does and found stands behind the (i) beside the part's name (item 848).
  const scoutAbout = aboutToggle("", t("views.plugins.aboutTheProposals"), "hint scout-line");
  const scoutLine = scoutAbout.note;
  const lookButton = el("button", { class: "btn sm scout-look", type: "button" }, icon("refresh"), t("views.plugins.lookNow"));
  lookButton.addEventListener("click", () => attempt(lookNow));
  const scoutSection = el(
    "section",
    { class: "gallery-section scout-section", dataset: { section: "proposals" }, hidden: true },
    el("div", { class: "gallery-section-head" }, el("h2", { class: "section-label gallery-section-title" }, t("views.plugins.proposedForYou")), scoutAbout.button, lookButton),
    scoutLine,
    scoutGrid,
  );
  // Made once and painted again in place, so that the search keeps the focus and the controls their state.
  const body = el("div", { class: "gallery", hidden: true }, scoutSection, featured, all);

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
    toast("ok", t("views.plugins.nameIsInstalledTheTasks", { name: plugin.name, part: tCount(plugin.skills.length, "views.plugins.countSkill", "views.plugins.countSkills") }));
  }

  /** Ask the scout for its proposals, and again every few seconds while a night runs. */
  async function loadScout() {
    const found = await api.get("/plugin-scout");
    if (disposed) {
      return;
    }
    scout = found;
    paintScout();
    clearTimeout(scoutTimer);
    if (scout.running) {
      scoutTimer = setTimeout(() => attempt(loadScout), 3000);
    }
  }

  async function lookNow() {
    lookButton.disabled = true;
    try {
      scout = await api.post("/plugin-scout/look", {});
      clearTimeout(scoutTimer);
      scoutTimer = setTimeout(() => attempt(loadScout), 3000);
    } finally {
      // Refused (the emergency stop answers 409), the button is given back as it was.
      paintScout();
    }
  }

  /** Install or update what `proposal` proposes; the gallery is read again, as it lists what was installed. */
  async function take(proposal) {
    const key = `scout:${proposal.kind}:${proposal.id}`;
    busy.add(key);
    paintScout();
    try {
      scout = await api.post(`/plugin-scout/${encodeURIComponent(proposal.id)}/install?kind=${proposal.kind}`, {});
      const now = await api.get("/plugin-gallery");
      if (!disposed) {
        gallery = now;
      }
    } finally {
      busy.delete(key);
      paint();
      paintScout();
    }
    toast(
      "ok",
      proposal.kind === "update"
        ? t("views.plugins.nameIsUpdatedRevertOn", { name: proposal.name })
        : t("views.plugins.nameIsInstalledTheTasks", { name: proposal.name, part: tCount(proposal.skills.length, "views.plugins.countSkill", "views.plugins.countSkills") }),
    );
  }

  async function skip(proposal) {
    const key = `scout:${proposal.kind}:${proposal.id}`;
    busy.add(key);
    paintScout();
    try {
      scout = await api.post(`/plugin-scout/${encodeURIComponent(proposal.id)}/skip?kind=${proposal.kind}`, {});
    } finally {
      busy.delete(key);
      paintScout();
    }
  }

  /** Put back the version an update replaced (item 712). */
  async function revert(plugin) {
    const sure = await confirmDialog({
      title: t("views.plugins.putBackTheEarlierVersion", { name: plugin.name }),
      message: t("views.plugins.theUpdatedVersionGoesTo"),
      confirmLabel: t("views.plugins.revert"),
      focusCancel: true,
    });
    if (!sure) {
      return;
    }
    busy.add(plugin.id);
    paint();
    try {
      const now = await api.post(`/plugin-gallery/${encodeURIComponent(plugin.id)}/revert`, {});
      if (!disposed) {
        gallery = now;
      }
    } finally {
      busy.delete(plugin.id);
      paint();
    }
    toast("ok", t("views.plugins.nameIsBackAtIts", { name: plugin.name }));
  }

  /** A proposal's card: its mark and name, whether it is new or an update and where it comes from, its risk, what it
   * does, the details folded (why the risk is what it is in the review's words, what it needs, how near it is to the
   * owner's work, its skills, what the scan found, its source), and Install or Update with Skip. */
  function proposalCard(proposal) {
    const key = `scout:${proposal.kind}:${proposal.id}`;
    const working = busy.has(key);
    const risk = RISKS[proposal.risk] ?? { label: t("views.plugins.notReviewed"), tone: "b-muted" };
    const takeButton = el(
      "button",
      { class: "btn sm primary scout-take", type: "button", disabled: working, "aria-label": `${proposal.kind === "update" ? t("views.plugins.update") : t("views.plugins.install")} ${proposal.name}` },
      working ? [el("span", { class: "spinner", "aria-hidden": "true" }), proposal.kind === "update" ? t("views.plugins.updating") : t("views.plugins.installing")] : [icon("download"), proposal.kind === "update" ? t("views.plugins.update") : t("views.plugins.install")],
    );
    takeButton.addEventListener("click", () => attempt(() => take(proposal)));
    const skipButton = el("button", { class: "btn sm ghost scout-skip", type: "button", disabled: working, "aria-label": t("views.plugins.skipName", { name: proposal.name }) }, t("views.plugins.skip"));
    skipButton.addEventListener("click", () => attempt(() => skip(proposal)));
    const findings = proposal.findings.map((finding) =>
      el("li", { dataset: { severity: finding.severity } }, el("span", { class: `badge ${finding.severity === "high" ? "b-bad" : finding.severity === "medium" ? "b-warn" : "b-muted"}` }, finding.rule), " ", el("span", { class: "mono" }, finding.path), ": ", finding.detail),
    );
    const details = el(
      "details",
      { class: "scout-details" },
      el("summary", null, t("views.plugins.details")),
      // Why its risk is what it is and what its skills need (item 949) open the card's details (item 1136): the card
      // says what it does, its risk and the one action.
      proposal.why ? el("p", { class: "hint scout-why" }, proposal.why) : null,
      // What its skills need that it does not bring: installed alone, they would only describe it.
      proposal.needs ? el("p", { class: "hint scout-needs" }, t("views.plugins.needsNeeds", { needs: proposal.needs })) : null,
      el("dl", { class: "gallery-facts" }, [
        el("dt", null, t("views.plugins.fitsYourWork")),
        el("dd", { class: "scout-fit", title: t("views.plugins.howCloseItsSkillsAre") }, `${Math.round(proposal.fit * 100)}%`),
        el("dt", null, t("views.plugins.source")),
        el("dd", null, el("a", { href: proposal.homepage, target: "_blank", rel: "noopener noreferrer" }, t("views.plugins.githubComRepository", { repository: proposal.repository }), icon("external")), ` · ${proposal.source}`),
        el("dt", null, t("views.plugins.versionChecked")),
        el("dd", null, el("span", { class: "mono" }, proposal.commit.slice(0, 7)), proposal.previous ? t("views.plugins.installedSlice", { slice: proposal.previous.slice(0, 7) }) : ""),
        el("dt", null, t("views.plugins.licence")),
        el("dd", null, t("views.plugins.licenceCompactStarsOnGithub", { licence: proposal.licence, compact: compact(proposal.stars) })),
        el("dt", null, t("views.plugins.skills")),
        el("dd", null, el("span", { class: "mono" }, proposal.skills.join(", "))),
        ...(proposal.changed.length > 0 ? [el("dt", null, t("views.plugins.changed")), el("dd", null, el("span", { class: "mono" }, proposal.changed.join(", ")))] : []),
      ]),
      findings.length > 0 ? el("ul", { class: "scout-findings" }, findings) : el("p", { class: "hint" }, t("views.plugins.theScanFoundNothingTo")),
    );
    return el(
      "article",
      { class: "gallery-card scout-card", dataset: { plugin: proposal.id, kind: proposal.kind, risk: proposal.risk ?? "none" } },
      galleryMark({ logo: null }),
      el(
        "div",
        { class: "gallery-card-main" },
        el("div", { class: "gallery-card-head" }, el("span", { class: "gallery-card-name" }, proposal.name), proposal.tier === "a" ? verified({ publisher: proposal.publisher }) : null),
        el(
          "div",
          { class: "gallery-card-by" },
          ...joined([proposal.kind === "update" ? t("views.plugins.update") : t("views.plugins.new"), proposal.publisher, proposal.tier === "a" ? t("views.plugins.anthropicOrItsMaker") : t("views.plugins.community"), tCount(proposal.skills.length, "views.plugins.countSkill", "views.plugins.countSkills")]),
        ),
        el(
          "div",
          { class: "scout-badges" },
          el("span", { class: `badge ${risk.tone}`, title: proposal.why }, risk.label),
        ),
        el("p", { class: "gallery-card-does" }, proposal.does || proposal.name),
        details,
      ),
      el("div", { class: "gallery-action scout-actions" }, takeButton, skipButton),
    );
  }

  function paintScout() {
    if (disposed || scout === null || pluginId !== undefined) {
      return;
    }
    scoutSection.hidden = words !== "";
    lookButton.disabled = scout.running;
    lookButton.replaceChildren(...(scout.running ? [el("span", { class: "spinner", "aria-hidden": "true" }), t("views.plugins.looking")] : [icon("refresh"), t("views.plugins.lookNow")]));
    const when = scout.last_run === null ? t("views.plugins.dotHasNotLookedYet") : t("views.plugins.lastLookedFormatLastSummary", { format: TIME.format(new Date(scout.last_run * 1000)), last_summary: scout.last_summary });
    const nightly = scout.time === "off" ? t("views.plugins.theNightlyLookIsOff") : t("views.plugins.dotLooksEveryNightAt", { time: scout.time });
    scoutLine.textContent = `${nightly} ${when}`;
    const parts = scout.proposals.map(proposalCard);
    if (parts.length === 0) {
      parts.push(emptyPanel("sparkles", t("views.plugins.nothingWaiting"), t("views.plugins.whatTheNextLookFinds")));
    }
    if (scout.refused.length > 0) {
      parts.push(
        el(
          "details",
          { class: "scout-refused" },
          el("summary", null, t("views.plugins.partRefusedByDotS", { part: tCount(scout.refused.length, "views.plugins.countPlugin", "views.plugins.countPlugins") })),
          el(
            "ul",
            null,
            scout.refused.map((one) => el("li", null, el("strong", null, one.name), t("views.plugins.githubComRepository2", { repository: one.repository }), one.why)),
          ),
        ),
      );
    }
    scoutGrid.replaceChildren(...parts);
  }

  /** Take the plugin `name` out of Dot after a confirmation: its folder goes to the Trash (item 497). `title` is how
   * the page names it, `servers` how many MCP servers it brings. */
  async function remove(name, title, servers) {
    const sure = await confirmDialog({
      title: t("views.plugins.removeTitleFromDot", { title }),
      message:
        servers > 0
          ? t("views.plugins.itsConnectedAppsStopNow")
          : t("views.plugins.itsSkillsLeaveTheTasks"),
      confirmLabel: t("views.plugins.remove"),
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
    toast("ok", t("views.plugins.titleWentToTheTrash", { title }));
  }

  /** What a card or a plugin's page offers: Install, or Installed with Remove, or that a plugin of Dot holds its name.
   * `wide` writes Remove out, as the plugin's page has room for. */
  function action(plugin, wide) {
    if (isInstalled(plugin)) {
      const label = t("views.plugins.removeName", { name: plugin.name });
      const removing = busy.has(plugin.id);
      const button = wide
        ? el("button", { class: "btn sm", type: "button", disabled: removing }, icon("trash"), t("views.plugins.remove"))
        : el("button", { class: "btn sm ghost icon-only gallery-remove", type: "button", title: label, "aria-label": label, disabled: removing }, icon("trash"));
      button.addEventListener("click", () => attempt(() => remove(plugin.id, plugin.name, 0)));
      // The pin moved since: the runs read the skills of the commit installed, and Install is refused while they are.
      // One badge, so that the card is no wider than with Installed.
      const badge =
        plugin.state === "outdated"
          ? el(
              "span",
              { class: "badge b-warn gallery-installed gallery-outdated", title: t("views.plugins.installedFromSliceTheGallery", { slice: plugin.installed_commit.slice(0, 7), slice2: plugin.commit.slice(0, 7) }) },
              t("views.plugins.olderVersion"),
            )
          : plugin.state === "updated"
            ? el("span", { class: "badge b-ok gallery-installed gallery-updated", title: t("views.plugins.updatedToSliceFromA", { slice: plugin.installed_commit.slice(0, 7) }) }, icon("check"), t("views.plugins.updated"))
            : el("span", { class: "badge b-ok gallery-installed" }, icon("check"), t("views.plugins.installed"));
      let revertButton = null;
      if (plugin.revertible) {
        const revertLabel = t("views.plugins.putBackTheEarlierVersion2", { name: plugin.name });
        revertButton = wide
          ? el("button", { class: "btn sm", type: "button", disabled: removing }, icon("undo"), t("views.plugins.revert"))
          : el("button", { class: "btn sm ghost icon-only gallery-revert", type: "button", title: revertLabel, "aria-label": revertLabel, disabled: removing }, icon("undo"));
        revertButton.addEventListener("click", () => attempt(() => revert(plugin)));
      }
      return el("div", { class: "gallery-action" }, badge, revertButton, button);
    }
    if (plugin.state === "taken") {
      return el(
        "div",
        { class: "gallery-action" },
        el("span", { class: "badge b-muted", title: t("views.plugins.dotHasAPluginNamed", { id: plugin.id }) }, t("views.plugins.nameTaken")),
      );
    }
    const installing = busy.has(plugin.id);
    const button = el(
      "button",
      { class: wide ? "btn sm primary gallery-install" : "btn sm gallery-install", type: "button", disabled: installing, "aria-label": t("views.plugins.installName", { name: plugin.name }) },
      installing ? [el("span", { class: "spinner", "aria-hidden": "true" }), t("views.plugins.installing")] : [icon("download"), t("views.plugins.install")],
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
        el("div", { class: "gallery-card-head" }, el("a", { class: "gallery-card-name", href: `#/settings/plugins/${plugin.id}` }, plugin.name), plugin.official ? verified(plugin) : null),
        el("div", { class: "gallery-card-by" }, ...joined([plugin.publisher, CATEGORIES[plugin.category]?.label ?? null, tCount(plugin.skills.length, "views.plugins.countSkill", "views.plugins.countSkills")])),
        el("p", { class: "gallery-card-does" }, plugin.does),
      ),
      action(plugin, false),
    );
  }

  /** A plugin Dot reads that is not the gallery's: its name, where it is, its servers and what it says of itself, and
   * Remove for one in Dot's own plugins folder (a folder of the config's is taken out of the config). */
  function otherCard(other) {
    const mark = el("span", { class: "plugin-mark", "aria-hidden": "true" }, icon("puzzle"));
    let removeButton = null;
    if (other.own) {
      const label = t("views.plugins.removeName", { name: other.name });
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
          ...joined([other.own ? t("views.plugins.inDotSPluginsFolder") : t("views.plugins.fromAFolderInDot"), other.servers > 0 ? tCount(other.servers, "views.plugins.countConnectedApp", "views.plugins.countConnectedApps") : null]),
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

  function paintCounts() {
    const [allButton, featuredButton, installedButton] = filters.element.children;
    const installed = gallery.plugins.filter(isInstalled).length + gallery.others.length;
    allButton.textContent = t("views.plugins.allLength", { length: gallery.plugins.length });
    featuredButton.textContent = t("views.plugins.featuredLength", { length: gallery.plugins.filter((plugin) => plugin.featured).length });
    installedButton.textContent = t("views.plugins.installedInstalled", { installed });
  }

  function paintList() {
    paintCounts();
    featured.hidden = words !== "" || shown !== "all";
    paintScout();
    featuredGrid.replaceChildren(...gallery.plugins.filter((plugin) => plugin.featured).map(card));
    const showing = ordered(
      gallery.plugins.filter((plugin) => (shown === "all" || (shown === "featured" && plugin.featured) || (shown === "installed" && isInstalled(plugin))) && matches(plugin)),
    );
    const others =
      shown === "installed" ? gallery.others.filter((other) => words === "" || foldSearch(`${other.name} ${other.description ?? ""}`).includes(words)) : [];
    const parts = showing.map(card);
    if (others.length > 0) {
      parts.push(el("h3", { class: "section-label gallery-others-title" }, t("views.plugins.notFromTheGallery")), ...others.map(otherCard));
    }
    if (parts.length === 0) {
      parts.push(
        words !== ""
          ? emptyPanel("search", t("views.plugins.noPluginMatchesTheSearch"), "")
          : emptyPanel("puzzle", t("views.plugins.noPluginInstalledYet"), t("views.plugins.installOneFromAllAnd")),
      );
    }
    allGrid.replaceChildren(...parts);
    holder.hidden = true;
    body.hidden = false;
  }

  function paintPlugin(plugin) {
    ctx.setTitle(t("views.plugins.namePlugins", { name: plugin.name }));
    const kind = CATEGORIES[plugin.category]?.label ?? null;
    const facts = [
      [t("views.plugins.source"), el("a", { href: plugin.homepage, target: "_blank", rel: "noopener noreferrer" }, t("views.plugins.githubComRepository", { repository: plugin.repository }), icon("external"))],
      [t("views.plugins.versionChecked"), el("span", { class: "mono" }, plugin.commit.slice(0, 7))],
      ...(plugin.state === "outdated" || plugin.state === "updated" ? [[t("views.plugins.installedFrom"), el("span", { class: "mono" }, plugin.installed_commit.slice(0, 7))]] : []),
      [t("views.plugins.licence"), plugin.licence],
      [t("views.plugins.stars"), t("views.plugins.compactOnGithub", { compact: compact(plugin.stars) })],
      [t("views.plugins.inTheGallerySince"), DAY.format(new Date(`${plugin.added}T00:00:00`))],
    ];
    holder.replaceChildren(
      el("div", { class: "plugin-page-bar" }, el("a", { class: "back", href: "#/settings/plugins" }, icon("arrowLeft"), t("views.plugins.plugins"))),
      el(
        "header",
        { class: "gallery-page-head", dataset: { plugin: plugin.id, state: plugin.state } },
        galleryMark(plugin),
        el(
          "div",
          { class: "gallery-card-main" },
          el("div", { class: "gallery-card-head" }, el("h2", { class: "plugin-title" }, plugin.name), plugin.official ? verified(plugin) : null),
          el("div", { class: "gallery-card-by" }, ...joined([plugin.publisher, kind, tCount(plugin.skills.length, "views.plugins.countSkill", "views.plugins.countSkills")])),
          el("p", { class: "gallery-page-does" }, plugin.does),
        ),
        action(plugin, true),
      ),
      pageCard(
        "skills",
        t("views.plugins.skillsItBrings"),
        "puzzle",
        el(
          "ul",
          { class: "gallery-skills" },
          plugin.skills.map((name) => el("li", null, icon("puzzle"), el("span", { class: "mono" }, name))),
        ),
      ),
      pageCard(
        "source",
        t("views.plugins.whereItComesFrom"),
        "shield",
        el(
          "div",
          null,
          el("dl", { class: "gallery-facts" }, facts.flatMap(([term, value]) => [el("dt", null, term), el("dd", null, value)])),
          el(
            "p",
            { class: "hint connect-hint" },
            t("views.plugins.installDownloadsThisVersionFrom"),
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
      location.replace(`${location.pathname}${location.search}#/settings/connectors/${pluginId}`);
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
  if (pluginId === undefined) {
    attempt(loadScout);
  }
  return () => {
    disposed = true;
    clearTimeout(scoutTimer);
  };
}

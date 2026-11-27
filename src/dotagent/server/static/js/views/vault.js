// Settings › Vault (item 621): the logins, cards and contact details the owner keeps for Dot to type into pages. Tabs with
// their counts (All, Logins, Cards, Contacts), a search, and a New item menu (Login, Card, Contact, Import from CSV…); each
// item is a row with its mark, its label, its site or the last four digits of its card, and Edit and Delete. The forms are
// dialogs (vaultforms.js). Nothing here holds a secret: the daemon's list has none, and a secret is asked of it only when the
// owner presses an eye in a form. The list scrolls in its card and draws its rows as it is scrolled; Select ticks rows to
// delete together (item 768), as the sidebar's Select ticks chats.

import { api } from "../api.js";
import { debounce, el, foldSearch, setText } from "../dom.js";
import { dotName } from "../face.js";
import { icon } from "../icons.js";
import { moreButton, openMenu } from "../menu.js";
import { emptyPanel, inlineError, inlineLoading, segmented, setCard, setSection } from "../panels.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";
import { itemMatches, reportWords, rowWords } from "../vaultitems.js";
import { openCardDialog, openContactDialog, openLoginDialog } from "../vaultforms.js";
import { t, tCount } from "../i18n.js";

const PAGE_SIZE = 60;
// How far under the list's end the next rows are drawn, so that a scroll does not wait on them.
const DRAW_AHEAD = "0px 0px 600px 0px";
const SEARCH_DELAY_MS = 150;
const TABS = [
  { id: "all", label: t("views.vault.all"), hint: t("views.vault.everyItem") },
  { id: "login", label: t("views.vault.logins"), hint: t("views.vault.sitesYouSignInTo") },
  { id: "card", label: t("views.vault.cards"), hint: t("views.vault.paymentCards") },
  { id: "contact", label: t("views.vault.contacts"), hint: t("views.vault.namesAddressesPhoneNumbersAnd") },
];
const KIND_MARK = { login: "key", card: "creditCard", contact: "contact" };
const KIND_NAME = { login: "login", card: "card", contact: "contact" };

/** The empty-tab line for the kind of item shown. */
function noneSaved(kind) {
  return kind === "login" ? t("views.vault.noneSaved.login") : kind === "card" ? t("views.vault.noneSaved.card") : kind === "contact" ? t("views.vault.noneSaved.contact") : t("views.vault.noneSaved.item");
}

/** One item as a row: its mark, its words, and its "…" with Edit and Delete (item 848: one action a row). */
function itemRow(item, actions) {
  const { title, sub } = rowWords(item);
  const more = moreButton({
    name: title,
    items: () => [
      { label: t("views.vault.edit"), icon: "pencil", run: () => actions.edit(item) },
      "separator",
      { label: t("views.vault.delete"), icon: "trash", danger: true, run: () => actions.remove(item) },
    ],
  });
  return el(
    "div",
    { class: "vault-row", dataset: { vaultItem: item.id, kind: item.kind } },
    el("span", { class: `vault-mark vault-mark-${item.kind}`, "aria-hidden": "true" }, icon(KIND_MARK[item.kind])),
    el(
      "div",
      { class: "vault-main" },
      el("div", { class: "vault-title" }, title),
      el("div", { class: "vault-sub" }, sub),
      item.kind === "card" && Object.keys(item.fields).length > 0 ? el("div", { class: "vault-sub" }, t("views.vault.withABillingAddress")) : null,
    ),
    el("div", { class: "vault-actions" }, more),
  );
}

/** An item as a row to tick while items are chosen to delete: the whole row is the tick's label, so what is in it is
 * phrasing content (spans). */
function pickRow(item, checked) {
  const { title, sub } = rowWords(item);
  return el(
    "label",
    { class: "vault-row vault-pick", dataset: { vaultItem: item.id, kind: item.kind } },
    el("input", { class: "vault-check", type: "checkbox", checked, "aria-label": sub === "" ? title : `${title}, ${sub}`, dataset: { pick: item.id } }),
    el("span", { class: `vault-mark vault-mark-${item.kind}`, "aria-hidden": "true" }, icon(KIND_MARK[item.kind])),
    el("span", { class: "vault-main" }, el("span", { class: "vault-title" }, title), el("span", { class: "vault-sub" }, sub)),
  );
}

export function mount(root, ctx) {
  let disposed = false;
  let ticket = 0;
  let items = [];
  let counts = { login: 0, card: 0, contact: 0 };
  let kind = "all";
  let shown = PAGE_SIZE;
  // What the tab and the search let through, as last painted: the rows drawn are its first `shown`.
  let current = [];
  let selecting = false;
  let deleting = false;
  // The ticked items, and the row ticked last, from which a shift-click ticks a run of rows.
  const selected = new Set();
  let anchor = null;
  let shiftHeld = false;

  const search = el("input", {
    class: "input",
    type: "search",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "200",
    placeholder: t("views.vault.searchTheVault"),
    "aria-label": t("views.vault.searchTheVault"),
  });
  const holder = el("div", null, inlineLoading());
  const list = el("div", { class: "vault-rows" });
  const end = el("div", { class: "vault-end", "aria-hidden": "true" });
  const selectButton = el("button", { class: "btn", type: "button", hidden: true }, t("views.vault.select"));
  const count = el("span", { class: "vault-select-count", role: "status" });
  const allButton = el("button", { class: "btn sm", type: "button" }, t("views.vault.selectAll"));
  const deleteButton = el("button", { class: "btn danger solid sm", type: "button" }, icon("trash"), t("views.vault.delete"));
  const cancelButton = el(
    "button",
    { class: "icon-btn", type: "button", title: t("views.vault.stopChoosing"), "aria-label": t("views.vault.stopChoosing") },
    icon("x"),
  );
  const bar = el("div", { class: "vault-select-bar", hidden: true }, count, el("div", { class: "vault-select-tools" }, allButton, deleteButton, cancelButton));
  const report = el("div", { class: "note vault-report", role: "status", hidden: true });
  const newItem = el("button", { class: "btn primary", type: "button", "aria-haspopup": "menu", "aria-expanded": "false" }, icon("plus"), t("views.vault.newItem"));
  const tabs = segmented(TABS, kind, (id) => {
    kind = id;
    shown = PAGE_SIZE;
    paint(false);
  });
  tabs.element.setAttribute("aria-label", t("views.vault.show"));
  tabs.element.classList.add("vault-tabs");
  const tabButtons = [...tabs.element.querySelectorAll("button")];

  function wanted() {
    const words = foldSearch(search.value).split(/\s+/).filter((word) => word !== "");
    return items.filter((item) => (kind === "all" || item.kind === kind) && itemMatches(item, words));
  }

  function paintCounts() {
    const total = counts.login + counts.card + counts.contact;
    const by = { all: total, ...counts };
    TABS.forEach((tab, index) => {
      tabButtons[index].replaceChildren(tab.label, " ", el("span", { class: "vault-count" }, String(by[tab.id])));
      tabButtons[index].setAttribute("aria-label", `${tab.label}, ${by[tab.id]}`);
    });
  }

  /** Draw the next rows of the list under those drawn. */
  function drawMore() {
    if (shown >= current.length) {
      return;
    }
    const next = current.slice(shown, shown + PAGE_SIZE);
    shown += next.length;
    end.before(...next.map(rowOf));
  }

  function rowOf(item) {
    return selecting ? pickRow(item, selected.has(item.id)) : itemRow(item, rowActions);
  }

  // The end of the list in sight, or near it, draws the next rows; it is watched again after, as it may still be in sight.
  const ahead = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting) && shown < current.length) {
        drawMore();
        ahead.unobserve(end);
        ahead.observe(end);
      }
    },
    { root: list, rootMargin: DRAW_AHEAD },
  );
  ahead.observe(end);

  function paintBar() {
    bar.hidden = !selecting;
    const all = current.length > 0 && current.every((item) => selected.has(item.id));
    setText(count, selected.size === 0 ? t("views.vault.chooseItemsToDelete") : t("views.vault.partSelected", { part: tCount(selected.size, "views.vault.countItem", "views.vault.countItems") }));
    setText(allButton, all ? t("views.vault.clear") : t("views.vault.selectAll"));
    allButton.disabled = current.length === 0 || deleting;
    deleteButton.disabled = selected.size === 0 || deleting;
  }

  /** Paint the list: in its place (`keep`), as after a save, or from its top, as after another tab or search. */
  function paint(keep = true) {
    paintCounts();
    current = wanted();
    const passing = new Set(current.map((item) => item.id));
    // Only what is listed stays ticked: Delete removes what the owner sees ticked.
    for (const id of selected) {
      if (!passing.has(id)) {
        selected.delete(id);
      }
    }
    if (items.length === 0) {
      selecting = false;
    }
    selectButton.hidden = items.length === 0 || selecting;
    paintBar();
    if (items.length === 0) {
      holder.replaceChildren(emptyPanel("lock", t("views.vault.nothingSavedYet"), t("views.vault.addALoginACard")));
      return;
    }
    if (current.length === 0) {
      const message = search.value.trim() === "" ? noneSaved(kind) : t("views.vault.tryOtherWordsOrAnother");
      holder.replaceChildren(emptyPanel("search", t("views.vault.nothingMatches"), message));
      return;
    }
    const top = keep ? list.scrollTop : 0;
    list.replaceChildren(...current.slice(0, shown).map(rowOf), end);
    if (holder.firstChild !== list) {
      holder.replaceChildren(list);
    }
    list.scrollTop = top;
  }

  /** Tick or untick the row of `id`; with shift, every row from the one ticked last to it. */
  function pick(id, on, shift) {
    const ids = current.map((item) => item.id);
    const from = shift && anchor !== null ? ids.indexOf(anchor) : -1;
    const to = ids.indexOf(id);
    const run = from === -1 || to === -1 ? [id] : ids.slice(Math.min(from, to), Math.max(from, to) + 1);
    for (const one of run) {
      if (on) {
        selected.add(one);
      } else {
        selected.delete(one);
      }
    }
    anchor = id;
    syncTicks();
  }

  /** The ticks of the rows drawn, and the bar, as `selected` has them. */
  function syncTicks() {
    for (const box of list.querySelectorAll("input.vault-check")) {
      box.checked = selected.has(box.dataset.pick);
    }
    paintBar();
  }

  function startChoosing() {
    selecting = true;
    selected.clear();
    anchor = null;
    paint();
    (list.querySelector("input.vault-check") ?? cancelButton).focus({ preventScroll: true });
  }

  function stopChoosing() {
    selecting = false;
    selected.clear();
    anchor = null;
    paint();
    selectButton.focus({ preventScroll: true });
  }

  async function deleteSelected() {
    const chosen = items.filter((item) => selected.has(item.id));
    if (chosen.length === 0) {
      return;
    }
    const one = chosen.length === 1;
    const logins = chosen.filter((item) => item.kind === "login").length;
    const kept = chosen.some((item) => item.kind !== "contact") ? t("views.vault.andFromTheKeychain") : "";
    const signIn = logins === 0 ? "" : t(logins === chosen.length ? (one ? "views.vault.cannotSignInOne" : "views.vault.cannotSignInMany") : "views.vault.cannotSignInSome", { dotName: dotName() });
    const confirmed = await confirmDialog({
      title: t("views.vault.deletePart", { part: tCount(chosen.length, "views.vault.countItem", "views.vault.countItems") }),
      message: t(one ? "views.vault.removedOne" : "views.vault.removedMany", { kept, signIn }),
      confirmLabel: t("views.vault.delete"),
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    deleting = true;
    paintBar();
    try {
      const done = await api.post("/vault/items/remove", { ids: chosen.map((item) => item.id) });
      if (done.stopped === null) {
        toast("ok", t("views.vault.deletedPart", { part: tCount(done.removed, "views.vault.countItem", "views.vault.countItems") }));
        selecting = false;
        selected.clear();
      } else {
        toast("error", t("views.vault.deletedPartTheOthersAre", { part: tCount(done.removed, "views.vault.countItem", "views.vault.countItems"), stopped: done.stopped }));
      }
    } finally {
      deleting = false;
      paintBar();
    }
    await reload();
    if (!selecting) {
      selectButton.focus({ preventScroll: true });
    }
  }

  async function reload() {
    const mine = ++ticket;
    try {
      const listed = await api.get("/vault/items");
      if (disposed || mine !== ticket) {
        return;
      }
      items = listed.items;
      counts = { login: 0, card: 0, contact: 0, ...listed.counts };
      paint();
    } catch (error) {
      if (disposed || mine !== ticket) {
        return;
      }
      console.error("the vault could not be loaded", error);
      holder.replaceChildren(inlineError(error, () => attempt(reload)));
    }
  }

  const saved = () => attempt(reload);

  const rowActions = {
    edit: (item) => {
      const open = { login: openLoginDialog, card: openCardDialog, contact: openContactDialog }[item.kind];
      // The list is drawn again once the item is saved: the row's own Edit is found anew.
      open({ existing: item, returnFocus: () => root.querySelector(`[data-vault-item="${item.id}"] .row-more`), done: saved });
    },
    remove: (item) =>
      attempt(async () => {
        const { title } = rowWords(item);
        const confirmed = await confirmDialog({
          title: t("views.vault.deleteThisValue", { value: KIND_NAME[item.kind] }),
          message:
            item.kind === "contact"
              ? t("views.vault.titleIsRemovedFromThe", { title })
              : t("views.vault.titleRemovedAndKeychain", { title, signIn: item.kind === "login" ? t("views.vault.dotnameCanNoLongerSign", { dotName: dotName() }) : "" }),
          confirmLabel: t("views.vault.delete"),
          danger: true,
          focusCancel: true,
        });
        if (!confirmed) {
          return;
        }
        await api.del(`/vault/items/${encodeURIComponent(item.id)}`);
        toast("ok", t("views.vault.deleted"));
        await reload();
      }),
  };

  /** The dialog that takes a CSV export of a password manager. */
  function openImport() {
    const picked = el("input", { class: "input", id: "vault-csv", type: "file", accept: ".csv,text/csv,text/plain" });
    return formDialog({
      title: t("views.vault.importLoginsFromACsv"),
      description:
        t("views.vault.exportYourLoginsFromChrome"),
      body: [
        el("div", { class: "field" }, el("label", { class: "field-label", for: "vault-csv" }, t("views.vault.csvFile")), picked),
        el(
          "p",
          { class: "hint" },
          t("views.vault.theFileHoldsYourPasswords"),
        ),
      ],
      submitLabel: t("views.vault.import"),
      valid: () => picked.files !== null && picked.files.length > 0,
      returnFocus: newItem,
      onSubmit: async () => {
        const file = picked.files?.[0];
        if (file === undefined) {
          return false;
        }
        const done = await api.post("/vault/items/import", { csv: await file.text() });
        picked.value = "";
        report.replaceChildren(
          icon(done.stopped === null ? "check" : "alert"),
          el("div", { class: "vault-report-lines" }, reportWords(done).map((line) => el("p", null, line))),
          el("button", { class: "btn sm", type: "button" }, t("views.vault.gotIt")),
        );
        report.hidden = false;
        toast("ok", t("views.vault.importedPart", { part: tCount(done.imported, "views.vault.countLogin", "views.vault.countLogins") }));
        await reload();
        return true;
      },
    });
  }

  newItem.addEventListener("click", () => {
    const box = newItem.getBoundingClientRect();
    openMenu({
      label: t("views.vault.newItem"),
      at: { x: box.left, y: box.bottom + 4 },
      back: newItem,
      button: newItem,
      items: [
        { label: t("views.vault.login"), icon: "key", run: () => openLoginDialog({ existing: null, returnFocus: newItem, done: saved }) },
        { label: t("views.vault.card"), icon: "creditCard", run: () => openCardDialog({ existing: null, returnFocus: newItem, done: saved }) },
        { label: t("views.vault.contact"), icon: "contact", run: () => openContactDialog({ existing: null, returnFocus: newItem, done: saved }) },
        "separator",
        { label: t("views.vault.importFromCsv"), icon: "upload", run: openImport },
      ],
    });
  });

  const reloadSoon = debounce(() => {
    if (!disposed) {
      shown = PAGE_SIZE;
      paint(false);
    }
  }, SEARCH_DELAY_MS);
  search.addEventListener("input", reloadSoon);
  // A click on a row's words reaches its tick as a second click: whether shift was held is taken from the first.
  list.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const box = target?.closest("input.vault-check") ?? null;
    if (box === null) {
      shiftHeld = event.shiftKey && target?.closest(".vault-pick") !== null;
      return;
    }
    const shift = event.shiftKey || shiftHeld;
    shiftHeld = false;
    pick(box.dataset.pick, box.checked, shift);
  });
  selectButton.addEventListener("click", startChoosing);
  cancelButton.addEventListener("click", stopChoosing);
  allButton.addEventListener("click", () => {
    const all = current.every((item) => selected.has(item.id));
    for (const item of current) {
      if (all) {
        selected.delete(item.id);
      } else {
        selected.add(item.id);
      }
    }
    syncTicks();
  });
  deleteButton.addEventListener("click", () => attempt(deleteSelected));
  report.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("button") !== null) {
      report.hidden = true;
    }
  });

  ctx.head.setLead(
    t("views.vault.loginsCardsAndContactDetails", { dotName: dotName() }),
  );
  ctx.head.setActions(selectButton, newItem);
  root.append(
    el(
      "div",
      { class: "set-sections vault-page" },
      report,
      setSection({
        title: t("views.vault.savedItems"),
        actions: el("div", { class: "note-tools vault-tools" }, tabs.element, el("label", { class: "note-search" }, icon("search"), search)),
        body: setCard(bar, holder),
      }),
      el(
        "p",
        { class: "vault-note hint" },
        icon("shield"),
        t("views.vault.dotnameNeverSeesAPassword", { dotName: dotName(), dotName2: dotName() }),
      ),
    ),
  );
  attempt(reload);
  return () => {
    disposed = true;
    ahead.disconnect();
  };
}

// Settings › Vault (item 621): the logins, cards and contact details the owner keeps for Dot to type into pages. Tabs with
// their counts (All, Logins, Cards, Contacts), a search, and a New item menu (Login, Card, Contact, Import from CSV…); each
// item is a row with its mark, its label, its site or the last four digits of its card, and Edit and Delete. The forms are
// dialogs (vaultforms.js). Nothing here holds a secret: the daemon's list has none, and a secret is asked of it only when the
// owner presses an eye in a form.

import { api } from "../api.js";
import { debounce, el, foldSearch, plural } from "../dom.js";
import { dotName } from "../face.js";
import { icon } from "../icons.js";
import { openMenu } from "../menu.js";
import { emptyPanel, inlineError, inlineLoading, segmented, setCard, setSection } from "../panels.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";
import { itemMatches, reportWords, rowWords } from "../vaultitems.js";
import { openCardDialog, openContactDialog, openLoginDialog } from "../vaultforms.js";

const PAGE_SIZE = 60;
const SEARCH_DELAY_MS = 150;
const TABS = [
  { id: "all", label: "All", hint: "Every item" },
  { id: "login", label: "Logins", hint: "Sites you sign in to" },
  { id: "card", label: "Cards", hint: "Payment cards" },
  { id: "contact", label: "Contacts", hint: "Names, addresses, phone numbers and emails" },
];
const KIND_MARK = { login: "key", card: "creditCard", contact: "contact" };
const KIND_NAME = { login: "login", card: "card", contact: "contact" };

/** One item as a row: its mark, its words, and Edit and Delete. */
function itemRow(item, actions) {
  const { title, sub } = rowWords(item);
  const edit = el(
    "button",
    { class: "btn ghost sm icon-only", type: "button", title: `Edit ${title}`, "aria-label": `Edit ${title}`, "aria-haspopup": "dialog" },
    icon("pencil"),
  );
  const remove = el(
    "button",
    { class: "btn ghost sm icon-only", type: "button", title: `Delete ${title}`, "aria-label": `Delete ${title}` },
    icon("trash"),
  );
  edit.addEventListener("click", () => actions.edit(item));
  remove.addEventListener("click", () => actions.remove(item));
  return el(
    "div",
    { class: "vault-row", dataset: { vaultItem: item.id, kind: item.kind } },
    el("span", { class: `vault-mark vault-mark-${item.kind}`, "aria-hidden": "true" }, icon(KIND_MARK[item.kind])),
    el(
      "div",
      { class: "vault-main" },
      el("div", { class: "vault-title" }, title),
      el("div", { class: "vault-sub" }, sub),
      item.kind === "card" && Object.keys(item.fields).length > 0 ? el("div", { class: "vault-sub" }, "With a billing address") : null,
    ),
    el("div", { class: "vault-actions" }, edit, remove),
  );
}

export function mount(root, ctx) {
  let disposed = false;
  let ticket = 0;
  let items = [];
  let counts = { login: 0, card: 0, contact: 0 };
  let kind = "all";
  let shown = PAGE_SIZE;

  const search = el("input", {
    class: "input",
    type: "search",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "200",
    placeholder: "Search the vault",
    "aria-label": "Search the vault",
  });
  const holder = el("div", null, inlineLoading());
  const more = el("button", { class: "note-more", type: "button", hidden: true }, "Show more");
  const report = el("div", { class: "note vault-report", role: "status", hidden: true });
  const newItem = el("button", { class: "btn primary", type: "button", "aria-haspopup": "menu", "aria-expanded": "false" }, icon("plus"), "New item");
  const tabs = segmented(TABS, kind, (id) => {
    kind = id;
    shown = PAGE_SIZE;
    paint();
  });
  tabs.element.setAttribute("aria-label", "Show");
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

  function paint() {
    paintCounts();
    const found = wanted();
    if (items.length === 0) {
      holder.replaceChildren(emptyPanel("lock", "Nothing saved yet", "Add a login, a card or contact details with New item."));
      more.hidden = true;
      return;
    }
    if (found.length === 0) {
      const message = search.value.trim() === "" ? `No ${KIND_NAME[kind] ?? "item"}s saved yet.` : "Try other words, or another tab.";
      holder.replaceChildren(emptyPanel("search", "Nothing matches", message));
      more.hidden = true;
      return;
    }
    holder.replaceChildren(el("div", { class: "vault-rows" }, found.slice(0, shown).map((item) => itemRow(item, rowActions))));
    more.hidden = found.length <= shown;
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
      open({ existing: item, returnFocus: () => root.querySelector(`[data-vault-item="${item.id}"] [aria-haspopup="dialog"]`), done: saved });
    },
    remove: (item) =>
      attempt(async () => {
        const { title } = rowWords(item);
        const confirmed = await confirmDialog({
          title: `Delete this ${KIND_NAME[item.kind]}?`,
          message:
            item.kind === "contact"
              ? `${title} is removed from the vault.`
              : `${title} is removed from the vault and from the Keychain.${item.kind === "login" ? ` ${dotName()} can no longer sign in with it.` : ""}`,
          confirmLabel: "Delete",
          danger: true,
          focusCancel: true,
        });
        if (!confirmed) {
          return;
        }
        await api.del(`/vault/items/${encodeURIComponent(item.id)}`);
        toast("ok", "Deleted.");
        await reload();
      }),
  };

  /** The dialog that takes a CSV export of a password manager. */
  function openImport() {
    const picked = el("input", { class: "input", id: "vault-csv", type: "file", accept: ".csv,text/csv,text/plain" });
    return formDialog({
      title: "Import logins from a CSV file",
      description:
        "Export your logins from Chrome, Safari, Firefox, 1Password or Bitwarden as CSV, then choose the file. It is read here and kept nowhere.",
      body: [
        el("div", { class: "field" }, el("label", { class: "field-label", for: "vault-csv" }, "CSV file"), picked),
        el(
          "p",
          { class: "hint" },
          "The file holds your passwords in plain text: delete it once it is imported. Cards, notes and one-time codes are left out.",
        ),
      ],
      submitLabel: "Import",
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
          el("button", { class: "btn sm", type: "button" }, "Got it"),
        );
        report.hidden = false;
        toast("ok", `Imported ${plural(done.imported, "login", "logins")}.`);
        await reload();
        return true;
      },
    });
  }

  newItem.addEventListener("click", () => {
    const box = newItem.getBoundingClientRect();
    openMenu({
      label: "New item",
      at: { x: box.left, y: box.bottom + 4 },
      back: newItem,
      button: newItem,
      items: [
        { label: "Login", icon: "key", run: () => openLoginDialog({ existing: null, returnFocus: newItem, done: saved }) },
        { label: "Card", icon: "creditCard", run: () => openCardDialog({ existing: null, returnFocus: newItem, done: saved }) },
        { label: "Contact", icon: "contact", run: () => openContactDialog({ existing: null, returnFocus: newItem, done: saved }) },
        "separator",
        { label: "Import from CSV…", icon: "upload", run: openImport },
      ],
    });
  });

  const reloadSoon = debounce(() => {
    if (!disposed) {
      shown = PAGE_SIZE;
      paint();
    }
  }, SEARCH_DELAY_MS);
  search.addEventListener("input", reloadSoon);
  more.addEventListener("click", () => {
    shown += PAGE_SIZE;
    paint();
  });
  report.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("button") !== null) {
      report.hidden = true;
    }
  });

  ctx.head.setLead(
    `Logins, cards and contact details you keep for ${dotName()}. It can sign in with a login and type your contact details where a form asks. Passwords and cards are kept in your Mac's Keychain.`,
  );
  ctx.head.setActions(newItem);
  root.append(
    el(
      "div",
      { class: "set-sections vault-page" },
      report,
      setSection({
        title: "Saved items",
        actions: el("div", { class: "note-tools vault-tools" }, tabs.element, el("label", { class: "note-search" }, icon("search"), search)),
        body: setCard(holder, el("div", { class: "note-foot" }, more)),
      }),
      el(
        "p",
        { class: "vault-note hint" },
        icon("shield"),
        `${dotName()} never sees a password, a saved card's number, its expiry or its security code. It types a login into a page itself, and asks you first. It can read your contact details, to type them for you. A card is only kept here for now: ${dotName()} does not fill it into a page.`,
      ),
    ),
  );
  attempt(reload);
  return () => {
    disposed = true;
  };
}

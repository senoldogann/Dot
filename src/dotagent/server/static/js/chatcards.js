// Versioned data from show_cards. All labels and post text go into text nodes.
import { imageUrl } from "./api.js";
import { copyText, el } from "./dom.js";
import { cardChart } from "./chatcardchart.js";
import { t } from "./i18n.js";
import { PAGE_ANSWER_EVENT } from "./pageframe.js";
import { attempt, toast } from "./ui.js";

export const POST_DRAFT_SETTLED = "post_draft_settled";

/** @typedef {{name: string, alt: string}} Media */
/** @typedef {{kind: "post_draft", platform: string, account: string, text: string, media: Media[]}} PostDraft */
/** @typedef {{kind: "image_gallery", title: string, images: Media[]}} Gallery */
/** @typedef {{kind: "table", title: string, columns: string[], rows: string[][]}} Table */
/** @typedef {{kind: "choice", prompt: string, options: string[]}} Choice */
/** @typedef {import("./chatcardchart.js").Chart} Chart */
/** @typedef {PostDraft | Gallery | Table | Choice | Chart} Card */
/** @typedef {{status: "cancelled"} | {status: "published", text: string, url: string}} DraftOutcome */
/** @typedef {{id: number, run_id: string, data: {version: number, cards: Card[]}}} CardsEvent */
/** @typedef {{run_id: string, data: {cards_event_id: number, card_index: number, status: string, text?: string, url?: string}}} DraftOutcomeEvent */

/** @param {string} label @param {() => void} action */
function button(label, action) {
  return el("button", { class: "btn", type: "button", onclick: action }, label);
}

/** @param {string[]} columns @param {(string | number)[][]} rows */
function cardTable(columns, rows) {
  return el(
    "div",
    { class: "chat-card-table" },
    el(
      "table",
      null,
      el("thead", null, el("tr", null, columns.map((column) => el("th", { scope: "col" }, column)))),
      el("tbody", null, rows.map((row) => el("tr", null, row.map((cell) => el("td", null, String(cell)))))),
    ),
  );
}

/** @param {Media[]} images */
function mediaRow(images) {
  return el(
    "div",
    { class: "chat-card-media" },
    images.map(({ name, alt }) => {
      const image = el("img", { alt, decoding: "async" });
      const caption = el("figcaption", null, alt);
      const figure = el("figure", null, image, caption);
      if (/^[0-9a-f]{24}\.(png|jpg|webp|gif)$/.test(name)) {
        imageUrl(name).then(
          (url) => image.setAttribute("src", url),
          () => {
            image.remove();
            caption.textContent = t("cards.imageUnavailable", { alt });
          },
        );
      }
      return figure;
    }),
  );
}

/** @param {PostDraft} card */
function draft(card) {
  const { platform, account, media } = card;
  const initial = Array.from(account.replace(/^@/, ""))[0]?.toUpperCase() ?? "@";
  const body = el("p", { class: "chat-draft-text" }, card.text);
  const title = el("strong", null, t("cards.post"));
  const avatar = el("span", { class: "chat-draft-avatar", "aria-hidden": "true" }, initial);
  const copy = button(t("cards.copy"), () => attempt(async () => {
    await copyText(body.textContent);
    toast("ok", t("cards.postCopied"));
  }));
  const editor = /** @type {HTMLTextAreaElement} */ (el("textarea", { class: "textarea", rows: "5", maxlength: "5000", "aria-label": t("cards.postText"), hidden: true }));
  const header = el(
    "div",
    { class: "chat-draft-head" },
    avatar,
    el("div", null, title, el("div", { class: "muted" }, `${platform} · ${account}`)),
    copy,
  );
  const cancel = button(t("cards.cancel"), () => paintCancelled());
  const edit = button(t("cards.edit"), () => {
    editor.value = body.textContent;
    body.hidden = true;
    editor.hidden = false;
    actions.hidden = true;
    editing.hidden = false;
    editor.focus();
  });
  const prepare = media.length === 0
    ? button(t("cards.prepare"), () => {
        const text = t("cards.publishRequest", { data: JSON.stringify({ platform, account, text: body.textContent }, null, 2) });
        if (window.dispatchEvent(new CustomEvent(PAGE_ANSWER_EVENT, { cancelable: true, detail: { text, preserveDraft: true } }))) {
          toast("error", t("cards.openChatPrepare"));
        }
      })
    : null;
  const endEdit = () => {
    body.hidden = false;
    editor.hidden = true;
    actions.hidden = false;
    editing.hidden = true;
    edit.focus();
  };
  const editing = el("div", { class: "chat-card-actions", hidden: true },
    button(t("cards.cancelEdit"), endEdit),
    button(t("cards.saveEdit"), () => {
      if (editor.value.trim() === "") {
        toast("error", t("cards.enterText"));
        return;
      }
      body.textContent = editor.value;
      endEdit();
    }),
  );
  const actions = el("div", { class: "chat-card-actions" }, cancel, edit, prepare);
  const node = el("article", { class: "chat-card chat-draft", "aria-label": t("cards.draftFor", { platform, account }) }, header, body, editor,
    media.length > 0 ? mediaRow(media) : null, actions, editing);
  let settled = false;

  function paintCancelled() {
    node.classList.add("chat-draft-cancelled");
    node.setAttribute("aria-label", t("cards.cancelledFor", { account }));
    title.textContent = t("cards.cancelled");
    copy.remove();
    actions.remove();
    editing.remove();
    editor.remove();
    body.hidden = false;
  }

  /** @param {DraftOutcome} outcome */
  function settle(outcome) {
    if (settled) {
      return;
    }
    if (outcome.status === "cancelled") {
      paintCancelled();
    } else {
      if (platform.toLowerCase() !== "x") {
        return;
      }
      let url;
      try {
        url = new URL(outcome.url);
      } catch (error) {
        if (error instanceof TypeError) {
          return;
        }
        throw error;
      }
      if (url.origin !== "https://x.com" || url.username !== "" || url.password !== "" ||
          !/^\/[a-z0-9_]{1,15}\/status\/\d+$/i.test(url.pathname) || url.search !== "" || url.hash !== "") {
        return;
      }
      const flat = outcome.text.split(/\s+/).filter(Boolean).join(" ");
      const characters = Array.from(flat);
      const preview = characters.length > 52 ? `${characters.slice(0, 51).join("")}…` : flat;
      node.classList.remove("chat-draft-cancelled");
      node.setAttribute("aria-label", t("cards.sharedFor", { account }));
      node.replaceChildren(el(
        "details",
        { class: "chat-draft-shared" },
        el("summary", { class: "chat-draft-head" }, avatar, el("strong", null, t("cards.shared", { preview }))),
        el("p", { class: "chat-draft-text" }, outcome.text),
        el("a", { class: "btn", href: url.href, target: "_blank", rel: "noopener noreferrer" }, t("cards.openOnX")),
      ));
    }
    settled = true;
  }

  return { node, settle };
}

/** @param {Choice} card */
function choice(card) {
  const options = card.options.map((text) => button(text, () => {
    if (window.dispatchEvent(new CustomEvent(PAGE_ANSWER_EVENT, { cancelable: true, detail: { text, preserveDraft: true } }))) {
      toast("error", t("cards.openChatChoice"));
    }
  }));
  return el("section", { class: "chat-card" }, el("h3", null, card.prompt), el("div", { class: "chat-card-actions" }, options));
}

/** @param {Card} card */
function block(card) {
  switch (card.kind) {
    case "chart":
      return cardChart(card, cardTable);
    case "table":
      return el("section", { class: "chat-card" }, el("h3", null, card.title), cardTable(card.columns, card.rows));
    case "choice":
      return choice(card);
    case "image_gallery":
      return el("section", { class: "chat-card" }, el("h3", null, card.title), mediaRow(card.images));
  }
}

/** @param {CardsEvent} record @param {(index: number, settle: (outcome: DraftOutcome) => void) => void} register */
function chatCards(record, register) {
  const { data } = record;
  if (data.version !== 1 || !Array.isArray(data.cards)) {
    return null;
  }
  const nodes = el("div", { class: "chat-cards" });
  let drafts = /** @type {HTMLElement | null} */ (null);
  for (const [index, card] of data.cards.entries()) {
    if (card.kind === "post_draft") {
      if (drafts === null) {
        drafts = el("div", { class: "chat-draft-carousel", role: "group", "aria-label": t("cards.postDrafts") });
        nodes.append(drafts);
      }
      const rendered = draft(card);
      register(index, rendered.settle);
      drafts.append(rendered.node);
    } else {
      drafts = null;
      const node = block(card);
      if (node !== undefined) {
        nodes.append(node);
      }
    }
  }
  return nodes;
}

export function chatCardRenderer() {
  /** @type {Map<string, (outcome: DraftOutcome) => void>} */
  const drafts = new Map();
  return {
    /** @param {CardsEvent} record */
    show(record) {
      return chatCards(record, (index, settle) => drafts.set(`${record.run_id}:${record.id}:${index}`, settle));
    },
    /** @param {DraftOutcomeEvent} record */
    settle(record) {
      const { cards_event_id: id, card_index: index, status, text, url } = record.data;
      if (!Number.isSafeInteger(id) || id < 1 || !Number.isSafeInteger(index) || index < 0) {
        return;
      }
      const paint = drafts.get(`${record.run_id}:${id}:${index}`);
      if (status === "cancelled") {
        paint?.({ status });
      } else if (status === "published" && typeof text === "string" && text.trim() !== "" && typeof url === "string") {
        paint?.({ status, text, url });
      }
    },
  };
}

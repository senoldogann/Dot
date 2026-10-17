// A draft a run wrote for the owner (item 314, after Grok's): a card in the conversation with the recipients and the
// words. The owner sends it as it is, changes it first (Edit) or discards it; nothing goes out before Send, and then
// the daemon hands it to Mail or Messages with what the card holds. A run goes on without waiting for it, so a card
// may be answered long after its run ended.

import { api } from "./api.js";
import { clock, el, plural, setText } from "./dom.js";
import { icon } from "./icons.js";
import { attempt, confirmDialog, toast } from "./ui.js";

const KIND = { mail: "Email", message: "Message" };
const ICON = { mail: "mail", message: "message" };
const SEND = { mail: "Send email", message: "Send message" };
const STATE = { waiting: "Draft", sending: "Sending…", sent: "Sent", discarded: "Discarded", failed: "Not sent" };
const BADGE = { waiting: "b-warn", sending: "b-muted", sent: "b-ok", discarded: "b-muted", failed: "b-bad" };
// The drafts that still take an answer: one that waits, and one whose send failed; and the answered ones.
const OPEN = new Set(["waiting", "failed"]);
const FINAL = new Set(["sent", "discarded"]);

/** The addresses in what the owner typed: separated by commas, semicolons or lines. */
function addresses(text) {
  return text
    .split(/[,;\n]/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

function field(label, value) {
  return el("div", { class: "draft-field" }, el("span", { class: "draft-label" }, label), el("span", { class: "draft-value" }, value));
}

/**
 * The card of `initial`, a draft as the daemon tells it (`draft_created`, `draft_decided`, or the answer to a press).
 * Returns `{ node, update, refresh, isOpen, picked, selectable, deliver }`: `update(draft)` paints the draft as it now
 * stands and `refresh()` reads it from the daemon first; for the bar that sends several at once, `isOpen()` says whether
 * it still takes an answer, `picked()` whether its tick is on, `selectable(on)` shows the tick and `deliver()` sends it
 * and says whether it went. `changed()` is called when the draft or its tick changes. A sent or discarded draft stays
 * so: a reading that crossed its answer cannot undo it.
 */
export function draftCard(initial, changed) {
  let draft = initial;
  let editing = false;
  let busy = false;
  let ticks = false;
  const node = el("section", { class: "draft-card", dataset: { draft: draft.id } });
  const pick = el("input", { class: "draft-pick", type: "checkbox", "aria-label": "Send with the other ticked drafts" });
  pick.addEventListener("change", () => changed());
  const toInput = el("input", { class: "input", type: "text", autocomplete: "off", spellcheck: "false", "aria-label": "To" });
  const ccInput = el("input", { class: "input", type: "text", autocomplete: "off", spellcheck: "false", "aria-label": "Cc" });
  const subjectInput = el("input", { class: "input", type: "text", autocomplete: "off", "aria-label": "Subject" });
  const bodyInput = el("textarea", { class: "textarea", rows: "8", "aria-label": "Body" });

  /** What Send sends: the form's words while it is open, the draft's otherwise. */
  function words() {
    if (!editing) {
      return { to: draft.to, cc: draft.cc, subject: draft.subject, body: draft.body };
    }
    const mail = draft.channel === "mail";
    return { to: addresses(toInput.value), cc: mail ? addresses(ccInput.value) : [], subject: mail ? subjectInput.value : "", body: bodyInput.value };
  }

  function edit() {
    toInput.value = draft.to.join(", ");
    ccInput.value = draft.cc.join(", ");
    subjectInput.value = draft.subject;
    bodyInput.value = draft.body;
    editing = true;
    paint();
    bodyInput.focus();
  }

  /** Hand the draft to the daemon with the words the card holds; the card shows what came of it. */
  async function deliver() {
    busy = true;
    paint();
    try {
      update(await api.post(`/drafts/${draft.id}/send`, words()));
    } catch (error) {
      busy = false;
      paint();
      if (error.status === 409) {
        update(await api.get(`/drafts/${draft.id}`));
      }
      throw error;
    }
    return draft.status === "sent";
  }

  async function send() {
    if (await deliver()) {
      toast("ok", `${KIND[draft.channel]} sent.`);
    }
  }

  async function discard() {
    const confirmed = await confirmDialog({
      title: "Discard this draft?",
      message: "Nothing is sent, and the draft cannot be brought back.",
      confirmLabel: "Discard",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    busy = true;
    paint();
    try {
      update(await api.post(`/drafts/${draft.id}/discard`, {}));
    } catch (error) {
      busy = false;
      paint();
      if (error.status === 409) {
        update(await api.get(`/drafts/${draft.id}`));
      }
      throw error;
    }
  }

  function head() {
    const when = draft.decided_at === null ? draft.created_at : draft.decided_at;
    return el(
      "div",
      { class: "draft-head" },
      ticks && OPEN.has(draft.status) ? pick : null,
      icon(ICON[draft.channel]),
      el("span", { class: "draft-kind" }, `${KIND[draft.channel]} draft`),
      el("span", { class: `badge ${BADGE[draft.status]}` }, busy && draft.status !== "sending" ? "Sending…" : STATE[draft.status]),
      el("span", { class: "spacer" }),
      el("span", { class: "draft-time muted" }, clock(when)),
    );
  }

  function shown() {
    const mail = draft.channel === "mail";
    return [
      el(
        "div",
        { class: "draft-fields" },
        field("To", draft.to.join(", ")),
        mail && draft.cc.length > 0 ? field("Cc", draft.cc.join(", ")) : null,
        mail ? field("Subject", draft.subject) : null,
      ),
      el("div", { class: "draft-body" }, draft.body),
    ];
  }

  function form() {
    const mail = draft.channel === "mail";
    const row = (label, input) => el("label", { class: "draft-field" }, el("span", { class: "draft-label" }, label), input);
    return [
      el("div", { class: "draft-fields" }, row("To", toInput), mail ? row("Cc", ccInput) : null, mail ? row("Subject", subjectInput) : null),
      bodyInput,
    ];
  }

  function actions() {
    if (!OPEN.has(draft.status)) {
      return null;
    }
    const button = (label, className, action, iconName) =>
      el("button", { class: className, type: "button", disabled: busy, onclick: () => attempt(action) }, iconName === null ? null : icon(iconName), label);
    const sendLabel = draft.status === "failed" ? "Send again" : SEND[draft.channel];
    return el(
      "div",
      { class: "draft-actions" },
      editing
        ? button("Cancel", "btn", async () => {
            editing = false;
            paint();
          }, null)
        : button("Discard", "btn", discard, null),
      editing ? null : button("Edit", "btn", async () => edit(), "pencil"),
      button(sendLabel, "btn primary draft-send", send, "send"),
    );
  }

  function paint() {
    node.dataset.status = draft.status;
    const parts = [
      head(),
      ...(editing && OPEN.has(draft.status) ? form() : shown()),
      draft.status === "failed" && draft.error !== null ? el("div", { class: "draft-error" }, icon("alert"), el("span", null, draft.error)) : null,
      actions(),
    ];
    // The DOM's replaceChildren writes a null as the word "null".
    node.replaceChildren(...parts.filter((part) => part !== null));
  }

  /** Paint `next`, the same draft as it stands now; a decided draft closes the form. */
  function update(next) {
    if (FINAL.has(draft.status) && !FINAL.has(next.status)) {
      return;
    }
    draft = next;
    busy = false;
    if (!OPEN.has(draft.status)) {
      editing = false;
      pick.checked = false;
    }
    paint();
    changed();
  }

  function selectable(on) {
    if (ticks !== on) {
      ticks = on;
      pick.checked = pick.checked && on;
      paint();
    }
  }

  async function refresh() {
    try {
      update(await api.get(`/drafts/${draft.id}`));
    } catch (error) {
      console.warn("the draft could not be read again; the card shows it as it was written", error);
    }
  }

  paint();
  return {
    node,
    update,
    refresh,
    isOpen: () => OPEN.has(draft.status),
    picked: () => pick.checked,
    selectable,
    deliver,
  };
}

/**
 * The bar that sends several drafts at once (item 314, after Grok's "send the top 10"): while two or more drafts wait,
 * each card shows a tick, and the bar, after the last of them, sends the ticked ones one after the other, each with the
 * words its card holds; a card that was not sent says why. `cards()` gives the cards in the order of the conversation;
 * `paint()` puts the bar where it belongs, or takes it away.
 */
export function draftsBar(cards) {
  const count = el("span", { class: "draft-bar-count" });
  const label = el("span");
  const sendButton = el("button", { class: "btn primary draft-send", type: "button" }, icon("send"), label);
  const node = el("div", { class: "draft-bar" }, count, sendButton);

  function paint() {
    const open = cards().filter((card) => card.isOpen());
    for (const card of open) {
      card.selectable(open.length > 1);
    }
    if (open.length < 2) {
      node.remove();
      return;
    }
    const picked = open.filter((card) => card.picked());
    setText(count, picked.length === 0 ? `${open.length} drafts wait: tick the ones to send together` : `${picked.length} of ${open.length} drafts ticked`);
    setText(label, picked.length === 0 ? "Send ticked" : `Send ${picked.length}`);
    sendButton.disabled = picked.length === 0;
    const last = open[open.length - 1].node;
    if (last.nextElementSibling !== node) {
      last.after(node);
    }
  }

  async function sendTicked() {
    const picked = cards().filter((card) => card.isOpen() && card.picked());
    sendButton.disabled = true;
    const problems = [];
    let sent = 0;
    for (const card of picked) {
      try {
        sent += (await card.deliver()) ? 1 : 0;
      } catch (error) {
        problems.push(error.message);
      }
    }
    paint();
    if (sent < picked.length) {
      throw new Error(`${sent} of ${picked.length} sent; the cards say why the others were not${problems.length > 0 ? `: ${problems[0]}` : "."}`);
    }
    toast("ok", `${plural(sent, "draft", "drafts")} sent.`);
  }

  sendButton.addEventListener("click", () => attempt(sendTicked));
  return { node, paint };
}

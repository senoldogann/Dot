// Settings › Phone (item 267): the addresses a phone reaches Dot at, and a pairing code for the app added to a phone's
// home screen, which iOS keeps apart from Safari, so that it has none of the token Safari was given.

import { api } from "../api.js";
import { copyText, el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, paneHead } from "../panels.js";
import { attempt, toast } from "../ui.js";

function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function addressRow(address) {
  return el(
    "div",
    { class: "browser-copy" },
    el("code", { class: "code-line" }, address),
    el(
      "button",
      {
        class: "btn sm",
        type: "button",
        onclick: () =>
          attempt(async () => {
            await copyText(address);
            toast("ok", "Address copied.");
          }),
      },
      icon("copy"),
      "Copy",
    ),
  );
}

export function mount(root, ctx) {
  let disposed = false;
  const holder = el("div", null, loadingPanel());
  const code = el("output", { class: "pair-code mono", "aria-label": "Pairing code", hidden: true });
  const codeHint = el("p", { class: "hint", hidden: true });
  const pairButton = el("button", { class: "btn primary", type: "button", onclick: () => attempt(pair) }, icon("smartphone"), "Pair a phone");

  async function pair() {
    pairButton.disabled = true;
    let issued;
    try {
      issued = await api.post("/phone/pairing", {});
    } finally {
      pairButton.disabled = false;
    }
    if (disposed) {
      return;
    }
    code.textContent = issued.code;
    code.hidden = false;
    codeHint.textContent = `Type it in Dot on the phone's home screen before ${clockTime(issued.expires_at)}. It works once, and a new code takes its place.`;
    codeHint.hidden = false;
  }

  function card(phone) {
    const addresses =
      phone.addresses.length > 0
        ? el("div", { class: "stack tight" }, phone.addresses.map(addressRow))
        : el(
            "div",
            { class: "note" },
            icon("info"),
            el(
              "span",
              null,
              "No address yet. A phone reaches Dot through Tailscale: serve Dot on your tailnet and write the Mac's name on it in Dot's config file, as “Dot on the phone” in Dot's documentation (docs/phone.md) says. ",
              el("span", { class: "faint" }, "In the config file: [server] reach."),
            ),
          );
    return el(
      "section",
      { class: "card" },
      el("div", { class: "card-head" }, el("span", { class: "grow" }, "Dot on your phone")),
      el(
        "div",
        { class: "card-pad stack" },
        el(
          "ol",
          { class: "phone-steps" },
          el("li", null, el("p", null, "With Tailscale on the phone, open this address in Safari:"), addresses),
          el("li", null, el("p", null, "Share › Add to Home Screen.")),
          el(
            "li",
            null,
            el("p", null, "Open Dot from the home screen, or in Safari, and type a code from here:"),
            el("div", { class: "row" }, pairButton),
            code,
            codeHint,
          ),
        ),
        el("p", { class: "hint" }, "The phone reaches Dot while this Mac is on and awake."),
      ),
    );
  }

  async function load() {
    let phone;
    try {
      phone = await api.get("/phone");
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      }
      return;
    }
    if (!disposed) {
      holder.replaceChildren(card(phone));
    }
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await load();
  }

  root.append(paneHead("Dot on a phone, at home or away: its address, and a code that signs in the app on its home screen.", null), holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

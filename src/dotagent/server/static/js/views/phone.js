// Settings › Phone (item 267): the addresses a phone reaches Dot at, and a pairing code for the app added to a phone's
// home screen, which iOS keeps apart from Safari, so that it has none of the token Safari was given.

import { api } from "../api.js";
import { copyText, el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, paneHead, switchSetting } from "../panels.js";
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

// What a phone needs to reach Dot away from home through Tailscale (item 271), a line each: whether it holds, and what
// to do when it does not; then what Tailscale said when it could not be asked.
function tailnetLines(tailnet) {
  const line = (ok, text) => el("li", { class: ok ? "tailnet-ok" : "tailnet-missing" }, icon(ok ? "check" : "alert"), el("span", null, text));
  const said = tailnet.problem === null ? [] : [line(false, tailnet.problem)];
  if (!tailnet.installed) {
    return [line(false, "Tailscale is not on this Mac: install it from tailscale.com, open it and sign in, on the phone too.")];
  }
  if (!tailnet.running || tailnet.name === null) {
    return [line(false, "Tailscale is not running signed in: open it on this Mac and sign in."), ...said];
  }
  return [
    line(true, `Tailscale runs: this Mac is ${tailnet.name} on your tailnet.`),
    line(tailnet.served, tailnet.served ? "Tailscale serves Dot to your tailnet over HTTPS." : "Tailscale does not serve Dot yet."),
    line(
      tailnet.reached,
      tailnet.reached
        ? "Dot answers to that name."
        : tailnet.written
          ? "Dot's config file names it: Dot answers to it from the daemon's next start (dotx service install restarts it)."
          : "Dot's config file does not name it yet.",
    ),
    ...said,
  ];
}

export function mount(root, ctx) {
  let disposed = false;
  const holder = el("div", { class: "stack" }, loadingPanel());
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

  function reachCard(phone) {
    const tailnet = phone.tailnet;
    const missing = tailnet.installed && tailnet.running && tailnet.name !== null && (!tailnet.served || !tailnet.written);
    const setUp = el(
      "button",
      {
        class: "btn sm",
        type: "button",
        hidden: !missing,
        onclick: () =>
          attempt(async () => {
            setUp.disabled = true;
            try {
              await change(api.post("/phone/tailnet", {}), "Set up for your tailnet.");
            } finally {
              setUp.disabled = false;
            }
          }),
      },
      icon("plug"),
      el("span", { class: "label" }, "Set up"),
    );
    const awake = switchSetting({
      label: "Keep this Mac awake while it charges",
      hint: "So that the phone reaches Dot away from home. A closed lid still puts a MacBook to sleep.",
      read: () => phone.keep_awake,
      write: async (on) => {
        phone = await api.post("/phone/awake", { on });
      },
      onToggled: () => {
        if (!disposed) {
          awake.paint();
          toast("ok", phone.keep_awake ? "This Mac stays awake while it charges." : "This Mac sleeps as it would.");
        }
      },
    });
    awake.paint();
    return el(
      "section",
      { class: "card" },
      el("div", { class: "card-head" }, el("span", { class: "grow" }, "Reach Dot from anywhere"), setUp),
      el("div", { class: "card-pad stack" }, el("ul", { class: "tailnet-lines", "aria-label": "Tailscale" }, tailnetLines(tailnet)), awake.element),
    );
  }

  async function change(request, done) {
    const phone = await request;
    if (!disposed) {
      paint(phone);
      toast("ok", done);
    }
  }

  function paint(phone) {
    holder.replaceChildren(reachCard(phone), card(phone));
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
              "No address yet: Set up, above, serves Dot on your tailnet and names the Mac in Dot's config file; then restart the daemon with dotx service install. ",
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
      paint(phone);
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

// Settings › Phone (items 267, 271 and 421): a guide to Dot on a phone through Tailscale, a step at a time. Each step
// on this Mac says where it stands and offers what does it: Tailscale from the App Store or opened to sign in, Dot
// served to the tailnet, the daemon started again to take its name. Then the phone's own steps, with QR codes its
// camera opens, and a pairing code for the app added to its home screen, which iOS keeps apart from Safari.

import { api, blobUrl } from "../api.js";
import { copyText, el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, paneHead, switchSetting } from "../panels.js";
import { attempt, toast } from "../ui.js";

// How often the guide reads Tailscale again while a step is left, so that it follows an install or a sign-in.
const FOLLOW_MS = 4000;

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

/** One step of the guide: its number, its title, whether it is done, what it says, and what it offers. */
function step(number, title, done, text, ...offers) {
  return el(
    "li",
    { class: `phone-guide-step ${done ? "done" : "left"}` },
    el("span", { class: "phone-guide-mark", "aria-hidden": "true" }, done ? icon("check") : String(number)),
    el(
      "div",
      { class: "phone-guide-body" },
      el("div", { class: "phone-guide-title" }, title),
      el("p", { class: "hint" }, text),
      ...offers.filter((offer) => offer !== null),
    ),
  );
}

export function mount(root, _ctx) {
  let disposed = false;
  // True while an action of the guide waits for its answer: the guide is not read again under it.
  let busy = false;
  let following = null;
  // The page Tailscale named to turn on HTTPS for the tailnet, after a Set up it refused for want of it.
  let httpsLink = null;
  // The QR codes made so far, by what they open, as addresses of their pictures.
  const codes = new Map();
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

  /** A button of the guide that runs `action` once at a time, its errors shown; its words show on a phone too, where
   * they say what the step does. */
  function offer(label, iconName, primary, action) {
    const button = el("button", { class: `btn sm${primary ? " primary" : ""}`, type: "button" }, icon(iconName), el("span", null, label));
    button.addEventListener("click", () =>
      attempt(async () => {
        button.disabled = true;
        busy = true;
        try {
          await action();
        } finally {
          busy = false;
          button.disabled = false;
        }
      }),
    );
    return button;
  }

  async function changed(request, done) {
    const phone = await request;
    if (!disposed) {
      paint(phone);
      toast("ok", done);
    }
  }

  async function setUp() {
    try {
      await changed(api.post("/phone/tailnet", {}), "Set up for your tailnet.");
      httpsLink = null;
    } catch (error) {
      const link = error?.context?.link;
      if (typeof link === "string" && link.startsWith("https://")) {
        httpsLink = link;
        await load();
      }
      throw error;
    }
  }

  async function restart() {
    await api.post("/system/restart", null);
    toast("ok", "Dot starts again and takes the name up; this page answers again in a moment.");
  }

  function guideCard(phone) {
    const tailnet = phone.tailnet;
    const onMac = tailnet.installed && tailnet.running && tailnet.name !== null;
    const served = onMac && tailnet.served && tailnet.written;
    const said = tailnet.problem === null ? null : el("p", { class: "hint" }, `Tailscale said: ${tailnet.problem}`);
    const openTailscale = (done) => changed(api.post("/phone/tailscale", {}), done);
    const first = onMac
      ? step(1, "Tailscale on this Mac", true, `This Mac is ${tailnet.name} on your tailnet.`)
      : tailnet.installed
        ? step(
            1,
            "Tailscale on this Mac",
            false,
            "Open Tailscale and sign in, in its window or from its icon in the menu bar. This page follows.",
            said,
            offer("Open Tailscale", "external", true, () => openTailscale("Tailscale is opening.")),
          )
        : step(
            1,
            "Tailscale on this Mac",
            false,
            "Tailscale is not on this Mac yet. Get it from the App Store, open it and sign in. This page follows.",
            offer("Get Tailscale", "download", true, () => openTailscale("The App Store opens on Tailscale.")),
          );
    const https =
      httpsLink === null
        ? null
        : el("a", { class: "btn sm primary", href: httpsLink, target: "_blank", rel: "noopener noreferrer" }, icon("external"), el("span", null, "Turn on HTTPS for your tailnet"));
    const second = served
      ? step(2, "Dot on your tailnet", true, "Tailscale serves Dot to your tailnet over HTTPS, and Dot's config names this Mac.")
      : step(
          2,
          "Dot on your tailnet",
          false,
          !onMac
            ? "After step 1."
            : httpsLink === null
              ? "Set up has Tailscale serve Dot to your tailnet over HTTPS, and writes this Mac's name into Dot's config."
              : "Tailscale needs HTTPS on for your tailnet: turn it on, then Set up again.",
          https,
          onMac ? offer("Set up", "plug", httpsLink === null, setUp) : null,
        );
    const third = tailnet.reached
      ? step(3, "Dot answers to the name", true, "Dot answers to it.")
      : step(
          3,
          "Dot answers to the name",
          false,
          tailnet.written ? "Dot takes the name up when it starts again." : "After step 2.",
          tailnet.written ? offer("Restart Dot", "refresh", true, restart) : null,
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
      el("div", { class: "card-head" }, el("span", { class: "grow" }, "Reach Dot from anywhere")),
      el("div", { class: "card-pad stack" }, el("ol", { class: "phone-guide", "aria-label": "Steps on this Mac" }, first, second, third), awake.element),
    );
  }

  /** The QR code of what `code` opens (`tailscale`, `dot` at `address`), made once; an error is said in its place. */
  function qrPicture(which, address, label) {
    const key = which === "dot" ? `dot ${address}` : which;
    const picture = el("img", { class: "phone-qr", alt: label, width: "148", height: "148" });
    const made = codes.get(key);
    if (made !== undefined) {
      picture.src = made;
      return picture;
    }
    blobUrl(`/phone/qr/${which}`)
      .then((url) => {
        if (disposed) {
          URL.revokeObjectURL(url);
          return;
        }
        codes.set(key, url);
        picture.src = url;
      })
      .catch((error) => {
        picture.replaceWith(el("p", { class: "hint" }, `The QR code could not be made: ${error.message}`));
      });
    return picture;
  }

  function phoneCard(phone) {
    const address = phone.addresses[0] ?? null;
    const open =
      address === null
        ? el(
            "div",
            { class: "note" },
            icon("info"),
            el("span", null, "No address yet: the steps above give it. ", el("span", { class: "faint" }, "In the config file: [server] reach.")),
          )
        : el(
            "div",
            { class: "phone-qr-row" },
            qrPicture("dot", address, `A QR code of ${address}`),
            el("div", { class: "stack tight grow" }, el("p", { class: "hint" }, "Scan it with the phone's camera, or type it in Safari:"), phone.addresses.map(addressRow)),
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
          el(
            "li",
            null,
            el("p", null, "Install Tailscale on the phone and sign in with the account this Mac uses."),
            el(
              "div",
              { class: "phone-qr-row" },
              qrPicture("tailscale", null, "A QR code of Tailscale's download page"),
              el("p", { class: "hint grow" }, "Scan it with the phone's camera, or open tailscale.com/download on the phone."),
            ),
          ),
          el("li", null, el("p", null, "Open Dot on the phone, in Safari:"), open),
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

  /** Read Tailscale again a moment from now while a step is left: the guide follows an install, a sign-in or a start. */
  function follow(phone) {
    clearTimeout(following);
    if (disposed || phone.tailnet.reached) {
      return;
    }
    following = setTimeout(async () => {
      if (disposed) {
        return;
      }
      if (busy || document.visibilityState !== "visible") {
        follow(phone);
        return;
      }
      let next = phone;
      try {
        next = await api.get("/phone");
      } catch (error) {
        // The daemon may be starting again (Restart Dot): the guide reads it again at the next turn.
        console.warn("the phone's steps could not be read again", error);
      }
      if (!disposed && !busy) {
        paint(next);
      }
    }, FOLLOW_MS);
  }

  function paint(phone) {
    holder.replaceChildren(guideCard(phone), phoneCard(phone));
    follow(phone);
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

  root.append(paneHead("Dot on a phone, at home or away: the steps there, a step at a time, and a code that signs in the app on its home screen.", null), holder);
  attempt(load);
  return () => {
    disposed = true;
    clearTimeout(following);
    for (const url of codes.values()) {
      URL.revokeObjectURL(url);
    }
  };
}

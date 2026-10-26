// Settings › Phone (items 267, 271, 421 and 565): a guide to Dot on a phone through Tailscale, a step at a time. The card
// at the head says whether the phone reaches Dot, with the three steps on this Mac, each saying where it stands and
// offering what does it (Tailscale from the App Store or opened to sign in, Dot served to the tailnet, the daemon started
// again to take its name), and the switch that keeps the Mac awake. Under it the phone's own steps, with the QR codes its
// camera opens, and the code that signs in the app added to its home screen, which iOS keeps apart from Safari. Under
// that the phones paired with a code, each holding a key of its own, with Remove (item 670).

import { api, blobUrl, holdsPhoneKey, unreachable } from "../api.js";
import { copyText, el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, segmented, setCard, setRow, setSection, switchRowSetting } from "../panels.js";
import { getSnapshot } from "../state.js";
import { attempt, confirmDialog, settingFold, toast } from "../ui.js";

// How often the guide reads Tailscale again while a step is left, so that it follows an install or a sign-in.
const FOLLOW_MS = 4000;
// Tailscale's page for installing it on the phone, which the first QR code opens (the daemon makes the code of this
// address, `dotagent.tailnet.DOWNLOAD_PAGE`).
const TAILSCALE_PAGE = "https://tailscale.com/download";
// The phone's own steps: a title and the line under it.
const PHONE_STEPS = [
  {
    title: "Install Tailscale on the phone",
    text: "Sign in with the account this Mac uses. Scan the code with the phone's camera, or open tailscale.com/download on the phone.",
  },
  { title: "Open Dot on the phone, in Safari", text: "Scan the code with the phone's camera, or type the address." },
  { title: "Add it to the home screen", text: "Share › Add to Home Screen." },
  { title: "Sign in with a code", text: "Open Dot from the home screen, or in Safari, and type the code below." },
];
// What the one QR code can open: Tailscale's page, or Dot's address.
const QR_CHOICES = [
  { id: "tailscale", label: "Tailscale app", hint: "The code opens Tailscale's page for the phone" },
  { id: "dot", label: "Open Dot", hint: "The code opens Dot on your Tailscale network" },
];
// A pairing code is two groups of four characters: the dots that stand for it until it is shown are too.
const CODE_GROUPS = 2;
const CODE_GROUP_CHARS = 4;
const CODE_HIDDEN = "Hidden until you show it, so it isn't on screen by accident.";

function dayOf(seconds) {
  return new Date(seconds * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

function clockTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** An address in mono, in a box of its own, with the button that copies it. */
function addressRow(address) {
  return el(
    "div",
    { class: "phone-address" },
    el("code", { class: "phone-address-text" }, address),
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

/** A step's mark: a tick once it is done, else its number; the step to do now stands out in the accent. */
function mark(number, done) {
  return el("span", { class: "phone-mark", "aria-hidden": "true" }, done ? icon("check") : String(number));
}

/** One step on this Mac: its mark, its title, whether it is done, what it says, and what it offers. */
function step(number, title, done, text, ...offers) {
  return el(
    "li",
    { class: `phone-guide-step ${done ? "done" : "left"}` },
    mark(number, done),
    el(
      "div",
      { class: "phone-guide-body" },
      el("div", { class: "phone-guide-title" }, title),
      el("p", { class: "hint" }, text),
      ...offers.filter((offer) => offer !== null),
    ),
  );
}

export function mount(root, ctx) {
  let disposed = false;
  // How many actions of the guide wait for their answer: the guide is not drawn again under one.
  let busy = 0;
  let following = null;
  // The page Tailscale named to turn on HTTPS for the tailnet, after a Set up it refused for want of it.
  let httpsLink = null;
  // The QR codes asked for so far, by what they open, as the promises of their pictures' addresses; and the addresses
  // made, let go at the end.
  const codes = new Map();
  const made = [];
  // What the one QR code opens, once it was chosen (kept through every drawing of the guide); until then Dot's address
  // when there is one, else Tailscale's page.
  let which = null;
  // Whether a code was shown (the guide is then at its last step), and how to draw the phone's steps again for it.
  let codeShown = false;
  let repaintSetup = () => undefined;
  const holder = el("div", { class: "stack" }, loadingPanel());
  const code = el("output", { class: "pair-code mono", "aria-label": "Sign-in code", hidden: true });
  const codeLead = el("span", { class: "phone-code-lead" }, CODE_HIDDEN);
  const dots = el(
    "span",
    { class: "phone-dots", "aria-hidden": "true" },
    Array.from({ length: CODE_GROUPS }, () =>
      el(
        "span",
        { class: "phone-dot-group" },
        Array.from({ length: CODE_GROUP_CHARS }, () => el("span", { class: "phone-dot" })),
      ),
    ),
  );
  const pairButton = el("button", { class: "btn primary", type: "button", onclick: () => attempt(pair) }, "Show code");

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
    dots.hidden = true;
    codeLead.textContent = `Type it in Dot on the phone's home screen before ${clockTime(issued.expires_at)}. It works once, and a new code takes its place.`;
    // The code is on screen: the button now asks for another, which takes its place.
    pairButton.className = "btn";
    pairButton.textContent = "New code";
    codeShown = true;
    repaintSetup();
  }

  /** A button of the guide that runs `action` once at a time, its errors shown; its words show on a phone too, where
   * they say what the step does. */
  function offer(label, iconName, primary, action) {
    const button = el("button", { class: `btn sm${primary ? " primary" : ""}`, type: "button" }, icon(iconName), el("span", null, label));
    button.addEventListener("click", () =>
      attempt(async () => {
        button.disabled = true;
        busy += 1;
        try {
          await action();
        } finally {
          busy -= 1;
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
    let phone;
    try {
      phone = await api.post("/phone/tailnet", {});
    } catch (error) {
      // The page Tailscale named, when it named one; a refusal for another reason leaves none of an earlier one.
      const link = error?.context?.link;
      httpsLink = typeof link === "string" && link.startsWith("https://") ? link : null;
      await load();
      throw error;
    }
    httpsLink = null;
    if (!disposed) {
      paint(phone);
      toast("ok", "Set up for your Tailscale network.");
    }
  }

  async function restart() {
    // The runs going on, or null while the daemon's status is not known yet.
    const status = getSnapshot().status;
    const running = status === null ? null : status.active_runs.length;
    const stops =
      running === null
        ? " Any task going on now stops too."
        : running === 0
          ? ""
          : ` The ${running === 1 ? "task" : `${running} tasks`} going on now ${running === 1 ? "stops" : "stop"} too.`;
    const confirmed = await confirmDialog({
      title: "Restart Dot?",
      message: `Dot closes and starts again by itself, with the name of your Tailscale network.${stops}`,
      confirmLabel: "Restart",
      danger: running !== 0,
      focusCancel: running !== 0,
    });
    if (!confirmed) {
      return;
    }
    await api.post("/system/restart", null);
    toast("ok", "Dot is starting again and takes the name up; this page works again in a moment.");
  }

  /** The card at the head: whether the phone reaches Dot (its tile, its name and, while a step is left, how many are
   * done), the steps on this Mac side by side, and the switch that keeps the Mac awake. */
  function reachCard(phone) {
    const tailnet = phone.tailnet;
    const onMac = tailnet.installed && tailnet.running && tailnet.name !== null;
    const served = onMac && tailnet.served && tailnet.written;
    const said = tailnet.problem === null ? null : el("p", { class: "hint" }, `Tailscale said: ${tailnet.problem}`);
    const openTailscale = (done) => changed(api.post("/phone/tailscale", {}), done);
    const first = onMac
      ? step(1, "Tailscale on this Mac", true, `This Mac is ${tailnet.name} on your Tailscale network.`)
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
        : el("a", { class: "btn sm primary", href: httpsLink, target: "_blank", rel: "noopener noreferrer" }, icon("external"), el("span", null, "Turn on secure connections"));
    const second = served
      ? step(2, "Dot on your Tailscale network", true, "Tailscale makes Dot available on your private network, securely, and Dot remembers this Mac's name.")
      : step(
          2,
          "Dot on your Tailscale network",
          false,
          !onMac
            ? "After step 1."
            : httpsLink === null
              ? "Set up makes Dot available on your private network, securely, and saves this Mac's name."
              : "Tailscale needs secure connections turned on for your network: turn them on, then choose Set up again.",
          onMac ? said : null,
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
    const awake = switchRowSetting({
      label: "Keep this Mac awake while it charges",
      hint: "So the phone reaches Dot away from home. A closed lid still puts a MacBook to sleep.",
      read: () => phone.keep_awake,
      // The words follow the change once it is made (item 559): a switch that failed says so alone.
      write: async (on) => {
        phone = await api.post("/phone/awake", { on });
        toast("ok", phone.keep_awake ? "This Mac stays awake while it charges." : "This Mac sleeps as it would.");
      },
      onToggled: () => {
        if (!disposed) {
          awake.paint();
        }
      },
    });
    awake.paint();
    // The step to do now stands out: the first one not done.
    const done = [onMac, served, tailnet.reached];
    const now = done.indexOf(false);
    if (now >= 0) {
      [first, second, third][now].classList.add("current");
    }
    const count = done.filter(Boolean).length;
    const ready = count === 3;
    // Ready, the name the phone reaches Dot by; else how far the steps on this Mac are, in words and in a track of three.
    const name = tailnet.name ?? phone.addresses[0] ?? null;
    const head = el(
      "div",
      { class: "phone-reach-head" },
      el("span", { class: `phone-reach-tile${ready ? " ready" : ""}`, "aria-hidden": "true" }, icon(ready ? "check" : "plug")),
      el(
        "div",
        { class: "phone-reach-text" },
        el("div", { class: "phone-reach-title" }, ready ? "Ready: your phone reaches Dot" : `${count} of 3 steps done on this Mac`),
        ready && name !== null ? el("code", { class: "phone-reach-name mono" }, name) : null,
      ),
      ready
        ? null
        : el(
            "div",
            { class: "phone-progress-track", role: "progressbar", "aria-label": "Steps done on this Mac", "aria-valuemin": "0", "aria-valuemax": "3", "aria-valuenow": String(count) },
            done.map((ok, index) => el("span", { class: ok ? "phone-progress-part done" : index === now ? "phone-progress-part now" : "phone-progress-part" })),
          ),
    );
    return el(
      "section",
      { class: "set-card phone-reach", "aria-label": "Reach Dot from anywhere" },
      head,
      el("ol", { class: "phone-guide", "aria-label": "Steps on this Mac" }, first, second, third),
      awake.element,
    );
  }

  /** The QR code of what `opens` names (`tailscale`, `dot` at `address`), made once; an error is said in its place. */
  function qrPicture(opens, address, label) {
    const key = opens === "dot" ? `dot ${address}` : opens;
    const picture = el("img", { class: "phone-qr", alt: label, width: "152", height: "152" });
    let asked = codes.get(key);
    if (asked === undefined) {
      asked = blobUrl(`/phone/qr/${opens}`).then((url) => {
        // One that comes after the tab was left is let go of at once (item 559): nothing shows it, nor lets it go later.
        if (disposed) {
          URL.revokeObjectURL(url);
        } else {
          made.push(url);
        }
        return url;
      });
      codes.set(key, asked);
      // A code that could not be made is asked for again at the next drawing.
      asked.catch(() => codes.delete(key));
    }
    asked
      .then((url) => {
        if (!disposed) {
          picture.src = url;
        }
      })
      .catch((error) => {
        picture.replaceWith(el("p", { class: "hint" }, `The QR code could not be made: ${error.message}`));
      });
    return picture;
  }

  /** The phone's own steps at the left, and at the right the one QR code, what it opens (the choice above it) and the
   * address to type instead. The step the guide is at is the one whose code is shown, and the last once a code is
   * shown; those before it are ticked. */
  function setupSection(phone) {
    const address = phone.addresses[0] ?? null;
    const steps = el("ol", { class: "phone-steps", "aria-label": "Steps on the phone" });
    const picture = el("div", { class: "phone-qr-box" });
    const addresses = el("div", { class: "phone-addresses" });
    const choice = segmented(QR_CHOICES, which ?? (address === null ? "tailscale" : "dot"), (id) => {
      which = id;
      paintSetup();
    });
    choice.element.setAttribute("aria-label", "What the QR code opens");

    function paintSetup() {
      const showing = choice.value();
      const now = codeShown ? PHONE_STEPS.length - 1 : showing === "dot" ? 1 : 0;
      steps.replaceChildren(
        ...PHONE_STEPS.map((phoneStep, index) =>
          el(
            "li",
            { class: `phone-step${index < now ? " done" : index === now ? " current" : ""}` },
            mark(index + 1, index < now),
            el("div", { class: "phone-step-body" }, el("div", { class: "phone-step-title" }, phoneStep.title), el("p", { class: "hint" }, phoneStep.text)),
          ),
        ),
      );
      if (showing === "tailscale") {
        picture.replaceChildren(qrPicture("tailscale", null, "A QR code of Tailscale's download page"));
        addresses.replaceChildren(addressRow(TAILSCALE_PAGE));
      } else if (address === null) {
        picture.replaceChildren();
        addresses.replaceChildren(
          el(
            "div",
            { class: "note" },
            icon("info"),
            el("span", null, "No address yet: the steps above give it.", settingFold("[server] reach")),
          ),
        );
      } else {
        picture.replaceChildren(qrPicture("dot", address, `A QR code of ${address}`));
        addresses.replaceChildren(...phone.addresses.map(addressRow));
      }
    }

    repaintSetup = paintSetup;
    paintSetup();
    return setSection({
      title: "Set up a phone",
      lead: "The phone reaches Dot while this Mac is on and awake.",
      body: setCard(el("div", { class: "phone-setup-grid" }, steps, el("div", { class: "phone-side" }, choice.element, picture, addresses))),
    });
  }

  // The code that signs in the app on a phone's home screen is made when it is asked for, and shown in place of the dots.
  // This row does not depend on what Tailscale says, so it is drawn once and stays through every drawing of the cards.
  const signIn = setSection({
    title: "Sign-in code",
    body: setCard(setRow({ title: "A code signs in the app on one phone", lead: codeLead, control: [dots, code, pairButton] })),
  });
  // The phones that hold a key of their own (item 670), each with Remove. A page signed in with a phone's key can neither
  // pair nor remove a phone: the daemon answers those to the Mac's own key alone, and the page says where to do it.
  const phonesBody = el("div", null, loadingPanel());
  const paired = setSection({ title: "Paired phones", body: setCard(phonesBody) });

  function phoneRow(phone) {
    const used = phone.last_used_at === null ? "not used yet" : `last used ${dayOf(phone.last_used_at)}`;
    const remove = el("button", { class: "btn sm", type: "button", "aria-label": `Remove ${phone.name}, paired ${dayOf(phone.paired_at)}` }, "Remove");
    remove.addEventListener("click", () =>
      attempt(async () => {
        const confirmed = await confirmDialog({
          title: `Remove this ${phone.name}?`,
          message: "It is signed out at once, and needs a new sign-in code to use Dot again.",
          confirmLabel: "Remove",
          danger: true,
          focusCancel: true,
        });
        if (!confirmed) {
          return;
        }
        await api.del(`/phone/phones/${encodeURIComponent(phone.id)}`);
        toast("ok", `${phone.name} removed.`);
        await loadPhones();
      }),
    );
    const row = setRow({ title: phone.name, lead: `Paired ${dayOf(phone.paired_at)}, ${used}`, control: remove });
    row.dataset.phone = phone.id;
    return row;
  }

  async function loadPhones() {
    if (holdsPhoneKey()) {
      phonesBody.replaceChildren(el("p", { class: "hint" }, "This phone signed in with a code. Pair or remove phones in Settings › Phone on your Mac."));
      return;
    }
    let listed;
    try {
      listed = await api.get("/phone/phones");
    } catch (error) {
      if (!disposed) {
        phonesBody.replaceChildren(errorPanel(error, () => attempt(loadPhones)));
      }
      return;
    }
    if (disposed) {
      return;
    }
    const rows = listed.phones.map(phoneRow);
    phonesBody.replaceChildren(
      ...(rows.length > 0 ? rows : [el("p", { class: "hint" }, "No phone signed in with a code yet.")]),
      el("p", { class: "hint" }, "A phone signed in with a code before this list existed holds your Mac's own key and is not listed here. To cut one off, take it off your Tailscale network in Tailscale's admin console."),
    );
  }

  if (holdsPhoneKey()) {
    pairButton.hidden = true;
    codeLead.textContent = "Show a code in Settings › Phone on your Mac.";
  }
  const reachSlot = el("div", null);
  const setupSlot = el("div", null);
  const sections = el("div", { class: "set-sections" }, reachSlot, setupSlot, signIn, paired);

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
      if (busy > 0 || document.visibilityState !== "visible") {
        follow(phone);
        return;
      }
      let next;
      try {
        next = await api.get("/phone");
      } catch (error) {
        if (!unreachable(error)) {
          // A daemon that answers with an error is shown, and the guide stops following it.
          if (!disposed) {
            shown = "";
            holder.replaceChildren(errorPanel(error, () => attempt(retry)));
          }
          return;
        }
        // No answer at all: the daemon may be starting again (Restart Dot), and is read again at the next turn.
        follow(phone);
        return;
      }
      if (disposed) {
        return;
      }
      if (busy > 0) {
        // An action of the guide answers and paints; the guide goes on following meanwhile.
        follow(next);
        return;
      }
      paint(next);
    }, FOLLOW_MS);
  }

  // What the cards show now, so that a reading that changed nothing leaves them (and the focus in them) as they are.
  let shown = "";

  function paint(phone) {
    const drawn = JSON.stringify([phone, httpsLink]);
    if (drawn !== shown) {
      shown = drawn;
      reachSlot.replaceChildren(reachCard(phone));
      setupSlot.replaceChildren(setupSection(phone));
      if (sections.parentElement !== holder) {
        holder.replaceChildren(sections);
      }
    }
    follow(phone);
  }

  async function load() {
    let phone;
    try {
      phone = await api.get("/phone");
    } catch (error) {
      if (!disposed) {
        shown = "";
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      }
      return;
    }
    if (!disposed) {
      paint(phone);
    }
  }

  async function retry() {
    shown = "";
    holder.replaceChildren(loadingPanel());
    await load();
  }

  ctx.head.setLead("Use Dot from your phone, at home or away, as an app on its home screen.");
  root.append(holder);
  attempt(load);
  attempt(loadPhones);
  // A phone that pairs while this page is open shows up when the page is looked at again.
  const relist = () => {
    if (document.visibilityState === "visible") {
      attempt(loadPhones);
    }
  };
  window.addEventListener("focus", relist);
  return () => {
    disposed = true;
    window.removeEventListener("focus", relist);
    clearTimeout(following);
    for (const url of made) {
      URL.revokeObjectURL(url);
    }
  };
}

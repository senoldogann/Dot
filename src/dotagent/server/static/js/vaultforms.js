// The three forms of Settings › Vault (item 621), each in a dialog (formDialog, item 615): a login, a card and a contact.
// The main button stays off until the form is good; what is wrong is said under the field as the owner types, and what
// the daemon refuses is said inside the dialog with what was typed kept. A secret is shown only when the eye is pressed,
// and then it is asked of the daemon, which answers it to this Mac's own page alone.

import { api } from "./api.js";
import { countryNames } from "./countries.js";
import { el } from "./dom.js";
import { dotName } from "./face.js";
import { icon } from "./icons.js";
import { formDialog, toast } from "./ui.js";
import {
  BRAND_NAMES,
  brandOf,
  codeProblem,
  digitsOf,
  expiryProblem,
  expiryTyping,
  numberGood,
  numberProblem,
  numberTyping,
} from "./vaultcard.js";

const MOST_PHOTO_BYTES = 12_000_000;
// How long the eye shows a saved secret before it hides it again by itself. The daemon leaves Dot's own windows out of a
// screenshot of the Mac for a little longer than this after it answers the eye (vault/shown.py, SHOWN_FOR_S).
const SHOWN_FOR_MS = 20_000;
// The details of a contact, and of the billing address of a card, with their labels.
const DETAILS = [
  { name: "name", label: "Full name" },
  { name: "address", label: "Address" },
  { name: "address2", label: "Address line 2" },
  { name: "city", label: "City" },
  { name: "region", label: "State or region" },
  { name: "postal_code", label: "Postal code" },
  { name: "country", label: "Country" },
  { name: "phone", label: "Phone" },
  { name: "email", label: "Email" },
];
const BILLING_NAMES = ["name", "address", "address2", "city", "region", "postal_code", "country"];
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

let fieldCount = 0;

/**
 * A field with its label, tied to its control by an id; `box` is what stands under the label (the control itself, or a
 * wrapper of it), `aside` a note at the right end of the label's line (the brand of a card).
 */
function labelled(label, control, hint = "", box = control, aside = null) {
  fieldCount += 1;
  const id = `vault-field-${fieldCount}`;
  control.id = id;
  if (hint !== "") {
    control.setAttribute("aria-describedby", `${id}-hint`);
  }
  return el(
    "div",
    { class: "field" },
    el("div", { class: "vault-label-row" }, el("label", { class: "field-label", for: id }, label), aside),
    box,
    hint === "" ? null : el("span", { class: "hint", id: `${id}-hint` }, hint),
  );
}

/** A line under a field that says what is wrong with it, hidden when nothing is. */
function problemLine() {
  const line = el("span", { class: "vault-problem", role: "status" });
  line.hidden = true;
  return {
    element: line,
    say(text) {
      line.textContent = text ?? "";
      line.hidden = text === null || text === undefined || text === "";
    },
  };
}

/** Set a box's text as the owner would have, so that what listens to it hears. */
function setValue(input, value) {
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

/**
 * A box for a secret (item 621), with an eye that shows its text or hides it again. A text that is drawn as dots is a
 * password input (`data-secret`), which the accessibility tree of the page gives no text for; the box that cannot be one
 * (notes, of many lines) holds no text while it is hidden. For an item that is saved already (`saved`: its id and the
 * part) the box starts empty and means "keep what is saved"; the eye then asks the daemon for the saved text, to this
 * Mac's own page, each time it shows it, and puts it in the box to be read or changed. A saved secret is hidden again by
 * itself after `SHOWN_FOR_MS`, and what the daemon answered then leaves the page: the next show asks again. A text the
 * owner changed stays as they wrote it (a box that cannot hide it by its type keeps it in the script meanwhile).
 * `startShown` is for a text that is seen as it is written (a card number). `send()` is what the form sends for the box:
 * nothing (`undefined`) for a box left alone, its text for one the owner wrote, an empty text for a saved secret the
 * owner showed and emptied.
 * @param {HTMLInputElement | HTMLTextAreaElement} input
 * @param {{ saved: { id: string, part: string } | null, what: string, startShown: boolean, eye: boolean, onError: (error: Error) => void, onShown: (input: HTMLInputElement | HTMLTextAreaElement) => void }} options
 */
function secretBox(input, { saved, what, startShown, eye: withEye, onError, onShown }) {
  const byType = input instanceof HTMLInputElement && input.dataset.secret === "password";
  const idle = input.placeholder;
  let shown = startShown;
  let fetched = false;
  let original = "";
  // The text of a box that cannot hide it by its type, which the owner changed, while the box is hidden.
  let kept = null;
  let hideTimer = 0;
  // The eye says what it does in its label; it has no aria-pressed, which the dialog counts as a change of the form.
  const eye = el("button", { class: "btn ghost sm icon-only vault-eye", type: "button" }, icon("eye"));

  function paint() {
    if (byType) {
      input.type = shown ? "text" : "password";
    } else if (saved !== null) {
      // A box that holds no text while it is hidden takes none: the eye shows it first.
      input.readOnly = !shown;
    }
    input.placeholder = kept !== null && !shown ? "Your change is kept: press the eye to read it" : idle;
    const words = `${shown ? "Hide" : "Show"} the ${what}`;
    eye.title = words;
    eye.setAttribute("aria-label", words);
    eye.replaceChildren(icon(shown ? "eyeOff" : "eye"));
  }

  /**
   * What a box that is hidden holds, so that the page's text (an accessibility tree reads what a box holds, however it is
   * drawn) does not give a saved secret away: what the daemon answered, and the owner did not change, leaves the box;
   * a text the owner changed stays in a box that is a password input, and in the script for one that is not.
   */
  function leave() {
    if (fetched && input.value === original) {
      fetched = false;
      original = "";
      setValue(input, "");
      // The empty box is as it was when the dialog opened, not a change of the owner's.
      onShown(input);
    } else if (!byType && input.value !== "") {
      kept = input.value;
      setValue(input, "");
    }
  }

  function hide() {
    clearTimeout(hideTimer);
    if (shown && saved !== null) {
      leave();
    }
    shown = false;
    paint();
  }

  eye.addEventListener("click", async () => {
    if (shown) {
      hide();
      return;
    }
    if (saved !== null) {
      // The daemon is asked each time a saved secret is shown, so that it knows one is on the screen. What it answers goes
      // into the box while the box holds nothing the owner wrote: a text they typed or changed stays as it is.
      eye.disabled = true;
      try {
        const answer = await api.post(`/vault/items/${encodeURIComponent(saved.id)}/reveal`, { part: saved.part });
        if (kept !== null) {
          // The owner's own text, which the box did not hold while it was hidden.
          setValue(input, kept);
          kept = null;
        } else if (input.value === "" || input.value === original) {
          fetched = true;
          setValue(input, answer.value);
          // The text as the box holds it now (a card number's box groups the digits it is given).
          original = input.value;
          // What the eye brought is not a change the owner made: closing the dialog does not ask about it.
          onShown(input);
        }
      } catch (error) {
        onError(error);
        return;
      } finally {
        eye.disabled = false;
      }
    }
    shown = true;
    paint();
    if (saved !== null) {
      hideTimer = setTimeout(hide, SHOWN_FOR_MS);
    }
  });
  paint();
  return {
    box: withEye ? el("div", { class: "vault-secret" }, input, eye) : input,
    /** The text the form sends for the box: undefined for a box left alone. */
    send() {
      const text = kept ?? input.value;
      if (fetched) {
        return text === original ? undefined : text;
      }
      return text === "" ? undefined : text;
    },
  };
}

/** A password-like box: its text is dots by the type of the box. */
function passwordInput(placeholder, inputmode = "") {
  const input = el("input", {
    class: "input",
    type: "password",
    autocomplete: "new-password",
    spellcheck: "false",
    placeholder,
    inputmode: inputmode === "" ? null : inputmode,
  });
  input.dataset.secret = "password";
  return input;
}

/** What a dialog says at its foot: what the assistant does with the item, or never does. */
function assurance(text) {
  return el("span", { class: "vault-assurance" }, icon("shield"), text);
}

/** `save` as the daemon takes it: a new item posts, a changed one puts. */
function keep(existing, body) {
  return existing === null ? api.post("/vault/items", body) : api.put(`/vault/items/${encodeURIComponent(existing.id)}`, body);
}

// ---------------------------------------------------------------------------------------------------------------- login

/**
 * The form of a login, new (`existing` null) or changed. `done(record)` runs once the daemon kept it.
 * @param {{ existing: any, returnFocus: (() => HTMLElement | null) | HTMLElement | null, done: (record: any) => void }} options
 */
export function openLoginDialog({ existing, returnFocus, done }) {
  let dialog = null;
  const onError = (error) => dialog?.fail(error);
  const onShown = (input) => dialog?.accept(input);
  const saved = (part) => (existing === null ? null : { id: existing.id, part });
  const website = el("input", {
    class: "input",
    type: "text",
    autocomplete: "off",
    spellcheck: "false",
    autocapitalize: "none",
    placeholder: "example.com",
    maxlength: "2000",
  });
  const username = el("input", { class: "input", type: "text", autocomplete: "off", spellcheck: "false", autocapitalize: "none", maxlength: "320" });
  const password = passwordInput(existing === null ? "" : "Saved: write to change it");
  const passwordBox = secretBox(password, { saved: saved("password"), what: "password", startShown: false, eye: true, onError, onShown });
  const notes = el("textarea", { class: "textarea", rows: "3", maxlength: "1000", spellcheck: "false" });
  notes.placeholder = existing === null ? "" : "Saved notes stay as they are: show them to change them";
  const notesBox = secretBox(notes, { saved: saved("notes"), what: "notes", startShown: existing === null, eye: existing !== null, onError, onShown });
  if (existing !== null) {
    website.value = existing.website ?? "";
    username.value = existing.username ?? "";
  }

  const valid = () => {
    const site = website.value.trim();
    return site !== "" && !/\s/.test(site) && (existing !== null || password.value !== "");
  };

  dialog = formDialog({
    title: existing === null ? "New login" : "Edit login",
    description: `A site and the account you sign in with. ${dotName()} types it for you where you ask, only on that site.`,
    body: [
      labelled("Website", website),
      labelled("Username or email (optional)", username),
      labelled("Password", password, "", passwordBox.box),
      labelled("Notes (optional)", notes, "", notesBox.box),
    ],
    submitLabel: "Save login",
    valid,
    leading: [assurance(`${dotName()} can use this login to sign in for you`)],
    returnFocus,
    onSubmit: async () => {
      const body = { kind: "login", website: website.value.trim(), username: username.value.trim() };
      const secret = passwordBox.send();
      if (secret !== undefined) {
        body.password = secret;
      }
      const written = notesBox.send();
      if (written !== undefined) {
        body.notes = written;
      }
      const record = await keep(existing, body);
      toast("ok", existing === null ? "Login saved." : "Login changed.");
      done(record);
      return true;
    },
  });
  return dialog;
}

// ----------------------------------------------------------------------------------------------------------------- card

/** The details of an address as boxes, in the order of `names`: their fields, `values()` and whether `any()` is written. */
function addressBoxes(names, existing, countries) {
  const boxes = new Map();
  const fields = [];
  for (const spec of DETAILS.filter((one) => names.includes(one.name))) {
    let control;
    if (spec.name === "country") {
      control = el("select", { class: "input" });
      const known = existing[spec.name] ?? "";
      const options = known !== "" && !countries.includes(known) ? [known, ...countries] : countries;
      control.append(el("option", { value: "" }, ""), ...options.map((name) => el("option", { value: name }, name)));
    } else {
      control = el("input", {
        class: "input",
        type: spec.name === "email" ? "email" : spec.name === "phone" ? "tel" : "text",
        autocomplete: "off",
        spellcheck: "false",
        maxlength: spec.name === "postal_code" ? "20" : "200",
      });
    }
    control.value = existing[spec.name] ?? "";
    boxes.set(spec.name, control);
    fields.push(labelled(spec.label, control));
  }
  return {
    fields,
    boxes,
    values() {
      return Object.fromEntries([...boxes].map(([name, control]) => [name, control.value.trim()]));
    },
    any() {
      return [...boxes.values()].some((control) => control.value.trim() !== "");
    },
  };
}

/** The picture a file holds, as base64 without the prefix of a data address. */
function pictureOf(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result).replace(/^data:[^,]*,/, "")));
    reader.addEventListener("error", () => reject(new Error("The photo could not be read.")));
    reader.readAsDataURL(file);
  });
}

/**
 * The form of a card, new or changed. The number is checked as it is typed (its check digit, its brand), the expiry is a
 * month and a year, and Save stays off until the card is good. "Upload a photo" reads the card on this Mac and fills what it
 * found, for the owner to check.
 * @param {{ existing: any, returnFocus: (() => HTMLElement | null) | HTMLElement | null, done: (record: any) => void }} options
 */
export function openCardDialog({ existing, returnFocus, done }) {
  let dialog = null;
  const onError = (error) => dialog?.fail(error);
  const onShown = (input) => dialog?.accept(input);
  const saved = (part) => (existing === null ? null : { id: existing.id, part });

  const number = el("input", {
    class: "input mono",
    type: "text",
    inputmode: "numeric",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "23",
    placeholder: existing === null ? "1234 5678 9012 3456" : `Saved: •••• ${existing.last4}`,
  });
  number.dataset.secret = "password";
  const brand = el("span", { class: "vault-brand", "aria-live": "polite" });
  const numberSays = problemLine();
  // A number being written is seen as it is typed; a saved one starts hidden, and shows when the eye is pressed.
  const numberBox = secretBox(number, { saved: saved("number"), what: "card number", startShown: existing === null, eye: true, onError, onShown });
  const expiry = el("input", {
    class: "input mono",
    type: "text",
    inputmode: "numeric",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "7",
    placeholder: existing === null ? "MM/YY" : "Saved: write to change it",
  });
  const expirySays = problemLine();
  const code = passwordInput(existing === null ? "" : "Saved: write to change it", "numeric");
  code.maxLength = 4;
  const codeSays = problemLine();
  const codeBox = secretBox(code, { saved: saved("code"), what: "security code", startShown: false, eye: true, onError, onShown });
  // The name on the card is kept in the Keychain like the code, so it has the same eye: shown, it can be read, changed or
  // emptied (which removes it); left alone, it is kept.
  const name = passwordInput(existing === null ? "" : "Saved: write to change it");
  name.maxLength = 120;
  const nameBox = secretBox(name, { saved: saved("name"), what: "name on the card", startShown: existing === null, eye: true, onError, onShown });
  const nickname = el("input", { class: "input", type: "text", autocomplete: "off", maxlength: "40", placeholder: "Travel" });
  nickname.value = existing?.nickname ?? "";

  const billing = addressBoxes(BILLING_NAMES, existing?.fields ?? {}, countryNames());
  const hasBilling = existing !== null && Object.keys(existing.fields).length > 0;
  const billingBox = el("div", { class: "vault-billing", hidden: !hasBilling }, billing.fields);
  const billingToggle = el("button", { class: "btn ghost sm vault-billing-toggle", type: "button" });
  function paintBilling() {
    const open = !billingBox.hidden;
    billingToggle.setAttribute("aria-expanded", String(open));
    billingToggle.replaceChildren(icon(open ? "x" : "plus"), open ? "Remove the billing address" : "Add a billing address (optional)");
  }
  billingToggle.addEventListener("click", () => {
    if (!billingBox.hidden) {
      for (const control of billing.boxes.values()) {
        control.value = "";
      }
    }
    billingBox.hidden = !billingBox.hidden;
    paintBilling();
  });
  paintBilling();

  // The photo: a picture of the card, read on this Mac (Vision) and sent nowhere.
  const picture = el("input", { type: "file", accept: "image/*", hidden: true, "aria-label": "Photo of the card" });
  const upload = el("button", { class: "btn", type: "button" }, icon("upload"), "Upload a photo");
  const photoNote = el("p", { class: "hint vault-photo-note", role: "status", hidden: true });
  upload.addEventListener("click", () => picture.click());
  picture.addEventListener("change", async () => {
    const file = picture.files?.[0];
    if (file === undefined) {
      return;
    }
    if (file.size > MOST_PHOTO_BYTES) {
      picture.value = "";
      dialog?.fail(new Error("This photo is too big: use one under 12 MB."));
      return;
    }
    upload.disabled = true;
    photoNote.hidden = false;
    photoNote.textContent = "Reading the photo on this Mac…";
    try {
      const read = await api.post("/vault/card-photo", { image: await pictureOf(file) });
      dialog?.fail(null);
      const found = [];
      if (read.number !== null) {
        setValue(number, read.number);
        found.push("the number");
      }
      if (read.expiry !== null) {
        setValue(expiry, read.expiry);
        found.push("the expiry");
      }
      if (read.name !== null && name.value === "") {
        setValue(name, read.name);
        found.push("the name");
      }
      photoNote.textContent =
        found.length === 0
          ? "No card number could be read in this photo. Try a sharper photo, straight on, or type it."
          : `Read ${found.join(", ")} from the photo, on this Mac. Check them before you save.`;
      dialog?.revalidate();
    } catch (error) {
      photoNote.hidden = true;
      dialog?.fail(error);
    } finally {
      picture.value = "";
      upload.disabled = false;
    }
  });

  number.addEventListener("input", () => {
    const typed = numberTyping(number.value, number.selectionStart ?? number.value.length);
    if (typed.text !== number.value) {
      number.value = typed.text;
      number.setSelectionRange(typed.caret, typed.caret);
    }
    const digits = digitsOf(number.value) ?? "";
    const named = brandOf(digits);
    brand.textContent = digits.length === 0 || named === "other" ? "" : BRAND_NAMES[named];
    numberSays.say(numberProblem(number.value));
  });
  expiry.addEventListener("input", (event) => {
    const typed = expiryTyping(expiry.value, String(event.inputType ?? "").startsWith("delete"));
    if (typed !== expiry.value) {
      expiry.value = typed;
    }
    expirySays.say(expiryProblem(expiry.value, new Date()));
  });
  code.addEventListener("input", () => codeSays.say(codeProblem(code.value)));

  const valid = () => {
    const numberFine = number.value.trim() === "" ? existing !== null : numberGood(number.value);
    const expiryFine = expiry.value.trim() === "" ? existing !== null : expiryProblem(expiry.value, new Date(), true) === null;
    return numberFine && expiryFine && codeProblem(code.value) === null;
  };

  dialog = formDialog({
    title: existing === null ? "New card" : "Edit card",
    description: `A card kept in your Mac's Keychain. ${dotName()} never sees its number, expiry or code.`,
    body: [
      el("div", { class: "vault-photo" }, upload, el("span", { class: "muted" }, "or enter it"), picture),
      photoNote,
      labelled("Card number", number, "", numberBox.box, brand),
      numberSays.element,
      labelled("Expiry (MM / YY)", expiry),
      expirySays.element,
      labelled("Security code (optional)", code, "", codeBox.box),
      codeSays.element,
      labelled("Name on the card (optional)", name, "", nameBox.box),
      labelled("Nickname (optional)", nickname, "Like Travel or Work: how the card is named in lists."),
      billingToggle,
      billingBox,
    ],
    submitLabel: "Save card",
    valid,
    leading: [assurance(`${dotName()} never sees the number`)],
    returnFocus,
    onSubmit: async () => {
      const body = { kind: "card", nickname: nickname.value.trim() };
      const written = numberBox.send();
      if (written !== undefined) {
        body.number = written;
      }
      if (expiry.value.trim() !== "") {
        body.expiry = expiry.value.trim();
      }
      const secret = codeBox.send();
      if (secret !== undefined) {
        body.code = secret;
      }
      const named = nameBox.send();
      if (named !== undefined) {
        body.name = named.trim();
      }
      if (!billingBox.hidden || existing !== null) {
        body.billing = billingBox.hidden ? {} : billing.values();
      }
      const record = await keep(existing, body);
      toast("ok", existing === null ? "Card saved." : "Card changed.");
      done(record);
      return true;
    },
  });
  return dialog;
}

// -------------------------------------------------------------------------------------------------------------- contact

/**
 * The form of a contact, new or changed: a label and the details a form asks for.
 * @param {{ existing: any, returnFocus: (() => HTMLElement | null) | HTMLElement | null, done: (record: any) => void }} options
 */
export function openContactDialog({ existing, returnFocus, done }) {
  const label = el("input", { class: "input", type: "text", autocomplete: "off", maxlength: "40", placeholder: "Home" });
  label.value = existing?.label ?? "";
  const details = addressBoxes(
    DETAILS.map((spec) => spec.name),
    existing?.fields ?? {},
    countryNames(),
  );
  const [fullName, address, address2, city, region, postal, country, phone, email] = details.fields;
  const emailBox = details.boxes.get("email");
  const emailSays = problemLine();
  const emailGood = () => emailBox.value.trim() === "" || EMAIL.test(emailBox.value.trim());
  emailBox.addEventListener("input", () => emailSays.say(emailGood() ? null : "The email address does not look right."));

  const valid = () => label.value.trim() !== "" && details.any() && emailGood();

  return formDialog({
    title: existing === null ? "New contact" : "Edit contact",
    description: `Details ${dotName()} may type into forms for you: a name, an address, a phone number, an email.`,
    body: [
      labelled("Label", label, "Like Home or Work."),
      fullName,
      address,
      address2,
      el("div", { class: "vault-pair" }, city, region),
      el("div", { class: "vault-pair" }, postal, country),
      el("div", { class: "vault-pair" }, phone, email),
      emailSays.element,
    ],
    submitLabel: "Save contact",
    valid,
    returnFocus,
    onSubmit: async () => {
      const record = await keep(existing, { kind: "contact", label: label.value.trim(), fields: details.values() });
      toast("ok", existing === null ? "Contact saved." : "Contact changed.");
      done(record);
      return true;
    },
  });
}

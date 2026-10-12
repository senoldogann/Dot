// API keys: the key each model provider takes, saved from here in Dot's own .env file. The page never receives a key
// back from the daemon: only where the one in use comes from and its last four characters.

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, paneHead } from "../panels.js";
import { providerLabel, providerOf } from "../providers.js";
import { forgetModels, getSnapshot, onChange, refresh } from "../state.js";
import { attempt, confirmDialog, toast } from "../ui.js";

// What the key of each provider is for and where it is made. `needed`: the models of the provider work only with a key
// (Ollama's run through the app on this Mac and need none; the typed judge is off unless the configuration names it).
const PROVIDERS = {
  openai: {
    use: "The GPT models.",
    page: "https://platform.openai.com/api-keys",
    site: "platform.openai.com",
    needed: true,
  },
  anthropic: {
    use: "The Claude models.",
    page: "https://platform.claude.com/settings/keys",
    site: "platform.claude.com",
    needed: true,
  },
  opencode_go: {
    use: "The open models of an OpenCode Go subscription.",
    page: "https://opencode.ai/auth",
    site: "opencode.ai",
    needed: true,
  },
  ollama: {
    use: "Optional: the hosted web search, and ollama.com without the Ollama app. The models the app runs need no key.",
    page: "https://ollama.com/settings/keys",
    site: "ollama.com",
    needed: false,
  },
  typesafe: {
    use: "Optional: the typed judge ([judge] provider = \"typesafe\"), which reads click labels no rule knows and campaign claims. Check sends one small judgment.",
    page: "https://console.typesafe.ai",
    site: "console.typesafe.ai",
    needed: false,
  },
};
const SOURCES = {
  environment: "from the daemon's environment",
  env_file: "saved in Dot",
  keychain: "from the Keychain",
};
const ROLES = [
  { id: "main", label: "main model" },
  { id: "vision", label: "vision model" },
  { id: "review", label: "review model" },
];
// A key as the daemon takes one: printable characters without spaces.
const KEY_PATTERN = /^[!-~]+$/;
const KEY_CHARS = 1000;

function aboutProvider(provider) {
  const about = PROVIDERS[provider];
  if (about === undefined) {
    throw new Error(`unknown provider: ${provider}`);
  }
  return { ...about, label: providerLabel(provider) };
}

/** The roles of the configured models that run on `provider`, read from the daemon status (none before it arrives). */
function rolesOn(provider) {
  const status = getSnapshot().status;
  if (status === null) {
    return [];
  }
  return ROLES.filter((role) => typeof status.models[role.id] === "string" && providerOf(status.models[role.id]) === provider);
}

/**
 * The field a key is pasted into, hidden like a password until it is shown. `save(value)` gets the trimmed key and may
 * throw; its message then shows under the field. `close()` empties the field, so a key does not stay in the page.
 */
function buildForm(label, save, cancel) {
  const input = el("input", {
    class: "input mono",
    type: "password",
    maxlength: String(KEY_CHARS),
    autocomplete: "off",
    autocapitalize: "off",
    spellcheck: "false",
    placeholder: "Paste the key",
    "aria-label": `${label} API key`,
  });
  const reveal = el("button", { class: "btn ghost sm icon-only key-reveal", type: "button" });
  const submit = el("button", { class: "btn primary", type: "button" }, "Save");
  const problem = el("div", { class: "note bad", hidden: true }, icon("alert"), el("span"));
  const element = el(
    "div",
    { class: "key-form", hidden: true },
    el(
      "div",
      { class: "key-entry" },
      el("div", { class: "key-field" }, input, reveal),
      submit,
      el("button", { class: "btn ghost", type: "button", onclick: () => cancel() }, "Cancel"),
    ),
    problem,
  );

  function paintReveal(shown) {
    const text = shown ? "Hide the key" : "Show the key";
    input.type = shown ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(shown));
    reveal.setAttribute("aria-label", text);
    reveal.title = text;
    reveal.replaceChildren(icon(shown ? "eyeOff" : "eye"));
  }

  function complain(message) {
    problem.lastChild.textContent = message;
    problem.hidden = false;
  }

  // True while a key is being saved: Enter does not look at the disabled button, so a second press is refused here.
  let saving = false;

  async function submitKey() {
    if (saving) {
      return;
    }
    const value = input.value.trim();
    if (value === "") {
      input.focus();
      return;
    }
    if (!KEY_PATTERN.test(value)) {
      complain("A key has no spaces or line breaks inside it. Copy it again from the page of the provider.");
      return;
    }
    problem.hidden = true;
    saving = true;
    submit.disabled = true;
    try {
      await save(value);
    } catch (error) {
      console.error("the key could not be saved", error);
      complain(error.message);
    } finally {
      saving = false;
      submit.disabled = false;
    }
  }

  reveal.addEventListener("click", () => paintReveal(input.type === "password"));
  submit.addEventListener("click", () => attempt(submitKey));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      attempt(submitKey);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    }
  });
  paintReveal(false);
  return {
    element,
    isOpen: () => !element.hidden,
    open() {
      problem.hidden = true;
      paintReveal(false);
      element.hidden = false;
      input.focus();
    },
    close() {
      input.value = "";
      paintReveal(false);
      element.hidden = true;
    },
  };
}

/**
 * One provider: whether its key is set and where from, the buttons that add, check and remove it, and the result of the
 * last check. `paint()` shows the state again, without touching a form that is open or a check that is shown.
 */
function buildKeyRow(initial, isDisposed) {
  let view = initial;
  const about = aboutProvider(view.provider);
  const badge = el("span", { class: "badge" });
  const roles = el("span", { class: "cluster" });
  const facts = el("div", { class: "row-meta" });
  const shadow = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span"));
  const outcome = el("div", { class: "note", hidden: true, role: "status" });
  const check = el("button", { class: "btn sm", type: "button", title: "Ask the provider whether it takes this key" }, icon("check"), "Check");
  const edit = el("button", { class: "btn sm", type: "button" });
  const remove = el(
    "button",
    {
      class: "btn ghost sm icon-only",
      type: "button",
      title: "Remove the key saved in Dot",
      "aria-label": `Remove the ${about.label} key saved in Dot`,
    },
    icon("trash"),
  );
  const form = buildForm(about.label, (value) => saveKey(value), () => closeForm());
  const element = el(
    "div",
    { class: "list-row key-row" },
    el("span", { class: "key-mark" }, icon("key")),
    el(
      "div",
      { class: "row-main" },
      el("div", { class: "row key-head" }, el("span", { class: "row-title" }, about.label), badge, roles),
      el("div", { class: "row-text muted" }, about.use),
      facts,
      shadow,
      outcome,
      form.element,
      el(
        "a",
        { class: "key-page", href: about.page, target: "_blank", rel: "noopener noreferrer" },
        `Get a key at ${about.site}`,
        icon("external"),
      ),
    ),
    el("div", { class: "row-actions" }, check, edit, remove),
  );

  function show(ok, detail) {
    outcome.className = ok ? "note ok" : "note bad";
    outcome.replaceChildren(
      icon(ok ? "check" : "alert"),
      el("span", null, el("strong", null, ok ? "The key works" : "The check failed"), ` — ${detail}`),
    );
    outcome.hidden = false;
  }

  function paint() {
    const set = view.source !== null;
    const using = about.needed ? rolesOn(view.provider) : [];
    element.dataset.state = set ? "set" : "unset";
    if (set) {
      badge.className = "badge b-ok";
      badge.textContent = "in use";
    } else if (using.length > 0) {
      badge.className = "badge b-bad";
      badge.textContent = "missing";
    } else {
      badge.className = "badge b-muted";
      badge.textContent = "not set";
    }
    roles.replaceChildren(...using.map((role) => el("span", { class: "chip", title: `Your ${role.label} runs on ${about.label}` }, role.label)));
    facts.replaceChildren(
      el("code", { class: "mono" }, view.name),
      ...(set
        ? [el("span", { class: "key-secret", title: "The last characters of the key in use" }, `••••${view.tail}`), el("span", null, SOURCES[view.source])]
        : []),
    );
    shadow.hidden = view.source !== "environment";
    shadow.lastChild.textContent = view.saved
      ? `The daemon was started with ${view.name} in its environment, which comes before the key saved in Dot: that one is not in use.`
      : `The daemon was started with ${view.name} in its environment, which comes before a key saved here.`;
    check.hidden = !set;
    edit.className = set ? "btn sm" : "btn sm primary";
    edit.replaceChildren(icon(set ? "pencil" : "plus"), set ? "Replace" : "Add key");
    edit.hidden = form.isOpen();
    remove.hidden = !view.saved;
  }

  // The button that opened the form shows again in its place, and the focus goes back to it.
  function closeForm() {
    form.close();
    paint();
    edit.focus();
  }

  async function runCheck() {
    check.disabled = true;
    outcome.className = "note";
    outcome.replaceChildren(el("span", { class: "spinner" }), el("span", null, `Asking ${about.label}…`));
    outcome.hidden = false;
    let result;
    try {
      result = await api.post(`/keys/${view.name}/check`, null);
    } catch (error) {
      if (!isDisposed()) {
        show(false, error.message);
      }
      return;
    } finally {
      check.disabled = false;
    }
    if (!isDisposed()) {
      show(result.ok, result.detail);
    }
  }

  async function saveKey(value) {
    const saved = await api.post(`/keys/${view.name}`, { value });
    forgetModels();
    if (isDisposed()) {
      return;
    }
    view = saved;
    form.close();
    paint();
    toast("ok", `${about.label} key saved.`);
    await runCheck();
  }

  async function removeKey() {
    const confirmed = await confirmDialog({
      title: `Remove the ${about.label} key?`,
      message: `Dot deletes ${view.name} from its .env file. The key itself stays valid at ${about.site} until you revoke it there.`,
      confirmLabel: "Remove",
      danger: true,
      focusCancel: true,
    });
    if (!confirmed) {
      return;
    }
    const removed = await api.del(`/keys/${view.name}`);
    forgetModels();
    if (isDisposed()) {
      return;
    }
    view = removed;
    outcome.hidden = true;
    paint();
    toast("ok", `${about.label} key removed.`);
  }

  check.addEventListener("click", () => attempt(runCheck));
  edit.addEventListener("click", () => {
    outcome.hidden = true;
    form.open();
    paint();
  });
  remove.addEventListener("click", () => attempt(removeKey));
  paint();
  return { element, paint };
}

/** The key of each provider, the form that saves one, and where the keys are kept. */
export function mount(root, ctx) {
  let disposed = false;
  let rows = [];
  const holder = el("div", null, loadingPanel());
  const kept = el("span");

  async function load() {
    let found;
    try {
      found = await api.get("/keys");
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      }
      return;
    }
    if (disposed) {
      return;
    }
    rows = found.keys.map((view) => buildKeyRow(view, () => disposed));
    kept.replaceChildren("Keys saved here go to ", el("code", { class: "mono" }, found.env_file), ", which only your user can read.");
    holder.replaceChildren(el("section", { class: "card" }, rows.map((row) => row.element)));
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await load();
  }

  root.append(
    paneHead("The keys Dot signs in to the model providers and the typed judge with. The page never shows a whole key.", null),
    holder,
    el(
      "div",
      { class: "stack tight" },
      el("div", { class: "note" }, icon("shield"), kept),
      el(
        "div",
        { class: "note" },
        icon("refresh"),
        el(
          "span",
          null,
          "A new key is used from the next request on, without a restart. The memory search and MCP servers read their keys when the daemon starts: after changing a key they use, restart it with ",
          el("code", { class: "mono" }, "dotx service install"),
          ".",
        ),
      ),
    ),
  );
  const stop = onChange(() => rows.forEach((row) => row.paint()));
  attempt(refresh);
  attempt(load);
  return () => {
    disposed = true;
    stop();
  };
}

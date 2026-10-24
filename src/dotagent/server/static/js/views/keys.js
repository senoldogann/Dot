// Providers: the model providers Dot signs in with and the typed judge's service (item 373): the providers down the
// left side, each with its mark, its name and the state of its key; the one chosen on the right, with the key it takes,
// the models it brings and, for TypeSafe, the judge's switch. The page never receives a key back from the daemon: only
// where the one in use comes from and its last four characters.

import { api } from "../api.js";
import { el, modelName } from "../dom.js";
import { icon } from "../icons.js";
import { PHONE } from "../layout.js";
import { errorPanel, loadingPanel, switchSetting } from "../panels.js";
import { providerLabel, providerOf } from "../providers.js";
import { forgetModels, getSnapshot, loadModels, onChange, refresh } from "../state.js";
import { attempt, confirmDialog, formDialog, toast } from "../ui.js";

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
  openrouter: {
    use: "The models of many labs, on one key and one balance.",
    page: "https://openrouter.ai/settings/keys",
    site: "openrouter.ai",
    needed: true,
  },
  ollama: {
    use: "Optional: the hosted web search, and ollama.com without the Ollama app. The models the app runs need no key.",
    page: "https://ollama.com/settings/keys",
    site: "ollama.com",
    needed: false,
  },
  typesafe: {
    use:
      "Optional: the typed judge, which reads click labels no rule knows and campaign claims; the switch here turns it on. " +
      "The saved key alone gives runs the judge_items tool and a campaign's post checks, the switch on or off; remove the key to stop those. " +
      "Check sends one small judgment.",
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
// The models a provider's page lists before Show all.
const MODELS_SHOWN = 8;
// A key as the daemon takes one: printable characters without spaces.
const KEY_PATTERN = /^[!-~]+$/;
const KEY_CHARS = 1000;
// The provider whose key the typed judge signs in with.
const JUDGE_SERVICE = "typesafe";
// The typed judge as the daemon last reported it (item 255): `{ provider, key_name, key_set }`, read with the keys.
let judge = null;
// The models of every provider as the daemon last listed them (item 373), read with the keys and again after a key is
// saved or removed: a provider without a key answers with its error instead. `modelsProblem` is why the list could not
// be read, null once it was.
let models = [];
let modelsProblem = null;

/** Reads the models of every provider again; a list that cannot be read is said on each provider's page. */
async function readModels() {
  try {
    models = await loadModels();
    modelsProblem = null;
  } catch (error) {
    modelsProblem = error.message;
  }
}

function aboutProvider(provider) {
  const about = PROVIDERS[provider];
  if (about === undefined) {
    throw new Error(`unknown provider: ${provider}`);
  }
  return { ...about, label: providerLabel(provider) };
}

/** The mark of a provider on its tile: its own logo as a mask, the generic one for a service that has none. */
function providerMark(provider, size) {
  const mark = el("span", { class: "provider-mark" });
  mark.dataset.provider = provider;
  return el("span", { class: size === "large" ? "provider-tile provider-tile-lg" : "provider-tile", "aria-hidden": "true" }, mark);
}

/** What a provider's models are, as the line under its name says it when its key is set: the words without their full stop. */
function shortUse(use) {
  const words = use.replace(/\.$/, "");
  return words.charAt(0).toLowerCase() + words.slice(1);
}

/** The roles of the configured models that run on `provider`, read from the daemon status (none before it arrives). */
function rolesOn(provider) {
  const status = getSnapshot().status;
  if (status === null) {
    return [];
  }
  return ROLES.filter((role) => providerOf(status.models[role.id] ?? "") === provider);
}

/**
 * The field a key is pasted into, for the dialog that holds it (item 615), hidden like a password until it is shown.
 * Returns the element, `submit(save)`, which hands the trimmed key to `save(value)` and resolves to `false` when there is
 * none (the field takes the focus), and throws when the key has a space or a line break inside or `save` fails, and
 * `clear()`, which empties the field, so that a key does not stay in the page.
 */
function buildKeyFields(label) {
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
  const element = el("div", { class: "key-entry" }, el("div", { class: "key-field" }, input, reveal));

  function paintReveal(shown) {
    const text = shown ? "Hide the key" : "Show the key";
    input.type = shown ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(shown));
    reveal.setAttribute("aria-label", text);
    reveal.title = text;
    reveal.replaceChildren(icon(shown ? "eyeOff" : "eye"));
  }

  reveal.addEventListener("click", () => paintReveal(input.type === "password"));
  paintReveal(false);
  return {
    element,
    async submit(save) {
      const value = input.value.trim();
      if (value === "") {
        input.focus();
        return false;
      }
      if (!KEY_PATTERN.test(value)) {
        throw new Error("A key has no spaces or line breaks inside it. Copy it again from the page of the provider.");
      }
      await save(value);
      return true;
    },
    clear() {
      input.value = "";
      paintReveal(false);
    },
  };
}

/** The models a provider answered with and how many there are; the error it answered with when it has none. */
function modelsOf(provider) {
  return models.find((each) => each.provider === provider) ?? null;
}

// The word under a provider's name in the list: where its key stands, in the few words a row has room for. The last
// characters of a key are set in the mono face, and words in the face of the page (item 576).
function keyLine(view) {
  // A key in use may be the daemon's own (its environment or the Keychain): `source` says so while `saved` is false.
  if (view.source === null) {
    return aboutProvider(view.provider).needed ? "No key yet" : "Optional · no key";
  }
  return view.tail === "" ? (SOURCES[view.source] ?? view.source) : el("span", { class: "mono" }, `••••${view.tail}`);
}

/**
 * One provider: the row that stands for it in the list and the panel that opens beside it. `paint()` shows the state
 * again, without touching a form that is open or a check that is shown.
 */
function buildProvider(initial, isDisposed, host) {
  let view = initial;
  const about = aboutProvider(view.provider);

  // ---- the row down the left side
  const dot = el("span", { class: "provider-dot" });
  const line = el("span", { class: "provider-line" });
  // A button among buttons, the open one marked as current (item 559): the list is no listbox, which the arrow keys
  // would be expected to move through.
  const element = el(
    "button",
    {
      class: "provider-item",
      type: "button",
      onclick: () => host.choose(view.provider),
    },
    providerMark(view.provider),
    el("span", { class: "provider-text" }, el("span", { class: "provider-name" }, about.label), line),
    dot,
  );

  // ---- the panel beside it
  const stateLine = el("p", { class: "provider-state" });
  const back = el(
    "button",
    { class: "provider-back", type: "button", "aria-label": "Back to the providers", onclick: () => host.back() },
    icon("chevronLeft"),
  );
  const check = el(
    "button",
    {
      class: "btn ghost sm icon-only provider-check",
      type: "button",
      "aria-label": `Check the ${about.label} key`,
      title: `Ask ${about.label} whether the key works`,
      onclick: () => attempt(runCheck),
    },
    icon("refresh"),
  );
  const head = el(
    "header",
    { class: "provider-head" },
    back,
    providerMark(view.provider, "large"),
    el("div", { class: "provider-ident" }, el("h2", { class: "provider-title" }, about.label), stateLine),
    check,
  );

  // The key in use: its name in the environment, its last characters and where it comes from.
  const facts = el("div", { class: "provider-facts" });
  const shadow = el("div", { class: "note warn", hidden: true }, icon("alert"), el("span"));
  const outcome = el("div", { class: "note", hidden: true, role: "status" });

  // Add key and Replace key open the form that takes the key in a dialog (item 615).
  const edit = el("button", { class: "btn primary", type: "button", "aria-haspopup": "dialog" }, icon("plus"));
  const remove = el(
    "button",
    {
      class: "btn ghost",
      type: "button",
      "aria-label": `Remove the ${about.label} key saved in Dot`,
      onclick: () => attempt(removeKey),
    },
    icon("trash"),
    "Remove",
  );
  remove.classList.add("provider-remove");
  const actions = el("div", { class: "provider-actions" }, edit, remove);

  const auth = el(
    "section",
    { class: "provider-section" },
    el(
      "div",
      { class: "provider-section-head" },
      el("h3", { class: "provider-section-title" }, "Key"),
      el(
        "a",
        { class: "provider-docs", href: about.page, target: "_blank", rel: "noopener noreferrer" },
        `Get a key at ${about.site}`,
        icon("external"),
      ),
    ),
    // What a provider whose key is needed brings is the line under its name; an optional one has more to say.
    about.needed ? null : el("p", { class: "provider-use" }, about.use),
    facts,
    actions,
    shadow,
    outcome,
  );

  // The models this provider brings, named as the pickers name them, with what each one takes; the judge's service
  // brings none.
  const modelCount = el("span", { class: "provider-count" });
  const modelBox = el("div", { class: "provider-models" });
  const filter = el("input", {
    class: "input sm provider-filter",
    type: "search",
    placeholder: "Filter models",
    "aria-label": `Filter the models of ${about.label}`,
    autocomplete: "off",
    spellcheck: "false",
    hidden: true,
  });
  const showAll = el("button", { class: "provider-show-all", type: "button", hidden: true });
  let expanded = false;
  const modelsSection =
    view.provider === JUDGE_SERVICE
      ? null
      : el(
          "section",
          { class: "provider-section" },
          el("div", { class: "provider-section-head" }, el("h3", { class: "provider-section-title" }, "Models", modelCount), filter),
          modelBox,
        );

  /** The rows the filter lets through, the first few of them unless Show all was pressed or something is typed. */
  function filterModels() {
    const needle = filter.value.trim().toLowerCase();
    const rows = [...modelBox.querySelectorAll(".provider-model")];
    let shown = 0;
    for (const row of rows) {
      const matched = needle === "" || row.dataset.model.toLowerCase().includes(needle);
      row.hidden = !(matched && (needle !== "" || expanded || shown < MODELS_SHOWN));
      shown += row.hidden ? 0 : 1;
    }
    showAll.hidden = needle !== "" || expanded || rows.length <= MODELS_SHOWN;
    showAll.textContent = `Show all ${rows.length}`;
    filter.hidden = rows.length === 0;
  }
  showAll.addEventListener("click", () => {
    expanded = true;
    filterModels();
  });
  filter.addEventListener("input", filterModels);

  const judgeSwitch =
    view.provider !== JUDGE_SERVICE
      ? null
      : switchSetting({
          label: "Typed judge (Jev)",
          hint: "Asks TypeSafe what a click no rule knows does and whether a run's claim holds. Saved in the config file; no restart.",
          read: () => judge !== null && judge.provider === "typesafe",
          write: async (on) => {
            judge = await api.put("/judge", { provider: on ? "typesafe" : "none" });
            toast(
              "ok",
              on ? "The typed judge answers from the next judgment on." : "The typed judge is off; the patterns and the goal checker decide alone.",
            );
          },
          onToggled: () => judgeSwitch.paint(),
        });
  const judgeSection =
    judgeSwitch === null
      ? null
      : el("section", { class: "provider-section" }, el("h3", { class: "provider-section-title" }, "Typed judge"), judgeSwitch.element);

  const panel = el("div", { class: "provider-panel" }, head, auth, modelsSection, judgeSection);

  function show(ok, detail) {
    outcome.className = ok ? "note ok" : "note bad";
    outcome.replaceChildren(
      icon(ok ? "check" : "alert"),
      el("span", null, el("strong", null, ok ? "The key works" : "The check failed"), ` — ${detail}`),
    );
    outcome.hidden = false;
  }

  let modelsShown = "";

  function paintModels() {
    if (modelsSection === null) {
      return;
    }
    const entry = modelsOf(view.provider);
    // Drawn again only when something it shows changed: the models, why they could not be read, and the models of the
    // roles their chips name. A stream event that changes none of them leaves the rows as they are.
    const roles = ROLES.map((role) => getSnapshot().status?.models[role.id] ?? null);
    const shown = JSON.stringify([entry, modelsProblem, roles]);
    if (shown === modelsShown) {
      return;
    }
    modelsShown = shown;
    if (entry === null) {
      modelCount.textContent = "";
      modelBox.replaceChildren(
        modelsProblem === null
          ? el("div", { class: "small muted" }, "Reading the models…")
          : el("div", { class: "note bad" }, icon("alert"), el("span", null, `The models could not be read: ${modelsProblem}`)),
      );
      filterModels();
      return;
    }
    modelCount.textContent = String(entry.models.length);
    if (entry.models.length === 0) {
      modelBox.replaceChildren(
        el("div", { class: "note" }, icon("info"), el("span", null, entry.error ?? "This provider named no models.")),
      );
      filterModels();
      return;
    }
    const main = getSnapshot().status?.models.main ?? "";
    const roleOf = (ref) => ROLES.find((role) => getSnapshot().status?.models[role.id] === ref);
    modelBox.replaceChildren(
      ...entry.models.map((model) => {
        const chips = [
          roleOf(model.ref) === undefined ? null : el("span", { class: "chip b-accent" }, roleOf(model.ref).label),
          model.vision ? el("span", { class: "chip" }, "vision") : null,
          model.tools ? null : el("span", { class: "chip" }, "no tools"),
          model.free ? el("span", { class: "chip" }, "free") : null,
          model.ref === main ? el("span", { class: "chip" }, "chosen") : null,
        ].filter((chip) => chip !== null);
        return el(
          "div",
          { class: "provider-model", dataset: { model: model.ref } },
          el("span", { class: "provider-model-name mono" }, modelName(model.ref)),
          el("span", { class: "provider-model-chips" }, chips),
        );
      }),
      showAll,
    );
    filterModels();
  }

  function paint() {
    const set = view.source !== null;
    const using = about.needed ? rolesOn(view.provider) : [];
    element.dataset.state = set ? "set" : "unset";
    line.replaceChildren(keyLine(view));
    stateLine.dataset.tone = set ? "ok" : "off";
    stateLine.replaceChildren(
      ...(set ? [icon("check"), `Connected · ${about.needed ? shortUse(about.use) : "optional"}`] : [about.needed ? "No key yet" : "Optional"]),
    );
    dot.dataset.tone = set ? "ok" : using.length > 0 ? "bad" : "off";
    facts.replaceChildren(
      el("code", { class: "mono provider-var" }, view.name),
      ...(set
        ? [
            el("span", { class: "key-secret mono", title: "The last characters of the key in use" }, `••••${view.tail}`),
            el("span", { class: "provider-source" }, SOURCES[view.source]),
          ]
        : [el("span", { class: "provider-source" }, "not set")]),
      ...using.map((role) =>
        el("span", { class: "chip", title: `Your ${role.label} runs on ${about.label}` }, role.label),
      ),
    );
    shadow.hidden = view.source !== "environment";
    shadow.lastChild.textContent = view.saved
      ? `The daemon was started with ${view.name} in its environment, which comes before the key saved in Dot: that one is not in use.`
      : `The daemon was started with ${view.name} in its environment, which comes before a key saved here.`;
    check.hidden = !set;
    if (judgeSwitch !== null) {
      judgeSwitch.paint();
      judgeSwitch.toggle.disabled = !set || judge === null || !judge.key_set;
    }
    edit.className = set ? "btn" : "btn primary";
    edit.replaceChildren(icon(set ? "pencil" : "plus"), set ? "Replace key" : "Add key");
    remove.hidden = !view.saved;
    paintModels();
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
    paint();
    toast("ok", `${about.label} key saved.`);
    void readModels().then(paint);
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
    void readModels().then(paint);
  }

  /** The form that takes the key, in a dialog (item 615). The key is saved and the dialog closed at once; Check, which
   * asks the provider whether it takes the key, runs on the page after. The field is emptied as the dialog closes. */
  function openKeyForm() {
    outcome.hidden = true;
    const set = view.source !== null;
    const form = buildKeyFields(about.label);
    const opened = formDialog({
      title: `${set ? "Replace" : "Add"} the ${about.label} key`,
      description: "Dot keeps it in its .env file on this Mac, and this page never shows it whole.",
      body: form.element,
      submitLabel: "Save",
      size: "narrow",
      submitOnEnter: true,
      returnFocus: edit,
      onSubmit: async () => {
        if ((await form.submit(saveKey)) === false) {
          return false;
        }
        void attempt(runCheck);
        return true;
      },
    });
    void opened.closed.then(form.clear);
  }

  edit.addEventListener("click", openKeyForm);
  paint();
  return { id: view.provider, element, panel, paint };
}

/** The key of each provider, the form that saves one, and where the keys are kept. */
export function mount(root, ctx) {
  let disposed = false;
  let entries = [];
  let chosen = null;
  const holder = el("div", null, loadingPanel());
  const kept = el("span");
  const list = el("div", { class: "providers-list", role: "group", "aria-label": "Providers" });
  const detail = el("div", { class: "providers-detail" });
  const layout = el("div", { class: "providers-layout" }, list, detail);
  layout.dataset.open = "false";

  function choose(id) {
    chosen = id;
    layout.dataset.open = "true";
    for (const entry of entries) {
      if (entry.id === id) {
        entry.element.setAttribute("aria-current", "true");
      } else {
        entry.element.removeAttribute("aria-current");
      }
    }
    const entry = entries.find((each) => each.id === id);
    if (entry !== undefined) {
      detail.replaceChildren(entry.panel);
      entry.paint();
    }
  }

  // Back on a phone gives the focus to the provider that was open (item 559), where the reader left the list.
  function back() {
    const was = entries.find((entry) => entry.id === chosen) ?? entries[0];
    chosen = null;
    layout.dataset.open = "false";
    for (const entry of entries) {
      entry.element.removeAttribute("aria-current");
    }
    was?.element.focus();
  }

  async function load() {
    let found;
    try {
      [found, judge] = await Promise.all([api.get("/keys"), api.get("/judge")]);
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(retry)));
      }
      return;
    }
    if (disposed) {
      return;
    }
    entries = found.keys.map((view) => buildProvider(view, () => disposed, { choose, back }));
    list.replaceChildren(...entries.map((entry) => entry.element));
    kept.replaceChildren("Keys saved here go to ", el("code", { class: "mono" }, found.env_file), ", which only your user can read.");
    holder.replaceChildren(layout);
    if (!PHONE.matches && entries.length > 0) {
      choose(entries[0].id);
    }
    // The models of every provider are read after the page stands: one provider that answers slowly does not hold the
    // list and the keys back.
    void readModels().then(() => {
      for (const entry of entries) {
        entry.paint();
      }
    });
  }

  async function retry() {
    holder.replaceChildren(loadingPanel());
    await load();
  }

  ctx.head.setLead("The keys Dot signs in to its models with. This page never shows a whole key.");
  root.append(
    holder,
    el(
      "div",
      { class: "stack tight" },
      el("div", { class: "note" }, icon("lock"), kept),
      el(
        "div",
        { class: "note" },
        icon("info"),
        el(
          "span",
          null,
          "A new key is used from the next request on, without a restart. The memory search and MCP servers read their keys when the daemon starts: after changing a key they use, restart it with ",
          el("code", { class: "mono" }, "dotx service restart"),
          ".",
        ),
      ),
    ),
  );
  const stop = onChange(() => {
    for (const entry of entries) {
      entry.paint();
    }
  });
  attempt(refresh);
  attempt(load);
  return () => {
    disposed = true;
    stop();
  };
}

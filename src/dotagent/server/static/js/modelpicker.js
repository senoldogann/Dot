// The menu of the composer's Model pill (item 208): a rail on its left with a star for the
// favourites and the mark of each provider, and the models of the view the rail shows, which a search narrows (item
// 395), each with a star that keeps it among the favourites. The favourites are kept in this browser, as the
// home's choices are; the menu opens on them when there are any, else on the provider of the model a run would use.
// The focus stays in the search: the arrows move the active row, Enter picks it, ⌘1 to ⌘9 pick the rows that show
// those keys, ⌘D stars the active row, and the left arrow (on an empty search) goes to the rail, the right arrow back.
// The default's row has Change (item 209): the list then chooses the daemon's default instead of the chat's model.
// Settings › Models opens the same menu from each of its roles (item 1044), with no Change, and with the providers
// whose models cannot play the role on the rail all the same, their view saying why.

import { el, foldSearch, modelName } from "./dom.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { readPref, writePref } from "./prefs.js";
import { providerLabel, providerMark, providerOf } from "./providers.js";

const FAVOURITES_KEY = "dot.models.favourites";
// The view of the favourites; the rail's other views are the providers, by their ids.
const FAVOURITES = "favourites";
// The model rows that wear a key, ⌘1 to ⌘9.
const KEYED_ROWS = 9;
// A match in a model's name counts before one in its provider's: each field after the first adds this much.
const FIELD_STEP = 4;

// The menus made on this page, for their lists' ids.
let made = 0;

/** The references of the models kept as favourites in this browser; anything unreadable counts as none. */
function readFavourites() {
  let stored;
  try {
    stored = JSON.parse(readPref(FAVOURITES_KEY) ?? "[]");
  } catch {
    return [];
  }
  return Array.isArray(stored) && stored.every((ref) => typeof ref === "string") ? stored : [];
}

function writeFavourites(refs) {
  writePref(FAVOURITES_KEY, JSON.stringify(refs));
}

// Where `word` lies in `field`, both folded, lower being closer: the whole field, its start, a word's start, anywhere
// else; null when it is not in it.
function placeScore(field, word) {
  if (field === word) {
    return 0;
  }
  const at = field.indexOf(word);
  if (at === -1) {
    return null;
  }
  if (at === 0) {
    return 1;
  }
  return /[\p{L}\p{N}]/u.test(field[at - 1]) ? 3 : 2;
}

/** How close `fields` (folded, the first counting most) are to the search `words`: lower is closer, null when a word is
 * in none of them. */
function searchScore(fields, words) {
  let total = 0;
  for (const word of words) {
    const scores = fields
      .map((field, index) => {
        const place = placeScore(field, word);
        return place === null ? null : index * FIELD_STEP + place;
      })
      .filter((score) => score !== null);
    if (scores.length === 0) {
      return null;
    }
    total += Math.min(...scores);
  }
  return total;
}

// Every model of `groups` with its provider, in the order the providers list them.
function entriesOf(groups) {
  return groups.flatMap((group) => group.models.map((info) => ({ info, provider: group.provider })));
}

/** The models of a view: a provider's, or the favourites; in the order the providers list them. */
function viewEntries(groups, view, favourites) {
  const entries = entriesOf(groups);
  return view === FAVOURITES
    ? entries.filter(({ info }) => favourites.includes(info.ref))
    : entries.filter(({ provider }) => provider === view);
}

// What a search reads of a model: its name, its provider's name and id, "free" when it costs nothing, and its id, so
// that `claude-opus-4-7` finds Opus 4.7 (item 1054).
function searchFields({ info, provider }) {
  return [
    foldSearch(modelName(info.ref)),
    foldSearch(providerLabel(provider)),
    provider,
    info.free ? "free" : "",
    foldSearch(info.ref.slice(info.ref.indexOf(":") + 1)),
  ];
}

/** The models of `entries` that match the search `words`, closest first; on a tie the favourites first, then the
 * providers' order. */
function foundEntries(entries, words, favourites) {
  return entries
    .map((entry, order) => ({
      entry,
      order,
      favourite: favourites.includes(entry.info.ref),
      score: searchScore(searchFields(entry), words),
    }))
    .filter(({ score }) => score !== null)
    .sort((a, b) => a.score - b.score || Number(b.favourite) - Number(a.favourite) || a.order - b.order)
    .map(({ entry }) => entry);
}

/** Where a menu opened anew starts: on the favourites when any of them is offered, else on the provider of `current`
 * (the model chosen now, or null) when it offers models here, else on the first provider that does. */
function openingView(groups, favourites, current) {
  if (entriesOf(groups).some(({ info }) => favourites.includes(info.ref))) {
    return FAVOURITES;
  }
  const provider = current === null ? "" : providerOf(current);
  if (groups.some((group) => group.provider === provider && group.models.length > 0)) {
    return provider;
  }
  const open = groups.find((group) => group.models.length > 0);
  return open === undefined ? FAVOURITES : open.provider;
}

/** The words of the daemon's default model, for the row that follows it in the composer's menu. */
export function defaultRowText(ref) {
  return ref === null ? t("modelpicker.defaultModel") : t("modelpicker.defaultModelname", { modelName: modelName(ref) });
}

/** The models a search looks through in `view`, in words: "OpenAI models", or "favourites". */
function viewModels(view) {
  return view === FAVOURITES ? "favourites" : t("modelpicker.providerlabelModels", { providerLabel: providerLabel(view) });
}

// A press on a row or a star leaves the focus in the search.
function keepFocus(event) {
  event.preventDefault();
}

/**
 * A model menu, for `anchoredPopover`: `build({ groups, chosen, fallback, blank })` returns its content, where `groups`
 * are the models that may be picked, by provider (`{ provider, models, why }`: a provider whose models cannot be picked
 * here has none and `why` says so, null otherwise), `chosen` the reference picked ("" for the blank row), `fallback` the
 * model the blank row stands for (null while it is not known) and `blank` the blank row's words (null for no blank
 * row); `onPick(ref)` runs when a row is picked ("" for the blank one). With `onMakeDefault` (the composer's menu, null
 * in Settings), Change on the blank row turns the list to choosing the daemon's default: a row picked then goes to
 * `onMakeDefault(ref)`, which resolves to whether the daemon took it, and the list goes back to the chat's model once
 * it did; Escape or Cancel goes back at once, and the menu stays open. A menu opened anew starts on its opening view
 * with an empty search; one built again while it is open (a repaint) keeps its search, its view and what it chooses.
 */
export function modelPicker({ onPick, onMakeDefault }) {
  made += 1;
  const listId = `model-list-${made}`;
  let query = "";
  let view = FAVOURITES;
  // Whether the view was chosen before the models or the daemon's default were known: it is chosen again once they are.
  let guessed = false;
  // The menu as last built: no longer on the page once the menu has closed.
  let root = null;
  // Whether the list chooses the daemon's default instead of the chat's model, and the model being made the default
  // (null while none is).
  let choosingDefault = false;
  let making = null;
  // Paints the menu as last built again, while it is open: what a default being made ended in.
  let repaintBuilt = () => undefined;

  function build({ groups, chosen, fallback, blank }) {
    const views = [FAVOURITES, ...groups.map((group) => group.provider)];
    const anew = root === null || !root.isConnected;
    if (anew) {
      query = "";
      choosingDefault = false;
    }
    if (anew || guessed || !views.includes(view)) {
      view = openingView(groups, readFavourites(), chosen === "" ? fallback : chosen);
      guessed = groups.length === 0 || (chosen === "" && fallback === null && blank !== null);
    }
    // Where a pointer can hover, a keyboard is likely: the search takes the focus and the rows show their keys. On a
    // touch screen the focus stays off the search, so that no keyboard springs up over the list.
    const keyboard = window.matchMedia("(hover: hover)").matches;
    // The rows shown, `{ option, ref, star }` (no star on the default's row), and the one the keys are on.
    let rows = [];
    let active = -1;
    // The references the rows that wear ⌘1 to ⌘9 pick, in order.
    let keyed = [];

    const search = el("input", {
      class: "model-search-input",
      type: "text",
      value: query,
      role: "combobox",
      "aria-autocomplete": "list",
      "aria-expanded": "true",
      "aria-controls": listId,
      autocomplete: "off",
      spellcheck: "false",
      "data-autofocus": keyboard ? "" : null,
    });
    const list = el("div", { class: "model-list", id: listId, role: "listbox", "aria-label": t("modelpicker.models") });
    // What the list lacks: no model matches the search, or no model is a favourite yet.
    const note = el("p", { class: "model-note", hidden: true });
    const railItems = views.map((id) => {
      const label = id === FAVOURITES ? t("modelpicker.favourites") : providerLabel(id);
      return el(
        "button",
        { class: "model-rail-item", type: "button", title: label, "aria-label": label, "data-view": id, onclick: () => showView(id) },
        id === FAVOURITES ? icon("star") : providerMark(id),
      );
    });
    const rail = el(
      "div",
      { class: "model-rail", role: "toolbar", "aria-orientation": "vertical", "aria-label": t("modelpicker.aiServices") },
      railItems[0],
      el("div", { class: "model-rail-rule", "aria-hidden": "true" }),
      railItems.slice(1),
    );

    // The rail presses the view shown, whose models the search looks through, as its placeholder and its label say;
    // the item pressed, or the first, is the rail's one stop for Tab.
    function paintRail() {
      const scope = t("modelpicker.searchViewmodels", { viewModels: viewModels(view) });
      search.placeholder = scope;
      search.setAttribute("aria-label", scope);
      for (const item of railItems) {
        item.setAttribute("aria-pressed", String(item.dataset.view === view));
      }
      const stop = railItems.find((item) => item.getAttribute("aria-pressed") === "true") ?? railItems[0];
      for (const item of railItems) {
        item.tabIndex = item === stop ? 0 : -1;
      }
    }

    function setActive(index) {
      rows[active]?.option.classList.remove("active");
      active = index;
      if (active === -1) {
        search.removeAttribute("aria-activedescendant");
        return;
      }
      rows[active].option.classList.add("active");
      search.setAttribute("aria-activedescendant", rows[active].option.id);
    }

    // Scrolls the list, and nothing around it, so that `option` is in sight.
    function reveal(option) {
      const box = list.getBoundingClientRect();
      const row = option.getBoundingClientRect();
      if (row.top < box.top) {
        list.scrollTop -= box.top - row.top;
      } else if (row.bottom > box.bottom) {
        list.scrollTop += row.bottom - box.bottom;
      }
    }

    function moveActive(step) {
      if (rows.length === 0) {
        return;
      }
      setActive((active + step + rows.length) % rows.length);
      reveal(rows[active].option);
    }

    function paintStar(star, starred) {
      star.setAttribute("aria-pressed", String(starred));
      star.title = starred ? t("modelpicker.removeFromFavouritesD") : t("modelpicker.addToFavouritesD");
    }

    function toggleFavourite(row) {
      const kept = readFavourites();
      const starred = !kept.includes(row.ref);
      writeFavourites(starred ? [...kept, row.ref] : kept.filter((ref) => ref !== row.ref));
      paintStar(row.star, starred);
    }

    // The model the tick is on: the chat's, or the daemon's default while the list chooses it.
    function tickedRef() {
      return choosingDefault ? fallback : chosen;
    }

    // A row picked: the chat's model, or the daemon's default while the list chooses it.
    function pickRow(ref) {
      if (choosingDefault) {
        makeDefault(ref);
      } else {
        onPick(ref);
      }
    }

    function chooseDefault() {
      choosingDefault = true;
      paintList();
      if (keyboard) {
        search.focus();
      }
    }

    function leaveChoosing() {
      choosingDefault = false;
      paintList();
      if (keyboard) {
        search.focus();
      }
    }

    // One default is made at a time; the list says which while the daemon takes it.
    function makeDefault(ref) {
      if (making !== null) {
        return;
      }
      if (ref === fallback) {
        leaveChoosing();
        return;
      }
      making = ref;
      paintList();
      Promise.resolve(onMakeDefault(ref))
        .then((taken) => {
          if (taken) {
            choosingDefault = false;
          }
        })
        .finally(() => {
          making = null;
          repaintBuilt();
        });
    }

    function optionOf({ lead, label, hint, key, ref }) {
      const index = rows.length;
      const option = el(
        "button",
        {
          class: "pop-option model-option",
          type: "button",
          role: "option",
          id: `${listId}-${index}`,
          tabindex: "-1",
          "aria-selected": String(ref === tickedRef()),
          onmousedown: keepFocus,
          onpointermove: () => {
            if (active !== index) {
              setActive(index);
            }
          },
          onclick: () => pickRow(ref),
        },
        lead,
        el("span", { class: "pop-option-text" }, el("span", null, label), hint === "" ? null : el("span", { class: "pop-option-hint" }, hint)),
        key === null ? null : el("kbd", { "aria-hidden": "true" }, `⌘${key}`),
        icon("check"),
      );
      const row = { option, ref, star: null };
      rows.push(row);
      return row;
    }

    // The blank row, with Change in the composer's menu, which turns the list to choosing the default (once the default
    // is known).
    function blankRow(words) {
      const { option } = optionOf({
        lead: providerMark(fallback === null ? "" : providerOf(fallback)),
        label: words,
        hint: "",
        key: null,
        ref: "",
      });
      const change =
        fallback === null || onMakeDefault === null
          ? el("span", { class: "model-star-room", "aria-hidden": "true" })
          : el(
              "button",
              { class: "model-change", type: "button", title: t("modelpicker.chooseTheModelNewChats"), onmousedown: keepFocus, onclick: chooseDefault },
              t("modelpicker.change"),
            );
      return el("div", { class: "model-row", role: "none" }, option, change);
    }

    // While the list chooses the default: what it does, or the model being made the default, and the way back.
    const choosingText = el("span", { class: "model-choosing-text", "aria-live": "polite" });
    const choosing = el(
      "div",
      { class: "model-choosing", hidden: true },
      choosingText,
      el("button", { class: "model-change", type: "button", onmousedown: keepFocus, onclick: leaveChoosing }, t("modelpicker.cancel")),
    );

    function paintChoosing() {
      choosing.hidden = !choosingDefault;
      choosingText.textContent = making === null ? t("modelpicker.chooseTheDefaultModel") : t("modelpicker.makingModelnameTheDefault", { modelName: modelName(making) });
    }

    // A model's row: in a view of one provider its name, and "Free" when it costs nothing (and that its provider may
    // train on what it is sent, when it may); among the favourites its provider's name too.
    function modelRow({ info, provider }, key, mixed, starred) {
      const name = modelName(info.ref);
      const free = info.free ? (info.learns ? t("modelpicker.freeMayTrainOnYour") : t("modelpicker.free")) : "";
      const hint = mixed ? [providerLabel(provider), free.toLowerCase()].filter((part) => part !== "").join(", ") : free;
      const row = optionOf({ lead: providerMark(provider), label: name, hint, key, ref: info.ref });
      row.option.title = info.ref;
      row.star = el(
        "button",
        { class: "model-star", type: "button", tabindex: "-1", "aria-label": t("modelpicker.favouriteName", { name }), onmousedown: keepFocus, onclick: () => toggleFavourite(row) },
        icon("star"),
      );
      paintStar(row.star, starred);
      return el("div", { class: "model-row", role: "none" }, row.option, row.star);
    }

    // The rows of the view, or of those of its models the search finds: the blank row first (in a search only when its
    // words match; never while the list chooses the default), then the models, the first nine with their keys. A row
    // starred or not stays where it is until the list is shown again. A provider's view whose models cannot be picked
    // here says why.
    function paintList() {
      const words = foldSearch(query)
        .split(/\s+/)
        .filter((word) => word !== "");
      const searching = words.length > 0;
      const favourites = readFavourites();
      const shown = viewEntries(groups, view, favourites);
      const entries = searching ? foundEntries(shown, words, favourites) : shown;
      paintChoosing();
      setActive(-1);
      rows = [];
      const children = [];
      if (blank !== null && !choosingDefault && (!searching || searchScore([foldSearch(blank)], words) !== null)) {
        children.push(blankRow(blank));
      }
      entries.forEach((entry, index) => {
        const key = index < KEYED_ROWS ? index + 1 : null;
        children.push(modelRow(entry, keyboard ? key : null, view === FAVOURITES, favourites.includes(entry.info.ref)));
      });
      keyed = entries.slice(0, KEYED_ROWS).map(({ info }) => info.ref);
      const closed = groups.find((group) => group.provider === view)?.why ?? null;
      let lack = "";
      if (closed !== null) {
        lack = closed;
      } else if (searching && rows.length === 0) {
        lack = t("modelpicker.noViewmodelsMatchTrim", { viewModels: viewModels(view), trim: query.trim() });
      } else if (!searching && view === FAVOURITES && entries.length === 0) {
        lack = t("modelpicker.noFavouritesYetStarA");
      }
      note.textContent = lack;
      note.hidden = lack === "";
      list.replaceChildren(...children);
      list.scrollTop = 0;
      if (rows.length > 0) {
        const current = rows.findIndex((row) => row.ref === tickedRef());
        setActive(searching || current === -1 ? 0 : current);
        // A menu being built is not on the page yet: its list is measured once it is.
        queueMicrotask(() => {
          if (active !== -1) {
            reveal(rows[active].option);
          }
        });
      }
    }

    // Another view keeps the search, which looks through its models then.
    function showView(id) {
      view = id;
      paintRail();
      paintList();
      if (keyboard) {
        search.focus();
      }
    }

    search.addEventListener("input", () => {
      query = search.value;
      paintList();
    });
    search.addEventListener("keydown", (event) => {
      if (event.isComposing) {
        return;
      }
      const command = (event.metaKey || event.ctrlKey) && !event.altKey;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        moveActive(event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (active !== -1) {
          pickRow(rows[active].ref);
        }
      } else if (command && /^Digit[1-9]$/.test(event.code)) {
        // Read by where the key sits, with or without Shift, as a layout may need Shift for its digits.
        event.preventDefault();
        const ref = keyed[Number(event.code.slice("Digit".length)) - 1];
        if (ref !== undefined) {
          pickRow(ref);
        }
      } else if (command && !event.shiftKey && event.key.toLowerCase() === "d") {
        event.preventDefault();
        if (active !== -1 && rows[active].star !== null) {
          toggleFavourite(rows[active]);
        }
      } else if ((event.key === "ArrowLeft" && search.value === "") || (event.key === "Tab" && event.shiftKey)) {
        event.preventDefault();
        railItems.find((item) => item.tabIndex === 0).focus();
      }
    });
    rail.addEventListener("keydown", (event) => {
      const at = railItems.indexOf(document.activeElement);
      if (at === -1) {
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        railItems[(at + (event.key === "ArrowDown" ? 1 : -1) + railItems.length) % railItems.length].focus();
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        search.focus();
      }
    });

    const built = el(
      "div",
      { class: "model-picker" },
      rail,
      el("div", { class: "model-main" }, el("label", { class: "model-search" }, icon("search"), search), choosing, list, note),
    );
    // Escape leaves the choosing of the default, wherever the keys are in the menu, and the menu stays open.
    built.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && choosingDefault) {
        event.preventDefault();
        event.stopPropagation();
        leaveChoosing();
      }
    });
    root = built;
    repaintBuilt = () => {
      if (built.isConnected) {
        paintList();
      }
    };
    paintRail();
    paintList();
    return [root];
  }

  return { build };
}

/**
 * The models as a phone's chat settings show them (item 1202, after another assistant's thread settings): the blank
 * row of the daemon's default first, with Change, then the favourites, then a section per provider that opens and
 * folds, each row with a tick on the one chosen and a star; a search at the sheet's foot narrows them to one list,
 * closest first. `build({ groups, chosen, fallback, blank })` takes what `modelPicker`'s does and returns
 * `{ list, search }`: the element of the sections, painted again as the search changes, and the search's field for the
 * foot. `onPick(ref)` runs when a row is picked ("" for the blank one). Change turns the list to choosing the daemon's
 * default, as `modelPicker`'s does (item 209): a row picked then goes to `onMakeDefault(ref)`, which resolves to whether
 * the daemon took it, and Cancel goes back. The sections opened or folded by hand and the search stay as they were
 * while the sheet is open; a sheet opened anew starts with an empty search and the chosen model's provider open.
 */
export function modelSections({ onPick, onMakeDefault }) {
  let query = "";
  // The providers opened (true) or folded (false) by hand; the rest follow where the chosen model is.
  let folded = new Map();
  let root = null;
  // Whether the list chooses the daemon's default, and the model being made the default (null while none is).
  let choosingDefault = false;
  let making = null;
  // Paints the list as last built again, while it is on the page: what a default being made ended in, and the search.
  let paintBuilt = () => undefined;

  // Escape while the list chooses the default leaves the choosing, wherever the focus is, and the sheet stays open, as
  // on a Mac's menu: heard before the sheet's own Escape, and only while the list chooses.
  function onEscape(event) {
    // The sheet put away while the list chose: the choosing ends with it, and Escape is no longer the list's.
    if (root === null || !root.isConnected) {
      setChoosing(false);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setChoosing(false);
      paintBuilt();
    }
  }
  function setChoosing(on) {
    choosingDefault = on;
    if (on) {
      document.addEventListener("keydown", onEscape, true);
    } else {
      document.removeEventListener("keydown", onEscape, true);
    }
  }
  // One field for the search, kept across the sheet's repaints, so that a repaint (a choice the daemon changed, the
  // models come) takes neither the focus nor the keyboard from it.
  const search = el("input", {
    class: "sheet-search-field",
    type: "search",
    placeholder: t("choices.searchModels"),
    "aria-label": t("choices.searchModels"),
    autocomplete: "off",
    enterkeyhint: "search",
  });
  const searchBox = el("label", { class: "sheet-search" }, icon("search"), search);
  search.addEventListener("input", () => {
    query = search.value;
    paintBuilt();
  });

  function build({ groups, chosen, fallback, blank }) {
    if (root === null || !root.isConnected) {
      query = "";
      folded = new Map();
      setChoosing(false);
    }
    const list = el("div", { class: "model-sections", role: "listbox", "aria-label": t("choices.model") });
    search.value = query;
    const chosenProvider = chosen === "" ? (fallback === null ? "" : providerOf(fallback)) : providerOf(chosen);

    // The model the tick is on: the chat's, or the daemon's default while the list chooses it.
    function tickedRef() {
      return choosingDefault ? fallback : chosen;
    }

    function pickRow(ref) {
      if (!choosingDefault) {
        onPick(ref);
        return;
      }
      if (making !== null) {
        return;
      }
      if (ref === fallback) {
        setChoosing(false);
        paint();
        return;
      }
      making = ref;
      paint();
      Promise.resolve(onMakeDefault(ref))
        .then((taken) => {
          if (taken) {
            setChoosing(false);
          }
        })
        .finally(() => {
          making = null;
          paintBuilt();
        });
    }

    function row({ info, provider }, mixed, favourites) {
      const name = modelName(info.ref);
      const free = info.free ? (info.learns ? t("choices.freeMayTrain") : t("choices.free")) : "";
      const hint = mixed ? [providerLabel(provider), free.toLowerCase()].filter((part) => part !== "").join(", ") : free;
      const starred = favourites.includes(info.ref);
      const star = el(
        "button",
        {
          class: "model-star",
          type: "button",
          "aria-label": t("choices.favouriteOf", { name }),
          "aria-pressed": String(starred),
          onclick: () => {
            const now = readFavourites();
            writeFavourites(now.includes(info.ref) ? now.filter((ref) => ref !== info.ref) : [...now, info.ref]);
            paint();
          },
        },
        icon("star"),
      );
      const option = el(
        "button",
        { class: "sheet-row model-sheet-option", type: "button", role: "option", title: info.ref, "aria-selected": String(info.ref === tickedRef()), onclick: () => pickRow(info.ref) },
        el("span", { class: "sheet-row-text" }, el("span", null, name), hint === "" ? null : el("span", { class: "sheet-row-hint" }, hint)),
        icon("check"),
      );
      return el("div", { class: "model-sheet-row" }, option, star);
    }

    function card(rows) {
      return el("div", { class: "sheet-card" }, ...rows);
    }

    // The blank row, with Change once the default is known; while the list chooses the default, what it does (or the
    // model being made the default) and Cancel instead.
    function blankRow() {
      if (choosingDefault) {
        return el(
          "div",
          { class: "model-sheet-row model-choosing" },
          el("span", { class: "sheet-row model-choosing-text", "aria-live": "polite" }, making === null ? t("choices.chooseDefault") : t("choices.makingDefault", { model: modelName(making) })),
          el(
            "button",
            {
              class: "model-change",
              type: "button",
              onclick: () => {
                setChoosing(false);
                paint();
              },
            },
            t("dialog.cancel"),
          ),
        );
      }
      return el(
        "div",
        { class: "model-sheet-row" },
        el(
          "button",
          { class: "sheet-row model-sheet-option", type: "button", role: "option", "aria-selected": String(chosen === ""), onclick: () => onPick("") },
          el("span", { class: "sheet-row-text" }, el("span", null, blank)),
          icon("check"),
        ),
        fallback === null
          ? null
          : el(
              "button",
              {
                class: "model-change",
                type: "button",
                title: t("choices.changeTitle"),
                onclick: () => {
                  setChoosing(true);
                  paint();
                },
              },
              t("choices.change"),
            ),
      );
    }

    function section(provider, entries, favourites) {
      const open = folded.get(provider) ?? provider === chosenProvider;
      const count = () => el("span", { class: "sheet-section-count" }, String(entries.length));
      const head = el("summary", { class: "sheet-section-head" }, providerMark(provider), el("span", { class: "sheet-section-title" }, providerLabel(provider)), open ? null : count(), icon("chevron"));
      const details = el("details", { class: "sheet-section", open }, head, card(entries.map((entry) => row(entry, false, favourites))));
      // A section folded shows how many models it holds, as the other assistant's does.
      details.addEventListener("toggle", () => {
        folded.set(provider, details.open);
        head.querySelector(".sheet-section-count")?.remove();
        if (!details.open) {
          head.insertBefore(count(), head.lastElementChild);
        }
      });
      return details;
    }

    function paint() {
      const words = foldSearch(query)
        .split(/\s+/)
        .filter((word) => word !== "");
      const favourites = readFavourites();
      const entries = entriesOf(groups);
      const children = [];
      if (blank !== null && (words.length === 0 || choosingDefault)) {
        children.push(card([blankRow()]));
      }
      if (words.length > 0) {
        const found = foundEntries(entries, words, favourites);
        children.push(
          found.length === 0
            ? el("p", { class: "sheet-note" }, t("choices.noModelFound", { query: query.trim() }))
            : card(found.map((entry) => row(entry, true, favourites))),
        );
      } else {
        const starred = entries.filter(({ info }) => favourites.includes(info.ref));
        if (starred.length > 0) {
          children.push(el("div", { class: "sheet-heading" }, t("choices.favourites")), card(starred.map((entry) => row(entry, true, favourites))));
        }
        for (const group of groups) {
          if (group.models.length > 0) {
            children.push(section(group.provider, viewEntries(groups, group.provider, favourites), favourites));
          }
        }
      }
      list.replaceChildren(...children);
    }

    paint();
    root = list;
    paintBuilt = () => {
      if (list.isConnected) {
        paint();
      }
    };
    return { list, search: searchBox };
  }

  return { build };
}

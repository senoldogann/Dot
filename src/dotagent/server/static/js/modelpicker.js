// The menu of the composer's Model pill (item 208), after T3 Code's model picker: a rail on its left with a star for the
// favourites and the mark of each provider, a search over the models of every provider, and the models of the view the
// rail shows, each with a star that keeps it among the favourites. The favourites are kept in this browser, as the
// home's choices are; the menu opens on them when there are any, else on the provider of the model a run would use.
// The focus stays in the search: the arrows move the active row, Enter picks it, ⌘1 to ⌘9 pick the rows that show
// those keys, ⌘D stars the active row, and the left arrow (on an empty search) goes to the rail, the right arrow back.

import { el, foldSearch, modelName } from "./dom.js";
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

// What a search reads of a model: its name, its provider's name and id, and "free" when it costs nothing.
function searchFields({ info, provider }) {
  return [foldSearch(modelName(info.ref)), foldSearch(providerLabel(provider)), provider, info.free ? "free" : ""];
}

/** The models that match the search `words`, closest first; on a tie the favourites first, then the providers' order. */
function foundEntries(groups, words, favourites) {
  return entriesOf(groups)
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
 * (the model a run would use, or null), else on the first provider. */
function openingView(groups, favourites, current) {
  if (entriesOf(groups).some(({ info }) => favourites.includes(info.ref))) {
    return FAVOURITES;
  }
  const provider = current === null ? "" : providerOf(current);
  if (groups.some((group) => group.provider === provider)) {
    return provider;
  }
  return groups.length > 0 ? groups[0].provider : FAVOURITES;
}

/** The words of the daemon's default model, for the row that follows it. */
function defaultRowText(ref) {
  return ref === null ? "Default model" : `Default (${modelName(ref)})`;
}

// A press on a row or a star leaves the focus in the search.
function keepFocus(event) {
  event.preventDefault();
}

/**
 * The model menu of one composer, for `anchoredPopover`: `build({ groups, chosen, fallback })` returns its content, where
 * `groups` are the models runs can use, by provider (`{ provider, models }`), `chosen` the reference picked ("" for the
 * daemon's default) and `fallback` the daemon's default (null while it is not known); `onPick(ref)` runs when a row is
 * picked ("" for the default). A menu opened anew starts on its opening view with an empty search; one built again
 * while it is open (a repaint) keeps its search and its view.
 */
export function modelPicker({ onPick }) {
  made += 1;
  const listId = `model-list-${made}`;
  let query = "";
  let view = FAVOURITES;
  // Whether the view was chosen before the models or the daemon's default were known: it is chosen again once they are.
  let guessed = false;
  // The menu as last built: no longer on the page once the menu has closed.
  let root = null;

  function build({ groups, chosen, fallback }) {
    const views = [FAVOURITES, ...groups.map((group) => group.provider)];
    const anew = root === null || !root.isConnected;
    if (anew) {
      query = "";
    }
    if (anew || guessed || !views.includes(view)) {
      view = openingView(groups, readFavourites(), chosen === "" ? fallback : chosen);
      guessed = groups.length === 0 || (chosen === "" && fallback === null);
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
      placeholder: "Search models",
      "aria-label": "Search models",
      role: "combobox",
      "aria-autocomplete": "list",
      "aria-expanded": "true",
      "aria-controls": listId,
      autocomplete: "off",
      spellcheck: "false",
      "data-autofocus": keyboard ? "" : null,
    });
    const list = el("div", { class: "model-list", id: listId, role: "listbox", "aria-label": "Models" });
    // What the list lacks: no model matches the search, or no model is a favourite yet.
    const note = el("p", { class: "model-note", hidden: true });
    const railItems = views.map((id) => {
      const label = id === FAVOURITES ? "Favourites" : providerLabel(id);
      return el(
        "button",
        { class: "model-rail-item", type: "button", title: label, "aria-label": label, "data-view": id, onclick: () => showView(id) },
        id === FAVOURITES ? icon("star") : providerMark(id),
      );
    });
    const rail = el(
      "div",
      { class: "model-rail", role: "toolbar", "aria-orientation": "vertical", "aria-label": "Providers" },
      railItems[0],
      el("div", { class: "model-rail-rule", "aria-hidden": "true" }),
      railItems.slice(1),
    );

    // The rail presses the view shown, none while a search is under way; the item pressed, or the first, is the rail's
    // one stop for Tab.
    function paintRail() {
      const searching = query.trim() !== "";
      for (const item of railItems) {
        item.setAttribute("aria-pressed", String(!searching && item.dataset.view === view));
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
      star.title = starred ? "Remove from favourites (⌘D)" : "Add to favourites (⌘D)";
    }

    function toggleFavourite(row) {
      const kept = readFavourites();
      const starred = !kept.includes(row.ref);
      writeFavourites(starred ? [...kept, row.ref] : kept.filter((ref) => ref !== row.ref));
      paintStar(row.star, starred);
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
          "aria-selected": String(ref === chosen),
          onmousedown: keepFocus,
          onpointermove: () => {
            if (active !== index) {
              setActive(index);
            }
          },
          onclick: () => onPick(ref),
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

    function defaultRow() {
      const { option } = optionOf({
        lead: providerMark(fallback === null ? "" : providerOf(fallback)),
        label: defaultRowText(fallback),
        hint: "",
        key: null,
        ref: "",
      });
      return el("div", { class: "model-row", role: "none" }, option, el("span", { class: "model-star-room", "aria-hidden": "true" }));
    }

    // A model's row: in a view of one provider its name, and "Free" when it costs nothing; among the favourites and in a
    // search its provider's name too.
    function modelRow({ info, provider }, key, mixed, starred) {
      const name = modelName(info.ref);
      const free = info.free ? "Free" : "";
      const hint = mixed ? [providerLabel(provider), free.toLowerCase()].filter((part) => part !== "").join(", ") : free;
      const row = optionOf({ lead: providerMark(provider), label: name, hint, key, ref: info.ref });
      row.option.title = info.ref;
      row.star = el(
        "button",
        { class: "model-star", type: "button", tabindex: "-1", "aria-label": `Favourite ${name}`, onmousedown: keepFocus, onclick: () => toggleFavourite(row) },
        icon("star"),
      );
      paintStar(row.star, starred);
      return el("div", { class: "model-row", role: "none" }, row.option, row.star);
    }

    // The rows of the search, or of the view when there is none: the default's row first (in a search only when its
    // words match), then the models, the first nine with their keys. A row starred or not stays where it is until the
    // list is shown again.
    function paintList() {
      const words = foldSearch(query)
        .split(/\s+/)
        .filter((word) => word !== "");
      const searching = words.length > 0;
      const favourites = readFavourites();
      const entries = searching ? foundEntries(groups, words, favourites) : viewEntries(groups, view, favourites);
      setActive(-1);
      rows = [];
      const children = [];
      if (!searching || searchScore([foldSearch(defaultRowText(fallback))], words) !== null) {
        children.push(defaultRow());
      }
      entries.forEach((entry, index) => {
        const key = index < KEYED_ROWS ? index + 1 : null;
        children.push(modelRow(entry, keyboard ? key : null, searching || view === FAVOURITES, favourites.includes(entry.info.ref)));
      });
      keyed = entries.slice(0, KEYED_ROWS).map(({ info }) => info.ref);
      let lack = "";
      if (searching && rows.length === 0) {
        lack = `No model matches “${query.trim()}”.`;
      } else if (!searching && view === FAVOURITES && entries.length === 0) {
        lack = "No favourites yet: star a model to keep it here.";
      }
      note.textContent = lack;
      note.hidden = lack === "";
      list.replaceChildren(...children);
      list.scrollTop = 0;
      if (rows.length > 0) {
        const current = rows.findIndex((row) => row.ref === chosen);
        setActive(searching || current === -1 ? 0 : current);
        // A menu being built is not on the page yet: its list is measured once it is.
        queueMicrotask(() => {
          if (active !== -1) {
            reveal(rows[active].option);
          }
        });
      }
    }

    function showView(id) {
      view = id;
      query = "";
      search.value = "";
      paintRail();
      paintList();
      if (keyboard) {
        search.focus();
      }
    }

    search.addEventListener("input", () => {
      query = search.value;
      paintRail();
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
          onPick(rows[active].ref);
        }
      } else if (command && /^Digit[1-9]$/.test(event.code)) {
        // Read by where the key sits, with or without Shift, as a layout may need Shift for its digits.
        event.preventDefault();
        const ref = keyed[Number(event.code.slice("Digit".length)) - 1];
        if (ref !== undefined) {
          onPick(ref);
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

    root = el(
      "div",
      { class: "model-picker" },
      rail,
      el("div", { class: "model-main" }, el("label", { class: "model-search" }, icon("search"), search), list, note),
    );
    paintRail();
    paintList();
    return [root];
  }

  return { build };
}

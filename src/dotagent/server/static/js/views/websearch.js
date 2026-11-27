// Settings › Web search (item 657): the engines web_search asks, in turn, the first that answers giving the results; a
// row moves up or down or leaves the list, and one that is off comes back at its foot. A search API's key is saved here
// (Dot's .env, never shown again but its last characters), and Try asks an engine one search. A change holds from the
// next search, with no restart (`GET /api/web/search`, `PUT /api/web/search/order`, `POST|DELETE
// /api/web/search/keys/{engine}`, `POST /api/web/search/try/{engine}`).

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { errorPanel, loadingPanel, setCard, setRow, setSection } from "../panels.js";
import { attempt, toast } from "../ui.js";
import { t } from "../i18n.js";

/** The order of the engines that are on, as the view gives them. */
function orderOf(view) {
  return view.engines.filter((engine) => engine.on).map((engine) => engine.engine);
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  // What Try said of each engine, kept while the page is open.
  const tried = new Map();
  const holder = el("div", { class: "stack" }, loadingPanel());

  async function setOrder(order) {
    view = await api.put("/web/search/order", { order });
    paint();
  }

  function moved(order, index, by) {
    const next = [...order];
    const [engine] = next.splice(index, 1);
    next.splice(index + by, 0, engine);
    return next;
  }

  /** What an engine stands on now: ready, or what it needs; and what Try last said. */
  function state(engine) {
    const said = tried.get(engine.engine);
    const parts = [
      engine.ready
        ? el("span", { class: "websearch-ready" }, icon("check"), t("views.websearch.ready"))
        : el("span", { class: "websearch-needs" }, icon("alert"), t("views.websearch.needsNeeds", { needs: engine.needs })),
    ];
    if (said !== undefined) {
      parts.push(
        el(
          "span",
          { class: said.ok ? "websearch-tried" : "websearch-tried bad" },
          said.ok ? t("views.websearch.triedLengthResultsInSeconds", { length: said.titles.length, seconds: said.seconds }) : t("views.websearch.triedError", { error: said.error }),
        ),
      );
    }
    return el("span", { class: "websearch-state" }, ...parts);
  }

  /** The key of a search API: saved (its last characters, and Remove) or a field to save one. */
  function keyRow(engine) {
    if (engine.key_name === null) {
      return null;
    }
    if (engine.key_source !== null) {
      const where = engine.key_saved ? t("views.websearch.savedInDot") : engine.key_source === "environment" ? t("views.websearch.fromTheEnvironment") : t("views.websearch.fromTheKeychain");
      return el(
        "div",
        { class: "websearch-key" },
        el("span", { class: "small muted" }, `${engine.key_name}: ${where}${engine.key_tail === "" ? "" : ` · ····${engine.key_tail}`}`),
        engine.key_saved
          ? el(
              "button",
              {
                class: "btn sm",
                type: "button",
                onclick: () =>
                  attempt(async () => {
                    view = await api.del(`/web/search/keys/${engine.engine}`);
                    paint();
                  }),
              },
              t("views.websearch.remove"),
            )
          : null,
      );
    }
    const input = el("input", {
      class: "input",
      type: "password",
      autocomplete: "off",
      spellcheck: "false",
      placeholder: engine.key_name,
      "aria-label": t("views.keys.keyOfLabel", { label: engine.label }),
    });
    const save = el(
      "button",
      {
        class: "btn sm",
        type: "button",
        onclick: () =>
          attempt(async () => {
            const value = input.value.trim();
            if (value === "") {
              return;
            }
            view = await api.post(`/web/search/keys/${engine.engine}`, { value });
            input.value = "";
            toast(t("views.websearch.labelSKeyIsSaved", { label: engine.label }));
            paint();
          }),
      },
      t("views.websearch.save"),
    );
    return el("div", { class: "websearch-key" }, input, save);
  }

  function engineRow(engine, index, order) {
    const on = engine.on;
    const controls = [];
    if (on) {
      controls.push(
        el(
          "button",
          { class: "icon-btn", type: "button", title: t("views.websearch.askItEarlier"), "aria-label": t("views.websearch.moveLabelUp", { label: engine.label }), disabled: index === 0, onclick: () => attempt(() => setOrder(moved(order, index, -1))) },
          icon("arrowUp"),
        ),
        el(
          "button",
          {
            class: "icon-btn",
            type: "button",
            title: t("views.websearch.askItLater"),
            "aria-label": t("views.websearch.moveLabelDown", { label: engine.label }),
            disabled: index === order.length - 1,
            onclick: () => attempt(() => setOrder(moved(order, index, 1))),
          },
          icon("arrowDown"),
        ),
      );
    }
    if (engine.engine !== "browser") {
      controls.push(
        el(
          "button",
          {
            class: "btn sm",
            type: "button",
            title: t("views.websearch.askItOneSearchNow"),
            onclick: () =>
              attempt(async () => {
                tried.set(engine.engine, await api.post(`/web/search/try/${engine.engine}`, {}));
                paint();
              }),
          },
          t("views.websearch.try"),
        ),
      );
    }
    controls.push(
      on
        ? el(
            "button",
            {
              class: "btn sm",
              type: "button",
              disabled: order.length === 1,
              title: order.length === 1 ? t("views.websearch.aSearchNeedsOneEngine") : t("views.websearch.askItNoMore"),
              onclick: () => attempt(() => setOrder(order.filter((name) => name !== engine.engine))),
            },
            t("views.websearch.turnOff"),
          )
        : el("button", { class: "btn sm", type: "button", onclick: () => attempt(() => setOrder([...order, engine.engine])) }, t("views.websearch.add")),
    );
    const title = on ? `${index + 1}. ${engine.label}` : engine.label;
    const row = setRow({
      title,
      lead: el("span", null, engine.about, el("br"), state(engine)),
      control: el("div", { class: "websearch-controls" }, ...controls),
      body: keyRow(engine),
    });
    row.dataset.engine = engine.engine;
    return row;
  }

  function paint() {
    if (disposed || view === null) {
      return;
    }
    const order = orderOf(view);
    const on = view.engines.filter((engine) => engine.on);
    const off = view.engines.filter((engine) => !engine.on);
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections websearch", role: "region", "aria-label": t("views.websearch.webSearch") },
        setSection({
          title: t("views.websearch.askedInTurn"),
          lead: view.saved
            ? t("views.websearch.yourOrderTheFirstThat")
            : t("views.websearch.dotSOrderUntilYou"),
          body: setCard(...on.map((engine, index) => engineRow(engine, index, order))),
        }),
        off.length === 0 ? null : setSection({ title: t("views.websearch.notAsked"), lead: t("views.websearch.addOneToAskIt"), body: setCard(...off.map((engine) => engineRow(engine, -1, order))) }),
      ),
    );
  }

  async function load() {
    try {
      view = await api.get("/web/search");
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    paint();
  }

  ctx.head.setLead(t("views.websearch.whereDotSearchesTheWeb"));
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

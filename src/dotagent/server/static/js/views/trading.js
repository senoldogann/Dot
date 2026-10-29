// Settings › Trading, a trading desk (item 777): the account's worth and its day against the limits, the open positions
// with their stops and targets, what watches the market between runs, the trend engine (item 818), the holdings and the
// latest orders; under them the settings: the mode (off, paper or live), the limits of `[trading]` and the key of the
// owner's Coinbase account.
// Nothing that widens what runs may do with money is asked here of a phone: the daemon refuses it with its reason, which
// the page shows where the change was made. The key is typed here, kept in the Keychain by the daemon and never shown
// again but the end of its name (`GET|PATCH /api/trading`, `PUT|DELETE /api/trading/key`, `POST /api/trading/paper/reset`).

import { api } from "../api.js";
import { dateTime, el } from "../dom.js";
import { explain } from "../errors.js";
import { icon } from "../icons.js";
import { choiceRowSetting, emptyPanel, errorPanel, field, loadingPanel, setCard, setRow, setSection } from "../panels.js";
import {
  LIMITS,
  MODE_LINES,
  amountOf,
  changeTone,
  draftChanges,
  meterShare,
  money,
  needsRestart,
  noAccountWords,
  parseProduct,
  positionFacts,
  price,
  productLabel,
  shareOf,
  showsKey,
  signedMoney,
  signedShare,
  statusWords,
  tradedWords,
  watchLines,
} from "../tradingwords.js";
import { attempt, confirmDialog, toast } from "../ui.js";

const MODES = [
  { id: "off", label: "Off", hint: MODE_LINES.off },
  { id: "paper", label: "Paper", hint: MODE_LINES.paper },
  { id: "live", label: "Live", hint: MODE_LINES.live },
];
const MODE_NAME = { off: "Off", paper: "Paper", live: "Live" };

/** A line that says what went wrong where a change was made: empty until `say` gives it a reason. */
function problemLine() {
  const node = el("p", { class: "trading-problem", role: "alert", hidden: true });
  return {
    node,
    say(error) {
      node.textContent = error === null ? "" : explain(error).text;
      node.hidden = error === null;
    },
  };
}

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  // The fields of the limits as the person typed them, and the products as a list: the draft a save compares with the settings.
  let draft = null;
  // The schedules a move of the market may start (item 813), as `/api/schedules` lists them; null when they could not
  // be read, and the picker then offers only what is chosen now and none.
  let schedules = null;
  const holder = el("div", { class: "stack" }, loadingPanel());
  // The key's form is made once, so that what is typed in it stays through the repaints a mode change brings.
  const keyForm = buildKeyForm();

  /** Take what the daemon answered as the page's state and draw every part of it again. */
  function apply(next) {
    view = next;
    draft = {
      products: [...view.settings.products],
      paper_start: String(view.settings.paper_start),
      ...Object.fromEntries(LIMITS.map((limit) => [limit.id, String(view.settings[limit.id])])),
    };
    paint();
  }

  /** Run a change of the settings; `spot` says why the daemon refused it, and the page is drawn from its answer. */
  async function change(spot, action) {
    try {
      apply(await action());
      spot.say(null);
      return true;
    } catch (error) {
      console.error("trading change failed", error);
      spot.say(error);
      return false;
    }
  }

  function modeSection() {
    const spot = problemLine();
    const { settings } = view;
    const mode = choiceRowSetting({
      label: "Trading",
      hint: "",
      options: MODES,
      selected: settings.mode,
      onSelect: async (next) => {
        if (next === settings.mode) {
          return;
        }
        if (next === "live") {
          const sure = await confirmDialog({
            title: "Trade with real money?",
            message: `Live trading uses your Coinbase account. A run in yolo mode places orders without asking, within the limits: up to ${money(settings.max_order, settings.quote)} an order and ${money(settings.max_day, settings.quote)} a day.`,
            confirmLabel: "Switch to Live",
            danger: true,
            focusCancel: true,
          });
          if (!sure) {
            mode.choice.set(settings.mode);
            return;
          }
        }
        if (!(await change(spot, () => api.patch("/trading", { mode: next })))) {
          mode.choice.set(settings.mode);
        }
      },
    });
    const meaning = el(
      "div",
      { class: "trading-modes" },
      MODES.map((option) => el("p", { class: "hint", dataset: { mode: option.id } }, el("strong", null, `${option.label}: `), option.hint)),
    );
    const restart = needsRestart(view)
      ? el(
          "div",
          { class: "trading-notice", role: "status" },
          icon("alert"),
          el("span", { class: "grow" }, "Runs get the trading tools after Dot restarts."),
          el("button", { class: "btn sm", type: "button", onclick: () => attempt(restartDot) }, icon("power"), "Restart Dot"),
        )
      : null;
    return setSection({
      title: "Mode",
      lead: "What Dot may do with money. The limits below hold in every mode.",
      body: setCard(mode.element, el("div", { class: "set-row" }, meaning, restart, spot.node)),
    });
  }

  async function restartDot() {
    const sure = await confirmDialog({
      title: "Restart Dot?",
      message: "Dot closes and starts again by itself; this page works again in a moment.",
      confirmLabel: "Restart Dot",
    });
    if (sure) {
      await api.post("/system/restart", null);
      toast("ok", "Dot is closing and will start again by itself; this page works again in a moment.");
    }
  }

  function limitsSection() {
    const { settings } = view;
    const spot = problemLine();
    const paper = settings.mode === "paper";
    const save = el("button", { class: "btn primary", type: "button", disabled: true }, "Save limits");
    const inputs = new Map();

    function refresh() {
      const { changes, invalid } = draftChanges(settings, draft, paper);
      for (const [id, input] of inputs) {
        input.setAttribute("aria-invalid", String(invalid.includes(id)));
      }
      save.disabled = invalid.length > 0 || Object.keys(changes).length === 0;
    }

    function amountRow(id, label, hint) {
      const input = el("input", {
        class: "input trading-amount",
        type: "text",
        inputmode: "decimal",
        autocomplete: "off",
        spellcheck: "false",
        value: draft[id],
        "aria-label": `${label} (${settings.quote})`,
        dataset: { limit: id },
      });
      input.addEventListener("input", () => {
        draft[id] = input.value;
        refresh();
      });
      inputs.set(id, input);
      return setRow({ title: label, lead: hint, control: el("div", { class: "trading-unit" }, input, el("span", { class: "muted" }, settings.quote)) });
    }

    const rows = LIMITS.map((limit) => amountRow(limit.id, limit.label, limit.hint));
    if (paper) {
      rows.push(amountRow("paper_start", "Paper account starts with", "What the paper account holds when it starts, and when you start it again."));
    }
    rows.push(productsRow(settings, refresh));
    rows.push(el("div", { class: "set-row trading-save" }, save, spot.node));
    save.addEventListener("click", () => {
      const { changes } = draftChanges(settings, draft, paper);
      attempt(() => change(spot, () => api.patch("/trading", changes)));
    });
    return setSection({
      title: "Limits",
      lead: `In ${settings.quote}. No run can change them, and an order past one is refused before it reaches any market.`,
      body: setCard(...rows),
    });
  }

  /** The products Dot may trade: each a chip with its remove button, and a field that adds one. */
  function productsRow(settings, refresh) {
    const chips = el("div", { class: "trading-chips" });
    const note = el("p", { class: "hint", role: "alert", hidden: true });
    const input = el("input", {
      class: "input",
      type: "text",
      autocomplete: "off",
      autocapitalize: "characters",
      spellcheck: "false",
      placeholder: `BTC-${settings.quote}`,
      "aria-label": "Product to allow",
    });

    function paintChips() {
      chips.replaceChildren(
        ...draft.products.map((id) => {
          const label = productLabel(id, settings.min_volume, settings.quote);
          return el(
            "span",
            { class: "chip trading-chip", dataset: { product: id } },
            label,
            el(
              "button",
              {
                class: "icon-btn",
                type: "button",
                title: draft.products.length === 1 ? "Dot needs one product at least" : `Do not trade ${label}`,
                "aria-label": `Remove ${label}`,
                disabled: draft.products.length === 1,
                onclick: () => {
                  draft.products = draft.products.filter((other) => other !== id);
                  paintChips();
                  refresh();
                },
              },
              icon("x"),
            ),
          );
        }),
      );
    }

    function add() {
      const id = parseProduct(input.value, settings.quote);
      if (input.value.trim() === "") {
        return;
      }
      if (id === null) {
        note.textContent = `A product is priced in ${settings.quote}, as BTC-${settings.quote}, or * for the whole market.`;
        note.hidden = false;
        return;
      }
      note.hidden = true;
      if (!draft.products.includes(id)) {
        draft.products = [...draft.products, id];
      }
      input.value = "";
      paintChips();
      refresh();
    }

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        add();
      }
    });
    paintChips();
    return setRow({
      title: "Products",
      lead: `The only products a run may trade. * allows every product in ${settings.quote} that trades enough a day.`,
      body: el(
        "div",
        { class: "trading-products" },
        chips,
        el("div", { class: "trading-add" }, input, el("button", { class: "btn sm", type: "button", onclick: add }, icon("plus"), "Allow")),
        note,
      ),
    });
  }

  /** The key's form: its name and its private key, which is cleared once Coinbase has taken it. */
  function buildKeyForm() {
    const name = el("input", {
      class: "input mono",
      type: "text",
      autocomplete: "off",
      spellcheck: "false",
      placeholder: "organizations/…/apiKeys/…",
      "aria-label": "Key name",
    });
    const secret = el("textarea", {
      class: "textarea mono trading-secret",
      rows: 4,
      autocomplete: "off",
      spellcheck: "false",
      placeholder: "-----BEGIN EC PRIVATE KEY-----",
      "aria-label": "Private key",
    });
    const spot = problemLine();
    const save = el("button", { class: "btn primary", type: "button" }, "Save key");
    save.addEventListener("click", () =>
      attempt(async () => {
        const ok = await change(spot, () => api.put("/trading/key", { name: name.value.trim(), private_key: secret.value.trim() }));
        if (ok) {
          name.value = "";
          secret.value = "";
          toast("ok", "The key is saved in the Keychain.");
        }
      }),
    );
    const element = el(
      "div",
      { class: "trading-keyform" },
      field("Key name", name, ""),
      field("Private key", secret, "It goes to the Keychain and is not shown again."),
      el("div", { class: "cluster" }, save),
      spot.node,
    );
    return { element, spot };
  }

  function keySection() {
    const { key } = view;
    const spot = problemLine();
    const remove = el("button", { class: "btn sm", type: "button", "aria-haspopup": "dialog" }, icon("trash"), "Remove key");
    remove.addEventListener("click", () =>
      attempt(async () => {
        const sure = await confirmDialog({
          title: "Remove the Coinbase key?",
          message: "Live trading places nothing until a key is saved again.",
          confirmLabel: "Remove key",
          danger: true,
        });
        if (sure) {
          await change(spot, () => api.del("/trading/key"));
        }
      }),
    );
    const kept = key.kept;
    const howTo = el(
      "div",
      { class: "set-row" },
      el(
        "p",
        { class: "hint" },
        "Make the key in the Coinbase Developer Platform: choose the ECDSA signature algorithm and give it the permissions View and Trade only, never Transfer. Coinbase is asked what the key may do, and a key that may transfer money is not kept.",
      ),
      keyForm.element,
    );
    return setSection({
      title: "Coinbase key",
      lead: "What live trading signs its orders with. It stays in the login Keychain of this Mac.",
      body: setCard(
        setRow({
          title: kept ? "A key is kept" : "No key kept",
          lead: kept ? `The key ending ${key.name_end}` : "Live trading places nothing until one is saved.",
          control: kept ? remove : null,
          body: spot.node,
        }),
        howTo,
      ),
    });
  }

  /** The button that starts the paper account again, after a question; `spot` says why it could not. */
  function startAgainButton(spot) {
    const { settings } = view;
    const again = el("button", { class: "btn sm", type: "button", "aria-haspopup": "dialog" }, icon("refresh"), "Start again");
    again.addEventListener("click", () =>
      attempt(async () => {
        const sure = await confirmDialog({
          title: "Start the paper account again?",
          message: `It goes back to ${money(settings.paper_start, settings.quote)} and holds nothing else.`,
          confirmLabel: "Start again",
          danger: true,
        });
        if (sure) {
          await change(spot, () => api.post("/trading/paper/reset", null));
        }
      }),
    );
    return again;
  }

  /** A figure of the desk: its label, its value and, beside it, its share; `figure` names it for the tests. */
  function deskStat(label, value, share, tone, figure) {
    return el(
      "div",
      { class: "desk-stat" },
      el("span", { class: "desk-label" }, label),
      el("span", { class: `desk-figure trading-change ${tone}`, dataset: figure ? { figure } : undefined }, value),
      share === "" ? null : el("span", { class: `desk-share trading-change ${tone}` }, share),
    );
  }

  /** A meter of what a limit leaves: its label, a bar filled as far as `used` goes against `limit`, and its words. */
  function deskMeter(label, used, limit, words, figure) {
    const share = meterShare(used, limit);
    const tone = share >= 100 ? "bad" : share > 60 ? "warn" : "ok";
    const fill = el("span", { class: `desk-fill ${tone}` });
    fill.style.setProperty("--fill", `${share}%`);
    return el(
      "div",
      { class: "desk-meter" },
      el("span", { class: "desk-label" }, label),
      el(
        "span",
        { class: "desk-track", role: "meter", "aria-label": label, "aria-valuemin": "0", "aria-valuemax": String(limit), "aria-valuenow": String(Math.max(0, used)) },
        fill,
      ),
      el("span", { class: "desk-meter-words", dataset: figure ? { figure } : undefined }, words),
    );
  }

  /** The account at a glance: what it is worth, its day and since its start, what is in positions, and the day's limits. */
  function deskSection() {
    const { settings, portfolio, problem } = view;
    const spot = problemLine();
    const parts = [];
    if (problem !== "") {
      parts.push(el("div", { class: "trading-notice", role: "status" }, icon("alert"), el("span", { class: "grow" }, problem)));
    }
    if (portfolio === null) {
      parts.push(el("div", { class: "set-card" }, emptyPanel("trendingUp", "No account to show", noAccountWords(view))));
    } else {
      const quote = portfolio.quote;
      const day = portfolio.equity - portfolio.day_start_equity;
      const cash = portfolio.holdings.find((holding) => holding.currency === quote)?.amount ?? 0;
      const invested = Math.max(0, portfolio.equity - cash);
      const lost = Math.max(0, -day);
      // Counted from what the account started with at its start (item 785), not from `paper_start` changed since.
      const sinceStart = settings.mode === "paper" ? portfolio.equity - (view.paper_started_with ?? settings.paper_start) : null;
      parts.push(
        el(
          "div",
          { class: "set-card desk" },
          el(
            "div",
            { class: "desk-top" },
            el(
              "div",
              { class: "desk-hero" },
              el("span", { class: "desk-label" }, "Account value"),
              el("span", { class: "desk-value", dataset: { figure: "equity" } }, money(portfolio.equity, quote)),
              el("span", { class: "desk-sub" }, settings.mode === "paper" ? "Paper account, at Coinbase's prices" : "Your Coinbase account"),
            ),
            el(
              "div",
              { class: "desk-stats" },
              deskStat("Today", signedMoney(day, quote), signedShare(day, portfolio.day_start_equity), changeTone(day), "day"),
              sinceStart === null ? null : deskStat("Since the start", signedMoney(sinceStart, quote), signedShare(sinceStart, view.paper_started_with ?? settings.paper_start), changeTone(sinceStart), "start"),
              deskStat("In positions", money(invested, quote), shareOf(invested, portfolio.equity), "plain", "invested"),
            ),
          ),
          el(
            "div",
            { class: "desk-meters" },
            deskMeter("Loss today", lost, settings.max_day_loss, `${money(lost, quote)} of ${money(settings.max_day_loss, quote)}`, "lost"),
            deskMeter("Traded today", portfolio.day_traded, settings.max_day, tradedWords(portfolio.day_traded, settings.max_day, quote), "traded"),
          ),
        ),
      );
    }
    parts.push(spot.node);
    return setSection({
      title: "Account",
      lead: settings.mode === "paper" ? "Dot's paper account: Coinbase's real prices, with money that is not there." : "Your Coinbase account, with real money.",
      actions: settings.mode === "paper" ? startAgainButton(spot) : null,
      body: el("div", { class: "stack" }, parts),
    });
  }

  /** A cell of a table that turns into a labelled line on a phone: `label` is what the line says first. */
  function cell(label, content, numeric = false) {
    return el("td", { class: numeric ? "num" : null, "data-label": label }, content);
  }

  /** Where the price and the entry stand between the stop and the target, with how far each end is. */
  function rangeBar(position, facts) {
    const track = el("span", { class: "trading-range-track" });
    for (const [at, kind, words] of [
      [facts.entryAt, "entry", position.entry === null ? "" : `Entry ${price(position.entry)}`],
      [facts.priceAt, `now ${facts.tone}`, position.price === null ? "" : `Now ${price(position.price)}`],
    ]) {
      if (at !== null) {
        const mark = el("span", { class: `trading-range-mark ${kind}`, title: words });
        mark.style.setProperty("--at", `${at}%`);
        track.append(mark);
      }
    }
    const target = position.target === null ? "no target" : `target ${price(position.target)}${facts.toTarget === "" ? "" : ` (${facts.toTarget})`}`;
    return el(
      "div",
      { class: "trading-range", role: "img", "aria-label": `Stop ${price(position.stop)}${facts.toStop === "" ? "" : ` (${facts.toStop})`}, ${target}` },
      el("span", { class: "trading-range-end" }, price(position.stop), facts.toStop === "" ? null : el("small", { class: "down" }, facts.toStop)),
      track,
      el("span", { class: "trading-range-end" }, position.target === null ? "—" : price(position.target), facts.toTarget === "" ? null : el("small", { class: "up" }, facts.toTarget)),
    );
  }

  /** The open positions of the paper account: each buy's exit, with its gain and where it stands on its stop and target. */
  function positionsSection() {
    const { settings, positions, portfolio } = view;
    if (portfolio === null || settings.mode !== "paper") {
      return null;
    }
    const quote = portfolio.quote;
    const body =
      positions.length === 0
        ? el("div", { class: "set-card" }, el("div", { class: "empty" }, "No open position. A buy's stop and target are watched from the moment it fills."))
        : el(
            "div",
            { class: "set-card trading-table-card" },
            el(
              "table",
              { class: "trading-table" },
              el(
                "thead",
                null,
                el(
                  "tr",
                  null,
                  ["Position", "Size", "Entry", "Now", "Value", "Gain", "Stop · target"].map((title, index) =>
                    el("th", { class: index > 0 && index < 6 ? "num" : null, scope: "col" }, title),
                  ),
                ),
              ),
              el(
                "tbody",
                null,
                positions.map((position) => {
                  const facts = positionFacts(position, quote);
                  const base = position.product.split("-")[0];
                  return el(
                    "tr",
                    { dataset: { position: position.exit_id } },
                    cell("Position", el("span", { class: "trading-asset" }, el("strong", null, base), el("span", { class: "muted" }, position.product))),
                    cell("Size", amountOf(position.size, base), true),
                    cell("Entry", position.entry === null ? "—" : price(position.entry), true),
                    cell("Now", position.price === null ? "—" : price(position.price), true),
                    cell("Value", facts.value === "" ? "—" : facts.value, true),
                    cell(
                      "Gain",
                      el("span", { class: `trading-change ${facts.tone}` }, facts.gain === "" ? "—" : facts.gain, facts.gainShare === "" ? null : el("span", { class: "trading-share" }, facts.gainShare)),
                      true,
                    ),
                    cell("Stop · target", rangeBar(position, facts)),
                  );
                }),
              ),
            ),
          );
    return setSection({ title: "Open positions", lead: "Each buy with the stop and the target Dot sells it at.", body });
  }

  /** What watches the market between runs: the stops, the market Dot reads, and the schedule a move starts. */
  function watchSection() {
    const lines = watchLines(view, dateTime);
    if (lines.length === 0) {
      return null;
    }
    return setSection({
      title: "Watching",
      lead: "What Dot looks at between runs, day and night, with no model.",
      body: [
        el(
          "div",
          { class: "set-card trading-watch" },
          lines.map((line) => el("div", { class: `trading-watch-line ${line.tone}`, dataset: { watch: line.id } }, el("span", { class: "trading-dot", "aria-hidden": "true" }), el("span", null, line.text))),
        ),
        setCard(wakeRow()),
      ],
    });
  }

  /** The schedule a move of the market starts (item 813), chosen among the schedules or none; one that is gone shows as
   * such until another is chosen. */
  function wakeRow() {
    const spot = problemLine();
    const chosen = view.watch?.wake_schedule ?? view.settings.wake_schedule ?? "";
    const listed = schedules ?? [];
    const options = [el("option", { value: "" }, "None")];
    for (const schedule of listed) {
      const { id, name, enabled } = schedule.record;
      options.push(el("option", { value: id }, enabled ? name : `${name} (switched off)`));
    }
    if (chosen !== "" && !listed.some((schedule) => schedule.record.id === chosen)) {
      options.push(el("option", { value: chosen }, schedules === null ? chosen : "A schedule that is gone"));
    }
    const picker = el("select", { class: "select", "aria-label": "Wake on a move" }, options);
    picker.value = chosen;
    picker.addEventListener("change", () => {
      attempt(async () => {
        if (!(await change(spot, () => api.patch("/trading", { wake_schedule: picker.value })))) {
          // Refused (a schedule deleted meanwhile): the picker shows what is kept, beside the reason.
          picker.value = chosen;
        }
      });
    });
    // A schedule made since the page was read is offered once the picker is opened again.
    picker.addEventListener("focus", () => {
      api
        .get("/schedules")
        .then((listed) => {
          if (disposed || document.activeElement !== picker) {
            return;
          }
          const known = new Set((schedules ?? []).map((schedule) => schedule.record.id));
          schedules = listed;
          for (const schedule of listed) {
            if (!known.has(schedule.record.id)) {
              const { id, name, enabled } = schedule.record;
              picker.append(el("option", { value: id }, enabled ? name : `${name} (switched off)`));
            }
          }
        })
        .catch((error) => console.warn("the schedules could not be read again", error));
    });
    return setRow({
      title: "Wake on a move",
      lead: "The schedule a move of the market starts between its own times, as many times a day as wake_per_day allows.",
      control: picker,
      body: spot.node,
    });
  }

  /** The trend engine (item 818): its switch, the shares its rule wants beside those held at its last look, what it
   * made against holding, and what held an order back or why it is paused. It trades on paper only, with no model. */
  function engineSection() {
    const spot = problemLine();
    const { settings, engine } = view;
    const state = engine?.state ?? null;
    const paused = engine?.paused ?? null;
    const products = settings.engine_coins.map((coin) => `${coin}-${settings.quote}`);
    const toggle = choiceRowSetting({
      label: "Trend engine",
      hint: `Trades ${products.join(", ")} by a rule tried over years of daily closes, once an hour, on paper, within the limits. No model; a run may pause it.`,
      options: [
        { id: "off", label: "Off" },
        { id: "trend", label: "On" },
      ],
      selected: settings.engine,
      onSelect: async (next) => {
        if (next !== settings.engine && !(await change(spot, () => api.patch("/trading", { engine: next })))) {
          toggle.choice.set(settings.engine);
        }
      },
    });
    // Why the daemon refused a switch, under the switch: no empty row while there is none.
    toggle.element.append(spot.node);
    const lines = [];
    if (paused !== null) {
      lines.push(el("div", { class: "trading-watch-line warn", dataset: { engine: "paused" } }, el("span", { class: "trading-dot", "aria-hidden": "true" }), el("span", null, `Paused by ${paused.by} since ${dateTime(paused.since)}: ${paused.reason}`)));
    }
    if (state !== null) {
      lines.push(el("div", { class: "trading-watch-line", dataset: { engine: "look" } }, el("span", { class: "trading-dot", "aria-hidden": "true" }), el("span", null, `Last look ${dateTime(state.looked_at)}: ${state.placed.length === 0 ? "no order" : `${state.placed.length} ${state.placed.length === 1 ? "order" : "orders"}`}.`)));
      // What the account made since the engine started, beside holding its first coin instead (item 819).
      const result = state.result ?? null;
      if (result !== null) {
        lines.push(el("div", { class: "trading-watch-line", dataset: { engine: "result" } }, el("span", { class: "trading-dot", "aria-hidden": "true" }), el("span", null, `Since ${dateTime(result.started_at)}: the account ${signedShare(result.account, 1)}, holding ${result.product} instead ${signedShare(result.held, 1)}.`)));
      }
      for (const note of state.notes) {
        lines.push(el("p", { class: "hint", dataset: { engine: "note" } }, note));
      }
    } else if (settings.engine === "trend") {
      lines.push(el("p", { class: "hint" }, "It looks within the hour."));
    }
    const shares =
      state === null || state.shares.length === 0
        ? null
        : el(
            "table",
            { class: "trading-table" },
            el("thead", null, el("tr", null, ["Product", "Held", "The rule wants"].map((title, index) => el("th", { class: index > 0 ? "num" : null, scope: "col" }, title)))),
            el(
              "tbody",
              null,
              state.shares.map((share) =>
                el(
                  "tr",
                  { dataset: { share: share.product } },
                  cell("Product", el("strong", null, share.product)),
                  cell("Held", `${Math.round(share.weight * 100)}%`, true),
                  cell("The rule wants", `${Math.round(share.target * 100)}%`, true),
                ),
              ),
            ),
          );
    return setSection({
      title: "Trend engine",
      lead: "A rule instead of a model for the coins it trades: it holds a coin while its daily closes rise, sized to how wild it is, and cash when they fall.",
      body: [setCard(toggle.element), lines.length === 0 && shares === null ? null : el("div", { class: "set-card trading-watch trading-table-card" }, lines, shares)],
    });
  }

  /** What the account holds, each currency at the bid now and its share of the account. */
  function holdingsSection() {
    const { portfolio } = view;
    if (portfolio === null) {
      return null;
    }
    const quote = portfolio.quote;
    const held = [...portfolio.holdings].sort((a, b) => b.value - a.value);
    const unread = [...portfolio.unpriced, ...portfolio.unpriceable];
    return setSection({
      title: "Holdings",
      body:
        held.length === 0
          ? el("div", { class: "set-card" }, el("div", { class: "empty" }, "The account holds nothing."))
          : el(
              "div",
              { class: "set-card trading-table-card" },
              el(
                "table",
                { class: "trading-table" },
                el("thead", null, el("tr", null, ["Currency", "Amount", "Price", "Value", "Share"].map((title, index) => el("th", { class: index > 0 ? "num" : null, scope: "col" }, title)))),
                el(
                  "tbody",
                  null,
                  held.map((holding) =>
                    el(
                      "tr",
                      { dataset: { holding: holding.currency } },
                      cell("Currency", el("strong", null, holding.currency)),
                      cell("Amount", holding.currency === quote ? money(holding.amount, quote) : amountOf(holding.amount, holding.currency), true),
                      cell("Price", holding.currency === quote || !(holding.amount > 0) ? "—" : price(holding.value / holding.amount), true),
                      cell("Value", money(holding.value, quote), true),
                      cell("Share", shareOf(holding.value, portfolio.equity), true),
                    ),
                  ),
                ),
              ),
              unread.length === 0 ? null : el("p", { class: "hint trading-unread" }, `Not priced now, counted as nothing: ${unread.join(", ")}.`),
            ),
    });
  }

  /** The latest orders as a table, each with the reason the run gave under it. */
  function ordersSection() {
    const { settings, orders } = view;
    const newest = [...orders].sort((a, b) => b.ts - a.ts);
    const quote = settings.quote;
    return setSection({
      title: "Orders",
      lead: "The latest, newest first, each with the reason its run gave.",
      body:
        newest.length === 0
          ? el("div", { class: "set-card" }, emptyPanel("list", "No orders yet", "Orders Dot places show here."))
          : el(
              "div",
              { class: "set-card trading-table-card" },
              el(
                "table",
                { class: "trading-table trading-orders" },
                el("thead", null, el("tr", null, ["When", "Order", "Size", "Price", "Worth", "Fee", "Status"].map((title, index) => el("th", { class: index > 1 && index < 6 ? "num" : null, scope: "col" }, title)))),
                newest.map((order) => {
                  const base = order.product.split("-")[0];
                  const status = statusWords(order.status);
                  const filled = order.size > 0 && order.price > 0;
                  return el(
                    "tbody",
                    { dataset: { order: order.id } },
                    el(
                      "tr",
                      null,
                      cell("When", el("span", { class: "nowrap" }, dateTime(order.ts))),
                      cell("Order", el("span", { class: "trading-asset" }, el("span", { class: `trading-side ${order.side}` }, order.side === "buy" ? "Buy" : "Sell"), " ", el("strong", null, order.product))),
                      cell("Size", filled ? amountOf(order.size, base) : "—", true),
                      cell("Price", filled ? price(order.price) : "—", true),
                      cell("Worth", order.status === "failed" ? "—" : money(order.notional, quote), true),
                      cell("Fee", order.fee > 0 ? money(order.fee, quote) : "—", true),
                      cell("Status", el("span", { class: `badge ${status.tone}` }, status.label)),
                    ),
                    order.note === "" ? null : el("tr", { class: "trading-reason" }, el("td", { colspan: "7" }, order.note)),
                  );
                }),
              ),
            ),
    });
  }

  function paint() {
    if (disposed || view === null) {
      return;
    }
    ctx.head.setMeta(el("span", { class: `badge ${view.settings.mode === "off" ? "b-muted" : view.settings.mode === "live" ? "b-warn" : "b-info"}` }, MODE_NAME[view.settings.mode]));
    holder.replaceChildren(
      el(
        "div",
        { class: "set-sections trading", role: "region", "aria-label": "Trading" },
        view.settings.mode === "off" ? null : deskSection(),
        view.settings.mode === "off" ? null : positionsSection(),
        view.settings.mode === "off" ? null : watchSection(),
        view.settings.mode === "off" ? null : engineSection(),
        view.settings.mode === "off" ? null : holdingsSection(),
        view.settings.mode === "off" ? null : ordersSection(),
        view.settings.mode === "off" ? null : el("h2", { class: "trading-divider" }, "Settings"),
        modeSection(),
        limitsSection(),
        showsKey(view) ? keySection() : null,
      ),
    );
  }

  async function load() {
    try {
      const [trading, listed] = await Promise.all([
        api.get("/trading"),
        api.get("/schedules").catch((error) => {
          console.warn("the schedules could not be read", error);
          return null;
        }),
      ]);
      schedules = listed;
      apply(trading);
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
    }
  }

  ctx.head.setLead("What Dot's trading holds and does, and with what limits.");
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

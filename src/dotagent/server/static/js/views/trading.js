// Settings › Trading: the mode (off, paper or live) and the limits of `[trading]`, the key of the owner's Coinbase
// account, what the account holds and the latest orders. Nothing that widens what runs may do with money is asked
// here of a phone: the daemon refuses it with its reason, which the page shows where the change was made. The key is
// typed here, kept in the Keychain by the daemon and never shown again but the end of its name (`GET|PATCH /api/trading`,
// `PUT|DELETE /api/trading/key`, `POST /api/trading/paper/reset`).

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
  money,
  needsRestart,
  noAccountWords,
  orderWords,
  parseProduct,
  showsKey,
  signedMoney,
  statusWords,
  tradedWords,
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
        ...draft.products.map((id) =>
          el(
            "span",
            { class: "chip trading-chip", dataset: { product: id } },
            id,
            el(
              "button",
              {
                class: "icon-btn",
                type: "button",
                title: draft.products.length === 1 ? "Dot needs one product at least" : `Do not trade ${id}`,
                "aria-label": `Remove ${id}`,
                disabled: draft.products.length === 1,
                onclick: () => {
                  draft.products = draft.products.filter((other) => other !== id);
                  paintChips();
                  refresh();
                },
              },
              icon("x"),
            ),
          ),
        ),
      );
    }

    function add() {
      const id = parseProduct(input.value, settings.quote);
      if (input.value.trim() === "") {
        return;
      }
      if (id === null) {
        note.textContent = `A product is priced in ${settings.quote}, as BTC-${settings.quote}.`;
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
      lead: "The only products a run may trade.",
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

  function accountSection() {
    const { settings, portfolio, problem } = view;
    const spot = problemLine();
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
    const parts = [];
    if (problem !== "") {
      parts.push(el("div", { class: "trading-notice", role: "status" }, icon("alert"), el("span", { class: "grow" }, problem)));
    }
    if (portfolio === null) {
      parts.push(el("div", { class: "set-card" }, emptyPanel("trendingUp", "No account to show", noAccountWords(view))));
    } else {
      const quote = portfolio.quote;
      const day = portfolio.equity - portfolio.day_start_equity;
      parts.push(
        setCard(
          setRow({ title: "Worth now", lead: "Everything the account holds, at the prices of this moment.", control: el("span", { class: "trading-figure", dataset: { figure: "equity" } }, money(portfolio.equity, quote)) }),
          setRow({
            title: "Today",
            lead: `Since the day began, at ${money(portfolio.day_start_equity, quote)}.`,
            control: el("span", { class: `trading-figure trading-change ${changeTone(day)}`, dataset: { figure: "day" } }, signedMoney(day, quote)),
          }),
          setRow({
            title: "Traded today",
            lead: "What the day's orders add up to, against what a day may.",
            control: el("span", { class: "trading-figure", dataset: { figure: "traded" } }, tradedWords(portfolio.day_traded, settings.max_day, quote)),
          }),
        ),
        setSection({
          title: "Holdings",
          body:
            portfolio.holdings.length === 0
              ? el("div", { class: "set-card" }, el("div", { class: "empty" }, "The account holds nothing."))
              : setCard(
                  ...portfolio.holdings.map((holding) =>
                    setRow({ title: holding.currency, lead: amountOf(holding.amount, holding.currency), control: el("span", { class: "trading-figure" }, money(holding.value, quote)) }),
                  ),
                ),
        }),
      );
    }
    parts.push(spot.node);
    return setSection({
      title: "Account",
      lead: settings.mode === "paper" ? "Dot's paper account, at Coinbase's prices." : settings.mode === "live" ? "Your Coinbase account." : "",
      actions: settings.mode === "paper" ? again : null,
      body: el("div", { class: "stack" }, parts),
    });
  }

  function ordersSection() {
    const { settings, orders } = view;
    const newest = [...orders].sort((a, b) => b.ts - a.ts);
    return setSection({
      title: "Orders",
      lead: "The latest, newest first.",
      body:
        newest.length === 0
          ? el("div", { class: "set-card" }, emptyPanel("list", "No orders yet", "Orders Dot places show here."))
          : setCard(
              ...newest.map((order) => {
                const words = orderWords(order, settings.quote, dateTime);
                const status = statusWords(order.status);
                const row = setRow({
                  title: el("span", { class: "trading-order-title" }, words.title, el("span", { class: `badge ${status.tone}` }, status.label)),
                  lead: words.facts,
                  control: words.amount === "" ? null : el("span", { class: "trading-figure" }, words.amount),
                  body: words.note === "" ? null : el("p", { class: "hint trading-note" }, words.note),
                });
                row.dataset.order = order.id;
                return row;
              }),
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
        modeSection(),
        limitsSection(),
        showsKey(view) ? keySection() : null,
        view.settings.mode === "off" ? null : accountSection(),
        view.settings.mode === "off" ? null : ordersSection(),
      ),
    );
  }

  async function load() {
    try {
      apply(await api.get("/trading"));
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
    }
  }

  ctx.head.setLead("Whether Dot may trade crypto on Coinbase, with what limits, and what it did.");
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

// The official sign-ins of Google and Microsoft (item 422), at the head of Settings › Connectors: whether the client
// the owner registered is set, the accounts signed in, each with Sign out, and Sign in, which opens the provider's page
// in a new tab and follows the sign-in until the account is there.

import { api } from "./api.js";
import { el } from "./dom.js";
import { explain } from "./errors.js";
import { markContent } from "./marks.js";
import { attempt, detailsFold, toast } from "./ui.js";
import { t, listAnd } from "./i18n.js";

// How often the card reads the accounts again after a sign-in starts, and for how long.
const FOLLOW_MS = 3000;
const FOLLOW_FOR_MS = 10 * 60 * 1000;
// The providers' marks: the files the list of the plugins shows Google and Microsoft with, drawn in the tile's ink.
const MARKS = { google: "google-icon.svg", microsoft: "microsoft-icon.svg" };

/** The card: returns `element`, `read`, which settles once the card has read the accounts the first time or said why
 * it could not, and `dispose()`. */
export function accountsCard() {
  let disposed = false;
  let following = null;
  let until = 0;
  let shown = "";
  const list = el("div", { class: "accounts-list" });
  const element = el(
    "section",
    { class: "card", "aria-label": t("accounts.accounts") },
    el("div", { class: "card-head" }, el("span", { class: "grow" }, t("accounts.accounts")), el("span", { class: "hint" }, t("accounts.gmailGoogleCalendarOutlookAnd"))),
    el("div", { class: "card-pad" }, list),
  );

  function mark(provider) {
    const logo = MARKS[provider.id];
    return el("span", { class: "account-mark", "aria-hidden": "true" }, logo === undefined ? provider.name.slice(0, 1) : markContent({ logo, glyph: null }));
  }

  function row(provider) {
    const signIn = el(
      "button",
      { class: "btn sm primary", type: "button", disabled: !provider.configured },
      el("span", null, provider.accounts.length > 0 ? t("accounts.signInAnother") : t("accounts.signIn")),
    );
    signIn.addEventListener("click", () =>
      attempt(async () => {
        signIn.disabled = true;
        try {
          const started = await api.post(`/accounts/${provider.id}/start`, null);
          window.open(started.link, "_blank", "noopener,noreferrer");
          toast("ok", t("accounts.sayYesOnNameS", { name: provider.name }));
          until = Date.now() + FOLLOW_FOR_MS;
          follow();
        } finally {
          signIn.disabled = !provider.configured;
        }
      }),
    );
    const accounts = provider.accounts.map((account) =>
      el(
        "div",
        { class: "account-row" },
        el("span", { class: "grow" }, account.address),
        el(
          "button",
          {
            class: "btn sm",
            type: "button",
            onclick: () =>
              attempt(async () => {
                const answered = await api.del(`/accounts/${provider.id}/${encodeURIComponent(account.address)}`);
                paint(answered.providers);
                // The sign-out always stands here; when the provider has not ended Dot's access too, the toast says where.
                if (answered.revoked) {
                    toast("ok", t("accounts.addressIsSignedOut", { address: account.address }));
                } else {
                    toast("info", `${account.address}: ${answered.notice}`);
                }
              }),
          },
          t("accounts.signOut"),
        ),
      ),
    );
    const status = !provider.configured
      ? [
          t("accounts.notSetUpYetIt", { name: provider.name }),
          detailsFold(t("accounts.saveNamesInTheKeychain", { names: listAnd(provider.client_names) })),
        ]
      : provider.accounts.length === 0
        ? [t("accounts.notSignedIn")]
        : null;
    return el(
      "div",
      { class: "account-provider" },
      el("div", { class: "row" }, mark(provider), el("span", { class: "grow account-name" }, provider.name), signIn),
      status === null ? null : el("div", { class: "hint" }, ...status),
      ...accounts,
    );
  }

  function paint(providers) {
    const drawn = JSON.stringify(providers);
    if (drawn !== shown) {
      shown = drawn;
      list.replaceChildren(...providers.map(row));
    }
  }

  async function load() {
    let providers;
    try {
      providers = await api.get("/accounts");
    } catch (error) {
      shown = "";
      const { text, details } = explain(error);
      list.replaceChildren(el("div", { class: "hint" }, t("accounts.theAccountsCouldNotBe", { text }), detailsFold(details)));
      throw error;
    }
    if (!disposed) {
      paint(providers);
    }
  }

  function follow() {
    clearTimeout(following);
    if (disposed || Date.now() > until) {
      return;
    }
    following = setTimeout(async () => {
      try {
        await load();
      } catch (error) {
        // The card says what failed in its own place; the following stops with it.
        console.error("the accounts could not be read again", error);
        return;
      }
      follow();
    }, FOLLOW_MS);
  }

  const reread = () => {
    if (!disposed && document.visibilityState === "visible") {
      attempt(load);
    }
  };
  window.addEventListener("focus", reread);
  // What is drawn under the card waits for its first read (item 511): the providers' rows it gets then would push it.
  const read = attempt(load);
  return {
    element,
    read,
    dispose: () => {
      disposed = true;
      clearTimeout(following);
      window.removeEventListener("focus", reread);
    },
  };
}

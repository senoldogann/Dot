// The official sign-ins of Google and Microsoft (item 422), at the head of Customize › Plugins: whether the client the
// owner registered is set, the accounts signed in, each with Sign out, and Sign in, which opens the provider's page in
// a new tab and follows the sign-in until the account is there.

import { api } from "./api.js";
import { el } from "./dom.js";
import { attempt, toast } from "./ui.js";

// How often the card reads the accounts again after a sign-in starts, and for how long.
const FOLLOW_MS = 3000;
const FOLLOW_FOR_MS = 10 * 60 * 1000;
const MARKS = { google: "/icons/plugins/google.svg" };

/** The card: returns `element` and `dispose()`. */
export function accountsCard() {
  let disposed = false;
  let following = null;
  let until = 0;
  let shown = "";
  const list = el("div", { class: "accounts-list" });
  const element = el(
    "section",
    { class: "card", "aria-label": "Accounts" },
    el("div", { class: "card-head" }, el("span", { class: "grow" }, "Accounts"), el("span", { class: "hint" }, "Gmail, Google Calendar, Outlook and Microsoft 365, through their own sign-in")),
    el("div", { class: "card-pad" }, list),
  );

  function mark(provider) {
    const source = MARKS[provider.id];
    return source === undefined
      ? el("span", { class: "account-mark", "aria-hidden": "true" }, provider.name.slice(0, 1))
      : el("img", { class: "account-mark", src: source, alt: "", width: "24", height: "24" });
  }

  function row(provider) {
    const signIn = el(
      "button",
      { class: "btn sm primary", type: "button", disabled: !provider.configured },
      el("span", null, provider.accounts.length > 0 ? "Sign in another" : "Sign in"),
    );
    signIn.addEventListener("click", () =>
      attempt(async () => {
        signIn.disabled = true;
        try {
          const started = await api.post(`/accounts/${provider.id}/start`, null);
          window.open(started.link, "_blank", "noopener,noreferrer");
          toast("ok", `Say yes on ${provider.name}'s page; this card follows.`);
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
                paint(await api.del(`/accounts/${provider.id}/${encodeURIComponent(account.address)}`));
                toast("ok", `${account.address} is signed out.`);
              }),
          },
          "Sign out",
        ),
      ),
    );
    const status = !provider.configured
      ? `Needs the client you registered with ${provider.name}: save ${provider.client_names.join(" and ")} in the Keychain (docs/extending.md says how).`
      : provider.accounts.length === 0
        ? "Not signed in."
        : null;
    return el(
      "div",
      { class: "account-provider" },
      el("div", { class: "row" }, mark(provider), el("span", { class: "grow account-name" }, provider.name), signIn),
      status === null ? null : el("p", { class: "hint" }, status),
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
      list.replaceChildren(el("p", { class: "hint" }, `The accounts could not be read: ${error.message}`));
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
  attempt(load);
  return {
    element,
    dispose: () => {
      disposed = true;
      clearTimeout(following);
      window.removeEventListener("focus", reread);
    },
  };
}

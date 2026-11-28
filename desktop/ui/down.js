// What the shell tells this page in its fragment (`down_url` in src-tauri/src/lib.rs, item 191): `title` stands for
// "Dot is not running" and the advice to start it, when the shell could not read one of the daemon's files and the
// daemon may well run, or when something else answers at the daemon's address (item 558); `detail` says which file and
// what is wrong with it, or what answers there; `lang` is the language of the page's own words when it is not English
// (`[desktop_app] language`, the web UI's). The shell sends the page again when that changes.

/** The page's own words in Turkish, by the `data-word` of the element that holds them; English is in the HTML. */
const TURKISH = {
  notRunning: "Dot çalışmıyor",
  off: "İşi yapan arka plan hizmeti kapalı. Bir kez başlatın, her girişte ve bir çökmeden sonra açık kalır:",
  clone: "Dot'un bir kopyasında şunu yazın:",
  opens: "Arka plan hizmeti yanıt verir vermez bu pencere web arayüzünü kendiliğinden açar.",
};

const heading = /** @type {HTMLElement} */ (document.querySelector("h1"));
const why = /** @type {HTMLElement} */ (document.querySelector(".why"));
const advice = /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll(".start"));
const worded = /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll("[data-word]"));
/** The English of each worded element, as the HTML has it. */
const english = new Map(Array.from(worded, (element) => [element, element.textContent ?? ""]));

function tell() {
  const told = new URLSearchParams(location.hash.slice(1));
  const turkish = told.get("lang") === "tr";
  document.documentElement.lang = turkish ? "tr" : "en";
  for (const element of worded) {
    const key = /** @type {keyof typeof TURKISH} */ (element.dataset.word);
    element.textContent = turkish ? TURKISH[key] : (english.get(element) ?? "");
  }
  const title = told.get("title");
  const detail = told.get("detail");
  if (title !== null) {
    heading.textContent = title;
  }
  for (const part of advice) {
    part.hidden = title !== null;
  }
  why.textContent = detail ?? "";
  why.hidden = detail === null;
}

tell();
window.addEventListener("hashchange", tell);

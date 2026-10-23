// What the shell tells this page in its fragment (`down_url` in src-tauri/src/lib.rs, item 191): `title` stands for
// "Dot is not running" and the advice to start it, when the shell could not read one of the daemon's files and the
// daemon may well run, or when something else answers at the daemon's address (item 558); `detail` says which file and
// what is wrong with it, or what answers there. The shell sends the page again when that changes.

const heading = document.querySelector("h1");
const why = /** @type {HTMLElement} */ (document.querySelector(".why"));
const advice = /** @type {NodeListOf<HTMLElement>} */ (document.querySelectorAll(".start"));
const notRunning = heading.textContent;

function tell() {
  const told = new URLSearchParams(location.hash.slice(1));
  const title = told.get("title");
  const detail = told.get("detail");
  heading.textContent = title ?? notRunning;
  for (const part of advice) {
    part.hidden = title !== null;
  }
  why.textContent = detail ?? "";
  why.hidden = detail === null;
}

tell();
window.addEventListener("hashchange", tell);

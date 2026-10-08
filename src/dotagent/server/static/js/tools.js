// One tool call of a run: a collapsed row that shows what ran and how it went, and opens to the arguments and output.

import { imageUrl } from "./api.js";
import { clip, copyText, duration, el } from "./dom.js";
import { icon, toolIconName } from "./icons.js";
import { showImage, toast } from "./ui.js";

const PRIMARY_KEYS = ["command", "url", "path", "query", "goal", "text", "name", "app", "ref", "title", "script", "key"];
const SUMMARY_CHARS = 200;
const TEXT_PREVIEW_CHARS = 6000;

/** A short line for what the model asked the tool to do, taken from the most telling argument. */
export function describeArguments(args) {
  if (args === null || typeof args !== "object") {
    return "";
  }
  const action = typeof args.action === "string" ? args.action : "";
  for (const key of PRIMARY_KEYS) {
    const value = args[key];
    if (typeof value === "string" && value !== "") {
      return clip(`${action} ${value}`, SUMMARY_CHARS);
    }
  }
  return action;
}

function firstLine(text) {
  const line = text.split("\n").find((candidate) => candidate.trim() !== "");
  return line === undefined ? "" : clip(line, SUMMARY_CHARS);
}

function textBlock(text, failed) {
  const view = el("pre", { class: failed ? "out bad" : "out" }, text.length > TEXT_PREVIEW_CHARS ? text.slice(0, TEXT_PREVIEW_CHARS) : text);
  if (text.length <= TEXT_PREVIEW_CHARS) {
    return view;
  }
  const more = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      onclick: () => {
        view.textContent = text;
        more.remove();
      },
    },
    `Show all (${text.length.toLocaleString("en-GB")} characters)`,
  );
  return el("div", { class: "stack tight" }, view, more);
}

function thumbnail(name) {
  const image = el("img", { alt: "Screenshot", loading: "lazy" });
  const holder = el("button", { class: "shot", type: "button", "aria-label": "Show the screenshot at full size" }, image);
  imageUrl(name).then(
    (url) => {
      image.src = url;
      holder.addEventListener("click", () => showImage(url, "Screenshot"));
    },
    (error) => {
      holder.replaceChildren(el("span", { class: "small muted" }, `Image unavailable: ${error.message}`));
      holder.disabled = true;
    },
  );
  return holder;
}

function section(title, content, copyable) {
  return el(
    "div",
    null,
    el(
      "div",
      { class: "row" },
      el("h4", null, title),
      el("span", { class: "spacer" }),
      copyable === null
        ? null
        : el(
            "button",
            {
              class: "btn ghost sm",
              type: "button",
              onclick: () =>
                copyText(copyable).then(
                  () => toast("ok", "Copied"),
                  (error) => toast("error", `Could not copy: ${error.message}`),
                ),
            },
            icon("copy"),
            "Copy",
          ),
    ),
    content,
  );
}

/** A row for a tool call. `finish(result)` fills in the outcome once the `tool_result` event arrives. */
export function toolRow({ tool, args }) {
  const state = el("span", { class: "tool-state" }, el("span", { class: "spinner" }));
  const summary = el("span", { class: "tool-sum" }, describeArguments(args));
  const time = el("span", { class: "tool-time" });
  const badges = el("span", { class: "cluster" });
  const head = el(
    "summary",
    { class: "tool-head" },
    state,
    el("span", { class: "tool-kind" }, icon(toolIconName(tool))),
    el("span", { class: "tool-name" }, tool),
    summary,
    badges,
    time,
    el("span", { class: "chev" }, icon("chevron")),
  );
  const body = el("div", { class: "tool-body" });
  const details = el("details", { class: "tool" }, head, body);
  const shots = el("div", { class: "shots" });
  const node = el("div", { class: "stack tight" }, details, shots);
  let result = null;
  let built = false;

  function buildBody() {
    built = true;
    const argsText = JSON.stringify(args, null, 2);
    body.replaceChildren(
      section("Arguments", el("pre", { class: "out" }, argsText), argsText),
      result === null
        ? el("div", { class: "small muted" }, "Still running…")
        : section(
            result.ok ? "Result" : "Error",
            result.text === "" ? el("div", { class: "small muted" }, "(no text)") : textBlock(result.text, !result.ok),
            result.text === "" ? null : result.text,
          ),
    );
  }

  details.addEventListener("toggle", () => {
    if (details.open && (!built || result !== null)) {
      buildBody();
    }
  });

  function finish(outcome) {
    result = outcome;
    state.replaceChildren(icon(outcome.ok ? "check" : "x"));
    state.className = `tool-state ${outcome.ok ? "ok" : "bad"}`;
    if (!outcome.ok) {
      summary.className = "tool-sum bad";
      summary.textContent = firstLine(outcome.text) || "failed";
    } else if (outcome.summary !== "") {
      summary.textContent = clip(outcome.summary, SUMMARY_CHARS);
    }
    time.textContent = duration(outcome.seconds);
    if (outcome.risk !== "" && outcome.risk !== "safe") {
      badges.replaceChildren(el("span", { class: `badge r-${outcome.risk}` }, outcome.risk));
    }
    shots.replaceChildren(...outcome.images.map(thumbnail));
    if (details.open) {
      buildBody();
    }
  }

  return { node, finish };
}

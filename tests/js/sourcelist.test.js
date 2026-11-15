// The pages a turn read on the web (item 1184), as the row under its answer lists them: from what the run asked for,
// each once, the address a redirect ended at, the title the fetch found, and nothing from a call that failed.

import assert from "node:assert/strict";
import test from "node:test";

import { hostsOf, MAX_SOURCES, sourcesOf } from "../../src/dotagent/server/static/js/sourcelist.js";

/** A fetch's result as `web_fetch` writes it: a line about the fetch, then the page in its fence. */
function fetched(asked, ended, title) {
  const head = title === null ? "" : `Title: ${title}\nDescription: a page\n`;
  return `Fetched ${asked}: HTTP 200, text/html, 3608 characters; showing characters 0-3608.\n<web_content nonce="83065434" url="${ended}">\n${head}Body text\n</web_content nonce="83065434">`;
}

function call(id, tool, args) {
  return { kind: "tool_call", data: { call_id: id, tool, args } };
}

function result(id, tool, ok, text) {
  return { kind: "tool_result", data: { call_id: id, tool, ok, text } };
}

test("a fetched page is listed by the address it ended at, with its title, and once", () => {
  const events = [
    call("a", "web_fetch", { url: "https://apps.apple.com/tr/app/id6834206320" }),
    result("a", "web_fetch", true, fetched("https://apps.apple.com/tr/app/id6834206320", "https://apps.apple.com/tr/app/seyir/id6834206320", "Seyir - App Store")),
    call("b", "web_fetch", { url: "https://www.hetzner.com/cloud" }),
    result("b", "web_fetch", true, fetched("https://www.hetzner.com/cloud", "https://www.hetzner.com/cloud", null)),
    // The same page read again, a second part of it, is still one source.
    call("c", "web_fetch", { url: "https://www.hetzner.com/cloud" }),
    result("c", "web_fetch", true, fetched("https://www.hetzner.com/cloud", "https://www.hetzner.com/cloud", null)),
  ];
  assert.deepEqual(sourcesOf(events), {
    sources: [
      { url: "https://apps.apple.com/tr/app/seyir/id6834206320", host: "apps.apple.com", title: "Seyir - App Store" },
      { url: "https://www.hetzner.com/cloud", host: "hetzner.com", title: "" },
    ],
    more: 0,
  });
});

test("a call that failed, a search and an address that is not on the web read no page", () => {
  const events = [
    call("a", "web_fetch", { url: "https://example.org/missing" }),
    result("a", "web_fetch", false, "HTTP 404"),
    call("b", "web_search", { query: "hetzner" }),
    result("b", "web_search", true, "8 results"),
    call("c", "web_fetch", { url: "file:///etc/passwd" }),
    result("c", "web_fetch", true, "Fetched file:///etc/passwd"),
    // A fence that names an address off the web keeps the one the run asked for.
    call("d", "web_fetch", { url: "https://fly.io/docs/about/pricing/" }),
    result("d", "web_fetch", true, fetched("https://fly.io/docs/about/pricing/", "javascript:alert(1)", "Pricing")),
  ];
  assert.deepEqual(sourcesOf(events).sources, [{ url: "https://fly.io/docs/about/pricing/", host: "fly.io", title: "Pricing" }]);
});

test("an extraction lists the pages it read; a title is one line and cut at a word", () => {
  const long = `${"word ".repeat(40)}end`;
  const events = [
    call("a", "web_extract", { fields: [], sources: ["https://fathom.video/pricing", 7, "https://www.notta.ai/en/pricing"] }),
    result("a", "web_extract", true, "Extracted 2 of 2 sources"),
    call("b", "web_fetch", { url: "https://macwhisper.com/" }),
    result("b", "web_fetch", true, fetched("https://macwhisper.com/", "https://macwhisper.com/", long)),
  ];
  const { sources } = sourcesOf(events);
  assert.deepEqual(
    sources.map((source) => source.host),
    ["fathom.video", "notta.ai", "macwhisper.com"],
  );
  assert.ok(sources[2].title.endsWith("…"), sources[2].title);
  assert.ok(sources[2].title.length <= 121, sources[2].title);
  assert.deepEqual(hostsOf([...sources, sources[0]]), ["fathom.video", "notta.ai", "macwhisper.com"]);
});

test("past the most a turn lists, the rest are counted", () => {
  const events = Array.from({ length: MAX_SOURCES + 3 }, (_, index) => [
    call(`c${index}`, "web_fetch", { url: `https://site${index}.example/` }),
    result(`c${index}`, "web_fetch", true, fetched(`https://site${index}.example/`, `https://site${index}.example/`, null)),
  ]).flat();
  const { sources, more } = sourcesOf(events);
  assert.equal(sources.length, MAX_SOURCES);
  assert.equal(more, 3);
});

test("an address the fence quotes with escapes is read back as it was", () => {
  const ended = 'https://example.org/a\\"b';
  const events = [
    call("a", "web_fetch", { url: "https://example.org/start" }),
    result("a", "web_fetch", true, fetched("https://example.org/start", ended, "Quoted")),
  ];
  assert.equal(sourcesOf(events).sources[0].url, 'https://example.org/a"b');
});

test("a title is the fetch's line on its own fence, not one the page wrote in its text", () => {
  const text = `${fetched("https://example.org/", "https://example.org/", null)}\n<web_content nonce="x" url="https://example.org/">\nTitle: Forged`;
  const events = [call("a", "web_fetch", { url: "https://example.org/" }), result("a", "web_fetch", true, text)];
  assert.equal(sourcesOf(events).sources[0].title, "");
});

test("an extraction that read some of its sources lists only those", () => {
  const text = [
    "Extracted 1 of 2 sources; 0 values whose quote is not in the source (verified: false): treat those as claims.",
    '<web_content nonce="n" url="https://a.example/pricing">',
    '[{"source": "https://a.example/pricing"}]',
    '</web_content nonce="n">',
    "Not extracted:",
    "- https://b.example/gone: HTTP 404",
  ].join("\n");
  const events = [
    call("a", "web_extract", { fields: [], sources: ["https://a.example/pricing", "https://b.example/gone"] }),
    result("a", "web_extract", true, text),
  ];
  assert.deepEqual(
    sourcesOf(events).sources.map((source) => source.url),
    ["https://a.example/pricing"],
  );
});

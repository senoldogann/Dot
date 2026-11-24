import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BUDGET_BYTES,
  EVENTS_KEPT,
  evictions,
  historyOf,
  LAYOUT,
  lastIdOf,
  listFor,
  listKey,
  listReadable,
  ownerChanged,
  ownerOf,
  RECORD_MAX_BYTES,
  readable,
  RUNS_KEPT,
  snapshotOf,
  touch,
} from "../../src/dotagent/server/static/js/cacherules.js";

/** `count` stored events of run `runId`, ids from `from`. */
function events(runId, count, from = 1) {
  return Array.from({ length: count }, (_, index) => ({
    id: from + index,
    run_id: runId,
    ts: 1000 + index,
    kind: "tool_result",
    data: { n: index },
  }));
}

/** The stream's messages for `list` (the shape `sse` yields). */
function messages(list) {
  return list.map((event) => ({ id: event.id, event: event.kind, data: event }));
}

function run(id, threadId = "thread_1") {
  return { id, thread_id: threadId, goal: `goal of ${id}`, status: "done", parent_run_id: null };
}

function source({ earlier = 0, own = 10, read = true, queue = null } = {}) {
  const runs = Array.from({ length: earlier }, (_, index) => run(`run_${String(index).padStart(3, "0")}`));
  const map = new Map();
  if (read) {
    for (const past of runs) {
      map.set(past.id, { events: events(past.id, 5) });
    }
  }
  return {
    id: "run_open",
    detail: { run: run("run_open"), title: "A chat", children: [], active: false, elsewhere: false },
    history: { earlier: runs, read: map, cut: false },
    messages: messages(events("run_open", own)),
    queue,
  };
}

test("a read of a list the shell draws is kept under one key, and any other read is not kept", () => {
  assert.equal(listKey("/threads?limit=60"), "threads");
  assert.equal(listKey("/threads?limit=200"), "threads");
  assert.equal(listKey("/agents"), "agents");
  assert.equal(listKey("/status"), "status");
  assert.equal(listKey("/activity"), "activity");
  assert.equal(listKey("/approvals?status=pending&limit=100"), "approvals");
  assert.equal(listKey("/assistant"), "assistant");
  assert.equal(listKey("/themes"), "themes");
  for (const path of ["/runs/run_1", "/approvals?status=decided", "/threads", "/agents/agent_1", "/models", "/status/x", "/vault"]) {
    assert.equal(listKey(path), null, path);
  }
});

test("the list of chats kept once answers a read of any limit, cut to it", () => {
  const chats = Array.from({ length: 10 }, (_, index) => ({ id: `t${index}` }));
  assert.equal(listFor("/threads?limit=3", chats).length, 3);
  assert.equal(listFor("/threads?limit=60", chats).length, 10);
  assert.deepEqual(
    listFor("/threads?limit=2", chats).map((chat) => chat.id),
    ["t0", "t1"],
  );
  const agents = [{ id: "a" }];
  assert.equal(listFor("/agents", agents), agents);
});

test("a cache is another's when the address or the key differs, never when none was kept", () => {
  const mine = ownerOf("https://mac.tail1234.ts.net", "abc");
  assert.equal(ownerChanged(null, mine), false);
  assert.equal(ownerChanged(undefined, mine), false);
  assert.equal(ownerChanged(mine, mine), false);
  assert.equal(ownerChanged(ownerOf("https://mac.tail1234.ts.net", "def"), mine), true);
  assert.equal(ownerChanged(ownerOf("http://127.0.0.1:1234", "abc"), mine), true);
});

test("a snapshot keeps the newest twelve runs of a chat: the run itself and the eleven before it", () => {
  const snapshot = snapshotOf(source({ earlier: 30 }), 5000);
  assert.equal(snapshot.earlier.length, RUNS_KEPT - 1);
  assert.equal(snapshot.earlier[0].id, "run_019");
  assert.equal(snapshot.earlier.at(-1).id, "run_029");
  assert.deepEqual(
    Object.keys(snapshot.read).toSorted(),
    snapshot.earlier.map((past) => past.id),
  );
  assert.equal(snapshot.id, "run_open");
  assert.equal(snapshot.threadId, "thread_1");
  assert.equal(snapshot.savedAt, 5000);
  assert.equal(snapshot.layout, LAYOUT);
});

test("a snapshot keeps the last four hundred events of each run, oldest first", () => {
  const long = source({ earlier: 2, own: 1000 });
  long.history.read.set("run_000", { events: events("run_000", 900) });
  const snapshot = snapshotOf(long, 1);
  assert.equal(snapshot.messages.length, EVENTS_KEPT);
  assert.equal(snapshot.messages[0].id, 601);
  assert.equal(snapshot.messages.at(-1).id, 1000);
  assert.equal(snapshot.read.run_000.length, EVENTS_KEPT);
  assert.equal(snapshot.read.run_000[0].id, 501);
  assert.equal(snapshot.read.run_001.length, 5);
});

test("a live piece with no id is not kept: Dot does not store it, so it cannot be read again", () => {
  const live = source({ own: 5 });
  live.messages.push({ id: 0, event: "reply_delta", data: { kind: "reply_delta" } });
  const snapshot = snapshotOf(live, 1);
  assert.equal(snapshot.messages.length, 5);
  assert.ok(snapshot.messages.every((message) => message.id > 0));
});

test("a run whose events could not be read keeps its record and no events", () => {
  const failed = source({ earlier: 2, read: false });
  failed.history.read.set("run_000", { error: new Error("no link") });
  const snapshot = snapshotOf(failed, 1);
  assert.equal(snapshot.earlier.length, 2);
  assert.deepEqual(snapshot.read, {});
});

test("a snapshot is dated when Dot last said how the chat stands, not when it is written: a copy never looks fresher", () => {
  const drawn = { ...source({ own: 3 }), verifiedAt: 1_000 };
  assert.equal(snapshotOf(drawn, 9_000).savedAt, 1_000);
  assert.equal(snapshotOf(source({ own: 3 }), 9_000).savedAt, 9_000);
});

test("the chat's queue is kept beside it", () => {
  const queue = { held: false, messages: [{ id: "q1", text: "then this" }] };
  assert.deepEqual(snapshotOf(source({ queue }), 1).queue, queue);
  assert.equal(snapshotOf(source(), 1).queue, null);
});

test("a chat past the size one chat may take gives up its oldest events first, then its oldest runs, until it fits", () => {
  const heavy = source({ earlier: 6, own: 400 });
  const big = "x".repeat(8000);
  for (const found of heavy.history.read.values()) {
    found.events = found.events.map((event) => ({ ...event, data: { big } }));
  }
  heavy.messages = heavy.messages.map((message) => ({ ...message, data: { ...message.data, data: { big } } }));
  const snapshot = snapshotOf(heavy, 1);
  assert.ok(snapshot.bytes <= RECORD_MAX_BYTES, `${snapshot.bytes} bytes`);
  assert.ok(snapshot.bytes > 0);
  // The newest events are the ones that stay.
  assert.equal(snapshot.messages.at(-1).id, 400);
  assert.ok(snapshot.messages.length < 400 && snapshot.messages.length > 0);
});

test("a chat that cannot fit even with no events keeps only its own record", () => {
  const huge = source({ earlier: 4 });
  huge.detail.title = "y".repeat(RECORD_MAX_BYTES + 10);
  const snapshot = snapshotOf(huge, 1);
  assert.deepEqual(snapshot.messages, []);
  assert.deepEqual(snapshot.earlier, []);
});

test("the history of a record reads as the page's own: a map of what the runs did, none an error", () => {
  const snapshot = snapshotOf(source({ earlier: 3 }), 1);
  const history = historyOf(snapshot);
  assert.ok(history.read instanceof Map);
  assert.equal(history.earlier.length, 3);
  assert.deepEqual([...history.read.keys()], ["run_000", "run_001", "run_002"]);
  assert.equal(history.read.get("run_001").events.length, 5);
  assert.equal(history.cut, false);
});

test("a record reads when it is of this layout and holds what a chat needs", () => {
  const good = snapshotOf(source({ earlier: 2 }), 1);
  assert.equal(readable(good), true);
  // What is stored and read back is plain JSON.
  assert.equal(readable(JSON.parse(JSON.stringify(good))), true);
});

test("a record that does not read is refused, whatever is wrong with it", () => {
  const good = snapshotOf(source({ earlier: 2 }), 1);
  const refused = [
    null,
    undefined,
    "text",
    42,
    [],
    {},
    { ...good, layout: LAYOUT + 1 },
    { ...good, layout: 0 },
    { ...good, id: 5 },
    { ...good, threadId: undefined },
    { ...good, savedAt: "yesterday" },
    { ...good, detail: null },
    { ...good, detail: {} },
    { ...good, detail: { run: { id: "run_other" } } },
    { ...good, earlier: {} },
    { ...good, read: null },
    { ...good, messages: "none" },
    { ...good, messages: [null] },
    { ...good, messages: [{ id: "one" }] },
  ];
  for (const record of refused) {
    assert.equal(readable(record), false, JSON.stringify(record)?.slice(0, 80));
  }
});

test("a kept list reads when it is of this layout and has a value", () => {
  assert.equal(listReadable({ layout: LAYOUT, id: "agents", savedAt: 1, value: [] }), true);
  assert.equal(listReadable({ layout: LAYOUT, id: "status", savedAt: 1, value: null }), true);
  for (const record of [null, {}, { layout: 9, id: "a", savedAt: 1, value: [] }, { layout: LAYOUT, id: "a", savedAt: 1 }, { layout: LAYOUT, savedAt: 1, value: [] }]) {
    assert.equal(listReadable(record), false);
  }
});

test("the chats read longest ago go first, until the rest fit", () => {
  const entries = [
    { id: "a", bytes: 10, readAt: 300 },
    { id: "b", bytes: 10, readAt: 100 },
    { id: "c", bytes: 10, readAt: 200 },
  ];
  assert.deepEqual(evictions(entries, 30, null), []);
  assert.deepEqual(evictions(entries, 20, null), ["b"]);
  assert.deepEqual(evictions(entries, 10, null), ["b", "c"]);
  assert.deepEqual(evictions(entries, 0, null), ["b", "c", "a"]);
});

test("the chat the page is on is never let go, even when it alone is over the budget", () => {
  const entries = [
    { id: "open", bytes: 50, readAt: 1 },
    { id: "other", bytes: 10, readAt: 500 },
  ];
  assert.deepEqual(evictions(entries, 20, "open"), ["other"]);
  assert.deepEqual(evictions([{ id: "open", bytes: 50, readAt: 1 }], 20, "open"), []);
});

test("chats equally old go in the order of their ids, so that the same index lets go the same chats", () => {
  const entries = [
    { id: "b", bytes: 10, readAt: 1 },
    { id: "a", bytes: 10, readAt: 1 },
  ];
  assert.deepEqual(evictions(entries, 10, null), ["a"]);
});

test("writing a chat puts it in the index as the newest read and lets go what no longer fits", () => {
  const entries = [
    { id: "a", bytes: 40, readAt: 100 },
    { id: "b", bytes: 40, readAt: 200 },
  ];
  const written = touch(entries, { id: "c", bytes: 40, now: 300 }, 100);
  assert.deepEqual(written.dropped, ["a"]);
  assert.deepEqual(written.entries.map((entry) => entry.id).toSorted(), ["b", "c"]);
  // Writing a chat again replaces its entry.
  const again = touch(written.entries, { id: "b", bytes: 10, now: 400 }, 100);
  assert.deepEqual(again.dropped, []);
  assert.equal(again.entries.find((entry) => entry.id === "b").bytes, 10);
  assert.equal(again.entries.length, 2);
});

test("reading a chat moves it to the newest read without changing its size", () => {
  const entries = [
    { id: "a", bytes: 40, readAt: 100 },
    { id: "b", bytes: 40, readAt: 200 },
  ];
  const read = touch(entries, { id: "a", now: 300 }, 1000);
  assert.equal(read.entries.find((entry) => entry.id === "a").bytes, 40);
  assert.equal(read.entries.find((entry) => entry.id === "a").readAt, 300);
  // The chat that was read is the last to go when the budget shrinks.
  assert.deepEqual(evictions(read.entries, 40, null), ["b"]);
});

test("the stream goes on after the highest stored id; a live piece with no id is not one", () => {
  assert.equal(lastIdOf([]), 0);
  assert.equal(lastIdOf([{ id: 3 }, { id: 9 }, { id: 0 }, { id: 5 }]), 9);
  assert.equal(lastIdOf([{ id: 0 }]), 0);
});

test("the budget is 24 MB and one chat takes 2 MB at most", () => {
  assert.equal(BUDGET_BYTES, 24 * 1024 * 1024);
  assert.equal(RECORD_MAX_BYTES, 2 * 1024 * 1024);
  assert.equal(RUNS_KEPT, 12);
  assert.equal(EVENTS_KEPT, 400);
});

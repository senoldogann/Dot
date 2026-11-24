import assert from "node:assert/strict";
import { test } from "node:test";

import {
  added,
  afterKill,
  DAY_MS,
  entryOf,
  leaveOld,
  nextDue,
  ofChat,
  SENDING_ALREADY,
  sendAgain,
  sendingAlready,
  settled,
  tooOld,
  verdictOf,
  waitingCount,
} from "../../src/dotagent/server/static/js/outboxrules.js";

let seq = 0;
function make(id, chat, kind = "message", now = 1000) {
  seq += 1;
  return entryOf({ id, kind, chat, method: "POST", path: `/threads/${chat}/queue`, body: { text: id, key: id }, text: id, now, seq });
}

const UP = { linkUp: true, visible: true };

test("a new entry waits, with its key, its chat and the order it was made in", () => {
  const entry = make("k1", "thread_a");
  assert.equal(entry.state, "waiting");
  assert.equal(entry.tries, 0);
  assert.equal(entry.id, "k1");
  assert.equal(entry.chat, "thread_a");
  assert.equal(entry.text, "k1");
});

test("one send for one key: the same key made twice is one entry", () => {
  const first = make("k1", "thread_a");
  const again = { ...first, text: "other words" };
  const entries = added(added([], first), again);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].text, "k1");
});

test("nothing is due while the phone has no link or the page is out of view", () => {
  const entries = [make("k1", "a")];
  assert.equal(nextDue(entries, { linkUp: false, visible: true }), null);
  assert.equal(nextDue(entries, { linkUp: true, visible: false }), null);
  assert.equal(nextDue(entries, UP).id, "k1");
});

test("a chat's entries go in the order they were made, and the first holds the ones behind it", () => {
  const one = make("one", "a");
  const two = make("two", "a");
  const three = make("three", "a");
  assert.equal(nextDue([three, one, two], UP).id, "one");
  // While the first is on its way nothing else is sent.
  const sending = [{ ...one, state: "sending" }, two, three];
  assert.equal(nextDue(sending, UP), null);
  // Once it is done, the second is next.
  assert.equal(nextDue(settled(sending, "one", { done: true }), UP).id, "two");
});

test("an entry that waits for the link holds the ones behind it in its chat, not those of another chat", () => {
  const a1 = make("a1", "a");
  const a2 = make("a2", "a");
  const b1 = make("b1", "b");
  const waiting = settled([{ ...a1, state: "sending" }, a2, b1], "a1", { again: true });
  assert.equal(waiting.find((entry) => entry.id === "a1").state, "waiting");
  assert.equal(nextDue(waiting, UP).id, "a1");
});

test("of the chats' heads the oldest goes first", () => {
  const b1 = make("b1", "b");
  const a1 = make("a1", "a");
  const b2 = make("b2", "b");
  assert.equal(nextDue([a1, b1, b2], UP).id, "b1");
});

test("an entry that was refused or left does not hold the next one", () => {
  const first = { ...make("first", "a"), state: "refused", why: "no" };
  const second = make("second", "a");
  assert.equal(nextDue([first, second], UP).id, "second");
  const left = { ...make("left", "a"), state: "left" };
  assert.equal(nextDue([left, second], UP).id, "second");
});

test("a message that waited more than a day is left to the owner: Dot keeps its key a day", () => {
  const old = make("old", "a", "message", 0);
  const fresh = make("fresh", "a", "message", DAY_MS);
  assert.equal(tooOld(old, DAY_MS), false);
  assert.equal(tooOld(old, DAY_MS + 1), true);
  assert.equal(tooOld(fresh, DAY_MS + 1), false);
  const left = leaveOld([old, fresh], DAY_MS + 1, "It waited more than a day.");
  assert.equal(left[0].state, "left");
  assert.equal(left[0].why, "It waited more than a day.");
  assert.equal(left[1].state, "waiting");
  // A left entry is not sent, and does not hold the one behind it.
  assert.equal(nextDue(left, UP).id, "fresh");
});

test("what the daemon takes is done", () => {
  assert.deepEqual(verdictOf(make("m", "a"), { ok: true }), { done: true });
});

test("no answer, a busy daemon and a message still being sent wait: the entry is kept and holds the order", () => {
  const message = make("m", "a");
  for (const status of [0, 408, 429, 500, 502, 503, 504]) {
    assert.deepEqual(verdictOf(message, { status }), { again: true }, String(status));
  }
  // 409 saying that the message is being sent already is the first sending of it still under way: it is waited out.
  assert.deepEqual(verdictOf(message, { status: 409, message: SENDING_ALREADY }), { again: true });
  assert.equal(sendingAlready("The message is being sent already."), true);
});

test("a 409 that is Dot's refusal is not waited out for a day: the message stays with Dot's words and holds nothing", () => {
  const message = make("m", "a");
  // A steer to a run that has ended, a next run in a chat that has one at work: nothing changes them with time.
  for (const why of ["the run has ended", "this chat already has a run at work", ""]) {
    assert.deepEqual(verdictOf(message, { status: 409, message: why }), { refused: true, why });
  }
  assert.equal(sendingAlready(undefined), false);
  const after = settled([message], "m", verdictOf(message, { status: 409, message: "the run has ended" }));
  assert.equal(after[0].state, "refused");
  assert.equal(nextDue(after, UP), null, "a refused message holds no one");
});

test("an answer to a card that is closed is told: expired, or answered on the Mac", () => {
  const answer = make("a", "a", "answer");
  assert.deepEqual(verdictOf(answer, { status: 404 }), { done: true, told: "expired" });
  assert.deepEqual(verdictOf(answer, { status: 409 }), { done: true, told: "answered" });
});

test("a stop or a change of the queue that finds nothing to do is done", () => {
  for (const kind of ["stop", "queue"]) {
    const entry = make(kind, "a", kind);
    assert.deepEqual(verdictOf(entry, { status: 404 }), { done: true, told: "gone" });
    assert.deepEqual(verdictOf(entry, { status: 409 }), { done: true, told: "gone" });
  }
});

test("a message the daemon refuses stays, with its words, and is not tried again", () => {
  const message = make("m", "a");
  assert.deepEqual(verdictOf(message, { status: 422, message: "That model is not available" }), {
    refused: true,
    why: "That model is not available",
  });
  assert.deepEqual(verdictOf(message, { status: 404, message: "no such chat" }), { refused: true, why: "no such chat" });
  const settledOnes = settled([message], "m", { refused: true, why: "no" });
  assert.equal(settledOnes[0].state, "refused");
  assert.equal(settledOnes[0].why, "no");
  assert.equal(nextDue(settledOnes, UP), null);
});

test("a message the daemon refuses stays with its words; a change with no row to stay in is told and let go", () => {
  const message = make("m", "a");
  assert.deepEqual(verdictOf(message, { status: 422, message: "bad order" }), { refused: true, why: "bad order" });
  assert.deepEqual(verdictOf(message, { status: 401, message: "" }), { refused: true, why: "" });
  // An answer, a Stop and a change of the queue have no row that holds them: kept as refused they would sit in the store
  // for ever, unseen and with no Discard.
  for (const kind of ["answer", "stop", "queue"]) {
    const entry = make(kind, "a", kind);
    assert.deepEqual(verdictOf(entry, { status: 422, message: "bad order" }), { done: true, told: "refused", why: "bad order" });
    assert.deepEqual(verdictOf(entry, { status: 403, message: "" }), { done: true, told: "refused", why: "" });
    assert.deepEqual(settled([entry], kind, verdictOf(entry, { status: 403, message: "" })), []);
  }
});

test("a message sent again after it was left or refused has a new key, a new place in the order and a new day", () => {
  const left = { ...make("old-key", "a", "message", 0), state: "left" };
  const after = sendAgain([left], "old-key", "new-key", 5 * DAY_MS, 99);
  assert.equal(after[0].id, "new-key");
  assert.equal(after[0].body.key, "new-key");
  assert.equal(after[0].state, "waiting");
  assert.equal(after[0].madeAt, 5 * DAY_MS);
  assert.equal(after[0].seq, 99);
  assert.equal(tooOld(after[0], 5 * DAY_MS + 1000), false);
  // One that waits is not sent again under a new key: it is sent as it is.
  const waiting = make("w", "a");
  assert.deepEqual(sendAgain([waiting], "w", "x", 1, 1), [waiting]);
});

test("what waits is counted for the line; what is the owner's to decide is not", () => {
  const entries = [
    make("a", "a"),
    { ...make("b", "a"), state: "sending" },
    { ...make("c", "a"), state: "left" },
    { ...make("d", "a"), state: "refused" },
  ];
  assert.equal(waitingCount(entries), 2);
  assert.equal(waitingCount([]), 0);
});

test("a chat's entries are listed in the order they were made", () => {
  const second = make("second", "a");
  const other = make("other", "b");
  const third = make("third", "a");
  assert.deepEqual(
    ofChat([third, other, second], "a").map((entry) => entry.id),
    ["second", "third"],
  );
});

test("an entry that was on its way when the page was killed waits again, and its key makes the second send harmless", () => {
  const entries = afterKill([{ ...make("a", "a"), state: "sending" }, { ...make("b", "a"), state: "refused" }, make("c", "a")]);
  assert.deepEqual(
    entries.map((entry) => entry.state),
    ["waiting", "refused", "waiting"],
  );
});

test("three messages made with no link are sent once each, in order, whatever the answers do", () => {
  // The scenario of the iPhone app's tests: three messages, one answer lost after the daemon took it.
  let entries = [];
  for (const id of ["one", "two", "three"]) {
    entries = added(entries, make(id, "chat"));
  }
  const sent = [];
  const answers = [{ status: 0 }, { ok: true }, { ok: true }, { ok: true }];
  while (entries.length > 0) {
    const entry = nextDue(entries, UP);
    assert.ok(entry !== null);
    entries = entries.map((held) => (held.id === entry.id ? { ...held, state: "sending", tries: held.tries + 1 } : held));
    sent.push(entry.id);
    entries = settled(entries, entry.id, verdictOf(entry, answers.shift()));
  }
  // The first was sent twice (its answer was lost: the daemon's key makes that one message), the rest once, in order.
  assert.deepEqual(sent, ["one", "one", "two", "three"]);
});

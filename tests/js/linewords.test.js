import assert from "node:assert/strict";
import { test } from "node:test";

import { fill } from "../../src/dotagent/server/static/js/i18n.js";
import { EN } from "../../src/dotagent/server/static/js/lang/en.js";
import { TR } from "../../src/dotagent/server/static/js/lang/tr.js";
import { ageWords, lineOf } from "../../src/dotagent/server/static/js/linewords.js";

const english = (key, values = {}) => fill(EN[key], values, key);
const turkish = (key, values = {}) => fill(TR[key], values, key);

const NOW = 10_000_000_000;
const MIN = 60_000;

/** The facts of a link that is well; each test changes what it is about. */
function facts(changes = {}) {
  return { online: true, copyFrom: null, waiting: 0, reconnecting: false, lastContact: null, refused: false, now: NOW, ...changes };
}

test("a link that is well says nothing", () => {
  assert.equal(lineOf(facts(), english), null);
  assert.equal(lineOf(facts(), turkish), null);
  // A copy shown while the phone has a link and Dot is not yet lost is nothing to say either.
  assert.equal(lineOf(facts({ copyFrom: NOW - 5 * MIN }), english), null);
});

test("with no link it says it is offline and shows the saved chats, and how old they are", () => {
  assert.deepEqual(lineOf(facts({ online: false, copyFrom: NOW - 3 * MIN }), english), {
    kind: "offline",
    text: "Offline: showing saved chats, last updated 3 min ago",
    action: null,
  });
  assert.equal(
    lineOf(facts({ online: false, copyFrom: NOW - 3 * MIN }), turkish)?.text,
    "Çevrimdışı: kayıtlı sohbetler gösteriliyor, son güncelleme 3 dk önce",
  );
});

test("with no link and things waiting to be sent it says how many, which is what can be acted on", () => {
  assert.equal(lineOf(facts({ online: false, waiting: 2, copyFrom: NOW - MIN }), english)?.text, "Offline: 2 waiting to send");
  assert.equal(lineOf(facts({ online: false, waiting: 1 }), english)?.text, "Offline: 1 waiting to send");
  assert.equal(lineOf(facts({ online: false, waiting: 2 }), turkish)?.text, "Çevrimdışı: 2 gönderilmeyi bekliyor");
});

test("with no link and nothing kept it says only that it is offline", () => {
  assert.equal(lineOf(facts({ online: false }), english)?.text, "Offline");
  assert.equal(lineOf(facts({ online: false }), turkish)?.text, "Çevrimdışı");
});

test("an unreachable Mac shows its last confirmed contact in both languages", () => {
  assert.equal(lineOf(facts({ reconnecting: true }), english)?.text, "Mac asleep or unreachable");
  assert.equal(lineOf(facts({ reconnecting: true }), turkish)?.text, "Mac uykuda veya ulaşılamıyor");
  const lastContact = new Date(2026, 10, 27, 0, 5).getTime();
  const time = "00:05";
  const lost = facts({ reconnecting: true, lastContact, waiting: 3 });
  assert.deepEqual(lineOf(lost, english), { kind: "reconnecting", text: `Mac asleep or unreachable since ${time}`, action: null });
  assert.equal(lineOf(lost, turkish)?.text, `Mac uykuda veya ulaşılamıyor · son bağlantı ${time}`);
});

test("no link comes before reconnecting: a phone with no link is offline, not reconnecting", () => {
  assert.equal(lineOf(facts({ online: false, reconnecting: true }), english)?.kind, "offline");
});

test("a key that was refused comes first and has the way to pair again", () => {
  const line = lineOf(facts({ refused: true, online: false, waiting: 2, reconnecting: true }), english);
  assert.deepEqual(line, { kind: "refused", text: "This phone's key was refused", action: "Pair again" });
  assert.deepEqual(lineOf(facts({ refused: true }), turkish), {
    kind: "refused",
    text: "Bu telefonun anahtarı reddedildi",
    action: "Yeniden eşle",
  });
});

test("only the refused key has something to press", () => {
  for (const changes of [{ online: false }, { online: false, waiting: 1 }, { reconnecting: true }, { online: false, copyFrom: NOW - MIN }]) {
    assert.equal(lineOf(facts(changes), english)?.action, null);
  }
});

test("how long ago a copy was kept: just now, minutes, hours, days", () => {
  assert.equal(ageWords(NOW, NOW, english), "just now");
  assert.equal(ageWords(NOW - 59_000, NOW, english), "just now");
  assert.equal(ageWords(NOW - MIN, NOW, english), "1 min ago");
  assert.equal(ageWords(NOW - 59 * MIN, NOW, english), "59 min ago");
  assert.equal(ageWords(NOW - 60 * MIN, NOW, english), "1 h ago");
  assert.equal(ageWords(NOW - 23 * 60 * MIN, NOW, english), "23 h ago");
  assert.equal(ageWords(NOW - 24 * 60 * MIN, NOW, english), "1 d ago");
  assert.equal(ageWords(NOW - 5 * 24 * 60 * MIN, NOW, english), "5 d ago");
  assert.equal(ageWords(NOW - 5 * MIN, NOW, turkish), "5 dk önce");
  assert.equal(ageWords(NOW - 2 * 60 * MIN, NOW, turkish), "2 sa önce");
  assert.equal(ageWords(NOW, NOW, turkish), "az önce");
  // A copy kept in the future (a clock that moved) is as new as it can be.
  assert.equal(ageWords(NOW + 5 * MIN, NOW, english), "just now");
});

test("every word of the line is in both languages, and a line fits a phone's width in either", () => {
  const keys = Object.keys(EN).filter((key) => key.startsWith("line."));
  assert.ok(keys.length >= 10);
  for (const key of keys) {
    assert.ok(key in TR, key);
  }
  for (const say of [english, turkish]) {
    for (const line of [
      lineOf(facts({ online: false, copyFrom: NOW - 23 * 60 * MIN }), say),
      lineOf(facts({ online: false, waiting: 12 }), say),
      lineOf(facts({ reconnecting: true, waiting: 12 }), say),
      lineOf(facts({ refused: true }), say),
    ]) {
      // Two lines of a 320 px phone at 12 px hold about 90 characters.
      assert.ok(line.text.length <= 90, line.text);
    }
  }
});

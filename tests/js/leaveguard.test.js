import assert from "node:assert/strict";
import { test } from "node:test";

import { createLeaveGuard } from "../../src/dotagent/server/static/js/leaveguard.js";

/**
 * A guard over a fake history: `entries` are the addresses, `at` the one on screen. `push`, `back` and `replace` are what
 * the browser did before `hashchange` came; each returns the guard's answer. A walk the guard makes (`go`) is queued and
 * its event is delivered by `settle()`, as the browser's is a moment later.
 */
function rig({ withIndex = true } = {}) {
  const log = [];
  const entries = [{ hash: "#/settings/schedules", state: null }, { hash: "#/settings/schedules/one", state: { dotLinked: true } }];
  const bar = { at: 1 };
  const queue = [];
  const place = () => ({ hash: entries[bar.at].hash, state: entries[bar.at].state, index: withIndex ? bar.at : null });
  const guard = createLeaveGuard({
    now: place,
    restore: (shown) => {
      entries[bar.at] = { hash: shown.hash, state: shown.state };
      log.push(`restore ${shown.hash}`);
    },
    go: (delta) => {
      log.push(`go ${delta}`);
      queue.push(delta);
    },
    proceed: () => {
      log.push(`open ${entries[bar.at].hash}`);
    },
    kept: () => {
      log.push("kept");
    },
  });
  guard.shownAt(place());
  return {
    guard,
    log,
    entries,
    bar,
    push(hash) {
      entries.splice(bar.at + 1, entries.length, { hash, state: null });
      bar.at += 1;
      return guard.arrived();
    },
    back() {
      bar.at -= 1;
      return guard.arrived();
    },
    replace(hash) {
      entries[bar.at] = { hash, state: null };
      return guard.arrived();
    },
    /** The events of the walks the guard made. */
    async settle() {
      while (queue.length > 0) {
        bar.at += queue.shift();
        await guard.arrived();
      }
    },
  };
}

test("a page that registered nothing is left without a question", async () => {
  const { push, log } = rig();
  await push("#/settings/memory");
  assert.deepEqual(log, ["open #/settings/memory"]);
});

test("a hook that says no puts a new entry back by walking, and the page stays at its own entry", async () => {
  const r = rig();
  r.guard.onBeforeLeave(async () => false);
  await r.push("#/runs");
  assert.deepEqual(r.log, ["go -1", "kept"]);
  await r.settle();
  assert.equal(r.entries[r.bar.at].hash, "#/settings/schedules/one");
  // The event of the walk back opened nothing, and the entry of the page kept its state.
  assert.deepEqual(r.entries[r.bar.at].state, { dotLinked: true });
  assert.ok(!r.log.some((line) => line.startsWith("open")));
});

test("a hook that answers at once lets the change through as it came: nothing is put back, nothing is walked again", async () => {
  const r = rig();
  let asked = 0;
  r.guard.onBeforeLeave(() => {
    asked += 1;
    return true;
  });
  await r.push("#/runs");
  assert.deepEqual(r.log, ["open #/runs"]);
  assert.equal(asked, 1);
  assert.equal(r.bar.at, 2);
  // A no that comes at once is put back all the same.
  const no = rig();
  no.guard.onBeforeLeave(() => false);
  await no.push("#/runs");
  assert.deepEqual(no.log, ["go -1", "kept"]);
});

test("a question that is answered yes walks the change again once the walk that undid it has come, and opens the page once", async () => {
  const r = rig();
  let asked = 0;
  // Answered at once, as a promise: the walk that undid the change has not come yet, and the second must wait for it.
  r.guard.onBeforeLeave(() => {
    asked += 1;
    return Promise.resolve(true);
  });
  await r.push("#/runs");
  assert.deepEqual(r.log, ["go -1"]);
  await r.settle();
  assert.deepEqual(r.log, ["go -1", "go 1", "open #/runs"]);
  assert.equal(asked, 1);
  assert.equal(r.bar.at, 2);
  // Answered later, after the walk back came: it is made again at once.
  const later = rig();
  let answer = () => undefined;
  later.guard.onBeforeLeave(
    () =>
      new Promise((resolve) => {
        answer = resolve;
      }),
  );
  const pending = later.push("#/runs");
  await later.settle();
  answer(true);
  await pending;
  assert.deepEqual(later.log, ["go -1", "go 1"]);
  await later.settle();
  assert.deepEqual(later.log, ["go -1", "go 1", "open #/runs"]);
});

test("the browser's own Back is undone by walking forward and every entry stays as it was", async () => {
  const r = rig();
  r.guard.onBeforeLeave(() => false);
  await r.back();
  assert.deepEqual(r.log, ["go 1", "kept"]);
  await r.settle();
  assert.equal(r.bar.at, 1);
  assert.deepEqual(
    r.entries.map((entry) => entry.hash),
    ["#/settings/schedules", "#/settings/schedules/one"],
  );
});

test("a replaced entry, or a browser that tells no index, has the address of the page written back instead", async () => {
  const replaced = rig();
  replaced.guard.onBeforeLeave(() => false);
  await replaced.replace("#/runs");
  assert.deepEqual(replaced.log, ["restore #/settings/schedules/one", "kept"]);
  assert.equal(replaced.entries[1].hash, "#/settings/schedules/one");
  assert.deepEqual(replaced.entries[1].state, { dotLinked: true });

  // "Leave" writes the address asked for back and opens the page, with no walk.
  const left = rig();
  left.guard.onBeforeLeave(async () => true);
  await left.replace("#/runs");
  assert.deepEqual(left.log, ["restore #/settings/schedules/one", "restore #/runs", "open #/runs"]);

  const blind = rig({ withIndex: false });
  blind.guard.onBeforeLeave(() => false);
  await blind.push("#/runs");
  assert.deepEqual(blind.log, ["restore #/settings/schedules/one", "kept"]);
});

test("the question is asked once, and a change made while it is open is put back without a question", async () => {
  const r = rig();
  let asked = 0;
  let answer = () => undefined;
  r.guard.onBeforeLeave(() => {
    asked += 1;
    return new Promise((resolve) => {
      answer = resolve;
    });
  });
  const first = r.push("#/runs");
  await r.settle();
  await r.push("#/settings/memory");
  await r.settle();
  assert.equal(asked, 1);
  assert.equal(r.entries[r.bar.at].hash, "#/settings/schedules/one");
  answer(false);
  await first;
  assert.ok(!r.log.some((line) => line.startsWith("open")));
});

test("an address the page is already at is not a way out, and a hook taken away is not asked", async () => {
  const r = rig();
  let asked = 0;
  const stop = r.guard.onBeforeLeave(() => {
    asked += 1;
    return false;
  });
  await r.replace("#/settings/schedules/one");
  assert.equal(asked, 0);
  stop();
  await r.push("#/runs");
  assert.equal(asked, 0);
  assert.deepEqual(r.log, ["open #/settings/schedules/one", "open #/runs"]);
});

test("the first no is the answer, and the hooks of a page that was left are cleared", async () => {
  const r = rig();
  r.guard.onBeforeLeave(() => true);
  r.guard.onBeforeLeave(async () => false);
  let third = 0;
  r.guard.onBeforeLeave(() => {
    third += 1;
    return true;
  });
  await r.push("#/runs");
  await r.settle();
  assert.equal(third, 0);
  r.guard.clear();
  await r.push("#/memory");
  assert.ok(r.log.includes("open #/memory"));
});

test("a hook that fails never traps the owner, and an ended session opens nothing", async () => {
  const failing = rig();
  failing.guard.onBeforeLeave(() => {
    throw new Error("broken");
  });
  const quiet = console.error;
  console.error = () => undefined;
  try {
    await failing.push("#/runs");
    await failing.settle();
  } finally {
    console.error = quiet;
  }
  assert.deepEqual(failing.log, ["open #/runs"]);

  const ended = rig();
  ended.guard.onBeforeLeave(() => true);
  ended.guard.stop();
  await ended.push("#/runs");
  assert.deepEqual(ended.log, []);
});

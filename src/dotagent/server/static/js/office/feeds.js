// What the agents of the office do, step by step (item 889): each run followed has its stored events read once, then
// one stream (`GET /runs/streams`) brings the new ones of those that work (a finished one keeps its history alone), and
// every event is read into a short feed of its own: the calls in words ("Web search · cafés in Oulu"), the answer as it is written, how it ended, and to
// whom the agent sent a message. The office shows the newest call over a working agent's head, the feed in its panel,
// and a live message to another agent as the sender walking to the other's desk.

import { api, follow } from "../api.js";
import { clip } from "../dom.js";
import { describeArguments, toolWords } from "../tools.js";

// The most runs followed at once: the office shows that many working agents' steps live.
const MOST_FOLLOWED = 8;
// The steps a feed keeps, the newest last, and how much of the answer being written: all of an answer as long as most
// are, so that what the panel shows of it is cut between its blocks (office/tail.js), not here at any character (the
// pre-push review of item 905). Eight feeds of 64,000 characters at most.
const STEPS_KEPT = 14;
const TEXT_KEPT = 64_000;
const STEP_CHARS = 90;
// The tools that are the run's own business, not a step to show (finishing, the plan, the groups).
const QUIET = new Set(["finish", "update_plan", "use_tools", "react", "set_status"]);

/**
 * @typedef {{ runId: string, steps: { words: string, ok: boolean | null, ts: number }[], text: string, ended: string | null,
 *   doing: string, at: number, lastId: number, said: string[] }} Feed
 */

/** A new, empty feed of `runId`. */
function newFeed(runId) {
  /** @type {Feed} */
  const feed = { runId, steps: [], text: "", ended: null, doing: "", at: 0, lastId: 0, said: [] };
  return feed;
}

/** The words of a call: the tool's and what it was asked, short. */
function callWords(tool, args) {
  if (tool === "message_agent" && typeof args?.agent === "string" && args.agent.trim() !== "") {
    return clip(`Message ${args.agent.trim()}`, STEP_CHARS);
  }
  const detail = describeArguments(args);
  return clip(detail === "" ? toolWords(tool) : `${toolWords(tool)} · ${detail}`, STEP_CHARS);
}

/**
 * Read one event of a run into its feed. Returns the name of the agent a `message_agent` call sent to, when it is one,
 * so the office can walk the sender over; null otherwise. A pure function of the feed and the event.
 */
export function readEvent(feed, kind, data, ts) {
  feed.at = ts;
  switch (kind) {
    case "tool_call": {
      if (QUIET.has(data.tool)) return null;
      const words = callWords(data.tool, data.args);
      feed.steps.push({ words, ok: null, ts });
      if (feed.steps.length > STEPS_KEPT) feed.steps.shift();
      feed.doing = words;
      const target = data.tool === "message_agent" && typeof data.args?.agent === "string" ? data.args.agent.trim() : "";
      return target === "" ? null : target;
    }
    case "tool_result": {
      const open = [...feed.steps].reverse().find((step) => step.ok === null);
      if (open !== undefined) open.ok = data.ok !== false;
      return null;
    }
    case "delta":
      // As the conversation reads them (timeline.js): a thought, a reset of what was being written, or the words.
      if (data.kind === "thinking") {
        feed.doing = "Thinking";
      } else if (data.kind === "reset") {
        feed.text = "";
      } else {
        feed.text = (feed.text + (data.text ?? "")).slice(-TEXT_KEPT);
        feed.doing = "Writing";
      }
      return null;
    case "assistant":
      if (typeof data.text === "string" && data.text.trim() !== "") {
        feed.text = data.text.slice(-TEXT_KEPT);
      }
      return null;
    case "steer":
      // What the owner sent the run while it worked (item 898), for the office's chat; Dot's own notes (a reaction's,
      // a help offer's) vouch for nothing and are not the owner's words.
      if (data.vouches !== false && typeof data.text === "string" && data.text.trim() !== "") {
        feed.said.push(data.text);
        if (feed.said.length > STEPS_KEPT) feed.said.shift();
      }
      return null;
    case "run_finished":
      feed.ended = typeof data.status === "string" ? data.status : "done";
      feed.doing = "";
      if (typeof data.result === "string" && data.result.trim() !== "") {
        feed.text = data.result.slice(-TEXT_KEPT);
      }
      return null;
    default:
      return null;
  }
}

// A call that came longer ago than this is history, read to fill a feed: it moves nobody (the pre-push review: a
// message of twenty minutes ago walked its sender over when the office opened).
const LIVE_S = 20;
// The events of a run read at once to fill its feed when it is first followed.
const HISTORY_PAGE = 2000;

/**
 * The feeds of the runs the office follows, on one stream (item 889): a browser keeps six connections to a server, and
 * a stream for each working agent filled them, so the owner's message to an agent waited. `sync(runIds, working)` follows
 * those (the newest runs, up to the most followed): a run new to it has its stored events read once, then one stream
 * (`GET /runs/streams`) carries the events of those that work (in `working`) from the last one read; one that does not
 * keeps its history alone. `feed(runId)` is a run's feed, or
 * undefined; `onChange(runId, sentTo)` is called after each event, with the agent a message went to when the call is
 * live. `dispose()` lets the stream go.
 */
export function runFeeds(onChange) {
  /** @type {Map<string, Feed>} */
  const feeds = new Map();
  /** @type {Set<string>} */
  let followed = new Set();
  /** @type {Set<string>} */
  const finished = new Set();
  let stop = new AbortController();
  let generation = 0;
  let syncs = 0;
  /** @type {Set<string>} */
  let live = new Set();
  // Once let go, it starts nothing more: a history read that ends after the office closed opened a stream nobody read.
  let disposed = false;
  /** @type {Map<string, Promise<void>>} */
  const loading = new Map();

  // A run's stored events, page after page until a page comes back short (the review of 2026-11-01: a long run's
  // first 2000 alone left its panel with its first steps and no end).
  async function readHistory(runId) {
    let after = 0;
    for (;;) {
      const page = await api.get(`/runs/${encodeURIComponent(runId)}/events?after=${after}&limit=${HISTORY_PAGE}`);
      if (disposed) return;
      for (const record of page) take({ ...record, ts: 0 });
      if (page.length < HISTORY_PAGE) return;
      after = page[page.length - 1].id;
    }
  }

  function take(record) {
    if (record === undefined || record === null) return;
    const feed = feeds.get(record.run_id);
    if (feed === undefined) return;
    // Each feed reads an event once, by its id: the stream goes on from the feed that read the least, so the others
    // see some again (the pre-push review: one global id skipped what a slower history had not reached yet).
    if (typeof record.id === "number" && record.id > 0) {
      if (record.id <= feed.lastId) return;
      feed.lastId = record.id;
    }
    const sentTo = readEvent(feed, record.kind, record.data ?? {}, record.ts ?? 0);
    if (record.kind === "run_finished") finished.add(record.run_id);
    const live = typeof record.ts === "number" && Date.now() / 1000 - record.ts < LIVE_S;
    onChange(record.run_id, live ? sentTo : null);
  }

  async function restart() {
    if (disposed) return;
    stop.abort();
    stop = new AbortController();
    const mine = ++generation;
    const signal = stop.signal;
    // Only the runs that work are streamed: one that is over (its run_finished pruned, say) would end the stream at once,
    // and the stream would be made again and again (the pre-push review); its history is all there is of it.
    const open = [...followed].filter((runId) => live.has(runId) && !finished.has(runId));
    if (open.length === 0) return;
    const lastId = Math.min(...open.map((runId) => feeds.get(runId)?.lastId ?? 0));
    try {
      await follow({
        path: `/runs/streams?ids=${open.map(encodeURIComponent).join(",")}`,
        after: lastId,
        signal,
        isFinished: (message) => message.data?.kind === "run_finished" && open.every((runId) => finished.has(runId)),
        onState: () => {},
        onMessage: (message) => {
          if (mine === generation) take(message.data);
        },
      });
    } catch (error) {
      if (!signal.aborted) console.warn("the office stopped following the runs", error);
    }
  }

  return {
    async sync(runIds, working) {
      if (disposed) return;
      const wanted = new Set(runIds.slice(0, MOST_FOLLOWED));
      const alive = new Set([...wanted].filter((runId) => working.has(runId)));
      const same = (one, other) => one.size === other.size && [...one].every((runId) => other.has(runId));
      if (same(wanted, followed) && same(alive, live)) return;
      followed = wanted;
      live = alive;
      const ticket = ++syncs;
      // What each new one did before: read once, as history, before the stream goes on from the newest event read; a
      // sync while one is read waits for it too, so the stream never starts before the history it follows on from.
      for (const runId of wanted) {
        if (feeds.has(runId)) continue;
        feeds.set(runId, newFeed(runId));
        loading.set(
          runId,
          readHistory(runId)
            .catch((error) => console.warn("the office could not read a run's steps", runId, error))
            .finally(() => loading.delete(runId)),
        );
      }
      await Promise.all([...wanted].map((runId) => loading.get(runId)));
      // Keep the feeds of the runs followed and of the last ones let go, for their panels.
      for (const runId of feeds.keys()) {
        if (feeds.size <= MOST_FOLLOWED * 2) break;
        if (!followed.has(runId) && !loading.has(runId)) feeds.delete(runId);
      }
      if (ticket === syncs && !disposed) restart();
    },
    feed(runId) {
      return feeds.get(runId);
    },
    dispose() {
      disposed = true;
      generation += 1;
      stop.abort();
      feeds.clear();
    },
  };
}

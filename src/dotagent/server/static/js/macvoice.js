// What the Mac says of writing a recording down (item 626): whether it can, and when it cannot, why, in plain words. The
// page reads it in the background as it starts (`GET /api/voice`, main.js's connect) and nothing of its start waits for
// it: a slow Mac takes a while to answer (the daemon asks its developer tools once, up to three programs), and a page that
// waited would start late for every browser, a Mac's own included, which never needs the answer. The composer knows how
// its microphone listens (voice.js `listeningWay`) from the answer once it is in: a composer made before it listens the
// browser's way, and one that is not listening takes the way the answer gives at its next press (microphone.js). The
// daemon builds the program that writes down in the background meanwhile, so that the first recording does not wait for
// it. And the call that has the Mac write a recording down.

import { api } from "./api.js";
import { macLanguage } from "./recording.js";
import { base64Of } from "./wav.js";

// What the page believes until the Mac has answered: that it cannot. `known` says whether the answer is in, and `failed`,
// when the Mac could not be asked at all, why.
const NOT_KNOWN = { transcribe: false, why: null, failed: null, known: false };

/**
 * The Mac's answer and the read of it, with `get(path)` the daemon's GET (the API client's own; a test gives its own).
 * @param {(path: string) => Promise<{ transcribe: boolean, why: string | null }>} get
 */
export function createMacVoice(get) {
  let mac = NOT_KNOWN;
  const listeners = new Set();

  function keep(answer) {
    mac = answer;
    for (const listener of [...listeners]) {
      try {
        listener(mac);
      } catch (error) {
        console.error("a listener of the Mac's answer failed", error);
      }
    }
  }

  return {
    /** What the Mac said, or that it cannot while it has not answered (`known` is false). */
    macVoice: () => mac,

    /** Call `listener(answer)` each time the answer comes in; returns what stops it. */
    onMacVoice(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    /**
     * Ask the Mac whether it can write a recording down. Nothing waits for it: the page starts meanwhile, and the answer
     * goes to `macVoice` and the listeners when it comes. A Mac that could not be asked (the daemon failed, or could not be
     * reached) leaves the page as it was, with the browser's own recognition, and says so in `failed` (the Voice card tells
     * it) and in the console: the promise settles either way.
     */
    loadMacVoice() {
      return get("/voice").then(
        (answer) => keep({ transcribe: answer.transcribe, why: answer.why, failed: null, known: true }),
        (error) => {
          console.warn("the Mac could not be asked whether it writes recordings down", error);
          keep({ ...NOT_KNOWN, failed: error.message, known: true });
        },
      );
    },
  };
}

const shared = createMacVoice((path) => api.get(path));
export const macVoice = shared.macVoice;
export const onMacVoice = shared.onMacVoice;
export const loadMacVoice = shared.loadMacVoice;

/**
 * The words of `wav`, a recording, written down by the Mac in the language `tag` (the page's voice, as the Mac takes it).
 * Rejects with the daemon's error, which is words for the owner (`context.code` tells what kind), or with the one of a Mac
 * that could not be reached (`status` 0).
 * @param {string} tag
 * @param {Uint8Array} wav
 * @returns {Promise<string>}
 */
export async function writeDown(tag, wav) {
  const answer = await api.post("/voice/transcribe", { language: macLanguage(tag), data_b64: base64Of(wav) });
  return answer.text;
}

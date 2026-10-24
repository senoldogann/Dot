// What the Mac says of writing a recording down (item 626): whether it can, and when it cannot, why, in plain words. Read
// once as the page starts (`GET /api/voice`, in main.js's connect), so that the composer knows at once how its
// microphone listens (voice.js `listeningWay`); the daemon builds the program that writes down in the background meanwhile,
// so that the first recording does not wait for it. And the call that has the Mac write a recording down.

import { api } from "./api.js";
import { macLanguage } from "./recording.js";
import { base64Of } from "./wav.js";

// What the page believes until the Mac has answered: that it cannot (a composer made before the answer, the opening
// curtain's, listens the browser's own way).
const NOT_KNOWN = { transcribe: false, why: null };

let mac = NOT_KNOWN;

/** What the Mac said when the page asked, or that it cannot while it has not been asked. */
export function macVoice() {
  return mac;
}

/** Ask the Mac whether it can write a recording down. As the other things the page reads as it starts, a failure goes to
 * the page's start (main.js's connect), which says what failed: the route answers for any state of the Mac, so one that
 * fails is a daemon that is broken. */
export async function loadMacVoice() {
  mac = await api.get("/voice");
  return mac;
}

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

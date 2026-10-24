// Voice (item 574): the composer's microphone and the reading of answers aloud, both by what the browser itself offers
// (speech recognition and speech synthesis). Dot sends no voice anywhere: the browser recognizes it, and Safari and
// Chrome may send it to Apple's or Google's servers to do so (docs/safety.md). Nothing is sent by voice alone: the words
// go into the box when the owner ends the dictation (item 605), and the owner sends them as usual. An answer is read
// with a voice the browser keeps on this Mac when it has one for the language, since a network voice (Chrome's
// "Google ..." voices) sends the text it reads to a server.

// How long a dictation waits for its first word before it stops by itself, saying it heard nothing; and, once it heard
// one, how long a pause may last before it ends by itself with what it heard (item 605): long enough to think.
const SILENCE_MS = 6000;
const PAUSE_MS = 15000;
// The most characters of an answer one utterance holds: some browsers stop a long one in the middle.
const CHUNK_CHARS = 220;

// The region a language's bare tag is listened in (item 593): Chrome's recognition can hear nothing in a tag without one.
const REGIONS = { en: "en-US", tr: "tr-TR", fi: "fi-FI", sv: "sv-SE", de: "de-DE", fr: "fr-FR", es: "es-ES" };

/** The tag the voice speaks and listens in for `choice` (a language's id, or "page"): the page's language, else the
 * browser's, else English, a bare language given its region ("tr" listens as "tr-TR"). */
export function languageTag(choice, pageLang, browserLang) {
  if (choice !== "page") {
    return choice;
  }
  const tag = pageLang || browserLang || "en-US";
  return REGIONS[tag.toLowerCase()] ?? tag;
}

// ----- listening ---------------------------------------------------------------------------------------------------

/** The browser's speech recognition, or null where it has none (Firefox). Dot.app's web view has one too, WebKit's,
 * which macOS lets hear only an app that asks for the microphone and speech recognition (item 594): an older Dot.app
 * is refused (item 593). */
function recognitionClass() {
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null;
}

/** Whether the microphone button is offered. */
export function canListen() {
  return recognitionClass() !== null;
}

/**
 * What the engine has heard of a dictation, as two texts: the words it is sure of and the words it may still change. A
 * recognition keeps every result of its session in `results`, the ones it settled with `isFinal`.
 */
export function transcriptOf(results) {
  const final = [];
  const interim = [];
  for (let index = 0; index < results.length; index += 1) {
    const said = String(results[index][0]?.transcript ?? "").trim();
    if (said !== "") {
      (results[index].isFinal ? final : interim).push(said);
    }
  }
  return { final: final.join(" "), interim: interim.join(" ") };
}

/**
 * The box after `final` and `interim` were heard on top of `base`, the text it held when the dictation began, and where in
 * it the words that may still change begin (null when none do): they are drawn quieter until they are settled.
 */
export function dictated(base, final, interim) {
  const spoken = [final, interim].filter((part) => part !== "").join(" ");
  if (spoken === "") {
    return { value: base, interimFrom: null };
  }
  const gap = base === "" || /\s$/.test(base) ? "" : " ";
  const value = `${base}${gap}${spoken}`;
  return { value, interimFrom: interim === "" ? null : value.length - interim.length };
}

// The way to speak while the microphone cannot: macOS's own dictation types into any box, Dot's in its app too, and a
// phone's keyboard has a microphone of its own.
const DICTATION = {
  app: "macOS Dictation types into the box meanwhile: the Dictation key, or Fn twice.",
  mac: "macOS Dictation types into the box meanwhile: the Dictation key, or Fn twice.",
  other: "The keyboard's own microphone types into the box meanwhile.",
};

/**
 * Where the page is, for what the microphone's words name (item 593): `app`, Dot.app, where macOS gives the microphone;
 * `mac`, a browser on a Mac; `other`, a phone's browser or any other, which has no System Settings nor Fn key. An iPad
 * that asks for the desktop's pages says it is a Mac with a touch screen (as boot.js reads it): it is `other`.
 */
export function voicePlace(shell, userAgent, touchPoints) {
  if (shell === "desktop") {
    return "app";
  }
  return /Macintosh/.test(userAgent) && touchPoints <= 1 ? "mac" : "other";
}

/**
 * What to tell the owner of the recognition's error `code`, or null for none (the owner stopped it), in the words of
 * `place` (`voicePlace`): in Dot.app macOS, not a browser, gives the microphone (item 593), and only a Mac has System
 * Settings. "no-speech" is also what a dictation that ended by itself without a word says: nothing was heard, which a
 * microphone kept from the app or the browser looks like.
 */
export function problemWords(code, place) {
  const app = place === "app";
  const who = app ? "Dot" : "the browser";
  const onMac = place !== "other";
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      if (app) {
        return `macOS has not given Dot the microphone or its speech recognition. Allow Dot in System Settings › Privacy & Security › Microphone and Speech Recognition; a Dot that is not listed there is an older Dot.app, which cannot ask for them. ${DICTATION.app}`;
      }
      return onMac
        ? `The browser has not allowed the microphone, or its speech recognition, on this page. Allow it for this address in the browser, and the browser in System Settings › Privacy & Security › Microphone. ${DICTATION.mac}`
        : `The browser has not allowed the microphone, or its speech recognition, on this page. Allow it for this address in the browser's settings, and the browser in the device's settings. ${DICTATION.other}`;
    case "no-speech":
      return onMac
        ? `Nothing was heard. Check that ${who} may use the microphone in System Settings › Privacy & Security › Microphone, the input in System Settings › Sound, and the language in Settings › General › Voice.`
        : "Nothing was heard. Check that the browser may use the microphone, in its settings and the device's, and the language in Settings › General › Voice.";
    case "audio-capture":
      return onMac ? "No microphone was found: check the input in System Settings › Sound." : "No microphone was found.";
    case "network":
      return `The speech recognition ${app ? "macOS" : "the browser"} uses could not be reached: it needs the network.`;
    case "language-not-supported":
      return `${app ? "macOS" : "The browser"} does not recognize that language: choose another in Settings › General › Voice.`;
    case "aborted":
      return null;
    default:
      return `Speech recognition stopped: ${code}.`;
  }
}

/**
 * The dictation of one box. `language()` gives the tag to listen in and `text()` the words the box holds; `show(value,
 * interimFrom)` puts what was heard on top of them in the box, and `settled()` says that the quieter words are no longer
 * the engine's to change; `state(listening)` says whether the microphone is on, and `problem(words)` why it could not be.
 * `start()`, `stop()` (the engine settles what it has) and `abort()` (it drops what it has) are the owner's; one that is
 * not touched stops by itself after `SILENCE_MS` from the moment the engine began to listen with no word heard, after
 * `PAUSE_MS` of silence once one was, and with the page put out of sight. `began()` says the engine began to listen, and
 * `sound(loud)` whether it hears a sound now, for the recorder's clock and wave (item 605); both may be left out.
 */
export function createDictation({ language, text, show, settled, state, problem, began = () => {}, sound = (/** @type {boolean} */ _loud) => {} }) {
  let recognition = null;
  let base = "";
  let timer = null;
  // Whether this dictation heard a word, and whether the owner ended it: one that ends by itself having heard nothing
  // says so (item 593), where it ended without a word before.
  let heard = false;
  let byOwner = false;
  // Whether the engine began to listen (its `start`). The first time, a browser asks for the microphone and its speech
  // recognition before it does, and an iPhone asks twice, for as long as the owner reads: the silence is counted from
  // then, never through the questions, and an engine is not stopped before it began (item 603). One stopped while
  // Safari asked ended at once, saying it heard nothing, and Safari, once allowed, started it all the same, for nobody,
  // with the microphone on.
  let started = false;
  const place = () => voicePlace(document.documentElement.dataset.shell, navigator.userAgent, navigator.maxTouchPoints);

  function arm() {
    clearTimeout(timer);
    timer = setTimeout(() => recognition?.stop(), heard ? PAUSE_MS : SILENCE_MS);
  }

  /** The dictation is over; `words` say why to the owner, or null. */
  function finish(words) {
    clearTimeout(timer);
    if (recognition !== null) {
      recognition = null;
      state(false);
      settled();
      if (words !== null) {
        problem(words);
      }
    }
  }

  function start() {
    const Recognition = recognitionClass();
    if (recognition !== null || Recognition === null) {
      return;
    }
    const engine = new Recognition();
    engine.lang = language();
    engine.continuous = true;
    engine.interimResults = true;
    engine.maxAlternatives = 1;
    base = text();
    heard = false;
    byOwner = false;
    started = false;
    // An engine this dictation no longer holds that begins or hears anyway (Safari starts one once the owner allows it,
    // whatever was asked of it meanwhile) is dropped, so that the microphone is not left on for nobody.
    const left = () => recognition !== engine;
    // The engine says it began with `start`, and with `audiostart` as the microphone opens; a word heard says it too.
    const begin = () => {
      if (left()) {
        engine.abort();
        return;
      }
      if (!started) {
        started = true;
        began();
      }
      arm();
    };
    engine.addEventListener("start", begin);
    engine.addEventListener("audiostart", begin);
    // A sound, or speech, heard or ended: what the recorder's wave draws.
    for (const type of ["soundstart", "speechstart", "soundend", "speechend"]) {
      engine.addEventListener(type, () => {
        if (!left()) {
          sound(type.endsWith("start"));
        }
      });
    }
    /** @param {SpeechRecognitionEventLike} event */
    const heardSomething = (event) => {
      if (left()) {
        engine.abort();
        return;
      }
      if (!started) {
        started = true;
        began();
      }
      sound(true);
      const said = transcriptOf(event.results);
      heard = heard || said.final !== "" || said.interim !== "";
      const next = dictated(base, said.final, said.interim);
      show(next.value, next.interimFrom);
      arm();
    };
    /** @param {SpeechRecognitionErrorEventLike} event */
    const failed = (event) => {
      if (recognition !== engine) {
        return;
      }
      // The owner's own stop can come before the engine's word that it heard nothing: that silence says nothing.
      finish(byOwner && event.error === "no-speech" ? null : problemWords(event.error, place()));
    };
    engine.addEventListener("result", heardSomething);
    engine.addEventListener("error", failed);
    engine.addEventListener("end", () => {
      if (recognition === engine) {
        // One that never began heard nothing because it never listened: its error, if any, said why.
        finish(heard || byOwner || !started ? null : problemWords("no-speech", place()));
      }
    });
    recognition = engine;
    try {
      engine.start();
    } catch (error) {
      recognition = null;
      problem(`The microphone could not start: ${error.message}`);
      return;
    }
    // The button shows it on at once; the silence is counted from the engine's `start`.
    state(true);
  }

  function stop() {
    if (recognition === null) {
      return;
    }
    if (!started) {
      // Before the engine began (the browser still asks), there is nothing to settle: it is dropped.
      abort();
      return;
    }
    byOwner = true;
    clearTimeout(timer);
    recognition.stop();
    state(false);
  }

  function abort() {
    const engine = recognition;
    if (engine !== null) {
      finish(null);
      engine.abort();
    }
  }

  function hidden() {
    if (document.visibilityState === "hidden") {
      abort();
    }
  }
  document.addEventListener("visibilitychange", hidden);

  return {
    listening: () => recognition !== null,
    start,
    stop,
    abort,
    toggle: () => (recognition === null ? start() : stop()),
    dispose() {
      abort();
      document.removeEventListener("visibilitychange", hidden);
    },
  };
}

// ----- the recorder --------------------------------------------------------------------------------------------------

/** The recorder's clock for `ms` since the engine began: minutes and seconds, as "0:05" and "12:40". */
export function clockText(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

// How tall a bar of the wave is in a silence: a dot on the line.
export const QUIET_BAR = 0.12;

/**
 * The recorder's wave a moment later: the bars move one place to the left and a new one comes in at the right, tall while
 * the engine hears a sound (`loud`) and a dot in a silence. `random()` gives a number from 0 to 1, as Math.random does.
 */
export function nextWave(bars, loud, random) {
  return [...bars.slice(1), loud ? 0.3 + 0.7 * random() : QUIET_BAR];
}

// ----- speaking ----------------------------------------------------------------------------------------------------

/** Whether the browser can speak: the buttons that read an answer are offered only where it can. */
export function canSpeak() {
  return "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

/**
 * Ask the browser for its voices: Chrome lists them only after the first request (and says so with `voiceschanged`), so
 * the request is made as the page loads, not when the first answer is read.
 */
export function primeVoices() {
  if (canSpeak()) {
    window.speechSynthesis.getVoices();
  }
}

/** A language tag as the browsers write it, for comparing: lower case with a "-" (some write "tr_TR"). */
function sameTag(tag) {
  return tag.toLowerCase().replace("_", "-");
}

/**
 * The voice to read in `lang`: one the browser keeps on this Mac (`localService`), so that the words of an answer are not
 * sent to a server to be spoken, as a network voice does. The voice of the language and region asked for comes first,
 * then any voice of the language; null when the browser has no local one, and it then chooses by the language alone.
 * @param {string} lang
 * @param {readonly SpeechSynthesisVoice[]} voices
 * @returns {SpeechSynthesisVoice | null}
 */
export function localVoice(lang, voices) {
  const wanted = sameTag(lang);
  const language = wanted.split("-")[0];
  const local = voices.filter((voice) => voice.localService);
  return (
    local.find((voice) => sameTag(voice.lang) === wanted) ??
    local.find((voice) => sameTag(voice.lang).split("-")[0] === language) ??
    null
  );
}

/** A piece of speech for `words` in `lang`, with a local voice when the browser has one. */
function utteranceFor(words, lang) {
  const utterance = new SpeechSynthesisUtterance(words);
  utterance.lang = lang;
  const voice = localVoice(lang, window.speechSynthesis.getVoices());
  if (voice !== null) {
    utterance.voice = voice;
  }
  return utterance;
}

/**
 * An answer's Markdown as the words to say: its code left out (it is not read out), its links and images as their words,
 * an address as "link", its marks gone, a table's cells one after the other, and a full stop where a line ends without one,
 * so that a heading and the line under it are not run together.
 */
export function spokenText(markdown) {
  const plain = markdown
    .replace(/(```|~~~)[\s\S]*?(?:\1|$)/g, "\n")
    .replace(/!?\[([^\]\n]*)\]\([^)\s]*\)/g, "$1")
    .replace(/https?:\/\/[^\s)]+/g, "link")
    .replace(/^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$/gm, "")
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, "")
    .replace(/^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+/gm, "")
    .replace(/^[ \t]*>[ \t]?/gm, "")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(^|[\s(])([*_])(?=\S)([^*_\n]+?)(?<=\S)\2(?=[\s).,!?:;]|$)/gm, "$1$3")
    .replace(/[ \t]*\|[ \t]*/g, ", ");
  return plain
    .split("\n")
    .map((line) => line.trim().replace(/^,\s*|,\s*$/g, ""))
    .filter((line) => line !== "")
    .map((line) => (/[.!?:;,…]$/.test(line) ? line : `${line}.`))
    .join(" ");
}

/** `text` in pieces of at most `size` characters, each ending where a sentence does when it can, else at a space. */
export function sentenceChunks(text, size = CHUNK_CHARS) {
  const chunks = [];
  let current = "";
  const push = (piece) => {
    if (piece !== "") {
      chunks.push(piece);
    }
  };
  for (const sentence of text.match(/[^.!?…]+(?:[.!?…]+["')\]]*)?\s*/g) ?? []) {
    let rest = sentence;
    while (rest.length > size) {
      let cut = rest.lastIndexOf(" ", size);
      if (cut <= 0) {
        cut = size;
      }
      push(current.trim());
      current = "";
      push(rest.slice(0, cut).trim());
      rest = rest.slice(cut);
    }
    if (current.length + rest.length > size) {
      push(current.trim());
      current = "";
    }
    current += rest;
  }
  push(current.trim());
  return chunks;
}

// The answer being read now: `stop()` ends it.
let reading = null;

/** End the reading of the answer being read, if one is. */
export function stopReading() {
  reading?.stop();
}

/**
 * Read `markdown` aloud in the language `lang`, in the pieces of `sentenceChunks`, after ending any other reading;
 * `onEnd()` runs once it has been read or stopped, and `problem(words)` when the browser would not read it. Returns what
 * stops it, or null when there was nothing to say.
 */
export function readAloud(markdown, lang, onEnd, problem) {
  stopReading();
  const chunks = sentenceChunks(spokenText(markdown));
  if (chunks.length === 0) {
    onEnd();
    return null;
  }
  const synth = window.speechSynthesis;
  let ended = false;
  let told = false;
  const handle = {
    stop() {
      synth.cancel();
      finish();
    },
  };
  function finish() {
    if (!ended) {
      ended = true;
      if (reading === handle) {
        reading = null;
      }
      onEnd();
    }
  }
  reading = handle;
  chunks.forEach((chunk, index) => {
    const utterance = utteranceFor(chunk, lang);
    // The owner's stop cancels the queue, which fails each piece with "canceled" or "interrupted"; any other failure ends the
    // reading, so that the pieces after it are not read on their own.
    utterance.addEventListener("error", (event) => {
      if (event.error === "canceled" || event.error === "interrupted") {
        finish();
        return;
      }
      if (!told) {
        told = true;
        problem(
          event.error === "not-allowed"
            ? "The browser keeps its voice quiet until you touch the page: press the speaker on the answer."
            : `The browser could not read the answer aloud: ${event.error}.`,
        );
      }
      handle.stop();
    });
    if (index === chunks.length - 1) {
      utterance.addEventListener("end", finish);
    }
    synth.speak(utterance);
  });
  return handle;
}

/** Say `words` in `lang` at once, for the owner to hear that the voice works; it also lets a browser that speaks only after a touch speak later. */
export function sayOnce(words, lang) {
  stopReading();
  window.speechSynthesis.speak(utteranceFor(words, lang));
}

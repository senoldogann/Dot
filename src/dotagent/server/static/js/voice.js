// Voice (item 574): the composer's microphone and the reading of answers aloud, both by what the browser itself offers
// (speech recognition and speech synthesis). Dot sends no voice anywhere: the browser recognizes it, and Safari and
// Chrome may send it to Apple's or Google's servers to do so (docs/safety.md). Nothing is sent by voice alone: the words
// fill the box, and the owner sends them as usual. An answer is read with a voice the browser keeps on this Mac when it
// has one for the language, since a network voice (Chrome's "Google ..." voices) sends the text it reads to a server.

// How long a dictation waits for a word before it stops by itself.
const SILENCE_MS = 6000;
// The most characters of an answer one utterance holds: some browsers stop a long one in the middle.
const CHUNK_CHARS = 220;

/** The tag the voice speaks and listens in for `choice` (a language's id, or "page"): the page's language, else the browser's, else English. */
export function languageTag(choice, pageLang, browserLang) {
  if (choice !== "page") {
    return choice;
  }
  return pageLang || browserLang || "en-US";
}

// ----- listening ---------------------------------------------------------------------------------------------------

/** The browser's speech recognition, or null where it has none (Firefox, the desktop shell). */
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

// What an error of the recognition says to the owner; null for one that is not worth a word (nothing was said, or the
// owner stopped it).
const PROBLEMS = {
  "not-allowed": "The browser has not allowed the microphone, or its speech recognition, on this page. Allow it in the browser's settings for this address.",
  "service-not-allowed": "The browser has not allowed the microphone, or its speech recognition, on this page. Allow it in the browser's settings for this address.",
  "audio-capture": "No microphone was found.",
  network: "The browser's speech recognition could not be reached.",
  "language-not-supported": "The browser does not recognize that language: choose another in Settings › General › Voice.",
  "no-speech": null,
  aborted: null,
};

/** What to tell the owner of the recognition's error `code`, or null for none. */
export function problemWords(code) {
  return code in PROBLEMS ? PROBLEMS[code] : `Speech recognition stopped: ${code}.`;
}

/**
 * The dictation of one box. `language()` gives the tag to listen in and `text()` the words the box holds; `show(value,
 * interimFrom)` puts what was heard on top of them in the box, and `settled()` says that the quieter words are no longer
 * the engine's to change; `state(listening)` says whether the microphone is on, and `problem(words)` why it could not be.
 * `start()`, `stop()` (the engine settles what it has) and `abort()` (it drops what it has) are the owner's; one that is
 * not touched stops by itself after `SILENCE_MS`, and with the page put out of sight.
 */
export function createDictation({ language, text, show, settled, state, problem }) {
  let recognition = null;
  let base = "";
  let timer = null;

  function arm() {
    clearTimeout(timer);
    timer = setTimeout(() => recognition?.stop(), SILENCE_MS);
  }

  function finish() {
    clearTimeout(timer);
    if (recognition !== null) {
      recognition = null;
      state(false);
      settled();
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
    /** @param {SpeechRecognitionEventLike} event */
    const heardSomething = (event) => {
      if (recognition !== engine) {
        return;
      }
      const heard = transcriptOf(event.results);
      const next = dictated(base, heard.final, heard.interim);
      show(next.value, next.interimFrom);
      arm();
    };
    /** @param {SpeechRecognitionErrorEventLike} event */
    const failed = (event) => {
      if (recognition !== engine) {
        return;
      }
      const words = problemWords(event.error);
      if (words !== null) {
        problem(words);
      }
      finish();
    };
    engine.addEventListener("result", heardSomething);
    engine.addEventListener("error", failed);
    engine.addEventListener("end", () => {
      if (recognition === engine) {
        finish();
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
    state(true);
    arm();
  }

  function stop() {
    if (recognition !== null) {
      clearTimeout(timer);
      recognition.stop();
      state(false);
    }
  }

  function abort() {
    const engine = recognition;
    if (engine !== null) {
      finish();
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

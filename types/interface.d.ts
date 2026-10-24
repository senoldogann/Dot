// What the interface reads off the window that TypeScript's own DOM library does not know: the desktop shell's bridge,
// the hook the shell calls, Safari's word for a page opened from the home screen, the browser's speech recognition
// (item 574), Safari's prefixed Web Audio, and what the audio worklet of the recording sees (item 626). Checked by
// `npm run check` (tsconfig.json); never loaded by a browser.

interface TauriNotificationBridge {
  isPermissionGranted(): Promise<boolean>;
  requestPermission(): Promise<"granted" | "denied" | "default">;
  sendNotification(options: { title: string; body: string }): void | Promise<void>;
}

/** The part of the browser's speech recognition that the composer's microphone uses (item 574); TypeScript's own DOM library has none of it. */
interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: { transcript: string; confidence: number };
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: { readonly length: number; [index: number]: SpeechRecognitionResultLike };
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
  readonly message: string;
}

interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
}

/** What the global scope of an audio worklet has (voicecapture.js, item 626), which TypeScript's DOM library does not know. */
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  process(inputs: Float32Array[][], outputs: Float32Array[][], parameters: Record<string, Float32Array>): boolean;
}
declare function registerProcessor(name: string, processor: new () => AudioWorkletProcessor): void;

interface Window {
  /** Safari's Web Audio before it dropped the prefix (14.1). */
  webkitAudioContext?: typeof AudioContext;
  /** Tauri's bridge, present only inside the desktop shell (`withGlobalTauri`); its notification part is what the page uses. */
  __TAURI__?: { notification?: TauriNotificationBridge } & Record<string, unknown>;
  /** Set by main.js: the desktop shell's New chat calls it once it moved the page to the home. */
  focusComposer?: () => void;
  /** The browser's speech recognition: the standard name (Chrome, Edge) and Safari's. A browser with neither has no microphone button. */
  SpeechRecognition?: new () => SpeechRecognitionLike;
  webkitSpeechRecognition?: new () => SpeechRecognitionLike;
}

interface Navigator {
  /** Safari on iOS: whether the page runs as an app added to the home screen. */
  standalone?: boolean;
}

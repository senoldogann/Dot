// What the interface reads off the window that TypeScript's own DOM library does not know: the desktop shell's bridge,
// the hook the shell calls, and Safari's word for a page opened from the home screen. Checked by `npm run check`
// (tsconfig.json); never loaded by a browser.

interface TauriNotificationBridge {
  isPermissionGranted(): Promise<boolean>;
  requestPermission(): Promise<"granted" | "denied" | "default">;
  sendNotification(options: { title: string; body: string }): void | Promise<void>;
}

interface Window {
  /** Tauri's bridge, present only inside the desktop shell (`withGlobalTauri`); its notification part is what the page uses. */
  __TAURI__?: { notification?: TauriNotificationBridge } & Record<string, unknown>;
  /** Set by main.js: the desktop shell's New chat calls it once it moved the page to the home. */
  focusComposer?: () => void;
}

interface Navigator {
  /** Safari on iOS: whether the page runs as an app added to the home screen. */
  standalone?: boolean;
}

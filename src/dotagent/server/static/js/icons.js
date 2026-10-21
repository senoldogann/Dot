// Inline SVG icons, built as DOM nodes, so that their stroke takes the text's colour (.icon in app.css). 24x24 grid,
// stroke only. The shapes are Lucide's, under its ISC licence (Feather's MIT licence for those Lucide took from
// Feather): LICENSE-Lucide.txt.

import { svg } from "./dom.js";

const SHAPES = {
  paperclip: [["path", { d: "m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" }]],
  check: [["path", { d: "M20 6 9 17l-5-5" }]],
  x: [["path", { d: "M18 6 6 18" }], ["path", { d: "m6 6 12 12" }]],
  // For the queue of a chat (item 148): a handle to drag, a numbered list, a turn up and on.
  grip: [
    ["circle", { cx: "9", cy: "6", r: "1.2" }],
    ["circle", { cx: "9", cy: "12", r: "1.2" }],
    ["circle", { cx: "9", cy: "18", r: "1.2" }],
    ["circle", { cx: "15", cy: "6", r: "1.2" }],
    ["circle", { cx: "15", cy: "12", r: "1.2" }],
    ["circle", { cx: "15", cy: "18", r: "1.2" }],
  ],
  listOrdered: [
    ["path", { d: "M10 6h10" }],
    ["path", { d: "M10 12h10" }],
    ["path", { d: "M10 18h10" }],
    ["path", { d: "M4 5h1.5v4" }],
    ["path", { d: "M4 15h3l-3 4h3" }],
  ],
  cornerUpRight: [["path", { d: "m15 5 4 4-4 4" }], ["path", { d: "M5 19v-6a4 4 0 0 1 4-4h10" }]],
  bolt: [
    [
      "path",
      {
        d: "M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z",
      },
    ],
  ],
  target: [["circle", { cx: 12, cy: 12, r: 10 }], ["circle", { cx: 12, cy: 12, r: 6 }], ["circle", { cx: 12, cy: 12, r: 2 }]],
  chevron: [["path", { d: "m9 18 6-6-6-6" }]],
  // The way back to the chats on a phone (item 295).
  chevronLeft: [["path", { d: "m15 18-6-6 6-6" }]],
  alert: [
    ["path", { d: "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" }],
    ["path", { d: "M12 9v4" }],
    ["path", { d: "M12 17h.01" }],
  ],
  stop: [["rect", { width: 14, height: 14, x: 5, y: 5, rx: 2 }]],
  clock: [["circle", { cx: 12, cy: 12, r: 10 }], ["path", { d: "M12 6v6l4 2" }]],
  // A schedule's Run now (item 455), and an agent's Pause and Resume (item 465).
  play: [["path", { d: "M6 3l14 9-14 9z" }]],
  pause: [
    ["rect", { x: 14, y: 4, width: 4, height: 16, rx: 1 }],
    ["rect", { x: 6, y: 4, width: 4, height: 16, rx: 1 }],
  ],
  circleDashed: [
    ["path", { d: "M10.1 2.182a10 10 0 0 1 3.8 0" }],
    ["path", { d: "M13.9 21.818a10 10 0 0 1-3.8 0" }],
    ["path", { d: "M17.609 3.721a10 10 0 0 1 2.69 2.7" }],
    ["path", { d: "M2.182 13.9a10 10 0 0 1 0-3.8" }],
    ["path", { d: "M20.279 17.609a10 10 0 0 1-2.7 2.69" }],
    ["path", { d: "M21.818 10.1a10 10 0 0 1 0 3.8" }],
    ["path", { d: "M3.721 6.391a10 10 0 0 1 2.7-2.69" }],
    ["path", { d: "M6.391 20.279a10 10 0 0 1-2.69-2.7" }],
  ],
  terminal: [["polyline", { points: "4 17 10 11 4 5" }], ["line", { x1: 12, x2: 20, y1: 19, y2: 19 }]],
  globe: [
    ["circle", { cx: 12, cy: 12, r: 10 }],
    ["path", { d: "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" }],
    ["path", { d: "M2 12h20" }],
  ],
  smartphone: [
    ["rect", { width: 14, height: 20, x: 5, y: 2, rx: 2, ry: 2 }],
    ["path", { d: "M12 18h.01" }],
  ],
  monitor: [
    ["rect", { width: 20, height: 14, x: 2, y: 3, rx: 2 }],
    ["line", { x1: 8, x2: 16, y1: 21, y2: 21 }],
    ["line", { x1: 12, x2: 12, y1: 17, y2: 21 }],
  ],
  // For the Changes of a chat (item 368): Lucide's file-diff, a file with a plus over a minus.
  fileDiff: [
    ["path", { d: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" }],
    ["path", { d: "M9 10h6" }],
    ["path", { d: "M12 13V7" }],
    ["path", { d: "M9 17h6" }],
  ],
  file: [
    ["path", { d: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" }],
    ["path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }],
    ["path", { d: "M10 9H8" }],
    ["path", { d: "M16 13H8" }],
    ["path", { d: "M16 17H8" }],
  ],
  search: [["circle", { cx: 11, cy: 11, r: 8 }], ["path", { d: "m21 21-4.3-4.3" }]],
  star: [["polygon", { points: "12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" }]],
  trash: [
    ["path", { d: "M3 6h18" }],
    ["path", { d: "M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" }],
    ["path", { d: "M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" }],
  ],
  plus: [["path", { d: "M5 12h14" }], ["path", { d: "M12 5v14" }]],
  download: [["path", { d: "M12 15V3" }], ["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }], ["path", { d: "m7 10 5 5 5-5" }]],
  upload: [["path", { d: "M12 3v12" }], ["path", { d: "m17 8-5-5-5 5" }], ["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }]],
  ellipsis: [["circle", { cx: 12, cy: 12, r: 1 }], ["circle", { cx: 19, cy: 12, r: 1 }], ["circle", { cx: 5, cy: 12, r: 1 }]],
  pin: [
    ["path", { d: "M12 17v5" }],
    [
      "path",
      {
        d: "M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z",
      },
    ],
  ],
  pinOff: [
    ["path", { d: "M12 17v5" }],
    ["path", { d: "M15 9.34V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H7.89" }],
    ["path", { d: "m2 2 20 20" }],
    ["path", { d: "M9 9v1.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h11" }],
  ],
  plug: [
    ["path", { d: "M12 22v-5" }],
    ["path", { d: "M9 8V2" }],
    ["path", { d: "M15 8V2" }],
    ["path", { d: "M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" }],
  ],
  cloud: [["path", { d: "M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" }]],
  link: [
    ["path", { d: "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" }],
    ["path", { d: "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" }],
  ],
  squareCheck: [["rect", { width: 18, height: 18, x: 3, y: 3, rx: 2 }], ["path", { d: "m9 12 2 2 4-4" }]],
  refresh: [
    ["path", { d: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" }],
    ["path", { d: "M21 3v5h-5" }],
    ["path", { d: "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" }],
    ["path", { d: "M8 16H3v5" }],
  ],
  shield: [
    [
      "path",
      {
        d: "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z",
      },
    ],
  ],
  list: [
    ["line", { x1: 8, x2: 21, y1: 6, y2: 6 }],
    ["line", { x1: 8, x2: 21, y1: 12, y2: 12 }],
    ["line", { x1: 8, x2: 21, y1: 18, y2: 18 }],
    ["line", { x1: 3, x2: 3.01, y1: 6, y2: 6 }],
    ["line", { x1: 3, x2: 3.01, y1: 12, y2: 12 }],
    ["line", { x1: 3, x2: 3.01, y1: 18, y2: 18 }],
  ],
  user: [["path", { d: "M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" }], ["circle", { cx: 12, cy: 7, r: 4 }]],
  // For the side panel's tabs (item 227): the run's details, and the agents of a chat.
  info: [["circle", { cx: 12, cy: 12, r: 10 }], ["path", { d: "M12 16v-4" }], ["path", { d: "M12 8h.01" }]],
  users: [
    ["path", { d: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" }],
    ["circle", { cx: 9, cy: 7, r: 4 }],
    ["path", { d: "M22 21v-2a4 4 0 0 0-3-3.87" }],
    ["path", { d: "M16 3.13a4 4 0 0 1 0 7.75" }],
  ],
  copy: [
    ["rect", { width: 14, height: 14, x: 8, y: 8, rx: 2, ry: 2 }],
    ["path", { d: "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" }],
  ],
  image: [
    ["rect", { width: 18, height: 18, x: 3, y: 3, rx: 2, ry: 2 }],
    ["circle", { cx: 9, cy: 9, r: 2 }],
    ["path", { d: "m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21" }],
  ],
  arrowDown: [["path", { d: "M12 5v14" }], ["path", { d: "m19 12-7 7-7-7" }]],
  arrowUp: [["path", { d: "m5 12 7-7 7 7" }], ["path", { d: "M12 19V5" }]],
  arrowLeft: [["path", { d: "m12 19-7-7 7-7" }], ["path", { d: "M19 12H5" }]],
  undo: [["path", { d: "M9 14 4 9l5-5" }], ["path", { d: "M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11" }]],
  arrowRight: [["path", { d: "M5 12h14" }], ["path", { d: "m12 5 7 7-7 7" }]],
  calendar: [
    ["path", { d: "M8 2v4" }],
    ["path", { d: "M16 2v4" }],
    ["rect", { width: 18, height: 18, x: 3, y: 4, rx: 2 }],
    ["path", { d: "M3 10h18" }],
  ],
  database: [
    ["ellipse", { cx: 12, cy: 5, rx: 9, ry: 3 }],
    ["path", { d: "M3 5V19A9 3 0 0 0 21 19V5" }],
    ["path", { d: "M3 12A9 3 0 0 0 21 12" }],
  ],
  help: [
    ["circle", { cx: 12, cy: 12, r: 10 }],
    ["path", { d: "M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" }],
    ["path", { d: "M12 17h.01" }],
  ],
  hand: [
    ["path", { d: "M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2" }],
    ["path", { d: "M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2" }],
    ["path", { d: "M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8" }],
    [
      "path",
      {
        d: "M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15",
      },
    ],
  ],
  branch: [
    ["line", { x1: 6, x2: 6, y1: 3, y2: 15 }],
    ["circle", { cx: 18, cy: 6, r: 3 }],
    ["circle", { cx: 6, cy: 18, r: 3 }],
    ["path", { d: "M18 9a9 9 0 0 1-9 9" }],
  ],
  sparkle: [
    [
      "path",
      {
        d: "M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z",
      },
    ],
  ],
  sparkles: [
    [
      "path",
      {
        d: "m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275Z",
      },
    ],
    ["path", { d: "M5 3v4" }],
    ["path", { d: "M19 17v4" }],
    ["path", { d: "M3 5h4" }],
    ["path", { d: "M17 19h4" }],
  ],
  wrench: [
    [
      "path",
      {
        d: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z",
      },
    ],
  ],
  puzzle: [
    [
      "path",
      {
        d: "M19.439 7.85c-.049-.322.059-.648.289-.878l1.568-1.568a1.5 1.5 0 0 0-2.121-2.122l-1.568 1.568a1 1 0 0 1-.878.29c-.322-.049-.636-.214-.853-.483C15.228 3.86 14.28 3 13 3s-2.228.86-2.876 1.657c-.217.269-.531.434-.853.483a1 1 0 0 1-.878-.29L6.825 3.282a1.5 1.5 0 0 0-2.121 2.122l1.568 1.568c.23.23.338.556.289.878-.049.322-.214.636-.483.853C5.14 9.352 4 10.5 4 12s1.14 2.648 2.078 3.3c.269.217.434.531.483.853.049.322-.059.648-.289.878l-1.568 1.568a1.5 1.5 0 0 0 2.121 2.122l1.568-1.568a1 1 0 0 1 .878-.29c.322.049.636.214.853.483C10.772 20.14 11.72 21 13 21s2.228-.86 2.876-1.657c.217-.269.531-.434.853-.483a1 1 0 0 1 .878.29l1.568 1.568a1.5 1.5 0 0 0 2.121-2.122l-1.568-1.568a1 1 0 0 1-.29-.878c.049-.322.214-.636.483-.853C20.86 14.648 22 13.5 22 12s-1.14-2.648-2.078-3.3c-.269-.217-.434-.531-.483-.853",
      },
    ],
  ],
  command: [
    [
      "path",
      {
        d: "M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3",
      },
    ],
  ],
  power: [["path", { d: "M12 2v10" }], ["path", { d: "M18.4 6.6a9 9 0 1 1-12.77.04" }]],
  pointer: [
    ["path", { d: "M12.586 12.586 19 19" }],
    [
      "path",
      {
        d: "M3.688 3.037a.497.497 0 0 0-.651.651l6.5 15.999a.501.501 0 0 0 .947-.062l1.569-6.083a2 2 0 0 1 1.448-1.479l6.124-1.579a.5.5 0 0 0 .063-.947z",
      },
    ],
  ],
  sliders: [
    ["line", { x1: 4, x2: 4, y1: 21, y2: 14 }],
    ["line", { x1: 4, x2: 4, y1: 10, y2: 3 }],
    ["line", { x1: 12, x2: 12, y1: 21, y2: 12 }],
    ["line", { x1: 12, x2: 12, y1: 8, y2: 3 }],
    ["line", { x1: 20, x2: 20, y1: 21, y2: 16 }],
    ["line", { x1: 20, x2: 20, y1: 12, y2: 3 }],
    ["line", { x1: 2, x2: 6, y1: 14, y2: 14 }],
    ["line", { x1: 10, x2: 14, y1: 8, y2: 8 }],
    ["line", { x1: 18, x2: 22, y1: 16, y2: 16 }],
  ],
  // The settings' cog (item 492), which the foot of the sidebar wears in place of the word.
  settings: [
    [
      "path",
      {
        d: "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z",
      },
    ],
    ["circle", { cx: "12", cy: "12", r: "3" }],
  ],
  bell: [
    ["path", { d: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" }],
    ["path", { d: "M10.3 21a1.94 1.94 0 0 0 3.4 0" }],
  ],
  pencil: [
    ["path", { d: "M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" }],
  ],
  // A new chat on a phone's list of chats (item 294).
  squarePen: [
    ["path", { d: "M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" }],
    ["path", { d: "M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" }],
  ],
  layers: [
    ["path", { d: "m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" }],
    ["path", { d: "m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" }],
    ["path", { d: "m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" }],
  ],
  panelLeft: [["rect", { width: 18, height: 18, x: 3, y: 3, rx: 2 }], ["path", { d: "M9 3v18" }]],
  panelRight: [["rect", { width: 18, height: 18, x: 3, y: 3, rx: 2 }], ["path", { d: "M15 3v18" }]],
  columns: [["rect", { width: 18, height: 18, x: 3, y: 3, rx: 2 }], ["path", { d: "M12 3v18" }]],
  maximize: [
    ["polyline", { points: "15 3 21 3 21 9" }],
    ["polyline", { points: "9 21 3 21 3 15" }],
    ["line", { x1: 21, x2: 14, y1: 3, y2: 10 }],
    ["line", { x1: 3, x2: 10, y1: 21, y2: 14 }],
  ],
  minimize: [
    ["polyline", { points: "4 14 10 14 10 20" }],
    ["polyline", { points: "20 10 14 10 14 4" }],
    ["line", { x1: 14, x2: 21, y1: 10, y2: 3 }],
    ["line", { x1: 3, x2: 10, y1: 21, y2: 14 }],
  ],
  key: [
    ["path", { d: "m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4" }],
    ["path", { d: "m21 2-9.6 9.6" }],
    ["circle", { cx: 7.5, cy: 15.5, r: 5.5 }],
  ],
  eye: [
    ["path", { d: "M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" }],
    ["circle", { cx: 12, cy: 12, r: 3 }],
  ],
  eyeOff: [
    ["path", { d: "M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" }],
    ["path", { d: "M14.084 14.158a3 3 0 0 1-4.242-4.242" }],
    ["path", { d: "M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" }],
    ["path", { d: "m2 2 20 20" }],
  ],
  external: [["path", { d: "M15 3h6v6" }], ["path", { d: "M10 14 21 3" }], ["path", { d: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" }]],
  brain: [
    ["path", { d: "M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" }],
    ["path", { d: "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" }],
    ["path", { d: "M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" }],
    ["path", { d: "M17.599 6.5a3 3 0 0 0 .399-1.375" }],
    ["path", { d: "M6.003 5.125A3 3 0 0 0 6.401 6.5" }],
    ["path", { d: "M3.477 10.896a4 4 0 0 1 .585-.396" }],
    ["path", { d: "M19.938 10.5a4 4 0 0 1 .585.396" }],
    ["path", { d: "M6 18a4 4 0 0 1-1.967-.516" }],
    ["path", { d: "M19.967 17.484A4 4 0 0 1 18 18" }],
  ],
  message: [["path", { d: "M7.9 20A9 9 0 1 0 4 16.1L2 22Z" }]],
  // A draft of an email and the press that sends it (item 314).
  mail: [["rect", { width: 20, height: 16, x: 2, y: 4, rx: 2 }], ["path", { d: "m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" }]],
  send: [
    ["path", { d: "M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" }],
    ["path", { d: "m21.854 2.147-10.94 10.939" }],
  ],
  lock: [["rect", { width: 18, height: 11, x: 3, y: 11, rx: 2 }], ["path", { d: "M7 11V7a5 5 0 0 1 10 0v4" }]],
  lockOpen: [["rect", { width: 18, height: 11, x: 3, y: 11, rx: 2 }], ["path", { d: "M7 11V7a5 5 0 0 1 9.9-1" }]],
  sun: [
    ["circle", { cx: 12, cy: 12, r: 4 }],
    ["path", { d: "M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" }],
  ],
  moon: [["path", { d: "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" }]],
  palette: [
    ["path", { d: "M12 22a10 10 0 1 1 10-10c0 2.8-2.2 4-4.5 4H15a2 2 0 0 0-1.5 3.3c.6.7.2 2.7-1.5 2.7Z" }],
    ["circle", { cx: 7.5, cy: 10.5, r: 1 }],
    ["circle", { cx: 10.5, cy: 6.5, r: 1 }],
    ["circle", { cx: 15.5, cy: 7.5, r: 1 }],
  ],
};

/** Dot's wordmark (item 114), named "Dot": a copy of its one drawing, the opening's in index.html. */
export function wordmark() {
  const drawing = document.querySelector(".opening .wordmark");
  if (drawing === null) {
    throw new Error("the page has no wordmark to copy: index.html's opening is missing");
  }
  const copy = /** @type {SVGElement} */ (drawing.cloneNode(true));
  copy.setAttribute("role", "img");
  copy.setAttribute("aria-label", "Dot");
  return copy;
}

/** An inline SVG icon; its size follows the font size of the surrounding text. */
export function icon(name) {
  const shapes = SHAPES[name];
  if (shapes === undefined) {
    throw new Error(`unknown icon: ${name}`);
  }
  return svg(
    "svg",
    { class: "icon", "data-icon": name, viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" },
    shapes.map(([tag, attrs]) => svg(tag, attrs)),
  );
}

/** The icon that stands for a tool, chosen from its name. */
export function toolIconName(tool) {
  if (tool.startsWith("browser_") || tool.startsWith("web_")) {
    return "globe";
  }
  if (tool === "desktop_clipboard_read" || tool === "desktop_clipboard_write") {
    return "copy";
  }
  if (tool.startsWith("desktop_") || tool === "apple_script") {
    return "monitor";
  }
  if (tool === "shell" || tool.startsWith("terminal_")) {
    return "terminal";
  }
  if (tool === "read_file") {
    return "eye";
  }
  if (tool === "find_files" || tool === "search_files") {
    return "search";
  }
  if (tool === "write_file" || tool === "edit_file" || tool === "list_dir") {
    return "file";
  }
  if (tool.startsWith("memory_")) {
    return "database";
  }
  if (tool.startsWith("schedule_")) {
    return "calendar";
  }
  if (tool === "delegate") {
    return "branch";
  }
  if (tool === "update_plan" || tool === "mark_plan_step") {
    return "list";
  }
  if (tool === "ask_user") {
    return "help";
  }
  if (tool === "notify") {
    return "bell";
  }
  if (tool === "use_tools") {
    return "layers";
  }
  return "wrench";
}

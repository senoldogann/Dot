// The room of the office (the Office view of the Agents page) in three.js: a floor slab cut away at the front, two walls
// with windows onto a sky of the hour, and the furniture of the plan (office/layout.js), all built from rounded
// primitives with no model file. Warm light through the windows casts the one set of shadows; the lamps and the
// screens glow. `buildRoom` returns the scene's root, the desks' screens to switch on and off, and `tick(now, hour)`,
// which moves the clock's hands, scrolls the working screens and turns the sky and the light with the hour.

import { WALL } from "./layout.js";
import { THREE } from "./three.js";

/** @typedef {import("./layout.js").Plan} Plan */

const WALL_H = 3.0;
const SLAB = 0.35;
const OAK = "#c99a6b";
const OAK_DARK = "#a87b52";
const CREAM = "#f2e8d8";
const PLASTER = "#ece0cc";
const GRAPHITE = "#3a3a40";
const STEEL = "#b9bcc2";
const FABRIC = "#9fb6a3";
const LEAF = ["#5f8f5a", "#6fa364", "#4f7d4c"];
const BOOKS = ["#c8553d", "#f2a541", "#3c6e71", "#7d5ba6", "#e8d7b7", "#2d4059", "#d9886a"];
const MUGS = ["#e76f51", "#2a9d8f", "#e9c46a", "#f4a1c1", "#8ab6d6", "#f1f1f1"];

/** A box with every edge rounded by `r`, its base on y 0 and centred on x and z. */
export function roundedBox(w, h, d, r) {
  const radius = Math.min(r, w / 2 - 1e-3, d / 2 - 1e-3, h / 2 - 1e-3);
  const shape = new THREE.Shape();
  const x = -w / 2 + radius;
  const y = -d / 2 + radius;
  const iw = w - 2 * radius;
  const id = d - 2 * radius;
  shape.moveTo(x, y - radius);
  shape.lineTo(x + iw, y - radius);
  shape.absarc(x + iw, y, radius, -Math.PI / 2, 0, false);
  shape.lineTo(x + iw + radius, y + id);
  shape.absarc(x + iw, y + id, radius, 0, Math.PI / 2, false);
  shape.lineTo(x, y + id + radius);
  shape.absarc(x, y + id, radius, Math.PI / 2, Math.PI, false);
  shape.lineTo(x - radius, y);
  shape.absarc(x, y, radius, Math.PI, Math.PI * 1.5, false);
  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth: Math.max(1e-3, h - 2 * radius),
    bevelEnabled: true,
    bevelThickness: radius,
    bevelSize: radius * 0.98,
    // Small pieces need few segments: a book or a key is a few pixels wide (the measure of item 889: the room was 615k
    // triangles).
    bevelSegments: radius < 0.02 ? 1 : 2,
    curveSegments: radius < 0.02 ? 2 : 4,
  });
  geometry.rotateX(-Math.PI / 2);
  geometry.translate(0, radius, 0);
  geometry.computeVertexNormals();
  return geometry;
}

const materials = new Map();
/** One shared standard material per colour and finish. */
export function mat(color, roughness = 0.8, extra = {}) {
  const key = `${color}|${roughness}|${JSON.stringify(extra)}`;
  let found = materials.get(key);
  if (found === undefined) {
    found = new THREE.MeshStandardMaterial({ color, roughness, metalness: 0, ...extra });
    materials.set(key, found);
  }
  return found;
}

/** A mesh that casts and takes shadows, at `x, y, z`. */
export function mesh(geometry, material, x = 0, y = 0, z = 0) {
  const made = new THREE.Mesh(geometry, material);
  made.position.set(x, y, z);
  made.castShadow = true;
  made.receiveShadow = true;
  return made;
}

function cylinder(rTop, rBottom, h, segments = 20) {
  const geometry = new THREE.CylinderGeometry(rTop, rBottom, h, segments);
  geometry.translate(0, h / 2, 0);
  return geometry;
}

function canvasTexture(width, height, draw) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (context !== null) {
    draw(context, width, height);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return { texture, canvas, context };
}

/** The floor's boards: long planks of oak with a darker seam and a little grain. */
function floorTexture() {
  const { texture } = canvasTexture(512, 512, (g, w, h) => {
    g.fillStyle = "#d6b58c";
    g.fillRect(0, 0, w, h);
    const plank = h / 8;
    for (let row = 0; row < 8; row += 1) {
      const offset = (row * 173) % w;
      for (let start = -offset; start < w; start += w / 1.6) {
        const tone = 205 + ((row * 37 + Math.round(start)) % 18);
        g.fillStyle = `rgb(${tone}, ${tone - 34}, ${tone - 78})`;
        g.fillRect(start + 2, row * plank + 2, w / 1.6 - 4, plank - 4);
      }
      g.fillStyle = "rgba(120, 82, 50, 0.35)";
      g.fillRect(0, row * plank, w, 2);
    }
    g.globalAlpha = 0.06;
    for (let line = 0; line < 400; line += 1) {
      g.fillStyle = line % 2 ? "#6b4a2e" : "#fff";
      g.fillRect(Math.random() * w, Math.random() * h, 30 + Math.random() * 60, 1);
    }
  });
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  return texture;
}

/** The sky behind the windows at `hour`: day blue, evening amber to violet, night navy with stars. */
function paintSky(g, w, h, hour) {
  const night = hour < 6 || hour >= 21;
  const evening = !night && hour >= 17;
  const morning = !night && hour < 9;
  const stops = night
    ? ["#0d1330", "#1b2550", "#2c2f5e"]
    : evening
      ? ["#3e3a7a", "#c86b7a", "#f5b26b"]
      : morning
        ? ["#8fb8e8", "#f3c9a6", "#fbe3c4"]
        : ["#7fb2ec", "#a9cdf2", "#dcebf7"];
  const gradient = g.createLinearGradient(0, 0, 0, h);
  gradient.addColorStop(0, stops[0]);
  gradient.addColorStop(0.6, stops[1]);
  gradient.addColorStop(1, stops[2]);
  g.fillStyle = gradient;
  g.fillRect(0, 0, w, h);
  if (night) {
    g.fillStyle = "#fff";
    for (let star = 0; star < 70; star += 1) {
      const size = Math.random() < 0.15 ? 2 : 1;
      g.globalAlpha = 0.4 + Math.random() * 0.6;
      g.fillRect(Math.random() * w, Math.random() * h * 0.75, size, size);
    }
    g.globalAlpha = 1;
  } else {
    // Soft clouds.
    g.fillStyle = evening ? "rgba(255, 210, 190, 0.35)" : "rgba(255, 255, 255, 0.55)";
    for (let cloud = 0; cloud < 5; cloud += 1) {
      const cx = ((cloud * 211) % w) + 20;
      const cy = 30 + ((cloud * 97) % (h / 2));
      for (let puff = 0; puff < 4; puff += 1) {
        g.beginPath();
        g.ellipse(cx + puff * 22, cy + (puff % 2) * 6, 26, 12, 0, 0, Math.PI * 2);
        g.fill();
      }
    }
  }
  // A skyline far away.
  g.fillStyle = night ? "rgba(10, 12, 30, 0.85)" : evening ? "rgba(70, 50, 90, 0.55)" : "rgba(120, 140, 170, 0.35)";
  for (let block = 0; block < 14; block += 1) {
    const bw = 18 + ((block * 13) % 22);
    const bh = 30 + ((block * 41) % 70);
    g.fillRect(block * (w / 14), h - bh, bw, bh);
    if (night) {
      g.fillStyle = "rgba(255, 214, 140, 0.8)";
      for (let lit = 0; lit < 5; lit += 1) {
        g.fillRect(block * (w / 14) + 3 + ((lit * 7) % (bw - 4)), h - bh + 6 + lit * 9, 2, 3);
      }
      g.fillStyle = "rgba(10, 12, 30, 0.85)";
    }
  }
}

/** The code a working screen shows: lines of muted colours on a dark editor, drawn twice its height to scroll. */
function codeTexture() {
  const made = canvasTexture(128, 256, (g, w, h) => {
    g.fillStyle = "#1e2230";
    g.fillRect(0, 0, w, h);
    const colours = ["#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#c0caf5", "#7dcfff"];
    for (let line = 0; line < 32; line += 1) {
      const indent = ((line * 7) % 4) * 8;
      let x = 6 + indent;
      for (let word = 0; word < 1 + ((line * 5) % 4); word += 1) {
        const length = 10 + ((line * 13 + word * 29) % 34);
        g.fillStyle = colours[(line + word) % colours.length];
        g.fillRect(x, 6 + line * 8, Math.min(length, w - x - 4), 3);
        x += length + 5;
      }
    }
  });
  made.texture.wrapT = THREE.RepeatWrapping;
  made.texture.repeat.set(1, 0.5);
  return made.texture;
}

/** A nameplate for a desk: the agent's name on a little block of oak. */
function nameplate(name) {
  const { texture } = canvasTexture(256, 64, (g, w, h) => {
    g.fillStyle = "#e9d2b0";
    g.fillRect(0, 0, w, h);
    g.fillStyle = "#4a3624";
    g.font = "600 30px system-ui, -apple-system, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(name.length > 14 ? `${name.slice(0, 13)}…` : name, w / 2, h / 2 + 2);
  });
  const group = new THREE.Group();
  const block = mesh(new THREE.BoxGeometry(0.3, 0.07, 0.06), mat(OAK_DARK, 0.7));
  block.position.y = 0.035;
  block.rotation.x = -0.25;
  const label = new THREE.Mesh(new THREE.PlaneGeometry(0.28, 0.065), new THREE.MeshStandardMaterial({ map: texture, roughness: 0.8 }));
  label.position.set(0, 0.04, 0.031);
  label.rotation.x = -0.25;
  group.add(block, label);
  return group;
}

function desk(piece, name, code) {
  const group = new THREE.Group();
  const top = mesh(roundedBox(piece.w, 0.05, piece.d, 0.02), mat(OAK, 0.6), 0, 0.72, 0);
  group.add(top);
  for (const side of [-1, 1]) {
    for (const end of [-1, 1]) {
      group.add(mesh(cylinder(0.022, 0.022, 0.72, 12), mat("#d9dade", 0.35), side * (piece.w / 2 - 0.08), 0, end * (piece.d / 2 - 0.08)));
    }
    group.add(mesh(roundedBox(0.03, 0.03, piece.d - 0.16, 0.01), mat("#d9dade", 0.35), side * (piece.w / 2 - 0.08), 0.12, 0));
  }
  // The monitor faces the chair: the chair is toward -z of the desk in its own frame.
  const monitor = new THREE.Group();
  monitor.position.set(0, 0.77, piece.d * 0.18);
  const stand = mesh(cylinder(0.02, 0.025, 0.16), mat(STEEL, 0.4));
  const foot = mesh(roundedBox(0.2, 0.015, 0.14, 0.006), mat(STEEL, 0.4));
  const frame = mesh(roundedBox(0.64, 0.4, 0.035, 0.015), mat("#2b2b30", 0.4), 0, 0.12, 0);
  const screenMaterial = new THREE.MeshStandardMaterial({ color: "#11131a", roughness: 0.25, emissive: "#ffffff", emissiveIntensity: 0, emissiveMap: code });
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 0.36), screenMaterial);
  screen.position.set(0, 0.32, -0.019);
  // Lit while its agent works: drawn on its own, never baked into the room.
  screen.userData.dynamic = true;
  screen.rotation.y = Math.PI;
  monitor.add(foot, stand, frame, screen);
  group.add(monitor);
  const keyboard = mesh(roundedBox(0.42, 0.018, 0.14, 0.008), mat("#e7e4df", 0.6), -0.05, 0.77, -piece.d * 0.12);
  const mouse = mesh(roundedBox(0.06, 0.02, 0.1, 0.01), mat("#e7e4df", 0.5), 0.26, 0.77, -piece.d * 0.12);
  const mug = mesh(cylinder(0.04, 0.035, 0.1), mat(MUGS[Math.abs(hash(piece.id)) % MUGS.length], 0.5), -0.55, 0.77, 0.05);
  group.add(keyboard, mouse, mug);
  if (name !== "") {
    const plate = nameplate(name);
    plate.position.set(0.5, 0.77, -piece.d * 0.32);
    plate.rotation.y = Math.PI;
    group.add(plate);
  }
  return { group, screen: screenMaterial };
}

function chair(color) {
  const group = new THREE.Group();
  const base = new THREE.Group();
  for (let spoke = 0; spoke < 5; spoke += 1) {
    const arm = mesh(roundedBox(0.04, 0.03, 0.28, 0.012), mat(GRAPHITE, 0.4), 0, 0.05, 0.14);
    const holder = new THREE.Group();
    holder.rotation.y = (spoke / 5) * Math.PI * 2;
    holder.add(arm);
    const wheel = mesh(new THREE.SphereGeometry(0.03, 10, 8), mat("#1f1f22", 0.5), 0, 0.03, 0.27);
    holder.add(wheel);
    base.add(holder);
  }
  const lift = mesh(cylinder(0.025, 0.025, 0.33), mat(STEEL, 0.35), 0, 0.08, 0);
  const seat = mesh(roundedBox(0.48, 0.08, 0.46, 0.035), mat(color, 0.85), 0, 0.42, 0);
  const back = mesh(roundedBox(0.44, 0.48, 0.07, 0.03), mat(color, 0.85), 0, 0.55, -0.22);
  back.rotation.x = -0.12;
  group.add(base, lift, seat, back);
  return group;
}

function sofa(piece) {
  const group = new THREE.Group();
  const body = mat(FABRIC, 0.95);
  group.add(mesh(roundedBox(piece.w, 0.3, piece.d, 0.06), body, 0, 0.1, 0));
  for (const dx of [-1, 0, 1]) {
    group.add(mesh(roundedBox(piece.w / 3 - 0.06, 0.14, piece.d * 0.72, 0.06), mat("#b2c7b5", 0.95), (dx * piece.w) / 3, 0.38, -0.08));
    group.add(mesh(roundedBox(piece.w / 3 - 0.08, 0.42, 0.2, 0.08), mat("#b2c7b5", 0.95), (dx * piece.w) / 3, 0.38, piece.d / 2 - 0.16));
  }
  for (const side of [-1, 1]) {
    group.add(mesh(roundedBox(0.18, 0.55, piece.d, 0.07), body, side * (piece.w / 2 - 0.09), 0.1, 0));
  }
  for (const [x, z] of [
    [-1, -1],
    [1, -1],
    [-1, 1],
    [1, 1],
  ]) {
    group.add(mesh(cylinder(0.025, 0.02, 0.1), mat(OAK_DARK, 0.6), x * (piece.w / 2 - 0.1), 0, z * (piece.d / 2 - 0.1)));
  }
  const pillow = mesh(roundedBox(0.36, 0.32, 0.12, 0.06), mat("#f2a541", 0.95), piece.w / 2 - 0.42, 0.5, piece.d / 2 - 0.3);
  pillow.rotation.set(-0.2, 0.25, 0.15);
  group.add(pillow);
  return group;
}

function armchair(piece) {
  const group = new THREE.Group();
  const body = mat("#d9886a", 0.95);
  group.add(mesh(roundedBox(piece.w, 0.32, piece.d, 0.07), body, 0, 0.1, 0));
  group.add(mesh(roundedBox(piece.w - 0.1, 0.45, 0.2, 0.08), body, 0, 0.35, piece.d / 2 - 0.12));
  for (const side of [-1, 1]) {
    group.add(mesh(roundedBox(0.14, 0.5, piece.d, 0.06), body, side * (piece.w / 2 - 0.07), 0.1, 0));
  }
  return group;
}

function lowTable(piece) {
  const group = new THREE.Group();
  group.add(mesh(roundedBox(piece.w, 0.05, piece.d, 0.02), mat(OAK, 0.55), 0, 0.36, 0));
  for (const [x, z] of [
    [-1, -1],
    [1, -1],
    [-1, 1],
    [1, 1],
  ]) {
    group.add(mesh(cylinder(0.025, 0.025, 0.36), mat(OAK_DARK, 0.6), x * (piece.w / 2 - 0.08), 0, z * (piece.d / 2 - 0.08)));
  }
  group.add(mesh(new THREE.TorusKnotGeometry(0.06, 0.02, 48, 8), mat("#e76f51", 0.4), 0.25, 0.47, 0));
  for (let book = 0; book < 3; book += 1) {
    group.add(mesh(roundedBox(0.26, 0.035, 0.18, 0.005), mat(BOOKS[book + 2], 0.8), -0.25, 0.41 + book * 0.036, 0));
  }
  return group;
}

function rug(piece) {
  const { texture } = canvasTexture(256, 256, (g, w, h) => {
    g.fillStyle = "#e9dcc7";
    g.fillRect(0, 0, w, h);
    g.strokeStyle = "#c9a27e";
    g.lineWidth = 6;
    g.strokeRect(14, 14, w - 28, h - 28);
    g.fillStyle = "rgba(217, 136, 106, 0.25)";
    for (let dot = 0; dot < 40; dot += 1) {
      g.beginPath();
      g.arc(30 + ((dot * 53) % (w - 60)), 30 + ((dot * 91) % (h - 60)), 6, 0, Math.PI * 2);
      g.fill();
    }
  });
  const made = mesh(roundedBox(piece.w, 0.02, piece.d, 0.01), new THREE.MeshStandardMaterial({ map: texture, roughness: 1 }));
  made.castShadow = false;
  return made;
}

function bar(piece) {
  const group = new THREE.Group();
  group.add(mesh(roundedBox(piece.w, 0.9, piece.d, 0.03), mat(CREAM, 0.8), 0, 0, 0));
  group.add(mesh(roundedBox(piece.w + 0.04, 0.04, piece.d + 0.04, 0.015), mat(OAK, 0.5), 0, 0.9, 0));
  for (let door = 0; door < 4; door += 1) {
    const x = -piece.w / 2 + piece.w / 8 + (door * piece.w) / 4;
    group.add(mesh(roundedBox(piece.w / 4 - 0.04, 0.78, 0.02, 0.008), mat("#e8dcc8", 0.8), x, 0.06, piece.d / 2));
    group.add(mesh(new THREE.BoxGeometry(0.1, 0.015, 0.02), mat(STEEL, 0.3), x, 0.75, piece.d / 2 + 0.02));
  }
  const machine = new THREE.Group();
  machine.position.set(-piece.w / 2 + 0.45, 0.94, -0.05);
  machine.add(mesh(roundedBox(0.34, 0.42, 0.32, 0.04), mat("#2f2f35", 0.35)));
  machine.add(mesh(roundedBox(0.2, 0.06, 0.12, 0.02), mat(STEEL, 0.3), 0, 0.08, 0.16));
  machine.add(mesh(cylinder(0.035, 0.03, 0.07), mat("#ffffff", 0.4), 0, 0.02, 0.17));
  group.add(machine);
  for (let cup = 0; cup < 4; cup += 1) {
    group.add(mesh(cylinder(0.035, 0.03, 0.08), mat(MUGS[cup], 0.45), 0.1 + cup * 0.11, 0.94, 0.12));
  }
  const bowl = mesh(new THREE.SphereGeometry(0.15, 20, 10, 0, Math.PI * 2, Math.PI / 2, Math.PI / 2), mat("#f5f0e8", 0.5), piece.w / 2 - 0.4, 1.08, 0);
  group.add(bowl);
  for (let fruit = 0; fruit < 4; fruit += 1) {
    group.add(mesh(new THREE.SphereGeometry(0.055, 14, 10), mat(["#f2a541", "#e76f51", "#9ece6a", "#f2a541"][fruit], 0.6), piece.w / 2 - 0.45 + (fruit % 2) * 0.09, 1.0 + Math.floor(fruit / 2) * 0.05, -0.04 + (fruit % 3) * 0.04));
  }
  return group;
}

function whiteboard(piece) {
  const group = new THREE.Group();
  group.add(mesh(roundedBox(piece.w, 1.1, 0.05, 0.02), mat("#d9d9de", 0.5), 0, 0.95, 0));
  group.add(mesh(new THREE.BoxGeometry(piece.w - 0.1, 1.0, 0.01), mat("#fbfbf8", 0.35, { emissive: "#ffffff", emissiveIntensity: 0.35 }), 0, 1.5, 0.03));
  const notes = ["#f9e27d", "#f4a1c1", "#9ad1d4", "#b8e0a6", "#f9e27d", "#f4a1c1"];
  for (const [index, colour] of notes.entries()) {
    const note = mesh(new THREE.BoxGeometry(0.16, 0.16, 0.005), mat(colour, 0.9), -piece.w / 2 + 0.3 + (index % 3) * 0.24, 1.75 - Math.floor(index / 3) * 0.24, 0.04);
    note.rotation.z = (index % 2 ? 1 : -1) * 0.06;
    group.add(note);
  }
  // A chart drawn on the board.
  for (let bar = 0; bar < 4; bar += 1) {
    group.add(mesh(new THREE.BoxGeometry(0.08, 0.1 + bar * 0.09, 0.004), mat(["#3c6e71", "#f2a541", "#e76f51", "#7d5ba6"][bar], 0.6), piece.w / 2 - 0.7 + bar * 0.13, 1.2 + (0.1 + bar * 0.09) / 2, 0.04));
  }
  group.add(mesh(new THREE.BoxGeometry(piece.w - 0.3, 0.03, 0.08), mat("#cfd0d4", 0.4), 0, 0.97, 0.06));
  return group;
}

function shelf(piece) {
  const group = new THREE.Group();
  const width = piece.d;
  const wood = mat(OAK, 0.65);
  for (const side of [-1, 1]) {
    group.add(mesh(roundedBox(0.04, 2.0, piece.w, 0.01), wood, side * (width / 2), 0, 0));
  }
  for (let board = 0; board < 5; board += 1) {
    const y = 0.06 + board * 0.47;
    group.add(mesh(roundedBox(width, 0.035, piece.w, 0.01), wood, 0, y, 0));
    if (board === 4) continue;
    let x = -width / 2 + 0.08;
    let index = board * 3;
    while (x < width / 2 - 0.12) {
      const thick = 0.04 + ((index * 7) % 5) * 0.012;
      const tall = 0.24 + ((index * 11) % 6) * 0.025;
      const book = mesh(roundedBox(thick, tall, 0.22, 0.004), mat(BOOKS[index % BOOKS.length], 0.8), x + thick / 2, y + 0.035, 0);
      if (index % 9 === 4) book.rotation.z = 0.18;
      group.add(book);
      x += thick + 0.006 + (index % 7 === 3 ? 0.18 : 0);
      index += 1;
    }
  }
  group.add(plant({ w: 0.25, d: 0.25, id: "shelf-plant" }, 0.55).translateY(1.95));
  return group;
}

function roundTable(piece) {
  const group = new THREE.Group();
  group.add(mesh(cylinder(piece.w / 2, piece.w / 2, 0.05, 40), mat(CREAM, 0.5), 0, 0.72, 0));
  group.add(mesh(cylinder(0.06, 0.08, 0.72), mat(GRAPHITE, 0.4)));
  group.add(mesh(cylinder(0.3, 0.32, 0.03, 30), mat(GRAPHITE, 0.4)));
  group.add(mesh(roundedBox(0.3, 0.02, 0.22, 0.008), mat("#e7e4df", 0.5), 0.15, 0.77, 0.1));
  return group;
}

function plant(piece, scale = 1) {
  const group = new THREE.Group();
  const size = Math.min(piece.w, piece.d) * scale;
  group.add(mesh(cylinder(size * 0.42, size * 0.32, size * 0.7, 24), mat(["#e9dcc7", "#d9886a", "#3a3a40"][Math.abs(hash(piece.id)) % 3], 0.7)));
  const leaves = 7 + (Math.abs(hash(piece.id)) % 5);
  for (let leaf = 0; leaf < leaves; leaf += 1) {
    const geometry = new THREE.SphereGeometry(size * 0.28, 10, 8);
    geometry.scale(0.45, 1.6, 0.25);
    const blade = mesh(geometry, mat(LEAF[leaf % LEAF.length], 0.8));
    const angle = (leaf / leaves) * Math.PI * 2;
    blade.position.set(Math.sin(angle) * size * 0.16, size * (0.95 + (leaf % 3) * 0.12), Math.cos(angle) * size * 0.16);
    blade.rotation.set(Math.cos(angle) * 0.5, angle, Math.sin(angle) * -0.5);
    group.add(blade);
  }
  return group;
}

function lamp() {
  const group = new THREE.Group();
  group.add(mesh(cylinder(0.16, 0.18, 0.03, 24), mat(GRAPHITE, 0.4)));
  group.add(mesh(cylinder(0.015, 0.015, 1.45), mat(GRAPHITE, 0.4)));
  const shade = mesh(cylinder(0.12, 0.22, 0.26, 24), new THREE.MeshStandardMaterial({ color: "#f6ead2", roughness: 0.9, emissive: "#ffd9a0", emissiveIntensity: 0.6, side: THREE.DoubleSide }), 0, 1.4, 0);
  group.add(shade);
  const light = new THREE.PointLight("#ffcf8f", 1.2, 5, 1.6);
  light.position.set(0, 1.45, 0);
  group.add(light);
  return { group, light, shade: /** @type {ThreeStandardMaterial} */ (shade.material) };
}

/** A wall clock that tells the Mac's time. */
function clock() {
  const group = new THREE.Group();
  group.add(mesh(cylinder(0.24, 0.24, 0.05, 40).rotateX(Math.PI / 2), mat("#2f2f35", 0.4)));
  group.add(mesh(cylinder(0.215, 0.215, 0.01, 40).rotateX(Math.PI / 2), mat("#fbf7ef", 0.6), 0, 0, 0.05));
  for (let mark = 0; mark < 12; mark += 1) {
    const tick = mesh(new THREE.BoxGeometry(0.012, mark % 3 === 0 ? 0.05 : 0.025, 0.005), mat("#2f2f35", 0.5));
    const angle = (mark / 12) * Math.PI * 2;
    tick.position.set(Math.sin(angle) * 0.18, Math.cos(angle) * 0.18, 0.057);
    tick.rotation.z = -angle;
    group.add(tick);
  }
  const hand = (length, width, colour) => {
    const pivot = new THREE.Group();
    const bar = mesh(new THREE.BoxGeometry(width, length, 0.006), mat(colour, 0.5));
    bar.position.y = length / 2 - 0.02;
    pivot.add(bar);
    pivot.position.z = 0.062;
    group.add(pivot);
    return pivot;
  };
  const hours = hand(0.11, 0.016, "#2f2f35");
  const minutes = hand(0.16, 0.01, "#2f2f35");
  const seconds = hand(0.17, 0.004, "#e76f51");
  return {
    group,
    set(date) {
      const s = date.getSeconds() + date.getMilliseconds() / 1000;
      const m = date.getMinutes() + s / 60;
      const h = (date.getHours() % 12) + m / 60;
      seconds.rotation.z = -(s / 60) * Math.PI * 2;
      minutes.rotation.z = -(m / 60) * Math.PI * 2;
      hours.rotation.z = -(h / 12) * Math.PI * 2;
    },
  };
}

/** A framed print of soft shapes, warm and quiet. */
function print() {
  const { texture } = canvasTexture(256, 320, (g, w, h) => {
    g.fillStyle = "#f6efe3";
    g.fillRect(0, 0, w, h);
    const shapes = [
      ["#e76f51", 80, 110, 60],
      ["#2a9d8f", 170, 150, 46],
      ["#f2a541", 120, 210, 52],
      ["#7d5ba6", 190, 240, 30],
    ];
    for (const [colour, x, y, r] of shapes) {
      g.fillStyle = String(colour);
      g.beginPath();
      g.arc(Number(x), Number(y), Number(r), 0, Math.PI * 2);
      g.fill();
    }
  });
  const group = new THREE.Group();
  group.add(mesh(roundedBox(0.7, 0.86, 0.04, 0.01), mat(OAK_DARK, 0.6)));
  const art = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 0.76), new THREE.MeshStandardMaterial({ map: texture, roughness: 0.9 }));
  art.position.set(0, 0.43, 0.041);
  group.add(art);
  return group;
}

function hash(text) {
  let h = 0;
  for (let index = 0; index < text.length; index += 1) {
    h = (Math.imul(h, 31) + text.charCodeAt(index)) | 0;
  }
  return h;
}

/**
 * Build the room of `plan` into a new group, with each desk's nameplate from `names` (desk id to name). Returns the
 * group, the lights to aim, the screens by desk id and `tick(now, date)`.
 * @param {Plan} plan
 * @param {Map<string, string>} names
 */
export function buildRoom(plan, names) {
  const root = new THREE.Group();
  const { width, depth } = plan;
  const left = -width / 2;
  const back = -depth / 2;

  // The slab: the floor's boards on top of a block whose sides show its thickness.
  const boards = floorTexture();
  boards.repeat.set(width / 3, depth / 3);
  const slab = mesh(roundedBox(width + WALL, SLAB, depth + WALL, 0.05), mat("#cdb89a", 0.9), -WALL / 2, -SLAB, -WALL / 2);
  slab.castShadow = false;
  root.add(slab);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), new THREE.MeshStandardMaterial({ map: boards, roughness: 0.75 }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = 0.002;
  floor.receiveShadow = true;
  root.add(floor);

  // The walls, each with windows: a sky behind glass, framed in graphite.
  const sky = canvasTexture(512, 256, (g, w, h) => paintSky(g, w, h, new Date().getHours()));
  const skyMaterial = new THREE.MeshBasicMaterial({ map: sky.texture, toneMapped: false });
  const wallMaterial = mat(PLASTER, 0.95);
  const backWall = new THREE.Group();
  backWall.position.set(0, 0, back - WALL / 2);
  const leftWall = new THREE.Group();
  leftWall.position.set(left - WALL / 2, 0, 0);
  leftWall.rotation.y = Math.PI / 2;
  /** @param {ThreeGroup} wall @param {number} length @param {number[]} windows */
  const buildWall = (wall, length, windows) => {
    const holeW = 1.6;
    const holeBottom = 1.0;
    const holeTop = 2.45;
    const sorted = [...windows].sort((a, b) => a - b);
    const edges = [-length / 2, ...sorted.flatMap((at) => [at - holeW / 2, at + holeW / 2]), length / 2];
    for (let index = 0; index < edges.length; index += 2) {
      const from = edges[index];
      const to = edges[index + 1];
      if (to - from > 1e-3) {
        wall.add(mesh(new THREE.BoxGeometry(to - from, WALL_H, WALL), wallMaterial, (from + to) / 2, WALL_H / 2, 0));
      }
    }
    for (const at of windows) {
      wall.add(mesh(new THREE.BoxGeometry(holeW, holeBottom, WALL), wallMaterial, at, holeBottom / 2, 0));
      wall.add(mesh(new THREE.BoxGeometry(holeW, WALL_H - holeTop, WALL), wallMaterial, at, holeTop + (WALL_H - holeTop) / 2, 0));
      const view = new THREE.Mesh(new THREE.PlaneGeometry(holeW, holeTop - holeBottom), skyMaterial);
      view.position.set(at, (holeBottom + holeTop) / 2, -WALL / 2);
      wall.add(view);
      const frame = mat("#2f2f35", 0.4);
      wall.add(mesh(new THREE.BoxGeometry(holeW + 0.08, 0.06, WALL + 0.06), frame, at, holeBottom, 0.02));
      wall.add(mesh(new THREE.BoxGeometry(holeW + 0.08, 0.06, WALL + 0.06), frame, at, holeTop, 0.02));
      wall.add(mesh(new THREE.BoxGeometry(0.05, holeTop - holeBottom, 0.05), frame, at, (holeBottom + holeTop) / 2, 0.02));
      wall.add(mesh(new THREE.BoxGeometry(holeW, 0.04, 0.04), frame, at, (holeBottom + holeTop) / 2 + 0.1, 0.02));
      for (const side of [-1, 1]) {
        wall.add(mesh(new THREE.BoxGeometry(0.06, holeTop - holeBottom + 0.06, WALL + 0.06), frame, at + side * (holeW / 2 + 0.01), (holeBottom + holeTop) / 2, 0.02));
      }
      wall.add(mesh(roundedBox(holeW + 0.2, 0.04, 0.24, 0.015), mat(OAK, 0.6), at, holeBottom - 0.02, 0.16));
    }
    wall.add(mesh(new THREE.BoxGeometry(length, 0.1, 0.03), mat("#e2d3bb", 0.8), 0, 0.05, WALL / 2 + 0.015));
  };
  // The back wall runs along x; the left wall, turned a quarter, has its own x along -z.
  buildWall(backWall, width + WALL, [left + 2.6, width / 2 - 2.4]);
  const leftWindows = [back + 5.6, back + 8.2].filter((z) => z < depth / 2 - 1.2);
  buildWall(leftWall, depth + WALL, leftWindows.map((z) => -z));
  root.add(backWall, leftWall);

  const theClock = clock();
  theClock.group.userData.dynamic = true;
  theClock.group.position.set(left + width * 0.42 + 1.55, 2.25, back + 0.03);
  root.add(theClock.group);
  const art = print();
  art.position.set(left + width * 0.42 - 1.85, 1.25, back + 0.03);
  root.add(art);

  /** @type {Map<string, ThreeStandardMaterial>} */
  const screens = new Map();
  const code = codeTexture();
  /** @type {{ light: ThreePointLight, shade: ThreeStandardMaterial }[]} */
  const lamps = [];
  for (const piece of plan.pieces) {
    /** @type {ThreeObject | null} */
    let made = null;
    switch (piece.kind) {
      case "desk": {
        const built = desk(piece, names.get(piece.id) ?? "", code);
        screens.set(piece.id, built.screen);
        made = built.group;
        break;
      }
      case "chair":
        made = chair(piece.id.startsWith("meet") ? "#d9886a" : "#4b4b52");
        break;
      case "sofa":
        made = sofa(piece);
        break;
      case "armchair":
        made = armchair(piece);
        break;
      case "lowtable":
        made = lowTable(piece);
        break;
      case "rug":
        made = rug(piece);
        break;
      case "bar":
        made = bar(piece);
        break;
      case "whiteboard":
        made = whiteboard(piece);
        break;
      case "shelf":
        made = shelf(piece);
        break;
      case "roundtable":
        made = roundTable(piece);
        break;
      case "plant":
        made = plant(piece);
        break;
      case "lamp": {
        const built = lamp();
        lamps.push(built);
        made = built.group;
        break;
      }
      default:
        break;
    }
    if (made !== null) {
      made.position.x += piece.x;
      made.position.z += piece.z;
      made.rotation.y += piece.facing;
      made.userData.piece = piece.id;
      root.add(made);
    }
  }

  bakeStatic(root);
  let skyHour = new Date().getHours();
  return {
    root,
    screens,
    lamps,
    /** Move the clock, scroll the screens that work and repaint the sky when the hour turns. */
    tick(now, date) {
      theClock.set(date);
      code.offset.y = (now * 0.04) % 1;
      if (date.getHours() !== skyHour && sky.context !== null) {
        skyHour = date.getHours();
        paintSky(sky.context, sky.canvas.width, sky.canvas.height, skyHour);
        sky.texture.needsUpdate = true;
      }
    },
  };
}

/**
 * Bake the still parts of `root` into one mesh per material (item 889): the room was 1,420 draw calls of small meshes,
 * each a call of its own, and a phone's GPU spends more on calls than on triangles. A part marked `userData.dynamic`
 * (a screen that lights, the clock's hands) and what is under it stay as they are. Returns how many meshes went.
 */
function bakeStatic(root) {
  root.updateMatrixWorld(true);
  const inverse = root.matrixWorld.clone().invert();
  /** @type {Map<string, { material: ThreeStandardMaterial, cast: boolean, parts: ThreeGeometry[] }>} */
  const groups = new Map();
  /** @type {ThreeMesh[]} */
  const baked = [];
  const visit = (node) => {
    if (node.userData.dynamic === true) {
      return;
    }
    const meshNode = /** @type {ThreeMesh} */ (node);
    if (meshNode.isMesh === true && !Array.isArray(meshNode.material)) {
      const material = /** @type {ThreeStandardMaterial} */ (meshNode.material);
      const key = `${material.uuid}|${meshNode.castShadow}`;
      let group = groups.get(key);
      if (group === undefined) {
        group = { material, cast: meshNode.castShadow, parts: [] };
        groups.set(key, group);
      }
      const geometry = meshNode.geometry.index === null ? meshNode.geometry.clone() : meshNode.geometry.toNonIndexed();
      geometry.applyMatrix4(inverse.clone().multiply(meshNode.matrixWorld));
      group.parts.push(geometry);
      baked.push(meshNode);
    }
    for (const child of node.children) {
      visit(child);
    }
  };
  visit(root);
  for (const mesh of baked) {
    mesh.removeFromParent();
    mesh.geometry.dispose();
  }
  for (const { material, cast, parts } of groups.values()) {
    const merged = mergeParts(parts);
    for (const part of parts) part.dispose();
    const made = new THREE.Mesh(merged, material);
    made.castShadow = cast;
    made.receiveShadow = true;
    made.matrixAutoUpdate = false;
    root.add(made);
  }
  return baked.length;
}

/** One geometry of non-indexed `parts`: their positions, normals and uvs one after another (a part with no uv gets
 * zeros). */
function mergeParts(parts) {
  const count = parts.reduce((sum, part) => sum + part.attributes.position.count, 0);
  const position = new Float32Array(count * 3);
  const normal = new Float32Array(count * 3);
  const uv = new Float32Array(count * 2);
  let at = 0;
  for (const part of parts) {
    const n = part.attributes.position.count;
    position.set(/** @type {Float32Array} */ (part.attributes.position.array), at * 3);
    if (part.attributes.normal !== undefined) normal.set(/** @type {Float32Array} */ (part.attributes.normal.array), at * 3);
    if (part.attributes.uv !== undefined) uv.set(/** @type {Float32Array} */ (part.attributes.uv.array), at * 2);
    at += n;
  }
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.BufferAttribute(position, 3));
  merged.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
  merged.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  merged.computeBoundingSphere();
  return merged;
}

/** Whether `hour` is night in the office: the lamps glow brighter and the sun is the moon. */
export function isNight(hour) {
  return hour < 6 || hour >= 20;
}

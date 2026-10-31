// An agent of the office as a figure (the Office view of the Agents page): its head is its look in three dimensions (the
// shape of its face, office/../character/skins.js, drawn round and deep, in its colour), with eyes painted on a canvas
// that blink, look about and change with what it does; a body of rounded limbs in clothes of its own, and an accessory
// some wear. A pose is a set of joint angles; the figure eases toward the pose of the moment and walks with a stride
// measured on the floor, so its feet do not slide. Dot itself wears its own look with gold eyes.

import { COLOUR_BY_ID, SHAPE_BY_ID } from "../character/skins.js";
import { mat, mesh, roundedBox } from "./scene.js";
import { THREE } from "./three.js";

const SHIRTS = ["#f1e6d3", "#c8d8c2", "#d9886a", "#9ab4d6", "#e9c46a", "#b9a6d6", "#ece9e4", "#8fb9a8"];
const TROUSERS = ["#e2d6c2", "#4b4b52", "#c9b79c", "#5a6b7d", "#d8cfc0"];
const EXTRAS = ["none", "headphones", "beanie", "bun", "glasses", "none", "cap", "glasses"];
const STRIDE = 0.62;

let contactTexture = null;
/** The soft dark round a figure stands on, shared by every figure. */
function contactShadow() {
  if (contactTexture === null) {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 64;
    const g = canvas.getContext("2d");
    if (g !== null) {
      const gradient = g.createRadialGradient(32, 32, 2, 32, 32, 32);
      gradient.addColorStop(0, "rgba(40, 28, 18, 0.42)");
      gradient.addColorStop(1, "rgba(40, 28, 18, 0)");
      g.fillStyle = gradient;
      g.fillRect(0, 0, 64, 64);
    }
    contactTexture = new THREE.CanvasTexture(canvas);
  }
  return contactTexture;
}

/** A whole number from `text`, the same every time. */
function hash(text) {
  let h = 0;
  for (let index = 0; index < text.length; index += 1) {
    h = (Math.imul(h, 31) + text.charCodeAt(index)) | 0;
  }
  return Math.abs(h);
}

/** The head of `look`: its face's outline, extruded and rounded, its front toward +z and its centre at 0. */
function headGeometry(shapeId) {
  const shape = SHAPE_BY_ID.get(shapeId) ?? SHAPE_BY_ID.get("circle");
  const radii = shape?.radii ?? [1];
  const outline = new THREE.Shape();
  const size = 0.2;
  radii.forEach((radius, index) => {
    const angle = (index / radii.length) * Math.PI * 2;
    // The profile's y points down, as on the screen.
    const x = Math.cos(angle) * radius * size;
    const y = -Math.sin(angle) * radius * size;
    if (index === 0) outline.moveTo(x, y);
    else outline.lineTo(x, y);
  });
  outline.closePath();
  const geometry = new THREE.ExtrudeGeometry(outline, {
    depth: 0.16,
    bevelEnabled: true,
    bevelThickness: 0.09,
    bevelSize: 0.06,
    bevelSegments: 6,
    curveSegments: 4,
  });
  geometry.translate(0, 0, -0.08);
  geometry.computeVertexNormals();
  return geometry;
}

function capsule(radius, length) {
  return new THREE.CapsuleGeometry(radius, length, 6, 12);
}

/** A limb: a joint whose child hangs down from it by `length`. */
function limb(radius, length, material) {
  const joint = new THREE.Group();
  const part = mesh(capsule(radius, length), material, 0, -length / 2, 0);
  joint.add(part);
  return joint;
}

/** What the eyes show: open, half shut on work, wide when waiting, shut asleep, smiling over a coffee. */
function paintEyes(g, w, h, mood, blink, gaze, light, gold) {
  g.clearRect(0, 0, w, h);
  const ink = gold ? "#f5c451" : light ? "#1d1b20" : "#ffffff";
  g.fillStyle = ink;
  g.strokeStyle = ink;
  g.lineCap = "round";
  g.lineWidth = 9;
  const spots = [w * 0.34, w * 0.66];
  for (const cx of spots) {
    const x = cx + gaze.x * 10;
    const y = h * 0.5 + gaze.y * 8;
    if (mood === "sleep" || blink > 0.92) {
      g.beginPath();
      g.arc(x, y - 4, 12, 0.2 * Math.PI, 0.8 * Math.PI);
      g.stroke();
    } else if (mood === "happy") {
      g.beginPath();
      g.arc(x, y + 8, 13, 1.2 * Math.PI, 1.8 * Math.PI);
      g.stroke();
    } else {
      const tall = (mood === "wide" ? 24 : mood === "focus" ? 14 : 21) * (1 - blink);
      g.beginPath();
      g.ellipse(x, y, mood === "wide" ? 13 : 11.5, Math.max(2.5, tall), 0, 0, Math.PI * 2);
      g.fill();
      // A glint, which makes a dark eye read as alive.
      if (tall > 8) {
        g.fillStyle = light || gold ? "rgba(255, 255, 255, 0.9)" : "rgba(0, 0, 0, 0.25)";
        g.beginPath();
        g.ellipse(x + 4, y - tall * 0.45, 3.5, 4.5, 0, 0, Math.PI * 2);
        g.fill();
        g.fillStyle = ink;
      }
    }
  }
}

/**
 * @typedef {{ group: ThreeGroup, head: ThreeGroup, setPose: (pose: string) => void,
 *   update: (dt: number, now: number, walked: number) => void, setHover: (on: boolean) => void, top: () => ThreeVector3,
 *   dispose: () => void }} Figure
 */

/**
 * The figure of an agent: `id` picks its clothes and accessory, `look` (`{ shape, colour }`) its head. `gold` is Dot's
 * own figure, with gold eyes.
 * @returns {Figure}
 */
export function makeFigure(id, look, gold = false) {
  const seed = hash(id);
  const colour = COLOUR_BY_ID.get(look.colour)?.hex ?? "#f1efe9";
  const light = ["cream", "amber", "grey", "green", "teal", "orange", "pink"].includes(look.colour);
  const skin = mat(colour, 0.55);
  const shirt = mat(SHIRTS[seed % SHIRTS.length], 0.9);
  const trousers = mat(TROUSERS[(seed >> 3) % TROUSERS.length], 0.9);
  const shoes = mat(seed % 2 ? "#2f2f35" : "#f3f1ec", 0.6);
  const extra = gold ? "none" : EXTRAS[(seed >> 5) % EXTRAS.length];

  const group = new THREE.Group();
  const body = new THREE.Group();
  body.scale.setScalar(1.18);
  group.add(body);
  // A soft shadow under the feet, which keeps the figure on the floor whatever the sun does.
  const contact = new THREE.Mesh(new THREE.CircleGeometry(0.34, 32), new THREE.MeshBasicMaterial({ map: contactShadow(), transparent: true, depthWrite: false, toneMapped: false }));
  contact.rotation.x = -Math.PI / 2;
  contact.position.y = 0.006;
  group.add(contact);
  const hips = new THREE.Group();
  hips.position.y = 0.62;
  body.add(hips);
  hips.add(mesh(capsule(0.15, 0.06), trousers, 0, 0.02, 0));
  const spine = new THREE.Group();
  spine.position.y = 0.08;
  hips.add(spine);
  const torso = mesh(capsule(0.165, 0.2), shirt, 0, 0.17, 0);
  torso.scale.set(1.05, 1, 0.82);
  spine.add(torso);
  const neck = new THREE.Group();
  neck.position.y = 0.42;
  spine.add(neck);
  neck.add(mesh(new THREE.CylinderGeometry(0.05, 0.06, 0.08, 12), skin, 0, 0.02, 0));
  const head = new THREE.Group();
  head.position.y = 0.3;
  neck.add(head);
  const skull = mesh(headGeometry(look.shape), skin);
  skull.scale.setScalar(1.28);
  head.add(skull);

  // The eyes: a canvas on the front of the head, redrawn when the mood, a blink or the gaze changes.
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 64;
  const g = canvas.getContext("2d");
  const eyesTexture = new THREE.CanvasTexture(canvas);
  eyesTexture.colorSpace = THREE.SRGBColorSpace;
  const eyes = new THREE.Mesh(
    new THREE.PlaneGeometry(0.4, 0.2),
    new THREE.MeshBasicMaterial({ map: eyesTexture, transparent: true, toneMapped: false, depthWrite: false }),
  );
  eyes.position.set(0, 0.03, 0.222);
  head.add(eyes);
  if (extra === "glasses") {
    for (const side of [-1, 1]) {
      const ring = mesh(new THREE.TorusGeometry(0.062, 0.01, 8, 24), mat("#1d1b20", 0.4), side * 0.1, 0.03, 0.228);
      head.add(ring);
    }
  } else if (extra === "headphones") {
    const band = mesh(new THREE.TorusGeometry(0.27, 0.02, 8, 30, Math.PI), mat("#2f2f35", 0.4), 0, 0.03, 0);
    head.add(band);
    for (const side of [-1, 1]) {
      head.add(mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.06, 20).rotateZ(Math.PI / 2), mat("#3b93f0", 0.5), side * 0.275, 0.0, 0));
    }
  } else if (extra === "beanie") {
    const beanie = mesh(new THREE.SphereGeometry(0.26, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), mat("#e76f51", 0.95), 0, 0.12, 0);
    beanie.scale.set(1.08, 0.8, 1.0);
    head.add(beanie);
    head.add(mesh(new THREE.SphereGeometry(0.055, 12, 10), mat("#f1efe9", 1), 0, 0.34, 0));
  } else if (extra === "bun") {
    head.add(mesh(new THREE.SphereGeometry(0.095, 16, 12), skin, 0, 0.31, -0.06));
  } else if (extra === "cap") {
    const cap = mesh(new THREE.SphereGeometry(0.26, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), mat("#2a9d8f", 0.8), 0, 0.11, 0);
    cap.scale.set(1.06, 0.7, 1.0);
    head.add(cap);
    head.add(mesh(roundedBox(0.26, 0.02, 0.18, 0.008), mat("#2a9d8f", 0.8), 0, 0.115, 0.26));
  }

  const arms = [-1, 1].map((side) => {
    const shoulder = limb(0.05, 0.16, shirt);
    shoulder.position.set(side * 0.2, 0.33, 0);
    spine.add(shoulder);
    const elbow = limb(0.045, 0.14, skin);
    elbow.position.y = -0.21;
    shoulder.add(elbow);
    const hand = mesh(new THREE.SphereGeometry(0.055, 14, 10), skin, 0, -0.2, 0);
    elbow.add(hand);
    return { shoulder, elbow, side };
  });
  const legs = [-1, 1].map((side) => {
    const hip = limb(0.065, 0.2, trousers);
    hip.position.set(side * 0.085, 0, 0);
    hips.add(hip);
    const knee = limb(0.06, 0.2, trousers);
    knee.position.y = -0.28;
    hip.add(knee);
    const foot = mesh(roundedBox(0.11, 0.07, 0.19, 0.03), shoes, 0, -0.32, 0.03);
    knee.add(foot);
    return { hip, knee, side };
  });

  // The selection ring on the floor.
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.32, 0.38, 40),
    new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0, toneMapped: false, depthWrite: false }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.01;
  group.add(ring);

  group.traverse((child) => {
    child.userData.figure = id;
  });

  // The joint angles of now and of the pose it eases toward.
  const now = { lean: 0, hipsY: 0.62, headPitch: 0, headYaw: 0, arms: [0, 0, 0, 0, 0, 0], legs: [0, 0, 0, 0] };
  let pose = "stand";
  let mood = "open";
  let blink = 0;
  let nextBlink = 2 + (seed % 30) / 10;
  let gaze = { x: 0, y: 0 };
  let nextGaze = 1;
  let drawn = "";
  let hover = false;

  /** The joint angles of `pose` at time `t` (seconds) and `phase` of the walk (0 to 1). */
  function target(t, phase) {
    const a = Math.sin(phase * Math.PI * 2);
    const b = Math.cos(phase * Math.PI * 2);
    const breathe = Math.sin(t * 1.6 + seed) * 0.015;
    switch (pose) {
      case "walk":
        return {
          lean: 0.05,
          hipsY: 0.62 + Math.abs(b) * 0.03,
          headPitch: 0,
          headYaw: 0,
          // shoulder pitch L, roll L, elbow L, then R
          arms: [a * 0.55, 0.06, -0.35, -a * 0.55, 0.06, -0.35],
          // hip pitch L, knee L, then R
          legs: [-a * 0.55, Math.max(0, b) * 0.7, a * 0.55, Math.max(0, -b) * 0.7],
        };
      case "type": {
        const tap = Math.sin(t * 14 + seed) * 0.05;
        return { lean: 0.18, hipsY: 0.46, headPitch: 0.22, headYaw: Math.sin(t * 0.4 + seed) * 0.1, arms: [-0.75 + tap, 0.12, -1.0, -0.75 - tap, 0.12, -1.0], legs: [-1.5, 1.45, -1.5, 1.45] };
      }
      case "sit":
        return { lean: -0.05 + breathe, hipsY: 0.46, headPitch: 0.05, headYaw: Math.sin(t * 0.3 + seed) * 0.35, arms: [-0.35, 0.08, -0.8, -0.35, 0.08, -0.8], legs: [-1.5, 1.45, -1.5, 1.45] };
      case "rest":
        return { lean: -0.25 + breathe, hipsY: 0.42, headPitch: -0.05, headYaw: Math.sin(t * 0.25 + seed) * 0.3, arms: [-0.2, 0.35, -0.3, -0.2, 0.35, -0.3], legs: [-1.35, 1.2, -1.45, 1.3] };
      case "sleep":
        return { lean: -0.3, hipsY: 0.42, headPitch: 0.35 + breathe * 3, headYaw: 0.35, arms: [-0.1, 0.25, -0.4, -0.1, 0.25, -0.4], legs: [-1.35, 1.2, -1.45, 1.3] };
      case "wave": {
        const swing = Math.sin(t * 7) * 0.35;
        return { lean: 0, hipsY: 0.62 + Math.abs(Math.sin(t * 3.5)) * 0.02, headPitch: -0.08, headYaw: 0, arms: [0.05, 0.1, -0.15, -0.3, 2.5 + swing, -0.6], legs: [0, 0, 0, 0] };
      }
      case "drink":
        return { lean: 0, hipsY: 0.62 + breathe, headPitch: -0.05 + Math.max(0, Math.sin(t * 0.8 + seed)) * -0.15, headYaw: 0, arms: [0.05, 0.08, -0.1, -0.6, 0.15, -1.9], legs: [0, 0, 0, 0] };
      case "think":
        return { lean: 0.02, hipsY: 0.62 + breathe, headPitch: -0.12, headYaw: 0.25, arms: [0.05, 0.1, -0.1, -0.5, 0.2, -2.1], legs: [0, 0, 0, 0] };
      case "look":
        return { lean: 0, hipsY: 0.62 + breathe, headPitch: -0.08, headYaw: Math.sin(t * 0.35 + seed) * 0.45, arms: [0.08, 0.15, -0.2, 0.08, 0.15, -0.2], legs: [0, 0, 0, 0] };
      default:
        return { lean: 0, hipsY: 0.62 + breathe, headPitch: 0, headYaw: Math.sin(t * 0.3 + seed) * 0.2, arms: [0.05, 0.1, -0.15, 0.05, 0.1, -0.15], legs: [0, 0, 0, 0] };
    }
  }

  function moodOf() {
    switch (pose) {
      case "type":
        return "focus";
      case "wave":
        return "wide";
      case "sleep":
        return "sleep";
      case "drink":
      case "rest":
        return "happy";
      default:
        return "open";
    }
  }

  function drawEyes() {
    const key = `${mood}|${blink.toFixed(2)}|${gaze.x.toFixed(2)}|${gaze.y.toFixed(2)}`;
    if (key === drawn || g === null) return;
    drawn = key;
    paintEyes(g, canvas.width, canvas.height, mood, blink, gaze, light, gold);
    eyesTexture.needsUpdate = true;
  }

  const ease = (from, to, rate, dt) => from + (to - from) * (1 - Math.exp(-rate * dt));

  return {
    group,
    head,
    setPose(next) {
      pose = next;
    },
    update(dt, t, walked) {
      const goal = target(t, (walked / (STRIDE * 2)) % 1);
      const rate = pose === "walk" ? 16 : 7;
      now.lean = ease(now.lean, goal.lean, rate, dt);
      now.hipsY = ease(now.hipsY, goal.hipsY, rate, dt);
      now.headPitch = ease(now.headPitch, goal.headPitch, rate, dt);
      now.headYaw = ease(now.headYaw, goal.headYaw, rate, dt);
      for (let index = 0; index < 6; index += 1) now.arms[index] = ease(now.arms[index], goal.arms[index], rate, dt);
      for (let index = 0; index < 4; index += 1) now.legs[index] = ease(now.legs[index], goal.legs[index], rate, dt);
      hips.position.y = now.hipsY;
      spine.rotation.x = now.lean;
      head.rotation.set(now.headPitch, now.headYaw, 0);
      for (const [index, arm] of arms.entries()) {
        arm.shoulder.rotation.x = now.arms[index * 3];
        // A pose's roll lifts the arm away from the body when positive, on either side.
        arm.shoulder.rotation.z = arm.side * now.arms[index * 3 + 1];
        arm.elbow.rotation.x = now.arms[index * 3 + 2];
      }
      for (const [index, leg] of legs.entries()) {
        leg.hip.rotation.x = now.legs[index * 2];
        leg.knee.rotation.x = now.legs[index * 2 + 1];
      }
      // Blinks, a look about now and then, and the mood of the pose.
      mood = moodOf();
      nextBlink -= dt;
      if (nextBlink <= 0) {
        blink = Math.min(1, blink + dt * 9);
        if (blink >= 1) {
          nextBlink = 2.5 + Math.random() * 4;
        }
      } else {
        blink = Math.max(0, blink - dt * 9);
      }
      nextGaze -= dt;
      if (nextGaze <= 0) {
        nextGaze = 1.5 + Math.random() * 3;
        gaze = pose === "type" ? { x: (Math.random() - 0.5) * 0.6, y: 0.5 } : { x: (Math.random() - 0.5) * 1.4, y: (Math.random() - 0.5) * 0.8 };
      }
      drawEyes();
      const ringMaterial = /** @type {ThreeBasicMaterial} */ (ring.material);
      ringMaterial.opacity = ease(ringMaterial.opacity, hover ? 0.85 : 0, 10, dt);
    },
    setHover(on) {
      hover = on;
    },
    top() {
      const point = new THREE.Vector3(0, 0.36, 0);
      head.localToWorld(point);
      return point;
    },
    dispose() {
      eyesTexture.dispose();
    },
  };
}

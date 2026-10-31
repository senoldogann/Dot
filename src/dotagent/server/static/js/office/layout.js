// The office's plan (the Office view of the Agents page): where the walls, the furniture and the places the agents go
// to are, in metres on the floor, x to the right and z toward the viewer, the room's centre at 0. A pure module: the
// scene (office/scene.js) draws what it says, the walking (office/nav.js) keeps to its free floor, and the agents'
// days (office/brain.js) pick among its places. Its size follows the number of desks: two desks face each other in a
// pod, and the pods stand in rows of two.

/** @typedef {{ x: number, z: number, w: number, d: number }} Rect A footprint: centre and size along x and z. */
/**
 * @typedef {{ id: string, kind: string, x: number, z: number, facing: number, seat: { x: number, z: number } | null,
 *   pose: string }} Spot
 * A place an agent goes to: where it stands (`x`, `z`), which way it looks then (`facing`, radians, 0 toward +z), the
 * seat it sits on there if any, and the pose it takes ("type", "sit", "drink", "look", "think", "rest").
 */
/**
 * @typedef {{ kind: string, x: number, z: number, w: number, d: number, facing: number, solid: boolean, id: string }} Piece
 * A piece of furniture as the scene draws it; `solid` ones are kept out of the walking floor.
 */
/** @typedef {{ width: number, depth: number, pieces: Piece[], spots: Spot[], desks: Spot[] }} Plan */

export const WALL = 0.2;
const DESK_W = 1.5;
const DESK_D = 0.75;
// The room left free of furniture along the walls and between the parts of the room.
const AISLE = 1.4;
const POD_GAP = 1.6;

/** The number of pods that hold `desks` desks: two desks a pod, two pods at least. */
export function podsFor(desks) {
  return Math.max(2, Math.ceil(desks / 2));
}

/**
 * The plan of a room with `desks` desks (one for each agent and one for Dot). The work floor (the pods) takes the
 * right part of the room, the lounge (a sofa, a low table, plants) the front left, the coffee bar and the whiteboard
 * stand on the back wall, the bookshelf on the left wall and a round meeting table at the front right.
 * @param {number} desks
 * @returns {Plan}
 */
export function planFor(desks) {
  const pods = podsFor(desks);
  const rows = Math.ceil(pods / 2);
  const workWidth = 2 * (DESK_W + POD_GAP);
  const workDepth = rows * (2 * DESK_D + 2.4);
  const width = Math.max(14, workWidth + 7.5);
  const depth = Math.max(11, workDepth + 5.2);
  const left = -width / 2;
  const back = -depth / 2;
  /** @type {Piece[]} */
  const pieces = [];
  /** @type {Spot[]} */
  const spots = [];
  /** @type {Spot[]} */
  const deskSpots = [];
  const piece = (kind, x, z, w, d, facing = 0, solid = true, id = `${kind}-${pieces.length}`) => {
    pieces.push({ kind, x, z, w, d, facing, solid, id });
  };

  // The pods: two desks back to back, each with its chair on its own side; the first row nearest the back wall.
  const workLeft = width / 2 - AISLE - workWidth;
  let made = 0;
  for (let pod = 0; pod < pods; pod += 1) {
    const column = pod % 2;
    const row = Math.floor(pod / 2);
    const cx = workLeft + column * (DESK_W + POD_GAP) + DESK_W / 2 + 0.3;
    const cz = back + 3.2 + row * (2 * DESK_D + 2.4) + DESK_D;
    for (const side of [-1, 1]) {
      if (made >= Math.max(desks, 4)) {
        break;
      }
      const dz = cz + side * (DESK_D / 2);
      const id = `desk-${made}`;
      // A desk faces its chair: side -1 is the back desk of the pod, whose chair stands behind it, toward the wall.
      const facing = side < 0 ? Math.PI : 0;
      piece("desk", cx, dz, DESK_W, DESK_D, facing, true, id);
      const seat = { x: cx, z: dz + side * (DESK_D / 2 + 0.38) };
      piece("chair", seat.x, seat.z, 0.55, 0.55, facing + Math.PI, false, `${id}-chair`);
      const desk = {
        id,
        kind: "desk",
        x: cx,
        z: seat.z + side * 0.5,
        facing: facing + Math.PI,
        seat,
        pose: "type",
      };
      deskSpots.push(desk);
      made += 1;
    }
  }

  // The lounge: a sofa against nothing, facing the room, a low table before it, a rug under both.
  const loungeX = left + 3.1;
  const loungeZ = depth / 2 - 2.6;
  piece("rug", loungeX, loungeZ - 0.4, 4.2, 3.0, 0, false);
  piece("sofa", loungeX, loungeZ + 0.55, 2.6, 0.9, Math.PI);
  piece("lowtable", loungeX, loungeZ - 0.8, 1.2, 0.6);
  for (const [index, dx] of [-0.8, 0, 0.8].entries()) {
    spots.push({
      id: `sofa-${index}`,
      kind: "sofa",
      x: loungeX + dx,
      z: loungeZ - 0.05,
      facing: Math.PI,
      seat: { x: loungeX + dx, z: loungeZ + 0.42 },
      pose: "rest",
    });
  }
  piece("armchair", loungeX + 2.0, loungeZ - 0.5, 0.85, 0.85, -Math.PI / 2);
  spots.push({
    id: "armchair",
    kind: "sofa",
    x: loungeX + 1.45,
    z: loungeZ - 0.5,
    facing: -Math.PI / 2,
    seat: { x: loungeX + 1.95, z: loungeZ - 0.5 },
    pose: "rest",
  });

  // The back wall: the coffee bar at the right, the whiteboard in the middle, windows between.
  const barX = width / 2 - 2.4;
  piece("bar", barX, back + 0.45, 2.8, 0.7);
  for (const [index, dx] of [-0.7, 0.7].entries()) {
    spots.push({ id: `coffee-${index}`, kind: "coffee", x: barX + dx, z: back + 1.25, facing: Math.PI, seat: null, pose: "drink" });
  }
  const boardX = left + width * 0.42;
  piece("whiteboard", boardX, back + 0.12, 2.2, 0.12, 0, false);
  spots.push({ id: "whiteboard", kind: "board", x: boardX - 0.4, z: back + 1.1, facing: Math.PI, seat: null, pose: "think" });
  spots.push({ id: "whiteboard-2", kind: "board", x: boardX + 0.5, z: back + 1.15, facing: Math.PI, seat: null, pose: "look" });

  // The left wall: the bookshelf, and a window to look out of.
  piece("shelf", left + 0.35, back + 3.0, 0.5, 2.2, Math.PI / 2);
  spots.push({ id: "shelf", kind: "shelf", x: left + 1.2, z: back + 3.0, facing: -Math.PI / 2, seat: null, pose: "look" });
  spots.push({ id: "window-left", kind: "window", x: left + 1.1, z: back + 5.6, facing: -Math.PI / 2, seat: null, pose: "look" });
  spots.push({ id: "window-back", kind: "window", x: left + 2.6, z: back + 1.1, facing: Math.PI, seat: null, pose: "look" });

  // The front right: a round meeting table with four chairs.
  const meetX = width / 2 - 2.2;
  const meetZ = depth / 2 - 2.0;
  piece("roundtable", meetX, meetZ, 1.3, 1.3);
  for (let index = 0; index < 4; index += 1) {
    const angle = (index / 4) * Math.PI * 2 + Math.PI / 4;
    const seat = { x: meetX + Math.sin(angle) * 1.05, z: meetZ + Math.cos(angle) * 1.05 };
    piece("chair", seat.x, seat.z, 0.55, 0.55, angle + Math.PI, false, `meet-chair-${index}`);
    spots.push({
      id: `meet-${index}`,
      kind: "meeting",
      x: meetX + Math.sin(angle) * 1.6,
      z: meetZ + Math.cos(angle) * 1.6,
      facing: angle + Math.PI,
      seat,
      pose: "sit",
    });
  }

  // Plants in the corners and beside the sofa, a lamp by the armchair.
  piece("plant", left + 0.6, back + 0.6, 0.6, 0.6);
  piece("plant", width / 2 - 0.6, depth / 2 - 0.6, 0.6, 0.6);
  piece("plant", loungeX - 1.75, loungeZ + 0.55, 0.5, 0.5);
  piece("plant", workLeft - 0.5, back + 0.6, 0.5, 0.5);
  piece("lamp", loungeX + 2.0, loungeZ + 0.4, 0.4, 0.4);

  return { width, depth, pieces, spots, desks: deskSpots };
}

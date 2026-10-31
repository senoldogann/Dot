// How an agent of the office walks (the Office view of the Agents page): the room's floor as a grid of cells, the
// solid furniture of its plan (office/layout.js) and the walls kept out with a margin, A* over the free cells in eight
// directions, and the path cut down to the corners a straight walk cannot skip. A pure module.

/** @typedef {{ x: number, z: number }} Point */
/** @typedef {{ cell: number, cols: number, rows: number, left: number, back: number, free: Uint8Array }} Grid */

const CELL = 0.25;
// How far from a solid piece and from a wall an agent's centre keeps.
const MARGIN = 0.3;
const SQRT2 = Math.SQRT2;

/**
 * The walking floor of `plan`: a cell is free unless a solid piece (grown by the margin) or a wall's margin covers its
 * centre.
 * @param {{ width: number, depth: number, pieces: { x: number, z: number, w: number, d: number, facing: number, solid: boolean }[] }} plan
 * @returns {Grid}
 */
export function gridFor(plan) {
  const cols = Math.round(plan.width / CELL);
  const rows = Math.round(plan.depth / CELL);
  const left = -plan.width / 2;
  const back = -plan.depth / 2;
  const free = new Uint8Array(cols * rows).fill(1);
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const x = left + (col + 0.5) * CELL;
      const z = back + (row + 0.5) * CELL;
      const nearWall = x < left + MARGIN || z < back + MARGIN || x > -left - MARGIN || z > -back - MARGIN;
      if (nearWall || plan.pieces.some((piece) => piece.solid && covers(piece, x, z, MARGIN))) {
        free[row * cols + col] = 0;
      }
    }
  }
  return { cell: CELL, cols, rows, left, back, free };
}

/** Whether the footprint of `piece`, turned by its facing and grown by `margin`, covers the point. */
export function covers(piece, x, z, margin) {
  const turned = Math.abs(Math.sin(piece.facing)) > 0.7;
  const halfW = (turned ? piece.d : piece.w) / 2 + margin;
  const halfD = (turned ? piece.w : piece.d) / 2 + margin;
  return Math.abs(x - piece.x) <= halfW && Math.abs(z - piece.z) <= halfD;
}

/** The cell of a point, clamped into the grid. */
export function cellOf(grid, point) {
  const col = Math.min(grid.cols - 1, Math.max(0, Math.floor((point.x - grid.left) / grid.cell)));
  const row = Math.min(grid.rows - 1, Math.max(0, Math.floor((point.z - grid.back) / grid.cell)));
  return row * grid.cols + col;
}

/** The centre of a cell. */
function centreOf(grid, index) {
  const col = index % grid.cols;
  const row = Math.floor(index / grid.cols);
  return { x: grid.left + (col + 0.5) * grid.cell, z: grid.back + (row + 0.5) * grid.cell };
}

/** The free cell nearest to `point` (itself when free), by rings of growing size; -1 for a grid with none. */
function nearestFree(grid, point) {
  const start = cellOf(grid, point);
  if (grid.free[start]) {
    return start;
  }
  const col0 = start % grid.cols;
  const row0 = Math.floor(start / grid.cols);
  const reach = Math.max(grid.cols, grid.rows);
  for (let ring = 1; ring < reach; ring += 1) {
    let best = -1;
    let bestDistance = Infinity;
    for (let row = row0 - ring; row <= row0 + ring; row += 1) {
      for (let col = col0 - ring; col <= col0 + ring; col += 1) {
        const onRing = Math.max(Math.abs(row - row0), Math.abs(col - col0)) === ring;
        if (!onRing || row < 0 || col < 0 || row >= grid.rows || col >= grid.cols || !grid.free[row * grid.cols + col]) {
          continue;
        }
        const centre = centreOf(grid, row * grid.cols + col);
        const distance = (centre.x - point.x) ** 2 + (centre.z - point.z) ** 2;
        if (distance < bestDistance) {
          best = row * grid.cols + col;
          bestDistance = distance;
        }
      }
    }
    if (best >= 0) {
      return best;
    }
  }
  return -1;
}

/** Whether a straight walk from `a` to `b` stays on free cells, checked every half cell. */
export function clearLine(grid, a, b) {
  const length = Math.hypot(b.x - a.x, b.z - a.z);
  const steps = Math.max(1, Math.ceil(length / (grid.cell / 2)));
  for (let step = 0; step <= steps; step += 1) {
    const t = step / steps;
    if (!grid.free[cellOf(grid, { x: a.x + (b.x - a.x) * t, z: a.z + (b.z - a.z) * t })]) {
      return false;
    }
  }
  return true;
}

/**
 * The walk from `from` to `to` over free floor: the corners to pass, the last of them the free point nearest to `to`
 * (an agent then steps from there to a seat or a spot the furniture's margin covers). Empty when there is no way; a
 * diagonal step never cuts the corner of a blocked cell.
 * @param {Grid} grid
 * @param {Point} from
 * @param {Point} to
 * @returns {Point[]}
 */
export function findPath(grid, from, to) {
  const start = nearestFree(grid, from);
  const goal = nearestFree(grid, to);
  if (start < 0 || goal < 0) {
    return [];
  }
  const size = grid.cols * grid.rows;
  const cost = new Float32Array(size).fill(Infinity);
  const came = new Int32Array(size).fill(-1);
  const closed = new Uint8Array(size);
  const open = new MinHeap();
  const goalCentre = centreOf(grid, goal);
  const guess = (index) => {
    const c = centreOf(grid, index);
    const dx = Math.abs(c.x - goalCentre.x) / grid.cell;
    const dz = Math.abs(c.z - goalCentre.z) / grid.cell;
    return Math.max(dx, dz) + (SQRT2 - 1) * Math.min(dx, dz);
  };
  cost[start] = 0;
  open.push(start, guess(start));
  while (open.size > 0) {
    const current = open.pop();
    if (current === goal) {
      break;
    }
    if (closed[current]) {
      continue;
    }
    closed[current] = 1;
    const col = current % grid.cols;
    const row = Math.floor(current / grid.cols);
    for (let dr = -1; dr <= 1; dr += 1) {
      for (let dc = -1; dc <= 1; dc += 1) {
        if ((dr === 0 && dc === 0) || row + dr < 0 || col + dc < 0 || row + dr >= grid.rows || col + dc >= grid.cols) {
          continue;
        }
        const next = (row + dr) * grid.cols + col + dc;
        if (!grid.free[next] || closed[next]) {
          continue;
        }
        if (dr !== 0 && dc !== 0 && (!grid.free[row * grid.cols + col + dc] || !grid.free[(row + dr) * grid.cols + col])) {
          continue;
        }
        const step = dr !== 0 && dc !== 0 ? SQRT2 : 1;
        if (cost[current] + step < cost[next]) {
          cost[next] = cost[current] + step;
          came[next] = current;
          open.push(next, cost[next] + guess(next));
        }
      }
    }
  }
  if (start === goal) {
    return [centreOf(grid, goal)];
  }
  if (came[goal] < 0) {
    return [];
  }
  const cells = [goal];
  while (cells[cells.length - 1] !== start) {
    cells.push(came[cells[cells.length - 1]]);
  }
  cells.reverse();
  return smooth(grid, [from, ...cells.slice(1).map((index) => centreOf(grid, index))]).slice(1);
}

/** The points of a walk with every one a straight line from the last kept point can skip left out. */
function smooth(grid, points) {
  if (points.length <= 2) {
    return points;
  }
  const kept = [points[0]];
  let anchor = 0;
  for (let index = 2; index < points.length; index += 1) {
    if (!clearLine(grid, points[anchor], points[index])) {
      kept.push(points[index - 1]);
      anchor = index - 1;
    }
  }
  kept.push(points[points.length - 1]);
  return kept;
}

/** A binary heap of cell indexes by priority, the lowest first. */
class MinHeap {
  constructor() {
    /** @type {number[]} */
    this.items = [];
    /** @type {number[]} */
    this.keys = [];
  }

  get size() {
    return this.items.length;
  }

  push(item, key) {
    this.items.push(item);
    this.keys.push(key);
    let at = this.items.length - 1;
    while (at > 0) {
      const parent = (at - 1) >> 1;
      if (this.keys[parent] <= this.keys[at]) {
        break;
      }
      this.swap(at, parent);
      at = parent;
    }
  }

  pop() {
    const top = this.items[0];
    const lastItem = this.items.pop();
    const lastKey = this.keys.pop();
    if (this.items.length > 0) {
      this.items[0] = lastItem;
      this.keys[0] = lastKey;
      let at = 0;
      for (;;) {
        const left = 2 * at + 1;
        const right = left + 1;
        let least = at;
        if (left < this.items.length && this.keys[left] < this.keys[least]) least = left;
        if (right < this.items.length && this.keys[right] < this.keys[least]) least = right;
        if (least === at) break;
        this.swap(at, least);
        at = least;
      }
    }
    return top;
  }

  swap(a, b) {
    [this.items[a], this.items[b]] = [this.items[b], this.items[a]];
    [this.keys[a], this.keys[b]] = [this.keys[b], this.keys[a]];
  }
}

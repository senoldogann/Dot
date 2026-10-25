// The versions of a learned skill, said in words and compared (item 585): what the Skills page shows of how a version came
// to be, and the steps of one version against the steps of another, as a diff the owner reads. Pure: no page, no network.

/**
 * @typedef {object} StepChange
 * @property {" " | "+" | "-"} sign " " for a step both have, "-" for one only the earlier has, "+" for one only the later has
 * @property {string} text
 */

/**
 * The steps of `after` against those of `before`, each step once and in order: the ones both have, the ones only
 * `before` has (dropped) and the ones only `after` has (added). A step that was reworded is the old one dropped and the
 * new one added, the dropped one first.
 * @param {readonly string[]} before
 * @param {readonly string[]} after
 * @returns {StepChange[]}
 */
export function stepChanges(before, after) {
  // common[i][j]: how many steps `before` from i and `after` from j have in common, in order.
  const common = Array.from({ length: before.length + 1 }, () => new Array(after.length + 1).fill(0));
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      common[i][j] = before[i] === after[j] ? common[i + 1][j + 1] + 1 : Math.max(common[i + 1][j], common[i][j + 1]);
    }
  }
  /** @type {StepChange[]} */
  const changes = [];
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      changes.push({ sign: " ", text: before[i] });
      i += 1;
      j += 1;
    } else if (common[i + 1][j] >= common[i][j + 1]) {
      changes.push({ sign: "-", text: before[i] });
      i += 1;
    } else {
      changes.push({ sign: "+", text: after[j] });
      j += 1;
    }
  }
  for (; i < before.length; i += 1) {
    changes.push({ sign: "-", text: before[i] });
  }
  for (; j < after.length; j += 1) {
    changes.push({ sign: "+", text: after[j] });
  }
  return changes;
}

/**
 * How many steps `changes` keeps, adds and drops.
 * @param {readonly StepChange[]} changes
 * @returns {{ kept: number, added: number, dropped: number }}
 */
export function countChanges(changes) {
  const count = { kept: 0, added: 0, dropped: 0 };
  for (const change of changes) {
    if (change.sign === " ") {
      count.kept += 1;
    } else if (change.sign === "+") {
      count.added += 1;
    } else {
      count.dropped += 1;
    }
  }
  return count;
}

/**
 * How a version came to be, in the words the page shows: its change, and for a merge the skills it took in, for a
 * restore the version it put back, and for a revision the owner's words.
 * @param {{ change: string, from_version: number | null, merged: readonly string[], words: string | null }} note
 * @returns {string}
 */
export function describeVersion(note) {
  switch (note.change) {
    case "learned":
      return "Learned from a task";
    case "relearned":
      return "Rewritten by a later task";
    case "taught":
      return "Taught by you";
    case "restored":
      return `Restored from version ${note.from_version}`;
    case "merged":
      return note.merged.length > 0 ? `Merged with ${note.merged.join(", ")}` : "Merged with another skill";
    case "revised":
      return note.words ? `Revised at your word: ${note.words}` : "Revised at your word";
    default:
      return note.change;
  }
}

/**
 * What the version's mark of content third parties can author says, or null for a version offered as written: `tainted`
 * is true for steps a run that read such content wrote, null for one of unknown origin (a record older than the mark).
 * @param {boolean | null} tainted
 * @returns {string | null}
 */
export function markOf(tainted) {
  if (tainted === null) {
    return "Origin unknown";
  }
  return tainted ? "From outside content" : null;
}

/**
 * The steps a proposal writes, in a line: how many, and how many of them are new, how many the skill it replaces drops
 * and how many it keeps.
 * @param {readonly { field: string, lines: readonly StepChange[] }[]} fields the fields the yes writes
 * @param {string} first the name of the skill they are written to
 * @returns {string}
 */
export function stepsSummary(fields, first) {
  const steps = fields.find((field) => field.field === "steps");
  const counts = countChanges(steps === undefined ? [] : steps.lines);
  const written = counts.kept + counts.added;
  return `${written} ${written === 1 ? "step" : "steps"}: ${counts.added} new, ${counts.dropped} dropped, ${counts.kept} kept from ${first}.`;
}

/**
 * What a proposal to change learned skills does, as the card's title says it.
 * @param {{ kind: string, names: readonly string[] }} proposal
 * @returns {string}
 */
export function proposalTitle(proposal) {
  return proposal.kind === "merge" ? `Merge ${proposal.names.join(" and ")}` : `Change ${proposal.names[0]}`;
}

/**
 * What happens to each skill of a proposal when the owner says yes, in words: the first skill gets everything the card
 * shows (name, description, kind of work, when, steps, parameters) as a new version, and a merge puts each other skill to
 * rest, kept whole.
 * @param {{ kind: string, names: readonly string[] }} proposal
 * @returns {string[]}
 */
export function proposalEffects(proposal) {
  const [first, ...others] = proposal.names;
  const written = `${first} gets all of the above as a new version; the file it has now is kept as the version before.`;
  if (proposal.kind !== "merge") {
    return [written];
  }
  return [
    written,
    ...others.map((name) => `${name} rests, kept whole: wake it to offer it again, or restore ${first}'s earlier version to undo the merge.`),
  ];
}

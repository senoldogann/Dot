// Delegated runs under the run that delegated them (item 68): the rows of one section, nested.

/**
 * The rows of a section as parents with their children: a row whose `parent_run_id` names another row of the section
 * hangs under that row; every other row stands on its own, in the order given. A run with several rows (the steps it
 * kept for you, the questions it left) shows each, the first of them taking its delegated runs. Returns
 * `[{ row, children }]`.
 */
export function nestRows(rows) {
  const byRun = new Map();
  for (const row of rows) {
    if (row.run_id !== null && !byRun.has(row.run_id)) {
      byRun.set(row.run_id, { row, children: [] });
    }
  }
  const top = [];
  for (const row of rows) {
    const parent = row.parent_run_id === null ? undefined : byRun.get(row.parent_run_id);
    if (parent !== undefined && parent.row.run_id !== row.run_id) {
      parent.children.push(row);
    } else {
      const own = row.run_id === null ? undefined : byRun.get(row.run_id);
      top.push(own !== undefined && own.row === row ? own : { row, children: [] });
    }
  }
  return top;
}

/** The facts of a nested row's progress: its steps, then its channel. Its detail (that another run delegated it) is
 * what its place under that run says already (item 198). */
export function progressFacts(row) {
  const parts = [];
  if (row.steps !== null && row.steps > 0) {
    parts.push(row.steps === 1 ? "1 step" : `${row.steps} steps`);
  }
  if (row.channel !== "") {
    parts.push(row.channel);
  }
  return parts;
}

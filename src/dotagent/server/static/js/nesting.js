// Delegated runs under the run that delegated them (item 68): the rows of one section, nested.

/**
 * The rows of a section as parents with their children: a row whose `parent_run_id` names another row of the section
 * hangs under that row; every other row stands on its own, in the order given. Returns `[{ row, children }]`.
 */
export function nestRows(rows) {
  const byRun = new Map(rows.filter((row) => row.run_id !== null).map((row) => [row.run_id, { row, children: [] }]));
  const top = [];
  for (const row of rows) {
    const parent = row.parent_run_id === null ? undefined : byRun.get(row.parent_run_id);
    if (parent !== undefined && parent.row !== row) {
      parent.children.push(row);
    } else {
      top.push(byRun.get(row.run_id) ?? { row, children: [] });
    }
  }
  return top;
}

/** What a row says about its progress: its steps, then its channel and detail, joined with dots. */
export function progressWords(row) {
  const parts = [];
  if (row.steps !== null && row.steps > 0) {
    parts.push(row.steps === 1 ? "1 step" : `${row.steps} steps`);
  }
  if (row.channel !== "") {
    parts.push(row.channel);
  }
  if (row.detail !== "") {
    parts.push(row.detail);
  }
  return parts.join(" · ");
}

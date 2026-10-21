// The rows of one section of the sidebar, with the runs each delegated (item 68): a row's `parent_run_id` names the row
// it belongs to, which wears the faces of those runs (item 507).

/**
 * The rows of a section as parents with their children: a row whose `parent_run_id` names another row of the section
 * belongs to that row; every other row stands on its own, in the order given. A run with several rows (the steps it
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

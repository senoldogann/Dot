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

/**
 * Where the runs at work stand (item 202). `rows` are the rows of Working with the delegated runs that ended under
 * them, `chats` the chats listed. A chat whose latest run is one of them stands once, as that chat, with the runs it
 * delegated: under Working (`working`, `{ chat, row, children }`), unless it is pinned, which keeps it under Pinned
 * (`pinned`, its family by chat id). A row that is no listed chat's (a run whose chat is not listed, a delegated run
 * whose parent is not) stands under Working as it is, its `chat` null.
 */
export function placeWork(rows, chats) {
  const byRun = new Map(chats.map((chat) => [chat.latest_run_id, chat]));
  const byId = new Map(chats.map((chat) => [chat.id, chat]));
  const families = new Map();
  const loose = [];
  const orphans = [];
  for (const family of nestRows(rows)) {
    const chat = family.row.run_id === null ? undefined : byRun.get(family.row.run_id);
    if (chat !== undefined) {
      families.set(chat.id, { chat, row: family.row, children: family.children });
    } else if (family.row.parent_run_id !== null && byId.has(family.row.root_thread_id)) {
      orphans.push(family);
    } else {
      loose.push({ chat: null, row: family.row, children: family.children });
    }
  }
  // A run another run delegated whose parent works no more (a work order's attempt outlives the lead's turn that gave
  // it, item 1074) stands under the chat its work came from, which works while it does, and not as a row of its own.
  for (const family of orphans) {
    const chat = byId.get(family.row.root_thread_id);
    const kept = families.get(chat.id);
    if (kept === undefined) {
      // The chat's own run has ended: while what it handed out works, the chat stands at work.
      const atWork = { ...chat, latest_status: family.row.status };
      families.set(chat.id, { chat: atWork, row: { ...family.row, title: chat.title }, children: [family.row, ...family.children] });
    } else {
      kept.children.push(family.row, ...family.children);
    }
  }
  const working = [...loose];
  const pinned = new Map();
  for (const entry of families.values()) {
    if (entry.chat.pinned_at === null) {
      working.push(entry);
    } else {
      pinned.set(entry.chat.id, { row: entry.row, children: entry.children });
    }
  }
  return { working, pinned };
}

// The settings' rows (settingstabs.js): the sidebar has no Schedules icon (item 1328), so its page, Scheduled tasks
// (item 1337), stands among the rows of the settings, on a Mac's nav and the phone's sheet alike.

import assert from "node:assert/strict";
import { test } from "node:test";

import { SETTINGS_GROUPS, tabOf } from "../../src/dotagent/server/static/js/settingstabs.js";

test("Settings holds a row that leads to Scheduled tasks, the one way in from the shell", () => {
  const rows = SETTINGS_GROUPS.flatMap((group) => group.rows);
  const schedules = rows.find((row) => row.tab === "schedules");
  assert.ok(schedules, "no row leads to Scheduled tasks");
  assert.equal(tabOf("schedules")?.label, "Scheduled tasks");
  assert.equal(typeof tabOf("schedules")?.icon, "string");
  assert.ok(schedules.line.length > 0, "the phone's sheet shows a line under the name");
});

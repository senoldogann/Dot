// The settings' rows (settingstabs.js): the sidebar has no Schedules icon (item 1328), so its page stands among the rows
// the settings lead to, on a Mac's nav and the phone's sheet alike.

import assert from "node:assert/strict";
import { test } from "node:test";

import { SETTINGS_GROUPS } from "../../src/dotagent/server/static/js/settingstabs.js";

test("Settings holds a row that leads to the Schedules page, the one way in from the shell", () => {
  const rows = SETTINGS_GROUPS.flatMap((group) => group.rows);
  const schedules = rows.find((row) => row.href === "#/schedules");
  assert.ok(schedules, "no row leads to #/schedules");
  assert.equal(schedules.label, "Schedules");
  assert.equal(typeof schedules.icon, "string");
  assert.ok(schedules.line.length > 0, "the phone's sheet shows a line under the name");
});

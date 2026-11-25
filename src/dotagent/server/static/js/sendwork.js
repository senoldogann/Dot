// A message for a chat whose run works (items 898, 1233 and 1245): the agent page's box and the home's send
// it as one sent in that chat goes. Its own module, so that the home does not load the agents' pages to send.

import { api } from "./api.js";
import { BUSY_SEND, settingValue } from "./appearance/settings.js";
import { toast } from "./ui.js";

/**
 * Send `text` to the run at work of `where` (items 898 and 1245), as a message in its chat goes: it waits in the chat's
 * queue, or steers the run when Settings › General › Messages says so. Whether there was such a run: none, and the
 * caller starts one.
 * @param {{ where: { threadId: string | null, working: string | null }, text: string, choices: any, frame: any, owner: object, keys: any, ctx: { queued?: (runId: string, text: string) => void, steered?: (runId: string, text: string) => void } }} options
 */
export async function sendToWork({ where, text, choices, frame, owner, keys, ctx }) {
  const { attachments } = frame;
    if (where.working !== null && where.threadId !== null && settingValue(BUSY_SEND) !== "steer") {
      // The agent works and Settings › General › Messages says to queue (item 230, the default): the message waits in
      // its chat's queue for the work under way to end, as one sent in the chat does (the review of item 898).
      await choices.ready();
      const chosen = choices.value();
      const body = {
        text,
        mode: chosen.mode,
        model: chosen.model,
        effort: chosen.effort,
        speed: chosen.speed,
        once: chosen.once,
        approvals: chosen.approvals,
        goal_mode: chosen.goal_mode,
        attachments: attachments.ids(),
        ...owner,
        ...frame.picks.body(),
      };
      await api.post(`/threads/${encodeURIComponent(where.threadId)}/queue`, { ...body, key: keys.of("queue", where.threadId, body) });
      keys.sent();
      choices.sent();
      frame.picks.sent();
      frame.clear();
      ctx.queued?.(where.working, text);
      return true;
    }
    if (where.working !== null) {
      // The agent works and the owner chose Steer: the message reaches that run, as one sent in its chat does. A
      // steer joins the run under way, which keeps what it has (item 259): picks wait for a new task, and say so.
      if (frame.picks.count() > 0) {
        toast("ok", "Sent to the task under way, which keeps what it has. Plugins, skills and Plan first wait for a message that starts a new task.");
      }
      const steer = { text, attachments: attachments.ids() };
      await api.post(`/runs/${encodeURIComponent(where.working)}/steer`, { ...steer, key: keys.of("steer", where.working, steer) });
      keys.sent();
      frame.clear();
      ctx.steered?.(where.working, text);
      return true;
    }
  return false;
}

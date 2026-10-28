# Captcha and human checks

Dot solves the human check in front of it — hCaptcha, reCAPTCHA, Cloudflare Turnstile, "Verify you are human" — in
its own Chrome, from the page's screenshot, and then carries on with the task. It does not stop to ask the owner; that
is a standing instruction.

## How it is solved

- `browser_screenshot` gives a JPEG of the viewport; one pixel of it is one CSS pixel of the page.
- `browser_act` with `click_at` presses a checkbox or an image tile by its pixel.
- `browser_act` with `drag_at` presses at one pixel and lets go at another: an hCaptcha "drag the shape to the loop
  that is closed" puzzle, or a slider.
- After a click or a drag the page is read again; a captcha redraws its tiles and moves its pieces, so the next look
  is a new screenshot.

The method is the built-in `captcha` skill (`src/dotagent/skills/builtin/captcha/SKILL.md`): the checkbox, the image
grid, the drag puzzle, Cloudflare's wait, the three-attempt limit, and what a failure looks like. The system prompt
says the same in one line, so a run does not turn a check into a question.

## What it is not

- Not a CAPTCHA farm or a solving service: the check is solved in the owner's own session, by looking at it. No image
  or token leaves the Mac for a third party.
- Not a way past a site's access control for a flow the owner is not in: this is for his own application, his own
  account, his own form. It does not create accounts or send spam.
- Not an exploit: a Cloudflare block ("Sorry, you have been blocked", a Ray ID, no checkbox) is reported, not
  hammered. A WAF, a rate limit and a login wall stay walls.

## When it fails

After three honest attempts (a fresh screenshot, other tiles, the other closed loop, a new tab for Cloudflare, the
audio challenge once) the tab is left open and the result says what is left. A check is a step in the work, not a
reason to sit on the page until it times out.

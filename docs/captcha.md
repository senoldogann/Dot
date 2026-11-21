# Captcha and human checks

Dot solves the human check in front of it — hCaptcha, reCAPTCHA, Cloudflare Turnstile, a site's own "Press & Hold" or
"Verify you are human" — in its own Chrome, and then carries on with the task. It does not stop to ask the owner; that
is a standing instruction.

## How it is solved

- `browser_captcha` finds the check itself, in the page or in any frame of it, and passes the mechanical part in one
  call. It reads the DOM of every frame for the widgets it knows (hCaptcha, reCAPTCHA, Cloudflare Turnstile) and for a
  pressable button that says what it wants ("Press & Hold", "Verify you are human", "I'm not a robot"), takes each
  box in CSS pixels of the main frame's viewport, presses the checkbox or holds the button down (a hold jiggles the
  pointer once and lets go when the widget stops asking for it), and reads the page again every 120 ms until the check
  clears: the widget gone, its box checked, the site's token set, or the widget's own success words. A page that is
  still checking itself (Cloudflare's interstitial, Turnstile's spinner) is watched the same way, and the call returns
  the moment it goes on.
- What needs eyes comes back from that call as the check's own picture at twice its size, the box it came from, the
  tile count when the widget names it, and the box of its Verify / Next button. `browser_act` with `click_at` presses a
  tile by its pixel, `drag_at` presses at one pixel and lets go at another: an hCaptcha "drag the shape to the loop
  that is closed" puzzle, or a slider.
- `browser_screenshot` is still there for a check the call does not know: a JPEG of the viewport whose pixels are CSS
  pixels, with each interactive element marked by its snapshot ref at its corner (item 1261).

The method is the built-in `captcha` skill (`src/dotagent/skills/builtin/captcha/SKILL.md`): the first call, the image
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

`browser_captcha` answers `unfinished` with the check's picture as it stands. After three honest attempts (other tiles,
the other closed loop, a new tab for Cloudflare, the audio challenge once) the tab is left open and the result says what
is left. A check is a step in the work, not a reason to sit on the page until it times out.

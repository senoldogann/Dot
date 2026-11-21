---
name: captcha
description: "Pass an hCaptcha, reCAPTCHA, Cloudflare Turnstile, a 'Press & Hold' button or another human check in Dot's Chrome by calling browser_captcha, which presses or holds the widget itself, and only what it hands back as a picture is solved by looking and clicking or dragging, without stopping to ask the owner. Use whenever a page says 'Verify you are human', 'I am human', 'Press & Hold', 'Checking your browser' or shows a captcha frame."
---

# The human check

A human check is part of the task, not the end of it. The owner has said, as a standing instruction, that Dot solves
these itself and does not stop to ask. `browser_captcha` does the mechanical part in one call; this skill is about what
is left after it.

This is for the owner's own Chrome session and the flow he is already in (his job application, his account, his form).
It is not for mass account creation, spam, scraping a site that forbids it, or a captcha farm.

## First call, every time

`browser_captcha` (with no arguments to start; `timeout_s` up to 30 if a check is slow). One call does what a
screenshot, a guess at a pixel and a sleep used to do:

- It finds the widget in the page or in any frame of it - hCaptcha, reCAPTCHA, Cloudflare Turnstile, or the site's own
  "Press & Hold" / "Verify you are human" button - and says where it is in page pixels.
- It presses a checkbox, or **holds** a "Press & Hold" button down, and lets go when the widget says it took it.
- It reads the page again a few times a second until the check clears, and stops the moment it does. The answer is
  `passed` (carry on), `challenge` (it needs eyes), `unfinished` (pressed, still there) or `none` (there is no check:
  do not go looking for one with a screenshot).
- A check that passed comes with the page as it stands after it, so the next step is often the one after this call.

Do not take a screenshot to find a captcha, and do not guess at a pixel, before you have called it.

## What the picture is, when it answers `challenge`

The result carries the check's own picture at **twice** its size, with the box it came from: its top-left corner is the
`x, y` the answer names, and every 2 pixels of the picture are 1 pixel of the page. The answer also names the tiles'
count when the widget says it (3 by 3, 4 by 4) and the box of the challenge's **Verify** / **Next** button.

| The picture shows | It is | What to do |
| --- | --- | --- |
| "Please click each image containing …", a grid | reCAPTCHA / hCaptcha image challenge | click each matching tile at its centre with `click_at`, press the Verify box the answer named in the same batch, then call `browser_captcha` again |
| "Please drag the shape to the loop that is closed" | hCaptcha drag puzzle | `drag_at` the shape into the closed loop |
| An audio button, headphones or "Get an audio challenge" | audio fallback | use it only after the visual way failed twice |

Tiles and checkboxes have a margin; the **centre** is safe. From the answer's `x, y` and the tile count, the centre of
tile (i, j) is `x + (i + 0.5) * width / cols`, `y + (j + 0.5) * height / rows`. Batch every matching tile of one grid
into one `browser_act` call and end it with the Verify box: a captcha redraws after a stray click, and every extra
round trip is a chance to lose it.

For the drag puzzle, the shape is usually at the bottom and the targets are outlines above it. Take the outline that is
**closed** (no gap) and matches the shape's curve; drag from the shape's centre, in one smooth line, and let go in the
loop's centre. If it snaps back, start 2–3 px inside the shape or take the next closed loop.

## After a pass, and when it does not pass

`browser_captcha` answers `passed` only when the page says so (the widget is gone, its box is checked, the site's token
is set, or the check's own words say success). Go on with the task from the page the answer carries: the form's Send,
the next page, the upload.

An `unfinished` answer comes with the check's picture as it stands: look at it. A widget whose inside could not be read
(rare), a grid that opened without a prompt, a spinner that never ended - solve it from the picture with `click_at` or
`drag_at`, then call `browser_captcha` again. Three honest attempts per widget is the limit; then leave the tab open
and say in the result what is left. Do not ask the owner to solve it and do not sit on the page until it times out.

## The details that decide it

- **Cloudflare judges the whole browser, not the click.** Dot's own Chrome is a persistent, visible profile and passes
  most managed checks with no puzzle. `browser_captcha` waits for it and stops as soon as the page goes on; if it
  answers `unfinished`, open the same URL once in a new tab and call `browser_captcha` there. Never reload in a loop: a
  reload is a new challenge and looks like a bot.
- **Never** ship the challenge to a solving service, paste its images into a chat, or use a CAPTCHA farm. The check is
  solved here, in the owner's own session, by looking at it.
- **Never** solve a check to create accounts, send spam, or get past a site's access control for a flow the owner is
  not in. The owner's own application is the case this skill is for.

## If the page is a Cloudflare block, not a check

"Sorry, you have been blocked" or a Ray ID with no checkbox is not a puzzle: the site refused this browser or this IP.
`browser_captcha` answers `none` there, or `unfinished` with the block's own words. Do not hammer it. Try the same URL
once in a new tab; if it is still blocked, say so with the Ray ID and the page, and finish the part that does not need
that site.

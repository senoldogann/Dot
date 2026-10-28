---
name: captcha
description: "Pass an hCaptcha, reCAPTCHA or Cloudflare Turnstile human check in Dot's Chrome by looking at the page and clicking or dragging, without stopping to ask the owner: a checkbox, an image grid, a drag puzzle or a managed challenge. Use whenever a page says 'Verify you are human', 'I am human', 'Checking your browser' or shows a captcha frame."
---

# The human check

A human check is part of the task, not the end of it. The owner has said, as a standing instruction, that Dot solves
these itself and does not stop to ask: solve it with the browser's screenshot and pixel actions, then carry on with
the work.

This is for the owner's own Chrome session and the flow he is already in (his job application, his account, his form).
It is not for mass account creation, spam, scraping a site that forbids it, or a captcha farm.

## What is in front of you

| The page shows | It is | What to do |
| --- | --- | --- |
| A box with "I am human" / "I'm not a robot" | hCaptcha / reCAPTCHA v2 checkbox | `click_at` its centre, wait 2 s, take a screenshot again |
| "Please click each image containing …", a 3×3 or 4×4 grid | reCAPTCHA / hCaptcha image challenge | click each matching tile, then Verify/Next; repeat if it refreshes |
| "Please drag the shape to the loop that is closed" | hCaptcha drag puzzle | `drag_at` the shape into the closed loop |
| "Verify you are human" in a small Cloudflare box | Turnstile | `click_at` the checkbox, wait 10–15 s; often no puzzle follows |
| "Checking your browser before accessing…" | Cloudflare managed challenge | wait 10–15 s; if it does not pass, open the same URL in a new tab and wait again |
| An audio button, headphones or "Get an audio challenge" | audio fallback | use it only after the visual way failed twice |

The widget is usually a cross-origin iframe. A ref cannot reach inside it, so `browser_snapshot` may show only the
frame. That is why the screenshot and pixel coordinates are the way in: a pixel of `browser_screenshot` is a CSS pixel
of the page, and `click_at` / `drag_at` use the same pixels.

## The loop

1. **Wait for the widget** (2–5 s after the page loads), then `browser_screenshot`. If it is below the fold, scroll it
   into view first, then screenshot again.
2. **The checkbox.** `browser_act` with `click_at` at the centre of the box. Wait 2 s and screenshot: either the check
   turns green (done), or a challenge opens.
3. **An image grid.** Read the prompt ("traffic lights", "crosswalks", "bicycles"…). The grid is a rectangle in the
   screenshot: divide it into its rows and columns and click the centre of every matching tile. Take a new screenshot
   after a few clicks if the grid redraws. Press **Verify** / **Next** when the prompt says so; a new grid may appear —
   solve it the same way, up to three rounds.
4. **A drag puzzle.** The shape is usually at the bottom; the targets are outlines above it. Find the outline that is
   **closed** (no gap) and matches the shape. `browser_act` with:
   `{"action": "drag_at", "x": shape_x, "y": shape_y, "to_x": loop_x, "to_y": loop_y}`.
   Press at the shape's centre, move in one smooth line, let go in the loop's centre. Wait 1–2 s and screenshot; if
   the shape snaps back, try the next closed loop, or start the drag 2–3 px inside the shape.
5. **Cloudflare.** Click the Turnstile checkbox and wait 10–15 s. Do not reload in a loop: a reload is a new challenge
   and looks like a bot. If it spins, open the same URL in a new tab in the same profile and wait there. A real Chrome
   profile (Dot's own) usually passes a managed challenge with no puzzle.
6. **Check it passed.** The widget disappears, the page shows its next content, or a success word appears. Use
   `browser_wait_text` or a fresh `browser_snapshot`; do not assume the check passed because you clicked. Then go on
   with the task — the form's Send, the next page, the upload.
7. **If it fails.** Try another way: a new screenshot and different tiles, the other closed loop, a new tab for
   Cloudflare, the audio challenge after two visual failures. Three honest attempts per widget is the limit. Then leave
   the tab open and say in the result what is left; do not ask the owner to solve it and do not sit on the page until
   it times out.

## The details that decide it

- **Look before every click.** A captcha redraws its images and moves its tiles after each click; coordinates from an
  old screenshot are wrong. One screenshot, one small batch of clicks, one screenshot.
- **Click centres.** Tiles and checkboxes have a margin; the centre is safe. For a 400×400 grid at (x0, y0), the
  centres are at `x0 + (i + 0.5) * 400 / 3`, `y0 + (j + 0.5) * 400 / 3`.
- **hCaptcha drag:** "the loop that is closed" means the outline without a gap. If two look closed, the one whose
  curve matches the shape's edge is the target. Drag from the shape, not from the empty canvas.
- **reCAPTCHA** often needs several rounds: grid, then a second grid, then Verify. Do not press Verify before every
  matching tile is clicked.
- **Cloudflare** judges the whole browser, not the click: a visible, persistent Chrome profile passes more often than
  a fresh headless one. Dot's own Chrome is the visible profile; use it and wait rather than retrying.
- **Never** ship the challenge to a solving service, paste its images into a chat, or use a CAPTCHA farm. The check is
  solved here, in the owner's own session, by looking at it.
- **Never** solve a check to create accounts, send spam, or get past a site's access control for a flow the owner is
  not in. The owner's own application is the case this skill is for.

## If the page is a Cloudflare block, not a check

"Sorry, you have been blocked" or a Ray ID with no checkbox is not a puzzle: the site refused this browser or this IP.
Do not hammer it. Try the same URL once in a new tab; if it is still blocked, say so with the Ray ID and the page, and
finish the part that does not need that site.

# Making pictures

Dot makes a picture with the tool `generate_image` (item 1969, in the tool group `images`): a logo, a diagram, a social
card, a cover, an icon, a simple illustration, a chart, or a photo-like image. The PNG is saved in the run's workspace
at the path the run gives, and it shows in the conversation where it was made, at the conversation's width; a press
opens it full size (the tool's row above it keeps a small thumbnail, as every tool's does). It takes **no key of its own**: a picture is drawn by Dot's own browser, and a photo uses a key you
already have.

## Two ways, and how the run chooses

| | Drawn | Photo |
| --- | --- | --- |
| For | logos, diagrams, social cards, covers, icons, simple illustrations, charts | a photo-like or painted image no drawing can give |
| How | the model writes an SVG (with a `viewBox`) or a static HTML and CSS page; Dot's browser draws it at `width` by `height` pixels (64 to 4096 a side) | Google's picture model, asked for the words in `photo` and an optional `aspect_ratio` |
| Needs | nothing: every model, no key | `GOOGLE_API_KEY` of a Google AI Studio project **with billing** |
| Argument | `svg` or `html` | `photo` |

A call gives exactly one of `svg`, `html` and `photo`, and a `path` ending in `.png`. The tool's description teaches the
run to draw unless the request needs a photo. The run is shown the picture it made (a JPEG of at most 1024 pixels on a
side) and can draw it again when it is off.

## Drawn pictures

The browser is the one Dot already drives, in a quiet tab of its own that closes when the picture is taken
(`BrowserSession.render_image`), so it opens no window in front of yours. The page is the run's own writing, so it is
given no way out:

- an SVG is drawn as a picture (an `<img>` with a `data:` address), which loads nothing and runs nothing;
- an HTML page has a content security policy put first in it that bars every script and every network address; only
  what the page holds, pictures and fonts written in as `data:` addresses, loads;
- the browser also refuses every request the page makes, whatever the policy says.

Fonts are the Mac's own. A chart is drawn as SVG (or as CSS), not with a script library.

## Photos

A photo is made by Gemini's picture model on the Google key Dot already uses for `google:` models
([Models](models.md)): `POST <image_base_url>/models/<image_model>:generateContent` with the key in the
`x-goog-api-key` header, only the picture asked for (`responseModalities: ["IMAGE"]`), and the shape in
`imageConfig.aspectRatio` when one is given. The answer's picture is saved as a PNG, whatever format Google sent. The
call is retried with backoff like every call of the Google connector, and recorded on the run as a model call of
purpose `image` with the tokens Google reported (the run's tokens grow by them; the price list has no price for a
picture model, so no dollars are estimated).

- **The model** is `[providers.google] image_model` (`gemini-3.1-flash-lite-image` by default, the cheapest of the
  current picture models) and the route `image_base_url` (`https://generativelanguage.googleapis.com/v1`).
- **Pictures are not on the free tier** of the Gemini API: the key's project needs billing, or Google refuses with a
  403 and the run is told so, with Google's own words.
- **With no key**, the tool says that `GOOGLE_API_KEY` is not set (`dotx auth set GOOGLE_API_KEY`) and names drawing
  as the other way. It does not draw instead, and it never asks for another key.
- The words of `photo` go to Google, as a question to a helper model goes to its provider.
- A prompt or a reply Google blocks for safety is reported as the content filter's, and nothing is saved.

## What it is for the policy

The call is a **write of the file at `path`**, classified as `write_file` is (low in the workspace, medium in the home
folder, high in a protected place), and noted in the undo journal. No call of this tool asks anything of the owner that
a write did not: it has a row in the table of [the freedom contract](freedom.md), and a yolo or free run makes it
unasked. It is an acting tool: a `research` run does not have it.

## Checks

`tests/unit/test_google_image.py` (the request body and the answer, read from `tests/fixtures/gemini_image_answer.json`,
shaped as the API reference gives it; the arguments; the page of a drawing),
`tests/smoke/test_generate_image.py` (`browser`: an SVG and an HTML page drawn in a real headless Chrome at the size
asked, a script that does not run, a network address that is not fetched) and `tests/smoke/test_generate_image_photo.py`
(the real router and connector against a stand-in of the Gemini API on this Mac: the route, the key's header, the saved
PNG, the recorded call, no key, and a refused project). No test makes a live call.

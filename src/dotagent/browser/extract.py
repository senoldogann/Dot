"""Records read out of a page by a spec of fields (item 714): for each element a CSS selector picks (a result, a row, a
card), the text or an attribute of what each field's selector finds in it. Dot's own script reads them, never the
model's, so a research run can use it: it reads and changes nothing, and a field that takes a secret (a password, a
one-time code, a card) is never read."""

import asyncio
import json

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from pydantic import JsonValue

from dotagent.browser.network import HIDDEN, SECRET_NAME
from dotagent.browser.safety import secret_selector
from dotagent.core.errors import BrowserError

# The most characters of one field's value: a field is a name, a price, a date or a line, not a page.
FIELD_CHARS: int = 2000
_EXTRACT_S: float = 10.0
# Each record is the element `from` picks, or the whole document; each field is `css` or `css@attribute` in it, where an
# empty `css` is the record itself. A field that takes a secret gives HIDDEN, one that is not there null.
_SCRIPT: str = """(spec) => {
  const roots = spec.from === "" ? [document] : [...document.querySelectorAll(spec.from)].slice(0, spec.limit);
  const read = (root, field) => {
    const at = field.lastIndexOf("@");
    const css = at === -1 ? field : field.slice(0, at);
    const attribute = at === -1 ? null : field.slice(at + 1);
    const found = css.trim() === "" ? root : root.querySelector(css);
    if (found === null || found === document) {
      return found === document ? (document.body?.innerText ?? "").slice(0, spec.chars) : null;
    }
    if (found.matches(spec.secret)) {
      return spec.hidden;
    }
    // An attribute whose name, or whose element's name or id, says it holds a secret (a CSRF token in a meta tag, a
    // session in a hidden input): hidden outside yolo, as a JSON key of that name is (the code review of item 714).
    const named = (text) => text !== null && text !== "" && new RegExp(spec.secretName, "i").test(text);
    if (spec.hideNamed && attribute !== null && [attribute, found.getAttribute("name"), found.id].some(named)) {
      return spec.hidden;
    }
    const value = attribute === null ? (found.innerText ?? found.textContent) : found.getAttribute(attribute);
    return value === null ? null : value.trim().slice(0, spec.chars);
  };
  const record = (root) => Object.entries(spec.fields).map(([name, field]) => [name, read(root, field)]);
  return roots.map((root) => Object.fromEntries(record(root)));
}"""


async def extract_records(
    page: Page, records: str, fields: dict[str, str], limit: int, hide_secrets: bool
) -> list[JsonValue]:
    """The records of ``page``: one for each element ``records`` picks (``limit`` at most), or one for the page when it
    is empty, each mapping the names of ``fields`` to what their selectors find. A selector the page cannot read fails
    the call with the browser's words."""
    spec: dict[str, JsonValue] = {
        "from": records,
        "fields": dict(fields),
        "limit": limit,
        "chars": FIELD_CHARS,
        "secret": secret_selector(),
        "secretName": SECRET_NAME.pattern,
        "hideNamed": hide_secrets,
        "hidden": HIDDEN,
    }
    try:
        async with asyncio.timeout(_EXTRACT_S):
            value: object = await page.main_frame.evaluate(_SCRIPT, spec)
    except TimeoutError as error:
        raise BrowserError("the page did not answer in time", {"seconds": _EXTRACT_S}) from error
    except PlaywrightError as error:
        raise BrowserError(
            "a selector could not be read: use CSS selectors such as 'li.result', 'a@href' or '.price'",
            {"error": str(error).splitlines()[0]},
        ) from error
    return list(value) if isinstance(value, list) else []


def render_records(url: str, found: list[JsonValue]) -> str:
    """The records as one JSON line each, under the page's address."""
    lines: list[str] = [f"{len(found)} record(s) from {url}"]
    lines.extend(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in found)
    return "\n".join(lines)

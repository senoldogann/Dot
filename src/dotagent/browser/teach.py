"""Recording the steps the owner takes in one tab, so that Dot can be taught a task (item 423).

The owner works in Chrome itself: the tab comes to the front, and what they do there is what Dot is shown. A script
put into the page by :data:`TEACH_SCRIPT` reports the steps that mean something (a page opened, a click on a named
control, a field filled, a choice made, Enter pressed) through the binding :data:`TEACH_BINDING`; :func:`taught_step`
turns what it reports into a line. The script reports no value of any field, and for a password, one-time-code or card
field it reports not even the field's name: a recording is shown to the owner and read by the skill writer, and a value
that got into it would be a secret in a skill (:mod:`dotagent.desktop.actions` keeps a typed text out of a step in the
same way).
"""

import json
import time
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from dotagent.core.types import FrozenModel

# What the page calls the binding the recorder reports through. The script is put in with ``add_init_script``, so it
# runs again in every document the tab loads and a recording follows the owner from page to page without another start.
TEACH_BINDING: str = "__dotTeach"
TEACH_SCRIPT: str = """
(() => {
  if (window !== window.top || window.__dotTeachOn) { return; }
  window.__dotTeachOn = true;
  const text = (value) => (value || "").replace(/\\s+/g, " ").trim().slice(0, 80);
  // What a control is called: its label, its accessible name, its text, its tag. Never its value: a value typed into a
  // field is the owner's, and a step names the field, not what they put in it.
  const named = (element) => {
    const label = element.labels && element.labels[0] ? element.labels[0].innerText : "";
    const attribute = element.getAttribute && (element.getAttribute("aria-label")
      || element.getAttribute("name") || element.getAttribute("placeholder"));
    const own = element.innerText || "";
    return text(label || attribute || own || element.tagName.toLowerCase()) || "a control";
  };
  const secret = (element) => {
    const autocomplete = ((element.getAttribute && element.getAttribute("autocomplete")) || "").toLowerCase();
    return element.type === "password" || autocomplete.includes("one-time-code")
      || autocomplete.includes("cc-number") || autocomplete.includes("cc-exp")
      || autocomplete.includes("cc-csc");
  };
  const tell = (step) => {
    try {
      window.__dotTeach(JSON.stringify(step));
    } catch (error) {
      console.warn("dot: the recorder is not listening", error);
    }
  };
  const control = "a, button, [role=button], summary, input[type=submit], input[type=button]";
  document.addEventListener("click", (event) => {
    const clicked = event.target.closest && event.target.closest(control);
    if (clicked) { tell({ kind: "click", label: named(clicked) }); }
  }, true);
  document.addEventListener("change", (event) => {
    const field = event.target;
    if (!field || !field.tagName) { return; }
    if (field.tagName === "SELECT" || field.type === "checkbox" || field.type === "radio") {
      tell({ kind: "choice", label: named(field) });
      return;
    }
    if (field.tagName === "INPUT" || field.tagName === "TEXTAREA") {
      const hidden = secret(field);
      tell({ kind: "fill", label: hidden ? "" : named(field), secret: hidden });
    }
  }, true);
  document.addEventListener("keydown", (event) => {
    const field = event.target;
    if (event.key !== "Enter" || !field || (field.tagName !== "INPUT" && field.tagName !== "TEXTAREA")) { return; }
    const hidden = secret(field);
    tell({ kind: "enter", label: hidden ? "" : named(field), secret: hidden });
  }, true);
  tell({ kind: "page", label: location.origin + location.pathname });
})();
"""

StepKind = Literal["page", "click", "fill", "choice", "enter"]

# The most steps a recording keeps (item 554). What a person shows is some tens of steps; the binding the recorder
# reports through is in the page's own world, where the page's script can call it too, and a list that grows without
# end would fill the daemon and the skill writer's request.
MAX_STEPS: int = 400
FULL_NOTE: str = f"(the recording is full: the steps after the first {MAX_STEPS} were not kept)"


# How long a part of an address's path may be before a recording will not carry it: a path part that long is an
# identifier more often than a word, and a reset or share link carries its token there (`/reset/<token>`, `/s/<id>`).
_TOKEN_SEGMENT: int = 16


def named_page(url: str) -> str:
    """The page at ``url`` as a recording names it: where it is, and nothing a secret could be in. The query and the
    fragment go (a form's words, an OAuth code, a token in a magic link are there), and so does a part of the path long
    enough to be an identifier rather than a word: a recording is shown to the owner, read by the model that writes the
    skill, and kept in it, so a token that got into a line would become part of the skill (item 423)."""
    parts = urlsplit(url)
    segments: list[str] = [part if len(part) < _TOKEN_SEGMENT else "…" for part in parts.path.split("/")]
    return f"{parts.scheme}://{parts.netloc}" + "/".join(segments)


# The pages a recording names: a blank tab (about:blank, whose script reports its origin as "null") or a page of
# Chrome's own is where the owner starts, not a step of the task.
_WEB_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def web_page(url: str) -> bool:
    """Whether ``url`` is a page of the web, which a recording names, rather than a blank tab or Chrome's own."""
    return urlsplit(url).scheme in _WEB_SCHEMES


_KIND_OF: dict[str, StepKind] = {"page": "page", "click": "click", "fill": "fill", "choice": "choice", "enter": "enter"}


class TaughtStep(FrozenModel):
    """One step of a recording: what was done, and to what. ``label`` is the address of a page opened, the name of the
    control clicked, the name of the field filled or chosen, and for a field that holds a secret nothing at all."""

    kind: StepKind
    label: str = Field(default="", max_length=200)
    secret: bool = False
    at: float = 0.0

    @property
    def line(self) -> str:
        """The step as the owner reads it, and as the skill writer is shown it."""
        if self.kind == "page":
            return f"Opened {self.label}"
        if self.kind == "click":
            return f'Clicked "{self.label}"'
        if self.kind == "fill":
            return "Filled a secret field" if self.secret else f'Filled "{self.label}"'
        if self.kind == "choice":
            return f'Chose "{self.label}"'
        return "Pressed Enter in a secret field" if self.secret else f'Pressed Enter in "{self.label}"'


def taught_step(payload: str, now: float | None = None) -> TaughtStep | None:
    """The step the recorder reported as ``payload`` (a JSON object it wrote), or ``None`` when it reported something
    this code does not know, or a step that says nothing: a page whose report cannot be read is a page that changed
    under the recording, not a step, and a blank tab or a page of Chrome's own is not one either."""
    try:
        reported = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(reported, dict):
        return None
    raw: str = str(reported.get("kind", ""))
    kind: StepKind | None = _KIND_OF.get(raw)
    if kind is None:
        return None
    label: str = str(reported.get("label", ""))[:200].strip()
    if kind == "page":
        if not web_page(label):
            return None
        label = named_page(label)
    secret: bool = bool(reported.get("secret")) and kind in ("fill", "enter")
    if kind != "page" and not label and not secret:
        return None
    return TaughtStep(kind=kind, label="" if secret else label, secret=secret, at=now or time.time())


@dataclass
class Recording:
    """One tab's recording while it goes on: the steps so far, the page they came from and when it started. A session
    keeps one at a time: two would show one owner's steps in another's panel."""

    thread_id: str
    page: object
    tab_index: int
    started_at: float = field(default_factory=time.time)
    steps: list[TaughtStep] = field(default_factory=list)

    @property
    def full(self) -> bool:
        """Whether the recording keeps no more steps (:data:`MAX_STEPS`)."""
        return len(self.steps) >= MAX_STEPS

    def take(self, source: dict[str, object], payload: str) -> None:
        """Take what the page ``source`` reports as a step, when it is the recorded page: a step of a tab the owner
        opened elsewhere is not part of what they showed Dot here. A recording that is full takes no more (item 554)."""
        if source.get("page") is not self.page:
            return
        step: TaughtStep | None = taught_step(payload)
        if step is None:
            return
        if self.full:
            return
        last: TaughtStep | None = self.steps[-1] if self.steps else None
        if step.kind == "page" and last is not None and last.kind == "page" and last.label == step.label:
            # The same page reported again, its script put in once more: nothing happened between the two lines.
            return
        self.steps.append(step)

    def opened(self, url: str) -> None:
        """Take the page the recording starts on, when the tab is already there and its script has nothing new to
        report: the skill needs the page it begins from, and the last step tells whether it was already taken. A blank
        tab is no page to begin from."""
        if not web_page(url):
            return
        address: str = named_page(url)
        if not any(step.kind == "page" and step.label == address for step in self.steps):
            self.steps.append(TaughtStep(kind="page", label=address))

    def digest(self) -> str:
        """The steps as the skill writer reads them: one line each, numbered, and a last line that says so when the
        recording was full and later steps were not kept."""
        lines: list[str] = [f"{number}. {step.line}" for number, step in enumerate(self.steps, start=1)]
        if self.full:
            lines.append(FULL_NOTE)
        return "\n".join(lines)

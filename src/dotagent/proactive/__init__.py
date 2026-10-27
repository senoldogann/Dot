"""Dot comes to the owner (items 570 and 571): the morning brief a schedule of its own writes each day
(:mod:`dotagent.proactive.brief`), and the watchers that tell the owner when a mail, a calendar event or a GitHub
notification they care about comes (:mod:`dotagent.proactive.watchers`, ``looks``, ``judge`` and ``watching``), and
the work Dot does for them on its own initiative while the Mac is idle (:mod:`dotagent.proactive.initiative`, item
715)."""

# The source of the run a watcher starts for an item that came in (item 571): a run nobody watches, started from what a
# third party wrote, so it is told apart from a schedule's and from the owner's own.
WATCHER_SOURCE: str = "watcher"
# The source of a line a watcher keeps in its chat (``RunManager.keep_notice``): a run that no model made, ended as it
# is stored, whose goal is Dot's own words and whose result is the tell.
NOTICE_SOURCE: str = "notice"
# The source of a run Dot starts on its own initiative (item 715, :mod:`dotagent.proactive.initiative`): nobody asked
# for it and nobody watches it, and its goal is Dot's words, never the owner's instruction.
INITIATIVE_SOURCE: str = "initiative"

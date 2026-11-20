"""The local HTTP interface: a JSON API, live event streams and the browser UI, reachable from this Mac only."""

# The ``source`` of the threads and runs that the UI starts.
UI_SOURCE: str = "ui"
# The ``source`` of the runs that ``dotx run`` starts.
CLI_SOURCE: str = "cli"
# The ``source`` of the runs that ``dotx run --review`` starts: the code reviews of the pre-push hook, kept out of
# the owner's chats under a section of their own.
REVIEW_SOURCE: str = "review"

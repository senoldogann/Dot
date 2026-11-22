# Roadmap items from 459 on

**For items after 458 use [INDEX.md](INDEX.md)**: a table of every item here, newest first, made by
`uv run python scripts/roadmap_index.py`. A change that adds or edits an item's file runs that script and commits the
index with it; `scripts/check_item_records.py` (the gate's item-records check) fails on an index that is stale. When
two branches added items side by side and the merge conflicts in `INDEX.md`, run the script again instead of merging
the rows by hand.

[ROADMAP.md](../ROADMAP.md) holds the items up to 458 and the log up to then. From item 459 on, each item is a file of
its own here, `roadmap/<item>.md`: its section as ROADMAP.md writes one (`### <item>. <title>`, its status and how it was
built and checked), then the lines it adds to the log, under `Log:`. Pull requests made side by side then add files
instead of writing the same lines at the end of ROADMAP.md. The next item's number is one more than the highest in
ROADMAP.md and here; a change takes it by adding its file.

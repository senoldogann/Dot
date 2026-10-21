# Changelog entries not released yet

Each change writes its changelog entry here, in a file of its own named for its roadmap item: `changes/<item>.md`,
the list it would have added under **Unreleased** in [CHANGELOG.md](../CHANGELOG.md) (`- ` lines, continued with two
spaces). Pull requests made side by side then add files instead of writing the same lines at the top of the changelog.
A release folds them into its section (`uv run python scripts/fold_changes.py <version> <date>`, see
[Releasing](../docs/development.md#releasing)) and removes them.

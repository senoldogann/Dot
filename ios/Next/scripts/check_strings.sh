#!/bin/sh
# Fails when DotNext shows a string the String Catalog does not hold, holds a key the code no longer uses (stale), or
# holds a key with no Turkish translation or no translator comment (the owner reads Turkish only).
#
# Usage: ios/Next/scripts/check_strings.sh <derived data folder of a DotNext build>
#   The build writes the keys the compiler found (.stringsdata, SWIFT_EMIT_LOC_STRINGS). A copy of the catalog is
#   synced with them; any change the sync makes is a failure. After adding strings: copy the synced catalog over
#   Resources/Localizable.xcstrings (the script prints its path), add the Turkish to translations_tr.py and run
#   apply_translations.py.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
next=$(dirname "$here")
derived=${1:?usage: check_strings.sh <derived data folder>}
work=$(mktemp -d)
data=$(find "$derived" -path '*DotNext.build*' -name '*.stringsdata' ! -name 'ExtractedAppShortcuts*')
[ -n "$data" ] || { echo "no .stringsdata under $derived: build DotNext first" >&2; exit 2; }
cp "$next/Resources/Localizable.xcstrings" "$work/Localizable.xcstrings"
# shellcheck disable=SC2086
if ! xcrun xcstringstool sync "$work/Localizable.xcstrings" --stringsdata $data >"$work/sync.log" 2>&1; then
    echo "xcstringstool sync failed; its output:" >&2
    cat "$work/sync.log" >&2
    exit 2
fi
# Raw literals in SwiftUI calls bypass the comment rule: every UI string goes through String(localized:comment:).
raw=$(grep -rnE '(Text|Button|Label|Tab|navigationTitle|accessibilityLabel|accessibilityHint|confirmationDialog)\("[^"]*[A-Za-z]' \
    "$next/App" "$next/Activity" "$next/Components" "$next/Design" "$next/Settings" | grep -v 'Text(verbatim:' || true)
python3 - "$next/Resources/Localizable.xcstrings" "$work/Localizable.xcstrings" "$raw" <<'PY'
import json, sys
committed = json.load(open(sys.argv[1], encoding="utf-8"))["strings"]
synced = json.load(open(sys.argv[2], encoding="utf-8"))["strings"]
raw = sys.argv[3].strip()
problems = []
problems += [f"not in the catalog (new string): {k!r}" for k in sorted(set(synced) - set(committed))]
problems += [f"stale (no longer in the code): {k!r}" for k, v in sorted(synced.items()) if v.get("extractionState") == "stale"]
for key, entry in sorted(committed.items()):
    unit = entry.get("localizations", {}).get("tr", {}).get("stringUnit", {})
    if unit.get("state") != "translated" or not unit.get("value"):
        problems.append(f"no Turkish translation: {key!r}")
    if not entry.get("comment"):
        problems.append(f"no translator comment: {key!r}")
if raw:
    problems += [f"raw UI literal (use String(localized:comment:)): {line}" for line in raw.splitlines()]
if problems:
    print("\n".join(problems), file=sys.stderr)
    print(f"synced copy: {sys.argv[2]}", file=sys.stderr)
    sys.exit(1)
print(f"String Catalog OK: {len(committed)} keys, all with Turkish and a comment")
PY

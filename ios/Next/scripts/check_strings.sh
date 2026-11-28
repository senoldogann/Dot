#!/bin/sh
# Fails when DotNext shows a string the String Catalog does not hold, holds a key the code no longer uses (stale), or
# holds a key with no Turkish translation or no translator comment (the owner reads Turkish only), or holds an English
# unit not marked translated (a key that is not its English: the build leaves such a unit out of en.lproj).
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
# The Turkish table is Python the translator workflow imports: a broken one fails here, not at the next regeneration.
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import translations_tr" "$here" || { echo "translations_tr.py does not import" >&2; exit 2; }
# Every catalog key with a Turkish value must be in the table, or the next apply_translations.py run fails (item 2117).
python3 - "$here" "$next/Resources/Localizable.xcstrings" <<'PY' || exit 2
import json, sys
sys.path.insert(0, sys.argv[1])
from translations_tr import TR
strings = json.load(open(sys.argv[2], encoding="utf-8"))["strings"]
missing = sorted(k for k, v in strings.items() if v.get("localizations", {}).get("tr", {}).get("stringUnit", {}).get("value") and k not in TR)
if missing:
    print("in the catalog with Turkish but not in translations_tr.py (apply_translations.py would fail):", *missing, sep="\n  ", file=sys.stderr)
    sys.exit(1)
PY
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
    "$next/App" "$next/Activity" "$next/Components" "$next/Design" "$next/Settings" "$next/Watch" | grep -v 'Text(verbatim:' || true)
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
    english = entry.get("localizations", {}).get("en", {}).get("stringUnit")
    if english is not None and english.get("state") != "translated":
        problems.append(f"English not marked translated (left out of en.lproj; run apply_translations.py): {key!r}")
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

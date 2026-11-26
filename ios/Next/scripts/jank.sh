#!/bin/bash
# The jank check of a work-log row (`UITests/WorkLogJankUITests.swift`): builds the debug app, opens and closes a command
# row with 600 lines of output six times on the fixture chat `n2-jank` and prints the frame meter's line of each toggle
# (frames, hitches, frames over 16.7 ms, the longest frame, hitch ms per second, the main thread's waits).
#   usage: jank.sh <booted simulator udid> <derived data folder>
set -euo pipefail
udid=$1
derived=$2
ios=$(cd "$(dirname "$0")/../.." && pwd)
xcrun simctl uninstall "$udid" dev.dotagent.phone.next 2>/dev/null || true
while ! mkdir /tmp/dotnext-xcode.lock 2>/dev/null; do sleep 15; done
trap 'rmdir /tmp/dotnext-xcode.lock' EXIT
xcodebuild test -project "$ios/Dot.xcodeproj" -scheme DotNext -destination "id=$udid" -configuration Debug CODE_SIGN_IDENTITY=- \
    -derivedDataPath "$derived" -only-testing:DotNextUITests/WorkLogJankUITests 2>&1 \
    | grep -E "^Test Case .*(passed|failed)|error:|round [0-9]|Executed|TEST (EXECUTE )?(SUCCEEDED|FAILED)"
container=$(xcrun simctl get_app_container "$udid" dev.dotagent.phone.next data)
echo "frame meter (one line per toggle):"
cat "$container/Library/Caches/dotnext-frames.txt" 2>/dev/null || echo "  (no line written)"

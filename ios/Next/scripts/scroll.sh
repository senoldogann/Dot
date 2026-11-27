#!/bin/bash
# The scroll check of a long chat (`UITests/ChatScrollUITests.swift`): builds the app (Debug, optimized like Release so
# the numbers are the shipped app's), flicks the fixture chat `n2-long` up and down, and prints XCTest's hitch ratio,
# the render meter's count and the frame meter's line of each flick.
#   usage: scroll.sh <destination id: a booted simulator or a connected iPhone> <derived data folder> [test name]
set -euo pipefail
udid=$1
derived=$2
only=${3:-ChatScrollUITests}
ios=$(cd "$(dirname "$0")/../.." && pwd)
# The caller may hold Xcode's lock already (SCROLL_LOCKED=1, with the heavy lock: both or neither, so no deadlock).
if [ "${SCROLL_LOCKED:-}" != 1 ]; then
    while ! mkdir /tmp/dotnext-xcode.lock 2>/dev/null; do sleep 15; done
    trap 'rmdir /tmp/dotnext-xcode.lock' EXIT
fi
# A simulator signs locally, an iPhone with the team (the list is read whole first: no pipe decides it).
simulators=$(xcrun simctl list devices)
case "$simulators" in
    *"$udid"*) simulator=1; signing=(CODE_SIGN_IDENTITY=-) ;;
    *) simulator=0; signing=(-allowProvisioningUpdates DEVELOPMENT_TEAM=79DZ4AA4DW CODE_SIGN_STYLE=Automatic) ;;
esac
# The test's status is kept, not acted on at once: a failed run still prints its frame lines, then fails.
mkdir -p "$derived"
set +e
xcodebuild test -project "$ios/Dot.xcodeproj" -scheme DotNext -destination "id=$udid" -configuration Debug "${signing[@]}" \
    SWIFT_OPTIMIZATION_LEVEL=-O -derivedDataPath "$derived" -only-testing:"DotNextUITests/$only" >"$derived/scroll-test.log" 2>&1
status=$?
set -e
grep -E "^Test Case .*(passed|failed)|error:|scroll-check|Scroll|hitch|Hitch|Executed|TEST (EXECUTE )?(SUCCEEDED|FAILED)" "$derived/scroll-test.log" \
    || echo "  (no test line in $derived/scroll-test.log)"
echo "frame meter (one line per flick: the 16 + 16 of the flick test, then the measure's 20):"
if [ "$simulator" = 1 ]; then
    container=$(xcrun simctl get_app_container "$udid" dev.dotagent.phone.next data)
    cat "$container/Library/Caches/dotnext-frames.txt" 2>/dev/null || echo "  (no line written)"
    rm -f "$container/Library/Caches/dotnext-frames.txt"
else
    xcrun devicectl device copy from --device "$udid" --domain-type appDataContainer --domain-identifier dev.dotagent.phone.next \
        --source Library/Caches/dotnext-frames.txt --destination "$derived/frames.txt" >/dev/null 2>&1 \
        && cat "$derived/frames.txt" || echo "  (no line copied from the iPhone)"
fi
exit $status

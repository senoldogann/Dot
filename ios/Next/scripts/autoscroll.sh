#!/bin/bash
# The scroll check on an iPhone whose UI automation is off: builds the app (Debug, optimized like Release), installs it,
# opens the fixture chat `n2-long` (or the one named) with `-DotNextAutoScroll 1`, which glides the transcript to its
# top and back twice, and prints the frame meter's line of each pass.
#   usage: autoscroll.sh <iPhone id> <derived data folder> [tool call mode]
set -euo pipefail
device=$1
derived=$2
mode=${3:-showAll}
ios=$(cd "$(dirname "$0")/../.." && pwd)
if [ "${SCROLL_LOCKED:-}" != 1 ]; then
    while ! mkdir /tmp/dotnext-xcode.lock 2>/dev/null; do sleep 15; done
    trap 'rmdir /tmp/dotnext-xcode.lock' EXIT
fi
# The app of an earlier build is removed first, so that a build that fails can never be measured in its place.
app="$derived/Build/Products/Debug-iphoneos/DotNext.app"
rm -rf "$app"
mkdir -p "$derived"
if ! xcodebuild build -project "$ios/Dot.xcodeproj" -scheme DotNext -destination "id=$device" -configuration Debug -allowProvisioningUpdates \
    DEVELOPMENT_TEAM=79DZ4AA4DW CODE_SIGN_STYLE=Automatic SWIFT_OPTIMIZATION_LEVEL=-O -derivedDataPath "$derived" -quiet >"$derived/autoscroll-build.log" 2>&1; then
    grep -E "error:|FAILED" "$derived/autoscroll-build.log" | tail -20
    echo "the build failed: nothing is measured (the log: $derived/autoscroll-build.log)" >&2
    exit 1
fi
[ -d "$app" ] || { echo "the build left no app at $app" >&2; exit 1; }
xcrun devicectl device install app --device "$device" "$app" >/dev/null
out=$(mktemp -d)
# Each launch glides four times (to the top and back, twice); its lines are copied until all four are written (each
# glide takes about 12 s), and a launch that has not written them in 3 minutes says how many it did.
copy() {
    xcrun devicectl device copy from --device "$device" --domain-type appDataContainer --domain-identifier dev.dotagent.phone.next \
        --source Library/Caches/dotnext-frames.txt --destination "$1" >/dev/null 2>&1
}
# The app's file keeps the lines of earlier launches: what it holds before this run is counted first.
base=0
if copy "$out/frames.txt"; then base=$(grep -c autoscroll "$out/frames.txt" || true); fi
for round in 1 2; do
    xcrun devicectl device process launch --device "$device" --terminate-existing dev.dotagent.phone.next -- \
        -DotNextScene n2-long -toolCallMode "$mode" -DotNextScrollMeter 1 -DotNextAutoScroll 1 -AppleLanguages "(en)" -AppleLocale en_US >/dev/null
    want=$((base + round * 4))
    have=0
    for _ in $(seq 36); do
        sleep 5
        if copy "$out/frames.txt"; then have=$(grep -c autoscroll "$out/frames.txt" || true); fi
        [ "$have" -ge "$want" ] && break
    done
    [ "$have" -ge "$want" ] || echo "  (round $round: $((have - base - (round - 1) * 4)) of 4 glide lines after 3 minutes; the glides did not all end)"
done
echo "frame meter, mode $mode (four lines a launch, two launches):"
grep autoscroll "$out/frames.txt" 2>/dev/null | tail -n +$((base + 1)) || echo "  (no line written)"

#!/bin/sh
# A chat's streaming end to end (items 1848 and 1849): a throwaway daemon (e2e_daemon.py, its "stream:" script) in three
# mktemp folders, a phone paired with a code, one chat made so the live chat scene has one to open, then
# StreamingLiveUITests: a new chat stays open when its first message is sent, and a streaming run of about half a minute
# is measured by the app's frame meter. The script prints the meter's lines and, with a fourth argument, records the
# simulator's screen to that file. Nothing touches the owner's Dot; no model is called.
#
# Usage: ios/Next/scripts/e2e_streaming.sh <simulator udid> <derived data folder> [only test] [video.mp4]
set -eu
udid=${1:?usage: e2e_streaming.sh <simulator udid> <derived data> [only test] [video]}
derived=${2:?derived data folder}
only=${3:-}
video=${4:-}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
port=18794
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$(dirname "$ios")" python "$here/e2e_daemon.py" "$port" >"$log" 2>&1 &
pid=$!
rec=""
trap 'kill $pid 2>/dev/null || true; [ -n "$rec" ] && kill -INT $rec 2>/dev/null || true' EXIT
tries=0
until grep -q READY "$log"; do
    tries=$((tries + 1))
    if [ "$tries" -gt 300 ]; then echo "the daemon never said READY:" >&2; cat "$log" >&2; exit 1; fi
    perl -e 'select(undef, undef, undef, 0.2)'
done
base="http://127.0.0.1:$port"
main="Authorization: Bearer dotnext-e2e-token"
code=$(curl -fsS -X POST -H "$main" "$base/api/phone/pairing" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
key=$(curl -fsS -X POST -H "Content-Type: application/json" -d "{\"code\":\"$code\"}" "$base/api/phone/pair" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -fsS -X POST -H "$main" -H "Content-Type: application/json" -d '{"goal":"A first chat for the streaming check"}' "$base/api/runs" >/dev/null
# A fresh install: a cache, outbox or key of an earlier run's daemon (the same port) must not stand in this one's way.
xcrun simctl uninstall "$udid" dev.dotagent.phone.next 2>/dev/null || true
xcodebuild build-for-testing -project "$ios/Dot.xcodeproj" -scheme DotNext -destination "id=$udid" -configuration Debug \
    CODE_SIGN_IDENTITY=- -derivedDataPath "$derived" 2>&1 | grep -E "error:|BUILD (SUCCEEDED|FAILED)" | sort -u
if [ -n "$video" ]; then
    xcrun simctl io "$udid" recordVideo --codec h264 --force "$video" >/dev/null 2>&1 &
    rec=$!
fi
filter=${only:+-only-testing:DotNextUITests/StreamingLiveUITests/$only}
TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" xcodebuild test-without-building -project "$ios/Dot.xcodeproj" \
    -scheme DotNext -destination "id=$udid" -configuration Debug CODE_SIGN_IDENTITY=- -derivedDataPath "$derived" \
    ${filter:--only-testing:DotNextUITests/StreamingLiveUITests} 2>&1 | grep -E "^Test Case .*(passed|failed)|error:|Executed|TEST (EXECUTE )?(SUCCEEDED|FAILED)"
if [ -n "$rec" ]; then kill -INT $rec; wait $rec 2>/dev/null || true; rec=""; fi
container=$(xcrun simctl get_app_container "$udid" dev.dotagent.phone.next data)
echo "frame meter:"
cat "$container/Library/Caches/dotnext-frames.txt" 2>/dev/null || echo "  (no line written)"

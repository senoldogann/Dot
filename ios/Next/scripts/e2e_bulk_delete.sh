#!/bin/sh
# Deleting many chats at once end to end (items 1964 and 1965): a throwaway daemon (e2e_daemon.py, its scripted model)
# in three mktemp folders, a phone paired with a code, 20 chats with Dot, then BulkDeleteLiveUITests deletes them all
# from Dot's profile › All chats (Select › Select all › Delete) and times it, and the profile empties; this script reads
# on the daemon's side that no chat is left. Nothing touches the owner's Dot; no model is called. The app must be built
# for testing in <derived data> first (xcodebuild build-for-testing).
#
# Usage: ios/Next/scripts/e2e_bulk_delete.sh <simulator udid> <derived data folder>
set -eu
udid=${1:?usage: e2e_bulk_delete.sh <simulator udid> <derived data>}
derived=${2:?derived data folder}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
daemon=$(dirname "$ios")
port=$(python3 -c "import socket; s=socket.socket(); s.bind((\"127.0.0.1\", 0)); print(s.getsockname()[1])")
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$daemon" python "$here/e2e_daemon.py" "$port" >"$log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
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
for n in $(seq 1 20); do
    curl -fsS -X POST -H "$main" -H "Content-Type: application/json" \
        -d "{\"goal\":\"hello: chat number $n\",\"key\":\"k-$(uuidgen)\",\"attachments\":[],\"mode\":\"task\",\"standing\":false,\"speed\":\"standard\",\"plan_first\":false}" \
        "$base/api/runs" >/dev/null
done
sleep 5
rm -rf "$derived/bulkdelete.xcresult"
fail=0
if ! TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" xcodebuild test-without-building -project "$ios/Dot.xcodeproj" \
    -scheme DotNext -destination "id=$udid" -derivedDataPath "$derived" -only-testing:DotNextUITests/BulkDeleteLiveUITests \
    -resultBundlePath "$derived/bulkdelete.xcresult" >"$t/xcodebuild.log" 2>&1; then
    echo "FAIL  BulkDeleteLiveUITests (its log: $t/xcodebuild.log)" >&2
    fail=1
fi
grep -E "error:|Executed|took|TEST EXECUTE (SUCCEEDED|FAILED)|skipped" "$t/xcodebuild.log" | sort -u
left=$(curl -fsS -H "$main" "$base/api/threads?limit=50" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
if [ "$left" = "0" ]; then echo "ok    no chat is left on the Mac"; else echo "FAIL  $left chats left on the Mac" >&2; fail=1; fi
exit $fail

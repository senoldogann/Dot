#!/bin/sh
# The rows' states end to end (item 1920): a throwaway daemon (e2e_daemon.py, its scripted model) in three mktemp
# folders, a phone paired with a code, five agents each with one chat in one state (an approval that waits, a run at
# work, a run that waits on a helper, a command left in the background, an answer not yet seen), then RowStatesLiveUITests
# reads each bot's row on the home, Dot's ring, and pins a bot. Nothing touches the owner's Dot; no model is called.
# The app must be built for testing in <derived data> first (xcodebuild build-for-testing).
#
# Usage: ios/Next/scripts/e2e_row_states.sh <simulator udid> <derived data folder>
set -eu
udid=${1:?usage: e2e_row_states.sh <simulator udid> <derived data>}
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
agent() {
    curl -fsS -X POST -H "$main" -H "Content-Type: application/json" \
        -d "{\"name\":\"$1\",\"role\":\"$1\",\"instructions\":\"\",\"model\":\"\",\"effort\":null,\"approvals\":null}" "$base/api/agents" |
        python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("record") or d)["id"])'
}
say() {
    curl -fsS -X POST -H "$main" -H "Content-Type: application/json" \
        -d "{\"goal\":\"$2\",\"key\":\"k-$(uuidgen)\",\"attachments\":[],\"mode\":\"task\",\"standing\":false,\"speed\":\"standard\",\"plan_first\":false,\"agent_id\":\"$1\"}" \
        "$base/api/runs" >/dev/null
}
# The run that waits on a helper goes first: a helper is refused while the Mac already runs as many runs as it allows.
say "$(agent Delegator)" "waithelper: check the tests"
sleep 3
say "$(agent Reader)" "hello: say hi"
say "$(agent Runner)" "bg: start the server"
say "$(agent Asker)" "approve: touch the file"
say "$(agent Worker)" "wait: for the build"
rm -rf "$derived/rowstates.xcresult"
fail=0
if ! TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" xcodebuild test-without-building -project "$ios/Dot.xcodeproj" \
    -scheme DotNext -destination "id=$udid" -derivedDataPath "$derived" -only-testing:DotNextUITests/RowStatesLiveUITests \
    -resultBundlePath "$derived/rowstates.xcresult" >"$t/xcodebuild.log" 2>&1; then
    echo "FAIL  RowStatesLiveUITests (its log: $t/xcodebuild.log)" >&2
    fail=1
fi
grep -E "error:|Executed|TEST EXECUTE (SUCCEEDED|FAILED)|skipped" "$t/xcodebuild.log" | sort -u
exit $fail

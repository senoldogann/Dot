#!/bin/sh
# Bots end to end (PLAN §27, items 1881-1885): a throwaway daemon (e2e_daemon.py, its scripted model) in three mktemp
# folders, a phone paired with a code, then BotsLiveUITests makes a bot from the home, opens its menu, its profile and
# the settings sheet on the simulator, and this script reads on the daemon's side that the agent was made and greeted.
# Nothing touches the owner's Dot; no model is called. The test's shots land in <derived data>/bots.xcresult.
#
# The daemon is seeded with an agent, Scribe, that has work (``--seed-work``): the profile's Work group reads its orders,
# objectives and hand-offs, and the profile's menu pauses it.
#
# Usage: ios/Next/scripts/e2e_bots.sh <simulator udid> <derived data folder> [<test, as DotNextUITests/BotsLiveUITests/testX>]
set -eu
udid=${1:?usage: e2e_bots.sh <simulator udid> <derived data>}
derived=${2:?derived data folder}
only=${3:-DotNextUITests/BotsLiveUITests}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
daemon=$(dirname "$ios")
port=$(python3 -c "import socket; s=socket.socket(); s.bind((\"127.0.0.1\", 0)); print(s.getsockname()[1])")
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$daemon" python "$here/e2e_daemon.py" "$port" --seed-work >"$log" 2>&1 &
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
rm -rf "$derived/bots.xcresult"
# The UI test's own assertions (the pill, the menu, the profile, the sheet) count as much as the daemon's side below.
fail=0
if ! TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" xcodebuild test -project "$ios/Dot.xcodeproj" -scheme DotNext \
    -destination "id=$udid" -configuration Debug CODE_SIGN_IDENTITY=- -derivedDataPath "$derived" \
    -only-testing:"$only" -resultBundlePath "$derived/bots.xcresult" >"$t/xcodebuild.log" 2>&1; then
    echo "FAIL  BotsLiveUITests (xcodebuild test failed; its log: $t/xcodebuild.log)" >&2
    fail=1
fi
grep -E "error:|Executed|TEST (SUCCEEDED|FAILED)|skipped" "$t/xcodebuild.log" | sort -u

check() {
    if python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if ($2) else 1)"; then echo "ok    $1"; else echo "FAIL  $1" >&2; fail=1; fi
}
case "$only" in
*/BotsLiveUITests | *testABotMade*)
    curl -fsS -H "$main" "$base/api/agents" | check "the bot Scout was made with its job as its role" \
        'any(a["record"]["name"] == "Scout" and a["record"]["role"].startswith("Watch the market") for a in d)'
    curl -fsS -H "$main" "$base/api/threads?limit=20" | check "Scout's first meeting has a chat" \
        'any(t["title"] == "Scout" for t in d)'
    # The new chat of the bot's menu carries the bot: its first message is a run of Scout's own.
    curl -fsS -H "$main" "$base/api/runs?limit=20" | check "the new chat's first message went to Scout" \
        'any(r["goal"] == "Hello from a new chat" and r["agent_id"] is not None for r in d)'
    # The swiped message went out with its quote in front, the words the model reads (item 1972).
    curl -fsS -H "$main" "$base/api/runs?limit=20" | check "the quote rides on the message" \
        'any(r["goal"] == "> Hello from a new chat\n\nSay it again" for r in d)'
    # Edit's avatar page changed the look on the Mac; the first meeting asks for one answer, no tool (item 1962).
    curl -fsS -H "$main" "$base/api/agents" | check "Scout's new look was kept" \
        'any(a["record"]["name"] == "Scout" and a["record"]["look"] is not None and a["record"]["look"]["shape"] == "cloud" and a["record"]["look"]["colour"] == "teal" for a in d)'
    curl -fsS -H "$main" "$base/api/runs?limit=20" | check "Scout's first meeting asks for a message of its own and no tool" \
        'any(r["source"] == "welcome" and "Call no tool" in r["goal"] for r in d)'
    ;;
esac
# The profile's menu paused Scribe, the agent the daemon was seeded with.
case "$only" in
*/BotsLiveUITests | *testABotsWork*)
    curl -fsS -H "$main" "$base/api/agents" | check "the profile's menu paused Scribe" \
        'any(a["record"]["name"] == "Scribe" and a["record"]["paused_at"] is not None for a in d)'
    ;;
esac
exit $fail

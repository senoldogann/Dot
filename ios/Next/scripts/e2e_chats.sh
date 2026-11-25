#!/bin/sh
# The chat list and a new chat's settings end to end (items 1800-1801): a throwaway daemon
# (e2e_daemon.py) in three mktemp folders, a phone paired with a code, two "[test]" chats made, then ChatListLiveUITests
# taps on the simulator and this script reads on the daemon's side what each tap did: the new chat's run on the model,
# level and approvals picked in its settings (a real model: the owner's Claude plan through the `claude` CLI, which the
# throwaway home reaches as the owner's own Dot does), the deleted chats (the pins are checked by the UI test, which the
# script's result counts). Nothing touches the owner's Dot; the "[test]" chats live in the throwaway home.
#
# Usage: ios/Next/scripts/e2e_chats.sh <simulator udid> <derived data folder> [model ref, default claude_code:haiku]
set -eu
udid=${1:?usage: e2e_chats.sh <simulator udid> <derived data> [model ref]}
derived=${2:?derived data folder}
ref=${3:-claude_code:haiku}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
# A free port, picked now: another helper's daemon may hold any fixed one.
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$(dirname "$ios")" python "$here/e2e_daemon.py" "$port" >"$log" 2>&1 &
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
label=$(curl -fsS -H "$main" "$base/api/models" | python3 -c "
import json,sys
for p in json.load(sys.stdin):
    for m in p['models']:
        if m['ref'] == '$ref': print(m['label']); sys.exit(0)
sys.exit('the daemon offers no model $ref')")
echo "model  $ref ($label)"
for words in "[test] list one" "[test] list two"; do
    curl -fsS -X POST -H "$main" -H "Content-Type: application/json" -d "{\"goal\":\"$words\",\"mode\":\"chat\"}" "$base/api/runs" >/dev/null
done
# A clean app: its cache and outbox would hold the chats of an earlier throwaway daemon at the same address.
xcrun simctl uninstall "$udid" dev.dotagent.phone.next 2>/dev/null || true


# A UI test that fails fails the script (its xcodebuild's status, not the filter's).
run_test() {
    if ! TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" TEST_RUNNER_DOTNEXT_E2E_MODEL="$label" xcodebuild test \
        -project "$ios/Dot.xcodeproj" -scheme DotNext -destination "id=$udid" -configuration Debug CODE_SIGN_IDENTITY=- \
        -derivedDataPath "$derived" -only-testing:"DotNextUITests/ChatListLiveUITests/$1" >"$t/test-$1.log" 2>&1; then
        echo "FAIL  the UI test $1" >&2
        fail=1
    fi
    grep -E "error:|Executed|TEST (SUCCEEDED|FAILED)|skipped" "$t/test-$1.log" | sort -u
}

fail=0
check() {
    if python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if ($2) else 1)"; then echo "ok    $1"; else echo "FAIL  $1" >&2; fail=1; fi
}
run_test testSeveralChatsArePinnedAndDeletedTogether
curl -fsS -H "$main" "$base/api/threads" | check "Select: the two [test] chats were deleted together" 'not any(r["title"].startswith("[test] list") for r in d)'
run_test testANewChatRunsOnTheSettingsPickedBeforeItsFirstMessage
# The run ends on its own (a real model's answer); its record says what it ran on.
tries=0
until curl -fsS -H "$main" "$base/api/runs?limit=5" | python3 -c '
import json,sys
runs=[r for r in json.load(sys.stdin) if r["goal"].startswith("[test] Reply")]
sys.exit(0 if runs and runs[0]["status"] in ("done","failed") else 1)'; do
    tries=$((tries + 1))
    if [ "$tries" -gt 90 ]; then echo "FAIL  the run did not end in 3 min" >&2; fail=1; break; fi
    sleep 2
done
curl -fsS -H "$main" "$base/api/runs?limit=5" | check "New chat: the run is on $ref, level low, approvals yolo, and it answered" \
    "[(r['model'], r['effort'], r['approvals'], r['status']) for r in d if r['goal'].startswith('[test] Reply')][0] == ('$ref', 'low', 'yolo', 'done')"
curl -fsS -H "$main" "$base/api/runs?limit=5" | python3 -c '
import json,sys
for r in json.load(sys.stdin):
    if r["goal"].startswith("[test] Reply"): print("      run", r["id"], r["model"], r["effort"], r["approvals"], r["status"])'
# The "[test]" chats go with the throwaway home; deleted here too, as the owner asked of test chats.
ids=$(curl -fsS -H "$main" "$base/api/threads" | python3 -c 'import json,sys; print(json.dumps({"ids":[r["id"] for r in json.load(sys.stdin) if r["title"].startswith("[test]")]}))')
# An empty list is refused (422): only chats left are deleted.
if [ "$ids" != '{"ids": []}' ]; then
    curl -fsS -X DELETE -H "$main" -H "Content-Type: application/json" -d "$ids" "$base/api/threads" >/dev/null
fi
exit $fail

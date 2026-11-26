#!/bin/sh
# A chat's message actions and the run inspector end to end (package W4): a throwaway daemon (e2e_daemon.py) in three
# mktemp folders, a phone paired with a code, six chats made with the script's words in yolo, then
# ChatActionsLiveUITests walks them twice on the simulator: in the dark look opening each screen (shots only), in the
# light look tapping every action (the look is the app's own setting, written before each walk); this script then reads on the daemon's side what each tap did. Nothing touches the
# owner's Dot; no model is called (the script's model answers). The shots are written to <shots folder>.
#
# Usage: ios/Next/scripts/e2e_chat_actions.sh <simulator udid> <derived data folder> <shots folder>
#   Wrap it in the helpers' Xcode lock (/tmp/dotnext-xcode.lock): it runs xcodebuild test twice.
set -eu
udid=${1:?usage: e2e_chat_actions.sh <simulator udid> <derived data> <shots folder>}
derived=${2:?derived data folder}
shots=${3:?shots folder}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
repo=$(dirname "$ios")
port=18795
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$repo" python "$here/e2e_daemon.py" "$port" >"$log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT
tries=0
until grep -q READY "$log"; do
    tries=$((tries + 1))
    if [ "$tries" -gt 600 ]; then echo "the daemon never said READY:" >&2; cat "$log" >&2; exit 1; fi
    perl -e 'select(undef, undef, undef, 0.2)'
done
base="http://127.0.0.1:$port"
main="Authorization: Bearer dotnext-e2e-token"
json="Content-Type: application/json"
code=$(curl -fsS -X POST -H "$main" "$base/api/phone/pairing" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
key=$(curl -fsS -X POST -H "$json" -d "{\"code\":\"$code\"}" "$base/api/phone/pair" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

start() {
    curl -fsS -X POST -H "$main" -H "$json" -d "{\"goal\":\"$1\",\"approvals\":\"yolo\"}" "$base/api/runs" |
        python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["id"], d["thread_id"])'
}
set -- "$(start 'hello e2e')" "$(start 'fail: e2e')" "$(start 'draft: e2e')" "$(start 'write: e2e')" "$(start 'bg: e2e')" "$(start 'helper: e2e')"
hello_thread=${1#* }
fail_run=${2% *}
fail_thread=${2#* }
draft_run=${3% *}
write_run=${4% *}
bg_thread=${5#* }
sleep 8

walk() {
    xcrun simctl spawn "$udid" defaults write dev.dotagent.phone.next colorScheme "$1"
    rm -rf "$t/$1.xcresult"
    TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" TEST_RUNNER_DOTNEXT_W4_WRITES="$2" TEST_RUNNER_DOTNEXT_W4_LOOK="$1" \
        xcodebuild test-without-building -project "$ios/Dot.xcodeproj" -scheme DotNext -destination "id=$udid" -configuration Debug \
        -derivedDataPath "$derived" -resultBundlePath "$t/$1.xcresult" -only-testing:DotNextUITests/ChatActionsLiveUITests 2>&1 |
        grep -E "error:|Executed|TEST (EXECUTE )?(SUCCEEDED|FAILED)|skipped" | sort -u
    mkdir -p "$shots"
    xcrun xcresulttool export attachments --path "$t/$1.xcresult" --output-path "$t/$1-attachments" >/dev/null
    python3 - "$t/$1-attachments" "$shots" <<'PY'
import json, shutil, sys
from pathlib import Path
source, target = Path(sys.argv[1]), Path(sys.argv[2])
for test in json.loads((source / "manifest.json").read_text()):
    for item in test["attachments"]:
        name = item["suggestedHumanReadableName"].split("_0_")[0]
        if not name.startswith("w4-"):
            continue
        shutil.copy(source / item["exportedFileName"], target / f"{name}.png")
PY
}
# The app starts with nothing cached, so no chat of an earlier throwaway daemon at the same address is drawn
# (test-without-building installs it again).
xcrun simctl uninstall "$udid" dev.dotagent.phone.next || true
walk dark 0
walk light 1
xcrun simctl spawn "$udid" defaults delete dev.dotagent.phone.next colorScheme

fail=0
check() {
    if python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if ($2) else 1)"; then echo "ok    $1"; else echo "FAIL  $1" >&2; fail=1; fi
}
curl -fsS -H "$main" "$base/api/runs?thread_id=$hello_thread&limit=10" | check "Edit from here: the chat went back to before the message" 'd == []'
curl -fsS -H "$main" "$base/api/runs?thread_id=$fail_thread&limit=10" | check "Retry: the failed run left, the message went again; Continue resumed it" \
    "'$fail_run' not in [r['id'] for r in d] and any(r['goal'] == 'fail: e2e' for r in d) and any(r['source'] == 'resume' for r in d)"
curl -fsS -H "$main" "$base/api/runs/$draft_run/events" | python3 -c '
import json, sys
print(json.dumps([e["data"]["id"] for e in json.load(sys.stdin) if e["kind"] == "draft_created"]))' >"$t/drafts.json"
draft=$(python3 -c "import json; print(json.load(open('$t/drafts.json'))[0])")
curl -fsS -H "$main" "$base/api/drafts/$draft" | check "Draft: discarded, nothing sent" 'd["status"] == "discarded"'
curl -fsS -H "$main" "$base/api/runs/$write_run/actions" | check "Undo: the written file was taken back" 'all(e["state"] != "ready" for e in d["edits"])'
curl -fsS -H "$main" "$base/api/runs/$write_run/recap" | check "Recap: one was written" 'd["recap"] is not None'
curl -fsS -H "$main" "$base/api/threads/$bg_thread/terminals" | check "Background command: stopped" 'all(s["ended"] or not s["background"] for s in d)'
exit $fail

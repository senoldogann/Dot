#!/bin/sh
# Watching from the phone end to end (items 1838-1840): a throwaway daemon (e2e_daemon.py) in three mktemp folders, a
# phone paired with a code, a run that opens a local play page in its headless browser and keeps it open ("browse:"),
# then WatchLiveUITests: Watch the Mac from Activity, the run's mini player, full screen, Take over, a tap and typed
# text on the page, Hand back. This script then reads the run's handback event and the page's own state: its button's
# click count and its field's text, which the page reports to its own server.
# Nothing touches the owner's Dot; no model is called. The Mac's own screen is captured while Watch the Mac is open.
#
# Usage: ios/Next/scripts/e2e_watch.sh <simulator udid> <derived data folder>
set -eu
udid=${1:?usage: e2e_watch.sh <simulator udid> <derived data>}
derived=${2:?derived data folder}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
# A free port of this Mac: a fixed one may still be held by a daemon that is stopping.
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$(dirname "$ios")" python "$here/e2e_daemon.py" "$port" >"$log" 2>&1 &
pid=$!
# uv's child is the daemon: both go, so the port is free for the next run.
trap 'kill $pid 2>/dev/null || true; pkill -f "e2e_daemon.py $port" || true' EXIT
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
run=$(curl -fsS -X POST -H "$main" -H "Content-Type: application/json" \
    -d '{"goal":"browse: the play page","key":"e2e-watch-run-000001","attachments":[],"mode":"auto","standing":false}' "$base/api/runs" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "run $run"
# A clean app: a cache of another daemon's chats would open one of those instead of this run's.
xcrun simctl uninstall "$udid" dev.dotagent.phone.next 2>/dev/null || true

TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" xcodebuild test -project "$ios/Dot.xcodeproj" -scheme DotNext \
    -destination "id=$udid" -configuration Debug CODE_SIGN_IDENTITY=- -derivedDataPath "$derived" -resultBundlePath "$t/watch.xcresult" \
    -only-testing:DotNextUITests/WatchLiveUITests 2>&1 | grep -E "error:|Executed|TEST (SUCCEEDED|FAILED)|skipped" | sort -u
echo "result bundle: $t/watch.xcresult"

fail=0
curl -fsS -H "$main" "$base/api/runs/$run/events" >"$t/events.json"
python3 - "$t/events.json" "$t/home/play-page.json" <<'PY' || fail=1
import json, os, sys
events = json.load(open(sys.argv[1]))
events = events if isinstance(events, list) else events.get("events", [])
takeovers = [e for e in events if e["kind"] == "takeover"]
handbacks = [e for e in events if e["kind"] == "handback"]
held = bool(takeovers) and takeovers[0]["data"].get("by") == "screen" and bool(handbacks)
inputs = handbacks[0]["data"].get("inputs", {}) if handbacks else {}
# What the page itself received, as it reported it to its own server (e2e_daemon.py's play page).
page = json.load(open(sys.argv[2])) if os.path.exists(sys.argv[2]) else {}
clicked = page.get("clicks") == 2
typed = page.get("text") == "hi"
print(("ok   " if held else "FAIL ") + "held from the phone's screen and handed back", takeovers[0]["data"] if takeovers else None)
print("     the daemon counted", inputs)
print(("ok   " if clicked else "FAIL ") + "the page's button took the two taps", page)
print(("ok   " if typed else "FAIL ") + "the page's field holds what was typed", page)
sys.exit(0 if held and clicked and typed else 1)
PY
exit $fail

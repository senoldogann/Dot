#!/bin/sh
# Settings end to end (wave N3): a throwaway daemon (e2e_daemon.py --seed) in three mktemp folders, a phone paired with
# a code, a schedule made, then SettingsLiveUITests taps every page that writes on the simulator, and this script reads
# on the daemon's side what each tap wrote. Nothing touches the owner's Dot; no model is called.
#
# Usage: ios/Next/scripts/e2e_settings.sh <simulator udid> <derived data folder> [repo checkout for the daemon]
#   The third argument runs the daemon of another checkout (e.g. a branch with item 1731's caps); this checkout's
#   e2e_daemon.py is used either way.
set -eu
udid=${1:?usage: e2e_settings.sh <simulator udid> <derived data> [daemon checkout]}
derived=${2:?derived data folder}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
daemon=${3:-$(dirname "$ios")}
port=18793
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$daemon" python "$here/e2e_daemon.py" "$port" --seed >"$log" 2>&1 &
pid=$!
# `uv run` may outlive a kill of its own pid by a moment: the daemon is stopped by its command line too, so that a
# second run of this script finds the port free.
trap 'kill $pid 2>/dev/null || true; pkill -f "e2e_daemon.py $port" 2>/dev/null || true' EXIT
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
phone="Authorization: Bearer $key"
curl -fsS -X POST -H "$main" -H "Content-Type: application/json" \
    -d '{"name":"E2E brief","goal":"Say good morning.","cron":"0 8 * * *","mode":"task"}' "$base/api/schedules" >/dev/null
curl -fsS -X PUT -H "$main" -H "Content-Type: application/json" -d '{"free":true}' "$base/api/policy/free" >/dev/null
curl -fsS -X POST -H "$main" -H "Content-Type: application/json" \
    -d '{"name":"E2E Scout","role":"Finds leads","instructions":"","model":"","effort":null,"approvals":null}' "$base/api/agents" >/dev/null

# The UI test's own assertions (what each page shows) count as much as the daemon's side below: its status is kept.
fail=0
if ! TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" xcodebuild test -project "$ios/Dot.xcodeproj" -scheme DotNext \
    -destination "id=$udid" -configuration Debug CODE_SIGN_IDENTITY=- -derivedDataPath "$derived" \
    -only-testing:DotNextUITests/SettingsLiveUITests -only-testing:DotNextUITests/SettingsMaintenanceLiveUITests \
    -only-testing:DotNextUITests/ConnectAppsLiveUITests >"$t/xcodebuild.log" 2>&1; then
    echo "FAIL  SettingsLiveUITests, SettingsMaintenanceLiveUITests or ConnectAppsLiveUITests (xcodebuild test failed; its log: $t/xcodebuild.log)" >&2
    fail=1
fi
grep -E "error:|Executed|TEST (SUCCEEDED|FAILED)|skipped" "$t/xcodebuild.log" | sort -u

check() {
    if python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if ($2) else 1)"; then echo "ok    $1"; else echo "FAIL  $1" >&2; fail=1; fi
}
curl -fsS -H "$phone" "$base/api/phone/push-settings" | check "Notifications: Done or failed is off" 'd["done"] is False and d["needs_you"] is True'
curl -fsS -H "$main" "$base/api/assistant" | check "General: Dot's name is Nokta" 'd["name"] == "Nokta"'
curl -fsS -H "$main" "$base/api/reactions/settings" | check "Chat: reactions start no follow-up" 'd["follow_ups"] is False'
curl -fsS -H "$main" "$base/api/schedules" | check "Scheduled tasks: paused and renamed, its rhythm kept" \
    'd[0]["record"]["enabled"] is False and d[0]["record"]["name"] == "E2E brief edited" and d[0]["record"]["cron"] == "0 8 * * *"'
curl -fsS -H "$main" "$base/api/code-review" | check "Models: the code review's reasoning set from the phone" 'd["effort"] == "high"'
curl -fsS -H "$main" "$base/api/policy/free" | check "Rules: free mode switched off from the phone" 'd["free"] is False'
curl -fsS -H "$main" "$base/api/agents" | check "Agents: the agent's first chat opened from the phone" 'any(a["record"]["name"] == "E2E Scout" and a["home"] is not None for a in d)'
curl -fsS -H "$main" "$base/api/memories?limit=50" | check "Memory: the note edited from the phone" 'any("Edited." in m["content"] for m in d["items"])'
curl -fsS -H "$main" "$base/api/memories?limit=50" | check "Memory: a note added from the phone, with its tags, restored from the trash" \
    'any(m["content"] == "Phone note about the invoices." and m["tags"] == ["phone", "e2e"] for m in d["items"])'
curl -fsS -H "$main" "$base/api/memories/trash" | check "Memory: the trash is empty again" 'd["total"] == 0'
curl -fsS -H "$main" "$base/api/memories/proposed" | check "Memory: no proposal waits" 'd == []'
curl -fsS -H "$main" "$base/api/memories?limit=10" | check "Memory: the proposed words were used" \
    'any("commands written out in full" in (m.get("content") or m.get("record", {}).get("content", "")) for m in (d if isinstance(d, list) else d.get("items", [])))'
curl -fsS -H "$main" "$base/api/campaigns" | check "Campaigns: the seeded campaign was stopped, then resumed" 'd[0]["record"]["status"] == "active"'
if curl -fsS -H "$main" "$base/api/budget" >"$t/budget.json" 2>/dev/null; then
    check "Caps: one task lowered to 300M, the raise refused" 'd["caps"]["run_tokens"] == 300_000_000' <"$t/budget.json"
else
    echo "skip  Caps: this daemon has no /api/budget (item 1731 not merged): the page said Update Dot"
fi
curl -fsS -H "$main" "$base/api/status" | check "Maintenance: interrupted tasks are not resumed" 'd["resume"]["resume"] == "off"'
grep -c "PhoneRefused\|a phone cannot" "$log" | sed 's/^/      refusals to the phone in the daemon log: /'
exit $fail

#!/bin/sh
# The Activity tab end to end (item 1826): a throwaway daemon (e2e_daemon.py --seed-activity) in three mktemp folders,
# "while you were away" marked read from before the seeded runs, two watchers and the morning brief set as the Mac
# would, a phone paired with a code; then ActivityLiveUITests reads every part on the simulator, keeps a screenshot of
# each look (light and dark, exported to <shots dir>) and taps every control that writes, and this script reads on the
# daemon's side what each tap wrote. Nothing touches the owner's Dot; no model is called; no draft is sent.
#
# Usage: ios/Next/scripts/e2e_activity.sh <simulator udid> <derived data folder> <shots dir> [en|tr]
#   In tr the shots and the measures of the design bar run in Turkish; the writes are checked in the en run alone.
set -eu
udid=${1:?usage: e2e_activity.sh <simulator udid> <derived data> <shots dir> [en|tr]}
derived=${2:?derived data folder}
shots=${3:?shots dir}
lang=${4:-en}
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$(dirname "$here")")
port=18794
t=$(mktemp -d)
log="$t/daemon.log"
DOT_HOME="$t/home" DOT_LOG_DIR="$t/logs" DOT_CHROME_DIR="$t/chrome" \
    uv run --project "$(dirname "$ios")" python "$here/e2e_daemon.py" "$port" --seed-activity >"$log" 2>&1 &
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
json="Content-Type: application/json"
curl -fsS -X POST -H "$main" -H "$json" -d '{"until": 1000}' "$base/api/away/read" >/dev/null
curl -fsS -X POST -H "$main" -H "$json" \
    -d '{"name":"Reviews asked of me","kind":"github","account":"","words":"","minutes":10,"action":"tell","goal":""}' "$base/api/watchers" >/dev/null
curl -fsS -X POST -H "$main" -H "$json" \
    -d '{"name":"Landlord","kind":"mail","account":"me@example.com","words":"my landlord, the rent, the lease","minutes":5,"action":"tell","goal":""}' \
    "$base/api/watchers" >/dev/null
curl -fsS -X PUT -H "$main" -H "$json" -d '{"enabled":true,"time":"07:30","days":"weekdays","lines":["The roof repair"]}' "$base/api/brief" >/dev/null
code=$(curl -fsS -X POST -H "$main" "$base/api/phone/pairing" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
key=$(curl -fsS -X POST -H "$json" -d "{\"code\":\"$code\"}" "$base/api/phone/pair" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

(cd "$ios" && xcodegen generate >/dev/null)
TEST_RUNNER_DOTNEXT_E2E_HOST="$base" TEST_RUNNER_DOTNEXT_E2E_KEY="$key" TEST_RUNNER_DOTNEXT_E2E_LANG="$lang" xcodebuild test -project "$ios/Dot.xcodeproj" -scheme DotNext \
    -destination "id=$udid" -configuration Debug CODE_SIGN_IDENTITY=- -derivedDataPath "$derived" -resultBundlePath "$t/result.xcresult" \
    -only-testing:DotNextUITests/ActivityLiveUITests >"$t/test.log" 2>&1 || true
grep -E "error:|Executed|TEST (SUCCEEDED|FAILED)|skipped" "$t/test.log" | sort -u
mkdir -p "$shots"
xcrun xcresulttool export attachments --path "$t/result.xcresult" --output-path "$t/attachments" >/dev/null || true
python3 - "$t/attachments" "$shots" <<'PY'
import json, shutil, sys
from pathlib import Path
source, target = Path(sys.argv[1]), Path(sys.argv[2])
for test in json.loads((source / "manifest.json").read_text()):
    for attachment in test["attachments"]:
        if attachment.get("suggestedHumanReadableName", "").startswith(("UI Snapshot", "Synthesized Event")):
            continue
        exported = attachment["exportedFileName"]
        name = attachment.get("suggestedHumanReadableName", exported).split("_")[0]
        shutil.copy(source / exported, target / f"{name}{Path(exported).suffix}")
PY
ls "$shots"
if ! grep -q "TEST SUCCEEDED" "$t/test.log"; then
    echo "FAIL  ActivityLiveUITests did not run every test or one failed: $t/test.log" >&2
    exit 1
fi
cat "$shots"/*measures* 2>/dev/null || true
if [ "$lang" != en ]; then exit 0; fi

fail=0
# check <words> <route> <condition on d>: the daemon's answer to the Mac's token, read in this shell so a failure counts.
check() {
    curl -fsS -H "$main" "$base$2" >"$t/answer.json"
    if python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if ($3) else 1)" "$t/answer.json"; then echo "ok    $1"; else echo "FAIL  $1" >&2; fail=1; fi
}
check "Ready: the mail draft discarded, the failed message waits" /api/drafts \
    'len(d) == 1 and d[0]["channel"] == "message" and d[0]["status"] == "failed"'
check "Proposals: one in use, the one from outside content set aside" /api/harness \
    'd["adopted"] is not None and d["adopted"]["summary"].startswith("Run the tests") and [v["state"] for v in d["versions"] if v["summary"].startswith("Read the README")] == ["retired"]'
check "Morning brief: switched off, its time kept" /api/brief 'd["enabled"] is False and d["time"] == "07:30"'
check "Evening check-in: switched on" /api/checkin 'd["enabled"] is True'
check "On its own: on, Downloads let go, the passport on the list" /api/initiative \
    'd["enabled"] is True and {t["title"]: t["status"] for t in d["todos"]}.get("Tidy the Downloads folder") == "dropped" and {t["title"]: t["status"] for t in d["todos"]}.get("Renew the passport") == "open"'
check "Watchers: Landlord off, its choices kept, the other on" /api/watchers \
    '[(w["name"], w["enabled"], w["words"]) for w in d] == [("Reviews asked of me", True, ""), ("Landlord", False, "my landlord, the rent, the lease")]'
check "While you were away: marked read" /api/away/read 'd["read_at"] > 1000'
exit $fail

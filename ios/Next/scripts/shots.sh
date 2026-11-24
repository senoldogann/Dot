#!/bin/sh
# The screenshot harness of DotNext (lead's order, G2 #10): every scene in light and dark, at the Large text size and at
# AX3, on each simulator given, in each language given. The app is launched with `-DotNextScene <name>`, says when it is
# drawn (Library/Caches/dotnext-ready.txt in its container) and only then is the screen captured.
#
# Usage: ios/Next/scripts/shots.sh <DotNext.app> <out dir> <languages, e.g. "tr en"> <simulator udid>...
#   The simulators must be booted; the app is installed on each. Scenes: the gallery pages, the four tabs, the chat.
#   Files: <out>/<device>-<lang>-<light|dark>-<L|AX3>-<scene>.png
set -eu
app=${1:?usage: shots.sh <DotNext.app> <out dir> <languages> <udid>...}
out=${2:?out dir}
languages=${3:?languages}
shift 3
bundle=dev.dotagent.phone.next
# DOTNEXT_SCENES picks some of them (e.g. the live screens against a paired throwaway daemon: "chats live-chat settings").
scenes=${DOTNEXT_SCENES:-"tokens rows controls feedback transcript helpers chrome symbols chats chat chat-agents chat-background agents activity settings live-chat"}
mkdir -p "$out"
for udid in "$@"; do
    name=$(xcrun simctl list devices | grep "$udid" | sed -E 's/^ *(.*) \(.*\) \(.*$/\1/' | tr ' ' '_')
    xcrun simctl install "$udid" "$app"
    xcrun simctl status_bar "$udid" override --time 9:41 --batteryState charged --batteryLevel 100 --cellularBars 4 --wifiBars 3
    container=$(xcrun simctl get_app_container "$udid" "$bundle" data)
    for lang in $languages; do
        for look in light dark; do
            xcrun simctl ui "$udid" appearance "$look"
            for size in L AX3; do
                if [ "$size" = L ]; then category=large; else category=accessibility-extra-large; fi
                xcrun simctl ui "$udid" content_size "$category"
                for scene in $scenes; do
                    rm -f "$container/Library/Caches/dotnext-ready.txt"
                    xcrun simctl terminate "$udid" "$bundle" >/dev/null 2>&1 || true
                    xcrun simctl launch "$udid" "$bundle" -DotNextScene "$scene" -AppleLanguages "($lang)" -AppleLocale "$lang" >/dev/null
                    tries=0
                    until [ -f "$container/Library/Caches/dotnext-ready.txt" ]; do
                        tries=$((tries + 1))
                        if [ "$tries" -gt 150 ]; then echo "scene $scene never said ready on $name" >&2; exit 1; fi
                        perl -e 'select(undef, undef, undef, 0.1)'
                    done
                    # The mark comes from the app's first task; a loaded Mac can still be compositing the first frame.
                    perl -e 'select(undef, undef, undef, 0.6)'
                    shot="$out/$name-$lang-$look-$size-$scene.png"
                    if ! xcrun simctl io "$udid" screenshot --type=png "$shot" >"$out/.shot.log" 2>&1; then
                        echo "could not capture $shot:" >&2
                        cat "$out/.shot.log" >&2
                        exit 1
                    fi
                done
            done
        done
    done
    xcrun simctl ui "$udid" content_size large
    xcrun simctl status_bar "$udid" clear
done
rm -f "$out/.shot.log"
echo "shots in $out"

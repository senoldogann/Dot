#!/bin/bash
# A release of Dot's desktop app (item 432), built on the owner's Mac from a clean checkout of the commit to release:
# the app for Apple silicon and Intel, signed with the Developer ID (hardened runtime), notarized and stapled; the
# updater's archive of that app, signed with the updater's key and bound to its version; the disk image (the app and a
# link to Applications), signed, notarized and stapled; and latest.json, which the shell's updater reads from the latest
# release. They land in desktop/dist/<version>/, which git ignores. What a release needs once, and how it is published,
# is in docs/development.md (Releasing). The script publishes nothing: it prints the commands that do, in the order the
# app needs. The app installs the Python package of its own version from PyPI (item 575), and a v* tag publishes that
# package (.github/workflows/release.yml), once the whole gate passes again on it: so the tag goes first, PyPI lists the
# version, and only then is the release made, which is what puts the app (and the updater's latest.json) in front of
# anyone. The app also carries uv, ripgrep and the launcher of Dot.app (desktop/fetch-tools.sh, pinned in
# desktop/tools.lock): they are signed here, before the app is, since Apple's notary service asks it of every program
# inside an app. `release.sh --check-tools [--lock FILE]` only checks that the pinned archives are still the ones pinned.
# The app installs the package with every dependency at the version uv.lock pins (item 694), not the newest of the day:
# the release makes that list from uv.lock into the app's Resources before the app is signed, and checks the app's copy
# against uv.lock again once the app is built.
set -euo pipefail

if [ "${1:-}" = --check-tools ]; then
  # Only a lock file of another name may be given, to look at a change of the lock before it is committed. Nothing else
  # is passed on: a switch of fetch-tools.sh (the file addresses its tests take) cannot be given through this script.
  case $# in
    1) exec "$(dirname "$0")/fetch-tools.sh" --dry-run ;;
    3) if [ "$2" = --lock ]; then
         exec "$(dirname "$0")/fetch-tools.sh" --dry-run --lock "$3"
       fi ;;
  esac
  echo "release: --check-tools takes no argument but --lock FILE" >&2
  exit 1
fi

desktop=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$desktop")
conf=$desktop/src-tauri/tauri.conf.json
tauri=$desktop/node_modules/.bin/tauri
key=$HOME/.tauri/dot.key
notary_profile=dot-notary

fail() {
  echo "release: $*" >&2
  exit 1
}

# notarize FILE: hand FILE to Apple's notary service with the saved profile and wait for its verdict; any verdict but
# Accepted stops the release, with the service's log of what it found.
notarize() {
  local file=$1 name answer status id
  name=$(basename "$file")
  answer=$(xcrun notarytool submit "$file" --keychain-profile "$notary_profile" --wait --output-format json \
    2>"$work/notarytool.err") || true
  status=$(plutil -extract status raw -o - - <<<"$answer" 2>/dev/null) ||
    fail "notarytool did not take $name: $answer $(cat "$work/notarytool.err")"
  id=$(plutil -extract id raw -o - - <<<"$answer")
  if [ "$status" != Accepted ]; then
    echo "release: Apple's notary service answered $status for $name (submission $id); its log:" >&2
    xcrun notarytool log "$id" --keychain-profile "$notary_profile" >&2
    exit 1
  fi
  echo "release: $name notarized (submission $id)"
}

# version_in FILE PATTERN: the version a file states, the first line PATTERN (sed, its group the version) matches.
version_in() {
  sed -n "s/$2/\1/p" "$root/$1" | head -1
}

# constraints_to FILE: write to FILE the versions uv.lock pins for the package's runtime dependencies (not the
# development group, not the package itself), a `name==version` line each, with the markers that keep an entry to the
# Macs it is for. It is the uv the app carries that writes it (the one tools.lock pins, so that the file is the same
# whoever releases), and --locked stops it when uv.lock is not what pyproject.toml asks for. No hashes: `uv tool
# install` takes a constraints file's versions and ignores its hashes (item 694).
constraints_to() {
  (cd "$root" && "$desktop/src-tauri/resources/tools/uv" --no-config export --locked --no-dev --no-emit-project \
    --no-hashes --no-header --no-annotate --format requirements-txt --output-file "$1") ||
    fail "uv could not write the versions uv.lock pins to $1"
  [ -s "$1" ] || fail "uv.lock pins no dependency of the package: $1 is empty"
}

[ "$(uname -s)" = Darwin ] || fail "a release is built on a Mac"
cd "$root"
[ -z "$(git status --porcelain)" ] || fail "the checkout has files not committed; a release is built from a commit"
git fetch -q origin main
git merge-base --is-ancestor HEAD origin/main ||
  fail "$(git rev-parse --short HEAD) is not a commit of origin's main; a release is made from main"

version=$(plutil -extract version raw -o - "$conf")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "the version of tauri.conf.json is not x.y.z: $version"
for stated in \
  "desktop/src-tauri/Cargo.toml=$(version_in desktop/src-tauri/Cargo.toml '^version = "\(.*\)"$')" \
  "desktop/package.json=$(plutil -extract version raw -o - desktop/package.json)" \
  "pyproject.toml=$(version_in pyproject.toml '^version = "\(.*\)"$')" \
  "src/dotagent/__init__.py=$(version_in src/dotagent/__init__.py '^__version__ = "\(.*\)"$')"; do
  [ "${stated#*=}" = "$version" ] ||
    fail "${stated%%=*} says ${stated#*=} and tauri.conf.json $version: a release has one version"
done
tag=v$version
[ -z "$(git ls-remote --tags origin "refs/tags/$tag")" ] || fail "the tag $tag is on origin already: raise the version"
notes=$(awk -v heading="## $version " 'index($0, heading) == 1 { found = 1; next } /^## / { found = 0 } found' CHANGELOG.md)
[ -n "$notes" ] || fail "CHANGELOG.md has no section \"## $version (date)\" to publish with the release"

endpoint=$(plutil -extract plugins.updater.endpoints.0 raw -o - "$conf")
repo=${endpoint%/releases/latest/download/latest.json}
[[ $repo =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] ||
  fail "the updater's endpoint is not the latest.json of a GitHub repository's latest release: $endpoint"

[ -r "$key" ] || fail "the updater's private key is not at $key"
[ "$(plutil -extract plugins.updater.pubkey raw -o - "$conf")" = "$(cat "$key.pub")" ] ||
  fail "plugins.updater.pubkey of tauri.conf.json is not the public key of $key"
password=$(security find-generic-password -s dot-release -a TAURI_SIGNING_PRIVATE_KEY_PASSWORD -w) ||
  fail "the password of $key is not in the Keychain (service dot-release, account TAURI_SIGNING_PRIVATE_KEY_PASSWORD)"

# The app is built for both kinds of Mac: the Rust toolchain first in PATH needs the standard library of each.
sysroot=$(rustc --print sysroot)
for target in aarch64-apple-darwin x86_64-apple-darwin; do
  [ -d "$sysroot/lib/rustlib/$target" ] ||
    fail "the Rust toolchain at $sysroot cannot build for $target: put rustup's first in PATH and rustup target add $target"
done

identities=$(security find-identity -v -p codesigning |
  sed -n 's/^ *[0-9]*) [0-9A-F]\{40\} "\(Developer ID Application: .*\)"$/\1/p' | sort -u)
[ -n "$identities" ] || fail "no Developer ID Application identity in the keychain"
[ "$(wc -l <<<"$identities" | tr -d ' ')" = 1 ] ||
  fail "more than one Developer ID Application identity in the keychain: $identities"
identity=$identities
xcrun notarytool history --keychain-profile "$notary_profile" >/dev/null ||
  fail "notarytool has no profile $notary_profile in the Keychain"

out=$desktop/dist/$version
rm -rf "$out"
mkdir -p "$out"
printf '%s\n' "$notes" >"$out/notes.md"
work=$(mktemp -d)
# The release's pins leave the checkout however the release ends, so that a development build made after it carries the
# empty stand-in build.rs makes (desktop/src-tauri/build.rs), not these pins; the app holds its own copy.
trap 'rm -rf "$work"; rm -f "$desktop/src-tauri/resources/constraints.txt"' EXIT
name=Dot_${version}_universal

echo "release: building Dot $version, signed by $identity"
cd "$desktop"
# The tools the app carries, fetched against their pinned SHA-256s and signed with the Developer ID, the hardened
# runtime and a secure timestamp before the app, which then holds them in its Resources, is signed.
./fetch-tools.sh --sign "$identity"
# The versions of the package's dependencies, which the app bundles in its Resources (constraints.txt, beside tools) for
# the installer to give uv; made here, before the app is built, so that the signature covers it.
constraints_to "$desktop/src-tauri/resources/constraints.txt"
npm ci --no-audit --no-fund
# Tauri notarizes only with an Apple ID or an API key in its environment: the notary service is asked below with the
# saved profile, so the bundle Tauri makes is only signed.
env -u APPLE_ID -u APPLE_PASSWORD -u APPLE_TEAM_ID -u APPLE_API_KEY -u APPLE_API_ISSUER -u APPLE_API_KEY_PATH \
  APPLE_SIGNING_IDENTITY="$identity" "$tauri" build --target universal-apple-darwin --bundles app
app=$desktop/src-tauri/target/universal-apple-darwin/release/bundle/macos/Dot.app

codesign --verify --deep --strict "$app"
signature=$(codesign --display --verbose=4 "$app" 2>&1)
grep -qF "Authority=$identity" <<<"$signature" || fail "Dot.app is not signed by $identity"
grep -q 'flags=0x10000(runtime)' <<<"$signature" || fail "Dot.app is not signed with the hardened runtime"
grep -q '^Timestamp=' <<<"$signature" || fail "the signature of Dot.app has no secure timestamp"
# What the composer's dictation needs of macOS (item 594): the reasons the app asks for the microphone and speech
# recognition, and the microphone, the hardened runtime's one exception. A bundle without them is refused both.
for reason in NSMicrophoneUsageDescription NSSpeechRecognitionUsageDescription; do
  plutil -extract "$reason" raw -o /dev/null "$app/Contents/Info.plist" ||
    fail "the Info.plist of Dot.app has no $reason"
done
# The entitlements as a plist, read by key whatever the layout codesign gives them (PlistBuddy reads the key's dots as
# part of its name, where plutil would read a path).
codesign --display --entitlements - --xml "$app" >"$work/entitlements.plist" 2>"$work/codesign.err" ||
  fail "codesign could not read the entitlements of Dot.app: $(cat "$work/codesign.err")"
entitled=$(/usr/libexec/PlistBuddy -c 'Print :com.apple.security.device.audio-input' "$work/entitlements.plist" \
  2>&1 || true)
[[ $entitled == true ]] || fail "Dot.app is not entitled to the microphone (PlistBuddy: $entitled)"
archs=$(lipo -archs "$app/Contents/MacOS/$(plutil -extract CFBundleExecutable raw -o - "$app/Contents/Info.plist")")
[[ " $archs " == *" arm64 "* && " $archs " == *" x86_64 "* ]] ||
  fail "Dot.app is built for $archs, not for both Apple silicon and Intel"
# The tools folder of the app holds the three programs and nothing else: not a file that was lying in the source folder,
# not a link. It is first on the PATH of the service, which holds the keys of the owner's accounts, so what is in it
# is a program every user's daemon may run. (tauri.conf.json names the three one by one and fetch-tools.sh empties the
# source folder: this is the lock after those two, on the app that was built.)
"$desktop/fetch-tools.sh" --check-bundle "$app/Contents/Resources/tools"
for tool in uv rg dot-launcher; do
  program=$app/Contents/Resources/tools/$tool
  [ -x "$program" ] || fail "$tool is not in the app's Resources: the installer would have nothing to install with"
  codesign --verify --strict "$program"
  signature=$(codesign --display --verbose=4 "$program" 2>&1)
  grep -qF "Authority=$identity" <<<"$signature" || fail "$tool in the app is not signed by $identity"
  grep -q 'flags=0x10000(runtime)' <<<"$signature" || fail "$tool in the app is not signed with the hardened runtime"
  grep -q '^Timestamp=' <<<"$signature" || fail "the signature of $tool in the app has no secure timestamp"
  archs=$(lipo -archs "$program")
  [[ " $archs " == *" arm64 "* && " $archs " == *" x86_64 "* ]] || fail "$tool in the app is built for $archs only"
done

# The versions in the app are the ones uv.lock pins now: a lock changed, or a file in the app that is not the one made
# above, stops the release.
constraints_to "$work/constraints.txt"
cmp -s "$work/constraints.txt" "$app/Contents/Resources/constraints.txt" ||
  fail "the constraints.txt in the app is not what uv.lock pins: the installer would install untested versions"

ditto -c -k --keepParent "$app" "$work/Dot.zip"
notarize "$work/Dot.zip"
xcrun stapler staple "$app"
xcrun stapler validate "$app"
spctl --assess --type execute --verbose=2 "$app"

# The updater's archive holds the stapled app at its root, as Tauri's own does, without the extended attributes of
# this Mac's copy.
COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs -czf "$out/$name.app.tar.gz" -C "$(dirname "$app")" Dot.app
TAURI_SIGNING_PRIVATE_KEY_PASSWORD=$password "$tauri" signer sign --private-key-path "$key" --app-version "$version" \
  "$out/$name.app.tar.gz"
unset password
updater_signature=$(cat "$out/$name.app.tar.gz.sig")
[[ $updater_signature =~ ^[A-Za-z0-9+/=]+$ ]] || fail "the updater's signature is not base64: $updater_signature"

mkdir "$work/image"
ditto "$app" "$work/image/Dot.app"
ln -s /Applications "$work/image/Applications"
hdiutil create -volname Dot -srcfolder "$work/image" -fs HFS+ -format UDZO -ov "$out/$name.dmg"
codesign --sign "$identity" --timestamp "$out/$name.dmg"
notarize "$out/$name.dmg"
xcrun stapler staple "$out/$name.dmg"
xcrun stapler validate "$out/$name.dmg"
spctl --assess --type open --context context:primary-signature --verbose=2 "$out/$name.dmg"

# The same universal archive serves both kinds of Mac.
url=$repo/releases/download/$tag/$name.app.tar.gz
cat >"$out/latest.json" <<EOF
{
  "version": "$version",
  "notes": "Dot $version: $repo/blob/$tag/CHANGELOG.md",
  "pub_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "platforms": {
    "darwin-aarch64": { "signature": "$updater_signature", "url": "$url" },
    "darwin-x86_64": { "signature": "$updater_signature", "url": "$url" }
  }
}
EOF
for platform in darwin-aarch64 darwin-x86_64; do
  [ "$(plutil -extract "platforms.$platform.signature" raw -o - "$out/latest.json")" = "$updater_signature" ] ||
    fail "latest.json does not read back with the archive's signature for $platform"
done
[ "$(plutil -extract version raw -o - "$out/latest.json")" = "$version" ] || fail "latest.json does not read back"

cat <<EOF

release: Dot $version is ready in desktop/dist/$version:
$(cd "$out" && ls -1)

Publish it, from this commit ($(git -C "$root" rev-parse --short HEAD)), in this order: the app installs the Python
package of its own version from PyPI, so the package must be there before the app is anywhere anyone can get it.

  1. Tag the commit. The tag publishes dotagent-macos $version to PyPI (.github/workflows/release.yml) once the whole
     gate passes again on it, some eight minutes later (more, if the environment pypi waits for your click):

       git tag $tag $(git -C "$root" rev-parse HEAD) && git push origin $tag

  2. Wait until PyPI lists the version (a run that fails publishes nothing: fix main and release the next version):

       until curl -fsS https://pypi.org/pypi/dotagent-macos/$version/json >/dev/null; do sleep 30; done

  3. Only then make the release, which publishes the app, its update and latest.json:

       gh release create $tag --verify-tag --title "Dot $version" \\
         --notes-file desktop/dist/$version/notes.md \\
         desktop/dist/$version/$name.dmg desktop/dist/$version/$name.app.tar.gz desktop/dist/$version/latest.json
EOF

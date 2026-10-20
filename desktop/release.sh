#!/bin/bash
# A release of Dot's desktop app (item 432), built on the owner's Mac from a clean checkout of the commit to release:
# the app for Apple silicon and Intel, signed with the Developer ID (hardened runtime), notarized and stapled; the
# updater's archive of that app, signed with the updater's key and bound to its version; the disk image (the app and a
# link to Applications), signed, notarized and stapled; and latest.json, which the shell's updater reads from the latest
# release. They land in desktop/dist/<version>/, which git ignores. What a release needs once, and how it is published,
# is in docs/development.md (Releasing). The script publishes nothing: it prints the command that does, since a v* tag
# also publishes the Python package to PyPI (.github/workflows/release.yml).
set -euo pipefail

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

[ "$(uname -s)" = Darwin ] || fail "a release is built on a Mac"
cd "$root"
[ -z "$(git status --porcelain --untracked-files=no)" ] ||
  fail "the checkout has changes not committed; a release is built from a commit"

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
trap 'rm -rf "$work"' EXIT
name=Dot_${version}_universal

echo "release: building Dot $version, signed by $identity"
cd "$desktop"
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
archs=$(lipo -archs "$app/Contents/MacOS/$(plutil -extract CFBundleExecutable raw -o - "$app/Contents/Info.plist")")
[[ " $archs " == *" arm64 "* && " $archs " == *" x86_64 "* ]] ||
  fail "Dot.app is built for $archs, not for both Apple silicon and Intel"

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

Publish it, from this commit ($(git -C "$root" rev-parse --short HEAD)), with:

  gh release create $tag --target $(git -C "$root" rev-parse HEAD) --title "Dot $version" \\
    --notes-file desktop/dist/$version/notes.md \\
    desktop/dist/$version/$name.dmg desktop/dist/$version/$name.app.tar.gz desktop/dist/$version/latest.json

The tag $tag also publishes the Python package dotagent-macos $version to PyPI (.github/workflows/release.yml).
EOF

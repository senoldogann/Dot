#!/bin/bash
# An unsigned disk image and latest.json, from the build steps of desktop/release.sh: the tools the app
# carries, the hashed requirements and this version's wheel, then `tauri build --bundles dmg`. Nothing is
# signed or sent to Apple's notary service, and nothing is tagged or published. A signed release stays
# desktop/release.sh, on the owner's Mac.
#
#   desktop_artifact.sh --signing-plan    say whether signing secrets are absent, and that signing is skipped
#   desktop_artifact.sh                   build desktop/dist/artifact/ (a Mac)
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
desktop=$root/desktop

fail() {
  echo "artifact: $*" >&2
  exit 1
}

# signing_note: one line. The secrets are never printed. Absent or present, signing is skipped: this
# build uploads an unsigned artifact.
signing_note() {
  local name absent=1
  for name in \
    APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD APPLE_SIGNING_IDENTITY \
    APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID \
    APPLE_API_KEY APPLE_API_ISSUER APPLE_API_KEY_PATH \
    TAURI_SIGNING_PRIVATE_KEY TAURI_SIGNING_PRIVATE_KEY_PASSWORD; do
    if [ -n "${!name:-}" ]; then
      absent=0
    fi
  done
  if [ "$absent" = 1 ]; then
    echo "signing secrets are absent; codesign and notarization are skipped"
  else
    echo "signing secrets are present; codesign and notarization are skipped and this build uploads an unsigned artifact"
  fi
}

if [ "${1:-}" = --signing-plan ]; then
  signing_note
  exit 0
fi
[ $# -eq 0 ] || fail "usage: desktop_artifact.sh [--signing-plan]"

if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  py=(python3)
elif command -v uv >/dev/null 2>&1; then
  py=(uv run --no-project python)
else
  fail "python 3.11 or newer is required"
fi

version=$("${py[@]}" "$root/scripts/check_versions.py") || exit 1
[ "$(uname -s)" = Darwin ] || fail "a disk image is built on a Mac"
signing_note

# requirements_to FILE WHEEL_DIR: the same files desktop/release.sh writes before it builds the app, so the
# image carries the hashed requirements and this version's wheel. The uv the app carries writes them.
requirements_to() {
  local file=$1 wheel_dir=$2 uv=$desktop/src-tauri/resources/tools/uv dist name wheel hash
  (cd "$root" && "$uv" --no-config export --locked --no-dev --no-emit-project \
    --no-header --no-annotate --format requirements-txt --output-file "$file") ||
    fail "uv could not write the dependencies uv.lock pins to $file"
  [ -s "$file" ] || fail "uv.lock pins no dependency of the package: $file is empty"
  dist=$(mktemp -d)
  (cd "$root" && "$uv" build --wheel --out-dir "$dist") ||
    fail "uv could not build the wheel of dotagent-macos $version"
  name=dotagent_macos-$version-py3-none-any.whl
  wheel=$dist/$name
  [ -f "$wheel" ] || fail "uv built $(ls -1 "$dist" | tr '\n' ' ') and not $name"
  hash=$(shasum -a 256 "$wheel" | awk '{print $1}')
  [[ $hash =~ ^[0-9a-f]{64}$ ]] || fail "the wheel's SHA-256 is not 64 hex digits: $hash"
  cp "$wheel" "$wheel_dir/$name"
  printf '# dotagent-macos==%s\n./%s --hash=sha256:%s\n' "$version" "$name" "$hash" >>"$file"
  rm -rf "$dist"
  "${py[@]}" - "$file" "$name" "$hash" <<'PY' || fail "the requirements file does not pin a hash for every package"
import re
import sys

path, wheel, digest = sys.argv[1:]
blocks = []
buf = []
for raw in open(path, encoding="utf-8"):
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        continue
    buf.append(stripped)
    if not stripped.endswith("\\"):
        blocks.append(" ".join(part[:-1].strip() if part.endswith("\\") else part for part in buf))
        buf = []
if buf:
    sys.exit("a requirement is left unfinished")
wheel_line = f"./{wheel} --hash=sha256:{digest}"
if wheel_line not in [" ".join(block.split()) for block in blocks]:
    sys.exit("the wheel line is missing")
for block in blocks:
    if not re.search(r"--hash=sha256:[0-9a-f]{64}\b", block):
        sys.exit(f"no hash: {block[:120]}")
    token = block.split()[0]
    if token.startswith(("./", "/")) and not token.endswith(wheel):
        sys.exit(f"unexpected path: {block[:120]}")
PY
}

# The requirements and the wheel leave the checkout when this ends, as they do after desktop/release.sh.
trap 'rm -f "$desktop/src-tauri/resources/requirements.txt" "$desktop/src-tauri/resources"/dotagent_macos-*.whl' EXIT

echo "artifact: building an unsigned Dot $version"
"$desktop/fetch-tools.sh"
requirements_to "$desktop/src-tauri/resources/requirements.txt" "$desktop/src-tauri/resources"
(cd "$desktop" && npm ci --ignore-scripts --no-audit --no-fund)
tauri=$desktop/node_modules/.bin/tauri
[ -x "$tauri" ] || fail "the tauri command is not installed"
# Apple's credentials and the updater key are taken out of the environment, and --no-sign skips codesign,
# so a secret that happens to be set cannot start signing or notarization.
env -u APPLE_ID -u APPLE_PASSWORD -u APPLE_TEAM_ID -u APPLE_API_KEY -u APPLE_API_ISSUER \
  -u APPLE_API_KEY_PATH -u APPLE_CERTIFICATE -u APPLE_CERTIFICATE_PASSWORD -u APPLE_SIGNING_IDENTITY \
  -u TAURI_SIGNING_PRIVATE_KEY -u TAURI_SIGNING_PRIVATE_KEY_PASSWORD \
  "$tauri" build --target universal-apple-darwin --bundles dmg --no-sign

bundle=$desktop/src-tauri/target/universal-apple-darwin/release/bundle/dmg
shopt -s nullglob
dmgs=("$bundle"/*.dmg)
[ "${#dmgs[@]}" = 1 ] || fail "expected one disk image in $bundle, found ${#dmgs[@]}"
out=$desktop/dist/artifact
rm -rf "$out"
mkdir -p "$out"
cp "${dmgs[0]}" "$out/"
name=$(basename "${dmgs[0]}")
"${py[@]}" "$root/scripts/desktop_latest.py" "$out/latest.json" "$version" "$name"
echo "artifact: unsigned Dot $version is in $out"
ls -1 "$out"

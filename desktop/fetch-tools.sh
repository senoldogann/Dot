#!/bin/bash
# The tools the desktop app carries (item 575): uv, ripgrep (rg) and the launcher Dot.app is made of, each one universal
# program for Apple silicon and Intel, put where the app's build takes its resources (desktop/src-tauri/resources/tools).
# The tools are pinned in desktop/tools.lock: each archive is downloaded, read once (the same bytes are hashed and
# unpacked), and must have the pinned SHA-256; the program unpacked from it must have the pinned SHA-256 too, which
# catches an archive that was swapped after it was checked; and once the two kinds are joined, each half of the result
# must still be that very program. Anything else stops everything and nothing is made. The launcher is built from
# src/dotagent/dot_app.c, the source the Python package carries.
#
#   fetch-tools.sh                      fetch, check, make the three and put them in the folder
#   fetch-tools.sh --dry-run            fetch, unpack and check the archives and their programs; make and put nothing
#   fetch-tools.sh --sign IDENTITY      also sign each with the Developer ID IDENTITY, the hardened runtime and a secure
#                                       timestamp, as Apple's notary service asks of every program inside an app
#   fetch-tools.sh --check-bundle DIR   only check that DIR, the tools folder of a built app, holds exactly the three
#                                       programs and nothing else (release.sh asks it of the app it built)
#   fetch-tools.sh --allow-file-addresses   also take file:// addresses in the lock file, which the tests' own lock files
#                                       use for archives they made; the release's lock file takes https addresses only,
#                                       and release.sh never gives this
#
# The folder holds the three programs and its README.md, and nothing else: before it writes the three, this script
# empties it of anything but the README, so that nothing a person or a program left there goes into a release (the
# folder is first on the PATH of the service, which holds the keys of its owner's accounts). --lock FILE and --out DIR
# choose another lock file and folder (the tests use them with archives of their own). desktop/release.sh runs this
# with --sign before it builds the app, and `release.sh --check-tools` runs --dry-run.
set -euo pipefail

desktop=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$desktop")
lock=$desktop/tools.lock
out=$desktop/src-tauri/resources/tools
dry=0
files=0
identity=
check_bundle=
# What the folder of the app's tools holds, and what the folder of the sources holds besides: its note.
programs="dot-launcher rg uv"

fail() {
  echo "fetch-tools: $*" >&2
  exit 1
}

# check_set FOLDER NAMES: FOLDER holds the entries NAMES (a list separated by spaces, in order) and nothing else, nothing
# of them nested; each program in it is a file of its own (no link) that can be run.
check_set() {
  local folder=$1 names=$2 found name
  [ -d "$folder" ] && [ ! -L "$folder" ] || fail "$folder is not a folder"
  found=$(cd "$folder" && find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C sort | tr '\n' ' ')
  [ "$found" = "$names " ] || fail "$folder holds: ${found:-nothing}; it must hold exactly: $names"
  for name in $programs; do
    [ -f "$folder/$name" ] && [ ! -L "$folder/$name" ] && [ -x "$folder/$name" ] ||
      fail "$folder/$name is not a program of its own that can be run (it is a link, a folder or not executable)"
  done
}

while [ $# -gt 0 ]; do
  case $1 in
    --dry-run) dry=1 ;;
    --lock) lock=${2:?--lock needs a file}; shift ;;
    --out) out=${2:?--out needs a folder}; shift ;;
    --sign) identity=${2:?--sign needs an identity}; shift ;;
    --check-bundle) check_bundle=${2:?--check-bundle needs a folder}; shift ;;
    --allow-file-addresses) files=1 ;;
    *) fail "unknown argument: $1" ;;
  esac
  shift
done

if [ -n "$check_bundle" ]; then
  check_set "$check_bundle" "$programs"
  echo "fetch-tools: $check_bundle holds exactly $programs"
  exit 0
fi

[ -r "$lock" ] || fail "no lock file at $lock"
[ "$dry" = 0 ] || [ -z "$identity" ] || fail "--dry-run makes nothing to sign"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/archives" "$work/unpacked" "$work/made" "$work/check"

# unpack ARCHIVE MEMBER INTO: read ARCHIVE once, by tee, which gives each byte to tar (which unpacks MEMBER into INTO)
# and to shasum; prints the SHA-256 of what was read. What is hashed is what is unpacked, whatever happens to the file
# on the disk meanwhile. tee goes on to the end of the archive even if tar stops reading it early.
unpack() {
  local archive=$1 member=$2 into=$3 fifo hasher
  fifo=$work/fifo.$RANDOM
  mkfifo "$fifo"
  (shasum -a 256 <"$fifo" | cut -d' ' -f1 >"$fifo.sha") &
  hasher=$!
  { trap '' PIPE; tee "$fifo" <"$archive"; } | tar -xzf - -C "$into" "$member" || {
    wait "$hasher" || true
    return 1
  }
  wait "$hasher"
  cat "$fifo.sha"
}

# One program of each tool, thin, for each architecture of the lock file:
# tool version arch archive-sha256 address member program-sha256.
tools=
while read -r tool version arch sha address member program_sha extra; do
  case $tool in '' | '#'*) continue ;; esac
  [ -n "${program_sha:-}" ] && [ -z "${extra:-}" ] ||
    fail "$lock: a line has not seven fields (tool, version, architecture, two SHA-256s, address, program): $tool $version $arch"
  case $arch in aarch64 | x86_64) ;; *) fail "$lock: $tool has architecture $arch, not aarch64 or x86_64" ;; esac
  [[ $sha =~ ^[0-9a-f]{64}$ ]] || fail "$lock: the SHA-256 of the archive of $tool $arch is not 64 lowercase hex digits: $sha"
  [[ $program_sha =~ ^[0-9a-f]{64}$ ]] ||
    fail "$lock: the SHA-256 of the program of $tool $arch is not 64 lowercase hex digits: $program_sha"
  case $address in
    https://*) ;;
    file://*)
      [ "$files" = 1 ] ||
        fail "$lock: the address of $tool $arch is a file address, and the release takes https addresses only: $address"
      ;;
    *) fail "$lock: the address of $tool $arch is not an https address: $address" ;;
  esac
  case $member in /* | *..*) fail "$lock: the program of $tool $arch is not a path inside its archive: $member" ;; esac
  archive=$work/archives/$tool-$arch.tar.gz
  curl --fail --silent --show-error --location --retry 3 --max-time 600 --output "$archive" "$address" ||
    fail "could not download $tool $version for $arch from $address"
  into=$work/unpacked/$tool-$arch
  mkdir -p "$into"
  got=$(unpack "$archive" "$member" "$into") || fail "$member is not in the archive of $tool for $arch, or it does not unpack"
  [ "$got" = "$sha" ] ||
    fail "$tool $version for $arch from $address has SHA-256 $got, not the pinned $sha: nothing was made"
  program=$into/$member
  [ -f "$program" ] && [ ! -L "$program" ] || fail "$member in the archive of $tool for $arch is not a file of its own"
  have=$(shasum -a 256 "$program" | cut -d' ' -f1)
  [ "$have" = "$program_sha" ] ||
    fail "$member of $tool $version for $arch has SHA-256 $have, not the pinned $program_sha: nothing was made"
  echo "fetch-tools: $tool $version $arch: archive and program SHA-256 as pinned"
  case " $tools " in *" $tool "*) ;; *) tools="$tools $tool" ;; esac
  printf '%s %s %s %s\n' "$tool" "$arch" "$member" "$program_sha" >>"$work/members"
done <"$lock"
[ -n "$tools" ] || fail "$lock pins no tool"
for tool in $tools; do
  for arch in aarch64 x86_64; do
    grep -q "^$tool $arch " "$work/members" || fail "$lock pins $tool for one architecture only: it needs aarch64 and x86_64"
  done
done

if [ "$dry" = 1 ]; then
  echo "fetch-tools: dry run: the archives and their programs are the pinned ones; nothing was built or put in $out"
  exit 0
fi

lipo_arch() {
  case $1 in aarch64) echo arm64 ;; *) echo "$1" ;; esac
}

# member_of TOOL ARCH FIELD: the member (field 3) or the pinned SHA-256 of the program (field 4) of the lock line.
member_of() {
  awk -v t="$1" -v a="$2" -v f="$3" '$1 == t && $2 == a { print $f }' "$work/members"
}

for tool in $tools; do
  for arch in aarch64 x86_64; do
    program=$work/unpacked/$tool-$arch/$(member_of "$tool" "$arch" 3)
    have=$(lipo -archs "$program" 2>/dev/null) || fail "the program of $tool for $arch is not a macOS program"
    [ "$have" = "$(lipo_arch "$arch")" ] || fail "the program of $tool for $arch is built for $have"
  done
  lipo -create -output "$work/made/$tool" \
    "$work/unpacked/$tool-aarch64/$(member_of "$tool" aarch64 3)" \
    "$work/unpacked/$tool-x86_64/$(member_of "$tool" x86_64 3)"
  # Each half of the joined program is the pinned program, bit for bit: what is signed below is what was checked above.
  for arch in aarch64 x86_64; do
    lipo -thin "$(lipo_arch "$arch")" -output "$work/check/$tool-$arch" "$work/made/$tool"
    [ "$(shasum -a 256 "$work/check/$tool-$arch" | cut -d' ' -f1)" = "$(member_of "$tool" "$arch" 4)" ] ||
      fail "the $arch half of $tool is not the pinned program: nothing was put in $out"
  done
done

# The launcher, built here for both architectures from the source the package carries; the app's minimum system is
# the app's own (tauri.conf.json), so that it runs wherever the app does.
minimum=$(sed -n 's/^ *"minimumSystemVersion": "\(.*\)",$/\1/p' "$desktop/src-tauri/tauri.conf.json" | head -1)
[ -n "$minimum" ] || minimum=13.0
xcrun clang -arch arm64 -arch x86_64 -mmacosx-version-min="$minimum" -O2 -Wall -Wextra -Werror \
  -o "$work/made/dot-launcher" "$root/src/dotagent/dot_app.c" || fail "the launcher could not be built"

for name in $tools dot-launcher; do
  archs=$(lipo -archs "$work/made/$name")
  [[ " $archs " == *" arm64 "* && " $archs " == *" x86_64 "* ]] || fail "$name is built for $archs, not for both kinds of Mac"
  if [ -n "$identity" ]; then
    codesign --force --options runtime --timestamp --sign "$identity" "$work/made/$name"
    codesign --verify --strict "$work/made/$name"
  fi
done

# Empty the folder of everything but its note, and only a folder that is this one: a folder with no README.md of the
# tools (a wrong --out) is left as it is. Then the three go in.
mkdir -p "$out"
[ ! -L "$out" ] || fail "$out is a link, not a folder"
if [ -n "$(ls -A "$out")" ]; then
  grep -q '^The tools the desktop app carries' "$out/README.md" 2>/dev/null ||
    fail "$out is not the folder of the app's tools (it has no README.md of that folder): nothing was put in it"
  find "$out" -mindepth 1 -maxdepth 1 ! -name README.md -exec rm -rf {} +
fi
for name in $tools dot-launcher; do
  install -m 755 "$work/made/$name" "$out/$name"
  echo "fetch-tools: $out/$name ($(lipo -archs "$out/$name")${identity:+, signed})"
done
# What the folder holds now: the three and, when it has one, its note.
if [ -f "$out/README.md" ]; then
  check_set "$out" "README.md $programs"
else
  check_set "$out" "$programs"
fi

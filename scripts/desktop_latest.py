"""Write the updater's latest.json for an unsigned disk image.

The file names the version and the disk image. Its signatures are empty, and its addresses are the
image's file name: this is an artifact of a workflow run, not a release, so nothing here points at a
tag or a published download.

Usage: python scripts/desktop_latest.py DEST VERSION DMG_NAME
"""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

_VERSION: re.Pattern[str] = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\Z")
_DMG: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._+-]+\.dmg\Z")


def latest(version: str, dmg_name: str, pub_date: str) -> dict[str, object]:
    """The document the updater would read, with no signature and no release address."""
    if _VERSION.fullmatch(version) is None:
        raise ValueError(f"the version is not x.y.z: {version}")
    if _DMG.fullmatch(dmg_name) is None:
        raise ValueError(f"the disk image is not a file name: {dmg_name}")
    entry: dict[str, str] = {"signature": "", "url": dmg_name}
    return {
        "version": version,
        "notes": f"Unsigned artifact of Dot {version}. Not a release.",
        "pub_date": pub_date,
        "platforms": {"darwin-aarch64": entry, "darwin-x86_64": dict(entry)},
    }


def main(argv: list[str]) -> int:
    """Write ``argv``'s latest.json. Exit 1 when the version or the file name is refused."""
    if len(argv) != 4:
        print("usage: desktop_latest.py DEST VERSION DMG_NAME", file=sys.stderr)
        return 2
    destination, version, dmg_name = argv[1:]
    pub_date: str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        document: dict[str, object] = latest(version, dmg_name, pub_date)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    Path(destination).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

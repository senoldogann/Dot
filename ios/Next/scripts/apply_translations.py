"""Writes the Turkish translations (and English plural forms) of `translations_tr.py` into the String Catalog.

Usage: python3 ios/Next/scripts/apply_translations.py
Run it after `check_strings.sh` named new keys and its synced catalog was copied over Resources/Localizable.xcstrings.
It fails, naming them, when a key of the catalog has no Turkish entry, so a new string cannot slip through untranslated.
"""

import json
import sys
from pathlib import Path

from translations_tr import EN_PLURALS, TR

CATALOG: Path = Path(__file__).resolve().parent.parent / "Resources" / "Localizable.xcstrings"


def unit(value: str) -> dict[str, dict[str, str]]:
    return {"stringUnit": {"state": "translated", "value": value}}


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    strings: dict[str, dict[str, object]] = catalog["strings"]
    missing: list[str] = sorted(key for key in strings if key not in TR)
    if missing:
        print("No Turkish translation in translations_tr.py for:", *missing, sep="\n  ", file=sys.stderr)
        return 1
    for key, entry in strings.items():
        localizations: dict[str, object] = {"tr": unit(TR[key])}
        if key in EN_PLURALS:
            one, other = EN_PLURALS[key]
            localizations["en"] = {"variations": {"plural": {"one": unit(one), "other": unit(other)}}}
        entry["localizations"] = localizations
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(strings)} keys to {CATALOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

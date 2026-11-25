"""Writes the services' logos of the web (`src/dotagent/server/static/icons/plugins/*.svg`, Simple Icons and
gilbarbara/logos, CC0) into `Resources/PluginLogos.xcassets`, one template image each, so the Connect apps sheet draws
a service's mark in the theme's ink as the web does (item 946). An image is named `plugin-<file name without .svg>`;
a logo the app does not carry is drawn as a monogram tile.

Usage: python3 ios/Next/scripts/sync_plugin_logos.py
Run it after a logo is added to or removed from the web's folder. It rewrites the catalog whole.
"""

import json
import shutil
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
SOURCE: Path = HERE.parents[2] / "src" / "dotagent" / "server" / "static" / "icons" / "plugins"
CATALOG: Path = HERE.parent / "Resources" / "PluginLogos.xcassets"
INFO: dict[str, object] = {"author": "xcode", "version": 1}


def image_set(svg: Path) -> dict[str, object]:
    """The `Contents.json` of the image set of `svg`: one vector, kept as a vector, drawn as a template."""
    return {
        "images": [{"filename": svg.name, "idiom": "universal"}],
        "info": INFO,
        "properties": {"preserves-vector-representation": True, "template-rendering-intent": "template"},
    }


def main() -> None:
    logos: list[Path] = sorted(SOURCE.glob("*.svg"))
    if not logos:
        raise FileNotFoundError(f"no logo in {SOURCE}")
    if CATALOG.exists():
        shutil.rmtree(CATALOG)
    CATALOG.mkdir(parents=True)
    (CATALOG / "Contents.json").write_text(json.dumps({"info": INFO}, indent=2) + "\n", encoding="utf-8")
    for svg in logos:
        folder: Path = CATALOG / f"plugin-{svg.stem}.imageset"
        folder.mkdir()
        shutil.copyfile(svg, folder / svg.name)
        (folder / "Contents.json").write_text(json.dumps(image_set(svg), indent=2) + "\n", encoding="utf-8")
    print(f"{len(logos)} logos in {CATALOG}")


if __name__ == "__main__":
    main()

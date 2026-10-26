"""The versions of the packages Dot's catalogue starts on this Mac (item 695), for the owner who bumps them: for each
service started with ``npx`` or ``uvx``, the version the catalogue pins and the newest one its registry has, with the
ones that differ marked. It asks npm's registry and PyPI, so it needs the network and no test runs it.

A service's version is pinned in ``src/dotagent/mcp/catalogue.py`` (``@x.y.z`` after an npm package, ``==x.y.z`` after a
PyPI one): read what a release changed, try the server, then change the number there.

Usage: uv run python scripts/catalogue_versions.py
"""

import sys
from urllib.parse import quote

import httpx

from dotagent.mcp.catalogue import MCP_SERVICES, StartedPackage, package_of

_TIMEOUT_S: float = 30.0


def newest_version(client: httpx.Client, package: StartedPackage) -> str:
    """The newest release of ``package`` its registry lists; raises ``httpx.HTTPError`` or ``ValueError`` when the
    registry does not answer or answers with something else."""
    if package.registry == "npm":
        response: httpx.Response = client.get(f"https://registry.npmjs.org/{quote(package.name, safe='@')}/latest")
        response.raise_for_status()
        version: object = response.json().get("version")
    else:
        response = client.get(f"https://pypi.org/pypi/{quote(package.name, safe='')}/json")
        response.raise_for_status()
        version = response.json().get("info", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{package.registry} names no version for {package.name}: {response.text[:200]}")
    return version


def main() -> int:
    """Print one line for each package: the service, the package, the pinned version and the newest; the status is 1
    when a registry could not be asked."""
    rows: list[tuple[str, str, str, str, str]] = []
    failed: int = 0
    with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True) as client:
        for service in MCP_SERVICES:
            package: StartedPackage | None = package_of(service)
            if package is None:
                continue
            try:
                newest: str = newest_version(client, package)
            except (httpx.HTTPError, ValueError) as error:
                newest = f"? ({error})"
                failed += 1
            pinned: str = package.version or "(none)"
            note: str = (
                "not pinned"
                if not package.pinned
                else "newest not known"
                if newest.startswith("?")
                else ""
                if pinned == newest
                else "newer is out"
            )
            rows.append((service.id, f"{package.registry}:{package.name}", pinned, newest, note))
    widths: list[int] = [max(len(row[column]) for row in rows) for column in range(4)]
    print(
        "  ".join(
            label.ljust(widths[column]) for column, label in enumerate(("service", "package", "pinned", "newest"))
        )
    )
    for row in rows:
        print("  ".join(row[column].ljust(widths[column]) for column in range(4)) + (f"  {row[4]}" if row[4] else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

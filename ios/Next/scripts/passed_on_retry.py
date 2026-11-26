"""Print each test of a result bundle that failed and then passed on a retry, one "passed on retry: <test>" line each.

CI runs a failed test once more (`-retry-tests-on-failure`, .github/workflows/ios.yml); a test that passed only then is
flaky, and this names it in the job's summary so that the retry never hides it.

Usage: python3 ios/Next/scripts/passed_on_retry.py <result.xcresult>
"""

import json
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class TestNode:
    """One node of `xcresulttool get test-results tests`: a plan, bundle, suite, test case or one repetition."""

    node_type: str
    result: str
    identifier: str
    children: tuple["TestNode", ...]


def parse(raw: dict[str, object]) -> TestNode:
    """The node and its children from the tool's JSON."""
    children = raw.get("children", [])
    if not isinstance(children, list):
        raise TypeError(f"children of {raw.get('name')!r} is {type(children).__name__}, not a list")
    return TestNode(
        node_type=str(raw.get("nodeType", "")),
        result=str(raw.get("result", "")),
        identifier=str(raw.get("nodeIdentifier", raw.get("name", ""))),
        children=tuple(parse(child) for child in children),
    )


def passed_on_retry(node: TestNode) -> list[str]:
    """The test cases under the node that passed in the end while one of their repetitions failed."""
    if node.node_type == "Test Case":
        failed_once = any(c.node_type == "Repetition" and c.result == "Failed" for c in node.children)
        return [node.identifier] if node.result == "Passed" and failed_once else []
    return [name for child in node.children for name in passed_on_retry(child)]


def main(path: str) -> None:
    out = subprocess.run(
        ["xcrun", "xcresulttool", "get", "test-results", "tests", "--path", path],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    nodes = json.loads(out)["testNodes"]
    for name in [n for raw in nodes for n in passed_on_retry(parse(raw))]:
        print(f"passed on retry: {name}")


if __name__ == "__main__":
    main(sys.argv[1])

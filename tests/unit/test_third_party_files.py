"""Which reads of the workspace's folders of third-party files count as untrusted (item 494)."""

from pathlib import Path

from dotagent.policy.paths import expand_path
from dotagent.tools.third_party import holds_third_party, matches_third_party, mentions_third_party


def test_a_path_in_a_folder_of_third_party_files_is_known(tmp_path: Path) -> None:
    workspace: Path = expand_path(str(tmp_path), tmp_path)
    assert holds_third_party(workspace / "mcp-results" / "srv-tool.txt", workspace)
    assert holds_third_party(workspace / "crawl", workspace)
    assert not holds_third_party(workspace / "notes" / "run.md", workspace)
    assert not holds_third_party(workspace / "crawl.md", workspace)


def test_a_search_counts_when_a_match_comes_from_there(tmp_path: Path) -> None:
    workspace: Path = expand_path(str(tmp_path), tmp_path)
    found: list[str] = [f"{workspace}/notes/a.md:3:x", f"{workspace}/crawl/host/001.md:9:y"]
    assert matches_third_party(found, workspace, workspace)
    assert not matches_third_party([f"{workspace}/notes/a.md:3:crawl/host"], workspace, workspace)
    # One file searched alone: ripgrep writes no path before its lines.
    assert matches_third_party(["4321:row 004321"], workspace / "mcp-results" / "srv-tool.txt", workspace)
    assert not matches_third_party(["3:x"], workspace / "notes" / "a.md", workspace)


def test_a_search_counts_when_the_workspace_is_written_through_a_link(tmp_path: Path) -> None:
    real: Path = expand_path(str(tmp_path / "real"), tmp_path)
    real.mkdir()
    linked: Path = tmp_path / "linked"
    linked.symlink_to(real)
    assert matches_third_party([f"{linked}/mcp-results/srv-tool.txt:9:y"], linked, linked)


def test_a_shell_line_counts_when_it_names_such_a_path_or_runs_there(tmp_path: Path) -> None:
    workspace: Path = expand_path(str(tmp_path), tmp_path)
    kept: Path = workspace / "mcp-results"
    assert mentions_third_party(f"tail -n 50 {kept}/srv-tool.txt", workspace, workspace)
    assert mentions_third_party(f"jq . '{kept}/srv-tool.txt'", Path("/"), workspace)
    assert mentions_third_party("head -c 500 mcp-results/srv-tool.txt", workspace, workspace)
    assert mentions_third_party("cat ./crawl/host/001.md", workspace, workspace)
    assert mentions_third_party(f"cat {workspace.name}/crawl/index.md", workspace.parent, workspace)
    assert mentions_third_party("cat 001.md", kept, workspace)
    assert not mentions_third_party("uv run pytest tests/smoke/test_crawl.py", workspace, workspace)
    assert not mentions_third_party("grep web_crawl src/dotagent/mcp-results.py", workspace, workspace)
    assert not mentions_third_party("cat crawl/index.md", Path("/"), workspace)

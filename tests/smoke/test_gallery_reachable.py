"""Every plugin of the gallery (item 542) as an install takes it, from GitHub: the archive of its pinned commit is
downloaded, its skills unpacked and their hash compared with the one the gallery keeps. A plugin that fails here would
fail the owner's Install the same way. Run with ``-m internet``; nothing is installed in Dot's home.
"""

from pathlib import Path

import pytest

from dotagent.plugins.gallery import GalleryEntry, download_archive, files_hash, unpack
from dotagent.plugins.gallery_list import GALLERY

pytestmark = [pytest.mark.internet, pytest.mark.asyncio]


@pytest.mark.parametrize("entry", GALLERY, ids=[entry.id for entry in GALLERY])
async def test_the_pinned_commit_unpacks_to_the_pinned_files(entry: GalleryEntry, tmp_path: Path) -> None:
    archive: Path = await download_archive(entry, tmp_path)
    plugin: Path = tmp_path / entry.id

    unpack(entry, archive, plugin)

    assert files_hash(plugin / "skills") == entry.files

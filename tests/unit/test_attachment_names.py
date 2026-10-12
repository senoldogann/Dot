"""An upload named like a file Dot keeps next to it is kept whole under a name of its own."""

from pathlib import Path

import pytest

from dotagent.agent.attachments import StoredAttachment, load_attachment, store_attachment


@pytest.mark.parametrize("name", ["meta.json", "TEXT.TXT"])
def test_an_upload_named_like_a_file_dot_keeps_is_kept_whole(tmp_path: Path, name: str) -> None:
    data: bytes = b'{"note": "mine"}'
    stored: StoredAttachment = store_attachment(tmp_path, name, "text/plain", data)
    assert stored.name == f"_{name}"
    assert Path(stored.path).read_bytes() == data
    assert load_attachment(tmp_path, stored.id) == stored

"""What Settings › System › Open source licences reads (item 371): the licence a package's metadata names (its SPDX
expression, else the first line of its License field, else its classifiers), the page it is known by, and what a notice
of the pages covers, from where it is."""

from pathlib import Path

from dotagent.server.licences import bundled_notices, licence_of, notice_name, url_of


def test_a_packages_licence_is_its_expression_else_its_field_else_its_classifiers() -> None:
    assert licence_of("MIT OR Apache-2.0", "MIT", []) == "MIT OR Apache-2.0"
    assert licence_of(None, "BSD-3-Clause", []) == "BSD-3-Clause"
    whole: str = "Copyright (c) 2024 Someone\n\nPermission is hereby granted, free of charge, to any person"
    assert licence_of(None, whole, []) == "Copyright (c) 2024 Someone"
    classifiers: list[str] = [
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "License :: OSI Approved :: MIT License",
    ]
    assert licence_of(None, "UNKNOWN", classifiers) == "MIT License"
    assert licence_of(None, None, ["Topic :: Internet"]) == ""


def test_a_packages_page_is_its_home_else_the_url_its_labels_name_else_the_first() -> None:
    assert url_of("https://home.example", ["Source, https://src.example"]) == "https://home.example"
    assert url_of(None, ["Docs, https://docs.example", "Source, https://src.example"]) == "https://src.example"
    assert url_of("UNKNOWN", ["Docs, https://docs.example"]) == "https://docs.example"
    assert url_of(None, []) == ""


def test_a_notice_of_the_pages_is_named_for_what_follows_licence_or_for_its_folder(tmp_path: Path) -> None:
    assert notice_name(Path("fonts/LICENSE-Inter.txt")) == "Inter"
    assert notice_name(Path("js/appearance/LICENSE")) == "js/appearance"
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "LICENSE-Inter.txt").write_text("SIL Open Font License\n", encoding="utf-8")
    (tmp_path / "fonts" / "Inter.woff2").write_bytes(b"\0")
    notices = bundled_notices(tmp_path)
    assert [(notice.name, notice.path, notice.text) for notice in notices] == [
        ("Inter", "fonts/LICENSE-Inter.txt", "SIL Open Font License\n")
    ]

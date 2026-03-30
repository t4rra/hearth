from __future__ import annotations

from pathlib import Path

from hearth.core.settings import Settings, merge_overrides, sanitize_filename


def test_settings_roundtrip(tmp_path: Path) -> None:
    settings = Settings(
        opds_url="https://example.test/opds",
        auth_mode="bearer",
        auth_bearer_token="token",
        collection_sync_feeds=["https://example.test/opds/series-a"],
    )
    path = tmp_path / "settings.json"

    settings.save(path)
    loaded = Settings.load(path)

    assert loaded.opds_url == "https://example.test/opds"
    assert loaded.auth_headers() == {"Authorization": "Bearer token"}
    assert loaded.collection_sync_feeds == ["https://example.test/opds/series-a"]


def test_sanitize_filename_replaces_illegal_chars() -> None:
    assert sanitize_filename("My:Book/Title*") == "My_Book_Title_"


def test_merge_overrides_keeps_existing_values() -> None:
    base = Settings(opds_url="https://a.example")
    merged = merge_overrides(base, {"auth_mode": "none", "opds_url": None})
    assert merged.opds_url == "https://a.example"
    assert merged.auth_mode == "none"


def test_load_ignores_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        """
        {
          "opds_url": "https://example.test/opds",
          "auto_load_library_on_startup": true,
          "unknown_extra": "value"
        }
        """,
        encoding="utf-8",
    )

    loaded = Settings.load(path)
    assert loaded.opds_url == "https://example.test/opds"

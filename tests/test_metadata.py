from __future__ import annotations

from pathlib import Path

from hearth.sync.metadata import (
    SyncRecord,
    load_metadata,
    merge_device_files_into_records,
    reconcile_on_device,
    save_metadata,
    upsert_record,
)


def test_metadata_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / ".hearth_metadata.json"
    records = {
        "id-1": SyncRecord(
            id="id-1",
            title="Book One",
            desired=True,
            on_device=True,
            device_filename="Book One.epub",
        )
    }
    save_metadata(path, records)
    loaded = load_metadata(path)
    assert loaded["id-1"].title == "Book One"


def test_reconcile_marks_missing_as_not_on_device() -> None:
    records = {
        "id-1": SyncRecord(
            id="id-1",
            title="Book One",
            desired=True,
            on_device=True,
            device_filename="Book One.epub",
        )
    }
    reconciled = reconcile_on_device(records, device_files=set())
    assert reconciled["id-1"].on_device is False


def test_upsert_record_creates_or_updates() -> None:
    records = {}
    upsert_record(records, "id-2", "Book Two", True, True, "Book Two.epub")
    assert records["id-2"].desired is True


def test_upsert_record_tracks_collection_feeds() -> None:
    records: dict[str, SyncRecord] = {}
    upsert_record(
        records,
        "id-3",
        "Book Three",
        True,
        True,
        "Book Three.epub",
        collection_feeds=["https://example.test/series", "https://example.test/all"],
    )
    assert records["id-3"].collection_feeds == [
        "https://example.test/series",
        "https://example.test/all",
    ]

    upsert_record(
        records,
        "id-3",
        "Book Three",
        False,
        False,
        "Book Three.epub",
    )
    assert records["id-3"].collection_feeds == [
        "https://example.test/series",
        "https://example.test/all",
    ]


def test_merge_device_files_adds_missing_hearth_records() -> None:
    records = {}
    device_files = {
        "Hearth/Book One.epub",
        "Hearth/.hearth_metadata.json",
        "Hearth/.hearth_collection_cache.json",
    }

    merged = merge_device_files_into_records(records, device_files)

    book_records = [
        record
        for record in merged.values()
        if record.device_filename == "Hearth/Book One.epub"
    ]
    assert len(book_records) == 1
    assert book_records[0].desired is True
    assert book_records[0].on_device is True

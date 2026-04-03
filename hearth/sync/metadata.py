from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path


@dataclass(slots=True)
class SyncRecord:
    id: str
    title: str
    desired: bool
    on_device: bool
    device_filename: str
    collection_feeds: list[str] = field(default_factory=list)


def _is_device_file_artifact(device_filename: str) -> bool:
    normalized = device_filename.strip("/")
    if not normalized:
        return True

    path = Path(normalized)
    if path.name in {
        ".hearth_metadata.json",
        ".hearth_collection_cache.json",
    }:
        return True
    if normalized.endswith(".sdr"):
        return True
    return False


def load_metadata(path: Path) -> dict[str, SyncRecord]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, SyncRecord] = {}
    allowed = {item.name for item in fields(SyncRecord)}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        filtered = {k: v for k, v in value.items() if k in allowed}
        result[key] = SyncRecord(**filtered)
    return result


def save_metadata(path: Path, records: dict[str, SyncRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: asdict(value) for key, value in records.items()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def reconcile_on_device(
    records: dict[str, SyncRecord],
    device_files: set[str],
) -> dict[str, SyncRecord]:
    reconciled: dict[str, SyncRecord] = {}
    for key, record in records.items():
        reconciled[key] = SyncRecord(
            id=record.id,
            title=record.title,
            desired=record.desired,
            on_device=record.device_filename in device_files,
            device_filename=record.device_filename,
            collection_feeds=list(record.collection_feeds),
        )
    return reconciled


def merge_device_files_into_records(
    records: dict[str, SyncRecord],
    device_files: set[str],
) -> dict[str, SyncRecord]:
    reconciled = reconcile_on_device(records, device_files)
    known_device_filenames = {
        record.device_filename
        for record in reconciled.values()
        if record.device_filename
    }

    for device_file in sorted(device_files):
        normalized = device_file.strip("/")
        if _is_device_file_artifact(normalized):
            continue

        path = Path(normalized)
        parts = path.parts
        if "Hearth" not in parts:
            continue

        if normalized in known_device_filenames:
            continue

        record_id = f"device:{normalized}"
        if record_id in reconciled:
            suffix = 2
            while f"{record_id}:{suffix}" in reconciled:
                suffix += 1
            record_id = f"{record_id}:{suffix}"

        reconciled[record_id] = SyncRecord(
            id=record_id,
            title=path.stem or path.name,
            desired=True,
            on_device=True,
            device_filename=normalized,
            collection_feeds=[],
        )
        known_device_filenames.add(normalized)

    return reconciled


def upsert_record(
    records: dict[str, SyncRecord],
    book_id: str,
    title: str,
    desired: bool,
    on_device: bool,
    device_filename: str,
    collection_feeds: list[str] | None = None,
) -> dict[str, SyncRecord]:
    previous = records.get(book_id)
    records[book_id] = SyncRecord(
        id=book_id,
        title=title,
        desired=desired,
        on_device=on_device,
        device_filename=device_filename,
        collection_feeds=(
            list(collection_feeds)
            if collection_feeds is not None
            else list(previous.collection_feeds) if previous is not None else []
        ),
    )
    return records

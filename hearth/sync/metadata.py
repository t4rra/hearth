from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(slots=True)
class SyncRecord:
    id: str
    title: str
    desired: bool
    on_device: bool
    device_filename: str


def load_metadata(path: Path) -> dict[str, SyncRecord]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, SyncRecord] = {}
    for key, value in raw.items():
        result[key] = SyncRecord(**value)
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
        )
    return reconciled


def upsert_record(
    records: dict[str, SyncRecord],
    book_id: str,
    title: str,
    desired: bool,
    on_device: bool,
    device_filename: str,
) -> dict[str, SyncRecord]:
    records[book_id] = SyncRecord(
        id=book_id,
        title=title,
        desired=desired,
        on_device=on_device,
        device_filename=device_filename,
    )
    return records

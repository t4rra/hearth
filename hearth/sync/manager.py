from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from hearth.core.opds import OPDSSession
from hearth.core.settings import sanitize_filename

from .device import KindleDevice
from .metadata import (
    SyncRecord,
    load_metadata,
    reconcile_on_device,
    save_metadata,
    upsert_record,
)


@dataclass(slots=True)
class SyncItem:
    id: str
    title: str
    download_url: str
    declared_type: str


@dataclass(slots=True)
class SyncOutcome:
    synced: int = 0
    skipped: int = 0


class ConverterLike(Protocol):
    def convert_for_kindle(
        self,
        source: Path,
        destination_dir: Path,
        stem: str,
        declared_type: str = "",
    ): ...


class SyncManager:
    def __init__(
        self,
        session: OPDSSession,
        converters: ConverterLike,
        device: KindleDevice,
        workspace: Path,
    ):
        self.session = session
        self.converters = converters
        self.device = device
        self.workspace = workspace

    @property
    def metadata_path(self) -> Path:
        if self.device.transport == "mtp":
            return self.workspace / ".hearth_metadata.mtp.json"
        return self.device.documents_dir / ".hearth_metadata.json"

    def sync(
        self,
        items: list[SyncItem],
        force_resync: bool = False,
    ) -> SyncOutcome:
        self.device.ensure_layout()
        records = load_metadata(self.metadata_path)
        listed_files = [entry for entry in self.device.list_files() if not entry.is_dir]
        on_device_names: set[str] = set()
        for entry in listed_files:
            on_device_names.add(entry.path)
            on_device_names.add(entry.name)
            normalized = entry.path.strip("/")
            if normalized:
                on_device_names.add(normalized)
                if normalized.startswith("documents/"):
                    relative = normalized.removeprefix("documents/")
                    on_device_names.add(relative)
        records = reconcile_on_device(
            records,
            on_device_names,
        )
        downloads_dir = self.workspace / "downloads"
        converted_dir = self.workspace / "converted"
        outcome = SyncOutcome()

        for item in items:
            existing = records.get(item.id)
            if (
                existing
                and existing.on_device
                and existing.desired
                and not force_resync
            ):
                outcome.skipped += 1
                continue

            stem = sanitize_filename(item.title)
            source_path = downloads_dir / stem
            downloaded = self.session.download_to(
                item.download_url,
                source_path,
            )
            converted = self.converters.convert_for_kindle(
                source=downloaded,
                destination_dir=converted_dir,
                stem=stem,
                declared_type=item.declared_type,
            )
            remote_name = f"Hearth/{converted.output.name}"
            self.device.put_file(converted.output, remote_name)

            upsert_record(
                records=records,
                book_id=item.id,
                title=item.title,
                desired=True,
                on_device=True,
                device_filename=remote_name,
            )
            outcome.synced += 1

        save_metadata(self.metadata_path, records)
        return outcome

    def mark_deleted_on_device(self, record_id: str) -> bool:
        records = load_metadata(self.metadata_path)
        record = records.get(record_id)
        if not record:
            return False
        deleted = self.device.delete_file(record.device_filename)
        records[record_id] = SyncRecord(
            id=record.id,
            title=record.title,
            desired=False,
            on_device=False,
            device_filename=record.device_filename,
        )
        save_metadata(self.metadata_path, records)
        return deleted

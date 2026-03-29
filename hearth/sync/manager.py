from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
import urllib.parse

from hearth.converters.detection import COMIC_EXTENSIONS, infer_extension
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
    author: str = ""


@dataclass(slots=True)
class SyncOutcome:
    synced: int = 0
    skipped: int = 0


@dataclass(slots=True)
class SyncProgress:
    current: float
    total: int
    message: str
    is_log: bool = False


class ConverterLike(Protocol):
    def convert_for_kindle(
        self,
        source: Path,
        destination_dir: Path,
        stem: str,
        title: str = "",
        author: str = "",
        kcc_device_hint: str = "",
        progress_callback: Callable[[float | None, str], None] | None = None,
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
        progress_callback: Callable[[SyncProgress], None] | None = None,
    ) -> SyncOutcome:
        def emit(
            current: float,
            message: str,
            is_log: bool = False,
        ) -> None:
            if progress_callback is None:
                return
            progress_callback(
                SyncProgress(
                    current=current,
                    total=len(items),
                    message=message,
                    is_log=is_log,
                )
            )

        self.device.ensure_layout()
        emit(0, f"Preparing sync for {len(items)} item(s)...", is_log=True)
        records = load_metadata(self.metadata_path)
        listed_files = [entry for entry in self.device.list_files() if not entry.is_dir]
        emit(
            0,
            f"Indexed {len(listed_files)} file(s) currently on Kindle",
            is_log=True,
        )
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
        processed = 0

        for item in items:
            existing = records.get(item.id)
            if (
                existing
                and existing.on_device
                and existing.desired
                and not force_resync
            ):
                outcome.skipped += 1
                processed += 1
                emit(
                    processed,
                    f"[{processed}/{len(items)}] skipped: {item.title}",
                    is_log=True,
                )
                continue

            stem = sanitize_filename(item.title)
            source_path = downloads_dir / self._download_filename(stem, item)
            emit(
                processed,
                f"[{processed + 1}/{len(items)}] downloading: {item.title}",
                is_log=True,
            )
            downloaded = self.session.download_to(
                item.download_url,
                source_path,
            )
            detected_ext = infer_extension(
                downloaded,
                declared_type=item.declared_type,
            )
            emit(
                processed,
                (
                    f"downloaded {downloaded.name} "
                    f"({downloaded.stat().st_size} bytes)"
                ),
                is_log=True,
            )

            emit(
                processed,
                f"[{processed + 1}/{len(items)}] converting: {item.title}",
                is_log=True,
            )

            detected_kcc_profile = ""
            if detected_ext in COMIC_EXTENSIONS:
                detected_kcc_profile = self._detect_kcc_device_profile()
            if detected_kcc_profile:
                emit(
                    processed,
                    ("auto-detected KCC device profile: " f"{detected_kcc_profile}"),
                    is_log=True,
                )

            item_title = item.title
            item_index = processed + 1
            total_items = len(items)

            def on_converter_progress(
                percent: float | None,
                line: str,
                _item_index: int = item_index,
                _total_items: int = total_items,
                _item_title: str = item_title,
            ) -> None:
                if percent is not None:
                    emit(
                        processed + (percent / 100.0),
                        (
                            f"[{_item_index}/{_total_items}] converting "
                            f"{_item_title}: {percent:.0f}%"
                        ),
                    )
                emit(
                    processed,
                    f"converter: {line}",
                    is_log=True,
                )

            converted = self.converters.convert_for_kindle(
                source=downloaded,
                destination_dir=converted_dir,
                stem=stem,
                title=item.title,
                author=item.author,
                kcc_device_hint=detected_kcc_profile,
                progress_callback=on_converter_progress,
                declared_type=item.declared_type,
            )
            emit(
                processed,
                f"converted via {converted.backend}: {converted.output.name} "
                f"({converted.output.stat().st_size} bytes)",
                is_log=True,
            )
            remote_name = f"Hearth/{converted.output.name}"
            emit(
                processed,
                f"[{processed + 1}/{len(items)}] uploading: {remote_name}",
                is_log=True,
            )
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
            processed += 1
            emit(
                processed,
                f"[{processed}/{len(items)}] synced: {item.title}",
                is_log=True,
            )

        save_metadata(self.metadata_path, records)
        emit(
            len(items),
            (f"Sync complete: synced={outcome.synced} " f"skipped={outcome.skipped}"),
            is_log=True,
        )
        return outcome

    def _detect_kcc_device_profile(self) -> str:
        if self.device.transport != "mtp":
            return ""

        info = KindleDevice.mtp_backend().detected_device_info().lower()
        if not info:
            return ""

        if "scribe" in info:
            return "KS"
        if "oasis" in info:
            return "KO"
        if "paperwhite 5" in info or "signature" in info:
            return "KPW5"
        if "voyage" in info:
            return "KV"
        if "paperwhite" in info:
            return "KPW"
        if "kindle 11" in info:
            return "K11"
        if "kindle" in info:
            return "KPW"
        return ""

    def _download_filename(self, stem: str, item: SyncItem) -> str:
        parsed = urllib.parse.urlparse(item.download_url)
        source_name = Path(parsed.path).name if parsed.path else stem
        ext = infer_extension(
            Path(source_name),
            declared_type=item.declared_type,
        )
        if not ext.startswith("."):
            ext = ".bin"
        return f"{stem}{ext}"

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

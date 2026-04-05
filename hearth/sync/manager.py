from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from pathlib import Path
import tempfile
from typing import Callable, Protocol
import urllib.parse
import shutil

import json

from hearth.converters.detection import COMIC_EXTENSIONS, infer_extension
from hearth.core.opds import OPDSSession
from hearth.core.settings import sanitize_filename

from .device import KindleDevice
from .metadata import (
    SyncRecord,
    reconcile_on_device,
    upsert_record,
)


@dataclass(slots=True)
class SyncItem:
    id: str
    title: str
    download_url: str
    declared_type: str
    author: str = ""
    source_feeds: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SyncOutcome:
    synced: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(slots=True)
class SyncProgress:
    current: float
    total: int
    message: str
    is_log: bool = False


@dataclass(slots=True)
class PreparedSyncItem:
    order: int
    item: SyncItem
    stem: str
    downloaded: Path
    detected_ext: str
    detected_kcc_profile: str


@dataclass(slots=True)
class ConvertedSyncItem:
    prepared: PreparedSyncItem
    backend: str
    output: Path


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
        max_conversion_workers: int = 1,
        convert_pdfs: bool = False,
        settings_path: Path | None = None,
        selected_collections: list[str] | None = None,
    ):
        self.session = session
        self.converters = converters
        self.device = device
        self.workspace = workspace
        self.max_conversion_workers = max(1, int(max_conversion_workers))
        self.convert_pdfs = bool(convert_pdfs)
        self.settings_path = settings_path
        self.selected_collections = [
            feed.strip()
            for feed in (selected_collections or [])
            if isinstance(feed, str) and feed.strip()
        ]

    @property
    def collection_cache_path(self) -> Path:
        return self.device.documents_dir / "Hearth" / ".hearth_collection_cache.json"

    def _collection_cache_remote_name(self) -> str:
        return "Hearth/.hearth_collection_cache.json"

    def _cache_key_for_feed(self, feed_url: str) -> str:
        base_url = self.session.settings.opds_url.strip()
        parsed = urllib.parse.urlparse(feed_url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        if not base_url:
            return path

        base_parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme == base_parsed.scheme and parsed.netloc == base_parsed.netloc:
            return path
        return feed_url

    def _settings_remote_name(self) -> str:
        return "Hearth/.hearth_settings.json"

    def _load_collection_cache(self) -> dict[str, SyncRecord]:
        """Load cached records from collection cache file."""
        if self.device.transport != "mtp":
            cache_path = self.collection_cache_path
            if not cache_path.exists():
                return {}
            try:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                return self._parse_cache_records(raw)
            except (OSError, json.JSONDecodeError):
                return {}

        with tempfile.TemporaryDirectory(prefix="hearth-collection-cache-") as temp_dir:
            temp_path = Path(temp_dir) / ".hearth_collection_cache.json"
            try:
                self.device.download_file(self._collection_cache_remote_name(), temp_path)
                raw = json.loads(temp_path.read_text(encoding="utf-8"))
                return self._parse_cache_records(raw)
            except (OSError, RuntimeError, json.JSONDecodeError):
                return {}

    def _parse_cache_records(self, cache_data: object) -> dict[str, SyncRecord]:
        """Parse SyncRecords from collection cache format."""
        records: dict[str, SyncRecord] = {}
        if not isinstance(cache_data, dict):
            return records

        feed_by_book_id: dict[str, set[str]] = {}
        collections_dict = cache_data.get("collections", {})
        if isinstance(collections_dict, dict):
            for feed, raw_ids in collections_dict.items():
                if not isinstance(feed, str):
                    continue
                if isinstance(raw_ids, dict):
                    ids_list = raw_ids.get("book_ids", [])
                else:
                    ids_list = raw_ids
                if not isinstance(ids_list, list):
                    continue
                for raw_id in ids_list:
                    book_id = str(raw_id).strip()
                    if not book_id:
                        continue
                    feed_by_book_id.setdefault(book_id, set()).add(
                        self._cache_key_for_feed(feed)
                    )

        books_dict = cache_data.get("books", {})
        if isinstance(books_dict, dict):
            for book_id, book_data in books_dict.items():
                if not isinstance(book_data, dict):
                    continue
                persisted_feeds = [
                    str(f)
                    for f in book_data.get("collection_feeds", [])
                    if isinstance(f, str) and f.strip()
                ]
                records[book_id] = SyncRecord(
                    id=book_id,
                    title=book_data.get("title", ""),
                    desired=bool(book_data.get("desired", False)),
                    on_device=bool(book_data.get("on_device", False)),
                    device_filename=str(book_data.get("device_filename", "")),
                    collection_feeds=sorted(
                        set(persisted_feeds).union(feed_by_book_id.get(book_id, set()))
                    ),
                )
        return records

    def _build_cache_payload(self, records: dict[str, SyncRecord]) -> dict:
        """Build the unified collection cache payload."""
        books_payload: dict[str, dict] = {}
        collections_payload: dict[str, list[str]] = {}

        for record in records.values():
            books_payload[record.id] = {
                "title": record.title,
                "desired": record.desired,
                "on_device": record.on_device,
                "device_filename": record.device_filename,
            }
            for feed in record.collection_feeds:
                key = self._cache_key_for_feed(feed)
                collections_payload.setdefault(key, []).append(record.id)

        for feed in self.selected_collections:
            key = self._cache_key_for_feed(feed)
            collections_payload.setdefault(key, [])

        return {
            "books": books_payload,
            "collections": {
                k: sorted(set(v)) for k, v in sorted(collections_payload.items())
            },
        }

    def _save_collection_cache(self, records: dict[str, SyncRecord]) -> None:
        """Save collection cache to device."""
        payload = self._build_cache_payload(records)

        if self.device.transport != "mtp":
            try:
                self.collection_cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.collection_cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except OSError:
                pass
            return

        with tempfile.TemporaryDirectory(prefix="hearth-collection-cache-") as temp_dir:
            temp_path = Path(temp_dir) / ".hearth_collection_cache.json"
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.device.put_file(temp_path, self._collection_cache_remote_name())

    def _cleanup_staging_directories(self) -> None:
        for path in (self.workspace / "downloads", self.workspace / "converted"):
            shutil.rmtree(path, ignore_errors=True)

    def _copy_settings_to_device(self) -> bool:
        if self.settings_path is None:
            return False
        if not self.settings_path.exists() or not self.settings_path.is_file():
            return False

        self.device.put_file(self.settings_path, self._settings_remote_name())
        return True

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
        try:
            emit(0, f"Preparing sync for {len(items)} item(s)...", is_log=True)
            records = self._load_collection_cache()
            listed_files = [
                entry for entry in self.device.list_files() if not entry.is_dir
            ]
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
            outcome = SyncOutcome()
            prepared_items: list[PreparedSyncItem] = []

            for order, item in enumerate(items, start=1):
                existing = records.get(item.id)
                if (
                    existing
                    and existing.on_device
                    and existing.desired
                    and not force_resync
                ):
                    outcome.skipped += 1
                    emit(
                        order,
                        f"[{order}/{len(items)}] skipped: {item.title}",
                        is_log=True,
                    )
                    continue

                stem = sanitize_filename(item.title)
                source_path = downloads_dir / self._download_filename(stem, item)
                emit(
                    (order - 1) + 0.02,
                    f"[{order}/{len(items)}] downloading: {item.title}",
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
                    (order - 1) + 0.25,
                    (
                        f"downloaded {downloaded.name} "
                        f"({downloaded.stat().st_size} bytes)"
                    ),
                    is_log=True,
                )

                detected_kcc_profile = ""
                if detected_ext in COMIC_EXTENSIONS:
                    detected_kcc_profile = self._detect_kcc_device_profile()
                if detected_kcc_profile:
                    emit(
                        order - 1,
                        (
                            "auto-detected KCC device profile: "
                            f"{detected_kcc_profile}"
                        ),
                        is_log=True,
                    )

                prepared_items.append(
                    PreparedSyncItem(
                        order=order,
                        item=item,
                        stem=stem,
                        downloaded=downloaded,
                        detected_ext=detected_ext,
                        detected_kcc_profile=detected_kcc_profile,
                    )
                )

            if prepared_items:
                emit(
                    0,
                    (
                        "starting conversion phase with "
                        f"{self.max_conversion_workers} worker(s)"
                    ),
                    is_log=True,
                )

            converted_items: list[ConvertedSyncItem] = []
            failed_prepared: list[PreparedSyncItem] = []
            if self.max_conversion_workers == 1:
                for prepared in prepared_items:
                    try:
                        converted_items.append(
                            self._convert_item(
                                prepared=prepared,
                                emit=emit,
                                total_items=len(items),
                            )
                        )
                    except Exception as exc:
                        outcome.failed += 1
                        emit(
                            prepared.order,
                            (
                                f"[{prepared.order}/{len(items)}] failed conversion: "
                                f"{prepared.item.title} ({exc})"
                            ),
                            is_log=True,
                        )
                        failed_prepared.append(prepared)
            else:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.max_conversion_workers
                ) as pool:
                    futures = {
                        pool.submit(
                            self._convert_item,
                            prepared,
                            emit,
                            len(items),
                        ): prepared
                        for prepared in prepared_items
                    }
                    for future in concurrent.futures.as_completed(futures):
                        prepared = futures[future]
                        try:
                            converted_items.append(future.result())
                        except Exception as exc:
                            outcome.failed += 1
                            emit(
                                prepared.order,
                                (
                                    f"[{prepared.order}/{len(items)}] failed conversion: "
                                    f"{prepared.item.title} ({exc})"
                                ),
                                is_log=True,
                            )
                            failed_prepared.append(prepared)

            converted_items.sort(key=lambda value: value.prepared.order)

            for converted in converted_items:
                item = converted.prepared.item
                order = converted.prepared.order
                emit(
                    (order - 1) + 0.90,
                    f"converted via {converted.backend}: {converted.output.name} "
                    f"({converted.output.stat().st_size} bytes)",
                    is_log=True,
                )
                remote_name = f"Hearth/{converted.output.name}"
                emit(
                    (order - 1) + 0.95,
                    f"[{order}/{len(items)}] uploading: {remote_name}",
                    is_log=True,
                )
                previous = records.get(item.id)
                try:
                    self.device.put_file(converted.output, remote_name)
                except Exception as exc:
                    outcome.failed += 1
                    emit(
                        order,
                        (
                            f"[{order}/{len(items)}] failed upload: "
                            f"{item.title} ({exc})"
                        ),
                        is_log=True,
                    )
                    upsert_record(
                        records=records,
                        book_id=item.id,
                        title=item.title,
                        desired=True,
                        on_device=previous.on_device if previous is not None else False,
                        device_filename=(
                            previous.device_filename if previous is not None else ""
                        ),
                        collection_feeds=item.source_feeds,
                    )
                else:
                    upsert_record(
                        records=records,
                        book_id=item.id,
                        title=item.title,
                        desired=True,
                        on_device=True,
                        device_filename=remote_name,
                        collection_feeds=item.source_feeds,
                    )
                    outcome.synced += 1
                    emit(
                        order,
                        f"[{order}/{len(items)}] synced: {item.title}",
                        is_log=True,
                    )

                try:
                    converted.output.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    converted.prepared.downloaded.unlink(missing_ok=True)
                except OSError:
                    pass

            for prepared in failed_prepared:
                item = prepared.item
                previous = records.get(item.id)
                upsert_record(
                    records=records,
                    book_id=item.id,
                    title=item.title,
                    desired=True,
                    on_device=previous.on_device if previous is not None else False,
                    device_filename=(
                        previous.device_filename if previous is not None else ""
                    ),
                    collection_feeds=item.source_feeds,
                )
                try:
                    prepared.downloaded.unlink(missing_ok=True)
                except OSError:
                    pass

            self._save_collection_cache(records)
            try:
                copied = self._copy_settings_to_device()
            except Exception as exc:
                emit(
                    len(items),
                    f"settings copy failed: {exc}",
                    is_log=True,
                )
            else:
                if copied:
                    emit(
                        len(items),
                        "copied settings to Kindle: Hearth/.hearth_settings.json",
                        is_log=True,
                    )
            emit(
                len(items),
                (
                    f"Sync complete: synced={outcome.synced} "
                    f"skipped={outcome.skipped} failed={outcome.failed}"
                ),
                is_log=True,
            )
            return outcome
        finally:
            self._cleanup_staging_directories()

    def _convert_item(
        self,
        prepared: PreparedSyncItem,
        emit: Callable[[float, str, bool], None],
        total_items: int,
    ) -> ConvertedSyncItem:
        item = prepared.item
        order = prepared.order
        converted_dir = self.workspace / "converted"

        emit(
            (order - 1) + 0.30,
            f"[{order}/{total_items}] converting: {item.title}",
            True,
        )

        def on_converter_progress(
            percent: float | None,
            line: str,
            _order: int = order,
            _total_items: int = total_items,
            _title: str = item.title,
        ) -> None:
            if percent is not None:
                emit(
                    (_order - 1) + 0.30 + ((percent / 100.0) * 0.55),
                    f"[{_order}/{_total_items}] converting {_title}: {percent:.0f}%",
                    False,
                )
            emit(
                _order - 1,
                f"converter: {line}",
                True,
            )

        # If this item is a PDF and PDF conversion is disabled, copy directly.
        is_pdf = (prepared.detected_ext == ".pdf") or (
            "pdf" in (item.declared_type or "").lower()
        )
        if is_pdf and not self.convert_pdfs:
            converted_dir.mkdir(parents=True, exist_ok=True)
            dest = converted_dir / f"{prepared.stem}.pdf"
            shutil.copy2(prepared.downloaded, dest)
            emit(
                (order - 1) + 0.75,
                f"copied PDF without conversion: {dest.name}",
                True,
            )
            return ConvertedSyncItem(
                prepared=prepared,
                backend="identity",
                output=dest,
            )

        converted = self.converters.convert_for_kindle(
            source=prepared.downloaded,
            destination_dir=converted_dir,
            stem=prepared.stem,
            title=item.title,
            author=item.author,
            kcc_device_hint=prepared.detected_kcc_profile,
            progress_callback=on_converter_progress,
            declared_type=item.declared_type,
        )
        return ConvertedSyncItem(
            prepared=prepared,
            backend=converted.backend,
            output=converted.output,
        )

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
        records = self._load_collection_cache()
        record = records.get(record_id)
        if not record:
            return False
        deleted = self.device.delete_file(record.device_filename)
        if deleted:
            records.pop(record_id, None)
        else:
            records[record_id] = SyncRecord(
                id=record.id,
                title=record.title,
                desired=False,
                on_device=False,
                device_filename=record.device_filename,
                collection_feeds=list(record.collection_feeds),
            )
        self._save_collection_cache(records)
        return deleted

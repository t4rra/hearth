from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time

from hearth.core.opds import OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import KindleDevice
from hearth.sync.manager import SyncItem, SyncManager
from hearth.sync.metadata import load_metadata


class FakeSession(OPDSSession):
    def __init__(self, file_map: dict[str, Path]):
        super().__init__(Settings())
        self.file_map = file_map

    def download_to(self, url: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.file_map[url].read_bytes())
        return target


@dataclass(slots=True)
class FakeConversionResult:
    backend: str
    output: Path


class FakeConverters:
    def __init__(self) -> None:
        self.last_source: Path | None = None

    def convert_for_kindle(
        self,
        source: Path,
        destination_dir: Path,
        stem: str,
        title: str = "",
        author: str = "",
        kcc_device_hint: str = "",
        progress_callback=None,
        declared_type: str = "",
    ) -> FakeConversionResult:
        _ = (title, author, kcc_device_hint, progress_callback)
        self.last_source = source
        _ = declared_type
        destination_dir.mkdir(parents=True, exist_ok=True)
        output = destination_dir / f"{stem}.epub"
        output.write_bytes(source.read_bytes())
        return FakeConversionResult(backend="fake", output=output)


class SlowConverters(FakeConverters):
    def __init__(self, delay_seconds: float = 0.15) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self.max_active = 0
        self._active = 0

    def convert_for_kindle(
        self,
        source: Path,
        destination_dir: Path,
        stem: str,
        title: str = "",
        author: str = "",
        kcc_device_hint: str = "",
        progress_callback=None,
        declared_type: str = "",
    ) -> FakeConversionResult:
        _ = (title, author, kcc_device_hint, progress_callback, declared_type)
        with self._lock:
            self._active += 1
            if self._active > self.max_active:
                self.max_active = self._active
        try:
            time.sleep(self.delay_seconds)
            return super().convert_for_kindle(
                source=source,
                destination_dir=destination_dir,
                stem=stem,
                title=title,
                author=author,
                kcc_device_hint=kcc_device_hint,
                progress_callback=progress_callback,
                declared_type=declared_type,
            )
        finally:
            with self._lock:
                self._active -= 1


def test_sync_is_idempotent(tmp_path: Path, sample_epub_path: Path) -> None:
    device = KindleDevice("usb", tmp_path / "device")
    manager = SyncManager(
        session=FakeSession({"https://example.test/book.epub": sample_epub_path}),
        converters=FakeConverters(),
        device=device,
        workspace=tmp_path / "workspace",
    )
    items = [
        SyncItem(
            id="book-1",
            title="Book One",
            download_url="https://example.test/book.epub",
            declared_type="application/epub+zip",
        )
    ]

    first = manager.sync(items)
    second = manager.sync(items)

    assert first.synced == 1
    assert second.skipped == 1


def test_stale_state_is_reconciled(
    tmp_path: Path,
    sample_epub_path: Path,
) -> None:
    device = KindleDevice("usb", tmp_path / "device")
    manager = SyncManager(
        session=FakeSession({"https://example.test/book.epub": sample_epub_path}),
        converters=FakeConverters(),
        device=device,
        workspace=tmp_path / "workspace",
    )
    item = SyncItem(
        id="book-1",
        title="Book One",
        download_url="https://example.test/book.epub",
        declared_type="application/epub+zip",
    )

    manager.sync([item])
    remote_path = device.documents_dir / "Hearth" / "Book One.epub"
    assert remote_path.exists()
    remote_path.unlink()

    outcome = manager.sync([item])
    assert outcome.synced == 1


def test_download_staging_path_has_extension(
    tmp_path: Path,
    sample_epub_path: Path,
) -> None:
    converters = FakeConverters()
    manager = SyncManager(
        session=FakeSession({"https://example.test/book": sample_epub_path}),
        converters=converters,
        device=KindleDevice("usb", tmp_path / "device"),
        workspace=tmp_path / "workspace",
    )

    manager.sync(
        [
            SyncItem(
                id="book-1",
                title="Book One",
                download_url="https://example.test/book",
                declared_type="application/epub+zip",
            )
        ]
    )

    assert converters.last_source is not None
    assert converters.last_source.suffix == ".epub"


def test_pdf_files_are_transferred_directly(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF")
    device = KindleDevice("usb", tmp_path / "device")
    converters = FakeConverters()
    manager = SyncManager(
        session=FakeSession({"https://example.test/book.pdf": pdf_path}),
        converters=converters,
        device=device,
        workspace=tmp_path / "workspace",
    )

    item = SyncItem(
        id="book-pdf",
        title="Book PDF",
        download_url="https://example.test/book.pdf",
        declared_type="application/pdf",
    )

    manager.sync([item])

    # converter should not have been invoked
    assert converters.last_source is None

    remote_path = device.documents_dir / "Hearth" / "Book PDF.pdf"
    assert remote_path.exists()


def test_sync_can_convert_in_parallel(tmp_path: Path, sample_epub_path: Path) -> None:
    converters = SlowConverters()
    manager = SyncManager(
        session=FakeSession(
            {
                "https://example.test/book-1.epub": sample_epub_path,
                "https://example.test/book-2.epub": sample_epub_path,
            }
        ),
        converters=converters,
        device=KindleDevice("usb", tmp_path / "device"),
        workspace=tmp_path / "workspace",
        max_conversion_workers=2,
    )

    manager.sync(
        [
            SyncItem(
                id="book-1",
                title="Book One",
                download_url="https://example.test/book-1.epub",
                declared_type="application/epub+zip",
            ),
            SyncItem(
                id="book-2",
                title="Book Two",
                download_url="https://example.test/book-2.epub",
                declared_type="application/epub+zip",
            ),
        ]
    )

    assert converters.max_active >= 2


def test_mark_deleted_removes_record_on_success(
    tmp_path: Path,
    sample_epub_path: Path,
) -> None:
    device = KindleDevice("usb", tmp_path / "device")
    manager = SyncManager(
        session=FakeSession({"https://example.test/book.epub": sample_epub_path}),
        converters=FakeConverters(),
        device=device,
        workspace=tmp_path / "workspace",
    )
    item = SyncItem(
        id="book-1",
        title="Book One",
        download_url="https://example.test/book.epub",
        declared_type="application/epub+zip",
    )

    manager.sync([item])

    assert manager.mark_deleted_on_device("book-1") is True
    records = load_metadata(manager.metadata_path)
    assert "book-1" not in records

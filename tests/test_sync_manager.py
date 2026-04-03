from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time

from hearth.core.opds import OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import DeviceFile, KindleDevice
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


class FakeMTPDevice(KindleDevice):
    def ensure_layout(self) -> None:
        return

    def put_file(self, local_path: Path, remote_name: str) -> Path:
        remote_path = self.documents_dir / remote_name
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        remote_path.write_bytes(local_path.read_bytes())
        return remote_path

    def download_file(self, remote_name: str, destination: Path) -> Path:
        source = self.documents_dir / remote_name
        if not source.exists():
            raise RuntimeError(f"Remote MTP file not found: {remote_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return destination

    def list_files(self):
        rows = []
        if not self.documents_dir.exists():
            return rows
        for path in self.documents_dir.rglob("*"):
            rows.append(
                DeviceFile(
                    name=path.name,
                    path=path.relative_to(self.documents_dir).as_posix(),
                    size=path.stat().st_size,
                    is_dir=path.is_dir(),
                )
            )
        return rows

    def delete_file(self, remote_name: str) -> bool:
        target = self.documents_dir / remote_name
        if not target.exists():
            return False
        if target.is_dir():
            for child in sorted(target.rglob("*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
                else:
                    child.unlink()
            target.rmdir()
        else:
            target.unlink()
        return True


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


class WritesThenFailsConverters(FakeConverters):
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
        destination_dir.mkdir(parents=True, exist_ok=True)
        output = destination_dir / f"{stem}.epub"
        output.write_bytes(source.read_bytes())
        raise RuntimeError("converter failed after creating output")


class FlakyUploadDevice(KindleDevice):
    def __init__(self, *args, fail_on_remote_name: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_on_remote_name = fail_on_remote_name

    def put_file(self, local_path: Path, remote_name: str) -> Path:
        if remote_name == self.fail_on_remote_name:
            raise RuntimeError("upload failed during partial transfer")
        return super().put_file(local_path, remote_name)


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


def test_sync_cleans_up_staging_and_keeps_metadata_on_device(
    tmp_path: Path,
    sample_epub_path: Path,
) -> None:
    device = FakeMTPDevice(transport="mtp", root=tmp_path / "kindle")
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

    first = manager.sync([item])
    second = manager.sync([item])

    assert first.synced == 1
    assert second.skipped == 1

    workspace = tmp_path / "workspace"
    assert not (workspace / "downloads").exists()
    assert not (workspace / "converted").exists()
    assert not (workspace / ".hearth_metadata.mtp.json").exists()

    metadata_path = device.documents_dir / "Hearth" / ".hearth_metadata.json"
    assert metadata_path.exists()
    records = load_metadata(metadata_path)
    assert records["book-1"].on_device is True


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


def test_pdf_files_are_converted_when_enabled(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF")
    device = KindleDevice("usb", tmp_path / "device")
    converters = FakeConverters()
    manager = SyncManager(
        session=FakeSession({"https://example.test/book.pdf": pdf_path}),
        converters=converters,
        device=device,
        workspace=tmp_path / "workspace",
        convert_pdfs=True,
    )

    item = SyncItem(
        id="book-pdf",
        title="Book PDF",
        download_url="https://example.test/book.pdf",
        declared_type="application/pdf",
    )

    manager.sync([item])

    # converter should have been invoked for PDF when enabled
    assert converters.last_source is not None

    remote_path = device.documents_dir / "Hearth" / "Book PDF.epub"
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


def test_force_resync_treats_on_device_as_empty(
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

    first = manager.sync([item])
    second = manager.sync([item], force_resync=True)

    assert first.synced == 1
    assert second.synced == 1
    assert second.skipped == 0


def test_failed_conversion_output_is_not_uploaded_and_metadata_reflects_failure(
    tmp_path: Path,
    sample_epub_path: Path,
) -> None:
    device = KindleDevice("usb", tmp_path / "device")
    manager = SyncManager(
        session=FakeSession({"https://example.test/book.epub": sample_epub_path}),
        converters=WritesThenFailsConverters(),
        device=device,
        workspace=tmp_path / "workspace",
    )
    item = SyncItem(
        id="book-1",
        title="Book One",
        download_url="https://example.test/book.epub",
        declared_type="application/epub+zip",
    )

    outcome = manager.sync([item])

    assert outcome.synced == 0
    assert outcome.failed == 1

    # Even if converter emitted a file before failing, it must not be uploaded.
    remote_path = device.documents_dir / "Hearth" / "Book One.epub"
    assert not remote_path.exists()

    records = load_metadata(manager.metadata_path)
    record = records["book-1"]
    assert record.desired is True
    assert record.on_device is False


def test_partial_upload_failure_keeps_successful_records(
    tmp_path: Path,
    sample_epub_path: Path,
) -> None:
    device = FlakyUploadDevice(
        transport="usb",
        root=tmp_path / "device",
        fail_on_remote_name="Hearth/Book Two.epub",
    )
    manager = SyncManager(
        session=FakeSession(
            {
                "https://example.test/book-1.epub": sample_epub_path,
                "https://example.test/book-2.epub": sample_epub_path,
            }
        ),
        converters=FakeConverters(),
        device=device,
        workspace=tmp_path / "workspace",
    )

    outcome = manager.sync(
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

    assert outcome.synced == 1
    assert outcome.failed == 1

    records = load_metadata(manager.metadata_path)
    assert records["book-1"].on_device is True
    assert records["book-2"].desired is True
    assert records["book-2"].on_device is False

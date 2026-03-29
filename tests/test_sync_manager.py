from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hearth.core.opds import OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import KindleDevice
from hearth.sync.manager import SyncItem, SyncManager


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
    def convert_for_kindle(
        self,
        source: Path,
        destination_dir: Path,
        stem: str,
        declared_type: str = "",
    ) -> FakeConversionResult:
        _ = declared_type
        destination_dir.mkdir(parents=True, exist_ok=True)
        output = destination_dir / f"{stem}.epub"
        output.write_bytes(source.read_bytes())
        return FakeConversionResult(backend="fake", output=output)


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

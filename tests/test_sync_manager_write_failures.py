"""Unit tests for SyncManager behavior around Kindle write failures."""

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from hearth.core.config import HearthSettings
from hearth.core.opds_client import Book
from hearth.sync.manager import SyncManager


class _MockSettingsManager:
    """Simple settings manager test double for SyncManager."""

    def __init__(self, settings: HearthSettings):
        self._settings = settings

    def get_settings(self) -> HearthSettings:
        return self._settings


class TestSyncManagerWriteFailures(unittest.TestCase):
    """Verify write-path related control flow for sync operations."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="hearth_sync_manager_test_"))
        settings = HearthSettings(
            opds_url="",
            auto_convert=False,
            keep_originals=True,
            mtp_auto_install_backend=False,
        )
        self.sync = SyncManager(
            settings_manager=_MockSettingsManager(settings),
            output_dir=self.test_dir,
        )
        self.book = Book(
            title="Write Failure Test",
            author="Test Author",
            id="book-write-1",
            download_url="/download/book.epub",
            format="epub",
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sync_book_fails_when_hearth_folder_cannot_be_created(self):
        kindle = Mock()
        kindle.is_connected.return_value = True
        kindle.ensure_hearth_folder_exists.return_value = False
        self.sync.kindle = kindle

        with patch.object(self.sync, "download_book") as download_book:
            self.assertFalse(self.sync.sync_book(self.book))

        download_book.assert_not_called()

    def test_sync_book_fails_when_copy_to_kindle_fails(self):
        downloaded = self.test_dir / "downloaded.epub"
        downloaded.write_text("book", encoding="utf-8")

        kindle = Mock()
        kindle.is_connected.return_value = True
        kindle.ensure_hearth_folder_exists.return_value = True
        kindle.load_metadata.return_value = {}
        kindle.copy_to_kindle.return_value = False
        self.sync.kindle = kindle

        with patch.object(self.sync, "download_book", return_value=downloaded):
            self.assertFalse(self.sync.sync_book(self.book))

    def test_sync_book_success_attempts_metadata_update(self):
        downloaded = self.test_dir / "downloaded.epub"
        downloaded.write_text("book", encoding="utf-8")

        kindle = Mock()
        kindle.is_connected.return_value = True
        kindle.ensure_hearth_folder_exists.return_value = True
        kindle.load_metadata.return_value = {}
        kindle.copy_to_kindle.return_value = True
        self.sync.kindle = kindle

        with patch.object(self.sync, "download_book", return_value=downloaded):
            with patch.object(
                self.sync,
                "_update_sync_metadata",
            ) as update_meta:
                self.assertTrue(self.sync.sync_book(self.book))

        update_meta.assert_called_once_with(self.book, downloaded)

    def test_sync_collection_counts_partial_failures(self):
        self.sync.kindle = Mock()
        self.sync.kindle.is_connected.return_value = True
        books = [
            self.book,
            Book(
                title="Book 2",
                author="Author 2",
                id="book-write-2",
                download_url="/download/book2.epub",
            ),
            Book(
                title="Book 3",
                author="Author 3",
                id="book-write-3",
                download_url="/download/book3.epub",
            ),
        ]

        with patch.object(
            self.sync,
            "sync_book",
            side_effect=[True, False, True],
        ):
            synced_count = self.sync.sync_collection(books)

        self.assertEqual(synced_count, 2)

    def test_download_book_uses_cbz_for_zip_without_epub_container(self):
        book = Book(
            title="Comic Payload",
            author="Artist",
            id="comic-1",
            download_url="/download/comic.epub",
            format="epub",
        )
        payload_path = self.test_dir / "comic_payload.zip"
        with zipfile.ZipFile(payload_path, "w") as archive:
            archive.writestr("page_001.jpg", b"img1")
            archive.writestr("page_002.jpg", b"img2")

        self.sync.opds_client = Mock()
        self.sync.opds_client.download_content.return_value = payload_path.read_bytes()

        downloaded = self.sync.download_book(book, temp_dir=self.test_dir)
        self.assertIsNotNone(downloaded)
        assert downloaded is not None
        self.assertEqual(downloaded.suffix, ".cbz")

    def test_download_book_uses_declared_comic_format_extension(self):
        book = Book(
            title="Declared CBR",
            author="Artist",
            id="comic-2",
            download_url="/download/comic",
            format="cbr",
        )

        self.sync.opds_client = Mock()
        self.sync.opds_client.download_content.return_value = b"Rar!\x1a\x07\x00payload"

        downloaded = self.sync.download_book(book, temp_dir=self.test_dir)
        self.assertIsNotNone(downloaded)
        assert downloaded is not None
        self.assertEqual(downloaded.suffix, ".cbr")

    def test_get_startup_status_reports_converter_tooling(self):
        self.sync.kindle = Mock()
        self.sync.kindle.is_connected.return_value = True

        mock_calibre = Mock()
        mock_calibre.ebook_convert_path = "/usr/local/bin/ebook-convert"

        mock_kcc = Mock()
        mock_kcc.get_runtime_status.return_value = {
            "ready": False,
            "issues": ["KCC command not detected"],
            "command_text": "",
        }

        self.sync.converter = Mock()
        self.sync.converter.ebook_converter = mock_calibre
        self.sync.converter.comic_converter = mock_kcc

        status = self.sync.get_startup_status()

        self.assertFalse(status["opds_configured"])
        self.assertTrue(status["kindle_connected"])
        self.assertTrue(status["calibre_available"])
        self.assertFalse(status["kcc"]["ready"])
        self.assertIn("KCC command not detected", status["kcc"]["issues"])

    def test_sync_book_skips_comic_when_kcc_declined(self):
        self.sync.settings.auto_convert = True

        downloaded = self.test_dir / "downloaded.cbz"
        downloaded.write_text("comic", encoding="utf-8")

        kindle = Mock()
        kindle.is_connected.return_value = True
        kindle.ensure_hearth_folder_exists.return_value = True
        kindle.load_metadata.return_value = {}
        self.sync.kindle = kindle

        comic_converter = Mock()
        comic_converter.can_convert.return_value = True
        comic_converter.get_runtime_status.return_value = {
            "ready": False,
            "issues": ["KCC command not detected"],
            "seven_zip_available": True,
        }

        converter = Mock()
        converter.can_convert.return_value = True
        converter.detect_content_profile.return_value = {"is_comic": True}
        converter.comic_converter = comic_converter
        self.sync.converter = converter

        prompt = Mock(return_value=False)

        with patch.object(self.sync, "download_book", return_value=downloaded):
            ok = self.sync.sync_book(
                self.book,
                dependency_prompt_callback=prompt,
            )

        self.assertFalse(ok)
        prompt.assert_called_once()
        converter.convert.assert_not_called()

    def test_sync_book_comic_proceeds_after_kcc_approval(self):
        self.sync.settings.auto_convert = True

        downloaded = self.test_dir / "downloaded.cbz"
        downloaded.write_text("comic", encoding="utf-8")
        converted = self.test_dir / "converted.mobi"
        converted.write_text("mobi", encoding="utf-8")

        kindle = Mock()
        kindle.is_connected.return_value = True
        kindle.ensure_hearth_folder_exists.return_value = True
        kindle.load_metadata.return_value = {}
        kindle.copy_to_kindle.return_value = True
        self.sync.kindle = kindle

        comic_converter = Mock()
        comic_converter.can_convert.return_value = True
        comic_converter.get_runtime_status.side_effect = [
            {
                "ready": False,
                "issues": ["KCC command not detected"],
                "seven_zip_available": True,
            },
            {
                "ready": True,
                "issues": [],
                "seven_zip_available": True,
            },
        ]
        comic_converter.ensure_kcc_available.return_value = True

        converter = Mock()
        converter.can_convert.return_value = True
        converter.detect_content_profile.return_value = {"is_comic": True}
        converter.comic_converter = comic_converter
        converter.convert.return_value = Mock(success=True, output_path=converted)
        self.sync.converter = converter

        prompt = Mock(return_value=True)

        with patch.object(self.sync, "download_book", return_value=downloaded):
            with patch.object(self.sync, "_update_sync_metadata"):
                ok = self.sync.sync_book(
                    self.book,
                    dependency_prompt_callback=prompt,
                )

        self.assertTrue(ok)
        comic_converter.ensure_kcc_available.assert_called_once_with(
            allow_bootstrap=True
        )
        converter.convert.assert_called_once()


if __name__ == "__main__":
    unittest.main()

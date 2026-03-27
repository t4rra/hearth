"""Tests for sync skip and deletion features."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime

from hearth.sync.manager import SyncManager
from hearth.sync.kindle_device import KindleMetadata, KindleDevice
from hearth.core.opds_client import Book
from hearth.converters.base import ConversionFormat


class TestSyncSkip(unittest.TestCase):
    """Test skip-sync and deletion features."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_settings = Mock()
        self.mock_settings.opds_url = "http://test"
        self.mock_settings.opds_auth_type = "none"
        self.mock_settings.kindle_mount_path = "/test"
        self.mock_settings.mtp_auto_mount = False
        self.mock_settings.mtp_auto_install_backend = False
        self.mock_settings.mtp_mount_tool = "auto"
        self.mock_settings.auto_convert = True
        self.mock_settings.keep_originals = True

    def _create_sync_manager(self):
        """Create a SyncManager with mocked dependencies."""
        with patch("hearth.sync.manager.SettingsManager"):
            with patch("hearth.sync.manager.OPDSClient"):
                with patch("hearth.sync.manager.KindleDevice"):
                    with patch("hearth.sync.manager.ConverterManager"):
                        sync_mgr = SyncManager()
                        sync_mgr.settings = self.mock_settings
                        return sync_mgr

    def _create_test_book(self, book_id="test-book-1"):
        """Create a mock Book object."""
        test_book = Mock(spec=Book)
        test_book.id = book_id
        test_book.title = f"Test Book {book_id}"
        test_book.author = "Test Author"
        test_book.description = "Test Description"
        test_book.format = "EPUB"
        test_book.download_url = "http://test/book.epub"
        return test_book

    def _create_metadata(self, book_id="test-book-1"):
        """Create existing metadata for a synced book."""
        return {
            book_id: KindleMetadata(
                title=f"Test Book {book_id}",
                author="Test Author",
                opds_id=book_id,
                original_format="epub",
                kindle_format="mobi",
                sync_date=datetime.now().isoformat(),
                local_path=f"/kindle/{book_id}.mobi",
                desired_sync=True,
                on_device=True,
                sync_status="on_device",
                marked_for_deletion=False,
            )
        }

    def test_sync_book_skips_already_synced(self):
        """Test that sync_book skips already-synced books."""
        sync_mgr = self._create_sync_manager()
        test_book = self._create_test_book()
        existing_metadata = self._create_metadata()

        sync_mgr.kindle = Mock()
        sync_mgr.kindle.ensure_hearth_folder_exists.return_value = True
        sync_mgr.kindle.load_metadata.return_value = existing_metadata
        sync_mgr.is_kindle_connected = Mock(return_value=True)

        # Should return True (skipped) without downloading
        result = sync_mgr.sync_book(test_book, force_resync=False)

        self.assertTrue(result)
        sync_mgr.kindle.copy_to_kindle.assert_not_called()

    def test_sync_book_force_resync(self):
        """Test that force_resync bypasses skip."""
        sync_mgr = self._create_sync_manager()
        test_book = self._create_test_book()
        existing_metadata = self._create_metadata()

        sync_mgr.kindle = Mock()
        sync_mgr.kindle.ensure_hearth_folder_exists.return_value = True
        sync_mgr.kindle.load_metadata.return_value = existing_metadata
        sync_mgr.is_kindle_connected = Mock(return_value=True)
        sync_mgr.kindle.copy_to_kindle.return_value = True

        with patch.object(
            sync_mgr, "download_book", return_value=Path("/test/book.mobi")
        ):
            with patch.object(sync_mgr.converter, "can_convert", return_value=False):
                with patch.object(sync_mgr, "_update_sync_metadata"):
                    result = sync_mgr.sync_book(test_book, force_resync=True)

        self.assertTrue(result)
        sync_mgr.kindle.copy_to_kindle.assert_called()

    def test_sync_book_new_book_not_skipped(self):
        """Test that new books are not skipped."""
        sync_mgr = self._create_sync_manager()
        test_book = self._create_test_book("new-book-1")

        sync_mgr.kindle = Mock()
        sync_mgr.kindle.ensure_hearth_folder_exists.return_value = True
        sync_mgr.kindle.load_metadata.return_value = {}  # No metadata yet
        sync_mgr.is_kindle_connected = Mock(return_value=True)
        sync_mgr.kindle.copy_to_kindle.return_value = True

        with patch.object(
            sync_mgr, "download_book", return_value=Path("/test/book.mobi")
        ):
            with patch.object(sync_mgr.converter, "can_convert", return_value=False):
                with patch.object(sync_mgr, "_update_sync_metadata"):
                    result = sync_mgr.sync_book(test_book, force_resync=False)

        self.assertTrue(result)
        # Should have called copy_to_kindle for new book
        sync_mgr.kindle.copy_to_kindle.assert_called()

    def test_mark_book_for_deletion(self):
        """Test marking a book for deletion."""
        sync_mgr = self._create_sync_manager()
        existing_metadata = self._create_metadata()

        sync_mgr.kindle = Mock()
        sync_mgr.kindle.load_metadata.return_value = existing_metadata
        sync_mgr.kindle.save_metadata = Mock()

        result = sync_mgr.mark_book_for_deletion("test-book-1", "Test Book 1")

        self.assertTrue(result)
        self.assertTrue(sync_mgr.kindle.save_metadata.called)

        # Verify the saved metadata has deletion flag set
        saved_data = sync_mgr.kindle.save_metadata.call_args[0][0]
        self.assertTrue(saved_data["test-book-1"].marked_for_deletion)
        self.assertFalse(saved_data["test-book-1"].desired_sync)

    def test_delete_marked_books(self):
        """Test deleting marked books."""
        sync_mgr = self._create_sync_manager()
        metadata = self._create_metadata("test-book-1")
        metadata["test-book-1"].marked_for_deletion = True

        sync_mgr.kindle = Mock()
        sync_mgr.kindle.load_metadata.return_value = metadata
        sync_mgr.kindle.save_metadata = Mock()
        sync_mgr.kindle.delete_file_from_kindle.return_value = True

        deleted_count = sync_mgr.delete_marked_books()

        self.assertEqual(deleted_count, 1)
        sync_mgr.kindle.delete_file_from_kindle.assert_called()
        sync_mgr.kindle.save_metadata.assert_called()

    def test_force_resync_book(self):
        """Test force_resync_book convenience method."""
        sync_mgr = self._create_sync_manager()
        test_book = self._create_test_book()

        with patch.object(sync_mgr, "sync_book", return_value=True) as mock_sync:
            result = sync_mgr.force_resync_book(test_book)

            self.assertTrue(result)
            mock_sync.assert_called_once_with(test_book, force_resync=True)

    def test_kindlemetadata_marked_for_deletion_field(self):
        """Test that KindleMetadata includes marked_for_deletion field."""
        metadata = KindleMetadata(
            title="Test",
            author="Author",
            opds_id="test",
            original_format="epub",
            kindle_format="mobi",
            sync_date="2024-01-01",
            marked_for_deletion=False,
        )

        self.assertFalse(metadata.marked_for_deletion)
        metadata.marked_for_deletion = True
        self.assertTrue(metadata.marked_for_deletion)


if __name__ == "__main__":
    unittest.main()

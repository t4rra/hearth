"""Unit tests for KindleDevice write and metadata paths."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hearth.sync.kindle_device import KindleDevice, KindleMetadata, _LibMTPBackend


class TestKindleWritePaths(unittest.TestCase):
    """Verify MTP and USB write behavior without real hardware."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="hearth_kindle_write_test_"))
        self.source_file = self.test_dir / "book.mobi"
        self.source_file.write_bytes(b"test-kindle-content")
        self.device = KindleDevice(
            auto_mount_mtp=False,
            auto_install_mtp_backend=False,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_copy_to_kindle_rejects_missing_source_file(self):
        missing = self.test_dir / "missing.mobi"
        self.assertFalse(self.device.copy_to_kindle(missing))

    def test_copy_to_kindle_mtp_fails_without_backend(self):
        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=True,
        ):
            with patch.object(
                self.device,
                "_get_mtp_backend",
                return_value=None,
            ):
                self.assertFalse(self.device.copy_to_kindle(self.source_file))

    def test_copy_to_kindle_mtp_uses_second_candidate(self):
        backend = Mock()
        backend.send_file.side_effect = [False, True]

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=True,
        ):
            with patch.object(
                self.device,
                "_get_mtp_backend",
                return_value=backend,
            ):
                with patch.object(
                    self.device,
                    "ensure_hearth_folder_exists",
                    return_value=True,
                ):
                    copied = self.device.copy_to_kindle(self.source_file)
                    self.assertTrue(copied)

        self.assertEqual(backend.send_file.call_count, 2)

    def test_copy_to_kindle_mtp_fails_when_all_candidates_fail(self):
        backend = Mock()
        backend.send_file.return_value = False

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=True,
        ):
            with patch.object(
                self.device,
                "_get_mtp_backend",
                return_value=backend,
            ):
                with patch.object(
                    self.device,
                    "ensure_hearth_folder_exists",
                    return_value=True,
                ):
                    copied = self.device.copy_to_kindle(self.source_file)
                    self.assertFalse(copied)

    def test_copy_to_kindle_usb_copies_file(self):
        hearth_dir = self.test_dir / "kindle" / "documents" / "Hearth"

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=False,
        ):
            with patch.object(
                self.device,
                "get_hearth_dir",
                return_value=hearth_dir,
            ):
                self.assertTrue(self.device.copy_to_kindle(self.source_file))

        copied = hearth_dir / self.source_file.name
        self.assertTrue(copied.exists())
        self.assertEqual(copied.read_bytes(), self.source_file.read_bytes())

    def test_copy_to_kindle_usb_returns_false_on_copy_error(self):
        hearth_dir = self.test_dir / "kindle" / "documents" / "Hearth"

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=False,
        ):
            with patch.object(
                self.device,
                "get_hearth_dir",
                return_value=hearth_dir,
            ):
                with patch(
                    "hearth.sync.kindle_device.shutil.copy2",
                    side_effect=OSError,
                ):
                    copied = self.device.copy_to_kindle(self.source_file)
                    self.assertFalse(copied)

    def test_ensure_hearth_folder_exists_mtp_tries_candidates(self):
        backend = Mock()
        backend.ensure_folder_path.side_effect = [None, object()]

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=True,
        ):
            with patch.object(
                self.device,
                "_get_mtp_backend",
                return_value=backend,
            ):
                self.assertTrue(self.device.ensure_hearth_folder_exists())

        self.assertEqual(backend.ensure_folder_path.call_count, 2)

    def test_ensure_hearth_folder_exists_usb_creates_directory(self):
        hearth_dir = self.test_dir / "kindle" / "documents" / "Hearth"

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=False,
        ):
            with patch.object(
                self.device,
                "get_hearth_dir",
                return_value=hearth_dir,
            ):
                self.assertTrue(self.device.ensure_hearth_folder_exists())

        self.assertTrue(hearth_dir.exists())

    def test_save_metadata_mtp_writes_and_sends_json(self):
        metadata = {
            "book-1": KindleMetadata(
                title="Book 1",
                author="Author 1",
                opds_id="book-1",
                original_format="epub",
                kindle_format="mobi",
                sync_date="2026-03-27T12:00:00",
            )
        }
        backend = Mock()
        captured_payload = {}

        def capture_send(local_file: Path, remote_dir: str) -> bool:
            del remote_dir
            with open(local_file, "r", encoding="utf-8") as handle:
                captured_payload.update(json.load(handle))
            return True

        backend.send_file.side_effect = capture_send

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=True,
        ):
            with patch.object(
                self.device,
                "_get_mtp_backend",
                return_value=backend,
            ):
                with patch.object(
                    self.device,
                    "ensure_hearth_folder_exists",
                    return_value=True,
                ):
                    self.assertTrue(self.device.save_metadata(metadata))

        self.assertEqual(captured_payload["book-1"]["title"], "Book 1")

    def test_save_metadata_mtp_returns_false_when_tmp_write_fails(self):
        metadata = {
            "book-1": KindleMetadata(
                title="Book 1",
                author="Author 1",
                opds_id="book-1",
                original_format="epub",
                kindle_format="mobi",
                sync_date="2026-03-27T12:00:00",
            )
        }

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=True,
        ):
            with patch.object(
                self.device,
                "_get_mtp_backend",
                return_value=Mock(),
            ):
                with patch.object(
                    self.device,
                    "ensure_hearth_folder_exists",
                    return_value=True,
                ):
                    with patch(
                        "hearth.sync.kindle_device.open",
                        side_effect=OSError,
                    ):
                        self.assertFalse(self.device.save_metadata(metadata))

    def test_save_metadata_usb_writes_metadata_file(self):
        hearth_dir = self.test_dir / "kindle" / "documents" / "Hearth"
        metadata = {
            "book-2": KindleMetadata(
                title="Book 2",
                author="Author 2",
                opds_id="book-2",
                original_format="epub",
                kindle_format="mobi",
                sync_date="2026-03-27T12:05:00",
                desired_sync=True,
                on_device=False,
                sync_status="not_synced",
            )
        }

        with patch.object(
            self.device,
            "_detect_mtp_kindle",
            return_value=False,
        ):
            with patch.object(
                self.device,
                "get_hearth_dir",
                return_value=hearth_dir,
            ):
                self.assertTrue(self.device.save_metadata(metadata))

        metadata_path = hearth_dir / self.device.KINDLE_METADATA_FILE
        self.assertTrue(metadata_path.exists())
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["book-2"]["sync_status"], "not_synced")


if __name__ == "__main__":
    unittest.main()


class _FakeMTPFiletypeLib:
    """Minimal fake libmtp API for filetype description tests."""

    def __init__(self, descriptions):
        self._descriptions = descriptions

    def LIBMTP_Get_Filetype_Description(self, filetype: int):
        return self._descriptions.get(filetype, b"")


class TestLibMTPUploadFiletypes(unittest.TestCase):
    """Verify upload type selection never prefers folder descriptors."""

    def _make_backend_with_descriptions(self, descriptions):
        backend = _LibMTPBackend.__new__(_LibMTPBackend)
        backend._debug = lambda msg: None
        backend._lib = _FakeMTPFiletypeLib(descriptions)
        backend._device_ptr = None
        backend._opened_at = 0.0
        backend._clear_errorstack_fn = None
        backend._filetype_candidates = None
        return backend

    def test_pick_upload_filetype_avoids_folder_descriptor(self):
        backend = self._make_backend_with_descriptions(
            {
                0: b"Folder",
                1: b"Unknown file type",
                2: b"PDF",
            }
        )

        selected = backend._pick_upload_filetype(Path("book.mobi"))
        self.assertEqual(selected, 1)

    def test_pick_upload_filetype_prefers_pdf_for_pdf_extension(self):
        backend = self._make_backend_with_descriptions(
            {
                0: b"Folder",
                1: b"Unknown file type",
                2: b"PDF",
            }
        )

        selected = backend._pick_upload_filetype(Path("doc.pdf"))
        self.assertEqual(selected, 2)

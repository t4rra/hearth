"""Tests for OPDS authentication and libmtp Kindle detection logic."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hearth.core.opds_client import OPDSClient
from hearth.sync.kindle_device import KindleDevice


class TestOPDSAuthentication(unittest.TestCase):
    """Verify OPDS client auth/session behavior."""

    def test_basic_auth_sets_session_credentials(self):
        client = OPDSClient(
            "https://example.test/opds",
            auth_type="basic",
            username="user1",
            password="pw1",
        )
        self.assertEqual(client.session.auth, ("user1", "pw1"))

    def test_bearer_auth_sets_authorization_header(self):
        client = OPDSClient(
            "https://example.test/opds",
            auth_type="bearer",
            token="abc123",
        )
        self.assertEqual(
            client.session.headers.get("Authorization"),
            "Bearer abc123",
        )

    def test_get_feed_builds_relative_url(self):
        client = OPDSClient("https://example.test/opds")
        mock_response = Mock()
        mock_response.content = b"<feed></feed>"
        mock_response.raise_for_status = Mock()

        with patch.object(
            client.session,
            "get",
            return_value=mock_response,
        ) as get:
            client.get_feed("catalog")

        called_url = get.call_args.args[0]
        self.assertEqual(called_url, "https://example.test/opds/catalog")

    def test_download_content_uses_authenticated_session(self):
        client = OPDSClient(
            "https://example.test/opds",
            auth_type="basic",
            username="user1",
            password="pw1",
        )
        mock_response = Mock()
        mock_response.content = b"ebook-data"
        mock_response.raise_for_status = Mock()

        with patch.object(
            client.session,
            "get",
            return_value=mock_response,
        ) as get:
            result = client.download_content("/download/book.epub")

        self.assertEqual(result, b"ebook-data")
        get.assert_called_once()


class TestKindleMTPDetection(unittest.TestCase):
    """Verify libmtp-first detection and command flow."""

    def test_is_connected_prefers_mtp_first(self):
        device = KindleDevice()

        with patch.object(device, "_detect_mtp_kindle", return_value=True):
            with patch.object(device, "_detect_usb_kindle") as usb:
                self.assertTrue(device.is_connected())
                usb.assert_not_called()

    def test_get_mount_path_returns_none_when_mtp_connected(self):
        device = KindleDevice()
        with patch.object(device, "_detect_mtp_kindle", return_value=True):
            self.assertIsNone(device.get_mount_path())

    def test_mtp_detection_sets_transport(self):
        device = KindleDevice()
        backend = Mock()
        backend.ensure_connected.return_value = True

        with patch.object(
            device,
            "_ensure_mtp_tools_available",
            return_value=True,
        ):
            with patch.object(device, "_get_mtp_backend", return_value=backend):
                self.assertTrue(device.is_connected())

        self.assertEqual(device.get_transport(), "mtp-libmtp")

    def test_ensure_hearth_folder_uses_newfolder(self):
        device = KindleDevice()
        backend = Mock()
        backend.ensure_folder_path.return_value = object()

        with patch.object(device, "_detect_mtp_kindle", return_value=True):
            with patch.object(device, "_get_mtp_backend", return_value=backend):
                self.assertTrue(device.ensure_hearth_folder_exists())

        backend.ensure_folder_path.assert_called()

    def test_copy_to_kindle_uses_sendfile(self):
        device = KindleDevice()
        backend = Mock()
        backend.send_file.return_value = True

        with tempfile.TemporaryDirectory(prefix="hearth_mtp_test_") as tmp:
            file_path = Path(tmp) / "book.mobi"
            file_path.write_bytes(b"abc")

            with patch.object(device, "_detect_mtp_kindle", return_value=True):
                with patch.object(
                    device,
                    "ensure_hearth_folder_exists",
                    return_value=True,
                ):
                    with patch.object(
                        device,
                        "_get_mtp_backend",
                        return_value=backend,
                    ):
                        self.assertTrue(device.copy_to_kindle(file_path))

            backend.send_file.assert_called()

    def test_mtp_signature_without_backend_session_returns_false(self):
        device = KindleDevice()
        backend = Mock()
        backend.ensure_connected.return_value = False

        with patch.object(
            device,
            "_ensure_mtp_tools_available",
            return_value=True,
        ):
            with patch.object(device, "_get_mtp_backend", return_value=backend):
                with patch.object(
                    device,
                    "_read_usb_snapshot",
                    return_value="Amazon Kindle VID=1949",
                ):
                    self.assertFalse(device._detect_mtp_kindle())

        self.assertEqual(device.get_transport(), "none")

    def test_is_connected_falls_back_to_usb_when_mtp_session_unavailable(self):
        device = KindleDevice()
        backend = Mock()
        backend.ensure_connected.return_value = False

        with patch.object(
            device,
            "_ensure_mtp_tools_available",
            return_value=True,
        ):
            with patch.object(device, "_get_mtp_backend", return_value=backend):
                with patch.object(
                    device,
                    "_read_usb_snapshot",
                    return_value="Amazon Kindle VID=1949",
                ):
                    with patch.object(
                        device,
                        "_detect_usb_kindle",
                        return_value=Path("/Volumes/Kindle"),
                    ):
                        self.assertTrue(device.is_connected())
                        self.assertEqual(device.get_transport(), "usb")


if __name__ == "__main__":
    unittest.main()

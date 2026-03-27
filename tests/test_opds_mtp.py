"""Tests for OPDS authentication and MTP detection logic."""

import unittest
from unittest.mock import Mock, patch
import tempfile
from pathlib import Path

from hearth.core.opds_client import OPDSClient
from hearth.sync.kindle_device import KindleDevice


class KindleDeviceForTest(KindleDevice):
    """Test helper exposing a safe way to switch transport mode."""

    def activate_mtp_api(self) -> bool:
        self._transport = "mtp-api"
        return True


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
    """Verify MTP detection behavior on macOS."""

    def test_is_connected_reports_mtp_on_macos(self):
        device = KindleDeviceForTest()

        with patch(
            "hearth.sync.kindle_device.platform.system",
            return_value="Darwin",
        ):
            with patch.object(device, "_detect_usb_kindle", return_value=None):
                with patch.object(
                    device,
                    "_read_command_output",
                    side_effect=["Some USB\nAmazon Kindle\n", ""],
                ):
                    with patch.object(
                        device,
                        "_ensure_mtp_api_ready",
                        side_effect=device.activate_mtp_api,
                    ):
                        self.assertTrue(device.is_connected())
                        self.assertEqual(device.get_transport(), "mtp-api")

    def test_get_mount_path_returns_none_for_mtp_api(self):
        device = KindleDeviceForTest()

        with patch.object(device, "_detect_usb_kindle", return_value=None):
            with patch.object(device, "_detect_mtp_device", return_value=True):
                with patch.object(
                    device,
                    "_ensure_mtp_api_ready",
                    side_effect=device.activate_mtp_api,
                ):
                    self.assertIsNone(device.get_mount_path())
                    self.assertEqual(device.get_transport(), "mtp-api")

    def test_preferred_tool_kept_in_constructor(self):
        device = KindleDevice(preferred_mtp_tool="go-mtpx")
        self.assertEqual(device.preferred_mtp_tool, "go-mtpx")

    def test_ensure_hearth_folder_uses_api_mkdir(self):
        device = KindleDeviceForTest()
        device.activate_mtp_api()

        with patch.object(device, "get_mount_path", return_value=None):
            with patch.object(
                device,
                "_mtp_api_call",
                return_value="",
            ) as call:
                self.assertTrue(device.ensure_hearth_folder_exists())

        call.assert_called_once_with(["mkdir", "/documents/Hearth"])

    def test_copy_to_kindle_uses_api_upload(self):
        device = KindleDeviceForTest()
        device.activate_mtp_api()

        with tempfile.TemporaryDirectory(prefix="hearth_mtp_test_") as tmp:
            file_path = Path(tmp) / "book.mobi"
            file_path.write_bytes(b"abc")

            with patch.object(device, "get_mount_path", return_value=None):
                with patch.object(
                    device,
                    "ensure_hearth_folder_exists",
                    return_value=True,
                ):
                    with patch.object(
                        device,
                        "_mtp_api_call",
                        return_value="",
                    ) as call:
                        self.assertTrue(device.copy_to_kindle(file_path))

            self.assertEqual(call.call_args.args[0][0], "upload")


if __name__ == "__main__":
    unittest.main()

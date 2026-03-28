"""Tests for SettingsPage Kindle device behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from hearth.gui.settings_page import SettingsPage


class _FakeSettingsManager:
    """Minimal settings manager stub for SettingsPage tests."""

    def __init__(self):
        self._settings = SimpleNamespace(
            opds_url="",
            opds_auth_type="none",
            opds_username="",
            opds_password="",
            opds_token="",
            kindle_mount_path="",
            mtp_auto_mount=True,
            mtp_auto_install_backend=False,
            mtp_mount_tool="auto",
            auto_convert=True,
            keep_originals=True,
            conversion_settings=SimpleNamespace(comic_quality="high"),
        )

    def get_settings(self):
        return self._settings

    def update_settings(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self._settings, key, value)

    def save_settings(self):
        return None

    def reset_settings(self):
        return None


class TestSettingsPageKindleDevice(unittest.TestCase):
    """Verify SettingsPage uses shared Kindle device when provided."""

    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_detect_kindle_path_uses_injected_device(self):
        device = Mock()
        device.is_connected.return_value = True
        device.get_transport.return_value = "mtp-libmtp"

        with patch("hearth.gui.settings_page.SettingsManager", _FakeSettingsManager):
            page = SettingsPage(kindle_device=device)

        with patch(
            "hearth.gui.settings_page.KindleDevice",
            side_effect=AssertionError("Should not construct a new KindleDevice"),
        ):
            with patch.object(QMessageBox, "information") as mock_info:
                page.detect_kindle_path()

        device.is_connected.assert_called_once()
        device.get_transport.assert_called_once()
        self.assertEqual(page.kindle_path.text(), "")
        mock_info.assert_called_once()


if __name__ == "__main__":
    unittest.main()

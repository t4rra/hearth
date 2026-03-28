"""Tests for settings configuration management."""

import tempfile
import unittest
from pathlib import Path

from hearth.core.config import SettingsManager


class TestSettingsManager(unittest.TestCase):
    """Validate settings persistence and reset behavior."""

    def test_reset_settings_restores_defaults(self):
        with tempfile.TemporaryDirectory(prefix="hearth_config_test_") as tmp:
            manager = SettingsManager(config_dir=Path(tmp))
            manager.update_settings(opds_url="https://example.com", auto_convert=False)

            manager.reset_settings()

            settings = manager.get_settings()
            self.assertEqual(settings.opds_url, "")
            self.assertTrue(settings.auto_convert)
            self.assertEqual(settings.mtp_mount_tool, "auto")
            self.assertTrue(manager.config_file.exists())


if __name__ == "__main__":
    unittest.main()

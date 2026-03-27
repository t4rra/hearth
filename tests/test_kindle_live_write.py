"""Opt-in live integration test for writing to Kindle Hearth folder.

This test is skipped unless HEARTH_RUN_LIVE_KINDLE_WRITE_TEST=1.
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from hearth.sync.kindle_device import KindleDevice


class TestKindleLiveWrite(unittest.TestCase):
    """Validate an actual write to Kindle Hearth when explicitly enabled."""

    def setUp(self):
        if os.environ.get("HEARTH_RUN_LIVE_KINDLE_WRITE_TEST") != "1":
            self.skipTest(
                "Set HEARTH_RUN_LIVE_KINDLE_WRITE_TEST=1 "
                "to run live Kindle write test"
            )

        self.test_dir = Path(tempfile.mkdtemp(prefix="hearth_live_kindle_test_"))
        self.device = KindleDevice(auto_install_mtp_backend=False)
        self.sentinel_name = f"hearth-write-test-{int(time.time())}.mobi"
        self.sentinel_local = self.test_dir / self.sentinel_name
        self.sentinel_local.write_bytes(b"Hearth live write test file")
        self.hold_open_sec = int(
            os.environ.get("HEARTH_LIVE_KINDLE_HOLD_OPEN_SEC", "0")
        )

    def tearDown(self):
        if hasattr(self, "test_dir"):
            shutil.rmtree(self.test_dir, ignore_errors=True)

        if hasattr(self, "device"):
            try:
                transport = self.device.get_transport()
                if transport == "usb":
                    hearth_dir = self.device.get_hearth_dir()
                    if hearth_dir:
                        name = getattr(self, "sentinel_name", "")
                        target = hearth_dir / name
                        if target.exists():
                            target.unlink()
            except OSError:
                pass

    def test_can_write_sentinel_file_to_hearth(self):
        if not self.device.is_connected():
            self.skipTest("No connected Kindle detected")

        if self.device.get_transport() == "mtp-libmtp":
            backend = self.device._get_mtp_backend()
            if not backend or not backend.ensure_connected():
                self.skipTest(
                    "MTP interface detected but unavailable "
                    "(possibly claimed by another process)"
                )

        self.assertTrue(
            self.device.ensure_hearth_folder_exists(),
            "Could not create/find Hearth folder on Kindle",
        )

        copied = self.device.copy_to_kindle(self.sentinel_local)
        self.assertTrue(
            copied,
            "Failed to copy sentinel file to Kindle Hearth",
        )

        self.assertTrue(
            self._sentinel_present_on_device(),
            "Sentinel file was not visible in Kindle Hearth after write",
        )

        if self.hold_open_sec < 0:
            input("Live test holding MTP session. Press Enter to finish... ")
            return

        if self.hold_open_sec > 0:
            # Keep process alive so device/session state can be inspected.
            time.sleep(self.hold_open_sec)

    def _sentinel_present_on_device(self) -> bool:
        transport = self.device.get_transport()

        if transport == "usb":
            hearth_dir = self.device.get_hearth_dir()
            if not hearth_dir:
                return False
            return (hearth_dir / self.sentinel_name).exists()

        books = self.device.list_books()
        if self.sentinel_name in books:
            return True

        entries = self.device.list_file_tree()
        for entry in entries:
            if str(entry.get("name", "")) == self.sentinel_name:
                return True

        return False


if __name__ == "__main__":
    unittest.main()

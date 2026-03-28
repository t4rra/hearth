"""Test KCC command discovery prioritization."""

import os
import shutil
import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from hearth.converters.kcc import KCCConverter


class TestKCCCommandDiscovery(unittest.TestCase):
    """Test KCC command discovery and validation."""

    def test_kcc_prefers_cli_binary_over_module(self):
        """Test that CLI binaries are preferred over Python module."""
        converter = KCCConverter()

        with patch("shutil.which") as mock_which:
            with patch.object(
                converter, "_is_kcc_command", return_value=True
            ) as mock_validation:
                # Simulate finding the kcc-c2e binary
                def which_side_effect(name):
                    if name == "kcc-c2e":
                        return "/usr/local/bin/kcc-c2e"
                    return None

                mock_which.side_effect = which_side_effect

                result = converter._find_kcc_command()

                # Should find kcc-c2e binary
                self.assertIsNotNone(result)
                self.assertEqual(result, ["/usr/local/bin/kcc-c2e"])
                # Should have called which for binaries
                self.assertIn(
                    (("kcc-c2e",), {}),
                    [call for call in mock_which.call_args_list],
                )

    def test_kcc_falls_back_to_module_if_no_binary(self):
        """Test that Python module is used if no CLI binary found."""
        converter = KCCConverter()

        with patch("shutil.which", return_value=None):
            with patch.object(converter, "_ensure_repo_script", return_value=None):
                with patch.object(converter, "_is_kcc_command", return_value=True):
                    result = converter._find_kcc_command(allow_bootstrap=True)

                # Should fall back to Python module
                self.assertIsNotNone(result)
                self.assertIn("python", result[0])
                self.assertIn("-m", result)

    def test_kcc_prefers_repo_script_over_module(self):
        """Test that cloned kcc-c2e.py script is preferred over module fallback."""
        converter = KCCConverter()
        repo_script = Path("/tmp/kcc/kcc-c2e.py")

        with patch("shutil.which", return_value=None):
            with patch.object(
                converter, "_ensure_repo_script", return_value=repo_script
            ):
                with patch.object(converter, "_is_kcc_command", return_value=True):
                    result = converter._find_kcc_command(allow_bootstrap=True)

        self.assertEqual(result, [sys.executable, str(repo_script)])

    def test_kcc_validates_command_before_using(self):
        """Test that found command is validated before returning."""
        converter = KCCConverter()

        with patch("shutil.which") as mock_which:
            with patch.object(converter, "_is_kcc_command") as mock_validation:
                mock_which.return_value = "/usr/local/bin/kcc-c2e"
                mock_validation.return_value = True

                result = converter._find_kcc_command()

                # Should validate the found binary
                mock_validation.assert_called()
                self.assertIsNotNone(result)

    def test_kcc_rejects_invalid_command(self):
        """Test that invalid commands are rejected."""
        converter = KCCConverter()

        with patch("shutil.which") as mock_which:
            with patch.object(converter, "_is_kcc_command", return_value=False):
                mock_which.return_value = "/usr/local/bin/kcc-c2e"

                result = converter._find_kcc_command()

                # Should reject the invalid command
                self.assertIsNone(result)

    def test_kcc_validation_rejects_heimdal(self):
        """Test that Kerberos kcc is rejected."""
        converter = KCCConverter()

        with patch("subprocess.run") as mock_run:
            # Simulate kcc version output with "heimdal"
            mock_run.return_value = Mock(
                returncode=0,
                stdout="Kerberos 5 Release 1.21.0 (heimdal)\n",
                stderr="",
            )

            result = converter._is_kcc_command(["kcc"])

            self.assertFalse(result)

    def test_kcc_validation_accepts_comic_output(self):
        """Test that comic-related output passes validation."""
        converter = KCCConverter()

        with patch("subprocess.run") as mock_run:
            # Simulate KCC version output
            mock_run.return_value = Mock(
                returncode=0,
                stdout="KindleComicConverter 5.4.0\n",
                stderr="",
            )

            result = converter._is_kcc_command(["kcc-c2e"])

            self.assertTrue(result)

    def test_kcc_validation_rejects_nonzero_return(self):
        """Test that non-zero return codes are rejected."""
        converter = KCCConverter()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=127,
                stdout="",
                stderr="command not found",
            )

            result = converter._is_kcc_command(["invalid-kcc"])

            self.assertFalse(result)

    def test_kcc_validation_uses_help_fallback(self):
        """Accept KCC script when --version fails but --help is valid."""
        converter = KCCConverter()

        calls = [
            Mock(returncode=2, stdout="", stderr="unrecognized argument --version"),
            Mock(
                returncode=0,
                stdout="usage: kcc-c2e [options] [input]\ncomic2ebook",
                stderr="",
            ),
        ]

        with patch("subprocess.run", side_effect=calls):
            result = converter._is_kcc_command([sys.executable, "/tmp/kcc-c2e.py"])

        self.assertTrue(result)

    def test_find_7z_command_detects_path_binary(self):
        """7z detection should accept a working PATH-resolved binary."""
        converter = KCCConverter()

        def which_side_effect(name, path=None):
            _ = path
            if name == "7z":
                return "/usr/local/bin/7z"
            return None

        with patch("shutil.which", side_effect=which_side_effect):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(
                    returncode=0,
                    stdout="7-Zip [64] 24.09",
                    stderr="",
                )
                found = converter._find_7z_command()

        self.assertEqual(found, "/usr/local/bin/7z")

    def test_find_7z_command_falls_back_to_7zz(self):
        """7z detection should also support environments exposing only 7zz."""
        converter = KCCConverter()

        def which_side_effect(name, path=None):
            _ = path
            if name == "7zz":
                return "/opt/homebrew/bin/7zz"
            return None

        with patch("shutil.which", side_effect=which_side_effect):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(
                    returncode=0,
                    stdout="7-Zip (a) 24.09",
                    stderr="",
                )
                found = converter._find_7z_command()

        self.assertEqual(found, "/opt/homebrew/bin/7zz")

    def test_find_7z_command_uses_augmented_path(self):
        """Probe should resolve commands using fallback dirs in GUI PATH contexts."""
        converter = KCCConverter()
        observed = {}

        def which_side_effect(name, path=None):
            observed[name] = path
            if name == "7z" and path and "/opt/homebrew/bin" in path:
                return "/opt/homebrew/bin/7z"
            return None

        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
            with patch("shutil.which", side_effect=which_side_effect):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = Mock(
                        returncode=0,
                        stdout="7-Zip [64] 24.09",
                        stderr="",
                    )
                    found = converter._find_7z_command()

        self.assertEqual(found, "/opt/homebrew/bin/7z")
        self.assertIn("/opt/homebrew/bin", observed.get("7z", ""))

    def test_find_7z_command_passes_probe_env(self):
        """7z probe subprocess should run with augmented PATH env."""
        converter = KCCConverter()

        with patch("shutil.which", return_value="/usr/local/bin/7z"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(
                    returncode=0,
                    stdout="7-Zip",
                    stderr="",
                )
                found = converter._find_7z_command()

        self.assertEqual(found, "/usr/local/bin/7z")
        kwargs = mock_run.call_args.kwargs
        self.assertIn("env", kwargs)
        self.assertIn("/opt/homebrew/bin", kwargs["env"].get("PATH", ""))

    def test_find_7z_command_skips_non_executable_absolute_candidates(self):
        """Absolute candidates should be ignored when not executable."""
        converter = KCCConverter()

        with patch("shutil.which", return_value=None):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.is_file", return_value=True):
                    with patch("os.access", return_value=False):
                        with patch("subprocess.run") as mock_run:
                            found = converter._find_7z_command()

        self.assertIsNone(found)
        mock_run.assert_not_called()

    def test_build_runtime_env_adds_7zz_shim_on_macos(self):
        """When only 7z is found, runtime env should still expose 7zz on macOS."""
        converter = KCCConverter()

        with patch("platform.system", return_value="Darwin"):
            with patch.object(
                converter,
                "_find_7z_command",
                return_value="/opt/homebrew/bin/7z",
            ):
                env = converter._build_runtime_env()

        self.assertIn("PATH", env)
        self.assertIsNotNone(shutil.which("7zz", path=env["PATH"]))

    def test_runtime_status_checks_required_archive_tool_name(self):
        """Runtime status should verify KCC-required tool name, not just any 7z variant."""
        converter = KCCConverter()

        with patch("platform.system", return_value="Darwin"):
            with patch.object(
                converter,
                "_find_7z_command",
                return_value="/opt/homebrew/bin/7z",
            ):
                with patch.object(
                    converter,
                    "_find_kcc_command",
                    return_value=["/tmp/kcc-c2e"],
                ):
                    status = converter.get_runtime_status()

        self.assertTrue(status["seven_zip_available"])
        self.assertTrue(str(status["seven_zip_command"]).endswith("7zz"))


if __name__ == "__main__":
    unittest.main()

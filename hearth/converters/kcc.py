"""Comic converter using KCC (Kindle Comic Converter)."""

import os
import sys
import subprocess
import shutil
import importlib.util
import platform
import tempfile
import shlex
from pathlib import Path
from typing import Optional

from .base import BaseConverter, ConversionFormat, ConversionResult


class KCCConverter(BaseConverter):
    """Converter for comic formats using Kindle Comic Converter."""

    # Supported comic formats
    SUPPORTED_FORMATS = [
        ".cbz",
        ".cbr",
        ".cb7",
        ".cbt",
        ".cba",
        ".zip",
        ".rar",
    ]
    KCC_REPO_URL = "https://github.com/ciromattia/kcc.git"

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        keep_original: bool = True,
        quality: str = "high",
        remove_margins: bool = True,
    ):
        super().__init__(output_dir, keep_original)
        self.quality = quality
        self.remove_margins = remove_margins
        self.kcc_repo_dir = Path(
            os.environ.get(
                "HEARTH_KCC_REPO_DIR",
                str(Path.home() / ".cache" / "hearth" / "tools" / "kcc"),
            )
        )
        self.kcc_command: Optional[list[str]] = None
        self._install_attempted = False
        self._seven_zip_shim_dir: Optional[Path] = None

    def _kcc_archive_tool_name(self) -> str:
        """Return the archive tool name required by the active KCC build."""
        return "7zz" if platform.system() == "Darwin" else "7z"

    def _ensure_seven_zip_shims(self, seven_zip_command: str) -> Optional[Path]:
        """Create PATH shims so both 7z and 7zz names resolve consistently."""
        command_path = Path(seven_zip_command)
        if not command_path.exists():
            return None

        if self._seven_zip_shim_dir is None:
            self._seven_zip_shim_dir = Path(tempfile.mkdtemp(prefix="hearth-kcc-7zip-"))

        quoted_command = shlex.quote(str(command_path))
        script_body = f'#!/bin/sh\nexec {quoted_command} "$@"\n'
        for alias in ("7z", "7zz"):
            shim_path = self._seven_zip_shim_dir / alias
            try:
                shim_path.write_text(script_body, encoding="utf-8")
                shim_path.chmod(0o755)
            except OSError:
                return None

        return self._seven_zip_shim_dir

    def _find_kcc_command(self, allow_bootstrap: bool = False) -> Optional[list[str]]:
        """Find Kindle Comic Converter CLI command.

        Prioritizes CLI binaries and the official repository script.
        Avoids false-positive `/usr/bin/kcc` on macOS (Kerberos kcc).
        """
        # Try binary commands in order: kcc-c2e, kcc-c2e.py, comic2ebook
        # These are common KCC CLI entry points.
        cli_candidates = ["kcc-c2e", "kcc-c2e.py", "comic2ebook"]
        for name in cli_candidates:
            resolved = shutil.which(name)
            if resolved and self._is_kcc_command([resolved]):
                return [resolved]

        # If CLI commands are unavailable, bootstrap the official KCC repo
        # and run the standalone script directly.
        repo_script = self._ensure_repo_script()
        if allow_bootstrap and repo_script:
            script_command = [sys.executable, str(repo_script)]
            if self._is_kcc_command(script_command):
                return script_command

        # Only try Python module invocation if no CLI binary was found.
        # KCC modules don't always have proper entry points for -m invocation.
        module_variants = [
            "comic2ebook",
            "kindlecomicconverter.comic2ebook",
            "kcc_c2e",
        ]
        for module_name in module_variants:
            command = [sys.executable, "-m", module_name]
            if self._is_kcc_command(command):
                return command

        # Fallback for users with a `kcc` shim installed. Reject Kerberos kcc.
        fallback = shutil.which("kcc")
        if not fallback:
            return None

        if self._is_kcc_command([fallback]):
            return [fallback]
        return None

    def _ensure_kcc_command(self, allow_bootstrap: bool) -> Optional[list[str]]:
        """Resolve and cache KCC command only when needed."""
        if self.kcc_command and self._is_kcc_command(self.kcc_command):
            return self.kcc_command

        resolved = self._find_kcc_command(allow_bootstrap=allow_bootstrap)
        if resolved:
            self.kcc_command = resolved
            return resolved

        return None

    def ensure_kcc_available(self, allow_bootstrap: bool = False) -> bool:
        """Public helper for on-demand KCC availability checks."""
        return self._ensure_kcc_command(allow_bootstrap=allow_bootstrap) is not None

    def _ensure_repo_script(self) -> Optional[Path]:
        """Ensure official KCC repository exists and return kcc-c2e.py path."""
        script_path = self.kcc_repo_dir / "kcc-c2e.py"
        if script_path.exists():
            return script_path

        auto_fetch = os.environ.get("HEARTH_KCC_AUTO_FETCH", "1").strip().lower()
        if auto_fetch in {"0", "false", "off", "no"}:
            return None

        git_path = shutil.which("git")
        if not git_path:
            return None

        repo_parent = self.kcc_repo_dir.parent
        try:
            repo_parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        # If directory already exists but script is missing, avoid destructive
        # behavior and just treat as unavailable.
        if self.kcc_repo_dir.exists():
            return None

        try:
            clone_result = subprocess.run(
                [
                    git_path,
                    "clone",
                    "--depth",
                    "1",
                    self.KCC_REPO_URL,
                    str(self.kcc_repo_dir),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if clone_result.returncode != 0:
            return None

        if script_path.exists():
            return script_path
        return None

    def _is_kcc_command(self, command: list[str]) -> bool:
        """Validate that a discovered command is Kindle Comic Converter.

        Tests common info flags to ensure the command actually works.
        """
        flags = ["--version", "--help", "-h"]
        cwd = self._command_cwd(command)
        for flag in flags:
            try:
                result = subprocess.run(
                    [*command, flag],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                    cwd=cwd,
                )
            except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                continue

            output = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()

            # Reject Kerberos kcc (macOS false positive)
            if "heimdal" in output or "kerberos" in output:
                return False

            # Known KCC text markers across versions/help output.
            markers = (
                "kindle comic converter",
                "kindlecomicconverter",
                "comic2ebook",
                "kcc-c2e",
                "usage:",
                "--profile",
            )

            if result.returncode == 0 and any(marker in output for marker in markers):
                return True

            # Some wrappers may return non-zero for --version but still print
            # valid usage/help for KCC.
            if any(marker in output for marker in markers):
                return True

        return False

    def _command_cwd(self, command: list[str]) -> Optional[str]:
        """Return cwd hint for script-based commands.

        Running kcc-c2e.py from repository root makes relative imports/tool
        discovery behavior consistent with upstream usage.
        """
        if len(command) >= 2 and command[0] == sys.executable:
            script = Path(command[1])
            if script.name == "kcc-c2e.py":
                return str(script.parent)
        return None

    def _try_install_kcc_package(self) -> bool:
        """Check if KCC can be installed. Currently unsupported since KCC
        is not on PyPI. Users should install via system package manager or
        from source.
        """
        # KCC is not available on PyPI, so we cannot auto-install it.
        # Users must install via:
        # - macOS: brew install kcc (if available)
        # - Linux: apt install kcc or build from source
        # - Manual: https://github.com/ciromattia/kcc
        #
        # If the user has KCC installed, the discovery will find it.
        return False

    def _find_7z_command(self) -> Optional[str]:
        """Find a usable 7z executable.

        Some installs expose `7zz` instead of `7z`, and GUI-launched apps can
        miss shell PATH entries, so we also check common absolute paths.
        """
        probe_env = self._build_base_runtime_env()
        probe_path = probe_env.get("PATH", "")
        candidates: list[str] = [
            "7z",
            "7zz",
            "/opt/homebrew/bin/7z",
            "/opt/homebrew/bin/7zz",
            "/usr/local/bin/7z",
            "/usr/local/bin/7zz",
            "/usr/bin/7z",
            "/usr/bin/7zz",
        ]

        # Resolve bare command names through PATH first.
        resolved: list[str] = []
        for candidate in candidates:
            if "/" in candidate:
                candidate_path = Path(candidate)
                if (
                    candidate_path.exists()
                    and candidate_path.is_file()
                    and os.access(candidate_path, os.X_OK)
                ):
                    resolved.append(str(candidate_path))
                continue
            located = shutil.which(candidate, path=probe_path)
            if located:
                resolved.append(located)

        # Deduplicate while preserving order.
        seen = set()
        unique_resolved = []
        for path in resolved:
            if path in seen:
                continue
            seen.add(path)
            unique_resolved.append(path)

        for command in unique_resolved:
            try:
                result = subprocess.run(
                    [command, "-h"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    env=probe_env,
                )
            except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                continue

            output = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
            has_signature = any(
                marker in output
                for marker in (
                    "7-zip",
                    "7 zip",
                    "p7zip",
                    "7z ",
                    "7zz",
                )
            )
            if has_signature and result.returncode in {0, 1, 2}:
                return command

        return None

    def _build_base_runtime_env(self) -> dict[str, str]:
        """Build baseline env with fallback PATH locations for GUI sessions."""
        env = os.environ.copy()
        path_parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []

        fallback_dirs = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        for path_dir in fallback_dirs:
            if path_dir not in path_parts:
                path_parts.append(path_dir)

        env["PATH"] = os.pathsep.join(path_parts)
        return env

    def _build_runtime_env(self) -> dict[str, str]:
        """Build env for KCC subprocess with robust PATH for helper tools."""
        env = self._build_base_runtime_env()
        path_parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []

        seven_zip_command = self._find_7z_command()
        if seven_zip_command:
            shim_dir = self._ensure_seven_zip_shims(seven_zip_command)
            if shim_dir:
                shim_dir_text = str(shim_dir)
                if shim_dir_text not in path_parts:
                    path_parts.insert(0, shim_dir_text)

            seven_zip_dir = str(Path(seven_zip_command).parent)
            if seven_zip_dir not in path_parts:
                path_parts.insert(0, seven_zip_dir)

        env["PATH"] = os.pathsep.join(path_parts)
        return env

    def get_runtime_status(self) -> dict[str, object]:
        """Return detailed KCC readiness diagnostics for startup checks."""
        command = self.kcc_command or self._find_kcc_command(allow_bootstrap=False)
        command_text = " ".join(command) if command else ""

        script_path = self.kcc_repo_dir / "kcc-c2e.py"
        repo_exists = self.kcc_repo_dir.exists()
        script_exists = script_path.exists()
        git_available = bool(shutil.which("git"))
        runtime_env = self._build_runtime_env()
        runtime_path = runtime_env.get("PATH", "")
        required_archive_tool = self._kcc_archive_tool_name()
        seven_zip_command = shutil.which(required_archive_tool, path=runtime_path)
        seven_zip_available = bool(seven_zip_command)
        auto_fetch = os.environ.get("HEARTH_KCC_AUTO_FETCH", "1").strip().lower()
        auto_fetch_enabled = auto_fetch not in {"0", "false", "off", "no"}

        module_map = {
            "Pillow": "PIL",
            "psutil": "psutil",
            "requests": "requests",
            "python-slugify": "slugify",
            "packaging": "packaging",
            "mozjpeg-lossless-optimization": "mozjpeg_lossless_optimization",
            "natsort": "natsort",
            "distro": "distro",
            "numpy": "numpy",
            "PyMuPDF": "pymupdf",
        }

        missing_python_modules = [
            dep_name
            for dep_name, module_name in module_map.items()
            if importlib.util.find_spec(module_name) is None
        ]

        issues: list[str] = []
        if not command:
            issues.append("KCC command not detected")
        if not git_available:
            issues.append("git is not available")
        if not repo_exists and auto_fetch_enabled and git_available:
            issues.append("KCC repo not yet cloned")
        if repo_exists and not script_exists:
            issues.append("kcc-c2e.py missing in KCC repo")
        if missing_python_modules:
            issues.append(
                "Missing KCC Python dependencies: " + ", ".join(missing_python_modules)
            )
        if not seven_zip_available:
            issues.append(
                f"{required_archive_tool} not found " "(needed for some comic archives)"
            )

        return {
            "ready": bool(command) and not missing_python_modules,
            "command": command,
            "command_text": command_text,
            "repo_dir": str(self.kcc_repo_dir),
            "repo_exists": repo_exists,
            "script_exists": script_exists,
            "git_available": git_available,
            "auto_fetch_enabled": auto_fetch_enabled,
            "seven_zip_available": seven_zip_available,
            "seven_zip_command": seven_zip_command,
            "missing_python_modules": missing_python_modules,
            "issues": issues,
        }

    def can_convert(self, input_path: Path) -> bool:
        """Check if file is a supported comic format."""
        return input_path.suffix.lower() in self.SUPPORTED_FORMATS

    def get_supported_formats(self) -> list[str]:
        """Return list of supported input formats."""
        return self.SUPPORTED_FORMATS

    def convert(
        self,
        input_path: Path,
        output_format: ConversionFormat = ConversionFormat.MOBI,
        manga_rtl: bool = False,
    ) -> ConversionResult:
        """Convert comic to Kindle format using KCC."""
        self._log_progress(f"Starting comic conversion: {input_path.name}")

        if not input_path.exists():
            return ConversionResult(False, error=f"Input file not found: {input_path}")

        if not self.can_convert(input_path):
            return ConversionResult(
                False, error=f"Unsupported comic format: {input_path.suffix}"
            )

        if not self.kcc_command:
            self._ensure_kcc_command(allow_bootstrap=True)

        if not self.kcc_command:
            return ConversionResult(
                False,
                error=(
                    "Kindle Comic Converter (KCC) not found. "
                    "Please install KCC manually:\n"
                    "- macOS: brew install kcc\n"
                    "- Linux: apt install kcc (or build from source)\n"
                    "- Manual: https://github.com/ciromattia/kcc\n"
                    "Comic conversion will not work without KCC."
                ),
            )

        # Log which KCC command is being used for debugging
        cmd_display = (
            " ".join(self.kcc_command)
            if len(self.kcc_command) <= 2
            else f"{self.kcc_command[0]} ..."
        )
        self._log_progress(f"Using KCC: {cmd_display}")

        # Prepare output
        output_name = input_path.stem + "." + output_format.value
        output_path = self.output_dir / output_name

        try:
            # Build KCC command
            cmd = [*self.kcc_command, "-p", "KS", "-o", str(output_path)]

            # KCC CLI quality tuning: -q is the high-quality mode.
            if self.quality == "high":
                cmd.append("-q")

            # `remove_margins` has no direct 1:1 CLI flag in kcc-c2e.py,
            # so we keep default KCC cropping behavior.

            if manga_rtl:
                self._log_progress("Detected manga/RTL metadata; enabling manga mode")
                cmd.append("-m")

            cmd.append(str(input_path))

            self._log_progress(f"Running KCC: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                cwd=self._command_cwd(self.kcc_command),
                env=self._build_runtime_env(),
            )

            # If manga flag is unsupported by an unexpected KCC variant,
            # retry once without manga mode.
            if (
                manga_rtl
                and result.returncode != 0
                and "unrecognized" in ((result.stderr or result.stdout).lower())
                and "-m" in cmd
            ):
                self._log_progress(
                    "KCC manga flag not supported by this build; retrying without it"
                )
                retry_cmd = [arg for arg in cmd if arg != "-m"]
                result = subprocess.run(
                    retry_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                    cwd=self._command_cwd(self.kcc_command),
                    env=self._build_runtime_env(),
                )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                cmd_str = " ".join(self.kcc_command)
                return ConversionResult(
                    False,
                    error=(
                        f"KCC conversion failed with command '{cmd_str}': "
                        f"{error_msg}"
                    ),
                )

            if not output_path.exists():
                return ConversionResult(
                    False, error="Output file was not created by KCC"
                )

            self._log_progress(f"Comic conversion successful: {output_path.name}")

            # Remove original if requested
            if not self.keep_original:
                input_path.unlink()
                self._log_progress(f"Removed original: {input_path.name}")

            return ConversionResult(True, output_path=output_path)

        except subprocess.TimeoutExpired:
            return ConversionResult(
                False, error="KCC conversion timed out after 5 minutes"
            )
        except OSError as error:
            return ConversionResult(
                False,
                error=f"Unexpected error during comic conversion: {error}",
            )

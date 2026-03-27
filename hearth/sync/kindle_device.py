"""Kindle device interface for USB and libmtp-based MTP connections."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class KindleMetadata:
    """Metadata for tracking synced books on Kindle."""

    title: str
    author: str
    opds_id: str
    original_format: str
    kindle_format: str
    sync_date: str
    local_path: Optional[str] = None


class KindleDevice:
    """Interface for accessing Kindle over libmtp tools or USB filesystem."""

    KINDLE_DOCS_DIR = "documents"
    HEARTH_FOLDER = "Hearth"
    KINDLE_METADATA_FILE = ".hearth_metadata.json"

    def __init__(
        self,
        mount_path: Optional[Path] = None,
        auto_mount_mtp: bool = True,
        preferred_mtp_tool: str = "auto",
        auto_install_mtp_backend: bool = True,
    ):
        self.mount_path = mount_path
        self.auto_mount_mtp = auto_mount_mtp
        self.preferred_mtp_tool = preferred_mtp_tool
        self.auto_install_mtp_backend = auto_install_mtp_backend

        self._detected_path: Optional[Path] = None
        self._transport: str = "none"
        self._mtp_install_attempted = False
        self._mtp_tools_available: Optional[bool] = None

    def is_connected(self) -> bool:
        """Check if Kindle is connected over MTP, then USB mass storage."""
        if self._detect_mtp_kindle():
            return True

        mount = self.get_mount_path()
        return mount is not None

    def get_transport(self) -> str:
        """Return active transport: mtp-libmtp, usb, or none."""
        return self._transport

    def get_mount_path(self) -> Optional[Path]:
        """Get filesystem mount path for USB mode, if available."""
        if self._detect_mtp_kindle():
            self._transport = "mtp-libmtp"
            return None

        if self._transport == "mtp-libmtp":
            return None

        if self.mount_path:
            mount = Path(self.mount_path)
            if mount.exists():
                self._transport = "usb"
                return mount

        usb_path = self._detect_usb_kindle()
        if usb_path:
            self._transport = "usb"
            return usb_path

        self._transport = "none"
        return None

    def get_documents_dir(self) -> Optional[Path]:
        """Get the documents directory path for USB mode."""
        mount = self.get_mount_path()
        if not mount:
            return None
        return mount / self.KINDLE_DOCS_DIR

    def get_hearth_dir(self) -> Optional[Path]:
        """Get the Hearth folder path for USB mode."""
        docs_dir = self.get_documents_dir()
        if not docs_dir:
            return None
        return docs_dir / self.HEARTH_FOLDER

    def ensure_hearth_folder_exists(self) -> bool:
        """Ensure Hearth folder exists on Kindle."""
        if self._detect_mtp_kindle():
            for remote_path in self._mtp_hearth_candidates():
                if self._run_mtp_connect(["--newfolder", remote_path]):
                    return True
            return False

        hearth_dir = self.get_hearth_dir()
        if not hearth_dir:
            return False

        try:
            hearth_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    def copy_to_kindle(self, file_path: Path) -> bool:
        """Copy a file to the Kindle Hearth folder."""
        if not file_path.exists():
            return False

        if self._detect_mtp_kindle():
            if not self.ensure_hearth_folder_exists():
                return False
            for remote_dir in self._mtp_hearth_candidates():
                if self._run_mtp_connect(["--sendfile", f"{file_path},{remote_dir}"]):
                    return True
            return False

        hearth_dir = self.get_hearth_dir()
        if not hearth_dir:
            return False

        try:
            hearth_dir.mkdir(parents=True, exist_ok=True)
            dest_path = hearth_dir / file_path.name
            shutil.copy2(file_path, dest_path)
            return True
        except OSError:
            return False

    def load_metadata(self) -> Dict[str, KindleMetadata]:
        """Load Hearth metadata from Kindle."""
        if self._detect_mtp_kindle():
            for remote_hearth in self._mtp_hearth_candidates():
                local_tmp_dir = Path(tempfile.mkdtemp(prefix="hearth_mtp_meta_"))
                local_file = local_tmp_dir / self.KINDLE_METADATA_FILE
                remote_file = f"{remote_hearth}/{self.KINDLE_METADATA_FILE}"
                ok = self._run_mtp_connect(["--getfile", f"{remote_file},{local_file}"])
                if not ok or not local_file.exists():
                    continue
                try:
                    with open(
                        local_file,
                        "r",
                        encoding="utf-8",
                    ) as file_handle:
                        data = json.load(file_handle)
                    return {key: KindleMetadata(**value) for key, value in data.items()}
                except (OSError, json.JSONDecodeError, TypeError):
                    return {}
            return {}

        hearth_dir = self.get_hearth_dir()
        if not hearth_dir:
            return {}

        metadata_file = hearth_dir / self.KINDLE_METADATA_FILE
        if not metadata_file.exists():
            return {}

        try:
            with open(metadata_file, "r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
            return {key: KindleMetadata(**value) for key, value in data.items()}
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    def save_metadata(self, metadata_dict: Dict[str, KindleMetadata]) -> bool:
        """Save Hearth metadata to Kindle."""
        data = {
            key: {
                "title": meta.title,
                "author": meta.author,
                "opds_id": meta.opds_id,
                "original_format": meta.original_format,
                "kindle_format": meta.kindle_format,
                "sync_date": meta.sync_date,
                "local_path": meta.local_path,
            }
            for key, meta in metadata_dict.items()
        }

        if self._detect_mtp_kindle():
            if not self.ensure_hearth_folder_exists():
                return False

            local_tmp_dir = Path(tempfile.mkdtemp(prefix="hearth_mtp_meta_"))
            local_file = local_tmp_dir / self.KINDLE_METADATA_FILE
            try:
                with open(local_file, "w", encoding="utf-8") as file_handle:
                    json.dump(data, file_handle, indent=2)
            except OSError:
                return False

            for remote_dir in self._mtp_hearth_candidates():
                if self._run_mtp_connect(["--sendfile", f"{local_file},{remote_dir}"]):
                    return True
            return False

        hearth_dir = self.get_hearth_dir()
        if not hearth_dir:
            return False

        try:
            hearth_dir.mkdir(parents=True, exist_ok=True)
            metadata_file = hearth_dir / self.KINDLE_METADATA_FILE
            with open(metadata_file, "w", encoding="utf-8") as file_handle:
                json.dump(data, file_handle, indent=2)
            return True
        except OSError:
            return False

    def list_books(self) -> List[str]:
        """List supported ebook files in Hearth folder on Kindle."""
        supported_extensions = {".mobi", ".azw", ".azw3", ".pdf", ".kfx"}

        if self._detect_mtp_kindle():
            books: List[str] = []
            for entry in self.list_file_tree():
                if entry.get("is_dir"):
                    continue
                full_path = str(entry.get("full_path", "")).lower()
                if "/documents/hearth/" not in full_path:
                    continue
                suffix = Path(full_path).suffix.lower()
                if suffix in supported_extensions:
                    books.append(str(entry.get("name", "")))
            return books

        hearth_dir = self.get_hearth_dir()
        if not hearth_dir or not hearth_dir.exists():
            return []

        usb_books: List[str] = []
        for file_path in hearth_dir.rglob("*"):
            if file_path.suffix.lower() in supported_extensions:
                usb_books.append(file_path.name)
        return usb_books

    def list_file_tree(self) -> List[Dict[str, object]]:
        """Return recursive Kindle file entries for UI browsing."""
        if self._detect_mtp_kindle():
            return self._list_file_tree_from_mtp_filetree()

        mount = self.get_mount_path()
        if not mount:
            return []

        file_entries: List[Dict[str, object]] = []
        for path in sorted(mount.rglob("*"), key=lambda p: str(p).lower()):
            try:
                stat = path.stat()
                rel = str(path.relative_to(mount)).replace("\\", "/")
                rel_path = "/" + rel
                file_entries.append(
                    {
                        "full_path": rel_path,
                        "name": path.name,
                        "is_dir": path.is_dir(),
                        "size": stat.st_size if path.is_file() else 0,
                        "mod_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )
            except OSError:
                continue
        return file_entries

    def _detect_usb_kindle(self) -> Optional[Path]:
        """Detect Kindle exposed as filesystem (USB mass storage)."""
        if self._detected_path and self._detected_path.exists():
            return self._detected_path

        system = platform.system()
        potential_paths: List[Path] = []

        if system == "Darwin":
            volumes = Path("/Volumes")
            if volumes.exists():
                for item in volumes.iterdir():
                    if item.is_dir() and item.name != "Macintosh HD":
                        potential_paths.append(item)

        elif system == "Windows":
            for drive in range(ord("D"), ord("Z")):
                path = Path(f"{chr(drive)}:/")
                if path.exists():
                    potential_paths.append(path)

        elif system == "Linux":
            for root in [Path("/run/media"), Path("/media"), Path("/mnt")]:
                if root.exists():
                    potential_paths.extend([p for p in root.rglob("*") if p.is_dir()])

        for path in potential_paths:
            if self._is_kindle_device(path):
                self._detected_path = path
                return path

        return None

    def _is_kindle_device(self, path: Path) -> bool:
        """Check if path looks like a Kindle root directory."""
        if (path / self.KINDLE_DOCS_DIR).exists():
            return True

        for system_file in ["system", "Serial.txt", "Model"]:
            if (path / system_file).exists():
                return True

        name = path.name.lower()
        return "kindle" in name or "amazon" in name

    def _detect_mtp_kindle(self) -> bool:
        """Detect Kindle via libmtp tooling and set active transport."""
        if not self._ensure_mtp_tools_available():
            return False

        result = self._run_command(["mtp-detect"], timeout=60)
        if not result:
            return False

        combined = f"{result.stdout}\n{result.stderr}"
        if not self._contains_kindle_signature(combined):
            return False

        self._transport = "mtp-libmtp"
        return True

    def _contains_kindle_signature(self, text: str) -> bool:
        """Return True if command output looks like a Kindle device."""
        lower_text = text.lower()
        needles = [
            "kindle",
            "amazon",
            "vendor id: 0x1949",
            "vid=1949",
            "vid=0x1949",
        ]
        return any(needle in lower_text for needle in needles)

    def _ensure_mtp_tools_available(self) -> bool:
        """Ensure libmtp CLI tools are available."""
        if self._mtp_tools_available is not None:
            return self._mtp_tools_available

        required = ["mtp-detect", "mtp-connect", "mtp-filetree"]
        if all(shutil.which(cmd) for cmd in required):
            self._mtp_tools_available = True
            return True

        if not self.auto_install_mtp_backend:
            self._mtp_tools_available = False
            return False

        if platform.system() != "Darwin":
            self._mtp_tools_available = False
            return False

        if self._mtp_install_attempted:
            self._mtp_tools_available = False
            return False

        self._mtp_install_attempted = True
        if not shutil.which("brew"):
            self._mtp_tools_available = False
            return False

        install = self._run_command(
            ["brew", "install", "libmtp"],
            timeout=1800,
        )
        if not install or install.returncode != 0:
            self._mtp_tools_available = False
            return False

        self._mtp_tools_available = all(shutil.which(cmd) for cmd in required)
        return bool(self._mtp_tools_available)

    def _run_command(
        self,
        command: List[str],
        timeout: int,
    ) -> Optional[subprocess.CompletedProcess[str]]:
        """Run a subprocess command safely and return result."""
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def _run_mtp_connect(self, args: List[str]) -> bool:
        """Run mtp-connect command and return success."""
        result = self._run_command(["mtp-connect", *args], timeout=180)
        if not result or result.returncode != 0:
            return False

        output = f"{result.stdout}\n{result.stderr}".lower()
        if "no devices" in output:
            return False
        if "error" in output and "new folder created" not in output:
            return False
        return True

    def _mtp_hearth_candidates(self) -> List[str]:
        """Return likely remote Hearth folder paths for Kindle MTP."""
        return [
            "/documents/Hearth",
            "/Documents/Hearth",
        ]

    def _list_file_tree_from_mtp_filetree(self) -> List[Dict[str, object]]:
        """Parse mtp-filetree output into a file-tree structure."""
        result = self._run_command(["mtp-filetree"], timeout=300)
        if not result or result.returncode != 0:
            return []

        rows: List[tuple[int, str]] = []
        for raw in (result.stdout or "").splitlines():
            line = raw.rstrip()
            match = re.match(r"^(\s*)(\d+)\s+(.+)$", line)
            if not match:
                continue
            indent = match.group(1)
            name = match.group(3).strip()
            depth = max(0, len(indent) // 2)
            rows.append((depth, name))

        entries: List[Dict[str, object]] = []
        path_stack: List[str] = []

        for idx, (depth, name) in enumerate(rows):
            while len(path_stack) > depth:
                path_stack.pop()
            path_stack = path_stack[:depth]
            path_stack.append(name)
            full_path = "/" + "/".join(path_stack)

            is_dir = False
            if idx + 1 < len(rows):
                next_depth, _ = rows[idx + 1]
                is_dir = next_depth > depth

            entries.append(
                {
                    "full_path": full_path,
                    "name": name,
                    "is_dir": is_dir,
                    "size": 0,
                    "mod_time": "",
                }
            )

        return entries

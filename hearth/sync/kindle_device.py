"""Kindle device interface for USB and MTP connections."""

import json
import platform
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
    """Interface for accessing Kindle device over USB or MTP."""

    KINDLE_DOCS_DIR = "documents"
    HEARTH_FOLDER = "Hearth"
    KINDLE_METADATA_FILE = ".hearth_metadata.json"

    MTP_HELPER_DIR = Path.home() / ".cache" / "hearth" / "mtp_api_helper"
    MTP_HELPER_FILE = MTP_HELPER_DIR / "main.go"

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
        self._mtp_api_ready = False

    def is_connected(self) -> bool:
        """Check if Kindle is connected over USB or MTP."""
        if self.mount_path:
            return Path(self.mount_path).exists()

        usb_path = self._detect_usb_kindle()
        if usb_path is not None:
            return True

        if self._detect_mtp_device():
            self._ensure_mtp_api_ready()
            return True

        return False

    def get_transport(self) -> str:
        """Return active transport: usb, mtp, mtp-api, or none."""
        return self._transport

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
                    matches = [p for p in root.rglob("*") if p.is_dir()]
                    potential_paths.extend(matches)

        for path in potential_paths:
            if self._is_kindle_device(path):
                self._detected_path = path
                self._transport = "usb"
                return path

        return None

    def _detect_mtp_device(self) -> bool:
        """Detect Kindle presented as MTP-only USB device on macOS."""
        if platform.system() != "Darwin":
            return False

        for command in [
            ["system_profiler", "SPUSBDataType"],
            ["ioreg", "-p", "IOUSB", "-w0", "-l"],
        ]:
            output = self._read_command_output(command)
            if output and self._contains_kindle_signature(output):
                self._transport = "mtp"
                return True

        return False

    def _contains_kindle_signature(self, text: str) -> bool:
        """Return True if command output looks like a Kindle device."""
        needles = [
            "kindle",
            "amazon kindle",
            "scribe",
            "idvendor 0x1949",
            "vendor id: 0x1949",
        ]
        lower_text = text.lower()
        return any(needle in lower_text for needle in needles)

    def _read_command_output(self, command: List[str]) -> str:
        """Run command and return stdout text or empty string."""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            return result.stdout or ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def _is_kindle_device(self, path: Path) -> bool:
        """Check if path looks like a Kindle root directory."""
        for system_file in ["Model", "Serial.txt", ".amazon"]:
            if (path / system_file).exists():
                return True

        name = path.name.lower()
        if "kindle" in name or "amazon" in name:
            return True

        kindle_markers = [
            "documents",
            "audible",
            "active-content-data",
            "system",
        ]
        matches = sum(1 for marker in kindle_markers if (path / marker).exists())
        return matches >= 2

    def _auto_install_go(self) -> bool:
        """Install Go automatically on macOS if missing."""
        if shutil.which("go"):
            return True
        if not self.auto_install_mtp_backend:
            return False
        if platform.system() != "Darwin":
            return False
        if self._mtp_install_attempted:
            return False

        self._mtp_install_attempted = True
        if not shutil.which("brew"):
            return False

        try:
            result = subprocess.run(
                ["brew", "install", "go"],
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            return result.returncode == 0 and bool(shutil.which("go"))
        except (OSError, subprocess.SubprocessError):
            return False

    def _helper_source(self) -> str:
        """Return source code for the go-mtpx helper CLI."""
        return """package main

import (
    \"encoding/json\"
    \"fmt\"
    mtpx \"github.com/ganeshrvel/go-mtpx\"
    \"os\"
)

type Entry struct {
    FullPath string `json:\"full_path\"`
    Name string `json:\"name\"`
    IsDir bool `json:\"is_dir\"`
    Size int64 `json:\"size\"`
    ModTime string `json:\"mod_time\"`
}

func connect() (*mtpx.Device, uint32, error) {
    dev, err := mtpx.Initialize(mtpx.Init{})
    if err != nil {
        return nil, 0, err
    }
    storages, err := mtpx.FetchStorages(dev)
    if err != nil {
        mtpx.Dispose(dev)
        return nil, 0, err
    }
    if len(storages) == 0 {
        mtpx.Dispose(dev)
        return nil, 0, fmt.Errorf(\"no mtp storage\")
    }
    return dev, storages[0].Sid, nil
}

func main() {
    if len(os.Args) < 2 {
        fmt.Fprintln(os.Stderr, \"missing command\")
        os.Exit(2)
    }

    cmd := os.Args[1]
    dev, sid, err := connect()
    if err != nil {
        fmt.Fprintln(os.Stderr, err.Error())
        os.Exit(1)
    }
    defer mtpx.Dispose(dev)

    switch cmd {
    case \"tree\":
        if len(os.Args) < 3 {
            fmt.Fprintln(os.Stderr, \"tree requires remote path\")
            os.Exit(2)
        }
        remote := os.Args[2]
        entries := []Entry{}
        _, _, _, err = mtpx.Walk(
            dev, sid, remote, true, true, false,
            func(_ uint32, fi *mtpx.FileInfo, walkErr error) error {
                if walkErr != nil {
                    return walkErr
                }
                entries = append(entries, Entry{
                    FullPath: fi.FullPath,
                    Name: fi.Name,
                    IsDir: fi.IsDir,
                    Size: fi.Size,
                    ModTime: fi.ModTime.Format(\"2006-01-02T15:04:05\"),
                })
                return nil
            },
        )
        if err != nil {
            fmt.Fprintln(os.Stderr, err.Error())
            os.Exit(1)
        }
        _ = json.NewEncoder(os.Stdout).Encode(entries)
    case \"mkdir\":
        if len(os.Args) < 3 {
            fmt.Fprintln(os.Stderr, \"mkdir requires remote path\")
            os.Exit(2)
        }
        _, err = mtpx.MakeDirectory(dev, sid, os.Args[2])
        if err != nil {
            fmt.Fprintln(os.Stderr, err.Error())
            os.Exit(1)
        }
    case \"download\":
        if len(os.Args) < 4 {
            fmt.Fprintln(os.Stderr, \"download requires remote and local dir\")
            os.Exit(2)
        }
        _, _, err = mtpx.DownloadFiles(
            dev,
            sid,
            []string{os.Args[2]},
            os.Args[3],
            false,
            func(_ *mtpx.FileInfo, cbErr error) error { return cbErr },
            func(_ *mtpx.ProgressInfo, cbErr error) error { return cbErr },
        )
        if err != nil {
            fmt.Fprintln(os.Stderr, err.Error())
            os.Exit(1)
        }
    case \"upload\":
        if len(os.Args) < 4 {
            fmt.Fprintln(
                os.Stderr,
                \"upload requires local file and remote dir\",
            )
            os.Exit(2)
        }
        _, _, _, err = mtpx.UploadFiles(
            dev,
            sid,
            []string{os.Args[2]},
            os.Args[3],
            false,
            func(_ *os.FileInfo, _ string, cbErr error) error { return cbErr },
            func(_ *mtpx.ProgressInfo, cbErr error) error { return cbErr },
        )
        if err != nil {
            fmt.Fprintln(os.Stderr, err.Error())
            os.Exit(1)
        }
    default:
        fmt.Fprintln(os.Stderr, \"unknown command\")
        os.Exit(2)
    }
}
"""

    def _ensure_mtp_api_ready(self) -> bool:
        """Ensure go-mtpx API helper can be executed."""
        if self._mtp_api_ready:
            return True

        if not shutil.which("go") and not self._auto_install_go():
            return False

        self.MTP_HELPER_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.MTP_HELPER_FILE, "w", encoding="utf-8") as f:
                f.write(self._helper_source())
        except OSError:
            return False

        if not self._mtp_api_call(["tree", "/"], expect_json=True):
            return False

        self._mtp_api_ready = True
        self._transport = "mtp-api"
        return True

    def _mtp_api_call(
        self,
        args: List[str],
        expect_json: bool = False,
    ) -> Optional[object]:
        """Call the go-mtpx helper and return output or parsed JSON."""
        if not shutil.which("go"):
            return None

        command = ["go", "run", str(self.MTP_HELPER_FILE)] + args
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if result.returncode != 0:
            return None

        if expect_json:
            try:
                return json.loads(result.stdout or "[]")
            except json.JSONDecodeError:
                return None

        return result.stdout

    def _is_mtp_api_active(self) -> bool:
        """Return True when non-mount MTP API backend is active."""
        return self._transport == "mtp-api"

    def get_mount_path(self) -> Optional[Path]:
        """Get filesystem mount path for USB mode, if available."""
        if self.mount_path:
            mount = Path(self.mount_path)
            return mount if mount.exists() else None

        usb_path = self._detect_usb_kindle()
        if usb_path:
            return usb_path

        if self._detect_mtp_device() and self._ensure_mtp_api_ready():
            return None

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

    def _mtp_documents_path(self) -> str:
        """Remote MTP documents path."""
        return f"/{self.KINDLE_DOCS_DIR}"

    def _mtp_hearth_path(self) -> str:
        """Remote MTP Hearth path."""
        return f"/{self.KINDLE_DOCS_DIR}/{self.HEARTH_FOLDER}"

    def ensure_hearth_folder_exists(self) -> bool:
        """Ensure Hearth folder exists on Kindle."""
        self.get_mount_path()
        if self._is_mtp_api_active():
            result = self._mtp_api_call(["mkdir", self._mtp_hearth_path()])
            return result is not None

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
        self.get_mount_path()
        if not file_path.exists():
            return False

        if self._is_mtp_api_active():
            if not self.ensure_hearth_folder_exists():
                return False
            result = self._mtp_api_call(
                ["upload", str(file_path), self._mtp_hearth_path()]
            )
            return result is not None

        hearth_dir = self.get_hearth_dir()
        if not hearth_dir:
            return False

        try:
            hearth_dir.mkdir(parents=True, exist_ok=True)
            dest_path = hearth_dir / file_path.name
            with open(file_path, "rb") as src:
                with open(dest_path, "wb") as dst:
                    dst.write(src.read())
            return True
        except OSError:
            return False

    def _download_remote_file(self, remote_path: str) -> Optional[Path]:
        """Download one remote MTP file to a temporary local path."""
        local_dir = Path(tempfile.mkdtemp(prefix="hearth_mtp_dl_"))
        result = self._mtp_api_call(["download", remote_path, str(local_dir)])
        if result is None:
            return None

        candidates = list(local_dir.rglob(Path(remote_path).name))
        if candidates:
            return candidates[0]
        return None

    def load_metadata(self) -> Dict[str, KindleMetadata]:
        """Load Hearth metadata from Kindle."""
        self.get_mount_path()
        if self._is_mtp_api_active():
            remote_file = f"{self._mtp_hearth_path()}/" f"{self.KINDLE_METADATA_FILE}"
            local_file = self._download_remote_file(remote_file)
            if not local_file or not local_file.exists():
                return {}
            try:
                with open(local_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {key: KindleMetadata(**value) for key, value in data.items()}
            except (OSError, json.JSONDecodeError):
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
        except (OSError, json.JSONDecodeError):
            return {}

    def save_metadata(self, metadata_dict: Dict[str, KindleMetadata]) -> bool:
        """Save Hearth metadata to Kindle."""
        self.get_mount_path()
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

        if self._is_mtp_api_active():
            if not self.ensure_hearth_folder_exists():
                return False

            local_file = Path(tempfile.mkdtemp(prefix="hearth_mtp_meta_"))
            file_path = local_file / self.KINDLE_METADATA_FILE
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except OSError:
                return False

            result = self._mtp_api_call(
                ["upload", str(file_path), self._mtp_hearth_path()]
            )
            return result is not None

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
        self.get_mount_path()
        supported_extensions = [".mobi", ".azw", ".azw3", ".pdf"]

        if self._is_mtp_api_active():
            entries = self._mtp_api_call(
                ["tree", self._mtp_hearth_path()],
                expect_json=True,
            )
            if not isinstance(entries, list):
                return []
            mtp_books: List[str] = []
            for entry in entries:
                if entry.get("is_dir"):
                    continue
                path = str(entry.get("full_path", ""))
                suffix = Path(path).suffix.lower()
                if suffix in supported_extensions:
                    mtp_books.append(Path(path).name)
            return mtp_books

        hearth_dir = self.get_hearth_dir()
        if not hearth_dir or not hearth_dir.exists():
            return []

        books: List[str] = []
        for file_path in hearth_dir.rglob("*"):
            if file_path.suffix.lower() in supported_extensions:
                books.append(file_path.name)
        return books

    def list_file_tree(self) -> List[Dict[str, object]]:
        """Return recursive Kindle file entries for UI browsing."""
        self.get_mount_path()

        if self._is_mtp_api_active():
            mtp_entries = self._mtp_api_call(["tree", "/"], expect_json=True)
            if isinstance(mtp_entries, list):
                return mtp_entries
            return []

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

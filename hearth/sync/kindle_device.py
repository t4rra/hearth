"""Kindle device interface for USB and libmtp-based MTP connections."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


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
    desired_sync: bool = True
    on_device: bool = True
    sync_status: str = "on_device"
    marked_for_deletion: bool = False


@dataclass
class _MTPFolderNode:
    """Flattened libmtp folder record."""

    item_id: int
    parent_id: int
    storage_id: int
    name: str
    full_path: str


@dataclass
class _MTPFileNode:
    """Flattened libmtp file record."""

    item_id: int
    parent_id: int
    storage_id: int
    name: str
    full_path: str
    size: int
    mod_time: str


class _LIBMTPDeviceEntry(ctypes.Structure):
    _fields_ = [
        ("vendor", ctypes.c_char_p),
        ("vendor_id", ctypes.c_uint16),
        ("product", ctypes.c_char_p),
        ("product_id", ctypes.c_uint16),
        ("device_flags", ctypes.c_uint32),
    ]


class _LIBMTPRawDevice(ctypes.Structure):
    _fields_ = [
        ("device_entry", _LIBMTPDeviceEntry),
        ("bus_location", ctypes.c_uint32),
        ("devnum", ctypes.c_uint8),
    ]


class _LIBMTPDevice(ctypes.Structure):
    pass


class _LIBMTPFolder(ctypes.Structure):
    pass


_LIBMTPFolderPtr = ctypes.POINTER(_LIBMTPFolder)
_LIBMTPFolder._fields_ = [
    ("folder_id", ctypes.c_uint32),
    ("parent_id", ctypes.c_uint32),
    ("storage_id", ctypes.c_uint32),
    ("name", ctypes.c_char_p),
    ("sibling", _LIBMTPFolderPtr),
    ("child", _LIBMTPFolderPtr),
]


class _LIBMTPFile(ctypes.Structure):
    pass


_LIBMTPFilePtr = ctypes.POINTER(_LIBMTPFile)
_LIBMTPFile._fields_ = [
    ("item_id", ctypes.c_uint32),
    ("parent_id", ctypes.c_uint32),
    ("storage_id", ctypes.c_uint32),
    ("filename", ctypes.c_char_p),
    ("filesize", ctypes.c_uint64),
    ("modificationdate", ctypes.c_longlong),
    ("filetype", ctypes.c_int),
    ("next", _LIBMTPFilePtr),
]


class _LibMTPBackend:
    """Persistent in-process libmtp backend."""

    def __init__(self, debug_callback):
        self._debug = debug_callback
        self._lib = self._load_library()
        self._device_ptr: Optional[ctypes.POINTER(_LIBMTPDevice)] = None
        self.ROOT_OBJECT_ID = 0xFFFFFFFF
        self._opened_at: float = 0.0
        self._clear_errorstack_fn = None
        self._filetype_candidates: Optional[List[Tuple[int, str]]] = None
        destroy_env = os.environ.get("HEARTH_MTP_DESTROY_LISTINGS", "").strip().lower()
        if destroy_env in {"1", "true", "yes", "on"}:
            self._destroy_listing_buffers = True
        elif destroy_env in {"0", "false", "no", "off"}:
            self._destroy_listing_buffers = False
        else:
            # Some macOS libmtp builds crash when freeing listing buffers.
            self._destroy_listing_buffers = platform.system() != "Darwin"

        destroy_upload_env = (
            os.environ.get("HEARTH_MTP_DESTROY_UPLOAD_DESC", "").strip().lower()
        )
        if destroy_upload_env in {"1", "true", "yes", "on"}:
            self._destroy_upload_descriptors = True
        elif destroy_upload_env in {"0", "false", "no", "off"}:
            self._destroy_upload_descriptors = False
        else:
            # Some macOS libmtp builds also crash on upload descriptor destroy.
            self._destroy_upload_descriptors = platform.system() != "Darwin"

    def _load_library(self):
        lib_path = ctypes.util.find_library("mtp")
        if not lib_path:
            return None
        try:
            lib = ctypes.CDLL(lib_path)
        except OSError:
            return None

        lib.LIBMTP_Init.argtypes = []
        lib.LIBMTP_Init.restype = None
        lib.LIBMTP_Get_Files_And_Folders.argtypes = [
            ctypes.POINTER(_LIBMTPDevice),
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        lib.LIBMTP_Get_Files_And_Folders.restype = _LIBMTPFilePtr

        lib.LIBMTP_Detect_Raw_Devices.argtypes = [
            ctypes.POINTER(ctypes.POINTER(_LIBMTPRawDevice)),
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.LIBMTP_Detect_Raw_Devices.restype = ctypes.c_int

        lib.LIBMTP_Open_Raw_Device_Uncached.argtypes = [
            ctypes.POINTER(_LIBMTPRawDevice)
        ]
        lib.LIBMTP_Open_Raw_Device_Uncached.restype = ctypes.POINTER(_LIBMTPDevice)

        lib.LIBMTP_Release_Device.argtypes = [ctypes.POINTER(_LIBMTPDevice)]
        lib.LIBMTP_Release_Device.restype = None

        lib.LIBMTP_Get_Folder_List.argtypes = [ctypes.POINTER(_LIBMTPDevice)]
        lib.LIBMTP_Get_Folder_List.restype = _LIBMTPFolderPtr

        lib.LIBMTP_destroy_folder_t.argtypes = [_LIBMTPFolderPtr]
        lib.LIBMTP_destroy_folder_t.restype = None

        lib.LIBMTP_Get_Filelisting_With_Callback.argtypes = [
            ctypes.POINTER(_LIBMTPDevice),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.LIBMTP_Get_Filelisting_With_Callback.restype = _LIBMTPFilePtr

        lib.LIBMTP_Get_Filelisting.argtypes = [ctypes.POINTER(_LIBMTPDevice)]
        lib.LIBMTP_Get_Filelisting.restype = _LIBMTPFilePtr

        lib.LIBMTP_destroy_file_t.argtypes = [_LIBMTPFilePtr]
        lib.LIBMTP_destroy_file_t.restype = None

        lib.LIBMTP_new_file_t.argtypes = []
        lib.LIBMTP_new_file_t.restype = _LIBMTPFilePtr

        lib.LIBMTP_Create_Folder.argtypes = [
            ctypes.POINTER(_LIBMTPDevice),
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        lib.LIBMTP_Create_Folder.restype = ctypes.c_uint32

        lib.LIBMTP_Send_File_From_File.argtypes = [
            ctypes.POINTER(_LIBMTPDevice),
            ctypes.c_char_p,
            _LIBMTPFilePtr,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.LIBMTP_Send_File_From_File.restype = ctypes.c_int

        lib.LIBMTP_Get_File_To_File.argtypes = [
            ctypes.POINTER(_LIBMTPDevice),
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.LIBMTP_Get_File_To_File.restype = ctypes.c_int

        lib.LIBMTP_Get_Filetype_Description.argtypes = [ctypes.c_int]
        lib.LIBMTP_Get_Filetype_Description.restype = ctypes.c_char_p

        clear_errorstack = getattr(lib, "LIBMTP_clear_errorstack", None)
        if clear_errorstack is None:
            clear_errorstack = getattr(lib, "LIBMTP_Clear_Errorstack", None)
        if clear_errorstack is not None:
            clear_errorstack.argtypes = [ctypes.POINTER(_LIBMTPDevice)]
            clear_errorstack.restype = None
        self._clear_errorstack_fn = clear_errorstack
        return lib

    def _clear_errorstack(self) -> None:
        if self._clear_errorstack_fn and self._device_ptr:
            self._clear_errorstack_fn(self._device_ptr)

    @property
    def available(self) -> bool:
        return self._lib is not None

    def ensure_connected(self) -> bool:
        if not self._lib:
            return False
        if self._device_ptr:
            return True

        self._lib.LIBMTP_Init()

        raw_devices = ctypes.POINTER(_LIBMTPRawDevice)()
        count = ctypes.c_int(0)
        rc = self._lib.LIBMTP_Detect_Raw_Devices(
            ctypes.byref(raw_devices),
            ctypes.byref(count),
        )
        if rc != 0 or count.value <= 0:
            self._debug("libmtp raw-device detect found no devices")
            return False

        chosen = None
        for idx in range(count.value):
            candidate = raw_devices[idx]
            vendor = self._decode_cstr(candidate.device_entry.vendor)
            product = self._decode_cstr(candidate.device_entry.product)
            vendor_id = int(candidate.device_entry.vendor_id)
            info = f"vendor={vendor.strip()} product={product.strip()} vid={vendor_id:#06x}"
            self._debug(f"libmtp raw device {idx}: {info}")
            if vendor_id == 0x1949 or "kindle" in f"{vendor} {product}".lower():
                chosen = candidate
                break
            if chosen is None:
                chosen = candidate

        if chosen is None:
            return False

        device_ptr = self._lib.LIBMTP_Open_Raw_Device_Uncached(ctypes.byref(chosen))
        if not device_ptr:
            self._debug("libmtp failed to open selected raw device")
            return False

        self._device_ptr = device_ptr
        self._opened_at = time.monotonic()
        self._debug("Opened persistent libmtp device handle")
        return True

    def release(self) -> None:
        if not self._device_ptr or not self._lib:
            return
        self._lib.LIBMTP_Release_Device(self._device_ptr)
        self._device_ptr = None
        self._debug("Released persistent libmtp device handle")

    def _decode_cstr(self, value: object) -> str:
        if not value:
            return ""
        if isinstance(value, int):
            ptr = ctypes.cast(value, ctypes.c_char_p)
            if not ptr or not ptr.value:
                return ""
            value = ptr.value
        if isinstance(value, ctypes.c_char_p):
            value = value.value
        if isinstance(value, str):
            return value
        if not isinstance(value, (bytes, bytearray)):
            return ""
        return value.decode("utf-8", errors="ignore")

    def _scan_filetype_candidates(self) -> List[Tuple[int, str]]:
        """Cache valid libmtp filetype descriptions for upload selection."""
        if self._filetype_candidates is not None:
            return self._filetype_candidates

        if not self._lib:
            self._filetype_candidates = []
            return self._filetype_candidates

        candidates: List[Tuple[int, str]] = []
        for filetype in range(0, 128):
            try:
                desc_ptr = self._lib.LIBMTP_Get_Filetype_Description(filetype)
            except (AttributeError, OSError):
                break

            desc = self._decode_cstr(desc_ptr).strip().lower()
            if desc:
                candidates.append((filetype, desc))

        self._filetype_candidates = candidates
        return candidates

    def _resolve_filetype(self, terms: List[str]) -> Optional[int]:
        """Resolve a libmtp filetype id by matching description keywords."""
        candidates = self._scan_filetype_candidates()
        for term in terms:
            for filetype, desc in candidates:
                if "folder" in desc:
                    continue
                if term in desc:
                    return filetype
        return None

    def _pick_upload_filetype(self, local_file: Path) -> int:
        """Pick a non-folder libmtp filetype for uploaded files."""
        suffix = local_file.suffix.lower()
        preferred_terms = {
            ".mobi": ["mobi", "ebook", "book", "document", "unknown"],
            ".azw": ["azw", "ebook", "book", "document", "unknown"],
            ".azw3": ["azw", "ebook", "book", "document", "unknown"],
            ".kfx": ["kfx", "ebook", "book", "document", "unknown"],
            ".epub": ["epub", "ebook", "book", "document", "unknown"],
            ".pdf": ["pdf", "document", "unknown"],
            ".txt": ["text", "txt", "document", "unknown"],
        }

        terms = preferred_terms.get(
            suffix,
            ["unknown", "binary", "application", "document"],
        )
        resolved = self._resolve_filetype(terms)
        if resolved is not None:
            return resolved

        # Fallback: first non-folder descriptor, or zero if unavailable.
        for filetype, desc in self._scan_filetype_candidates():
            if "folder" not in desc:
                return filetype
        return 0

    def _read_folder_map(self) -> Dict[int, _MTPFolderNode]:
        folders: Dict[int, _MTPFolderNode] = {}
        if not self.ensure_connected() or not self._lib or not self._device_ptr:
            return folders

        root_ptr = self._lib.LIBMTP_Get_Folder_List(self._device_ptr)
        if not root_ptr:
            return folders

        def walk(node_ptr, parent_path: str) -> None:
            current = node_ptr
            while current:
                node = current.contents
                name = self._decode_cstr(node.name).strip()
                if not name:
                    name = str(node.folder_id)
                full_path = f"{parent_path}/{name}" if parent_path else f"/{name}"
                folders[int(node.folder_id)] = _MTPFolderNode(
                    item_id=int(node.folder_id),
                    parent_id=int(node.parent_id),
                    storage_id=int(node.storage_id),
                    name=name,
                    full_path=full_path,
                )
                if node.child:
                    walk(node.child, full_path)
                current = node.sibling

        walk(root_ptr, "")
        if self._destroy_listing_buffers:
            self._lib.LIBMTP_destroy_folder_t(root_ptr)
        return folders

    def _read_files(
        self, folders: Dict[int, _MTPFolderNode]
    ) -> Dict[int, _MTPFileNode]:
        files: Dict[int, _MTPFileNode] = {}
        if not self.ensure_connected() or not self._lib or not self._device_ptr:
            return files

        file_ptr = self._lib.LIBMTP_Get_Filelisting_With_Callback(
            self._device_ptr,
            None,
            None,
        )
        if not file_ptr:
            self._debug("libmtp returned empty file listing pointer")
            return files

        current = file_ptr
        while current:
            item = current.contents
            file_id = int(item.item_id)
            parent_id = int(item.parent_id)
            folder_path = folders.get(parent_id)
            filename = self._decode_cstr(item.filename).strip()
            if not filename:
                filename = str(file_id)
            base_path = folder_path.full_path if folder_path else ""
            full_path = f"{base_path}/{filename}" if base_path else f"/{filename}"

            mod_time = ""
            if int(item.modificationdate) > 0:
                try:
                    mod_time = datetime.fromtimestamp(
                        int(item.modificationdate)
                    ).isoformat()
                except (OSError, ValueError):
                    mod_time = ""

            files[file_id] = _MTPFileNode(
                item_id=file_id,
                parent_id=parent_id,
                storage_id=int(item.storage_id),
                name=filename,
                full_path=full_path,
                size=int(item.filesize),
                mod_time=mod_time,
            )
            current = item.next

        if self._destroy_listing_buffers:
            self._lib.LIBMTP_destroy_file_t(file_ptr)
        self._debug(f"libmtp file listing count={len(files)}")
        return files

    def _snapshot_via_files_and_folders(
        self,
    ) -> Tuple[Dict[int, _MTPFolderNode], Dict[int, _MTPFileNode]]:
        folders: Dict[int, _MTPFolderNode] = {}
        files: Dict[int, _MTPFileNode] = {}
        if not self.ensure_connected() or not self._lib or not self._device_ptr:
            return folders, files

        visited: set[Tuple[int, int]] = set()

        def is_folder_type(filetype: int) -> bool:
            desc_ptr = self._lib.LIBMTP_Get_Filetype_Description(filetype)
            desc = self._decode_cstr(desc_ptr)
            return "folder" in desc.lower()

        def walk(storage_id: int, parent_id: int, parent_path: str) -> None:
            key = (storage_id, parent_id)
            if key in visited:
                return
            visited.add(key)

            listing = self._lib.LIBMTP_Get_Files_And_Folders(
                self._device_ptr,
                storage_id,
                parent_id,
            )
            if not listing:
                return

            try:
                current = listing
                while current:
                    item = current.contents
                    item_id = int(item.item_id)
                    item_parent_id = int(item.parent_id)
                    item_storage_id = int(item.storage_id)
                    name = self._decode_cstr(item.filename).strip()
                    if not name:
                        name = str(item_id)
                    full_path = f"{parent_path}/{name}" if parent_path else f"/{name}"

                    if is_folder_type(int(item.filetype)):
                        folders[item_id] = _MTPFolderNode(
                            item_id=item_id,
                            parent_id=item_parent_id,
                            storage_id=item_storage_id,
                            name=name,
                            full_path=full_path,
                        )
                        walk(item_storage_id, item_id, full_path)
                    else:
                        mod_time = ""
                        if int(item.modificationdate) > 0:
                            try:
                                mod_time = datetime.fromtimestamp(
                                    int(item.modificationdate)
                                ).isoformat()
                            except (OSError, ValueError):
                                mod_time = ""

                        files[item_id] = _MTPFileNode(
                            item_id=item_id,
                            parent_id=item_parent_id,
                            storage_id=item_storage_id,
                            name=name,
                            full_path=full_path,
                            size=int(item.filesize),
                            mod_time=mod_time,
                        )

                    current = item.next
            finally:
                if self._destroy_listing_buffers:
                    self._lib.LIBMTP_destroy_file_t(listing)

        walk(0, self.ROOT_OBJECT_ID, "")
        if not folders and not files:
            walk(0x00010001, self.ROOT_OBJECT_ID, "")

        self._debug(
            "libmtp files+folders fallback "
            f"folders={len(folders)} files={len(files)}"
        )
        return folders, files

    def snapshot(self) -> Tuple[Dict[int, _MTPFolderNode], Dict[int, _MTPFileNode]]:
        # Prefer the recursive files-and-folders API because some libmtp builds
        # return unstable pointers for folder/file-list APIs on macOS.
        return self._snapshot_via_files_and_folders()

    def ensure_folder_path(self, remote_path: str) -> Optional[_MTPFolderNode]:
        if not self.ensure_connected() or not self._lib or not self._device_ptr:
            return None

        parts = [part for part in remote_path.strip("/").split("/") if part]
        if not parts:
            return None

        folders, _ = self.snapshot()
        if not folders:
            return None

        current_parent = 0
        current_storage = 0
        current_node: Optional[_MTPFolderNode] = None

        for index, part in enumerate(parts):
            match = None
            for folder in folders.values():
                if (
                    folder.parent_id == current_parent
                    and folder.name.lower() == part.lower()
                ):
                    match = folder
                    break

            if match:
                current_node = match
                current_parent = match.item_id
                current_storage = match.storage_id
                continue

            if index == 0:
                return None

            new_id = int(
                self._lib.LIBMTP_Create_Folder(
                    self._device_ptr,
                    part.encode("utf-8"),
                    current_parent,
                    current_storage,
                )
            )
            if new_id <= 0:
                return None

            folders, _ = self.snapshot()
            created = folders.get(new_id)
            if not created:
                return None
            current_node = created
            current_parent = created.item_id
            current_storage = created.storage_id

        return current_node

    def send_file(self, local_file: Path, remote_dir: str) -> bool:
        if not self.ensure_connected() or not self._lib or not self._device_ptr:
            return False

        folder = self.ensure_folder_path(remote_dir)
        if not folder:
            return False

        file_desc = self._lib.LIBMTP_new_file_t()
        if not file_desc:
            return False

        try:
            upload_type = self._pick_upload_filetype(local_file)
            file_desc.contents.filename = local_file.name.encode("utf-8")
            file_desc.contents.filesize = local_file.stat().st_size
            file_desc.contents.parent_id = folder.item_id
            file_desc.contents.storage_id = folder.storage_id
            file_desc.contents.filetype = upload_type

            rc = self._lib.LIBMTP_Send_File_From_File(
                self._device_ptr,
                str(local_file).encode("utf-8"),
                file_desc,
                None,
                None,
            )
            if rc != 0:
                self._clear_errorstack()
                return False
            return True
        finally:
            if self._destroy_upload_descriptors:
                self._lib.LIBMTP_destroy_file_t(file_desc)

    def get_file(self, remote_file: str, local_file: Path) -> bool:
        if not self.ensure_connected() or not self._lib or not self._device_ptr:
            return False

        folders, files = self.snapshot()
        _ = folders
        target = "/" + remote_file.strip().strip("/").replace("\\", "/")
        target = target.lower()
        for file_node in files.values():
            if file_node.full_path.lower() == target:
                rc = self._lib.LIBMTP_Get_File_To_File(
                    self._device_ptr,
                    ctypes.c_uint32(file_node.item_id),
                    str(local_file).encode("utf-8"),
                    None,
                    None,
                )
                if rc != 0:
                    self._clear_errorstack()
                    return False
                return True
        return False

    def list_tree(self) -> List[Dict[str, object]]:
        folders, files = self.snapshot()
        entries: List[Dict[str, object]] = []

        for folder in folders.values():
            entries.append(
                {
                    "full_path": folder.full_path,
                    "name": folder.name,
                    "is_dir": True,
                    "size": 0,
                    "mod_time": "",
                }
            )

        for file_node in files.values():
            entries.append(
                {
                    "full_path": file_node.full_path,
                    "name": file_node.name,
                    "is_dir": False,
                    "size": file_node.size,
                    "mod_time": file_node.mod_time,
                }
            )

        return sorted(entries, key=lambda row: str(row.get("full_path", "")).lower())


class KindleDevice:
    """Interface for accessing Kindle over libmtp tools or USB filesystem."""

    KINDLE_DOCS_DIR = "documents"
    HEARTH_FOLDER = "Hearth"
    KINDLE_METADATA_FILE = ".hearth_metadata.json"
    _SHARED_MTP_BACKEND: Optional[_LibMTPBackend] = None

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
        self._mtp_backend: Optional[_LibMTPBackend] = None
        self._mtp_last_probe_at: float = 0.0
        self._mtp_last_probe_ok: bool = False
        self._mtp_probe_interval_sec: float = 5.0
        self._mtp_last_seen_at: float = 0.0
        self._mtp_hold_connected_sec: float = 20.0
        self._debug_enabled = os.environ.get(
            "HEARTH_MTP_DEBUG", "1"
        ).strip().lower() not in {"0", "false", "off", "no"}

    def _debug(self, message: str) -> None:
        """Print MTP debug logs to terminal stderr."""
        if not self._debug_enabled:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(
            f"[Hearth MTP {timestamp}] {message}",
            file=sys.stderr,
            flush=True,
        )

    def __del__(self) -> None:
        """Avoid teardown-driven disconnects; use explicit close when needed."""
        return

    def close(self) -> None:
        """Explicitly release persistent libmtp handle if needed."""
        backend = self._get_mtp_backend()
        if not backend:
            return
        backend.release()
        KindleDevice._SHARED_MTP_BACKEND = None
        self._mtp_backend = None

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
            backend = self._get_mtp_backend()
            if not backend:
                return False
            for remote_path in self._mtp_hearth_candidates():
                if backend.ensure_folder_path(remote_path):
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
            backend = self._get_mtp_backend()
            if not backend:
                return False
            if not self.ensure_hearth_folder_exists():
                return False
            for remote_dir in self._mtp_hearth_candidates():
                if backend.send_file(file_path, remote_dir):
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

    def delete_file_from_kindle(self, remote_filename: str) -> bool:
        """Delete a file from Kindle Hearth folder."""
        hearth_dir = self.get_hearth_dir()
        if not hearth_dir:
            return False

        file_path = hearth_dir / remote_filename
        if not file_path.exists():
            return False

        try:
            file_path.unlink()
            return True
        except OSError:
            return False

    def load_metadata(self) -> Dict[str, KindleMetadata]:
        """Load Hearth metadata from Kindle."""
        if self._detect_mtp_kindle():
            backend = self._get_mtp_backend()
            if not backend:
                return {}
            for remote_hearth in self._mtp_hearth_candidates():
                local_tmp_dir = Path(tempfile.mkdtemp(prefix="hearth_mtp_meta_"))
                local_file = local_tmp_dir / self.KINDLE_METADATA_FILE
                remote_file = f"{remote_hearth}/{self.KINDLE_METADATA_FILE}"
                ok = backend.get_file(remote_file, local_file)
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
                "desired_sync": meta.desired_sync,
                "on_device": meta.on_device,
                "sync_status": meta.sync_status,
                "marked_for_deletion": meta.marked_for_deletion,
            }
            for key, meta in metadata_dict.items()
        }

        if self._detect_mtp_kindle():
            backend = self._get_mtp_backend()
            if not backend:
                return False
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
                if backend.send_file(local_file, remote_dir):
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
        now = time.monotonic()

        if (
            self._transport == "mtp-libmtp"
            and (now - self._mtp_last_seen_at) < self._mtp_hold_connected_sec
        ):
            return True

        if (now - self._mtp_last_probe_at) < self._mtp_probe_interval_sec:
            if self._mtp_last_probe_ok:
                self._mtp_last_seen_at = now
                self._transport = "mtp-libmtp"
            return self._mtp_last_probe_ok

        self._mtp_last_probe_at = now

        if not self._ensure_mtp_tools_available():
            self._mtp_last_probe_ok = False
            return False

        backend = self._get_mtp_backend()
        if not backend:
            self._mtp_last_probe_ok = False
            return False

        if backend.ensure_connected():
            self._transport = "mtp-libmtp"
            self._mtp_last_probe_ok = True
            self._mtp_last_seen_at = now
            return True

        usb_snapshot = self._read_usb_snapshot()
        if not self._contains_kindle_signature(usb_snapshot):
            if (
                self._transport == "mtp-libmtp"
                and (now - self._mtp_last_seen_at) < self._mtp_hold_connected_sec
            ):
                self._debug("Keeping MTP connection sticky after transient miss")
                self._mtp_last_probe_ok = True
                return True

            self._debug("USB snapshot did not match Kindle signature")
            self._mtp_last_probe_ok = False
            self._transport = "none"
            return False

        self._debug("Detected Kindle USB signature, but libmtp session is unavailable")
        self._mtp_last_probe_ok = False
        self._transport = "none"
        return False

    def _read_usb_snapshot(self) -> str:
        """Read non-invasive USB details for Kindle signature matching."""
        commands = []
        if platform.system() == "Darwin":
            commands = [
                ["ioreg", "-p", "IOUSB", "-w0", "-l"],
                ["system_profiler", "SPUSBDataType"],
            ]
        elif platform.system() == "Linux":
            commands = [["lsusb"]]
        else:
            commands = []

        for command in commands:
            result = self._run_command(command, timeout=20)
            if not result:
                continue
            combined = f"{result.stdout}\n{result.stderr}"
            if combined.strip():
                return combined
        return ""

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
        """Ensure libmtp shared library is available."""
        if self._mtp_tools_available is not None:
            return self._mtp_tools_available

        backend = self._get_mtp_backend()
        if backend and backend.available:
            self._mtp_tools_available = True
            self._debug("libmtp shared library loaded")
            return True

        if not self.auto_install_mtp_backend:
            self._mtp_tools_available = False
            self._debug("libmtp missing and auto-install is disabled")
            return False

        if platform.system() != "Darwin":
            self._mtp_tools_available = False
            self._debug("libmtp auto-install supported only on macOS")
            return False

        if self._mtp_install_attempted:
            self._mtp_tools_available = False
            self._debug("libmtp auto-install already attempted")
            return False

        self._mtp_install_attempted = True
        if not shutil.which("brew"):
            self._mtp_tools_available = False
            self._debug("Homebrew not found; cannot auto-install libmtp")
            return False

        install = self._run_command(
            ["brew", "install", "libmtp"],
            timeout=1800,
        )
        if not install or install.returncode != 0:
            self._mtp_tools_available = False
            self._debug("brew install libmtp failed")
            return False

        self._mtp_backend = _LibMTPBackend(self._debug)
        self._mtp_tools_available = bool(
            self._mtp_backend and self._mtp_backend.available
        )
        if self._mtp_tools_available:
            self._debug("libmtp installed and loaded successfully")
        else:
            self._debug("libmtp install completed but library still unavailable")
        return bool(self._mtp_tools_available)

    def _get_mtp_backend(self) -> Optional[_LibMTPBackend]:
        """Lazy-load persistent libmtp backend."""
        if KindleDevice._SHARED_MTP_BACKEND is None:
            KindleDevice._SHARED_MTP_BACKEND = _LibMTPBackend(self._debug)

        self._mtp_backend = KindleDevice._SHARED_MTP_BACKEND
        return self._mtp_backend if self._mtp_backend.available else None

    def _run_command(
        self,
        command: List[str],
        timeout: int,
    ) -> Optional[subprocess.CompletedProcess[str]]:
        """Run a subprocess command safely and return result."""
        self._debug(f"Running command: {' '.join(command)}")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            if stdout:
                self._debug(f"stdout: {stdout[:1200]}")
            if stderr:
                self._debug(f"stderr: {stderr[:1200]}")
            self._debug(f"exit={result.returncode}")
            return result
        except (OSError, subprocess.SubprocessError):
            self._debug("command failed to execute")
            return None

    def _mtp_hearth_candidates(self) -> List[str]:
        """Return likely remote Hearth folder paths for Kindle MTP."""
        return [
            "/documents/Hearth",
            "/Documents/Hearth",
        ]

    def _list_file_tree_from_mtp_filetree(self) -> List[Dict[str, object]]:
        """List file tree using persistent libmtp session."""
        backend = self._get_mtp_backend()
        if not backend or not backend.ensure_connected():
            return []
        return backend.list_tree()

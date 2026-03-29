from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import ClassVar

from .mtp_backend import LibmtpCLIBackend, MTPBackendError


@dataclass(slots=True)
class DeviceFile:
    name: str
    path: str
    size: int
    is_dir: bool = False
    remote_id: int | None = None


@dataclass(slots=True)
class KindleDevice:
    transport: str
    root: Path

    _mtp_backend: ClassVar[LibmtpCLIBackend | None] = None

    @classmethod
    def mtp_backend(cls) -> LibmtpCLIBackend:
        if cls._mtp_backend is None:
            cls._mtp_backend = LibmtpCLIBackend()
        return cls._mtp_backend

    @staticmethod
    def _looks_like_kindle_root(path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return False

        name = path.name.lower()
        if "kindle" in name:
            return True

        documents = path / "documents"
        if not documents.exists() or not documents.is_dir():
            return False

        # Common Kindle markers for USB-mounted roots.
        markers = [
            "system",
            "audible",
            "active-content-data",
        ]
        return any((path / marker).exists() for marker in markers)

    @classmethod
    def detect(
        cls,
        preferred: str = "auto",
        root_hint: str = "",
    ) -> "KindleDevice | None":
        if root_hint:
            candidate = Path(root_hint)
            if cls._looks_like_kindle_root(candidate):
                transport = preferred if preferred != "auto" else "usb"
                return cls(transport=transport, root=candidate)
            return None

        if preferred in {"auto", "usb"}:
            volumes = Path("/Volumes")
            if volumes.exists():
                for child in volumes.iterdir():
                    if cls._looks_like_kindle_root(child):
                        return cls(transport="usb", root=child)

        # MTP requires backend-specific APIs and is not a mounted filesystem.
        if preferred in {"auto", "mtp"}:
            backend = cls.mtp_backend()
            if not backend.available():
                return None
            if not backend.detect_device():
                return None
            return cls(transport="mtp", root=Path("/mtp/kindle"))

        return None

    @classmethod
    def probe(
        cls,
        preferred: str = "auto",
        root_hint: str = "",
    ) -> "KindleDevice":
        detected = cls.detect(preferred=preferred, root_hint=root_hint)
        if detected is not None:
            return detected

        if root_hint:
            transport = preferred if preferred != "auto" else "usb"
            return cls(transport=transport, root=Path(root_hint))

        if preferred == "mtp":
            backend = cls.mtp_backend()
            if not backend.available():
                raise RuntimeError("MTP backend is unavailable; install Go and libusb")
            if not backend.detect_device():
                raise RuntimeError("No MTP device detected")
            return cls(transport="mtp", root=Path("/mtp/kindle"))

        return cls(transport="usb", root=Path("/tmp/hearth-usb-placeholder"))

    @property
    def documents_dir(self) -> Path:
        if self.transport == "mtp":
            # Synthetic path for metadata fallbacks and UI text only.
            return self.root / "documents"
        return self.root / "documents"

    def ensure_layout(self) -> None:
        if self.transport == "mtp":
            return
        self.documents_dir.mkdir(parents=True, exist_ok=True)

    def hearth_dir_candidates(self) -> list[Path]:
        # Retry-friendly candidate list used by sync metadata operations.
        return [
            self.documents_dir / "Hearth",
            self.root / "Hearth",
            self.documents_dir,
        ]

    def put_file(self, local_path: Path, remote_name: str) -> Path:
        if self.transport == "mtp":
            try:
                self.mtp_backend().upload_file(local_path, remote_name)
            except MTPBackendError as exc:
                raise RuntimeError(str(exc)) from exc
            return self.root / remote_name

        self.ensure_layout()
        remote_path = self.documents_dir / remote_name
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, remote_path)
        return remote_path

    def delete_file(self, remote_name: str) -> bool:
        if self.transport == "mtp":
            try:
                return self.mtp_backend().delete_file_by_name(remote_name)
            except MTPBackendError as exc:
                raise RuntimeError(str(exc)) from exc

        path = self.documents_dir / remote_name
        if not path.exists():
            return False
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True

    def list_files(self) -> list[DeviceFile]:
        if self.transport == "mtp":
            try:
                remote = self.mtp_backend().list_files()
            except MTPBackendError as exc:
                raise RuntimeError(str(exc)) from exc

            return [
                DeviceFile(
                    name=item.name,
                    path=item.path,
                    size=item.size,
                    is_dir=item.is_dir,
                    remote_id=item.file_id,
                )
                for item in remote
            ]

        self.ensure_layout()
        rows: list[DeviceFile] = []
        for path in self.documents_dir.rglob("*"):
            relative = path.relative_to(self.documents_dir).as_posix()
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            rows.append(
                DeviceFile(
                    name=path.name,
                    path=relative,
                    size=size,
                    is_dir=path.is_dir(),
                )
            )
        return rows

    def download_file(self, remote_name: str, destination: Path) -> Path:
        if self.transport == "mtp":
            try:
                return self.mtp_backend().download_file_by_name(
                    remote_name,
                    destination,
                )
            except MTPBackendError as exc:
                raise RuntimeError(str(exc)) from exc

        source = self.documents_dir / remote_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

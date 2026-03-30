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

    @staticmethod
    def _matches_sdr_stem(folder_name: str, stem: str) -> bool:
        if not folder_name.lower().endswith(".sdr"):
            return False

        folder_stem = folder_name[:-4]
        folder_lower = folder_stem.lower()
        stem_lower = stem.lower()
        if folder_lower == stem_lower:
            return True
        if not folder_lower.startswith(stem_lower):
            return False

        remainder = folder_stem[len(stem) :]
        if not remainder:
            return True
        stripped_remainder = remainder.lstrip()
        if not stripped_remainder:
            return True
        return stripped_remainder[0] in {"-", "_", "(", "[", "{"}

    @staticmethod
    def _sdr_stem_for_remote_name(remote_name: str) -> tuple[str, str]:
        normalized = remote_name.strip("/")
        path = Path(normalized)
        parent = "" if str(path.parent) == "." else path.parent.as_posix()
        stem = path.stem if path.suffix else path.name
        return parent, stem

    def _find_usb_sdr_companions(self, remote_name: str) -> list[Path]:
        parent_rel, stem = self._sdr_stem_for_remote_name(remote_name)
        if parent_rel:
            parent = self.documents_dir / parent_rel
        else:
            parent = self.documents_dir
        if not parent.exists() or not parent.is_dir():
            return []

        companions: list[Path] = []
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            if self._matches_sdr_stem(child.name, stem):
                companions.append(child)
        return companions

    def _find_mtp_sdr_companions(self, remote_name: str) -> list[str]:
        parent_rel, stem = self._sdr_stem_for_remote_name(remote_name)
        matches: list[str] = []
        for entry in self.mtp_backend().list_files():
            if not entry.is_dir:
                continue
            full = entry.path.strip("/")
            if not full.startswith("documents/"):
                continue
            rel = full.removeprefix("documents/")
            rel_path = Path(rel)
            if str(rel_path.parent) == ".":
                rel_parent = ""
            else:
                rel_parent = rel_path.parent.as_posix()
            if rel_parent != parent_rel:
                continue
            if self._matches_sdr_stem(rel_path.name, stem):
                matches.append(rel_path.as_posix())
        return matches

    def delete_file(self, remote_name: str) -> bool:
        if self.transport == "mtp":
            try:
                backend = self.mtp_backend()
                deleted = backend.delete_file_by_name(remote_name)
                deleted_sdr = False
                mtp_companions = self._find_mtp_sdr_companions(remote_name)
                for mtp_companion in mtp_companions:
                    deleted_sdr = (
                        backend.delete_file_by_name(mtp_companion) or deleted_sdr
                    )
                return deleted or deleted_sdr
            except MTPBackendError as exc:
                raise RuntimeError(str(exc)) from exc

        path = self.documents_dir / remote_name
        deleted = False
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted = True

        deleted_sdr = False
        for usb_companion in self._find_usb_sdr_companions(remote_name):
            shutil.rmtree(usb_companion)
            deleted_sdr = True
        return deleted or deleted_sdr

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

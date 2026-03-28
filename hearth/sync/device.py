from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil


@dataclass(slots=True)
class KindleDevice:
    transport: str
    root: Path

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

        # MTP path is intentionally conservative. Actual MTP backend wiring
        # should replace this placeholder transport root.
        if preferred == "mtp":
            return cls(
                transport="mtp",
                root=Path("/tmp/hearth-mtp-placeholder"),
            )

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
            return cls(
                transport="mtp",
                root=Path("/tmp/hearth-mtp-placeholder"),
            )
        return cls(transport="usb", root=Path("/tmp/hearth-usb-placeholder"))

    @property
    def documents_dir(self) -> Path:
        return self.root / "documents"

    def ensure_layout(self) -> None:
        self.documents_dir.mkdir(parents=True, exist_ok=True)

    def hearth_dir_candidates(self) -> list[Path]:
        # Retry-friendly candidate list used by sync metadata operations.
        return [
            self.documents_dir / "Hearth",
            self.root / "Hearth",
            self.documents_dir,
        ]

    def put_file(self, local_path: Path, remote_name: str) -> Path:
        self.ensure_layout()
        remote_path = self.documents_dir / remote_name
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, remote_path)
        return remote_path

    def delete_file(self, remote_name: str) -> bool:
        path = self.documents_dir / remote_name
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_files(self) -> list[Path]:
        self.ensure_layout()
        return [p for p in self.documents_dir.iterdir() if p.is_file()]

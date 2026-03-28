from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil


@dataclass(slots=True)
class KindleDevice:
    transport: str
    root: Path

    @classmethod
    def probe(
        cls,
        preferred: str = "auto",
        root_hint: str = "",
    ) -> "KindleDevice":
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

from __future__ import annotations

from pathlib import Path
import shutil


class CalibreConverter:
    name = "calibre"

    def __init__(self, command: str = ""):
        self.command = command

    def discover_command(self) -> str | None:
        if self.command:
            return self.command

        direct = shutil.which("ebook-convert")
        if direct:
            return direct

        mac_path = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
        return mac_path if Path(mac_path).exists() else None

    def available(self) -> bool:
        return self.discover_command() is not None

    def convert(self, source: Path, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self.available():
            raise RuntimeError("Calibre ebook-convert is not available")
        # Placeholder to keep conversion boundary isolated in tests.
        target.write_bytes(source.read_bytes())
        return target

from __future__ import annotations

from pathlib import Path
import shutil


class KCCConverter:
    name = "kcc"

    def __init__(self, command: str = ""):
        self.command = command

    def discover_command(self) -> str | None:
        if self.command:
            return self.command
        candidates = ["kcc-c2e", "kcc", "comic2ebook"]
        for candidate in candidates:
            found = shutil.which(candidate)
            if found:
                return found
        return None

    def available(self) -> bool:
        return self.discover_command() is not None

    def diagnostics(self) -> dict[str, str | bool]:
        cmd = self.discover_command()
        seven_zip = shutil.which("7zz") or shutil.which("7z")
        return {
            "command": cmd or "",
            "command_available": bool(cmd),
            "archive_tool": seven_zip or "",
            "archive_tool_available": bool(seven_zip),
        }

    def convert(self, source: Path, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self.available():
            raise RuntimeError("KCC command not available")
        # Placeholder: preserve payload while swapping extension.
        target.write_bytes(source.read_bytes())
        return target

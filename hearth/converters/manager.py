from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .base import ConversionResult
from .calibre import CalibreConverter
from .detection import COMIC_EXTENSIONS, infer_extension
from .kcc import KCCConverter


@dataclass(slots=True)
class ConverterManager:
    kcc: KCCConverter
    calibre: CalibreConverter

    @classmethod
    def from_commands(
        cls,
        kcc_command: str = "",
        kcc_device: str = "auto",
        kcc_manga_default: bool = False,
        kcc_manga_force: bool = False,
        kcc_autolevel: bool = True,
        calibre_command: str = "",
    ) -> "ConverterManager":
        return cls(
            kcc=KCCConverter(
                kcc_command,
                device=kcc_device,
                manga_default=kcc_manga_default,
                manga_force=kcc_manga_force,
                autolevel=kcc_autolevel,
            ),
            calibre=CalibreConverter(calibre_command),
        )

    def convert_for_kindle(
        self,
        source: Path,
        destination_dir: Path,
        stem: str,
        title: str = "",
        author: str = "",
        kcc_device_hint: str = "",
        progress_callback: Callable[[float | None, str], None] | None = None,
        declared_type: str = "",
    ) -> ConversionResult:
        ext = infer_extension(source, declared_type=declared_type)
        destination_dir.mkdir(parents=True, exist_ok=True)

        if ext in COMIC_EXTENSIONS:
            # Prefer KCC for comic workflows to match device-profile output.
            if self.kcc.available():
                output = destination_dir / f"{stem}.mobi"
                converted = self.kcc.convert(
                    source,
                    output,
                    title=title,
                    author=author,
                    device_hint=kcc_device_hint,
                    progress_callback=progress_callback,
                )
                return ConversionResult(
                    backend=self.kcc.name,
                    output=converted,
                )

            raise RuntimeError(
                "Comic conversion requires Kindle Comic Converter CLI "
                "(kcc-c2e). Hearth can auto-bootstrap from the KCC repo, "
                "but that requires git/network access; otherwise set the "
                "KCC command in Settings."
            )

        if ext in {".epub", ".zip", ".pdf"}:
            if not self.calibre.available():
                raise RuntimeError(
                    "Calibre ebook-convert is required "
                    "for EPUB/ZIP/PDF conversion"
                )
            output = destination_dir / f"{stem}.mobi"
            converted = self.calibre.convert(
                source,
                output,
                title=title,
                author=author,
                progress_callback=progress_callback,
            )
            return ConversionResult(
                backend=self.calibre.name,
                output=converted,
            )

        passthrough = destination_dir / f"{stem}{ext}"
        passthrough.write_bytes(source.read_bytes())
        return ConversionResult(backend="passthrough", output=passthrough)

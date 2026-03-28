from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .base import ConversionResult
from .calibre import CalibreConverter
from .detection import infer_extension
from .kcc import KCCConverter


@dataclass(slots=True)
class ConverterManager:
    kcc: KCCConverter
    calibre: CalibreConverter

    @classmethod
    def from_commands(
        cls,
        kcc_command: str = "",
        calibre_command: str = "",
    ) -> "ConverterManager":
        return cls(
            kcc=KCCConverter(kcc_command),
            calibre=CalibreConverter(calibre_command),
        )

    def convert_for_kindle(
        self,
        source: Path,
        destination_dir: Path,
        stem: str,
        declared_type: str = "",
    ) -> ConversionResult:
        ext = infer_extension(source, declared_type=declared_type)
        destination_dir.mkdir(parents=True, exist_ok=True)

        if ext in {".cbz", ".cbr"}:
            output = destination_dir / f"{stem}.epub"
            converted = self.kcc.convert(source, output)
            return ConversionResult(backend=self.kcc.name, output=converted)

        if ext in {".epub", ".zip", ".pdf"}:
            output = destination_dir / f"{stem}.epub"
            converted = self.calibre.convert(source, output)
            return ConversionResult(
                backend=self.calibre.name,
                output=converted,
            )

        passthrough = destination_dir / f"{stem}{ext}"
        passthrough.write_bytes(source.read_bytes())
        return ConversionResult(backend="passthrough", output=passthrough)

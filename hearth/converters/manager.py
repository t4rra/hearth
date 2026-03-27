"""Converter manager for handling multiple format conversions."""

from pathlib import Path
from typing import Optional, Callable

from .base import ConversionFormat, ConversionResult
from .kcc import KCCConverter
from .calibre import CalibreConverter


class ConverterManager:
    """Manages multiple converters and handles format conversion."""

    def __init__(self, output_dir: Optional[Path] = None, keep_originals: bool = True):
        self.output_dir = output_dir or Path.cwd()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.keep_originals = keep_originals

        # Initialize converters
        self.comic_converter = KCCConverter(
            output_dir=self.output_dir, keep_original=keep_originals
        )
        self.ebook_converter = CalibreConverter(
            output_dir=self.output_dir, keep_original=keep_originals
        )

    def can_convert(self, input_path: Path) -> bool:
        """Check if any converter can handle this file."""
        return self.comic_converter.can_convert(
            input_path
        ) or self.ebook_converter.can_convert(input_path)

    def convert(
        self,
        input_path: Path,
        output_format: ConversionFormat = (ConversionFormat.MOBI),
    ) -> ConversionResult:
        """Convert file to specified format using appropriate converter."""
        if self.comic_converter.can_convert(input_path):
            return self.comic_converter.convert(input_path, output_format)
        elif self.ebook_converter.can_convert(input_path):
            return self.ebook_converter.convert(input_path, output_format)
        else:
            return ConversionResult(
                False, error=f"No converter available for {input_path.suffix}"
            )

    def set_progress_callback(self, callback: Callable[[str], None]) -> None:
        """Set progress callback for all converters."""
        self.comic_converter.set_progress_callback(callback)
        self.ebook_converter.set_progress_callback(callback)

    def get_supported_formats(self) -> list[str]:
        """Get all supported input formats."""
        formats = set()
        formats.update(self.comic_converter.get_supported_formats())
        formats.update(self.ebook_converter.get_supported_formats())
        return list(formats)

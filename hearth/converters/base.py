"""Base converter class and interfaces."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Callable
from enum import Enum


class ConversionFormat(Enum):
    """Supported output formats."""

    MOBI = "mobi"
    EPUB = "epub"
    AZW3 = "azw3"


class ConversionResult:
    """Result of a conversion operation."""

    def __init__(
        self,
        success: bool,
        output_path: Optional[Path] = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.output_path = output_path
        self.error = error

    def __repr__(self):
        return (
            f"ConversionResult(success={self.success}, "
            f"path={self.output_path}, error={self.error})"
        )


class BaseConverter(ABC):
    """Abstract base class for format converters."""

    def __init__(self, output_dir: Optional[Path] = None, keep_original: bool = True):
        self.output_dir = output_dir or Path.cwd()
        self.keep_original = keep_original
        self.progress_callback: Optional[Callable[[str], None]] = None

    def set_progress_callback(self, callback: Callable[[str], None]) -> None:
        """Set a callback for progress updates."""
        self.progress_callback = callback

    def _log_progress(self, message: str) -> None:
        """Log progress through callback if available."""
        if self.progress_callback:
            self.progress_callback(message)
        else:
            print(message)

    @abstractmethod
    def can_convert(self, input_path: Path) -> bool:
        """Check if this converter can handle the input file."""

    @abstractmethod
    def convert(
        self,
        input_path: Path,
        output_format: ConversionFormat = (ConversionFormat.MOBI),
    ) -> ConversionResult:
        """Convert the input file to the specified format."""

    @abstractmethod
    def get_supported_formats(self) -> list[str]:
        """Return list of supported input formats."""

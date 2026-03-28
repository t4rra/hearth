from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Converter(Protocol):
    name: str

    def available(self) -> bool: ...

    def convert(self, source: Path, target: Path) -> Path: ...


@dataclass(slots=True)
class ConversionResult:
    backend: str
    output: Path

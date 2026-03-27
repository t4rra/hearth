"""Converter manager for handling multiple format conversions."""

import zipfile
from pathlib import Path
from typing import Optional, Callable
from xml.etree import ElementTree

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
        source_metadata: Optional[dict[str, str]] = None,
    ) -> ConversionResult:
        """Convert file to specified format using appropriate converter."""
        profile = self.detect_content_profile(
            input_path,
            source_metadata=source_metadata,
        )

        is_comic = profile["is_comic"]
        is_manga_rtl = profile["is_manga_rtl"]

        # Route comics through KCC first; if KCC cannot handle the input type,
        # fallback to Calibre conversion.
        if is_comic and self.comic_converter.can_convert(input_path):
            return self.comic_converter.convert(
                input_path,
                output_format,
                manga_rtl=is_manga_rtl,
            )

        if self.ebook_converter.can_convert(input_path):
            return self.ebook_converter.convert(input_path, output_format)

        return ConversionResult(
            False, error=f"No converter available for {input_path.suffix}"
        )

    def detect_content_profile(
        self,
        input_path: Path,
        source_metadata: Optional[dict[str, str]] = None,
    ) -> dict[str, bool]:
        """Detect whether a downloaded file is comic/manga using file + metadata."""
        profile = {
            "is_comic": False,
            "is_manga_rtl": False,
        }

        suffix = input_path.suffix.lower()
        if suffix in self.comic_converter.get_supported_formats():
            profile["is_comic"] = True

        text_samples: list[str] = []
        if source_metadata:
            for key in ("title", "description", "author", "format"):
                value = source_metadata.get(key)
                if value:
                    text_samples.append(str(value))

        if suffix == ".epub":
            text_samples.extend(self._read_epub_metadata_text(input_path))
        elif suffix == ".cbz":
            comicinfo = self._read_cbz_comicinfo_text(input_path)
            if comicinfo:
                text_samples.append(comicinfo)

        full_text = "\n".join(text_samples).lower()

        comic_keywords = [
            "manga",
            "manhwa",
            "webtoon",
            "graphic novel",
            "comic book",
            "comicinfo",
            "pre-paginated",
            "prepaginated",
        ]
        manga_keywords = [
            "manga",
            "right-to-left",
            "right to left",
            "rtl",
            "readingdirection:righttoleft",
            "readingdirection=righttoleft",
            "yesandrighttoleft",
        ]

        if any(keyword in full_text for keyword in comic_keywords):
            profile["is_comic"] = True
        if any(keyword in full_text for keyword in manga_keywords):
            profile["is_comic"] = True
            profile["is_manga_rtl"] = True

        return profile

    def _read_epub_metadata_text(self, input_path: Path) -> list[str]:
        """Extract metadata text snippets from EPUB OPF/package docs."""
        chunks: list[str] = []
        try:
            with zipfile.ZipFile(input_path, "r") as archive:
                namelist = archive.namelist()
                container_candidates = [
                    "META-INF/container.xml",
                    "meta-inf/container.xml",
                ]
                opf_paths: list[str] = []

                for container_name in container_candidates:
                    if container_name not in namelist:
                        continue
                    container_data = archive.read(container_name)
                    root = ElementTree.fromstring(container_data)
                    for node in root.findall(".//{*}rootfile"):
                        opf_path = node.attrib.get("full-path")
                        if opf_path:
                            opf_paths.append(opf_path)

                if not opf_paths:
                    opf_paths = [
                        name for name in namelist if name.lower().endswith(".opf")
                    ]

                for opf_path in opf_paths[:3]:
                    if opf_path not in namelist:
                        continue
                    opf_data = archive.read(opf_path)
                    root = ElementTree.fromstring(opf_data)
                    for node in root.findall(".//{*}metadata//*"):
                        text = (node.text or "").strip()
                        if text:
                            chunks.append(text)
        except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
            return []

        return chunks

    def _read_cbz_comicinfo_text(self, input_path: Path) -> str:
        """Extract ComicInfo.xml text from CBZ when present."""
        try:
            with zipfile.ZipFile(input_path, "r") as archive:
                name = next(
                    (
                        entry
                        for entry in archive.namelist()
                        if entry.lower().endswith("comicinfo.xml")
                    ),
                    "",
                )
                if not name:
                    return ""
                data = archive.read(name)
                try:
                    root = ElementTree.fromstring(data)
                    values = []
                    for node in root.iter():
                        text = (node.text or "").strip()
                        if text:
                            values.append(f"{node.tag}:{text}")
                    return "\n".join(values)
                except ElementTree.ParseError:
                    return data.decode("utf-8", errors="ignore")
        except (OSError, zipfile.BadZipFile, StopIteration):
            return ""

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

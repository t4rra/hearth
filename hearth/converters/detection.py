from __future__ import annotations

from pathlib import Path
import zipfile


COMIC_EXTENSIONS = {".cbr", ".cbz", ".cbt", ".cba", ".cb7"}


def _looks_like_epub_archive(path: Path) -> bool:
    if not path.exists() or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = {n.lower() for n in archive.namelist()}
            return "mimetype" in names and "meta-inf/container.xml" in names
    except zipfile.BadZipFile:
        return False


def _looks_like_comic_archive(path: Path) -> bool:
    if not path.exists() or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path, "r") as archive:
            image_files = [
                n
                for n in archive.namelist()
                if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            ]
            return len(image_files) > 0
    except zipfile.BadZipFile:
        return False


def infer_extension(path: Path, declared_type: str = "") -> str:
    """Infer extension using both declared MIME and content signatures."""

    declared = declared_type.lower()
    suffix = path.suffix.lower()

    # Comic detection is extension-based.
    if suffix in COMIC_EXTENSIONS:
        return suffix

    # Trust explicit file extensions first when we have known formats.
    if suffix in {".epub", ".mobi", ".azw3"}:
        return suffix

    # Trust real content signatures before declared MIME.
    if _looks_like_epub_archive(path):
        return ".epub"
    if _looks_like_comic_archive(path):
        return ".cbz"

    if "epub" in declared:
        return ".epub"
    if "comic" in declared or "cbz" in declared:
        return ".cbz"

    with path.open("rb") as handle:
        head = handle.read(8)
    if head.startswith(b"PK"):
        return ".zip"
    return suffix or ".bin"

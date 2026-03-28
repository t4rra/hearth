"""Sync manager for coordinating OPDS to Kindle synchronization."""

import io
import re
import shutil
import subprocess
import platform
import zipfile
from pathlib import Path
from typing import Optional, Callable, List
from datetime import datetime

from ..core.config import SettingsManager
from ..core.opds_client import OPDSClient, Book, Collection
from ..converters.manager import ConverterManager
from ..converters.base import ConversionFormat
from .kindle_device import KindleDevice, KindleMetadata


class SyncManager:
    """Manages synchronization between OPDS server and Kindle."""

    def __init__(
        self,
        settings_manager: Optional[SettingsManager] = None,
        output_dir: Optional[Path] = None,
    ):
        self.settings_manager = settings_manager or SettingsManager()
        self.settings = self.settings_manager.get_settings()

        self.output_dir = (
            output_dir or Path.home() / ".cache" / "hearth" / "conversions"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.opds_client: Optional[OPDSClient] = None
        self.kindle: Optional[KindleDevice] = None
        self.converter: Optional[ConverterManager] = None

        self.progress_callback: Optional[Callable[[str], None]] = None
        self._initialize()

    def _initialize(self):
        """Initialize sync components."""
        if self.settings.opds_url:
            self.opds_client = OPDSClient(
                self.settings.opds_url,
                auth_type=self.settings.opds_auth_type,
                username=self.settings.opds_username,
                password=self.settings.opds_password,
                token=self.settings.opds_token,
            )

        if self.settings.kindle_mount_path:
            self.kindle = KindleDevice(
                Path(self.settings.kindle_mount_path),
                auto_install_mtp_backend=(self.settings.mtp_auto_install_backend),
            )
        else:
            self.kindle = KindleDevice(
                auto_mount_mtp=self.settings.mtp_auto_mount,
                preferred_mtp_tool=self.settings.mtp_mount_tool,
                auto_install_mtp_backend=(self.settings.mtp_auto_install_backend),
            )

        self.converter = ConverterManager(
            output_dir=self.output_dir,
            keep_originals=self.settings.keep_originals,
        )

        if self.progress_callback:
            self.converter.set_progress_callback(self.progress_callback)

    def set_progress_callback(self, callback: Callable[[str], None]) -> None:
        """Set callback for progress updates."""
        self.progress_callback = callback
        if self.converter:
            self.converter.set_progress_callback(callback)

    def _log(self, message: str) -> None:
        """Log a message."""
        if self.progress_callback:
            self.progress_callback(message)
        else:
            print(message)

    def is_opds_configured(self) -> bool:
        """Check if OPDS server is configured."""
        return bool(self.settings.opds_url and self.opds_client)

    def is_kindle_connected(self) -> bool:
        """Check if Kindle device is connected."""
        if not self.kindle:
            return False
        return self.kindle.is_connected()

    def get_startup_status(self) -> dict[str, object]:
        """Collect startup readiness diagnostics for sync operations."""
        opds_ok = self.is_opds_configured()
        kindle_ok = self.is_kindle_connected()

        if kindle_ok and self.kindle:
            self._reconcile_metadata_with_device_books()

        calibre_available = False
        kcc_status: dict[str, object] = {
            "ready": False,
            "issues": ["KCC converter not initialized"],
        }

        if self.converter:
            calibre_converter = getattr(self.converter, "ebook_converter", None)
            if calibre_converter is not None:
                calibre_available = bool(
                    getattr(calibre_converter, "ebook_convert_path", None)
                )

            comic_converter = getattr(self.converter, "comic_converter", None)
            if comic_converter is not None and hasattr(
                comic_converter, "get_runtime_status"
            ):
                kcc_status = comic_converter.get_runtime_status()

        return {
            "opds_configured": opds_ok,
            "kindle_connected": kindle_ok,
            "calibre_available": calibre_available,
            "kcc": kcc_status,
            "sync_ready": opds_ok and kindle_ok,
        }

    def _reconcile_metadata_with_device_books(self) -> int:
        """Align metadata with files currently present on Kindle.

        If a user manually deleted a synced file on Kindle, clear desired sync
        intent so the UI no longer marks that title as wanted.
        """
        if not self.kindle:
            return 0

        metadata = self.kindle.load_metadata()
        if not metadata:
            return 0

        try:
            raw_books = self.kindle.list_books()
            if isinstance(raw_books, list):
                device_books = raw_books
            else:
                device_books = list(raw_books)
        except (TypeError, OSError, RuntimeError, AttributeError):
            return 0

        device_book_names = {name.lower() for name in device_books if name}
        changed = 0

        for meta in metadata.values():
            if not meta.local_path:
                continue

            filename = Path(meta.local_path).name.lower()
            if not filename:
                continue

            on_device_now = filename in device_book_names
            if on_device_now:
                updated = False
                if not meta.on_device:
                    meta.on_device = True
                    updated = True
                if meta.sync_status != "on_device":
                    meta.sync_status = "on_device"
                    updated = True
                if updated:
                    changed += 1
                continue

            updated = False
            if meta.on_device:
                meta.on_device = False
                updated = True
            if meta.sync_status != "not_synced":
                meta.sync_status = "not_synced"
                updated = True
            if meta.desired_sync:
                meta.desired_sync = False
                updated = True
            if meta.marked_for_deletion:
                meta.marked_for_deletion = False
                updated = True
            if updated:
                changed += 1

        if changed:
            self.kindle.save_metadata(metadata)
            self._log(
                "Reconciled Kindle metadata: "
                f"updated {changed} entry(s) from current device files"
            )

        return changed

    def fetch_books_from_server(self) -> List[Book]:
        """Fetch all available books from OPDS server."""
        if not self.is_opds_configured():
            self._log("Error: OPDS server not configured")
            return []

        self._log("Fetching books from OPDS server...")
        if not self.opds_client:
            return []
        return self.opds_client.get_all_books()

    def fetch_collections(self) -> List[Collection]:
        """Fetch all collections from OPDS server."""
        if not self.is_opds_configured():
            self._log("Error: OPDS server not configured")
            return []

        self._log("Fetching collections from OPDS server...")
        if not self.opds_client:
            return []
        return self.opds_client.get_collections()

    def load_collection_books(self, collection: Collection) -> bool:
        """Load books for a specific collection."""
        if not self.is_opds_configured():
            self._log("Error: OPDS server not configured")
            return False

        self._log(f"Loading books from collection: {collection.title}...")
        if not self.opds_client:
            return False
        return self.opds_client.load_collection(collection)

    def download_book(
        self, book: Book, temp_dir: Optional[Path] = None
    ) -> Optional[Path]:
        """Download a book from OPDS server."""
        if not self.opds_client:
            self._log("Error: OPDS client not initialized")
            return None

        if not book.download_url:
            self._log(f"Error: No download URL for {book.title}")
            return None

        if not temp_dir:
            temp_dir = self.output_dir / "downloads"
            temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._log(f"Downloading {book.title}...")
            content = self.opds_client.download_content(book.download_url)
            if content is None:
                self._log(f"Error downloading {book.title}")
                return None

            ext = self._infer_download_extension(book, content)
            file_path = temp_dir / f"{book.id}{ext}"

            with open(file_path, "wb") as f:
                f.write(content)

            self._log(f"Downloaded {book.title} to {file_path}")
            return file_path

        except OSError as error:
            self._log(f"Error downloading {book.title}: {error}")
            return None

    def _infer_download_extension(self, book: Book, content: bytes) -> str:
        """Infer best extension so comic downloads route to comic converter."""
        comic_exts = {".cbz", ".cbr", ".cb7", ".cbt", ".cba"}

        declared: List[str] = []
        if book.download_url:
            declared.append(Path(book.download_url).suffix.lower())
        if book.format:
            fmt = str(book.format).strip().lower()
            if fmt and not fmt.startswith("."):
                fmt = f".{fmt}"
            declared.append(fmt)

        declared = [ext for ext in declared if ext]
        for ext in declared:
            if ext in comic_exts:
                return ext

        # ZIP payloads can be either EPUB or comic archives.
        if content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            if self._looks_like_epub(content):
                return ".epub"
            if self._looks_like_cbz(content):
                return ".cbz"

        if content.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")):
            return ".cbr"

        if content.startswith(b"7z\xbc\xaf\x27\x1c"):
            return ".cb7"

        if len(content) > 262 and content[257:262] == b"ustar":
            return ".cbt"

        if declared:
            return declared[0]
        return ".epub"

    def _looks_like_epub(self, content: bytes) -> bool:
        """Return True when ZIP payload has EPUB container metadata."""
        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
                names = {name.lower() for name in archive.namelist()}
                if "meta-inf/container.xml" in names:
                    return True
                return any(name.endswith(".opf") for name in names)
        except (OSError, zipfile.BadZipFile):
            return False

    def _looks_like_cbz(self, content: bytes) -> bool:
        """Return True when ZIP payload looks like a comic archive."""
        image_exts = (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".avif",
        )
        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as archive:
                names = [name.lower() for name in archive.namelist()]
                if any(name.endswith("comicinfo.xml") for name in names):
                    return True
                image_count = sum(name.endswith(image_exts) for name in names)
                return image_count >= 2
        except (OSError, zipfile.BadZipFile):
            return False

    def sync_book(
        self,
        book: Book,
        force_resync: bool = False,
        dependency_prompt_callback: Optional[Callable[[str, dict], bool]] = None,
    ) -> bool:
        """Download, convert, and sync a book to Kindle."""
        prepared_path = self.prepare_book_for_sync(
            book,
            force_resync=force_resync,
            dependency_prompt_callback=dependency_prompt_callback,
        )
        if not prepared_path:
            if not force_resync and self._is_book_already_synced_on_device(book):
                return True
            return False

        return self.push_prepared_book_to_kindle(book, prepared_path)

    def _is_book_already_synced_on_device(self, book: Book) -> bool:
        """Return True when metadata indicates the book is already present on device."""
        if not self.kindle:
            return False

        metadata = self.kindle.load_metadata()
        if book.id not in metadata:
            return False

        book_meta = metadata[book.id]
        return bool(book_meta.on_device and book_meta.sync_status == "on_device")

    def prepare_book_for_sync(
        self,
        book: Book,
        force_resync: bool = False,
        dependency_prompt_callback: Optional[Callable[[str, dict], bool]] = None,
    ) -> Optional[Path]:
        """Download and convert a book to local cache path.

        Returns converted/downloaded local file path, or None if skipped/failed.
        """
        if not self.is_kindle_connected():
            self._log("Error: Kindle device not connected")
            return None

        if not self.kindle:
            self._log("Error: Kindle device not available")
            return None

        # Check if already synced (unless force_resync is True)
        if not force_resync:
            metadata = self.kindle.load_metadata()
            if book.id in metadata:
                book_meta = metadata[book.id]
                is_on_device = (
                    book_meta.on_device and book_meta.sync_status == "on_device"
                )
                if is_on_device:
                    skip_msg = f"Skipping {book.title} (already synced)"
                    self._log(skip_msg)
                    return None

        # Download book
        downloaded_path = self.download_book(book)
        if not downloaded_path:
            return None

        # Convert if needed
        if (
            self.settings.auto_convert
            and self.converter
            and self.converter.can_convert(downloaded_path)
        ):
            profile = self.converter.detect_content_profile(
                downloaded_path,
                source_metadata={
                    "title": book.title,
                    "author": book.author,
                    "description": book.description or "",
                    "format": book.format or "",
                },
            )

            if profile.get("is_comic") and self.converter.comic_converter.can_convert(
                downloaded_path
            ):
                comic_converter = self.converter.comic_converter
                kcc_status = comic_converter.get_runtime_status()
                if not kcc_status.get("ready"):
                    approved = False
                    if dependency_prompt_callback:
                        approved = dependency_prompt_callback("kcc", kcc_status)

                    if not approved:
                        self._log(
                            f"Skipping {book.title}: KCC required for comic conversion"
                        )
                        return None

                    if not comic_converter.ensure_kcc_available(allow_bootstrap=True):
                        self._log(
                            f"Skipping {book.title}: KCC install/bootstrap failed"
                        )
                        return None

                kcc_status = comic_converter.get_runtime_status()
                if not kcc_status.get("seven_zip_available"):
                    proceed_without_7z = False
                    if dependency_prompt_callback:
                        proceed_without_7z = dependency_prompt_callback(
                            "7z",
                            kcc_status,
                        )

                    if not proceed_without_7z:
                        self._log(
                            f"Skipping {book.title}: 7z is required/expected for archives"
                        )
                        return None

            self._log(f"Converting {book.title}...")
            result = self.converter.convert(
                downloaded_path,
                ConversionFormat.MOBI,
                source_metadata={
                    "title": book.title,
                    "author": book.author,
                    "description": book.description or "",
                    "format": book.format or "",
                },
                conversion_settings=self.settings.conversion_settings,
            )

            if not result.success:
                self._log(f"Error converting {book.title}: {result.error}")
                return None

            if result.output_path is None:
                self._log(f"Error converting {book.title}: no output file")
                return None

            converted_path = result.output_path
        else:
            converted_path = downloaded_path

        self._apply_book_metadata_overrides(converted_path, book)

        self._log(f"Prepared {book.title} for sync: {converted_path}")
        return converted_path

    def _find_ebook_meta_command(self) -> Optional[str]:
        """Locate calibre's ebook-meta utility when available."""
        found = shutil.which("ebook-meta")
        if found:
            return found

        system = platform.system()
        if system == "Darwin":
            candidates = [
                "/Applications/calibre.app/Contents/MacOS/ebook-meta",
                "/opt/homebrew/bin/ebook-meta",
                "/usr/local/bin/ebook-meta",
            ]
        elif system == "Windows":
            candidates = [
                r"C:\Program Files\Calibre2\ebook-meta.exe",
                r"C:\Program Files (x86)\Calibre2\ebook-meta.exe",
            ]
        else:
            candidates = ["/opt/calibre/ebook-meta", "/usr/bin/ebook-meta"]

        for candidate in candidates:
            if Path(candidate).exists():
                return candidate

        return None

    def _apply_book_metadata_overrides(self, file_path: Path, book: Book) -> None:
        """Apply title/author metadata to converted books for Kindle display."""
        if file_path.suffix.lower() not in {".mobi", ".azw3", ".epub"}:
            return

        title = (book.title or "").strip()
        author = (book.author or "").strip()
        if not title:
            return

        ebook_meta = self._find_ebook_meta_command()
        if not ebook_meta:
            return

        cmd = [ebook_meta, str(file_path), "--title", title]
        if author:
            cmd.extend(["--authors", author])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            return

        if result.returncode != 0:
            self._log(
                "Warning: ebook-meta failed for "
                f"{file_path.name}; Kindle may show source metadata title"
            )

    def push_prepared_book_to_kindle(self, book: Book, file_path: Path) -> bool:
        """Copy a locally prepared file to Kindle and persist metadata."""
        if not self.is_kindle_connected():
            self._log("Error: Kindle device not connected")
            return False

        if not self.kindle:
            self._log("Error: Kindle device not available")
            return False

        if not self.kindle.ensure_hearth_folder_exists():
            self._log("Error: Could not create Hearth folder on Kindle")
            return False

        kindle_filename = self._build_kindle_filename(book, file_path)

        # Copy to Kindle
        if not self.kindle.copy_to_kindle(
            file_path,
            target_filename=kindle_filename,
        ):
            self._log(f"Error copying {book.title} to Kindle")
            return False

        # Update metadata
        self._update_sync_metadata(
            book,
            file_path,
            kindle_filename=kindle_filename,
        )

        self._log(f"Successfully synced {book.title} to Kindle")
        return True

    def _sanitize_filename_segment(self, value: str) -> str:
        """Normalize one filename segment for Kindle and local filesystems."""
        cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", value or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.strip(". ")
        return cleaned

    def _build_kindle_filename(self, book: Book, file_path: Path) -> str:
        """Build a readable Kindle filename from metadata with safe fallbacks."""
        title = self._sanitize_filename_segment(book.title or "") or "Unknown Title"
        author = self._sanitize_filename_segment(book.author or "") or "Unknown Author"

        series_name = self._sanitize_filename_segment(
            str(getattr(book, "series_name", "") or "")
        )
        series_index = self._sanitize_filename_segment(
            str(getattr(book, "series_index", "") or "")
        )

        parts = []
        if series_name:
            parts.append(series_name)
            if series_index:
                parts.append(f"#{series_index}")

        parts.extend([title, author])
        base_name = " - ".join(part for part in parts if part)

        ext = file_path.suffix or ".mobi"
        max_base_length = 140 - len(ext)
        if len(base_name) > max_base_length:
            base_name = base_name[:max_base_length].rstrip(" .-")

        if not base_name:
            base_name = "book"

        return f"{base_name}{ext}"

    def _update_sync_metadata(
        self,
        book: Book,
        file_path: Path,
        kindle_filename: Optional[str] = None,
    ) -> None:
        """Update metadata file with synced book info."""
        if not self.kindle:
            return

        metadata = self.kindle.load_metadata()
        stored_filename = kindle_filename or file_path.name

        metadata[book.id] = KindleMetadata(
            title=book.title,
            author=book.author,
            opds_id=book.id,
            original_format=(
                Path(book.download_url).suffix[1:] if book.download_url else "unknown"
            ),
            kindle_format="mobi",
            sync_date=datetime.now().isoformat(),
            local_path=f"Documents/Hearth/{stored_filename}",
            desired_sync=True,
            on_device=True,
            sync_status="on_device",
        )

        self.kindle.save_metadata(metadata)

    def mark_books_desired_for_sync(self, books: List[Book]) -> None:
        """Persist user's selected sync intent in Kindle metadata file."""
        if not self.kindle:
            return

        metadata = self.kindle.load_metadata()
        for book in books:
            existing = metadata.get(book.id)
            if existing:
                existing.desired_sync = True
                if not existing.on_device:
                    existing.sync_status = "not_synced"
                metadata[book.id] = existing
                continue

            metadata[book.id] = KindleMetadata(
                title=book.title,
                author=book.author,
                opds_id=book.id,
                original_format=(
                    Path(book.download_url).suffix[1:]
                    if book.download_url
                    else (book.format or "unknown")
                ),
                kindle_format="mobi",
                sync_date="",
                local_path=None,
                desired_sync=True,
                on_device=False,
                sync_status="not_synced",
            )

        self.kindle.save_metadata(metadata)

    def sync_collection(self, books: List[Book]) -> int:
        """Sync a collection and return the number of synced books."""
        if not self.is_kindle_connected():
            self._log("Error: Kindle device not connected")
            return 0

        synced_count = 0
        for i, book in enumerate(books, 1):
            self._log(f"\n[{i}/{len(books)}] Syncing {book.title}...")
            if self.sync_book(book):
                synced_count += 1

        self._log(f"\nSync complete: {synced_count}/{len(books)} books synced")
        return synced_count

    def force_resync_book(self, book: Book) -> bool:
        """Force re-sync of a book to Kindle, even if already synced."""
        self._log(f"Forcing re-sync of {book.title}...")
        return self.sync_book(book, force_resync=True)

    def mark_book_for_deletion(self, book_id: str, book_title: str) -> bool:
        """Mark a book for deletion from Kindle."""
        if not self.kindle:
            return False

        metadata = self.kindle.load_metadata()
        if book_id not in metadata:
            self._log(f"Book {book_title} not found in metadata")
            return False

        book_meta = metadata[book_id]
        book_meta.marked_for_deletion = True
        book_meta.desired_sync = False
        metadata[book_id] = book_meta

        self.kindle.save_metadata(metadata)
        self._log(f"Marked {book_title} for deletion")
        return True

    def delete_marked_books(self) -> int:
        """Delete all books marked for deletion from Kindle."""
        if not self.kindle:
            return 0

        metadata = self.kindle.load_metadata()
        deleted_count = 0

        for book_id, book_meta in list(metadata.items()):
            if book_meta.marked_for_deletion:
                if book_meta.local_path:
                    filename = Path(book_meta.local_path).name
                    if self.kindle.delete_file_from_kindle(filename):
                        self._log(f"Deleted {book_meta.title} from Kindle")
                        deleted_count += 1
                        del metadata[book_id]
                    else:
                        self._log(f"Failed to delete {book_meta.title}")
                else:
                    # No local path stored, just remove from metadata
                    del metadata[book_id]
                    deleted_count += 1

        self.kindle.save_metadata(metadata)
        return deleted_count

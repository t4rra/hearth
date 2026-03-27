"""Sync manager for coordinating OPDS to Kindle synchronization."""

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

            # Determine file extension from URL or content type
            ext = Path(book.download_url).suffix or ".epub"
            file_path = temp_dir / f"{book.id}{ext}"

            with open(file_path, "wb") as f:
                f.write(content)

            self._log(f"Downloaded {book.title} to {file_path}")
            return file_path

        except OSError as error:
            self._log(f"Error downloading {book.title}: {error}")
            return None

    def sync_book(self, book: Book) -> bool:
        """Download, convert, and sync a book to Kindle."""
        if not self.is_kindle_connected():
            self._log("Error: Kindle device not connected")
            return False

        # Ensure Hearth folder exists on Kindle
        if not self.kindle:
            self._log("Error: Kindle device not available")
            return False

        if not self.kindle.ensure_hearth_folder_exists():
            self._log("Error: Could not create Hearth folder on Kindle")
            return False

        # Download book
        downloaded_path = self.download_book(book)
        if not downloaded_path:
            return False

        # Convert if needed
        if (
            self.settings.auto_convert
            and self.converter
            and self.converter.can_convert(downloaded_path)
        ):
            self._log(f"Converting {book.title}...")
            result = self.converter.convert(
                downloaded_path,
                ConversionFormat.MOBI,
            )

            if not result.success:
                self._log(f"Error converting {book.title}: {result.error}")
                return False

            if result.output_path is None:
                self._log(f"Error converting {book.title}: no output file")
                return False

            converted_path = result.output_path
        else:
            converted_path = downloaded_path

        # Copy to Kindle
        if not self.kindle.copy_to_kindle(converted_path):
            self._log(f"Error copying {book.title} to Kindle")
            return False

        # Update metadata
        self._update_sync_metadata(book, converted_path)

        self._log(f"Successfully synced {book.title} to Kindle")
        return True

    def _update_sync_metadata(self, book: Book, file_path: Path) -> None:
        """Update metadata file with synced book info."""
        if not self.kindle:
            return

        metadata = self.kindle.load_metadata()

        metadata[book.id] = KindleMetadata(
            title=book.title,
            author=book.author,
            opds_id=book.id,
            original_format=(
                Path(book.download_url).suffix[1:] if book.download_url else "unknown"
            ),
            kindle_format="mobi",
            sync_date=datetime.now().isoformat(),
            local_path=str(file_path),
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

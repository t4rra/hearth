"""Sync page for Hearth GUI."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QFileDialog,
    QTreeWidget,
    QTreeWidgetItem,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor, QColor, QBrush

from ..sync.manager import SyncManager
from ..core.config import SettingsManager


class SyncWorker(QThread):
    """Worker thread for sync operations."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    books_loaded = pyqtSignal(list)
    collections_loaded = pyqtSignal(list)

    def __init__(self, sync_manager, operation, collection=None):
        super().__init__()
        self.sync_manager = sync_manager
        self.operation = operation
        self.collection = collection

    def run(self):
        """Run sync operation in background."""
        try:
            if self.operation == "fetch_collections":
                collections = self.sync_manager.fetch_collections()
                self.collections_loaded.emit(collections)
                msg = f"Loaded {len(collections)} " "collections from server"
                self.finished.emit(True, msg)

            elif self.operation == "fetch_books":
                books = self.sync_manager.fetch_books_from_server()
                self.books_loaded.emit(books)
                msg = f"Loaded {len(books)} books from server"
                self.finished.emit(True, msg)

            elif self.operation == "load_collection" and self.collection:
                success = self.sync_manager.load_collection_books(self.collection)
                if success:
                    self.books_loaded.emit(self.collection.books)
                    msg = (
                        f"Loaded {len(self.collection.books)} " "books from collection"
                    )
                    self.finished.emit(True, msg)
                else:
                    msg = "Failed to load collection books"
                    self.finished.emit(False, msg)

            elif self.operation == "check_connection":
                opds_ok = self.sync_manager.is_opds_configured()
                kindle_ok = self.sync_manager.is_kindle_connected()
                opds_icon = "✓" if opds_ok else "✗"
                kindle_icon = "✓" if kindle_ok else "✗"
                msg = f"OPDS: {opds_icon} | " f"Kindle: {kindle_icon}"
                self.finished.emit(opds_ok and kindle_ok, msg)

        except (AttributeError, RuntimeError, ConnectionError, OSError) as error:
            self.finished.emit(False, f"Error: {str(error)}")


class SyncPage(QWidget):
    """Sync management page."""

    def __init__(self):
        super().__init__()
        self.settings_manager = SettingsManager()
        self.sync_manager = SyncManager(self.settings_manager)
        self.sync_worker = None
        self.collections = []
        self.books_by_id = {}
        self.installed_book_ids = set()
        self._tree_nodes_by_path = {}
        self._library_root_item = None
        self.init_ui()
        self.fetch_collections()

    def init_ui(self):
        """Initialize UI elements."""
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Status section
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Connection Status:"))
        self.status_label = QLabel("Checking...")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        check_btn = QPushButton("Check Connection")
        check_btn.clicked.connect(self.check_connection)
        status_layout.addWidget(check_btn)
        layout.addLayout(status_layout)

        # Books/Collections display
        layout.addWidget(QLabel("Library:"))

        # Collections tree (for Collections view)
        self.collections_tree = QTreeWidget()
        self.collections_tree.setHeaderLabels(["Library", "Status"])
        layout.addWidget(self.collections_tree)

        # Buttons
        button_layout = QHBoxLayout()

        fetch_btn = QPushButton("Refresh from Server")
        fetch_btn.clicked.connect(self.fetch_collections)
        button_layout.addWidget(fetch_btn)

        sync_btn = QPushButton("Sync Checked")
        sync_btn.clicked.connect(self.sync_selected)
        button_layout.addWidget(sync_btn)

        layout.addLayout(button_layout)

        # Output log
        layout.addWidget(QLabel("Sync Log:"))
        self.output_log = QTextEdit()
        self.output_log.setReadOnly(True)
        layout.addWidget(self.output_log)

        self.sync_manager.set_progress_callback(self.log_output)

    def check_connection(self):
        """Check connection status."""
        self.log_output("Checking connection status...")
        self.sync_worker = SyncWorker(self.sync_manager, "check_connection")
        self.sync_worker.finished.connect(self.on_connection_checked)
        self.sync_worker.start()

    def on_connection_checked(self, _success: bool, message: str):
        """Handle connection check result."""
        self.status_label.setText(message)
        self.log_output(f"Connection check: {message}")

    def fetch_collections(self):
        """Fetch collections from OPDS server."""
        if not self.sync_manager.is_opds_configured():
            msg = "OPDS server not configured. " "Please configure in Settings."
            QMessageBox.warning(self, "Error", msg)
            return

        self.log_output("Fetching collections from OPDS server...")
        self.sync_worker = SyncWorker(self.sync_manager, "fetch_collections")
        self.sync_worker.finished.connect(self.on_collections_fetched)
        self.sync_worker.collections_loaded.connect(self.display_collections)
        self.sync_worker.start()

    def on_collections_fetched(self, _success: bool, message: str):
        """Handle collections fetch completion."""
        self.log_output(message)

    def display_collections(self, collections: list) -> None:
        """Display collections with status badges."""
        self.collections_tree.clear()
        self._tree_nodes_by_path = {}
        self.collections = collections
        self.books_by_id = {}
        self._load_installed_books()

        self._library_root_item = QTreeWidgetItem(["Library", ""])
        self._set_checkable(self._library_root_item, tri_state=True)
        self._library_root_item.setData(
            0,
            Qt.ItemDataRole.UserRole + 1,
            "root",
        )
        self.collections_tree.addTopLevelItem(self._library_root_item)

        for collection in collections:
            # Try to load books for this collection
            loaded = self.sync_manager.load_collection_books(collection)
            if loaded:
                self.log_output(
                    f"Loaded {len(collection.books)} books " f"in {collection.title}"
                )
            else:
                self.log_output(f"No books loaded for {collection.title}")

            # Count installed vs total
            installed_count = sum(
                1 for b in collection.books if b.id in self.installed_book_ids
            )
            total_count = len(collection.books)

            # Create collection item with badge showing sync status
            if total_count > 0:
                badge_text = f"[{installed_count}/{total_count}]"
            else:
                badge_text = ""
            collection_item = self._get_or_create_collection_item(collection)
            collection_item.setText(1, badge_text)

            for book in collection.books:
                if not book.id:
                    continue
                self.books_by_id[book.id] = book
                is_installed = book.id in self.installed_book_ids

                if self._has_book_child(collection_item, book.id):
                    continue

                # Create badge for each book
                if is_installed:
                    badge = "✓ ON DEVICE"
                    book_display = f"{book.title}"
                else:
                    badge = ""
                    book_display = book.title

                book_item = QTreeWidgetItem([book_display, badge])
                self._set_checkable(book_item)
                book_item.setData(0, Qt.ItemDataRole.UserRole, book.id)
                book_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole + 1,
                    "book",
                )

                # Color and style based on installation status
                if is_installed:
                    # Green for installed
                    brush = QBrush(QColor(76, 175, 80))  # Material green
                    book_item.setForeground(1, brush)
                    font = book_item.font(1)
                    font.setBold(True)
                    book_item.setFont(1, font)

                    # Slightly highlight row
                    bg_brush = QBrush(QColor(240, 248, 245))
                    book_item.setBackground(0, bg_brush)
                    brush = QBrush(QColor(100, 150, 100))
                    book_item.setForeground(0, brush)

                collection_item.addChild(book_item)

        self.collections_tree.collapseAll()
        self._library_root_item.setExpanded(True)

    def _has_book_child(
        self,
        parent: QTreeWidgetItem,
        book_id: str,
    ) -> bool:
        """Return whether a collection node already has this book child."""
        for idx in range(parent.childCount()):
            child = parent.child(idx)
            if child is None:
                continue
            node_type = child.data(0, Qt.ItemDataRole.UserRole + 1)
            if node_type != "book":
                continue
            existing_book_id = child.data(0, Qt.ItemDataRole.UserRole)
            if existing_book_id == book_id:
                return True
        return False

    def _set_checkable(
        self,
        item: QTreeWidgetItem,
        tri_state: bool = False,
    ) -> None:
        """Mark tree item as checkable."""
        flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
        if tri_state:
            flags |= Qt.ItemFlag.ItemIsAutoTristate
        item.setFlags(flags)
        item.setCheckState(0, Qt.CheckState.Unchecked)

    def _get_or_create_collection_item(
        self,
        collection,
    ) -> QTreeWidgetItem:
        """Build nested folder-like collection items from collection path."""
        if collection.path:
            parts = [part.strip() for part in collection.path.split("/")]
            parts = [part for part in parts if part]
        else:
            parts = [collection.title]

        current_parent = self._library_root_item
        current_path = ""

        for part in parts:
            current_path = f"{current_path}/{part}" if current_path else part
            existing = self._tree_nodes_by_path.get(current_path)
            if existing:
                current_parent = existing
                continue

            node = QTreeWidgetItem([part, ""])
            self._set_checkable(node, tri_state=True)
            node.setData(0, Qt.ItemDataRole.UserRole + 1, "collection")

            font = node.font(0)
            font.setBold(True)
            node.setFont(0, font)

            current_parent.addChild(node)
            self._tree_nodes_by_path[current_path] = node
            current_parent = node

        current_parent.setData(0, Qt.ItemDataRole.UserRole, collection.id)
        return current_parent

    def _load_installed_books(self):
        """Load list of books already installed on Kindle."""
        self.installed_book_ids = set()
        if self.sync_manager.kindle:
            if not self.sync_manager.is_kindle_connected():
                self.log_output("Kindle not connected; skipping installed-book scan")
                return

            transport = self.sync_manager.kindle.get_transport()
            if transport == "mtp-libmtp":
                self.log_output(
                    "MTP transport active; skipping startup metadata scan"
                )
                return

            metadata = self.sync_manager.kindle.load_metadata()
            self.installed_book_ids = set(metadata.keys())

    def sync_selected(self):
        """Sync selected books to Kindle."""
        if not self.sync_manager.is_kindle_connected():
            self.log_output("No MTP Kindle detected; requesting mounted Kindle path...")
            if not self._prompt_for_mounted_kindle():
                msg = (
                    "Kindle device not connected. "
                    "Please connect and configure in Settings."
                )
                QMessageBox.warning(self, "Error", msg)
                return
            if not self.sync_manager.is_kindle_connected():
                msg = (
                    "Could not access the selected Kindle mount path. "
                    "Please choose the Kindle root folder."
                )
                QMessageBox.warning(self, "Error", msg)
                return

        books_to_sync = self._get_checked_books_from_tree()
        if not books_to_sync:
            msg = "Please check at least one collection or book to sync."
            QMessageBox.warning(self, "Error", msg)
            return

        self.log_output(f"Starting sync of {len(books_to_sync)} book(s)...")

        for book in books_to_sync:
            self.sync_manager.sync_book(book)

    def _get_checked_books_from_tree(self):
        """Collect checked books in collection tree, deduplicated by ID."""
        checked_books = []
        seen_ids = set()

        if not self._library_root_item:
            return checked_books

        stack = [self._library_root_item]
        while stack:
            node = stack.pop()
            for idx in range(node.childCount()):
                child = node.child(idx)
                stack.append(child)

            node_type = node.data(0, Qt.ItemDataRole.UserRole + 1)
            if node_type != "book":
                continue

            book_id = node.data(0, Qt.ItemDataRole.UserRole)
            if not book_id:
                continue

            if node.checkState(0) != Qt.CheckState.Checked:
                continue

            if book_id in seen_ids:
                continue

            book = self.books_by_id.get(book_id)
            if book is None:
                continue

            seen_ids.add(book_id)
            checked_books.append(book)

        return checked_books

    def log_output(self, message: str):
        """Add message to output log."""
        self.output_log.moveCursor(QTextCursor.MoveOperation.End)
        self.output_log.insertPlainText(message + "\n")

    def _prompt_for_mounted_kindle(self) -> bool:
        """Prompt user to select mounted Kindle when MTP is unavailable."""
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select Mounted Kindle",
        )
        if not selected:
            return False

        self.settings_manager.update_settings(kindle_mount_path=selected)
        self.sync_manager = SyncManager(self.settings_manager)
        self.sync_manager.set_progress_callback(self.log_output)
        self.log_output(f"Using mounted Kindle path: {selected}")
        return True

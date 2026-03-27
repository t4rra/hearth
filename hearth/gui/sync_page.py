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
    QMenu,
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
    startup_status_loaded = pyqtSignal(dict)

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
                status = self.sync_manager.get_startup_status()
                self.startup_status_loaded.emit(status)
                opds_ok = bool(status.get("opds_configured"))
                kindle_ok = bool(status.get("kindle_connected"))
                calibre_ok = bool(status.get("calibre_available"))
                kcc_ok = bool((status.get("kcc") or {}).get("ready"))
                opds_icon = "✓" if opds_ok else "✗"
                kindle_icon = "✓" if kindle_ok else "✗"
                calibre_icon = "✓" if calibre_ok else "✗"
                kcc_icon = "✓" if kcc_ok else "✗"
                msg = (
                    f"OPDS: {opds_icon} | Kindle: {kindle_icon} | "
                    f"Calibre: {calibre_icon} | KCC: {kcc_icon}"
                )
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
        self._active_workers = []
        self.collections = []
        self.books_by_id = {}
        self.installed_book_ids = set()
        self.desired_book_ids = set()
        self.sync_status_by_id = {}
        self._tree_nodes_by_path = {}
        self._library_root_item = None
        self._startup_status_logged = False
        self.init_ui()
        self.check_connection()
        self.fetch_collections()

    def _start_worker(self, operation: str, collection=None) -> SyncWorker:
        """Create and start a tracked worker thread."""
        worker = SyncWorker(self.sync_manager, operation, collection)
        self.sync_worker = worker
        self._active_workers.append(worker)
        worker.finished.connect(
            lambda _success, _message, w=worker: self._cleanup_worker(w)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()
        return worker

    def _cleanup_worker(self, worker: SyncWorker) -> None:
        """Remove completed worker from active list."""
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def _stop_all_workers(self) -> None:
        """Stop any running workers before widget teardown."""
        for worker in list(self._active_workers):
            if worker.isRunning():
                worker.quit()
                if not worker.wait(1500):
                    worker.terminate()
                    worker.wait(500)
            self._cleanup_worker(worker)

    def closeEvent(self, event):
        """Ensure no background QThreads survive widget destruction."""
        self._stop_all_workers()
        super().closeEvent(event)

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
        context_menu_policy = Qt.ContextMenuPolicy.CustomContextMenu
        self.collections_tree.setContextMenuPolicy(context_menu_policy)
        self.collections_tree.customContextMenuRequested.connect(
            self.on_tree_context_menu
        )
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
        worker = self._start_worker("check_connection")
        worker.startup_status_loaded.connect(self.on_startup_status_loaded)
        worker.finished.connect(self.on_connection_checked)

    def on_startup_status_loaded(self, status: dict):
        """Handle startup diagnostic payload from worker."""
        self._log_startup_status(status)

    def on_connection_checked(self, _success: bool, message: str):
        """Handle connection check result."""
        self.status_label.setText(message)
        self.log_output(f"Connection check: {message}")

    def _log_startup_status(self, status: dict) -> None:
        """Render startup dependency diagnostics in the sync log."""
        if self._startup_status_logged:
            self.log_output("Startup status refreshed")
        else:
            self.log_output("Startup status")
            self._startup_status_logged = True

        def icon(value: bool) -> str:
            return "✓" if value else "✗"

        opds_ok = bool(status.get("opds_configured"))
        kindle_ok = bool(status.get("kindle_connected"))
        calibre_ok = bool(status.get("calibre_available"))
        kcc_status = status.get("kcc") or {}
        kcc_ok = bool(kcc_status.get("ready"))

        self.log_output(f"  {icon(opds_ok)} OPDS configured")
        self.log_output(f"  {icon(kindle_ok)} Kindle connected")
        self.log_output(f"  {icon(calibre_ok)} Calibre available")
        self.log_output(f"  {icon(kcc_ok)} KCC available")

        kcc_command = kcc_status.get("command_text") or ""
        if kcc_command:
            self.log_output(f"  KCC command: {kcc_command}")

        repo_dir = kcc_status.get("repo_dir") or ""
        if repo_dir:
            self.log_output(f"  KCC repo dir: {repo_dir}")

        issues = kcc_status.get("issues") or []
        for issue in issues:
            self.log_output(f"  KCC issue: {issue}")

    def fetch_collections(self):
        """Fetch collections from OPDS server."""
        if not self.sync_manager.is_opds_configured():
            msg = "OPDS server not configured. " "Please configure in Settings."
            QMessageBox.warning(self, "Error", msg)
            return

        self.log_output("Fetching collections from OPDS server...")
        worker = self._start_worker("fetch_collections")
        worker.finished.connect(self.on_collections_fetched)
        worker.collections_loaded.connect(self.display_collections)

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
                is_desired = book.id in self.desired_book_ids
                sync_status = self.sync_status_by_id.get(book.id, "")

                if self._has_book_child(collection_item, book.id):
                    continue

                # Create badge for each book
                if is_desired and is_installed:
                    badge = "✓ WANTED · ON DEVICE"
                    book_display = book.title
                elif is_desired and not is_installed:
                    if sync_status == "syncing":
                        badge = "⟳ WANTED · SYNCING"
                    else:
                        badge = "⚠ WANTED · NOT SYNCED"
                    book_display = book.title
                elif is_installed:
                    badge = "✓ ON DEVICE"
                    book_display = book.title
                else:
                    badge = ""
                    book_display = book.title

                book_item = QTreeWidgetItem([book_display, badge])
                self._set_checkable(book_item)
                if is_desired:
                    book_item.setCheckState(0, Qt.CheckState.Checked)
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
        self.desired_book_ids = set()
        self.sync_status_by_id = {}

        if self.sync_manager.kindle:
            if not self.sync_manager.is_kindle_connected():
                self.log_output("Kindle not connected; skipping installed-book scan")
                return

            metadata = self.sync_manager.kindle.load_metadata()
            for book_id, meta in metadata.items():
                if meta.desired_sync:
                    self.desired_book_ids.add(book_id)
                if meta.on_device or meta.sync_status == "on_device":
                    self.installed_book_ids.add(book_id)
                self.sync_status_by_id[book_id] = meta.sync_status

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

        self.sync_manager.mark_books_desired_for_sync(books_to_sync)

        self.log_output(f"Starting sync of {len(books_to_sync)} book(s)...")

        for book in books_to_sync:
            self.sync_manager.sync_book(
                book,
                dependency_prompt_callback=self._prompt_dependency_action,
            )

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

    def on_tree_context_menu(self, position):
        """Handle right-click context menu on tree items."""
        item = self.collections_tree.itemAt(position)
        if not item:
            return

        node_type = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if node_type != "book":
            return

        book_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not book_id:
            return

        book = self.books_by_id.get(book_id)
        if not book:
            return

        # Only show actions for installed books
        if book_id not in self.installed_book_ids:
            return

        menu = QMenu(self)

        resync_action = menu.addAction("Re-Sync to Kindle")
        resync_action.triggered.connect(lambda: self.resync_book(book))

        delete_action = menu.addAction("Delete from Kindle")
        delete_action.triggered.connect(lambda: self.delete_book(book))

        menu.exec(self.collections_tree.mapToGlobal(position))

    def resync_book(self, book):
        """Force re-sync a book to Kindle."""
        message = (
            f"Re-sync {book.title} to Kindle?\n\n"
            "This will re-download, re-convert, and re-send the book."
        )
        reply = QMessageBox.question(
            self,
            "Re-Sync Book",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        self.log_output(f"Starting re-sync of {book.title}...")
        if self.sync_manager.force_resync_book(book):
            self.log_output(f"Successfully re-synced {book.title}")
            # Refresh the tree to update status
            self._load_installed_books()
            self.fetch_collections()
        else:
            self.log_output(f"Failed to re-sync {book.title}")
            error_msg = f"Failed to re-sync {book.title}"
            QMessageBox.warning(self, "Error", error_msg)

    def delete_book(self, book):
        """Mark a book for deletion from Kindle."""
        message = (
            f"Delete {book.title} from Kindle?\n\n"
            "This action will remove the book from your Kindle."
        )
        reply = QMessageBox.question(
            self,
            "Delete Book",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.No:
            return

        self.log_output(f"Marking {book.title} for deletion...")
        if self.sync_manager.mark_book_for_deletion(book.id, book.title):
            # Delete marked books
            self.sync_manager.delete_marked_books()
            self.log_output(f"Successfully deleted {book.title}")
            # Refresh the tree to update status
            self._load_installed_books()
            self.fetch_collections()
        else:
            self.log_output(f"Failed to delete {book.title}")
            error_msg = f"Failed to delete {book.title}"
            QMessageBox.warning(self, "Error", error_msg)

    def _prompt_dependency_action(self, dependency: str, status: dict) -> bool:
        """Prompt user when an optional conversion dependency is missing."""
        if dependency == "kcc":
            issues = status.get("issues") or []
            issue_lines = "\n".join(f"- {item}" for item in issues)
            if issue_lines:
                issue_lines = f"\n\nDetected issues:\n{issue_lines}"

            reply = QMessageBox.question(
                self,
                "Comic Conversion Requires KCC",
                (
                    "This comic requires Kindle Comic Converter (KCC).\n\n"
                    "Would you like Hearth to download/setup KCC now?"
                    f"{issue_lines}"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes

        if dependency == "7z":
            reply = QMessageBox.question(
                self,
                "7z Not Found",
                (
                    "7z was not detected. Some comic archives require it.\n\n"
                    "Continue this conversion anyway? "
                    "(Choose No to skip this book and continue with others.)"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes

        return False

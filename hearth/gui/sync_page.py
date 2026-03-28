"""Sync page for Hearth GUI."""

from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QFileDialog,
    QTreeWidget,
    QTreeWidgetItem,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QMenu,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QRect, QEvent
from PyQt6.QtGui import (
    QTextCursor,
    QColor,
    QBrush,
    QPixmap,
    QPainter,
    QIcon,
    QPalette,
)

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

        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            OSError,
            TypeError,
            ValueError,
            UnicodeError,
        ) as error:
            self.finished.emit(False, f"Error: {str(error)}")


class _LoadingOverlay(QWidget):
    """Single-window blocking overlay used for loading and sync progress."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setVisible(False)

        card = QFrame(self)
        card.setObjectName("overlayCard")

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        self.title_label = QLabel("Please Wait")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = self.title_label.font()
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        card_layout.addWidget(self.title_label)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.message_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        card_layout.addWidget(self.progress_bar)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(24, 24, 24, 24)
        outer_layout.addStretch()

        center_row = QHBoxLayout()
        center_row.addStretch()
        center_row.addWidget(card)
        center_row.addStretch()

        outer_layout.addLayout(center_row)
        outer_layout.addStretch()

        self._card = card
        self._applying_palette_theme = False
        self._overlay_style = ""
        self._card_style = ""
        self._text_style = ""
        self._apply_palette_theme()

    def _apply_palette_theme(self) -> None:
        """Adapt overlay/card colors to active app palette."""
        if self._applying_palette_theme:
            return

        self._applying_palette_theme = True
        palette = self.palette()
        try:
            window_color = palette.window().color()
            text_color = palette.windowText().color()
            base_color = palette.base().color()
            border_color = palette.mid().color()

            alpha = 150 if window_color.lightness() < 128 else 96
            overlay_style = f"background-color: rgba(0, 0, 0, {alpha});"
            if overlay_style != self._overlay_style:
                self.setStyleSheet(overlay_style)
                self._overlay_style = overlay_style

            card_style = (
                "QFrame#overlayCard {"
                f"background: {base_color.name()};"
                f"border: 1px solid {border_color.name()};"
                "border-radius: 10px;"
                "}"
            )
            if card_style != self._card_style:
                self._card.setStyleSheet(card_style)
                self._card_style = card_style

            text_style = f"color: {text_color.name()};"
            if text_style != self._text_style:
                self.title_label.setStyleSheet(text_style)
                self.message_label.setStyleSheet(text_style)
                self._text_style = text_style
        finally:
            self._applying_palette_theme = False

    def changeEvent(self, event) -> None:
        """Refresh colors when system/app palette changes."""
        event_type = event.type()
        if event_type in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            if not self._applying_palette_theme:
                self._apply_palette_theme()
        super().changeEvent(event)

    def show_spinner(self, title: str, message: str) -> None:
        """Show indeterminate busy state."""
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.show()
        self.raise_()

    def show_progress(
        self,
        title: str,
        message: str,
        minimum: int,
        maximum: int,
    ) -> None:
        """Show determinate progress state."""
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.progress_bar.setRange(minimum, maximum)
        self.progress_bar.setValue(minimum)
        self.show()
        self.raise_()

    def update_progress(self, value: int, message: str) -> None:
        """Update determinate progress state."""
        self.message_label.setText(message)
        self.progress_bar.setValue(value)


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
        self.last_synced_by_id = {}
        self.on_device_by_id = {}
        self._tree_nodes_by_path = {}
        self._library_root_item = None
        self._startup_status_logged = False
        self._loading_overlay = None
        self._startup_pending_steps = set()
        self._sync_total_steps = 0
        self._use_grid_view = False
        self._cover_icon_cache = {}
        self.init_ui()
        self._loading_overlay = _LoadingOverlay(self)
        self._loading_overlay.setGeometry(self.rect())
        self._begin_startup_loading()
        self.check_connection()
        self.fetch_collections()

    def _begin_startup_loading(self) -> None:
        """Show blocking spinner while initial startup checks complete."""
        self._startup_pending_steps = {"connection", "collections"}
        self._show_blocking_dialog("Loading library and checking Kindle...")

    def _mark_startup_step_done(self, step: str) -> None:
        """Track startup async completion and close blocker when done."""
        if step in self._startup_pending_steps:
            self._startup_pending_steps.remove(step)
        if not self._startup_pending_steps:
            self._hide_blocking_dialog()

    def _show_blocking_dialog(self, label: str) -> None:
        """Show in-page blocking overlay with an indeterminate spinner."""
        if self._loading_overlay is None:
            return
        self._loading_overlay.show_spinner("Please Wait", label)

    def _hide_blocking_dialog(self) -> None:
        """Hide active loading overlay."""
        if self._loading_overlay is not None:
            self._loading_overlay.hide()

    def _start_sync_progress(self, total_books: int) -> None:
        """Start determinate in-page progress overlay for sync operations."""
        self._sync_total_steps = max(1, total_books * 2)
        if self._loading_overlay is None:
            return
        self._loading_overlay.show_progress(
            "Syncing Library",
            f"Preparing books 0/{total_books}...",
            0,
            self._sync_total_steps,
        )

    def _update_sync_progress(self, value: int, label: str) -> None:
        """Update sync progress value and label if overlay is active."""
        if self._loading_overlay is None:
            return
        self._loading_overlay.update_progress(
            min(value, self._sync_total_steps),
            label,
        )

    def _finish_sync_progress(self) -> None:
        """Close sync progress overlay."""
        self._sync_total_steps = 0
        self._hide_blocking_dialog()

    def resizeEvent(self, event) -> None:
        """Keep overlay stretched to page bounds on resize."""
        super().resizeEvent(event)
        if self._loading_overlay is not None:
            self._loading_overlay.setGeometry(self.rect())

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
        library_header_layout = QHBoxLayout()
        library_header_layout.addWidget(QLabel("Library:"))
        library_header_layout.addStretch()

        self.list_view_btn = QPushButton("List")
        self.list_view_btn.clicked.connect(lambda: self._set_library_view(False))
        library_header_layout.addWidget(self.list_view_btn)

        self.grid_view_btn = QPushButton("Grid")
        self.grid_view_btn.clicked.connect(lambda: self._set_library_view(True))
        library_header_layout.addWidget(self.grid_view_btn)

        layout.addLayout(library_header_layout)

        # Collections tree (for Collections view)
        self.collections_tree = QTreeWidget()
        self.collections_tree.setHeaderLabels(
            ["Library", "Status", "Last Synced", "On Device", "Actions"]
        )
        self.collections_tree.setColumnWidth(0, 460)
        self.collections_tree.setColumnWidth(1, 220)
        self.collections_tree.setColumnWidth(2, 170)
        self.collections_tree.setColumnWidth(3, 90)
        self.collections_tree.setColumnWidth(4, 200)
        context_menu_policy = Qt.ContextMenuPolicy.CustomContextMenu
        self.collections_tree.setContextMenuPolicy(context_menu_policy)
        self.collections_tree.customContextMenuRequested.connect(
            self.on_tree_context_menu
        )

        self.grid_view = QListWidget()
        self.grid_view.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid_view.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid_view.setMovement(QListWidget.Movement.Static)
        self.grid_view.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.grid_view.setIconSize(QSize(120, 170))
        self.grid_view.setGridSize(QSize(170, 250))
        self.grid_view.setSpacing(10)

        self.library_view_stack = QStackedWidget()
        self.library_view_stack.addWidget(self.collections_tree)
        self.library_view_stack.addWidget(self.grid_view)
        layout.addWidget(self.library_view_stack)
        self._set_library_view(False)

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

    def _set_library_view(self, grid: bool) -> None:
        """Switch between list and cover grid views."""
        self._use_grid_view = grid
        if grid:
            self.library_view_stack.setCurrentWidget(self.grid_view)
            self.grid_view_btn.setEnabled(False)
            self.list_view_btn.setEnabled(True)
        else:
            self.library_view_stack.setCurrentWidget(self.collections_tree)
            self.grid_view_btn.setEnabled(True)
            self.list_view_btn.setEnabled(False)

    def _build_cover_icon(self, title: str, author: str) -> QIcon:
        """Create a lightweight pseudo-cover icon for grid tiles."""
        pixmap = QPixmap(120, 170)
        pixmap.fill(QColor(242, 242, 242))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        accent = QColor(86, 113, 204)
        painter.fillRect(QRect(0, 0, 120, 170), QColor(230, 235, 248))
        painter.fillRect(QRect(0, 0, 120, 32), accent)

        initial = "?"
        for char in title or "":
            if char.strip():
                initial = char.upper()
                break

        painter.setPen(QColor("white"))
        title_font = painter.font()
        title_font.setBold(True)
        title_font.setPointSize(14)
        painter.setFont(title_font)
        painter.drawText(
            QRect(0, 0, 120, 32),
            Qt.AlignmentFlag.AlignCenter,
            initial,
        )

        painter.setPen(QColor(45, 45, 45))
        meta_font = painter.font()
        meta_font.setPointSize(8)
        painter.setFont(meta_font)

        title_text = (title or "Unknown").strip()
        if len(title_text) > 34:
            title_text = title_text[:31] + "..."
        author_text = (author or "Unknown").strip()
        if len(author_text) > 34:
            author_text = author_text[:31] + "..."

        painter.drawText(
            QRect(8, 44, 104, 68),
            Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            title_text,
        )
        painter.setPen(QColor(90, 90, 90))
        painter.drawText(
            QRect(8, 118, 104, 44),
            Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            author_text,
        )

        painter.end()
        return QIcon(pixmap)

    def _cover_icon_from_book(self, book) -> QIcon:
        """Return cached OPDS cover icon when available, else generated icon."""
        cache_key = book.id or book.title
        if cache_key in self._cover_icon_cache:
            return self._cover_icon_cache[cache_key]

        icon = None
        if book.cover_url and self.sync_manager.opds_client:
            content = self.sync_manager.opds_client.download_content(
                book.cover_url,
                timeout=6,
            )
            if content:
                pixmap = QPixmap()
                if pixmap.loadFromData(content):
                    scaled = pixmap.scaled(
                        120,
                        170,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    icon = QIcon(scaled)

        if icon is None:
            icon = self._build_cover_icon(book.title, book.author)

        self._cover_icon_cache[cache_key] = icon
        return icon

    def _refresh_grid_view(self) -> None:
        """Render flat book grid with cover tiles."""
        self.grid_view.clear()

        books = sorted(
            self.books_by_id.values(),
            key=lambda book: (book.title or "").lower(),
        )

        for book in books:
            is_installed = book.id in self.installed_book_ids
            is_desired = book.id in self.desired_book_ids

            status = ""
            if is_desired and is_installed:
                status = "Wanted · On Device"
            elif is_desired and not is_installed:
                status = "Wanted · Not Synced"
            elif is_installed:
                status = "On Device"

            text = book.title
            if status:
                text = f"{book.title}\n{status}"

            icon = self._cover_icon_from_book(book)
            item = QListWidgetItem(icon, text)
            item.setData(Qt.ItemDataRole.UserRole, book.id)
            item.setToolTip(f"{book.title}\n{book.author}")
            self.grid_view.addItem(item)

            if is_desired:
                item.setSelected(True)

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
        self._mark_startup_step_done("connection")

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
            self._mark_startup_step_done("collections")
            return

        self.log_output("Fetching collections from OPDS server...")
        worker = self._start_worker("fetch_collections")
        worker.finished.connect(self.on_collections_fetched)
        worker.collections_loaded.connect(self.display_collections)

    def on_collections_fetched(self, _success: bool, message: str):
        """Handle collections fetch completion."""
        self.log_output(message)
        self._mark_startup_step_done("collections")

    def display_collections(self, collections: list) -> None:
        """Display collections with status badges."""
        self.collections_tree.clear()
        self.grid_view.clear()
        self._cover_icon_cache.clear()
        self._tree_nodes_by_path = {}
        self.collections = collections
        self.books_by_id = {}
        self._load_installed_books()

        self._library_root_item = QTreeWidgetItem(["Library", "", "", "", ""])
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
            collection_item.setText(2, "")
            collection_item.setText(3, "")
            collection_item.setText(4, "")

            for book in collection.books:
                if not book.id:
                    continue
                self.books_by_id[book.id] = book
                is_installed = book.id in self.installed_book_ids
                is_desired = book.id in self.desired_book_ids
                sync_status = self.sync_status_by_id.get(book.id, "")
                on_device = bool(self.on_device_by_id.get(book.id, is_installed))
                on_device_text = "✓" if on_device else ""
                last_synced = self._format_sync_date(
                    self.last_synced_by_id.get(book.id, "")
                )

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

                book_item = QTreeWidgetItem(
                    [book_display, badge, last_synced, on_device_text, ""]
                )
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
                self._attach_book_actions(book_item, book, on_device)

        self.collections_tree.collapseAll()
        self._library_root_item.setExpanded(True)
        self._refresh_grid_view()

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

            node = QTreeWidgetItem([part, "", "", "", ""])
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
        self.last_synced_by_id = {}
        self.on_device_by_id = {}

        if self.sync_manager.kindle:
            if not self.sync_manager.is_kindle_connected():
                self.log_output("Kindle not connected; skipping installed-book scan")
                return

            metadata = self.sync_manager.kindle.load_metadata()
            for book_id, meta in metadata.items():
                if meta.desired_sync:
                    self.desired_book_ids.add(book_id)
                on_device = bool(meta.on_device or meta.sync_status == "on_device")
                self.on_device_by_id[book_id] = on_device
                if on_device:
                    self.installed_book_ids.add(book_id)
                self.sync_status_by_id[book_id] = meta.sync_status
                self.last_synced_by_id[book_id] = meta.sync_date

    def _format_sync_date(self, raw: str) -> str:
        """Format ISO-ish sync timestamp for table display."""
        value = (raw or "").strip()
        if not value:
            return ""

        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return value

        return parsed.strftime("%Y-%m-%d %H:%M")

    def _attach_book_actions(
        self,
        item: QTreeWidgetItem,
        book,
        on_device: bool,
    ):
        """Attach inline action buttons for row-level book operations."""
        if not on_device:
            item.setText(4, "-")
            return

        container = QWidget(self.collections_tree)
        row_layout = QHBoxLayout(container)
        row_layout.setContentsMargins(4, 0, 4, 0)
        row_layout.setSpacing(6)

        resync_btn = QPushButton("Re-Sync")
        delete_btn = QPushButton("Delete")
        resync_btn.clicked.connect(lambda _checked=False, b=book: self.resync_book(b))
        delete_btn.clicked.connect(lambda _checked=False, b=book: self.delete_book(b))

        row_layout.addWidget(resync_btn)
        row_layout.addWidget(delete_btn)
        row_layout.addStretch()
        self.collections_tree.setItemWidget(item, 4, container)

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

        if self._use_grid_view:
            books_to_sync = self._get_selected_books_from_grid()
            empty_selection_msg = "Please select at least one book to sync."
        else:
            books_to_sync = self._get_checked_books_from_tree()
            empty_selection_msg = (
                "Please check at least one collection or book to sync."
            )

        if not books_to_sync:
            QMessageBox.warning(self, "Error", empty_selection_msg)
            return

        self.sync_manager.mark_books_desired_for_sync(books_to_sync)

        self.log_output(f"Starting sync of {len(books_to_sync)} book(s)...")
        total_books = len(books_to_sync)
        total_steps = max(1, total_books * 2)
        completed_steps = 0
        prepared_count = 0
        pushed_count = 0
        self._start_sync_progress(total_books)

        try:
            prepared_books = []
            skipped_titles = []
            for book in books_to_sync:
                prepared = self.sync_manager.prepare_book_for_sync(
                    book,
                    dependency_prompt_callback=self._prompt_dependency_action,
                )
                completed_steps += 1
                if prepared is not None:
                    prepared_books.append((book, prepared))
                    prepared_count += 1
                else:
                    skipped_titles.append(book.title)

                self._update_sync_progress(
                    min(completed_steps, total_steps),
                    f"Preparing books {prepared_count}/{total_books}...",
                )

            if not prepared_books:
                self.log_output("No books were prepared for push")
                self._show_sync_summary(
                    requested=len(books_to_sync),
                    synced=0,
                    failed=0,
                    skipped=len(skipped_titles),
                    failed_titles=[],
                    skipped_titles=skipped_titles,
                )
                return

            self.log_output(
                f"Pushing {len(prepared_books)} prepared book(s) to Kindle..."
            )

            synced_titles = []
            failed_titles = []
            for book, local_path in prepared_books:
                if self.sync_manager.push_prepared_book_to_kindle(
                    book,
                    local_path,
                ):
                    synced_titles.append(book.title)
                else:
                    failed_titles.append(book.title)

                pushed_count += 1
                completed_steps += 1
                self._update_sync_progress(
                    min(completed_steps, total_steps),
                    f"Syncing books {pushed_count}/{len(prepared_books)}...",
                )

            self._load_installed_books()
            self.fetch_collections()

            self._show_sync_summary(
                requested=len(books_to_sync),
                synced=len(synced_titles),
                failed=len(failed_titles),
                skipped=len(skipped_titles),
                failed_titles=failed_titles,
                skipped_titles=skipped_titles,
            )
        finally:
            self._finish_sync_progress()

    def _show_sync_summary(
        self,
        requested: int,
        synced: int,
        failed: int,
        skipped: int,
        failed_titles: list[str],
        skipped_titles: list[str],
    ) -> None:
        """Show sync completion summary with optional eject action."""
        if failed > 0:
            icon = QMessageBox.Icon.Warning
        else:
            icon = QMessageBox.Icon.Information

        summary_lines = [
            f"Requested: {requested}",
            f"Synced: {synced}",
            f"Failed: {failed}",
            f"Skipped: {skipped}",
        ]
        summary_text = "\n".join(summary_lines)

        details = []
        if failed_titles:
            details.append(
                "Failed books:\n" + "\n".join(f"- {t}" for t in failed_titles)
            )
        if skipped_titles:
            details.append(
                "Skipped books (conversion/dependency/duplicate):\n"
                + "\n".join(f"- {t}" for t in skipped_titles)
            )

        dialog = QMessageBox(self)
        dialog.setWindowTitle("Sync Complete")
        dialog.setIcon(icon)
        dialog.setText("Sync completed.")
        dialog.setInformativeText(summary_text)
        if details:
            dialog.setDetailedText("\n\n".join(details))

        eject_button = dialog.addButton(
            "Eject Kindle",
            QMessageBox.ButtonRole.ActionRole,
        )
        dialog.addButton(QMessageBox.StandardButton.Ok)
        dialog.exec()

        if dialog.clickedButton() == eject_button:
            self._eject_kindle_connection()

    def _eject_kindle_connection(self) -> None:
        """Release Hearth's active Kindle connection/session handle."""
        if not self.sync_manager.kindle:
            self.log_output("No active Kindle device to eject")
            return

        self.sync_manager.kindle.close()
        self.log_output("Released Kindle connection; exiting Hearth")

        app = QApplication.instance()
        if app is not None:
            app.quit()

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

    def _get_selected_books_from_grid(self):
        """Collect selected books from cover grid view."""
        selected_books = []
        seen_ids = set()

        for item in self.grid_view.selectedItems():
            book_id = item.data(Qt.ItemDataRole.UserRole)
            if not book_id or book_id in seen_ids:
                continue

            book = self.books_by_id.get(book_id)
            if not book:
                continue

            seen_ids.add(book_id)
            selected_books.append(book)

        return selected_books

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

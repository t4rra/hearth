from __future__ import annotations

# pylint: disable=import-error

from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
import json
import traceback
import tempfile
from typing import Any, Callable, Literal, cast
import urllib.parse

from PyQt6.QtCore import QTimer, Qt  # type: ignore[import-not-found]
from PyQt6.QtGui import QBrush, QColor, QPalette, QIcon, QFont  # type: ignore[import-not-found]
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QProgressBar,
    QPushButton,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)  # type: ignore[import-not-found]

from hearth.converters.manager import ConverterManager
from hearth.core.opds import OPDSClient, OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import DeviceFile, KindleDevice
from hearth.sync.manager import SyncItem, SyncManager, SyncProgress
from hearth.sync.metadata import (
    SyncRecord,
    load_metadata,
    merge_device_files_into_records,
    reconcile_on_device,
    save_metadata,
)

from .workers import WorkerPool


@dataclass(slots=True)
class LibraryRow:
    id: str
    title: str
    author: str
    download_url: str
    declared_type: str
    source_feed: str = ""
    deleted_from_server: bool = False


@dataclass(slots=True)
class CollectionRow:
    feed_url: str
    title: str


@dataclass(slots=True)
class FeedLoadResult:
    feed_url: str
    is_root: bool
    children: list[CollectionRow]
    books: list[LibraryRow]


@dataclass(slots=True)
class PendingTask:
    future: Future[Any]
    on_success: Callable[[object], None]
    action_name: str
    show_errors: bool = True


@dataclass(slots=True)
class DeviceSnapshot:
    transport: str
    root: Path


@dataclass(slots=True)
class KindleFilesLoadResult:
    rows: list[DeviceFile]
    diagnostics: list[str]


@dataclass(slots=True)
class SyncRunSummary:
    attempted_add: int
    synced: int
    skipped: int
    deleted: int
    delete_failed: int
    failed_delete_ids: list[str]


@dataclass(slots=True)
class MetadataRebuildResult:
    path: Path
    before_count: int
    after_count: int


@dataclass(slots=True)
class RemoveFromKindleResult:
    removed: bool


class HearthMainWindow(QMainWindow):
    PLACEHOLDER_TEXT = "(expand to load)"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hearth")
        self.resize(1240, 820)

        self.settings_path = Path.home() / ".hearth" / "settings.json"
        self.worker_pool = WorkerPool(max_workers=3)
        self.pending_tasks: list[PendingTask] = []

        self.connected_device: DeviceSnapshot | None = None
        self.is_busy = False
        self.startup_catalog_attempted = False
        self.status_base_text = "Idle"
        self.status_anim_frame = 0
        self.sync_progress_queue: SimpleQueue[SyncProgress] | None = None

        self.books_by_feed: dict[str, list[LibraryRow]] = {}
        self.book_rows_by_id: dict[str, LibraryRow] = {}
        self.book_feeds_by_id: dict[str, set[str]] = {}
        self.collection_book_cache: dict[str, list[str]] = {}
        self.loaded_feeds: set[str] = set()
        self.loading_feeds: set[str] = set()
        self.tree_item_by_feed: dict[str, QTreeWidgetItem] = {}
        self.feed_children: dict[str, set[str]] = {}
        self.feed_parent: dict[str, str] = {}

        self.metadata_records: dict[str, SyncRecord] = {}
        self.device_files: set[str] = set()
        self.device_on_book_ids: set[str] = set()
        self.pending_book_actions: dict[str, Literal["add", "remove"]] = {}
        self.force_resync_book_ids: set[str] = set()
        self.collection_sync_feeds: set[str] = set()
        self._updating_library_widgets = False
        self._updating_collection_widgets = False
        self._recent_collection_toggles: set[str] = set()
        self._collection_state_icons: dict[str, QIcon] = {}
        self._expanded_collection_feeds: set[str] = set()
        self._full_load_active = False
        self._full_load_queue: list[str] = []
        self._full_load_seen: set[str] = set()
        self._full_load_dialog: QProgressDialog | None = None
        self._full_load_processed = 0
        self._kindle_expanded_paths: set[str] = set()
        self._kindle_selected_paths: set[str] = set()
        self._preferred_collection_feed: str | None = None
        self._preferred_collection_chain: list[str] = []

        self.workspace_input = QLineEdit(str(Path.home() / ".hearth"))
        self.download_dir_input = QLineEdit(str(Path.home() / "Downloads"))

        self.feed_input = QLineEdit("")
        self.auth_mode_combo = QComboBox()
        self.auth_mode_combo.addItems(["none", "basic", "bearer"])
        self.auth_username_input = QLineEdit("")
        self.auth_password_input = QLineEdit("")
        self.auth_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_bearer_input = QLineEdit("")
        self.auth_bearer_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.transport_combo = QComboBox()
        self.transport_combo.addItems(["auto", "usb", "mtp"])
        self.kindle_root_input = QLineEdit("")

        self.desired_output_combo = QComboBox()
        self.desired_output_combo.addItems(["auto", "mobi"])
        self.kcc_command_input = QLineEdit("")
        self.kcc_device_input = QComboBox()
        self.kcc_device_input.setEditable(False)
        self.kcc_manga_default_checkbox = QCheckBox(
            "Manga as default reading direction"
        )
        self.kcc_manga_force_checkbox = QCheckBox("Force manga reading direction")
        self.kcc_autolevel_checkbox = QCheckBox("Enable KCC autolevel")
        self.kcc_autolevel_checkbox.setChecked(True)
        self.kcc_preserve_margin_input = QSpinBox()
        self.kcc_preserve_margin_input.setRange(0, 100)
        self.kcc_preserve_margin_input.setSuffix("%")
        self.kcc_preserve_margin_input.setValue(0)
        self.calibre_command_input = QLineEdit("")
        self.convert_pdfs_checkbox = QCheckBox("Convert PDFs with Calibre")
        self.max_conversion_workers_input = QSpinBox()
        self.max_conversion_workers_input.setRange(1, 8)
        self.max_conversion_workers_input.setValue(1)

        self.probe_kindle_button = QPushButton("Reconnect Kindle")

        self.load_catalog_button = QPushButton("Reload Library")
        self.refresh_collection_button = QPushButton("Reload Collection")
        self.sync_selected_button = QPushButton("Sync to Kindle")
        self.select_all_library_button = QPushButton("Select All")
        self.clear_library_selection_button = QPushButton("Clear")
        self.force_checkbox = QCheckBox("Force re-sync")

        self.refresh_files_button = QPushButton("Refresh Files")
        self.download_selected_file_button = QPushButton("Download Selected")
        self.delete_selected_file_button = QPushButton("Delete Selected")

        self.status_label = QLabel("Idle")
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.kindle_status_label = QLabel("Kindle: probing...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Idle")
        self.progress_bar.setFixedWidth(420)

        self._kcc_profiles = [
            ("Auto (detected)", "auto"),
            ("Kindle 1", "K1"),
            ("Kindle 2", "K2"),
            ("Kindle Keyboard/Touch", "K34"),
            ("Kindle 5/7/8/10", "K578"),
            ("Kindle DX/DXG", "KDX"),
            ("Kindle Paperwhite 1/2", "KPW"),
            ("Kindle Paperwhite 5/Signature Edition", "KPW5"),
            ("Kindle Voyage", "KV"),
            ("Kindle Oasis 2/3", "KO"),
            ("Kindle 11", "K11"),
            ("Kindle Scribe 1/2", "KS"),
        ]
        for label, code in self._kcc_profiles:
            self.kcc_device_input.addItem(label, code)

        self.collections_tree = QTreeWidget()
        self.collections_tree.setColumnCount(1)
        self.collections_tree.setHeaderLabels(["Collections"])
        self.collections_tree.setUniformRowHeights(True)

        self.library_table = QTableWidget(0, 5)
        self.library_table.setHorizontalHeaderLabels(
            ["", "Title", "Author", "Type", "Status"]
        )
        self.library_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.kindle_files_tree = QTreeWidget()
        self.kindle_files_tree.setColumnCount(3)
        self.kindle_files_tree.setHeaderLabels(["Name", "Type", "Size"])
        self.kindle_files_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        self.reset_general_button = QPushButton("reset")
        self.reset_opds_button = QPushButton("reset")
        self.reset_kindle_button = QPushButton("reset")
        self.regenerate_metadata_button = QPushButton("Regenerate Metadata")
        self.reset_book_conversion_button = QPushButton("reset")
        self.reset_comic_conversion_button = QPushButton("reset")
        self.reset_all_button = QPushButton("Reset All Settings")
        self.remove_from_kindle_button = QPushButton("Remove Hearth Folder from Kindle")

        self.tabs = QTabWidget()
        self.library_tab = QWidget()
        self.kindle_files_tab = QWidget()
        self.settings_tab = QWidget()
        self.logs_tab = QWidget()

        self._configure_layout()
        self._connect_events()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(120)
        self.poll_timer.timeout.connect(self._poll_pending_tasks)
        self.poll_timer.start()

        self._load_settings_from_file()
        self._probe_kindle()

    def _configure_layout(self) -> None:
        central = QWidget()
        root = QVBoxLayout()

        header = QHBoxLayout()
        header.addWidget(self.probe_kindle_button)
        header.addSpacing(12)
        header.addWidget(self.kindle_status_label)
        header.addStretch(1)

        progress_container = QWidget()
        progress_layout = QVBoxLayout()
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.progress_bar)
        progress_container.setLayout(progress_layout)
        progress_container.setFixedWidth(420)
        header.addWidget(progress_container)

        self._configure_library_tab()
        self._configure_kindle_files_tab()
        self._configure_settings_tab()
        self._configure_logs_tab()

        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.kindle_files_tab, "Kindle Files")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.logs_tab, "Logs")

        root.addLayout(header)
        root.addWidget(self.tabs, stretch=1)

        central.setLayout(root)
        self.setCentralWidget(central)

    def _configure_library_tab(self) -> None:
        layout = QVBoxLayout()

        controls = QHBoxLayout()
        controls.addWidget(self.load_catalog_button)
        controls.addWidget(self.refresh_collection_button)
        controls.addWidget(self.select_all_library_button)
        controls.addWidget(self.clear_library_selection_button)
        controls.addStretch(1)

        tree_header = self.collections_tree.header()
        if tree_header is not None:
            tree_header.setStretchLastSection(True)

        library_header = self.library_table.horizontalHeader()
        if library_header is not None:
            library_header.setStretchLastSection(False)
            library_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            library_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            library_header.setSectionResizeMode(
                2,
                QHeaderView.ResizeMode.ResizeToContents,
            )
            library_header.setSectionResizeMode(
                3,
                QHeaderView.ResizeMode.ResizeToContents,
            )
            library_header.setSectionResizeMode(
                4,
                QHeaderView.ResizeMode.ResizeToContents,
            )
            self.library_table.setColumnWidth(0, 34)
            self.library_table.setColumnWidth(2, 180)
            self.library_table.setColumnWidth(3, 90)
            self.library_table.setColumnWidth(4, 170)
        library_vertical = self.library_table.verticalHeader()
        if library_vertical is not None:
            library_vertical.setVisible(False)

        layout.addLayout(controls)

        split = QHBoxLayout()
        split.addWidget(self.collections_tree, stretch=2)
        split.addWidget(self.library_table, stretch=4)
        layout.addLayout(split)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.force_checkbox)
        footer.addWidget(self.sync_selected_button)
        layout.addLayout(footer)

        self.library_tab.setLayout(layout)

    def _configure_kindle_files_tab(self) -> None:
        layout = QVBoxLayout()

        controls = QGridLayout()
        controls.addWidget(QLabel("Download folder"), 0, 0)
        controls.addWidget(self.download_dir_input, 0, 1, 1, 3)
        controls.addWidget(self.refresh_files_button, 1, 0)
        controls.addWidget(self.download_selected_file_button, 1, 1)
        controls.addWidget(self.delete_selected_file_button, 1, 2)

        files_header = self.kindle_files_tree.header()
        if files_header is not None:
            files_header.setStretchLastSection(True)
        self.kindle_files_tree.setColumnWidth(0, 560)
        self.kindle_files_tree.setColumnWidth(1, 90)
        self.kindle_files_tree.setColumnWidth(2, 140)
        self.kindle_files_tree.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection
        )

        layout.addLayout(controls)
        layout.addWidget(self.kindle_files_tree)

        self.kindle_files_tab.setLayout(layout)

    def _configure_settings_tab(self) -> None:
        tab_layout = QVBoxLayout()

        general_group = QGroupBox("General")
        general_layout = QGridLayout()
        general_layout.addWidget(QLabel("Workspace"), 0, 0)
        general_layout.addWidget(self.workspace_input, 0, 1, 1, 3)
        general_layout.addWidget(QLabel("Download folder"), 1, 0)
        general_layout.addWidget(self.download_dir_input, 1, 1, 1, 3)
        general_layout.addWidget(self.reset_general_button, 2, 3)
        general_group.setLayout(general_layout)

        opds_group = QGroupBox("OPDS")
        opds_layout = QGridLayout()
        opds_layout.addWidget(QLabel("Feed URL"), 0, 0)
        opds_layout.addWidget(self.feed_input, 0, 1, 1, 3)
        opds_layout.addWidget(QLabel("Auth mode"), 1, 0)
        opds_layout.addWidget(self.auth_mode_combo, 1, 1)
        opds_layout.addWidget(QLabel("Username"), 1, 2)
        opds_layout.addWidget(self.auth_username_input, 1, 3)
        opds_layout.addWidget(QLabel("Password"), 2, 0)
        opds_layout.addWidget(self.auth_password_input, 2, 1)
        opds_layout.addWidget(QLabel("Bearer token"), 2, 2)
        opds_layout.addWidget(self.auth_bearer_input, 2, 3)
        opds_layout.addWidget(self.reset_opds_button, 3, 3)
        opds_group.setLayout(opds_layout)

        kindle_group = QGroupBox("Kindle")
        kindle_layout = QGridLayout()
        kindle_layout.addWidget(QLabel("Transport"), 0, 0)
        kindle_layout.addWidget(self.transport_combo, 0, 1)
        kindle_layout.addWidget(QLabel("Mount"), 0, 2)
        kindle_layout.addWidget(self.kindle_root_input, 0, 3)
        kindle_layout.addWidget(self.regenerate_metadata_button, 1, 2)
        kindle_layout.addWidget(self.reset_kindle_button, 1, 3)
        kindle_group.setLayout(kindle_layout)

        conversion_group = QGroupBox("Conversion")
        conversion_layout = QGridLayout()
        conversion_layout.addWidget(QLabel("Concurrent conversions"), 0, 0)
        conversion_layout.addWidget(self.max_conversion_workers_input, 0, 1)

        books_label = QLabel("Books")
        books_label.setStyleSheet("font-weight: bold; color: #2a2f6a;")
        conversion_layout.addWidget(books_label, 3, 0, 1, 4)
        conversion_layout.addWidget(QLabel("Preferred output"), 4, 0)
        conversion_layout.addWidget(self.desired_output_combo, 4, 1)
        conversion_layout.addWidget(QLabel("Additional Calibre arguments"), 5, 0)
        conversion_layout.addWidget(self.calibre_command_input, 5, 1, 1, 3)
        self.calibre_command_input.setPlaceholderText(
            "e.g. --mobi-keep-original-images --some-flag OR /path/to/ebook-convert"
        )
        conversion_layout.addWidget(self.convert_pdfs_checkbox, 6, 0, 1, 2)
        conversion_layout.addWidget(self.reset_book_conversion_button, 7, 3)

        books_divider = QFrame()
        books_divider.setFrameShape(QFrame.Shape.HLine)
        books_divider.setFrameShadow(QFrame.Shadow.Sunken)
        conversion_layout.addWidget(books_divider, 8, 0, 1, 4)

        comics_label = QLabel("Comics")
        comics_label.setStyleSheet("font-weight: bold; color: #2a2f6a;")
        conversion_layout.addWidget(comics_label, 9, 0, 1, 4)
        conversion_layout.addWidget(QLabel("Additional KCC arguments"), 10, 0)
        conversion_layout.addWidget(self.kcc_command_input, 10, 1, 1, 3)
        self.kcc_command_input.setPlaceholderText(
            "e.g. --manga-style --quality=80 OR /path/to/kcc-c2e"
        )
        conversion_layout.addWidget(QLabel("KCC device"), 11, 0)
        conversion_layout.addWidget(self.kcc_device_input, 11, 1)
        conversion_layout.addWidget(self.kcc_manga_default_checkbox, 12, 0, 1, 2)
        conversion_layout.addWidget(self.kcc_manga_force_checkbox, 12, 2, 1, 2)
        conversion_layout.addWidget(self.kcc_autolevel_checkbox, 13, 0, 1, 2)
        conversion_layout.addWidget(QLabel("Preserve margin"), 14, 0)
        conversion_layout.addWidget(self.kcc_preserve_margin_input, 14, 1)
        conversion_layout.addWidget(self.reset_comic_conversion_button, 15, 3)
        conversion_group.setLayout(conversion_layout)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.remove_from_kindle_button)
        footer.addWidget(self.reset_all_button)

        tab_layout.addWidget(general_group)
        tab_layout.addWidget(opds_group)
        tab_layout.addWidget(kindle_group)
        tab_layout.addWidget(conversion_group)
        tab_layout.addLayout(footer)
        tab_layout.addStretch(1)

        # Make the settings panel scrollable to avoid huge window sizes.
        inner = QWidget()
        inner.setLayout(tab_layout)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        outer = QVBoxLayout()
        outer.addWidget(scroll)
        self.settings_tab.setLayout(outer)

    def _configure_logs_tab(self) -> None:
        layout = QVBoxLayout()
        layout.addWidget(self.log_output)
        self.logs_tab.setLayout(layout)

    def _connect_events(self) -> None:
        self.probe_kindle_button.clicked.connect(self._probe_kindle)
        self.auth_mode_combo.currentTextChanged.connect(self._update_auth_visibility)

        self.load_catalog_button.clicked.connect(self._load_root_collections)
        self.refresh_collection_button.clicked.connect(
            self._refresh_selected_collection
        )
        self.sync_selected_button.clicked.connect(self._sync_selected)
        self.select_all_library_button.clicked.connect(self._select_all_library_rows)
        self.clear_library_selection_button.clicked.connect(
            self._clear_library_selection
        )
        self.collections_tree.currentItemChanged.connect(self._on_collection_changed)
        self.collections_tree.itemExpanded.connect(self._on_collection_expanded)
        self.collections_tree.itemCollapsed.connect(self._on_collection_collapsed)
        self.collections_tree.itemChanged.connect(self._on_collection_item_changed)
        self.library_table.itemChanged.connect(self._on_library_item_changed)
        self.library_table.customContextMenuRequested.connect(
            self._show_library_context_menu
        )

        self.refresh_files_button.clicked.connect(self._refresh_kindle_files)
        self.download_selected_file_button.clicked.connect(
            self._download_selected_kindle_files
        )
        self.delete_selected_file_button.clicked.connect(
            self._delete_selected_kindle_files
        )
        self.kindle_files_tree.customContextMenuRequested.connect(
            self._show_kindle_files_context_menu
        )

        self.reset_general_button.clicked.connect(self._reset_general)
        self.reset_opds_button.clicked.connect(self._reset_opds)
        self.reset_kindle_button.clicked.connect(self._reset_kindle)
        self.regenerate_metadata_button.clicked.connect(self._regenerate_metadata_file)
        self.reset_book_conversion_button.clicked.connect(self._reset_book_conversion)
        self.reset_comic_conversion_button.clicked.connect(self._reset_comic_conversion)
        self.reset_all_button.clicked.connect(self._reset_all)
        self.remove_from_kindle_button.clicked.connect(
            self._remove_from_kindle_and_reset
        )
        self.force_checkbox.toggled.connect(self._on_force_resync_toggled)

        self._connect_autosave()

    def _connect_autosave(self) -> None:
        for combo in [
            self.auth_mode_combo,
            self.transport_combo,
            self.desired_output_combo,
            self.kcc_device_input,
        ]:
            combo.currentTextChanged.connect(self._save_settings_to_file)

        for checkbox in [
            self.convert_pdfs_checkbox,
            self.kcc_manga_default_checkbox,
            self.kcc_manga_force_checkbox,
            self.kcc_autolevel_checkbox,
        ]:
            checkbox.toggled.connect(self._save_settings_to_file)

        self.max_conversion_workers_input.valueChanged.connect(
            self._save_settings_to_file
        )
        self.kcc_preserve_margin_input.valueChanged.connect(self._save_settings_to_file)

        for edit in [
            self.workspace_input,
            self.download_dir_input,
            self.feed_input,
            self.auth_username_input,
            self.auth_password_input,
            self.auth_bearer_input,
            self.kindle_root_input,
            self.calibre_command_input,
            self.kcc_command_input,
        ]:
            edit.editingFinished.connect(self._save_settings_to_file)

    def _load_settings_from_file(self) -> None:
        settings = Settings.load(self.settings_path)

        self.feed_input.setText(settings.opds_url)
        self.auth_mode_combo.setCurrentText(settings.auth_mode)
        self.auth_username_input.setText(settings.auth_username)
        self.auth_password_input.setText(settings.auth_password)
        self.auth_bearer_input.setText(settings.auth_bearer_token)
        self.transport_combo.setCurrentText(settings.kindle_transport)
        self.kindle_root_input.setText(settings.kindle_mount)

        output = settings.desired_output
        if output not in {"auto", "mobi"}:
            output = "auto"
        self.desired_output_combo.setCurrentText(output)

        self.kcc_command_input.setText(settings.kcc_command)
        self._set_kcc_device_ui(settings.kcc_device)
        self.kcc_manga_default_checkbox.setChecked(settings.kcc_manga_default)
        self.kcc_manga_force_checkbox.setChecked(settings.kcc_manga_force)
        self.kcc_autolevel_checkbox.setChecked(settings.kcc_autolevel)
        self.kcc_preserve_margin_input.setValue(
            max(0, min(100, int(settings.kcc_preserve_margin_percent)))
        )
        self.convert_pdfs_checkbox.setChecked(settings.convert_pdfs)
        self.max_conversion_workers_input.setValue(
            max(1, min(8, int(settings.max_conversion_workers)))
        )
        self.calibre_command_input.setText(settings.calibre_command)
        self.collection_sync_feeds = {
            feed.strip()
            for feed in settings.collection_sync_feeds
            if isinstance(feed, str) and feed.strip()
        }

        self._update_auth_visibility()
        self._log(f"Loaded settings from {self.settings_path}")

    def _save_settings_to_file(self) -> None:
        settings = self._current_settings()
        settings.save(self.settings_path)

    def _current_settings(self) -> Settings:
        auth_mode = cast(
            Literal["none", "basic", "bearer"],
            self.auth_mode_combo.currentText(),
        )
        transport = cast(
            Literal["auto", "usb", "mtp"],
            self.transport_combo.currentText(),
        )
        desired_output = cast(
            Literal["auto", "epub", "mobi"],
            self.desired_output_combo.currentText(),
        )

        return Settings(
            opds_url=self.feed_input.text().strip(),
            auth_mode=auth_mode,
            auth_username=self.auth_username_input.text().strip(),
            auth_password=self.auth_password_input.text(),
            auth_bearer_token=self.auth_bearer_input.text().strip(),
            kindle_transport=transport,
            kindle_mount=self.kindle_root_input.text().strip(),
            desired_output=desired_output,
            kcc_command=self.kcc_command_input.text().strip(),
            kcc_device=self._selected_kcc_device_code(),
            kcc_manga_default=self.kcc_manga_default_checkbox.isChecked(),
            kcc_manga_force=self.kcc_manga_force_checkbox.isChecked(),
            kcc_autolevel=self.kcc_autolevel_checkbox.isChecked(),
            kcc_preserve_margin_percent=self.kcc_preserve_margin_input.value(),
            calibre_command=self.calibre_command_input.text().strip(),
            convert_pdfs=self.convert_pdfs_checkbox.isChecked(),
            max_conversion_workers=self.max_conversion_workers_input.value(),
            collection_sync_feeds=sorted(self.collection_sync_feeds),
        )

    def _reset_general(self) -> None:
        self.workspace_input.setText(str(Path.home() / ".hearth"))
        self.download_dir_input.setText(str(Path.home() / "Downloads"))
        self._save_settings_to_file()

    def _reset_opds(self) -> None:
        self.feed_input.setText("")
        self.auth_mode_combo.setCurrentText("none")
        self.auth_username_input.setText("")
        self.auth_password_input.setText("")
        self.auth_bearer_input.setText("")
        self.collection_sync_feeds.clear()
        self._update_auth_visibility()
        self._save_settings_to_file()

    def _reset_kindle(self) -> None:
        self.transport_combo.setCurrentText("auto")
        self.kindle_root_input.setText("")
        self._save_settings_to_file()

    def _metadata_remote_name(self) -> str:
        return "Hearth/.hearth_metadata.json"

    def _metadata_path_for(self, device: KindleDevice) -> Path:
        return device.documents_dir / "Hearth" / ".hearth_metadata.json"

    def _load_metadata_from_device(self, device: KindleDevice) -> dict[str, SyncRecord]:
        if device.transport != "mtp":
            records = load_metadata(self._metadata_path_for(device))
            try:
                device_files = {
                    entry.path for entry in device.list_files() if not entry.is_dir
                }
            except (OSError, RuntimeError):
                return records
            return merge_device_files_into_records(records, device_files)

        with tempfile.TemporaryDirectory(prefix="hearth-metadata-") as temp_dir:
            temp_path = Path(temp_dir) / ".hearth_metadata.json"
            try:
                device.download_file(self._metadata_remote_name(), temp_path)
            except (OSError, RuntimeError):
                records = {}
            else:
                records = load_metadata(temp_path)

        try:
            device_files = {
                entry.path for entry in device.list_files() if not entry.is_dir
            }
        except (OSError, RuntimeError):
            return records
        return merge_device_files_into_records(records, device_files)

    def _save_metadata_to_device(
        self,
        device: KindleDevice,
        records: dict[str, SyncRecord],
    ) -> Path:
        metadata_path = self._metadata_path_for(device)
        if device.transport != "mtp":
            save_metadata(metadata_path, records)
            return metadata_path

        with tempfile.TemporaryDirectory(prefix="hearth-metadata-") as temp_dir:
            temp_path = Path(temp_dir) / ".hearth_metadata.json"
            save_metadata(temp_path, records)
            device.put_file(temp_path, self._metadata_remote_name())
        return metadata_path

    def _regenerate_metadata_file(self) -> None:
        if self.is_busy:
            return

        if self.connected_device is None:
            QMessageBox.warning(
                self,
                "No Kindle",
                "Connect and probe a Kindle before regenerating metadata.",
            )
            return

        decision = QMessageBox.question(
            self,
            "Regenerate Metadata",
            (
                "This rebuilds Hearth metadata from currently tracked records "
                "and files detected on the Kindle. Continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if decision != QMessageBox.StandardButton.Yes:
            return

        self._set_busy("Regenerating metadata...")
        snapshot = self.connected_device
        future = self.worker_pool.submit(
            self._regenerate_metadata_worker,
            snapshot,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_regenerate_metadata_finished,
                action_name="regenerate metadata",
            )
        )

    def _regenerate_metadata_worker(
        self,
        snapshot: DeviceSnapshot,
    ) -> MetadataRebuildResult:
        device = KindleDevice(
            transport=snapshot.transport,
            root=snapshot.root,
        )
        metadata_path = self._metadata_path_for(device)
        records = self._load_metadata_from_device(device)
        before_count = len(records)

        device_files: set[str] = set()
        for entry in device.list_files():
            if entry.is_dir:
                continue
            device_files.add(entry.path)
            device_files.add(entry.name)
            normalized = entry.path.strip("/")
            if normalized:
                device_files.add(normalized)
                if normalized.startswith("documents/"):
                    relative = normalized.removeprefix("documents/")
                    device_files.add(relative)

        reconciled = reconcile_on_device(records, device_files)
        cleaned = {
            key: record
            for key, record in reconciled.items()
            if record.desired or record.on_device
        }
        self._save_metadata_to_device(device, cleaned)
        return MetadataRebuildResult(
            path=metadata_path,
            before_count=before_count,
            after_count=len(cleaned),
        )

    def _on_regenerate_metadata_finished(self, result: object) -> None:
        if not isinstance(result, MetadataRebuildResult):
            raise TypeError("Unexpected metadata rebuild result")

        self._refresh_device_library_state()
        current = self.collections_tree.currentItem()
        if current is not None:
            feed_url = current.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(feed_url, str):
                self._populate_library_table(
                    self._books_for_feed_subtree(feed_url),
                    allow_book_toggles=self._should_allow_book_toggles(feed_url),
                )
        self._refresh_all_collection_visuals()
        self._save_collection_cache()
        self._log(
            "Regenerated metadata: "
            f"{result.path} ({result.before_count} -> {result.after_count})"
        )
        QMessageBox.information(
            self,
            "Metadata Regenerated",
            (
                "Hearth metadata has been rebuilt.\n"
                f"Path: {result.path}\n"
                f"Records: {result.before_count} -> {result.after_count}"
            ),
        )

    def _reset_book_conversion(self) -> None:
        self.desired_output_combo.setCurrentText("auto")
        self.calibre_command_input.setText("")
        self.convert_pdfs_checkbox.setChecked(False)
        self.max_conversion_workers_input.setValue(1)
        self._save_settings_to_file()

    def _reset_comic_conversion(self) -> None:
        self.kcc_command_input.setText("")
        self._set_kcc_device_ui("auto")
        self.kcc_manga_default_checkbox.setChecked(False)
        self.kcc_manga_force_checkbox.setChecked(False)
        self.kcc_autolevel_checkbox.setChecked(True)
        self.kcc_preserve_margin_input.setValue(0)
        self._save_settings_to_file()

    def _reset_conversion(self) -> None:
        self._reset_book_conversion()
        self._reset_comic_conversion()

    def _remove_from_kindle_and_reset(self) -> None:
        if self.is_busy:
            return

        if self.connected_device is None:
            QMessageBox.warning(
                self,
                "No Kindle",
                "Connect and probe a Kindle before removing Hearth data.",
            )
            return

        dialog = QMessageBox(self)
        dialog.setWindowTitle("Remove Hearth Folder from Kindle")
        dialog.setText(
            "This will remove the Hearth folder from your Kindle. "
            "Local Hearth settings on this computer will not be changed."
        )
        dialog.setInformativeText("Are you sure you want to continue?")
        delete_button = dialog.addButton(
            "Delete",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() != delete_button:
            return

        snapshot = self.connected_device
        self._set_busy("Removing Hearth data from Kindle...")
        future = self.worker_pool.submit(
            self._remove_from_kindle_worker,
            snapshot,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_remove_from_kindle_finished,
                action_name="remove from kindle",
            )
        )

    def _remove_from_kindle_worker(
        self,
        snapshot: DeviceSnapshot,
    ) -> RemoveFromKindleResult:
        device = KindleDevice(
            transport=snapshot.transport,
            root=snapshot.root,
        )
        removed = device.delete_file("Hearth")
        return RemoveFromKindleResult(removed=removed)

    def _on_remove_from_kindle_finished(self, result: object) -> None:
        if not isinstance(result, RemoveFromKindleResult):
            raise TypeError("Unexpected remove-from-kindle result")
        self._refresh_kindle_files(force=True)
        self._refresh_device_library_state()
        self._refresh_all_collection_visuals()
        message = "Hearth folder removed from Kindle."
        if not result.removed:
            message = "Hearth folder was not found on Kindle."
        self._log(message)
        QMessageBox.information(
            self,
            "Remove Hearth Folder from Kindle",
            message,
        )

    def _selected_kcc_device_code(self) -> str:
        idx = self.kcc_device_input.currentIndex()
        if idx < 0:
            return "auto"
        value = self.kcc_device_input.itemData(idx)
        if isinstance(value, str) and value.strip():
            return value
        return "auto"

    def _set_kcc_device_ui(self, value: str) -> None:
        needle = (value or "auto").strip().upper()
        aliases = {
            "KOA": "KO",
            "KPW34": "KPW",
            "K57": "K578",
            "K810": "K578",
        }
        needle = aliases.get(needle, needle)
        if needle == "AUTO":
            needle = "auto"
        for idx in range(self.kcc_device_input.count()):
            code = self.kcc_device_input.itemData(idx)
            if isinstance(code, str) and code.upper() == needle.upper():
                self.kcc_device_input.setCurrentIndex(idx)
                return
        self.kcc_device_input.setCurrentIndex(0)

    def _reset_all(self) -> None:
        self._reset_general()
        self._reset_opds()
        self._reset_kindle()
        self._reset_book_conversion()
        self._reset_comic_conversion()
        self._log("Reset all settings groups")

    def _update_auth_visibility(self) -> None:
        mode = self.auth_mode_combo.currentText()
        is_basic = mode == "basic"
        is_bearer = mode == "bearer"

        self.auth_username_input.setEnabled(is_basic)
        self.auth_password_input.setEnabled(is_basic)
        self.auth_bearer_input.setEnabled(is_bearer)
        self._save_settings_to_file()

    def _probe_kindle(self) -> None:
        if self.is_busy:
            return

        settings = self._current_settings()
        self._set_busy("Probing Kindle...")
        future = self.worker_pool.submit(self._probe_kindle_worker, settings)
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_probe_kindle_result,
                action_name="probe kindle",
            )
        )

    def _probe_kindle_worker(
        self,
        settings: Settings,
    ) -> DeviceSnapshot | None:
        device = KindleDevice.detect(
            preferred=settings.kindle_transport,
            root_hint=settings.kindle_mount,
        )
        if device is None:
            return None
        return DeviceSnapshot(transport=device.transport, root=device.root)

    def _on_probe_kindle_result(self, result: object) -> None:
        if result is None:
            self.connected_device = None
            if self.transport_combo.currentText() == "mtp":
                self.kindle_status_label.setText("Kindle: MTP backend unavailable")
                self._log("MTP selected, but go-mtpx bridge is unavailable")
            else:
                self.kindle_status_label.setText("Kindle: not connected")
            self.kindle_files_tree.clear()
            self._log("Kindle not detected")
        elif isinstance(result, DeviceSnapshot):
            self.connected_device = result
            self.kindle_status_label.setText(
                f"Kindle: {result.transport} at {result.root}"
            )
            self._log(f"Detected Kindle at {result.root}")
            self._log_kindle_probe_details(result)
            self._refresh_kindle_files(force=True)
        else:
            raise TypeError("Unexpected probe result")

        self._startup_load_root_collections()

    def _startup_load_root_collections(self) -> None:
        if self.startup_catalog_attempted:
            return

        feed_url = self.feed_input.text().strip()
        if not feed_url:
            self.startup_catalog_attempted = True
            return

        self.startup_catalog_attempted = True
        self._load_root_collections(silent=True)

    def _load_root_collections(self, silent: bool = False) -> None:
        feed_url = self.feed_input.text().strip()
        if not feed_url:
            QMessageBox.warning(
                self,
                "Missing OPDS URL",
                "Set an OPDS URL in Settings first.",
            )
            return

        current = self.collections_tree.currentItem()
        if current is not None:
            data = current.data(0, Qt.ItemDataRole.UserRole)
            self._preferred_collection_feed = data if isinstance(data, str) else None
            chain: list[str] = []
            cursor: QTreeWidgetItem | None = current
            while cursor is not None:
                node_feed = cursor.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(node_feed, str):
                    chain.append(node_feed)
                cursor = cursor.parent()
            self._preferred_collection_chain = list(reversed(chain))
        else:
            self._preferred_collection_feed = None
            self._preferred_collection_chain = []

        self._cancel_full_library_load()

        self.books_by_feed = {}
        self.book_rows_by_id = {}
        self.loaded_feeds = set()
        self.loading_feeds = set()
        self.tree_item_by_feed = {}
        self.feed_children = {}
        self.feed_parent = {}
        self.pending_book_actions = {}
        self.collections_tree.clear()
        self.library_table.setRowCount(0)

        self._refresh_device_library_state()

        self._request_feed_load(
            feed_url=feed_url,
            is_root=True,
            show_errors=not silent,
        )

    def _refresh_selected_collection(self) -> None:
        current = self.collections_tree.currentItem()
        if current is None:
            self._load_root_collections(silent=True)
            return

        feed_url = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str) or not feed_url.strip():
            self._load_root_collections(silent=True)
            return

        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=True,
            force_reload=True,
        )

    def _request_feed_load(
        self,
        feed_url: str,
        is_root: bool,
        show_errors: bool,
        force_reload: bool = False,
    ) -> None:
        if feed_url in self.loading_feeds:
            return

        if feed_url in self.loaded_feeds and not is_root and not force_reload:
            current = self.collections_tree.currentItem()
            if current is not None:
                current_feed = current.data(0, Qt.ItemDataRole.UserRole)
                if current_feed == feed_url:
                    self._populate_library_table(
                        self._books_for_feed_subtree(feed_url),
                        allow_book_toggles=self._should_allow_book_toggles(feed_url),
                    )
            return

        settings = self._current_settings()
        if not self.is_busy:
            status = "Loading root collections..." if is_root else "Loading shelf..."
            self._set_busy(status)

        self.loading_feeds.add(feed_url)
        future = self.worker_pool.submit(
            self._fetch_feed_worker,
            settings,
            feed_url,
            is_root,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_feed_loaded,
                action_name=f"load feed {feed_url}",
                show_errors=show_errors,
            )
        )

    def _fetch_feed_worker(
        self,
        settings: Settings,
        feed_url: str,
        is_root: bool,
    ) -> FeedLoadResult:
        session = OPDSSession(settings)
        client = OPDSClient(session)

        entries = client.fetch_entries(feed_url)
        children: list[CollectionRow] = []
        books: list[LibraryRow] = []
        seen_children: set[str] = set()

        for entry in entries:
            for link in entry.links:
                if link.is_acquisition() and link.href:
                    books.append(
                        LibraryRow(
                            id=entry.id,
                            title=entry.title,
                            author=entry.author,
                            download_url=urllib.parse.urljoin(
                                feed_url,
                                link.href,
                            ),
                            declared_type=link.type,
                            source_feed=feed_url,
                        )
                    )
                    continue

                if not link.is_navigation() or not link.href:
                    continue

                child_feed_url = urllib.parse.urljoin(feed_url, link.href)
                if child_feed_url in seen_children:
                    continue
                seen_children.add(child_feed_url)
                children.append(
                    CollectionRow(
                        feed_url=child_feed_url,
                        title=link.title or entry.title or child_feed_url,
                    )
                )

        # For feeds that are category/group parents, only show child collections.
        if children:
            books = []

        return FeedLoadResult(
            feed_url=feed_url,
            is_root=is_root,
            children=children,
            books=books,
        )

    def _on_feed_loaded(self, result: object) -> None:
        if not isinstance(result, FeedLoadResult):
            raise TypeError("Unexpected feed load result")

        self.loading_feeds.discard(result.feed_url)
        self.loaded_feeds.add(result.feed_url)
        previous_rows = self.books_by_feed.get(result.feed_url, [])
        for old in previous_rows:
            feeds = self.book_feeds_by_id.get(old.id)
            if feeds is None:
                continue
            feeds.discard(result.feed_url)
            if not feeds:
                self.book_feeds_by_id.pop(old.id, None)
        self.books_by_feed[result.feed_url] = result.books
        self.feed_children[result.feed_url] = {row.feed_url for row in result.children}
        for row in result.children:
            self.feed_parent[row.feed_url] = result.feed_url
        for book in result.books:
            self.book_rows_by_id[book.id] = book
            self.book_feeds_by_id.setdefault(book.id, set()).add(result.feed_url)
        cache_key = self._cache_key_for_feed(result.feed_url)
        if self._is_auto_sync_feed(result.feed_url):
            # Keep previously seen IDs for tracked books so removals can be
            # detected when OPDS no longer returns those entries.
            tracked_ids = {
                record.id
                for record in self.metadata_records.values()
                if record.desired or record.on_device
            }
            current_ids = set(self._cache_ids_for_feed(result.feed_url))
            previous_ids = set(self.collection_book_cache.get(cache_key, []))
            preserved_ids = previous_ids.intersection(tracked_ids)
            merged_ids = sorted(preserved_ids.union(current_ids))
            if merged_ids:
                self.collection_book_cache[cache_key] = merged_ids
            else:
                self.collection_book_cache.pop(cache_key, None)
            self._save_collection_cache()

        if result.is_root:
            self._populate_root_collections(result.children)
            if self._is_auto_sync_feed(result.feed_url):
                self._queue_adds_for_feed(result.feed_url)
                self._queue_removals_for_feed(result.feed_url)
            self._refresh_all_collection_visuals()
            self._expand_synced_collection_paths()
            self._start_full_library_load(result.feed_url)
            self._log(f"Loaded {len(result.children)} top-level collections")
            return

        parent_item = self.tree_item_by_feed.get(result.feed_url)
        if parent_item is not None:
            self._replace_placeholder_with_children(
                parent_item,
                result.children,
            )
            self._try_restore_preferred_collection_selection()

        current = self.collections_tree.currentItem()
        if current is not None:
            current_feed = current.data(0, Qt.ItemDataRole.UserRole)
            if current_feed == result.feed_url:
                self._populate_library_table(
                    self._books_for_feed_subtree(result.feed_url),
                    allow_book_toggles=self._should_allow_book_toggles(
                        result.feed_url,
                    ),
                )

        if self._is_auto_sync_feed(result.feed_url):
            self._queue_adds_for_feed(result.feed_url)
            self._queue_removals_for_feed(result.feed_url)
            for child_feed in self.feed_children.get(result.feed_url, set()):
                self._request_feed_load(
                    feed_url=child_feed,
                    is_root=False,
                    show_errors=False,
                )
            if current is not None:
                current_feed = current.data(0, Qt.ItemDataRole.UserRole)
                if current_feed == result.feed_url:
                    self._populate_library_table(
                        self._books_for_feed_subtree(result.feed_url),
                        allow_book_toggles=self._should_allow_book_toggles(
                            result.feed_url,
                        ),
                    )

        self._refresh_collection_visual(result.feed_url)
        self._expand_synced_collection_paths()
        parent_feed = self.feed_parent.get(result.feed_url)
        while parent_feed:
            self._refresh_collection_visual(parent_feed)
            parent_feed = self.feed_parent.get(parent_feed)

        if self._full_load_active:
            self._full_load_processed += 1
            for child in result.children:
                if child.feed_url not in self._full_load_seen:
                    self._full_load_queue.append(child.feed_url)
            self._update_full_library_progress()
            self._kick_full_library_load()
            self._finish_full_library_load_if_done()

        self._log(f"Loaded {len(result.children)} sub-collections")

    def _populate_root_collections(self, rows: list[CollectionRow]) -> None:
        previous_current_feed: str | None = self._preferred_collection_feed
        current = self.collections_tree.currentItem()
        if current is not None:
            data = current.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, str):
                previous_current_feed = data

        self.collections_tree.clear()
        self.tree_item_by_feed = {}

        for row in rows:
            item = QTreeWidgetItem([row.title])
            item.setData(0, Qt.ItemDataRole.UserRole, row.feed_url)
            item.setData(0, Qt.ItemDataRole.UserRole + 20, row.title)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self.collections_tree.addTopLevelItem(item)
            self.tree_item_by_feed[row.feed_url] = item
            if row.feed_url in self._expanded_collection_feeds:
                self.collections_tree.expandItem(item)

        if previous_current_feed and previous_current_feed in self.tree_item_by_feed:
            self.collections_tree.setCurrentItem(
                self.tree_item_by_feed[previous_current_feed],
            )
            self._preferred_collection_feed = None
        elif previous_current_feed:
            self._preferred_collection_feed = previous_current_feed
        elif self.collections_tree.topLevelItemCount() > 0:
            self.collections_tree.setCurrentItem(
                self.collections_tree.topLevelItem(0),
            )
        self._expand_synced_collection_paths()
        self._try_restore_preferred_collection_selection()

    def _replace_placeholder_with_children(
        self,
        parent_item: QTreeWidgetItem,
        rows: list[CollectionRow],
    ) -> None:
        parent_item.takeChildren()
        for row in rows:
            child_item = QTreeWidgetItem([row.title])
            child_item.setData(0, Qt.ItemDataRole.UserRole, row.feed_url)
            child_item.setData(0, Qt.ItemDataRole.UserRole + 20, row.title)
            child_item.setFlags(
                child_item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            child_item.setCheckState(0, Qt.CheckState.Unchecked)
            parent_item.addChild(child_item)
            self.tree_item_by_feed[row.feed_url] = child_item
            if row.feed_url in self._expanded_collection_feeds:
                self.collections_tree.expandItem(child_item)

    def _on_collection_expanded(self, item: QTreeWidgetItem) -> None:
        feed_url = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return
        self._expanded_collection_feeds.add(feed_url)
        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=True,
        )

    def _on_collection_collapsed(self, item: QTreeWidgetItem) -> None:
        feed_url = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(feed_url, str):
            self._expanded_collection_feeds.discard(feed_url)

    def _collection_cache_path(self) -> Path:
        if (
            self.connected_device is not None
            and self.connected_device.transport == "usb"
        ):
            return (
                self.connected_device.root
                / "documents"
                / "Hearth"
                / ".hearth_collection_cache.json"
            )
        workspace = Path(self.workspace_input.text().strip() or ".hearth").expanduser()
        return workspace / ".hearth_collection_cache.json"

    def _collection_cache_remote_name(self) -> str:
        return "Hearth/.hearth_collection_cache.json"

    def _cache_key_for_feed(self, feed_url: str) -> str:
        base_url = self.feed_input.text().strip()
        parsed = urllib.parse.urlparse(feed_url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        if not base_url:
            return path

        base_parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme == base_parsed.scheme and parsed.netloc == base_parsed.netloc:
            return path
        return feed_url

    def _cache_ids_for_feed(self, feed_url: str) -> list[str]:
        rows = self.books_by_feed.get(feed_url, [])
        tracked = {
            record.id
            for record in self.metadata_records.values()
            if record.desired or record.on_device
        }
        if not tracked:
            return []
        ids = [row.id for row in rows if row.id in tracked]
        return sorted(set(ids))

    def _load_collection_cache(self) -> None:
        if (
            self.connected_device is not None
            and self.connected_device.transport == "mtp"
        ):
            with tempfile.TemporaryDirectory(
                prefix="hearth-collection-cache-"
            ) as temp_dir:
                path = Path(temp_dir) / ".hearth_collection_cache.json"
                device = KindleDevice(
                    transport=self.connected_device.transport,
                    root=self.connected_device.root,
                )
                try:
                    device.download_file(self._collection_cache_remote_name(), path)
                except (OSError, RuntimeError):
                    self.collection_book_cache = {}
                    return
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self.collection_book_cache = {}
                    return
        else:
            path = self._collection_cache_path()
            if not path.exists():
                self.collection_book_cache = {}
                return
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.collection_book_cache = {}
                return
        loaded: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for feed, ids in raw.items():
                if not isinstance(feed, str) or not isinstance(ids, list):
                    continue
                loaded[feed] = [str(book_id) for book_id in ids if str(book_id).strip()]
        self.collection_book_cache = loaded

    def _save_collection_cache(self) -> None:
        payload: dict[str, list[str]] = {}

        for record in self.metadata_records.values():
            if not (record.desired or record.on_device):
                continue
            for feed in record.collection_feeds:
                key = self._cache_key_for_feed(feed)
                payload.setdefault(key, []).append(record.id)

        if payload:
            payload = {key: sorted(set(ids)) for key, ids in payload.items() if ids}
            self.collection_book_cache = payload
        else:
            # Fallback: keep only selected collection keys if metadata has no feed links.
            slim: dict[str, list[str]] = {}
            for feed in self.collection_sync_feeds:
                key = self._cache_key_for_feed(feed)
                ids = self.collection_book_cache.get(key, [])
                if ids:
                    slim[key] = sorted(set(ids))
            payload = slim
            self.collection_book_cache = slim

        if self.connected_device is None or self.connected_device.transport != "mtp":
            path = self._collection_cache_path()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except OSError as exc:
                self._log(f"collection-cache save failed: {exc}")
            return

        try:
            with tempfile.TemporaryDirectory(
                prefix="hearth-collection-cache-"
            ) as temp_dir:
                temp_path = Path(temp_dir) / ".hearth_collection_cache.json"
                temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                device = KindleDevice(
                    transport=self.connected_device.transport,
                    root=self.connected_device.root,
                )
                device.put_file(
                    temp_path,
                    self._collection_cache_remote_name(),
                )
        except (OSError, RuntimeError) as exc:
            self._log(f"collection-cache mtp upload failed: {exc}")

    def _start_full_library_load(self, root_feed_url: str) -> None:
        if self._full_load_active:
            return
        self._full_load_active = True
        top_level = sorted(self.feed_children.get(root_feed_url, set()))
        self._full_load_queue = top_level
        self._full_load_seen = set()
        self._full_load_processed = 0
        dialog = QProgressDialog(
            "Loading full library metadata...",
            "Skip Remaining",
            0,
            100,
            self,
        )
        dialog.setWindowTitle("Loading Library")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self._cancel_full_library_load)
        dialog.show()
        self._full_load_dialog = dialog
        self._kick_full_library_load()
        self._update_full_library_progress()
        self._finish_full_library_load_if_done()

    def _cancel_full_library_load(self) -> None:
        self._full_load_active = False
        self._full_load_queue = []
        if self._full_load_dialog is not None:
            self._full_load_dialog.setLabelText(
                "Skipping remaining lazy-loaded collections"
            )
            self._full_load_dialog.close()
            self._full_load_dialog = None

    def _kick_full_library_load(self) -> None:
        if not self._full_load_active:
            return
        max_inflight = 3
        while self._full_load_queue and len(self.loading_feeds) < max_inflight:
            candidate = self._full_load_queue.pop(0)
            if candidate in self._full_load_seen:
                continue
            self._full_load_seen.add(candidate)
            self._request_feed_load(
                feed_url=candidate,
                is_root=False,
                show_errors=False,
            )

    def _update_full_library_progress(self) -> None:
        if not self._full_load_active:
            return
        dialog = self._full_load_dialog
        if dialog is None:
            return
        total = max(
            1,
            self._full_load_processed
            + len(self._full_load_queue)
            + len(self.loading_feeds),
        )
        value = int((self._full_load_processed / total) * 100)
        dialog.setValue(value)
        dialog.setLabelText(
            f"Loading full library... loaded {self._full_load_processed}, queued {len(self._full_load_queue)}"
        )

    def _finish_full_library_load_if_done(self) -> None:
        if not self._full_load_active:
            return
        if self._full_load_queue or self.loading_feeds:
            return
        self._full_load_active = False
        if self._full_load_dialog is not None:
            self._full_load_dialog.setValue(100)
            self._full_load_dialog.close()
            self._full_load_dialog = None

    def _try_restore_preferred_collection_selection(self) -> None:
        if not self._preferred_collection_feed:
            return

        item = self.tree_item_by_feed.get(self._preferred_collection_feed)
        if item is None:
            if self._preferred_collection_chain:
                for idx, feed in enumerate(self._preferred_collection_chain):
                    if feed in self.loaded_feeds:
                        continue
                    if idx == 0:
                        self._request_feed_load(
                            feed_url=feed,
                            is_root=False,
                            show_errors=False,
                        )
                        return
                    parent = self._preferred_collection_chain[idx - 1]
                    if parent in self.loaded_feeds:
                        self._request_feed_load(
                            feed_url=feed,
                            is_root=False,
                            show_errors=False,
                        )
                        return
            return

        current = self._preferred_collection_feed
        while current:
            parent_feed = self.feed_parent.get(current)
            if not parent_feed:
                break
            self._expanded_collection_feeds.add(parent_feed)
            parent_item = self.tree_item_by_feed.get(parent_feed)
            if parent_item is not None:
                self.collections_tree.expandItem(parent_item)
            current = parent_feed

        self._expanded_collection_feeds.add(self._preferred_collection_feed)
        self.collections_tree.setCurrentItem(item)
        self._preferred_collection_feed = None
        self._preferred_collection_chain = []

    def _expand_synced_collection_paths(self) -> None:
        for feed in self.collection_sync_feeds:
            self._expanded_collection_feeds.add(feed)
            current: str | None = feed
            while current:
                item = self.tree_item_by_feed.get(current)
                if item is not None:
                    self.collections_tree.expandItem(item)
                current = self.feed_parent.get(current)

        for feed in list(self._expanded_collection_feeds):
            item = self.tree_item_by_feed.get(feed)
            if item is not None:
                self.collections_tree.expandItem(item)

    def _on_collection_changed(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        _ = previous
        if current is None:
            self.library_table.setRowCount(0)
            return

        feed_url = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            self.library_table.setRowCount(0)
            return

        if feed_url in self.loaded_feeds:
            if self._is_auto_sync_feed(feed_url):
                self._queue_adds_for_feed(feed_url)
                self._queue_removals_for_feed(feed_url)
            self._populate_library_table(
                self._books_for_feed_subtree(feed_url),
                allow_book_toggles=self._should_allow_book_toggles(feed_url),
            )
            return

        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=True,
        )

    def _refresh_device_library_state(self) -> None:
        self.metadata_records = {}
        self.device_files = set()
        self.device_on_book_ids = set()
        self.collection_book_cache = {}

        if self.connected_device is None:
            return

        device = KindleDevice(
            transport=self.connected_device.transport,
            root=self.connected_device.root,
        )
        self.metadata_records = self._load_metadata_from_device(device)
        self._load_collection_cache()
        try:
            entries = device.list_files()
        except (OSError, RuntimeError):
            entries = []

        for entry in entries:
            if entry.is_dir:
                continue
            self.device_files.add(entry.name)
            self.device_files.add(entry.path)
            normalized = entry.path.strip("/")
            if normalized:
                self.device_files.add(normalized)
                if normalized.startswith("documents/"):
                    self.device_files.add(normalized.removeprefix("documents/"))

        for book_id, record in self.metadata_records.items():
            if record.device_filename in self.device_files:
                self.device_on_book_ids.add(book_id)

    def _book_visual_state(self, book_id: str) -> str:
        action = self.pending_book_actions.get(book_id)
        if action == "add":
            return "pending_add"
        if action == "remove":
            return "pending_remove"
        if book_id in self.device_on_book_ids:
            return "on_device"
        return "off_device"

    def _book_status_display(
        self,
        row: LibraryRow,
    ) -> tuple[str, Qt.CheckState, Literal["add", "remove"] | None]:
        force_resync = self.force_checkbox.isChecked()
        action = self.pending_book_actions.get(row.id)
        if row.id in self.force_resync_book_ids:
            return ("Re-Sync", Qt.CheckState.PartiallyChecked, "add")
        if action == "add":
            return ("Will Add", Qt.CheckState.PartiallyChecked, "add")
        if action == "remove":
            return ("Will Delete", Qt.CheckState.PartiallyChecked, "remove")
        if row.deleted_from_server and row.id in self.device_on_book_ids:
            return ("Deleted from Server", Qt.CheckState.PartiallyChecked, "remove")
        if row.deleted_from_server:
            return ("Deleted from Server", Qt.CheckState.Unchecked, None)
        if row.id in self.device_on_book_ids:
            if force_resync:
                return ("Will Add", Qt.CheckState.PartiallyChecked, "add")
            return ("On Device", Qt.CheckState.Checked, None)
        return ("Not On Device", Qt.CheckState.Unchecked, None)

    def _on_force_resync_toggled(self, _checked: bool) -> None:
        """Refresh current library visuals when force mode is toggled."""
        current = self.collections_tree.currentItem()
        if current is None:
            return
        feed_url = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return
        self._populate_library_table(
            self._books_for_feed_subtree(feed_url),
            allow_book_toggles=self._should_allow_book_toggles(feed_url),
        )
        self._refresh_collection_visual(feed_url)

    def _collection_subtree_feeds(self, root_feed: str) -> set[str]:
        feeds: set[str] = set()
        stack = [root_feed]
        while stack:
            feed = stack.pop()
            if feed in feeds:
                continue
            feeds.add(feed)
            stack.extend(self.feed_children.get(feed, set()))
        return feeds

    def _books_for_feed_subtree(self, root_feed: str) -> list[LibraryRow]:
        by_id: dict[str, LibraryRow] = {}
        subtree_feeds = self._collection_subtree_feeds(root_feed)
        visible_ids: set[str] = set()
        for feed in subtree_feeds:
            for row in self.books_by_feed.get(feed, []):
                by_id[row.id] = row
                self.book_rows_by_id[row.id] = row
                visible_ids.add(row.id)

        cached_missing_ids: set[str] = set()
        for feed in subtree_feeds:
            cached_ids = self.collection_book_cache.get(
                self._cache_key_for_feed(feed),
                [],
            )
            for cached_id in cached_ids:
                if cached_id not in visible_ids:
                    cached_missing_ids.add(cached_id)

        for record in self.metadata_records.values():
            if not record.desired and not record.on_device:
                continue
            linked_feeds = set(record.collection_feeds)
            if record.id in cached_missing_ids:
                linked_feeds.update(
                    {
                        feed
                        for feed in subtree_feeds
                        if record.id
                        in self.collection_book_cache.get(
                            self._cache_key_for_feed(feed),
                            [],
                        )
                    }
                )
            if not linked_feeds:
                continue
            if not any(feed in subtree_feeds for feed in linked_feeds):
                continue
            if record.id in by_id:
                continue
            by_id[record.id] = LibraryRow(
                id=record.id,
                title=record.title,
                author="",
                download_url="",
                declared_type="",
                source_feed=next(iter(linked_feeds)),
                deleted_from_server=True,
            )
        return list(by_id.values())

    def _should_allow_book_toggles(self, feed_url: str) -> bool:
        return not self._is_auto_sync_feed(feed_url)

    def _collection_sync_counts(self, feed_url: str) -> tuple[int, int, int]:
        if feed_url not in self.collection_sync_feeds:
            return (0, 0, len(self._books_for_feed_subtree(feed_url)))

        rows = self._books_for_feed_subtree(feed_url)
        add_count = 0
        remove_count = 0
        for row in rows:
            action = self.pending_book_actions.get(row.id)
            if action == "add":
                add_count += 1
                continue
            if action == "remove":
                remove_count += 1
                continue
            if row.deleted_from_server and row.id in self.device_on_book_ids:
                remove_count += 1
                continue
            if row.id not in self.device_on_book_ids and not row.deleted_from_server:
                add_count += 1
        return (add_count, remove_count, len(rows))

    def _collection_device_count(self, feed_url: str) -> int:
        count = 0
        for row in self._books_for_feed_subtree(feed_url):
            if row.id in self.device_on_book_ids:
                count += 1
        return count

    def _is_auto_sync_feed(self, feed_url: str) -> bool:
        current: str | None = feed_url
        while current:
            if current in self.collection_sync_feeds:
                return True
            current = self.feed_parent.get(current)
        return False

    def _queue_adds_for_feed(self, feed_url: str) -> None:
        for row in self._books_for_feed_subtree(feed_url):
            if row.deleted_from_server:
                continue
            if row.id in self.device_on_book_ids:
                continue
            self.pending_book_actions[row.id] = "add"
        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=False,
        )

    def _queue_removals_for_feed(self, feed_url: str) -> None:
        for row in self._books_for_feed_subtree(feed_url):
            if not row.deleted_from_server:
                continue
            record = self.metadata_records.get(row.id)
            tracked = row.id in self.device_on_book_ids or (
                record is not None and (record.desired or record.on_device)
            )
            if tracked:
                self.pending_book_actions[row.id] = "remove"

    def _clear_pending_for_feed(self, feed_url: str) -> None:
        for row in self._books_for_feed_subtree(feed_url):
            self.pending_book_actions.pop(row.id, None)

    def _collection_visual_state(
        self,
        feed_url: str,
    ) -> tuple[Qt.CheckState, Literal["add", "remove", "mixed", "auto"] | None]:
        if feed_url not in self.collection_sync_feeds:
            return (Qt.CheckState.Unchecked, None)

        add_count, remove_count, _ = self._collection_sync_counts(feed_url)
        if add_count or remove_count:
            return (Qt.CheckState.PartiallyChecked, "mixed")
        return (Qt.CheckState.Checked, None)

    def _collection_color(
        self,
        tone: Literal["add", "remove", "mixed", "auto"] | None,
    ) -> QBrush:
        _ = tone
        return QBrush(self._default_text_color())

    def _collection_state_icon(
        self, tone: str | None, check_state: Qt.CheckState
    ) -> QIcon:
        _ = (tone, check_state)
        return QIcon()

    def _refresh_collection_visual(self, feed_url: str) -> None:
        item = self.tree_item_by_feed.get(feed_url)
        if item is None:
            return
        # If the user manually toggled this collection recently, respect that
        # and avoid overriding the user's choice from background refreshes.
        if feed_url in self._recent_collection_toggles:
            return

        check_state, tone = self._collection_visual_state(feed_url)
        # Update visuals without triggering itemChanged handler
        self._updating_collection_widgets = True
        self.collections_tree.blockSignals(True)
        try:
            item.setCheckState(0, check_state)
            base_title = item.data(0, Qt.ItemDataRole.UserRole + 20)
            if not isinstance(base_title, str) or not base_title:
                base_title = item.text(0)
                item.setData(0, Qt.ItemDataRole.UserRole + 20, base_title)
            add_count, remove_count, total_count = self._collection_sync_counts(
                feed_url,
            )
            device_count = self._collection_device_count(feed_url)
            item.setText(
                0,
                (
                    f"{base_title} "
                    f"(+{add_count}/-{remove_count} {device_count}/{total_count})"
                ),
            )
            item.setData(0, Qt.ItemDataRole.UserRole + 1, tone or "")
            item.setData(
                0,
                Qt.ItemDataRole.UserRole + 2,
                (
                    "checked"
                    if check_state == Qt.CheckState.Checked
                    else (
                        "unchecked"
                        if check_state == Qt.CheckState.Unchecked
                        else f"partial_{tone or 'mixed'}"
                    )
                ),
            )
            item.setForeground(0, self._collection_color(tone))
            # set a plus/minus icon for partially-checked states
            item.setIcon(0, self._collection_state_icon(tone, check_state))
            # ensure children visuals refresh too
            for child_feed in self.feed_children.get(feed_url, set()):
                child_item = self.tree_item_by_feed.get(child_feed)
                if child_item is not None:
                    child_item.setForeground(0, self._collection_color(tone))
                    child_item.setIcon(
                        0, self._collection_state_icon(tone, check_state)
                    )
        finally:
            self.collections_tree.blockSignals(False)
            self._updating_collection_widgets = False

    def _refresh_all_collection_visuals(self) -> None:
        for feed in self.tree_item_by_feed:
            self._refresh_collection_visual(feed)

    def _apply_row_color(
        self,
        row_idx: int,
        action: Literal["add", "remove"] | None,
    ) -> None:
        dark_mode = self._is_dark_mode()
        color = self._default_text_color()
        if action == "add":
            color = QColor("#6dd48a" if dark_mode else "#1b7f3a")
        elif action == "remove":
            color = QColor("#ff8b8b" if dark_mode else "#b42318")
        brush = QBrush(color)
        for col in [1, 2, 3, 4]:
            item = self.library_table.item(row_idx, col)
            if item is not None:
                item.setForeground(brush)

    def _is_dark_mode(self) -> bool:
        palette = self.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        return window_color.lightness() < 128

    def _default_text_color(self) -> QColor:
        palette = self.palette()
        return palette.color(QPalette.ColorRole.Text)

    def _human_readable_type(self, row: LibraryRow) -> str:
        declared = (row.declared_type or "").lower()
        if "epub" in declared:
            return "EPUB"
        if "pdf" in declared:
            return "PDF"
        if "mobi" in declared:
            return "MOBI"
        if "cbz" in declared or "cbr" in declared or "comic" in declared:
            return "Comic"
        if "zip" in declared:
            return "ZIP"

        parsed_path = urllib.parse.urlparse(row.download_url).path.lower()
        suffix = Path(parsed_path).suffix
        suffix_map = {
            ".epub": "EPUB",
            ".pdf": "PDF",
            ".mobi": "MOBI",
            ".azw": "AZW",
            ".azw3": "AZW3",
            ".kfx": "KFX",
            ".cbz": "Comic",
            ".cbr": "Comic",
            ".cbt": "Comic",
            ".cb7": "Comic",
            ".zip": "ZIP",
        }
        return suffix_map.get(
            suffix, declared.split("/")[-1].upper() if declared else "Unknown"
        )

    def _on_library_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_library_widgets:
            return
        if item.column() != 0:
            return

        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        book_id = payload.get("id")
        if not isinstance(book_id, str):
            return

        previous = item.data(Qt.ItemDataRole.UserRole + 2)
        if previous == "on_device":
            self.pending_book_actions[book_id] = "remove"
            self.force_resync_book_ids.discard(book_id)
        elif previous == "off_device":
            self.pending_book_actions[book_id] = "add"
            self.force_resync_book_ids.discard(book_id)
        else:
            self.pending_book_actions.pop(book_id, None)
            self.force_resync_book_ids.discard(book_id)

        current = self.collections_tree.currentItem()
        if current is not None:
            feed_url = current.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(feed_url, str):
                if not self._should_allow_book_toggles(feed_url):
                    return
                self._populate_library_table(
                    self._books_for_feed_subtree(feed_url),
                    allow_book_toggles=self._should_allow_book_toggles(feed_url),
                )
                self._refresh_collection_visual(feed_url)
                parent_feed = self.feed_parent.get(feed_url)
                while parent_feed:
                    self._refresh_collection_visual(parent_feed)
                    parent_feed = self.feed_parent.get(parent_feed)

    def _selected_library_book_id(self) -> str | None:
        row_idx = self.library_table.currentRow()
        if row_idx < 0:
            return None
        payload_item = self.library_table.item(row_idx, 0)
        if payload_item is None:
            return None
        payload = payload_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return None
        book_id = payload.get("id")
        if not isinstance(book_id, str) or not book_id:
            return None
        return book_id

    def _refresh_current_library_view(self) -> None:
        current = self.collections_tree.currentItem()
        if current is None:
            return
        feed_url = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return
        self._populate_library_table(
            self._books_for_feed_subtree(feed_url),
            allow_book_toggles=self._should_allow_book_toggles(feed_url),
        )
        self._refresh_collection_visual(feed_url)
        parent_feed = self.feed_parent.get(feed_url)
        while parent_feed:
            self._refresh_collection_visual(parent_feed)
            parent_feed = self.feed_parent.get(parent_feed)

    def _queue_book_add_on_sync(self, book_id: str) -> None:
        self.force_resync_book_ids.discard(book_id)
        self.pending_book_actions[book_id] = "add"

    def _queue_book_delete_on_sync(self, book_id: str) -> None:
        self.force_resync_book_ids.discard(book_id)
        self.pending_book_actions[book_id] = "remove"

    def _queue_book_force_resync(self, book_id: str) -> None:
        self.pending_book_actions.pop(book_id, None)
        self.force_resync_book_ids.add(book_id)

    def _show_library_context_menu(self, pos) -> None:
        if self.is_busy:
            return
        item = self.library_table.itemAt(pos)
        if item is None:
            return

        row_idx = item.row()
        if row_idx < 0:
            return
        self.library_table.selectRow(row_idx)

        book_id = self._selected_library_book_id()
        if book_id is None:
            return
        row = self.book_rows_by_id.get(book_id)
        if row is None:
            payload_item = self.library_table.item(row_idx, 0)
            if payload_item is None:
                return
            payload = payload_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict):
                return
            row = LibraryRow(
                id=book_id,
                title=str(payload.get("title", "")),
                author=str(payload.get("author", "")),
                download_url=str(payload.get("download_url", "")),
                declared_type=str(payload.get("declared_type", "")),
                source_feed="",
                deleted_from_server=False,
            )

        menu = QMenu(self)
        has_download = bool(row.download_url) and not row.deleted_from_server
        on_device = book_id in self.device_on_book_ids
        pending_action = self.pending_book_actions.get(book_id)
        is_resync = book_id in self.force_resync_book_ids

        if has_download and (on_device or pending_action == "add" or is_resync):
            force_resync_action = menu.addAction("Force Re-sync on Sync")
            force_resync_action.triggered.connect(
                lambda _checked=False, bid=book_id: (
                    self._queue_book_force_resync(bid),
                    self._refresh_current_library_view(),
                )
            )

        if has_download and not on_device and not is_resync and pending_action != "add":
            add_action = menu.addAction("Add on Sync")
            add_action.triggered.connect(
                lambda _checked=False, bid=book_id: (
                    self._queue_book_add_on_sync(bid),
                    self._refresh_current_library_view(),
                )
            )

        if on_device and pending_action != "remove":
            delete_action = menu.addAction("Delete on Sync")
            delete_action.triggered.connect(
                lambda _checked=False, bid=book_id: (
                    self._queue_book_delete_on_sync(bid),
                    self._refresh_current_library_view(),
                )
            )

        if pending_action is not None or is_resync:
            clear_action = menu.addAction("Clear Pending Action")
            clear_action.triggered.connect(
                lambda _checked=False, bid=book_id: (
                    self.pending_book_actions.pop(bid, None),
                    self.force_resync_book_ids.discard(bid),
                    self._refresh_current_library_view(),
                )
            )

        if menu.isEmpty():
            return

        menu.exec(self.library_table.viewport().mapToGlobal(pos))

    def _on_collection_item_changed(
        self,
        item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if column != 0:
            return
        if self._updating_collection_widgets:
            return
        feed_url = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return
        state = item.checkState(0)
        if state == Qt.CheckState.PartiallyChecked:
            state = Qt.CheckState.Checked
            item.setCheckState(0, state)
        # Avoid recursive signals while we update children
        self._updating_collection_widgets = True
        try:
            if state == Qt.CheckState.Checked:
                self.collection_sync_feeds.add(feed_url)
                self._queue_adds_for_feed(feed_url)
                self._queue_removals_for_feed(feed_url)
                # start loading children so their books are discovered and queued
                for child_feed in self.feed_children.get(feed_url, set()):
                    self._request_feed_load(
                        feed_url=child_feed,
                        is_root=False,
                        show_errors=False,
                    )
            elif state == Qt.CheckState.Unchecked:
                # clear pending for subtree and clear flags
                self._clear_pending_for_feed(feed_url)
                for child_feed in self._collection_subtree_feeds(feed_url):
                    self.collection_sync_feeds.discard(child_feed)
                self.collection_sync_feeds.discard(feed_url)
            else:
                # ignore other states
                pass
            # mark this feed as recently toggled by the user so visual refreshes
            # won't immediately overwrite their choice; clear after 1.5s
            try:
                self._recent_collection_toggles.add(feed_url)
                QTimer.singleShot(
                    1500, lambda f=feed_url: self._recent_collection_toggles.discard(f)
                )
            except Exception:
                pass
        finally:
            self._updating_collection_widgets = False

        self._save_settings_to_file()
        self._refresh_collection_visual(feed_url)
        parent_feed = self.feed_parent.get(feed_url)
        while parent_feed:
            self._refresh_collection_visual(parent_feed)
            parent_feed = self.feed_parent.get(parent_feed)

        current = self.collections_tree.currentItem()
        if current is not None:
            current_feed = current.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(
                current_feed, str
            ) and current_feed in self._collection_subtree_feeds(feed_url):
                self._populate_library_table(
                    self._books_for_feed_subtree(current_feed),
                    allow_book_toggles=self._should_allow_book_toggles(
                        current_feed,
                    ),
                )

    def _populate_library_table(
        self,
        rows: list[LibraryRow],
        allow_book_toggles: bool = True,
    ) -> None:
        self._refresh_device_library_state()
        self._updating_library_widgets = True
        self.library_table.blockSignals(True)
        self.library_table.setColumnHidden(0, not allow_book_toggles)

        try:
            self.library_table.setRowCount(len(rows))
            for idx, row in enumerate(rows):
                check_item = QTableWidgetItem("")
                check_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                if allow_book_toggles and not row.deleted_from_server:
                    check_item.setFlags(
                        check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    )
                check_item.setData(
                    Qt.ItemDataRole.UserRole,
                    {
                        "id": row.id,
                        "title": row.title,
                        "author": row.author,
                        "download_url": row.download_url,
                        "declared_type": row.declared_type,
                    },
                )

                status_text, check_state, action = self._book_status_display(row)
                check_item.setCheckState(check_state)
                check_item.setData(Qt.ItemDataRole.UserRole + 1, action or "")
                check_item.setData(
                    Qt.ItemDataRole.UserRole + 2,
                    self._book_visual_state(row.id),
                )

                self.library_table.setItem(idx, 0, check_item)
                self.library_table.setItem(idx, 1, QTableWidgetItem(row.title))
                self.library_table.setItem(idx, 2, QTableWidgetItem(row.author or ""))
                self.library_table.setItem(
                    idx,
                    3,
                    QTableWidgetItem(self._human_readable_type(row)),
                )
                self.library_table.setItem(idx, 4, QTableWidgetItem(status_text))
                self._apply_row_color(idx, action)
        finally:
            self.library_table.blockSignals(False)
            self._updating_library_widgets = False

    def _select_all_library_rows(self) -> None:
        current = self.collections_tree.currentItem()
        if current is None:
            return
        feed_url = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return

        self.collection_sync_feeds.add(feed_url)
        for row in self._books_for_feed_subtree(feed_url):
            self.force_resync_book_ids.discard(row.id)
        self._queue_adds_for_feed(feed_url)
        self._queue_removals_for_feed(feed_url)
        self._save_settings_to_file()
        self._populate_library_table(
            self._books_for_feed_subtree(feed_url),
            allow_book_toggles=self._should_allow_book_toggles(feed_url),
        )
        self._refresh_collection_visual(feed_url)

    def _clear_library_selection(self) -> None:
        current = self.collections_tree.currentItem()
        if current is None:
            return
        feed_url = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return

        self.collection_sync_feeds.discard(feed_url)
        for row in self._books_for_feed_subtree(feed_url):
            self.force_resync_book_ids.discard(row.id)
        self._clear_pending_for_feed(feed_url)
        self._save_settings_to_file()
        self._populate_library_table(
            self._books_for_feed_subtree(feed_url),
            allow_book_toggles=self._should_allow_book_toggles(feed_url),
        )
        self._refresh_collection_visual(feed_url)

    def _planned_sync_actions(
        self,
        force_resync: bool = False,
    ) -> tuple[list[SyncItem], list[str]]:
        add_by_id: dict[str, SyncItem] = {}
        remove_ids: set[str] = set()

        for book_id, action in self.pending_book_actions.items():
            if force_resync:
                continue
            if action == "remove":
                remove_ids.add(book_id)
                continue
            row = self.book_rows_by_id.get(book_id)
            if row is None or row.deleted_from_server or not row.download_url:
                continue
            source_feeds = sorted(self.book_feeds_by_id.get(row.id, set()))
            if not source_feeds and row.source_feed:
                source_feeds = [row.source_feed]
            add_by_id[row.id] = SyncItem(
                id=row.id,
                title=row.title,
                author=row.author,
                download_url=row.download_url,
                declared_type=row.declared_type,
                source_feeds=source_feeds,
            )

        for book_id in self.force_resync_book_ids:
            row = self.book_rows_by_id.get(book_id)
            if row is None or row.deleted_from_server or not row.download_url:
                continue
            source_feeds = sorted(self.book_feeds_by_id.get(row.id, set()))
            if not source_feeds and row.source_feed:
                source_feeds = [row.source_feed]
            add_by_id[row.id] = SyncItem(
                id=row.id,
                title=row.title,
                author=row.author,
                download_url=row.download_url,
                declared_type=row.declared_type,
                source_feeds=source_feeds,
            )

        selected_scope_feeds: set[str] = set()
        for feed in self.collection_sync_feeds:
            selected_scope_feeds.update(self._collection_subtree_feeds(feed))

        available_selected_ids: set[str] = set()
        for feed in selected_scope_feeds:
            for row in self._books_for_feed_subtree(feed):
                if row.deleted_from_server:
                    if (not force_resync) and row.id in self.device_on_book_ids:
                        remove_ids.add(row.id)
                    continue
                available_selected_ids.add(row.id)
                if (not force_resync) and row.id in self.device_on_book_ids:
                    continue
                if not row.download_url:
                    continue
                source_feeds = sorted(self.book_feeds_by_id.get(row.id, set()))
                if not source_feeds and row.source_feed:
                    source_feeds = [row.source_feed]
                add_by_id[row.id] = SyncItem(
                    id=row.id,
                    title=row.title,
                    author=row.author,
                    download_url=row.download_url,
                    declared_type=row.declared_type,
                    source_feeds=source_feeds,
                )

        if not force_resync:
            for record in self.metadata_records.values():
                if not (record.desired or record.on_device):
                    continue
                if not record.collection_feeds:
                    continue
                if not any(
                    feed in selected_scope_feeds for feed in record.collection_feeds
                ):
                    continue
                if record.id in available_selected_ids:
                    continue
                remove_ids.add(record.id)

        for record_id in remove_ids:
            add_by_id.pop(record_id, None)

        return (list(add_by_id.values()), sorted(remove_ids))

    def _sync_selected(self) -> None:
        if self.is_busy:
            return

        if self.connected_device is None:
            QMessageBox.warning(
                self,
                "No Kindle",
                "Connect a Kindle first.",
            )
            return

        self._refresh_device_library_state()
        force_resync = self.force_checkbox.isChecked()
        add_items, remove_ids = self._planned_sync_actions(force_resync=force_resync)
        if not add_items and not remove_ids:
            QMessageBox.information(
                self,
                "Nothing Planned",
                "Toggle one or more books or collections in Library first.",
            )
            return

        settings = self._current_settings()
        workspace = Path(self.workspace_input.text().strip() or ".hearth").expanduser()
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Invalid Workspace",
                f"Unable to create workspace folder:\n{workspace}\n\n{exc}",
            )
            return
        root = self.connected_device.root
        total_actions = len(add_items) + len(remove_ids)

        self.sync_progress_queue = SimpleQueue()
        self._set_busy(
            f"Applying {total_actions} planned changes",
            determinate_total=total_actions,
        )
        future = self.worker_pool.submit(
            self._run_sync_worker,
            settings,
            workspace,
            str(root),
            add_items,
            remove_ids,
            force_resync,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_sync_finished,
                action_name="sync",
            )
        )

    def _run_sync_worker(
        self,
        settings: Settings,
        workspace: Path,
        kindle_root: str,
        add_items: list[SyncItem],
        remove_ids: list[str],
        force_resync: bool,
    ) -> SyncRunSummary:
        if self.sync_progress_queue is not None:
            self.sync_progress_queue.put(
                SyncProgress(
                    current=0,
                    total=max(1, len(add_items) + len(remove_ids)),
                    message=(
                        "[sync-diag] "
                        f"workspace={workspace} exists={workspace.exists()} "
                        f"opds_url={settings.opds_url!r} "
                        f"transport={settings.kindle_transport} "
                        f"kindle_root={kindle_root}"
                    ),
                    is_log=True,
                )
            )
            for idx, item in enumerate(add_items[:5], start=1):
                self.sync_progress_queue.put(
                    SyncProgress(
                        current=0,
                        total=max(1, len(add_items) + len(remove_ids)),
                        message=(
                            "[sync-diag] "
                            f"item#{idx} id={item.id!r} title={item.title!r} "
                            f"url={item.download_url!r} type={item.declared_type!r}"
                        ),
                        is_log=True,
                    )
                )

        session = OPDSSession(settings)
        converters = ConverterManager.from_commands(
            settings.kcc_command,
            settings.kcc_device,
            settings.kcc_manga_default,
            settings.kcc_manga_force,
            settings.kcc_autolevel,
            settings.kcc_preserve_margin_percent,
            settings.calibre_command,
        )
        device = KindleDevice.probe(
            preferred=settings.kindle_transport,
            root_hint=kindle_root,
        )
        manager = SyncManager(
            session=session,
            converters=converters,
            device=device,
            workspace=workspace,
            max_conversion_workers=settings.max_conversion_workers,
            convert_pdfs=settings.convert_pdfs,
        )

        if self.sync_progress_queue is not None:
            kcc_diag = converters.kcc.diagnostics()
            calibre_cmd = converters.calibre.discover_command() or ""
            self.sync_progress_queue.put(
                SyncProgress(
                    current=0,
                    total=max(1, len(add_items) + len(remove_ids)),
                    message=(
                        "[sync-diag] converter "
                        f"kcc_available={kcc_diag.get('command_available')} "
                        f"kcc_command={kcc_diag.get('command')!r} "
                        f"calibre_available={bool(calibre_cmd)} "
                        f"calibre_command={calibre_cmd!r}"
                    ),
                    is_log=True,
                )
            )

        def on_progress(event: SyncProgress) -> None:
            if self.sync_progress_queue is not None:
                self.sync_progress_queue.put(event)

        deleted = 0
        delete_failed = 0
        failed_delete_ids: list[str] = []
        total = len(add_items) + len(remove_ids)
        processed = 0

        for record_id in remove_ids:
            record = self.metadata_records.get(record_id)
            title = record.title if record is not None and record.title else record_id
            if self.sync_progress_queue is not None:
                self.sync_progress_queue.put(
                    SyncProgress(
                        current=processed,
                        total=total,
                        message=f"[{processed + 1}/{total}] deleting: {title}",
                        is_log=True,
                    )
                )
            if manager.mark_deleted_on_device(record_id):
                deleted += 1
            else:
                delete_failed += 1
                failed_delete_ids.append(record_id)
            if self.sync_progress_queue is not None:
                self.sync_progress_queue.put(
                    SyncProgress(
                        current=processed + 0.95,
                        total=total,
                        message=f"[{processed + 1}/{total}] delete complete: {title}",
                        is_log=True,
                    )
                )
            processed += 1

        if add_items:

            def adjusted_progress(event: SyncProgress) -> None:
                adjusted_current = processed + event.current
                adjusted_total = total
                if self.sync_progress_queue is not None:
                    self.sync_progress_queue.put(
                        SyncProgress(
                            current=adjusted_current,
                            total=adjusted_total,
                            message=event.message,
                            is_log=event.is_log,
                        )
                    )

            outcome = manager.sync(
                items=add_items,
                force_resync=force_resync,
                progress_callback=adjusted_progress,
            )
            return SyncRunSummary(
                attempted_add=len(add_items),
                synced=outcome.synced,
                skipped=outcome.skipped,
                deleted=deleted,
                delete_failed=delete_failed,
                failed_delete_ids=failed_delete_ids,
            )

        return SyncRunSummary(
            attempted_add=0,
            synced=0,
            skipped=0,
            deleted=deleted,
            delete_failed=delete_failed,
            failed_delete_ids=failed_delete_ids,
        )

    def _on_sync_finished(self, result: object) -> None:
        if not isinstance(result, SyncRunSummary):
            raise TypeError("Unexpected sync result")
        failed_add = max(0, result.attempted_add - result.synced - result.skipped)
        self._log(
            "Sync complete: "
            f"synced={result.synced}, skipped={result.skipped}, "
            f"deleted={result.deleted}, delete_failed={result.delete_failed}, "
            f"failed_add={failed_add}"
        )
        self.pending_book_actions = {
            book_id: action
            for book_id, action in self.pending_book_actions.items()
            if action == "remove" and book_id in result.failed_delete_ids
        }
        self.force_resync_book_ids.clear()
        self.sync_progress_queue = None
        self._refresh_kindle_files(force=True)
        current = self.collections_tree.currentItem()
        if current is not None:
            feed_url = current.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(feed_url, str):
                self._populate_library_table(
                    self._books_for_feed_subtree(feed_url),
                    allow_book_toggles=self._should_allow_book_toggles(feed_url),
                )
        self._refresh_all_collection_visuals()

        summary = (
            "Sync complete\n\n"
            f"Added: {result.synced}\n"
            f"Deleted: {result.deleted}\n"
            f"Failed: {failed_add + result.delete_failed}\n"
            f"Skipped: {result.skipped}"
        )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Sync Complete")
        dialog.setText(summary)
        continue_button = dialog.addButton(
            "Continue",
            QMessageBox.ButtonRole.AcceptRole,
        )
        eject_button = dialog.addButton(
            "Eject Kindle and Exit",
            QMessageBox.ButtonRole.ActionRole,
        )
        dialog.setDefaultButton(cast(QPushButton, continue_button))
        dialog.exec()
        if dialog.clickedButton() == eject_button:
            if (
                self.connected_device is not None
                and self.connected_device.transport == "mtp"
            ):
                KindleDevice.mtp_backend().close()
            self.close()

        self._save_collection_cache()

    def _refresh_kindle_files(self, force: bool = False) -> None:
        if self.is_busy and not force:
            return

        if self.connected_device is None:
            self.kindle_files_tree.clear()
            return

        if not self.is_busy:
            self._set_busy("Loading Kindle files...")

        future = self.worker_pool.submit(
            self._list_kindle_files_worker,
            self.connected_device.transport,
            str(self.connected_device.root),
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._populate_kindle_files,
                action_name="refresh files",
            )
        )

    def _log_kindle_probe_details(self, snapshot: DeviceSnapshot) -> None:
        device = KindleDevice(
            transport=snapshot.transport,
            root=snapshot.root,
        )
        root = device.root
        docs = device.documents_dir

        self._log(f"[kindle-diag] root exists={root.exists()} dir={root.is_dir()}")
        self._log(
            f"[kindle-diag] documents path={docs} exists={docs.exists()} "
            f"dir={docs.is_dir()}"
        )

        for candidate in device.hearth_dir_candidates():
            self._log(
                f"[kindle-diag] candidate {candidate} " f"exists={candidate.exists()}"
            )

        if snapshot.transport == "mtp":
            mtp_diag = KindleDevice.mtp_backend().diagnostics()
            self._log(f"[kindle-diag] mtp available={mtp_diag['available']}")
            self._log(
                "[kindle-diag] mtp commands: "
                f"detect={mtp_diag['detect_cmd']} "
                f"files={mtp_diag['files_cmd']} "
                f"folders={mtp_diag['folders_cmd']} "
                f"get={mtp_diag['get_cmd']} "
                f"send={mtp_diag['send_cmd']} "
                f"delete={mtp_diag['delete_cmd']}"
            )
            self._log(f"[kindle-diag] mtp install hint={mtp_diag['install_hint']}")

        try:
            entries = sorted(path.name for path in root.iterdir())[:12]
        except OSError as exc:
            self._log(f"[kindle-diag] root list failed: {exc}")
        else:
            self._log(
                f"[kindle-diag] root entries({len(entries)} shown): "
                + ", ".join(entries)
            )

    def _list_kindle_files_worker(
        self,
        transport: str,
        root: str,
    ) -> KindleFilesLoadResult:
        device = KindleDevice(transport=transport, root=Path(root))
        diagnostics: list[str] = []
        diagnostics.append(
            f"transport={transport} root={device.root} "
            f"exists={device.root.exists()}"
        )
        diagnostics.append(
            f"documents={device.documents_dir} "
            f"exists={device.documents_dir.exists()}"
        )

        if transport == "mtp":
            mtp_diag = KindleDevice.mtp_backend().diagnostics()
            diagnostics.append(f"mtp available={mtp_diag['available']}")
            diagnostics.append(
                "mtp commands "
                f"detect={mtp_diag['detect_cmd']} "
                f"files={mtp_diag['files_cmd']} "
                f"folders={mtp_diag['folders_cmd']} "
                f"get={mtp_diag['get_cmd']} "
                f"send={mtp_diag['send_cmd']} "
                f"delete={mtp_diag['delete_cmd']}"
            )

        for candidate in device.hearth_dir_candidates():
            diagnostics.append(f"candidate={candidate} exists={candidate.exists()}")

        rows: list[DeviceFile] = []
        try:
            listed = device.list_files()
        except (OSError, RuntimeError) as exc:
            diagnostics.append(f"list_files failed: {exc}")
            return KindleFilesLoadResult(rows=rows, diagnostics=diagnostics)

        diagnostics.append(f"list_files count={len(listed)}")
        rows = sorted(listed, key=lambda item: item.path.lower())
        return KindleFilesLoadResult(rows=rows, diagnostics=diagnostics)

    def _populate_kindle_files(self, result: object) -> None:
        if not isinstance(result, KindleFilesLoadResult):
            raise TypeError("Unexpected kindle files result")

        for line in result.diagnostics:
            self._log(f"[kindle-diag] {line}")

        self._populate_kindle_files_tree(result.rows)
        self._refresh_device_library_state()
        for feed_url in self.loaded_feeds:
            if self._is_auto_sync_feed(feed_url):
                self._queue_adds_for_feed(feed_url)
                self._queue_removals_for_feed(feed_url)

        current = self.collections_tree.currentItem()
        if current is not None:
            feed_url = current.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(feed_url, str):
                self._populate_library_table(
                    self._books_for_feed_subtree(feed_url),
                    allow_book_toggles=self._should_allow_book_toggles(feed_url),
                )
        self._refresh_all_collection_visuals()

        file_count = len([row for row in result.rows if not row.is_dir])
        dir_count = len([row for row in result.rows if row.is_dir])
        self._log(f"Loaded {file_count} files and {dir_count} folders from Kindle")

    def _populate_kindle_files_tree(self, rows: list[DeviceFile]) -> None:
        self._kindle_expanded_paths = self._tree_expanded_paths(self.kindle_files_tree)
        self._kindle_selected_paths = {
            self._normalize_tree_path(path)
            for path in self._selected_kindle_paths(files_only=False)
            if isinstance(path, str)
        }
        self.kindle_files_tree.clear()
        node_by_path: dict[str, QTreeWidgetItem] = {}

        for entry in rows:
            parts = [segment for segment in entry.path.split("/") if segment]
            if not parts:
                continue

            parent_item: QTreeWidgetItem | None = None
            current_path = ""
            for idx, part in enumerate(parts):
                current_path = f"{current_path}/{part}" if current_path else part
                node = node_by_path.get(current_path)
                is_leaf = idx == len(parts) - 1
                if node is None:
                    node = QTreeWidgetItem([part, "Folder", ""])
                    node.setData(0, Qt.ItemDataRole.UserRole, current_path)
                    node.setData(1, Qt.ItemDataRole.UserRole, False)
                    if parent_item is None:
                        self.kindle_files_tree.addTopLevelItem(node)
                    else:
                        parent_item.addChild(node)
                    node_by_path[current_path] = node

                if is_leaf:
                    is_file = not entry.is_dir
                    node.setText(0, entry.name)
                    node.setText(1, "File" if is_file else "Folder")
                    node.setText(
                        2,
                        self._format_size(entry.size) if is_file else "",
                    )
                    node.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        self._normalize_tree_path(entry.path),
                    )
                    node.setData(1, Qt.ItemDataRole.UserRole, is_file)

                parent_item = node

        for path in self._kindle_expanded_paths:
            node = node_by_path.get(self._normalize_tree_path(path))
            if node is not None:
                node.setExpanded(True)
        for path in self._kindle_selected_paths:
            node = node_by_path.get(self._normalize_tree_path(path))
            if node is not None:
                node.setSelected(True)

    def _tree_expanded_paths(self, tree: QTreeWidget) -> set[str]:
        expanded: set[str] = set()

        def visit(item: QTreeWidgetItem) -> None:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(path, str) and item.isExpanded():
                expanded.add(self._normalize_tree_path(path))
            for idx in range(item.childCount()):
                child = item.child(idx)
                if isinstance(child, QTreeWidgetItem):
                    visit(child)

        for idx in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(idx)
            if isinstance(top, QTreeWidgetItem):
                visit(top)
        return expanded

    @staticmethod
    def _normalize_tree_path(path: str) -> str:
        return path.strip().strip("/")

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size_bytes)
        unit_idx = 0
        while value >= 1024.0 and unit_idx < len(units) - 1:
            value /= 1024.0
            unit_idx += 1
        if unit_idx == 0:
            return f"{int(value)} {units[unit_idx]}"
        return f"{value:.1f} {units[unit_idx]}"

    def _selected_kindle_paths(self, files_only: bool = True) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for item in self.kindle_files_tree.selectedItems():
            is_file = item.data(1, Qt.ItemDataRole.UserRole)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(path, str):
                continue
            if files_only and is_file is not True:
                continue
            if path in seen:
                continue
            seen.add(path)
            names.append(path)
        return names

    def _show_kindle_files_context_menu(self, pos) -> None:
        if self.is_busy:
            return

        item = self.kindle_files_tree.itemAt(pos)
        if item is not None and not item.isSelected():
            self.kindle_files_tree.clearSelection()
            item.setSelected(True)

        selected_files = self._selected_kindle_paths(files_only=True)
        selected_any = self._selected_kindle_paths(files_only=False)
        if not selected_any:
            return

        menu = QMenu(self)
        download_action = menu.addAction("Download Selected")
        download_action.setEnabled(bool(selected_files))
        download_action.triggered.connect(self._download_selected_kindle_files)

        delete_action = menu.addAction("Delete Selected")
        delete_action.setEnabled(bool(selected_any))
        delete_action.triggered.connect(self._delete_selected_kindle_files)

        menu.exec(self.kindle_files_tree.viewport().mapToGlobal(pos))

    def _download_selected_kindle_files(self) -> None:
        if self.is_busy:
            return
        if self.connected_device is None:
            QMessageBox.warning(self, "No Kindle", "No connected Kindle.")
            return

        names = self._selected_kindle_paths(files_only=True)
        if not names:
            QMessageBox.information(
                self,
                "No file selected",
                "Select one or more files in Kindle Files.",
            )
            return

        target_dir = Path(self.download_dir_input.text().strip())
        self._set_busy(f"Downloading {len(names)} files...")
        future = self.worker_pool.submit(
            self._download_files_worker,
            self.connected_device.transport,
            str(self.connected_device.root),
            names,
            str(target_dir),
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_files_downloaded,
                action_name="download files",
            )
        )

    def _download_files_worker(
        self,
        transport: str,
        root: str,
        names: list[str],
        target_dir: str,
    ) -> int:
        device = KindleDevice(transport=transport, root=Path(root))
        destination = Path(target_dir)
        destination.mkdir(parents=True, exist_ok=True)

        copied = 0
        for name in names:
            filename = self._download_filename(name)
            destination_path = self._dedupe_download_path(destination / filename)
            try:
                device.download_file(name, destination_path)
            except (OSError, RuntimeError):
                continue
            else:
                copied += 1
        return copied

    def _download_filename(self, source_path: str) -> str:
        normalized = source_path.strip().rstrip("/")
        candidate = Path(normalized).name
        if not candidate:
            return "download.bin"
        return candidate

    def _dedupe_download_path(self, destination: Path) -> Path:
        if not destination.exists():
            return destination

        stem = destination.stem
        suffix = destination.suffix
        parent = destination.parent
        index = 1
        while True:
            candidate = parent / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def _on_files_downloaded(self, result: object) -> None:
        if not isinstance(result, int):
            raise TypeError("Unexpected download result")
        self._log(f"Downloaded {result} file(s)")

    def _delete_selected_kindle_files(self) -> None:
        if self.is_busy:
            return
        if self.connected_device is None:
            QMessageBox.warning(self, "No Kindle", "No connected Kindle.")
            return

        names = self._selected_kindle_paths(files_only=False)
        if not names:
            QMessageBox.information(
                self,
                "No selection",
                "Select one or more files or folders in Kindle Files.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Delete items",
            f"Delete {len(names)} selected item(s) from Kindle?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(f"Deleting {len(names)} files...")
        future = self.worker_pool.submit(
            self._delete_files_worker,
            self.connected_device.transport,
            str(self.connected_device.root),
            names,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_files_deleted,
                action_name="delete files",
            )
        )

    def _delete_files_worker(
        self,
        transport: str,
        root: str,
        names: list[str],
    ) -> int:
        device = KindleDevice(transport=transport, root=Path(root))
        deleted = 0
        for name in names:
            if device.delete_file(name):
                deleted += 1
        return deleted

    def _on_files_deleted(self, result: object) -> None:
        if not isinstance(result, int):
            raise TypeError("Unexpected delete result")
        self._log(f"Deleted {result} item(s)")
        self._refresh_kindle_files(force=True)

    def _poll_pending_tasks(self) -> None:
        self._drain_sync_progress_events()
        self._animate_busy_status()

        remaining: list[PendingTask] = []
        for task in self.pending_tasks:
            if not task.future.done():
                remaining.append(task)
                continue

            try:
                output = task.future.result()
                task.on_success(output)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                feed = task.action_name.removeprefix("load feed ")
                self.loading_feeds.discard(feed)
                if task.action_name == "sync":
                    self.sync_progress_queue = None
                trace = traceback.format_exc()
                self._log(f"{task.action_name} failed: {exc}")
                self._log(f"{task.action_name} traceback:\n{trace}")
                if task.show_errors:
                    QMessageBox.critical(
                        self,
                        "Operation failed",
                        f"{task.action_name} failed:\n{exc}",
                    )

        self.pending_tasks = remaining
        if self.is_busy and not self.pending_tasks:
            self._set_idle()

    def _drain_sync_progress_events(self) -> None:
        if self.sync_progress_queue is None:
            return

        while True:
            try:
                event = self.sync_progress_queue.get_nowait()
            except Empty:
                break

            if event.total > 0:
                max_value = event.total * 100
                current_value = int(max(0.0, min(event.current, event.total)) * 100)
                self.progress_bar.setRange(0, max_value)
                self.progress_bar.setValue(current_value)
                self.progress_bar.setFormat("%p%")
            else:
                self.progress_bar.setRange(0, 0)
                self.progress_bar.setFormat("Working")
            self.status_base_text = event.message
            if event.is_log:
                self._log(f"[sync] {event.message}")

    def _animate_busy_status(self) -> None:
        if not self.is_busy:
            return
        self.status_anim_frame = (self.status_anim_frame + 1) % 4
        suffix = "." * self.status_anim_frame
        self._set_status_text(f"{self.status_base_text}{suffix}")

    def _set_status_text(self, text: str) -> None:
        metrics = self.status_label.fontMetrics()
        width = max(40, self.status_label.width() - 4)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)
        self.status_label.setText(elided)

    def _set_busy(self, text: str, determinate_total: int | None = None) -> None:
        self.is_busy = True
        self.status_base_text = text
        self.status_anim_frame = 0
        self._set_status_text(text)
        if determinate_total is None:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("Working")
        else:
            self.progress_bar.setRange(0, max(1, determinate_total) * 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("%p%")
        self.load_catalog_button.setEnabled(False)
        self.refresh_collection_button.setEnabled(False)
        self.sync_selected_button.setEnabled(False)
        self.refresh_files_button.setEnabled(False)
        self.download_selected_file_button.setEnabled(False)
        self.delete_selected_file_button.setEnabled(False)
        self.regenerate_metadata_button.setEnabled(False)
        self.remove_from_kindle_button.setEnabled(False)
        self.probe_kindle_button.setEnabled(False)

    def _set_idle(self) -> None:
        self.is_busy = False
        self.status_base_text = "Idle"
        self._set_status_text("Idle")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.load_catalog_button.setEnabled(True)
        self.refresh_collection_button.setEnabled(True)
        self.sync_selected_button.setEnabled(True)
        self.refresh_files_button.setEnabled(True)
        self.download_selected_file_button.setEnabled(True)
        self.delete_selected_file_button.setEnabled(True)
        self.regenerate_metadata_button.setEnabled(True)
        self.remove_from_kindle_button.setEnabled(True)
        self.probe_kindle_button.setEnabled(True)

    def _log(self, message: str) -> None:
        self.log_output.append(message)

    def resizeEvent(self, event) -> None:  # pragma: no cover
        self._set_status_text(self.status_base_text)
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:  # pragma: no cover
        self.worker_pool.shutdown()
        event.accept()


def main() -> int:
    app = QApplication([])
    window = HearthMainWindow()
    window.show()
    return app.exec()

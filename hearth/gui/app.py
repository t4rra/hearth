from __future__ import annotations

# pylint: disable=import-error

from concurrent.futures import Future
from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Callable, Literal, cast
import urllib.parse

from PyQt6.QtCore import QTimer, Qt  # type: ignore[import-not-found]
from PyQt6.QtGui import QColor  # type: ignore[import-not-found]
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)  # type: ignore[import-not-found]

from hearth.converters.manager import ConverterManager
from hearth.core.opds import OPDSClient, OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import DeviceFile, KindleDevice
from hearth.sync.manager import SyncItem, SyncManager, SyncProgress
from hearth.sync.metadata import load_metadata

from .workers import WorkerPool


@dataclass(slots=True)
class LibraryRow:
    id: str
    title: str
    author: str
    download_url: str
    declared_type: str


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
        self.loaded_feeds: set[str] = set()
        self.loading_feeds: set[str] = set()
        self.tree_item_by_feed: dict[str, QTreeWidgetItem] = {}
        self.book_rows_by_id: dict[str, LibraryRow] = {}
        self.book_on_device: dict[str, bool] = {}
        self.pending_book_actions: dict[str, Literal["add", "remove"]] = {}
        self.collection_sync_feeds: set[str] = set()
        self.pending_collection_targets: dict[str, bool] = {}
        self._updating_library_checks = False
        self._updating_collection_checks = False

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
        self.calibre_command_input = QLineEdit("")

        self.probe_kindle_button = QPushButton("Probe Kindle")

        self.load_catalog_button = QPushButton("Reload Root")
        self.sync_selected_button = QPushButton("Sync Selected")
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
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
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

        self.library_table = QTableWidget(0, 5)
        self.library_table.setHorizontalHeaderLabels(
            ["Sync", "Title", "Author", "Type", "Status"]
        )

        self.kindle_files_tree = QTreeWidget()
        self.kindle_files_tree.setColumnCount(3)
        self.kindle_files_tree.setHeaderLabels(["Name", "Type", "Size"])

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        self.reset_general_button = QPushButton("Reset General")
        self.reset_opds_button = QPushButton("Reset OPDS")
        self.reset_kindle_button = QPushButton("Reset Kindle")
        self.reset_conversion_button = QPushButton("Reset Conversion")
        self.reset_all_button = QPushButton("Reset All")

        self.tabs = QTabWidget()
        self.library_tab = QWidget()
        self.kindle_files_tab = QWidget()
        self.settings_tab = QWidget()

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

        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.kindle_files_tab, "Kindle Files")
        self.tabs.addTab(self.settings_tab, "Settings")

        root.addLayout(header)
        root.addWidget(self.tabs, stretch=5)
        root.addWidget(QLabel("Log"))
        root.addWidget(self.log_output, stretch=2)

        central.setLayout(root)
        self.setCentralWidget(central)

    def _configure_library_tab(self) -> None:
        layout = QVBoxLayout()

        controls = QHBoxLayout()
        controls.addWidget(self.load_catalog_button)
        controls.addWidget(self.sync_selected_button)
        controls.addWidget(self.select_all_library_button)
        controls.addWidget(self.clear_library_selection_button)
        controls.addWidget(self.force_checkbox)
        controls.addStretch(1)

        tree_header = self.collections_tree.header()
        if tree_header is not None:
            tree_header.setStretchLastSection(True)

        library_header = self.library_table.horizontalHeader()
        if library_header is not None:
            library_header.setStretchLastSection(True)
        library_vertical = self.library_table.verticalHeader()
        if library_vertical is not None:
            library_vertical.setVisible(False)

        layout.addLayout(controls)

        split = QHBoxLayout()
        split.addWidget(self.collections_tree, stretch=2)
        split.addWidget(self.library_table, stretch=4)
        layout.addLayout(split)

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
        general_layout.addWidget(self.download_dir_input, 1, 1, 1, 2)
        general_layout.addWidget(self.reset_general_button, 1, 3)
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
        kindle_layout.addWidget(self.reset_kindle_button, 1, 3)
        kindle_group.setLayout(kindle_layout)

        conversion_group = QGroupBox("Conversion")
        conversion_layout = QGridLayout()
        conversion_layout.addWidget(QLabel("Preferred output"), 0, 0)
        conversion_layout.addWidget(self.desired_output_combo, 0, 1)
        conversion_layout.addWidget(QLabel("KCC command"), 1, 0)
        conversion_layout.addWidget(self.kcc_command_input, 1, 1, 1, 3)
        conversion_layout.addWidget(QLabel("KCC device"), 2, 0)
        conversion_layout.addWidget(self.kcc_device_input, 2, 1)
        conversion_layout.addWidget(self.kcc_manga_default_checkbox, 3, 0, 1, 2)
        conversion_layout.addWidget(self.kcc_manga_force_checkbox, 3, 2, 1, 2)
        conversion_layout.addWidget(self.kcc_autolevel_checkbox, 4, 0, 1, 2)
        conversion_layout.addWidget(self.reset_conversion_button, 5, 3)
        conversion_group.setLayout(conversion_layout)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.reset_all_button)

        tab_layout.addWidget(general_group)
        tab_layout.addWidget(opds_group)
        tab_layout.addWidget(kindle_group)
        tab_layout.addWidget(conversion_group)
        tab_layout.addLayout(footer)
        tab_layout.addStretch(1)

        self.settings_tab.setLayout(tab_layout)

    def _connect_events(self) -> None:
        self.probe_kindle_button.clicked.connect(self._probe_kindle)
        self.auth_mode_combo.currentTextChanged.connect(self._update_auth_visibility)

        self.load_catalog_button.clicked.connect(self._load_root_collections)
        self.sync_selected_button.clicked.connect(self._sync_selected)
        self.select_all_library_button.clicked.connect(self._select_all_library_rows)
        self.clear_library_selection_button.clicked.connect(
            self._clear_library_selection
        )
        self.collections_tree.currentItemChanged.connect(self._on_collection_changed)
        self.collections_tree.itemExpanded.connect(self._on_collection_expanded)
        self.collections_tree.itemChanged.connect(self._on_collection_item_changed)
        self.library_table.itemChanged.connect(self._on_library_item_changed)

        self.refresh_files_button.clicked.connect(self._refresh_kindle_files)
        self.download_selected_file_button.clicked.connect(
            self._download_selected_kindle_files
        )
        self.delete_selected_file_button.clicked.connect(
            self._delete_selected_kindle_files
        )

        self.reset_general_button.clicked.connect(self._reset_general)
        self.reset_opds_button.clicked.connect(self._reset_opds)
        self.reset_kindle_button.clicked.connect(self._reset_kindle)
        self.reset_conversion_button.clicked.connect(self._reset_conversion)
        self.reset_all_button.clicked.connect(self._reset_all)

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
            self.kcc_manga_default_checkbox,
            self.kcc_manga_force_checkbox,
            self.kcc_autolevel_checkbox,
        ]:
            checkbox.toggled.connect(self._save_settings_to_file)

        for edit in [
            self.workspace_input,
            self.download_dir_input,
            self.feed_input,
            self.auth_username_input,
            self.auth_password_input,
            self.auth_bearer_input,
            self.kindle_root_input,
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
        self.calibre_command_input.setText(settings.calibre_command)

        self._update_auth_visibility()
        self._load_collection_sync_preferences()
        self._log(f"Loaded settings from {self.settings_path}")

    def _collection_sync_path(self) -> Path:
        workspace = Path(self.workspace_input.text().strip() or ".hearth")
        return workspace / ".hearth_collection_sync.json"

    def _load_collection_sync_preferences(self) -> None:
        path = self._collection_sync_path()
        if not path.exists():
            self.collection_sync_feeds = set()
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.collection_sync_feeds = set()
            return

        feeds = payload.get("feeds", [])
        if isinstance(feeds, list):
            self.collection_sync_feeds = {
                item for item in feeds if isinstance(item, str) and item.strip()
            }
        else:
            self.collection_sync_feeds = set()

    def _save_collection_sync_preferences(self) -> None:
        path = self._collection_sync_path()
        payload = {
            "feeds": sorted(self.collection_sync_feeds),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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
            calibre_command=self.calibre_command_input.text().strip(),
        )

    def _reset_general(self) -> None:
        self.workspace_input.setText(str(Path.home() / ".hearth"))
        self.download_dir_input.setText(str(Path.home() / "Downloads"))
        self._save_settings_to_file()
        self._load_collection_sync_preferences()

    def _reset_opds(self) -> None:
        self.feed_input.setText("")
        self.auth_mode_combo.setCurrentText("none")
        self.auth_username_input.setText("")
        self.auth_password_input.setText("")
        self.auth_bearer_input.setText("")
        self._update_auth_visibility()
        self._save_settings_to_file()

    def _reset_kindle(self) -> None:
        self.transport_combo.setCurrentText("auto")
        self.kindle_root_input.setText("")
        self._save_settings_to_file()

    def _reset_conversion(self) -> None:
        self.desired_output_combo.setCurrentText("auto")
        self.kcc_command_input.setText("")
        self._set_kcc_device_ui("auto")
        self.kcc_manga_default_checkbox.setChecked(False)
        self.kcc_manga_force_checkbox.setChecked(False)
        self.kcc_autolevel_checkbox.setChecked(True)
        self.calibre_command_input.setText("")
        self._save_settings_to_file()

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
        self._reset_conversion()
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

        self.books_by_feed = {}
        self.loaded_feeds = set()
        self.loading_feeds = set()
        self.tree_item_by_feed = {}
        self.book_rows_by_id = {}
        self.book_on_device = {}
        self.pending_book_actions = {}
        self.pending_collection_targets = {}
        self.collections_tree.clear()
        self.library_table.setRowCount(0)

        self._request_feed_load(
            feed_url=feed_url,
            is_root=True,
            show_errors=not silent,
        )

    def _request_feed_load(
        self,
        feed_url: str,
        is_root: bool,
        show_errors: bool,
    ) -> None:
        if feed_url in self.loading_feeds:
            return

        if feed_url in self.loaded_feeds and not is_root:
            current = self.collections_tree.currentItem()
            if current is not None:
                current_feed = current.data(0, Qt.ItemDataRole.UserRole)
                if current_feed == feed_url:
                    self._populate_library_table(self.books_by_feed.get(feed_url, []))
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
        self.books_by_feed[result.feed_url] = result.books
        for row in result.books:
            self.book_rows_by_id[row.id] = row
        self._refresh_book_presence_cache()

        pending_target = self.pending_collection_targets.pop(
            result.feed_url,
            None,
        )
        if pending_target is not None:
            self._apply_collection_target(result.feed_url, pending_target)

        if result.feed_url in self.collection_sync_feeds:
            self._plan_collection_missing_books(result.feed_url)

        if result.is_root:
            self._populate_root_collections(result.children)
            self._queue_collection_autoloads()
            self._refresh_all_collection_states()
            self._log(f"Loaded {len(result.children)} top-level collections")
            return

        parent_item = self.tree_item_by_feed.get(result.feed_url)
        if parent_item is not None:
            self._replace_placeholder_with_children(
                parent_item,
                result.children,
            )

        current = self.collections_tree.currentItem()
        if current is not None:
            current_feed = current.data(0, Qt.ItemDataRole.UserRole)
            if current_feed == result.feed_url:
                self._populate_library_table(result.books)

        self._refresh_all_collection_states()
        self._log(f"Loaded {len(result.children)} sub-collections")

    def _queue_collection_autoloads(self) -> None:
        for feed_url in sorted(self.collection_sync_feeds):
            if feed_url in self.loaded_feeds:
                continue
            self._request_feed_load(
                feed_url=feed_url,
                is_root=False,
                show_errors=False,
            )

    def _populate_root_collections(self, rows: list[CollectionRow]) -> None:
        self.collections_tree.clear()
        self.tree_item_by_feed = {}

        self._updating_collection_checks = True
        for row in rows:
            item = self._build_collection_item(row)
            self._attach_placeholder(item)
            self.collections_tree.addTopLevelItem(item)
            self.tree_item_by_feed[row.feed_url] = item
        self._updating_collection_checks = False

        self.collections_tree.collapseAll()
        if self.collections_tree.topLevelItemCount() > 0:
            self.collections_tree.setCurrentItem(self.collections_tree.topLevelItem(0))

    def _build_collection_item(self, row: CollectionRow) -> QTreeWidgetItem:
        item = QTreeWidgetItem([row.title])
        item.setData(0, Qt.ItemDataRole.UserRole, row.feed_url)
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEnabled
        )
        item.setCheckState(0, Qt.CheckState.Unchecked)
        return item

    def _attach_placeholder(self, item: QTreeWidgetItem) -> None:
        if item.childCount() > 0:
            return
        child = QTreeWidgetItem([self.PLACEHOLDER_TEXT])
        child.setData(0, Qt.ItemDataRole.UserRole, None)
        item.addChild(child)

    def _replace_placeholder_with_children(
        self,
        parent_item: QTreeWidgetItem,
        rows: list[CollectionRow],
    ) -> None:
        parent_item.takeChildren()
        self._updating_collection_checks = True
        for row in rows:
            child_item = self._build_collection_item(row)
            self._attach_placeholder(child_item)
            parent_item.addChild(child_item)
            self.tree_item_by_feed[row.feed_url] = child_item
        self._updating_collection_checks = False

    def _on_collection_expanded(self, item: QTreeWidgetItem) -> None:
        feed_url = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return
        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=True,
        )

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
            self._populate_library_table(self.books_by_feed.get(feed_url, []))
            return

        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=True,
        )

    def _populate_library_table(self, rows: list[LibraryRow]) -> None:
        self._refresh_book_presence_cache()

        self._updating_library_checks = True
        self.library_table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "id": row.id,
                    "title": row.title,
                    "author": row.author,
                    "download_url": row.download_url,
                    "declared_type": row.declared_type,
                    "base_on_device": self.book_on_device.get(row.id, False),
                },
            )

            status_item = QTableWidgetItem("")
            self._apply_book_visual_state(
                check_item,
                status_item,
                book_id=row.id,
                base_on_device=self.book_on_device.get(row.id, False),
            )

            self.library_table.setItem(idx, 0, check_item)
            self.library_table.setItem(idx, 1, QTableWidgetItem(row.title))
            self.library_table.setItem(
                idx,
                2,
                QTableWidgetItem(row.author or ""),
            )
            self.library_table.setItem(
                idx,
                3,
                QTableWidgetItem(row.declared_type or ""),
            )
            self.library_table.setItem(idx, 4, status_item)
        self._updating_library_checks = False
        self._refresh_all_collection_states()

    def _refresh_book_presence_cache(self) -> None:
        self.book_on_device = {}
        if self.connected_device is None:
            return

        all_rows = [row for rows in self.books_by_feed.values() for row in rows]
        if not all_rows:
            return

        device = KindleDevice(
            transport=self.connected_device.transport,
            root=self.connected_device.root,
        )
        if device.transport == "mtp":
            metadata_path = (
                Path(self.workspace_input.text().strip() or ".hearth")
                / ".hearth_metadata.mtp.json"
            )
        else:
            metadata_path = device.documents_dir / ".hearth_metadata.json"

        records = load_metadata(metadata_path)
        device_files: set[str] = set()
        for entry in device.list_files():
            if entry.is_dir:
                continue
            device_files.add(entry.name)
            device_files.add(entry.path)
            normalized = entry.path.strip("/")
            if normalized:
                device_files.add(normalized)
                if normalized.startswith("documents/"):
                    relative = normalized.removeprefix("documents/")
                    device_files.add(relative)

        for row in all_rows:
            record = records.get(row.id)
            if not record:
                self.book_on_device[row.id] = False
                continue
            self.book_on_device[row.id] = (
                record.on_device and record.device_filename in device_files
            )

    def _apply_book_visual_state(
        self,
        check_item: QTableWidgetItem,
        status_item: QTableWidgetItem,
        book_id: str,
        base_on_device: bool,
    ) -> None:
        action = self.pending_book_actions.get(book_id)
        if action == "add":
            check_item.setCheckState(Qt.CheckState.PartiallyChecked)
            status_item.setText("Will Add")
            status_item.setForeground(QColor("#198754"))
            return
        if action == "remove":
            check_item.setCheckState(Qt.CheckState.PartiallyChecked)
            status_item.setText("Will Remove")
            status_item.setForeground(QColor("#b02a37"))
            return

        if base_on_device:
            check_item.setCheckState(Qt.CheckState.Checked)
            status_item.setText("On Kindle")
        else:
            check_item.setCheckState(Qt.CheckState.Unchecked)
            status_item.setText("Not On Kindle")
        status_item.setForeground(QColor("#000000"))

    def _on_library_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_library_checks:
            return
        if item.column() != 0:
            return

        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return

        book_id = payload.get("id")
        if not isinstance(book_id, str):
            return
        base_on_device = bool(payload.get("base_on_device", False))
        existing = self.pending_book_actions.get(book_id)

        if existing is not None:
            del self.pending_book_actions[book_id]
        elif base_on_device:
            self.pending_book_actions[book_id] = "remove"
        else:
            self.pending_book_actions[book_id] = "add"

        row_idx = item.row()
        status_item = self.library_table.item(row_idx, 4)
        if status_item is None:
            status_item = QTableWidgetItem("")
            self.library_table.setItem(row_idx, 4, status_item)

        self._updating_library_checks = True
        self._apply_book_visual_state(item, status_item, book_id, base_on_device)
        self._updating_library_checks = False
        self._refresh_all_collection_states()

    def _select_all_library_rows(self) -> None:
        for row in range(self.library_table.rowCount()):
            item = self.library_table.item(row, 0)
            if item is None:
                continue
            payload = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict):
                continue
            book_id = payload.get("id")
            if not isinstance(book_id, str):
                continue
            base_on_device = bool(payload.get("base_on_device", False))
            if base_on_device:
                self.pending_book_actions.pop(book_id, None)
            else:
                self.pending_book_actions[book_id] = "add"

        self._refresh_visible_library_rows()
        self._refresh_all_collection_states()

    def _clear_library_selection(self) -> None:
        for row in range(self.library_table.rowCount()):
            item = self.library_table.item(row, 0)
            if item is None:
                continue
            payload = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict):
                continue
            book_id = payload.get("id")
            if isinstance(book_id, str):
                self.pending_book_actions.pop(book_id, None)

        self._refresh_visible_library_rows()
        self._refresh_all_collection_states()

    def _refresh_visible_library_rows(self) -> None:
        self._updating_library_checks = True
        for row_idx in range(self.library_table.rowCount()):
            check_item = self.library_table.item(row_idx, 0)
            if check_item is None:
                continue
            payload = check_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict):
                continue
            book_id = payload.get("id")
            if not isinstance(book_id, str):
                continue
            base_on_device = bool(payload.get("base_on_device", False))
            status_item = self.library_table.item(row_idx, 4)
            if status_item is None:
                status_item = QTableWidgetItem("")
                self.library_table.setItem(row_idx, 4, status_item)
            self._apply_book_visual_state(
                check_item,
                status_item,
                book_id=book_id,
                base_on_device=base_on_device,
            )
        self._updating_library_checks = False

    def _on_collection_item_changed(
        self,
        item: QTreeWidgetItem,
        column: int,
    ) -> None:
        if self._updating_collection_checks:
            return
        if column != 0:
            return
        feed_url = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return

        state = item.checkState(0)
        target_present = state == Qt.CheckState.Checked
        self._apply_collection_target(feed_url, target_present)

    def _plan_collection_missing_books(self, feed_url: str) -> None:
        rows = self.books_by_feed.get(feed_url, [])
        for row in rows:
            current_action = self.pending_book_actions.get(row.id)
            if current_action == "remove":
                del self.pending_book_actions[row.id]
            if not self.book_on_device.get(row.id, False):
                self.pending_book_actions[row.id] = "add"

    def _apply_collection_target(
        self,
        feed_url: str,
        target_present: bool,
    ) -> None:
        if target_present:
            self.collection_sync_feeds.add(feed_url)
        else:
            self.collection_sync_feeds.discard(feed_url)

        rows = self.books_by_feed.get(feed_url)
        if rows is None:
            self.pending_collection_targets[feed_url] = target_present
            self._request_feed_load(
                feed_url=feed_url,
                is_root=False,
                show_errors=False,
            )
            self._save_collection_sync_preferences()
            self._refresh_all_collection_states()
            return

        for row in rows:
            on_device = self.book_on_device.get(row.id, False)
            action = self.pending_book_actions.get(row.id)

            if target_present:
                if action == "remove":
                    del self.pending_book_actions[row.id]
                if not on_device:
                    self.pending_book_actions[row.id] = "add"
            else:
                if action == "add":
                    del self.pending_book_actions[row.id]
                if on_device:
                    self.pending_book_actions[row.id] = "remove"

        self._save_collection_sync_preferences()
        self._refresh_visible_library_rows()
        self._refresh_all_collection_states()

    def _refresh_all_collection_states(self) -> None:
        self._updating_collection_checks = True
        for feed_url, item in self.tree_item_by_feed.items():
            rows = self.books_by_feed.get(feed_url)
            if rows is None:
                if feed_url in self.collection_sync_feeds:
                    item.setCheckState(0, Qt.CheckState.PartiallyChecked)
                    item.setForeground(0, QColor("#198754"))
                else:
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                    item.setForeground(0, QColor("#000000"))
                continue

            total = len(rows)
            on_device = sum(1 for row in rows if self.book_on_device.get(row.id, False))
            adds = sum(
                1 for row in rows if self.pending_book_actions.get(row.id) == "add"
            )
            removes = sum(
                1 for row in rows if self.pending_book_actions.get(row.id) == "remove"
            )

            if adds or removes:
                state = Qt.CheckState.PartiallyChecked
            elif total > 0 and on_device == total:
                state = Qt.CheckState.Checked
            elif on_device == 0:
                state = Qt.CheckState.Unchecked
            else:
                state = Qt.CheckState.PartiallyChecked

            item.setCheckState(0, state)
            if adds and not removes:
                item.setForeground(0, QColor("#198754"))
            elif removes and not adds:
                item.setForeground(0, QColor("#b02a37"))
            elif adds or removes:
                item.setForeground(0, QColor("#b26a00"))
            else:
                item.setForeground(0, QColor("#000000"))
        self._updating_collection_checks = False

    def _planned_sync_actions(self) -> tuple[list[SyncItem], list[str]]:
        to_add: list[SyncItem] = []
        to_remove: list[str] = []
        for book_id, action in self.pending_book_actions.items():
            if action == "remove":
                to_remove.append(book_id)
                continue

            row = self.book_rows_by_id.get(book_id)
            if row is None:
                continue
            to_add.append(
                SyncItem(
                    id=row.id,
                    title=row.title,
                    author=row.author,
                    download_url=row.download_url,
                    declared_type=row.declared_type,
                )
            )
        return to_add, to_remove

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

        items, delete_ids = self._planned_sync_actions()
        if not items and not delete_ids:
            QMessageBox.information(
                self,
                "No pending changes",
                "Plan one or more adds/removals in Library first.",
            )
            return

        settings = self._current_settings()
        workspace = Path(self.workspace_input.text().strip() or ".hearth")
        root = self.connected_device.root
        force_resync = self.force_checkbox.isChecked()
        total_ops = len(items) + len(delete_ids)

        self.sync_progress_queue = SimpleQueue()
        self._set_busy(
            f"Syncing {total_ops} change(s)",
            determinate_total=total_ops,
        )
        future = self.worker_pool.submit(
            self._run_sync_worker,
            settings,
            workspace,
            str(root),
            items,
            delete_ids,
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
        items: list[SyncItem],
        delete_ids: list[str],
        force_resync: bool,
    ) -> tuple[int, int, int]:
        session = OPDSSession(settings)
        converters = ConverterManager.from_commands(
            settings.kcc_command,
            settings.kcc_device,
            settings.kcc_manga_default,
            settings.kcc_manga_force,
            settings.kcc_autolevel,
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
        )

        total_ops = len(items) + len(delete_ids)
        deleted = 0
        removed_done = 0

        def emit_direct(
            current: float,
            message: str,
            is_log: bool = False,
        ) -> None:
            if self.sync_progress_queue is None:
                return
            self.sync_progress_queue.put(
                SyncProgress(
                    current=current,
                    total=max(1, total_ops),
                    message=message,
                    is_log=is_log,
                )
            )

        for index, record_id in enumerate(delete_ids, start=1):
            emit_direct(
                removed_done,
                f"[{index}/{total_ops}] removing: {record_id}",
                is_log=True,
            )
            if manager.mark_deleted_on_device(record_id):
                deleted += 1
                emit_direct(
                    removed_done,
                    f"removed from device: {record_id}",
                    is_log=True,
                )
            else:
                emit_direct(
                    removed_done,
                    f"remove skipped (not found): {record_id}",
                    is_log=True,
                )
            removed_done += 1

        def on_progress(event: SyncProgress) -> None:
            if self.sync_progress_queue is not None:
                self.sync_progress_queue.put(
                    SyncProgress(
                        current=removed_done + event.current,
                        total=max(1, total_ops),
                        message=event.message,
                        is_log=event.is_log,
                    )
                )

        if items:
            outcome = manager.sync(
                items=items,
                force_resync=force_resync,
                progress_callback=on_progress,
            )
            return (outcome.synced, outcome.skipped, deleted)

        return (0, 0, deleted)

    def _on_sync_finished(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 3:
            raise TypeError("Unexpected sync result")

        synced, skipped, deleted = result
        self._log(
            f"Sync complete: synced={synced}, skipped={skipped}, deleted={deleted}"
        )
        self.pending_book_actions = {}
        self.sync_progress_queue = None
        self._refresh_kindle_files(force=True)
        self._refresh_book_presence_cache()

        current_item = self.collections_tree.currentItem()
        if current_item is not None:
            feed_url = current_item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(feed_url, str):
                self._populate_library_table(self.books_by_feed.get(feed_url, []))
        self._refresh_all_collection_states()

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

        file_count = len([row for row in result.rows if not row.is_dir])
        dir_count = len([row for row in result.rows if row.is_dir])
        self._log(f"Loaded {file_count} files and {dir_count} folders from Kindle")

    def _populate_kindle_files_tree(self, rows: list[DeviceFile]) -> None:
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
                    node.setData(0, Qt.ItemDataRole.UserRole, entry.path)
                    node.setData(1, Qt.ItemDataRole.UserRole, is_file)

                parent_item = node

        self.kindle_files_tree.collapseAll()

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
                self._log(f"{task.action_name} failed: {exc}")
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
        else:
            self.progress_bar.setRange(0, max(1, determinate_total) * 100)
            self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.load_catalog_button.setEnabled(False)
        self.sync_selected_button.setEnabled(False)
        self.refresh_files_button.setEnabled(False)
        self.download_selected_file_button.setEnabled(False)
        self.delete_selected_file_button.setEnabled(False)
        self.probe_kindle_button.setEnabled(False)

    def _set_idle(self) -> None:
        self.is_busy = False
        self.status_base_text = "Idle"
        self._set_status_text("Idle")
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.load_catalog_button.setEnabled(True)
        self.sync_selected_button.setEnabled(True)
        self.refresh_files_button.setEnabled(True)
        self.download_selected_file_button.setEnabled(True)
        self.delete_selected_file_button.setEnabled(True)
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
